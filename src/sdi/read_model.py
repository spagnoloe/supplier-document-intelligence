"""Read model: every query the CLI summary and the review UI need, and nothing else reads
the tables directly."""

import json
from pathlib import Path

from pydantic import BaseModel

from sdi.store import connect


class DocumentStatus(BaseModel):
    source: str
    file_type: str | None
    status: str
    document_type: str | None
    error: str | None
    line_count: int


class LineView(BaseModel):
    source: str
    supplier: str | None
    quote_ref: str | None
    line_no: int
    brand_name: str | None
    inns: list[str]
    active_ingredients: list[dict]
    dosage_form: str | None
    uom: str | None
    pack_size: float | None
    unit_price: float | None
    pack_price: float | None
    currency: str | None
    discount_pct: float | None
    quantity: float | None
    line_total: float | None
    price_tiers: list[dict]
    revision_note: str | None
    evidence: list[dict]


class ReadModel:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)

    def document_statuses(self) -> list[DocumentStatus]:
        with connect(self.db_path) as conn:
            rows = conn.execute(
                """SELECT d.source_path AS source, d.file_type, d.status, d.document_type,
                          d.error, COUNT(l.id) AS line_count
                   FROM documents d
                   LEFT JOIN quotations q ON q.document_id = d.id
                   LEFT JOIN quotation_lines l ON l.quotation_id = q.id
                   GROUP BY d.id
                   ORDER BY d.source_path"""
            ).fetchall()
        return [DocumentStatus(**dict(r)) for r in rows]

    def quotation_lines(self) -> list[LineView]:
        with connect(self.db_path) as conn:
            rows = conn.execute(
                """SELECT d.source_path AS source, q.supplier, q.quote_ref, l.line_no,
                          l.brand_name, l.active_ingredients, l.dosage_form, l.uom,
                          l.pack_size, l.unit_price, l.pack_price,
                          COALESCE(l.currency, q.currency) AS currency, l.discount_pct,
                          l.quantity, l.line_total, l.price_tiers, l.revision_note, l.evidence
                   FROM quotation_lines l
                   JOIN quotations q ON q.id = l.quotation_id
                   JOIN documents d ON d.id = q.document_id
                   ORDER BY d.source_path, l.line_no"""
            ).fetchall()
        views = []
        for r in rows:
            row = dict(r)
            for col in ("active_ingredients", "price_tiers", "evidence"):
                row[col] = json.loads(row[col])
            row["inns"] = [i["inn"] for i in row["active_ingredients"]]
            views.append(LineView(**row))
        return views
