"""Per-field verification and review status for Quotation lines (ADR-0004).

Every non-empty field, and every critical field, is marked verified, unverified or failed
by independent checks; the model's own confidence is never used. A line needs review when
a critical field fails or when it carries a revision. Every flag is a Review reason.
"""

from collections.abc import Iterable
from enum import StrEnum
from typing import Literal, get_args

import pycountry
from pydantic import BaseModel

from sdi.extraction.schema import FieldEvidence, LineField, Quotation, QuotationLine
from sdi.loaders import DocumentContent
from sdi.validation.evidence import SourceText, numbers_in
from sdi.validation.pricing import DerivedPrices, derive_prices, headline_tier

FieldResult = Literal["verified", "unverified", "failed"]
ReviewStatus = Literal["needs_review", "auto_ok"]


class ReasonCode(StrEnum):
    """Stable codes the UI filters on and the eval counts. Stored as plain text, so adding
    one needs no schema change."""

    EVIDENCE_NOT_FOUND = "EVIDENCE_NOT_FOUND"
    INVALID_CURRENCY = "INVALID_CURRENCY"
    NON_POSITIVE_PRICE = "NON_POSITIVE_PRICE"
    MISSING_CRITICAL_FIELD = "MISSING_CRITICAL_FIELD"
    IMPLAUSIBLE_VALUE = "IMPLAUSIBLE_VALUE"
    INFERRED_VALUE = "INFERRED_VALUE"
    UNCLEAR_VALUE = "UNCLEAR_VALUE"
    ARITHMETIC_MISMATCH = "ARITHMETIC_MISMATCH"
    REVISION = "REVISION"
    TIERED_PRICING = "TIERED_PRICING"


class ReviewReason(BaseModel):
    code: str
    field: str | None
    message: str


class LineVerification(BaseModel):
    fields: dict[str, FieldResult]
    reasons: list[ReviewReason]
    review_status: ReviewStatus
    prices: DerivedPrices


# A line needs at least one of these prices, and each one stated is critical.
PRICE_FIELDS = ("unit_price", "pack_price", "price_tiers")
CRITICAL_FIELDS = frozenset({*PRICE_FIELDS, "currency", "pack_size", "uom", "active_ingredients"})

# ISO 4217 codes that aren't money a price can be quoted in: precious metals, the IMF SDR,
# bond-market units, testing and "no currency" codes.
_NON_MONEY_CODES = frozenset(
    {"XAG", "XAU", "XPD", "XPT", "XDR", "XBA", "XBB", "XBC", "XBD", "XSU", "XUA", "XTS", "XXX"}
)

# Relative tolerance for a float value against the number written in its evidence.
_NUMBER_TOLERANCE = 1e-9

# Relative tolerance for an arithmetic cross-check. Documents round derived figures (a Unit
# price of 12.50 / 30 is written 0.42, 0.8% off), so 1% absorbs rounding to the cent but
# still catches a price for the wrong quantity.
_ARITHMETIC_TOLERANCE = 0.01


def verify(quotation: Quotation, content: DocumentContent) -> list[LineVerification]:
    # Without source text (images) the evidence check can't run, so fields stay unverified.
    text = SourceText(content.text) if content.text else None
    return [_verify_line(line, quotation, text) for line in quotation.lines]


class _LineChecks:
    """Accumulates check outcomes for one line. A failure is never overridden by a pass."""

    def __init__(self) -> None:
        self.fields: dict[str, FieldResult] = {}
        self.reasons: list[ReviewReason] = []
        self._forced_review = False

    def present(self, field: str) -> None:
        self.fields.setdefault(field, "unverified")

    def verified(self, field: str) -> None:
        if self.fields.get(field) != "failed":
            self.fields[field] = "verified"

    def failed(self, field: str, code: ReasonCode, message: str) -> None:
        self.fields[field] = "failed"
        self.note(code, field, message)

    def verified_all(self, fields: Iterable[str]) -> None:
        for field in fields:
            self.verified(field)

    def failed_all(self, fields: Iterable[str], code: ReasonCode, message: str) -> None:
        for field in fields:
            self.failed(field, code, message)

    def note(self, code: ReasonCode, field: str | None, message: str) -> None:
        self.reasons.append(ReviewReason(code=code, field=field, message=message))

    def force_review(self, code: ReasonCode, field: str | None, message: str) -> None:
        self._forced_review = True
        self.note(code, field, message)

    def result(self, prices: DerivedPrices) -> LineVerification:
        critical_failed = any(
            result == "failed" for field, result in self.fields.items() if field in CRITICAL_FIELDS
        )
        status: ReviewStatus = (
            "needs_review" if critical_failed or self._forced_review else "auto_ok"
        )
        return LineVerification(
            fields=self.fields, reasons=self.reasons, review_status=status, prices=prices
        )


def _verify_line(
    line: QuotationLine, quotation: Quotation, text: SourceText | None
) -> LineVerification:
    checks = _LineChecks()
    for field in get_args(LineField):
        value, evidence = _field_value(line, quotation, field)
        if value is None or value == []:
            continue
        checks.present(field)
        if text is not None:
            _check_evidence(checks, field, value, evidence, text)
        _check_certainty(checks, field, evidence)
    _check_rules(checks, line, quotation)
    prices = derive_prices(line)
    _check_arithmetic(checks, line, prices)
    if line.revision_note:
        checks.force_review(ReasonCode.REVISION, None, f"Revised: {line.revision_note}")
    if (tier := headline_tier(line)) is not None:
        checks.note(
            ReasonCode.TIERED_PRICING,
            None,
            f"Price depends on order volume; headline price is the tier from {tier.min_qty or 0:g}",
        )
    return checks.result(prices)


def _field_value(
    line: QuotationLine, quotation: Quotation, field: str
) -> tuple[object, list[FieldEvidence]]:
    """A line's value and evidence for one field; a line without its own currency uses the
    Quotation's, and that currency's evidence."""
    if field == "currency" and line.currency is None:
        return quotation.currency, [e for e in quotation.evidence if e.field == "currency"]
    return getattr(line, field), [e for e in line.evidence if e.field == field]


def _check_evidence(
    checks: _LineChecks, field: str, value: object, evidence: list[FieldEvidence], text: SourceText
) -> None:
    spans = [e.span for e in evidence if e.span]
    if not spans:
        checks.failed(field, ReasonCode.EVIDENCE_NOT_FOUND, f"No evidence given for {field}")
        return
    missing = [span for span in spans if not text.contains(span)]
    if missing:
        checks.failed(
            field, ReasonCode.EVIDENCE_NOT_FOUND, f"'{missing[0]}' not found in source text"
        )
        return
    # A real span can still back the wrong number, so the value must be written in it. List
    # entries (strengths, tiers) are rarely all in one span, so their numbers need only be
    # written somewhere in the Source; that still catches values the model computed.
    if isinstance(value, list):
        written, where = text.numbers, "source text"
    else:
        written, where = numbers_in(" ".join(spans)), f"its evidence '{' / '.join(spans)}'"
    for number in _numbers(value):
        if not any(_close(float(w), number) for w in written):
            checks.failed(field, ReasonCode.EVIDENCE_NOT_FOUND, f"{number:g} not found in {where}")
            return
    checks.verified(field)


def _check_certainty(checks: _LineChecks, field: str, evidence: list[FieldEvidence]) -> None:
    certainties = {e.certainty for e in evidence}
    if "unclear" in certainties:
        checks.failed(
            field, ReasonCode.UNCLEAR_VALUE, f"{field} is unclear or partly unreadable in source"
        )
    if "inferred" in certainties:
        checks.note(
            ReasonCode.INFERRED_VALUE, field, f"{field} was inferred from context, not stated"
        )


def _check_rules(checks: _LineChecks, line: QuotationLine, quotation: Quotation) -> None:
    currency = line.currency or quotation.currency
    if currency is None:
        checks.failed("currency", ReasonCode.MISSING_CRITICAL_FIELD, "No currency stated")
    elif not _is_iso_currency(currency):
        checks.failed(
            "currency", ReasonCode.INVALID_CURRENCY, f"'{currency}' is not an ISO 4217 code"
        )

    tier_prices = [
        p for t in line.price_tiers for p in (t.unit_price, t.pack_price) if p is not None
    ]
    if line.unit_price is None and line.pack_price is None and not tier_prices:
        for field in ("unit_price", "pack_price"):
            checks.failed(field, ReasonCode.MISSING_CRITICAL_FIELD, "No price stated")
    for field, prices in (
        ("unit_price", [line.unit_price]),
        ("pack_price", [line.pack_price]),
        ("price_tiers", tier_prices),
    ):
        for price in prices:
            if price is not None and price <= 0:
                checks.failed(
                    field, ReasonCode.NON_POSITIVE_PRICE, f"Price {price:g} is not positive"
                )

    if line.uom is None:
        checks.failed("uom", ReasonCode.MISSING_CRITICAL_FIELD, "No unit of measure stated")
    if line.pack_size is None:
        checks.failed("pack_size", ReasonCode.MISSING_CRITICAL_FIELD, "No pack size stated")
    elif line.pack_size <= 0 or not line.pack_size.is_integer():
        checks.failed(
            "pack_size",
            ReasonCode.IMPLAUSIBLE_VALUE,
            f"Pack size {line.pack_size:g} is not a positive whole number of UOMs",
        )

    if not line.active_ingredients:
        checks.failed(
            "active_ingredients", ReasonCode.MISSING_CRITICAL_FIELD, "No active ingredient stated"
        )
    for ingredient in line.active_ingredients:
        if ingredient.strength_value is None:
            checks.failed(
                "active_ingredients",
                ReasonCode.MISSING_CRITICAL_FIELD,
                f"No strength stated for {ingredient.name_as_written}",
            )
        elif ingredient.strength_value <= 0:
            checks.failed(
                "active_ingredients",
                ReasonCode.IMPLAUSIBLE_VALUE,
                f"Strength {ingredient.strength_value:g} of {ingredient.name_as_written} "
                "is not positive",
            )

    if line.discount_pct is not None and not 0 <= line.discount_pct <= 100:
        checks.failed(
            "discount_pct",
            ReasonCode.IMPLAUSIBLE_VALUE,
            f"Discount {line.discount_pct:g}% is outside 0-100%",
        )
    for field in ("quantity", "line_total"):
        amount = getattr(line, field)
        if amount is not None and amount <= 0:
            checks.failed(
                field, ReasonCode.IMPLAUSIBLE_VALUE, f"{field} {amount:g} is not positive"
            )


def _check_arithmetic(checks: _LineChecks, line: QuotationLine, prices: DerivedPrices) -> None:
    """Stated values that must agree with each other verify together, or fail together:
    arithmetic shows that one of them is wrong, not which."""
    unit, pack, pack_size = line.unit_price, line.pack_price, line.pack_size
    if unit is not None and pack is not None and pack_size is not None:
        fields = ("unit_price", "pack_price", "pack_size")
        if _agrees(unit * pack_size, pack):
            checks.verified_all(fields)
        else:
            checks.failed_all(
                fields,
                ReasonCode.ARITHMETIC_MISMATCH,
                f"Unit price {unit:g} × Pack size {pack_size:g} = {unit * pack_size:g}, "
                f"but Pack price is {pack:g}",
            )
    _check_line_total(checks, line, prices)


def _check_line_total(checks: _LineChecks, line: QuotationLine, prices: DerivedPrices) -> None:
    quantity, total = line.quantity, line.line_total
    # A tier-only line has no single price to multiply: the tier depends on the quantity.
    if quantity is None or total is None or (line.unit_price is None and line.pack_price is None):
        return
    # Quantity may count UOMs or Packs, so either price may produce the total. Each candidate
    # carries the stated fields it rests on; stated prices come first.
    candidates: list[tuple[str, float, tuple[str, ...]]] = []
    if line.unit_price is not None:
        candidates.append(("Unit price", line.unit_price, ("unit_price",)))
    if line.pack_price is not None:
        candidates.append(("Pack price", line.pack_price, ("pack_price",)))
    if line.unit_price is None and prices.headline_unit_price is not None:
        candidates.append(("Unit price", prices.headline_unit_price, ("pack_price", "pack_size")))
    if line.pack_price is None and prices.headline_pack_price is not None:
        candidates.append(("Pack price", prices.headline_pack_price, ("unit_price", "pack_size")))
    keep = 1 - (line.discount_pct or 0) / 100
    involved = ("quantity", "line_total") + (("discount_pct",) if line.discount_pct else ())
    for _, price, fields in candidates:
        if _agrees(quantity * price * keep, total):
            checks.verified_all((*involved, *fields))
            return
    label, price, _ = candidates[0]
    less = f" less {line.discount_pct:g}%" if line.discount_pct else ""
    checks.failed_all(
        involved + tuple(f for f in ("unit_price", "pack_price") if getattr(line, f) is not None),
        ReasonCode.ARITHMETIC_MISMATCH,
        f"Quantity {quantity:g} × {label} {price:g}{less} = {quantity * price * keep:g}, "
        f"but line total is {total:g}",
    )


def _agrees(calculated: float, stated: float) -> bool:
    return abs(calculated - stated) <= _ARITHMETIC_TOLERANCE * abs(stated)


def _is_iso_currency(code: str) -> bool:
    # pycountry's lookup ignores case; a stored code must be the upper-case form itself.
    return (
        code.isupper()
        and code not in _NON_MONEY_CODES
        and pycountry.currencies.get(alpha_3=code) is not None
    )


def _numbers(value: object) -> list[float]:
    """The numbers a value carries, including those nested in list entries (strengths,
    tier prices), each checked against what the Source writes."""
    if isinstance(value, bool):
        return []
    if isinstance(value, int | float):
        return [float(value)]
    if isinstance(value, BaseModel):
        return _numbers(list(value.model_dump().values()))
    if isinstance(value, list):
        return [n for item in value for n in _numbers(item)]
    return []


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= _NUMBER_TOLERANCE * max(1.0, abs(b))
