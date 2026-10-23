"""Reconciliation after the batch (ADR-0003): Conflicts between Sources of one Quotation.

Sources are linked only by normalized supplier + quote reference, and lines within a
Quotation are matched only by Product. A field the matched lines state differently is a
Conflict. Nothing is merged and no value wins: the lines go to review instead.
"""

import json
import re
import unicodedata
from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path

from sdi.read_model import LineView
from sdi.store import ConflictRecord
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
    conflicts = [c for group in quotations.values() for c in _conflicts(group)]
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


def _conflicts(quotation_lines: list[LineView]) -> list[ConflictRecord]:
    # A Source may quote one Product more than once (e.g. two pack sizes); its n-th line
    # for a Product is matched with the n-th of every other Source, in document order.
    matched: dict[tuple[str, int], list[LineView]] = defaultdict(list)
    seen: dict[tuple[str, str], int] = defaultdict(int)
    for line in quotation_lines:
        product = _product_key(line)
        occurrence = seen[(line.source, product)]
        seen[(line.source, product)] += 1
        matched[(product, occurrence)].append(line)

    conflicts = []
    for group in matched.values():
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
    # doesn't, so "Q1-23" and "Q12-3" stay different references.
    return _words(quote_ref)


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
