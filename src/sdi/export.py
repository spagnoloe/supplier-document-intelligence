"""CSV export of every Quotation line with reviewers' corrections applied."""

import csv
from typing import TextIO

from sdi.read_model import LineView, ReadModel

COLUMNS = (
    "source",
    "line_no",
    "supplier",
    "quote_ref",
    "brand_name",
    "active_ingredients",
    "dosage_form",
    "uom",
    "pack_size",
    "unit_price",
    "pack_price",
    "currency",
    "discount_pct",
    "quantity",
    "line_total",
    "price_tiers",
    "revision_note",
    "headline_unit_price",
    "headline_pack_price",
    "net_unit_price",
    "net_pack_price",
    "usd_per_uom",
    "fx_rate",
    "fx_rate_date",
    "review_status",
    "approved",
    "corrected_fields",
    "review_reasons",
)


def export_csv(read_model: ReadModel, out: TextIO) -> None:
    """One row per line: its current values, review status, Review reasons, and the Source and
    line number it was read from."""
    writer = csv.DictWriter(out, fieldnames=COLUMNS)
    writer.writeheader()
    for line in read_model.quotation_lines():
        writer.writerow(_row(line))


def _row(line: LineView) -> dict[str, str]:
    values = line.model_dump(include=set(COLUMNS))
    values["active_ingredients"] = " + ".join(
        " ".join(
            part
            for part in (i["inn"], _cell(i.get("strength_value")), i.get("strength_unit"))
            if part
        )
        for i in line.active_ingredients
    )
    values["price_tiers"] = "; ".join(
        ", ".join(f"{k}={_cell(v)}" for k, v in tier.items() if v is not None)
        for tier in line.price_tiers
    )
    values["approved"] = "yes" if line.approved else "no"
    values["corrected_fields"] = "; ".join(line.extracted)
    values["review_reasons"] = "; ".join(
        f"{r.code}({r.field}): {r.message}" if r.field else f"{r.code}: {r.message}"
        for r in line.review_reasons
    )
    return {column: _cell(values[column]) for column in COLUMNS}


def _cell(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else repr(value)
    return str(value)
