"""Reconciliation after the batch (ADR-0003): Conflicts between Sources of one Quotation,
and Price comparisons across Quotations.

Sources are linked only by normalized supplier + quote reference, and lines within a
Quotation are matched only by Product. A field the matched lines state differently is a
Conflict. Nothing is merged and no value wins: the lines go to review instead.

Lines for one Product from different Quotations form a Price comparison. Their prices
differing is a market, not an error, so a comparison never changes a review status.
"""

import json
import re
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Sequence
from pathlib import Path

from sdi.read_model import LineView
from sdi.store import ConflictRecord, PriceComparisonRecord
from sdi.validation import ReasonCode, ReviewReason

# Line fields Sources can disagree on. Ingredients, strengths and dosage form are not
# here: they are the Product key, so lines that differ on them are never matched.
COMPARED_FIELDS = (
    "brand_name",
    "uom",
    "pack_size",
    "unit_price",
    "pack_price",
    "currency",
    "discount_pct",
    "quantity",
    "line_total",
    "price_tiers",
)

# Legal-form words dropped from supplier names, so "Acme Ltd" and "ACME Limited" link.
_LEGAL_FORMS = frozenset(
    {
        "ag", "bv", "co", "company", "corp", "corporation", "gmbh", "inc", "incorporated",
        "limited", "llc", "ltd", "ltda", "nv", "plc", "pte", "pty", "pvt", "sa", "sas",
        "sl", "spa", "srl",
    }
)  # fmt: skip

Reasons = list[tuple[int, ReviewReason]]


def reconcile(lines: Sequence[LineView]) -> tuple[list[ConflictRecord], Reasons]:
    """The Conflicts among stored lines, and the Review reasons they and unlinked Sources
    raise. Lines are expected in Source and line order."""
    reasons: Reasons = []
    quotations: dict[tuple[str, str], list[LineView]] = defaultdict(list)
    for line in lines:
        supplier, ref = _supplier_key(line.supplier), _quote_ref_key(line.quote_ref)
        if supplier and ref:
            quotations[(supplier, ref)].append(line)
            continue
        code, missing = (
            (ReasonCode.NO_SUPPLIER, "supplier")
            if not supplier
            else (ReasonCode.NO_QUOTE_REFERENCE, "quote reference")
        )
        message = (
            f"No {missing}, so this Source isn't linked to other Sources of the same Quotation"
        )
        reasons.append((line.id, ReviewReason(code=code, field=None, message=message)))
    conflicts = []
    for quotation_lines in quotations.values():
        groups = _matched(quotation_lines)
        conflicts += _conflicts(groups)
        reasons += _unmatched(groups, list(dict.fromkeys(line.source for line in quotation_lines)))
    for conflict in conflicts:
        message = _conflict_message(conflict, {line.id: line for line in lines})
        for line_id in conflict.values:
            reasons.append(
                (
                    line_id,
                    ReviewReason(
                        code=ReasonCode.IN_CONFLICT, field=conflict.field, message=message
                    ),
                )
            )
    return conflicts, reasons


def price_comparisons(lines: Sequence[LineView]) -> list[PriceComparisonRecord]:
    """Lines grouped by Product, for each Product quoted in more than one Quotation. Lines
    are expected in Source and line order."""
    by_product: dict[str, list[LineView]] = defaultdict(list)
    for line in lines:
        if _is_identified(line):
            by_product[_product_key(line)].append(line)
    return [
        PriceComparisonRecord(line_ids=[line.id for line in group], incoterm_warning=None)
        for group in by_product.values()
        if len({_quotation_key(line) for line in group}) > 1
    ]


def _is_identified(line: LineView) -> bool:
    """Every part of the Product key was read. Two lines missing the same part aren't
    known to be the same Product, so they aren't compared."""
    return (
        bool(line.active_ingredients)
        and all(
            i.get("inn") and i.get("strength_value") is not None for i in line.active_ingredients
        )
        and bool(_words(line.dosage_form))
    )


def _quotation_key(line: LineView) -> tuple[str, str]:
    """The Quotation a line belongs to. A Source that can't be linked (no supplier or
    reference) is a Quotation of its own."""
    supplier, ref = _supplier_key(line.supplier), _quote_ref_key(line.quote_ref)
    return (supplier, ref) if supplier and ref else ("", line.source)


def _matched(quotation_lines: list[LineView]) -> list[list[LineView]]:
    """Lines of one Quotation grouped by Product, each group at most one line per Source."""
    by_product: dict[str, dict[str, list[LineView]]] = defaultdict(lambda: defaultdict(list))
    for line in quotation_lines:
        by_product[_product_key(line)][line.source].append(line)
    groups = []
    for per_source in by_product.values():
        if all(len(lines) == 1 for lines in per_source.values()):
            groups.append([lines[0] for lines in per_source.values()])
            continue
        # A Source quotes the Product more than once (e.g. two pack sizes): pair lines by
        # pack size, then by document order among lines with the same or no pack size.
        by_pack: dict[tuple[float | None, int], list[LineView]] = defaultdict(list)
        for lines in per_source.values():
            seen: Counter[float | None] = Counter()
            for line in lines:
                by_pack[(line.pack_size, seen[line.pack_size])].append(line)
                seen[line.pack_size] += 1
        groups.extend(by_pack.values())
    return groups


def _conflicts(groups: list[list[LineView]]) -> list[ConflictRecord]:
    conflicts = []
    for group in groups:
        if len(group) < 2:
            continue
        for field in COMPARED_FIELDS:
            # A value missing from one Source (unreadable, not stated) isn't a disagreement.
            stated = {line.id: getattr(line, field) for line in group}
            stated = {i: v for i, v in stated.items() if v is not None and v != []}
            if len({_comparable(v) for v in stated.values()}) > 1:
                first = group[0]
                conflicts.append(
                    ConflictRecord(
                        supplier=first.supplier,
                        quote_ref=first.quote_ref,
                        field=field,
                        values=stated,
                    )
                )
    return conflicts


def _unmatched(groups: list[list[LineView]], sources: list[str]) -> Reasons:
    """A note on each line that some linked Source has no line to compare with: a Product
    key that differs (e.g. a strength one copy couldn't read) would otherwise hide a
    Conflict. A note only, since a partial copy legitimately lacks lines."""
    reasons: Reasons = []
    for group in groups:
        missing = [Path(s).name for s in sources if s not in {line.source for line in group}]
        if not missing:
            continue
        message = (
            f"No matching line in {', '.join(missing)}, so this line isn't compared with "
            "that Source of the same Quotation"
        )
        for line in group:
            reason = ReviewReason(
                code=ReasonCode.UNMATCHED_IN_QUOTATION, field=None, message=message
            )
            reasons.append((line.id, reason))
    return reasons


def _conflict_message(conflict: ConflictRecord, lines: dict[int, LineView]) -> str:
    values = ", ".join(
        f"{_display(value)} in {Path(lines[line_id].source).name}"
        for line_id, value in conflict.values.items()
    )
    return f"Sources of quotation {conflict.quote_ref} disagree on {conflict.field}: {values}"


def _product_key(line: LineView) -> str:
    ingredients = sorted(
        f"{_words(i.get('inn'))} {i.get('strength_value')} {_words(i.get('strength_unit'), '')}"
        for i in line.active_ingredients
    )
    return " + ".join(ingredients) + " | " + _words(line.dosage_form)


def _supplier_key(supplier: str | None) -> str:
    return " ".join(w for w in _words(supplier).split() if w not in _LEGAL_FORMS)


def _quote_ref_key(quote_ref: str | None) -> str:
    # Separator characters vary between copies ("Q-12/A" vs "Q 12 A") but where they fall
    # doesn't, so "Q1-23" and "Q12-3" stay different references. A reference without a
    # digit is a placeholder ("N/A", "TBD"), not something two Sources can share.
    key = _words(quote_ref)
    return key if any(c.isdigit() for c in key) else ""


def _words(text: str | None, sep: str = " ") -> str:
    """Lower-case letters and digits only, accents removed, runs joined by `sep`."""
    if text is None:
        return ""
    # The micro sign has no ASCII form and would vanish, turning "µg" into "g".
    text = text.replace("\u00b5", "mc").replace("\u03bc", "mc")
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return sep.join(re.findall(r"[a-z0-9]+", plain.casefold()))


def _comparable(value: object) -> str:
    if isinstance(value, str):
        return _words(value)
    if isinstance(value, float):
        return f"{value:.9g}"
    return json.dumps(value, sort_keys=True)


def _display(value: object) -> str:
    if isinstance(value, float):
        return f"{value:g}"
    if isinstance(value, list):
        return json.dumps(value)
    return str(value)
