"""Read model: every query the CLI summary and the review UI need, and nothing else reads
the tables directly."""

import json
from pathlib import Path

from pydantic import BaseModel

from sdi.extraction.schema import FieldEvidence
from sdi.store import ProcessingStatus, connect
from sdi.validation import FieldResult, ReviewReason, ReviewStatus
from sdi.validation.pricing import DerivedPrices


class DocumentStatus(BaseModel):
    source: str
    file_type: str | None
    status: ProcessingStatus
    document_type: str | None
    error: str | None
    line_count: int
    needs_review_count: int


class LineView(DerivedPrices):
    """A stored line: the stated values, its review outcome, and the prices calculated from
    them."""

    id: int
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
    evidence: list[FieldEvidence]
    review_status: ReviewStatus
    review_reasons: list[ReviewReason]


class FieldDetail(BaseModel):
    field: str
    value: object
    evidence: list[FieldEvidence]
    result: FieldResult
    reasons: list[ReviewReason]


class LineDetail(BaseModel):
    line: LineView
    fields: list[FieldDetail]
    # The Source to show next to the fields when the model read it as images: an image file,
    # or a scanned PDF whose pages were rendered. None for a text Source.
    source_image: str | None


class ConflictValue(BaseModel):
    line_id: int
    source: str
    line_no: int
    value: object


class ConflictView(BaseModel):
    """One field the Sources of a Quotation disagree on, each Source's value side by side."""

    id: int
    supplier: str | None
    quote_ref: str | None
    field: str
    inns: list[str]
    active_ingredients: list[dict]
    dosage_form: str | None
    values: list[ConflictValue]


_LINES_QUERY = """
    SELECT l.id, d.source_path AS source, q.supplier, q.quote_ref, l.line_no,
           l.brand_name, l.active_ingredients, l.dosage_form, l.uom,
           l.pack_size, l.unit_price, l.pack_price,
           COALESCE(l.currency, q.currency) AS currency, l.discount_pct,
           l.quantity, l.line_total, l.price_tiers, l.revision_note, l.evidence,
           l.review_status, l.field_verification, q.evidence AS quotation_evidence,
           d.read_as_images,
           {derived_columns},
           (SELECT json_group_array(json_object('code', r.code, 'field', r.field,
                                                'message', r.message))
            FROM (SELECT * FROM review_reasons WHERE line_id = l.id ORDER BY id) r
           ) AS review_reasons
    FROM quotation_lines l
    JOIN quotations q ON q.id = l.quotation_id
    JOIN documents d ON d.id = q.document_id
""".format(derived_columns=", ".join(f"l.{field}" for field in DerivedPrices.model_fields))


class ReadModel:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)

    def document_statuses(self) -> list[DocumentStatus]:
        with connect(self.db_path) as conn:
            rows = conn.execute(
                """SELECT d.source_path AS source, d.file_type, d.status, d.document_type,
                          d.error, COUNT(l.id) AS line_count,
                          COUNT(CASE WHEN l.review_status = 'needs_review' THEN 1 END)
                              AS needs_review_count
                   FROM documents d
                   LEFT JOIN quotations q ON q.document_id = d.id
                   LEFT JOIN quotation_lines l ON l.quotation_id = q.id
                   GROUP BY d.id
                   ORDER BY d.source_path"""
            ).fetchall()
        return [DocumentStatus(**dict(r)) for r in rows]

    def quotation_lines(self) -> list[LineView]:
        return [view for view, _ in self._lines("ORDER BY d.source_path, l.line_no")]

    def review_queue(
        self,
        *,
        supplier: str | None = None,
        review_status: ReviewStatus | None = None,
        source: str | None = None,
        reason_code: str | None = None,
    ) -> list[LineView]:
        """Lines to review, `needs_review` first; each filter is ignored when None."""
        return [
            view
            for view, _ in self._lines(
                """WHERE (:supplier IS NULL OR q.supplier = :supplier)
                     AND (:review_status IS NULL OR l.review_status = :review_status)
                     AND (:source IS NULL OR d.source_path = :source)
                     AND (:reason_code IS NULL OR EXISTS (
                          SELECT 1 FROM review_reasons r
                          WHERE r.line_id = l.id AND r.code = :reason_code))
                   ORDER BY l.review_status = 'needs_review' DESC, d.source_path, l.line_no""",
                {
                    "supplier": supplier,
                    "review_status": review_status,
                    "source": source,
                    "reason_code": reason_code,
                },
            )
        ]

    def line_detail(self, line_id: int) -> LineDetail:
        """Each verified field's value, its evidence, its verification result and the
        reasons raised against it."""
        [(view, row)] = self._lines("WHERE l.id = :id", {"id": line_id})
        fields = []
        for field, result in json.loads(row["field_verification"]).items():
            evidence = [e for e in view.evidence if e.field == field]
            if field == "currency" and not evidence:  # inherited from the Quotation
                evidence = [
                    FieldEvidence(**e)
                    for e in json.loads(row["quotation_evidence"])
                    if e["field"] == "currency"
                ]
            fields.append(
                FieldDetail(
                    field=field,
                    value=getattr(view, field),
                    evidence=evidence,
                    result=result,
                    reasons=[r for r in view.review_reasons if r.field == field],
                )
            )
        return LineDetail(
            line=view, fields=fields, source_image=view.source if row["read_as_images"] else None
        )

    def conflicts(self) -> list[ConflictView]:
        with connect(self.db_path) as conn:
            rows = conn.execute(
                """SELECT c.id, c.supplier, c.quote_ref, c.field, v.line_id, v.value,
                          d.source_path AS source, l.line_no, l.active_ingredients,
                          l.dosage_form
                   FROM conflicts c
                   JOIN conflict_values v ON v.conflict_id = c.id
                   JOIN quotation_lines l ON l.id = v.line_id
                   JOIN quotations q ON q.id = l.quotation_id
                   JOIN documents d ON d.id = q.document_id
                   ORDER BY c.id, d.source_path, l.line_no"""
            ).fetchall()
        views: dict[int, ConflictView] = {}
        for r in rows:
            if r["id"] not in views:
                ingredients = json.loads(r["active_ingredients"])
                views[r["id"]] = ConflictView(
                    id=r["id"],
                    supplier=r["supplier"],
                    quote_ref=r["quote_ref"],
                    field=r["field"],
                    inns=[i["inn"] for i in ingredients],
                    active_ingredients=ingredients,
                    dosage_form=r["dosage_form"],
                    values=[],
                )
            views[r["id"]].values.append(
                ConflictValue(
                    line_id=r["line_id"],
                    source=r["source"],
                    line_no=r["line_no"],
                    value=json.loads(r["value"]),
                )
            )
        return list(views.values())

    def _lines(self, clause: str, params: dict | None = None) -> list[tuple[LineView, dict]]:
        with connect(self.db_path) as conn:
            rows = conn.execute(_LINES_QUERY + clause, params or {}).fetchall()
        views = []
        for r in rows:
            row = dict(r)
            for col in ("active_ingredients", "price_tiers", "evidence", "review_reasons"):
                row[col] = json.loads(row[col])
            row["inns"] = [i["inn"] for i in row["active_ingredients"]]
            views.append((LineView(**row), row))
        return views
