"""Read model: every query the CLI summary and the review UI need, and nothing else reads
the tables directly."""

import json
import sqlite3
from collections import Counter
from datetime import date
from functools import partial
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from sdi.extraction.schema import FieldEvidence, QuotationLine
from sdi.store import ProcessingStatus, cached_fx_rate, connect
from sdi.validation import FieldResult, ReasonCode, ReviewReason
from sdi.validation.pricing import DerivedPrices, convert_to_usd, derive_prices

# A line's review status: the pipeline's (needs_review or auto_ok), or reviewed once a
# reviewer approves it and no Review reason has been raised since.
LineStatus = Literal["needs_review", "auto_ok", "reviewed"]


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
    rfq_ref: str | None
    issue_date: date | None
    valid_until: date | None
    incoterm: str | None
    incoterm_place: str | None
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
    review_status: LineStatus
    review_reasons: list[ReviewReason]
    # The extracted value of each field a reviewer corrected; the fields above hold the
    # current value, the latest correction.
    extracted_values: dict[str, object]


class ReviewDecision(BaseModel):
    id: int
    source: str
    line_no: int
    reviewer: str
    decided_at: str
    action: Literal["approve", "correct"]
    field: str | None
    old_value: object
    new_value: object
    # False once the Source has been read again: the decision was about lines since replaced.
    current: bool


class FieldDetail(BaseModel):
    field: str
    value: object
    extracted_value: object
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
    # None on a Quotation term: the value is the Source's, for every line in it.
    line_id: int | None
    source: str
    line_no: int | None
    value: object


class ConflictView(BaseModel):
    """One field the Sources of a Quotation disagree on, each Source's value side by side.
    A Quotation term has no Product: its ingredients are empty and its dosage form None."""

    id: int
    supplier: str | None
    quote_ref: str | None
    field: str
    scope: Literal["line", "quotation"]
    inns: list[str]
    active_ingredients: list[dict]
    dosage_form: str | None
    values: list[ConflictValue]


class PriceComparisonView(BaseModel):
    """Lines for one Product from different Quotations. Each line's `usd_per_uom` is the
    normalized price: USD per UOM, before discount, at the lowest-volume tier."""

    id: int
    inns: list[str]
    active_ingredients: list[dict]
    dosage_form: str | None
    incoterm_warning: str | None
    lines: list[LineView]


_LINES_QUERY = """
    SELECT l.id, d.source_path AS source, q.supplier, q.quote_ref, q.rfq_ref,
           q.valid_until, q.incoterm, q.incoterm_place, l.line_no,
           l.brand_name, l.active_ingredients, l.dosage_form, l.uom,
           l.pack_size, l.unit_price, l.pack_price,
           COALESCE(l.currency, q.currency) AS currency, q.currency AS quotation_currency,
           l.discount_pct,
           l.quantity, l.line_total, l.price_tiers, l.revision_note, l.evidence,
           l.review_status, l.field_verification, q.evidence AS quotation_evidence,
           q.issue_date,
           d.read_as_images,
           {derived_columns},
           (SELECT json_group_array(json_object('code', r.code, 'field', r.field,
                                                'message', r.message))
            FROM (SELECT * FROM review_reasons WHERE line_id = l.id ORDER BY id) r
           ) AS review_reasons,
           (SELECT json_group_array(json_object('action', rd.action, 'field', rd.field,
                                                'new_value', json(rd.new_value),
                                                'reasons_seen', json(rd.reasons_seen)))
            FROM (SELECT * FROM review_decisions
                  WHERE extraction_id = d.extraction_id AND line_no = l.line_no ORDER BY id) rd
           ) AS decisions
    FROM quotation_lines l
    JOIN quotations q ON q.id = l.quotation_id
    JOIN documents d ON d.id = q.document_id
""".format(derived_columns=", ".join(f"l.{field}" for field in DerivedPrices.model_fields))


class ReadModel:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)

    def document_statuses(self) -> list[DocumentStatus]:
        needs_review = Counter(
            line.source for line in self.quotation_lines() if line.review_status == "needs_review"
        )
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
        return [
            DocumentStatus(**dict(r), needs_review_count=needs_review[r["source"]]) for r in rows
        ]

    def quotation_lines(self, *, corrected: bool = True) -> list[LineView]:
        """Every line; with `corrected` False, as extracted, ignoring reviewers' corrections."""
        return [
            view
            for view, _ in self._lines("ORDER BY d.source_path, l.line_no", corrected=corrected)
        ]

    def review_queue(
        self,
        *,
        supplier: str | None = None,
        review_status: LineStatus | None = None,
        source: str | None = None,
        reason_code: str | None = None,
    ) -> list[LineView]:
        """Lines to review, `needs_review` first; each filter is ignored when None."""
        lines = [
            view
            for view, _ in self._lines(
                """WHERE (:supplier IS NULL OR q.supplier = :supplier)
                     AND (:source IS NULL OR d.source_path = :source)
                     AND (:reason_code IS NULL OR EXISTS (
                          SELECT 1 FROM review_reasons r
                          WHERE r.line_id = l.id AND r.code = :reason_code))
                   ORDER BY d.source_path, l.line_no""",
                {"supplier": supplier, "source": source, "reason_code": reason_code},
            )
            if review_status is None or view.review_status == review_status
        ]
        return sorted(lines, key=lambda line: line.review_status != "needs_review")

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
                    extracted_value=view.extracted_values.get(field, getattr(view, field)),
                    evidence=evidence,
                    result=result,
                    reasons=[r for r in view.review_reasons if r.field == field],
                )
            )
        return LineDetail(
            line=view, fields=fields, source_image=view.source if row["read_as_images"] else None
        )

    def source_images(self, source: str) -> list[bytes]:
        """The Source as the pipeline gave it to the model: the image, or each rendered page
        of a scanned PDF. Read from the file, which raises OSError once it has moved."""
        from sdi.loaders import load  # pdf and image libraries, needed only here

        return load(Path(source)).images

    def conflicts(self) -> list[ConflictView]:
        with connect(self.db_path) as conn:
            rows = conn.execute(
                """SELECT c.id, c.supplier, c.quote_ref, c.field, c.scope, v.line_id, v.value,
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
            term = r["scope"] == "quotation"
            if r["id"] not in views:
                ingredients = [] if term else json.loads(r["active_ingredients"])
                views[r["id"]] = ConflictView(
                    id=r["id"],
                    supplier=r["supplier"],
                    quote_ref=r["quote_ref"],
                    field=r["field"],
                    scope=r["scope"],
                    inns=[i["inn"] for i in ingredients],
                    active_ingredients=ingredients,
                    dosage_form=None if term else r["dosage_form"],
                    values=[],
                )
            values = views[r["id"]].values
            if term and r["source"] in {v.source for v in values}:
                continue  # one value per Source, stored on each of its lines
            values.append(
                ConflictValue(
                    line_id=None if term else r["line_id"],
                    source=r["source"],
                    line_no=None if term else r["line_no"],
                    value=json.loads(r["value"]),
                )
            )
        return list(views.values())

    def decisions(self, line_id: int) -> list[ReviewDecision]:
        """The review decisions that apply to the line, oldest first."""
        return self._decisions("WHERE l.id = :line_id", {"line_id": line_id})

    def decision_log(self) -> list[ReviewDecision]:
        """Every review decision ever made, oldest first, including those on an earlier
        reading of a Source that no longer apply."""
        return self._decisions()

    def _decisions(self, clause: str = "", params: dict | None = None) -> list[ReviewDecision]:
        with connect(self.db_path) as conn:
            rows = conn.execute(
                """SELECT rd.id, rd.source_path AS source, rd.line_no, rd.reviewer,
                          rd.decided_at, rd.action, rd.field, rd.old_value, rd.new_value,
                          l.id IS NOT NULL AS current
                   FROM review_decisions rd
                   LEFT JOIN documents d ON d.extraction_id = rd.extraction_id
                   LEFT JOIN quotations q ON q.document_id = d.id
                   LEFT JOIN quotation_lines l
                       ON l.quotation_id = q.id AND l.line_no = rd.line_no
                   """
                + clause
                + " ORDER BY rd.id",
                params or {},
            ).fetchall()
        return [
            ReviewDecision(
                **{
                    **dict(r),
                    "old_value": _json(r["old_value"]),
                    "new_value": _json(r["new_value"]),
                }
            )
            for r in rows
        ]

    def price_comparisons(self) -> list[PriceComparisonView]:
        with connect(self.db_path) as conn:
            rows = conn.execute(
                """SELECT c.id, c.incoterm_warning, p.line_id
                   FROM price_comparisons c
                   JOIN price_comparison_lines p ON p.comparison_id = c.id
                   ORDER BY c.id, p.id"""
            ).fetchall()
        lines = {
            view.id: view
            for view, _ in self._lines("WHERE l.id IN (SELECT line_id FROM price_comparison_lines)")
        }
        views: dict[int, PriceComparisonView] = {}
        for r in rows:
            line = lines[r["line_id"]]
            if r["id"] not in views:
                views[r["id"]] = PriceComparisonView(
                    id=r["id"],
                    inns=line.inns,
                    active_ingredients=line.active_ingredients,
                    dosage_form=line.dosage_form,
                    incoterm_warning=r["incoterm_warning"],
                    lines=[],
                )
            views[r["id"]].lines.append(line)
        return list(views.values())

    def _lines(
        self, clause: str, params: dict | None = None, *, corrected: bool = True
    ) -> list[tuple[LineView, dict]]:
        """Each line with its current values: the latest correction of a field, else the
        extracted value. With `corrected` False, the extracted values only."""
        with connect(self.db_path) as conn:
            rows = conn.execute(_LINES_QUERY + clause, params or {}).fetchall()
            views = []
            for r in rows:
                row = dict(r)
                for col in ("active_ingredients", "price_tiers", "evidence", "review_reasons"):
                    row[col] = json.loads(row[col])
                decisions = json.loads(row.pop("decisions"))
                row["review_status"] = _line_status(row, decisions)
                corrections = {
                    d["field"]: d["new_value"]
                    for d in decisions
                    if corrected and d["action"] == "correct"
                }
                row["extracted_values"] = {field: row[field] for field in corrections}
                row.update(corrections)
                if row["currency"] is None:  # a line with no currency of its own
                    row["currency"] = row["quotation_currency"]
                if corrections:
                    _reprice(conn, row)
                row["inns"] = [i["inn"] for i in row["active_ingredients"]]
                views.append((LineView(**row), row))
        return views


def _line_status(row: dict, decisions: list[dict]) -> LineStatus:
    """Reviewed when the latest decision is an approval and every stored Review reason was
    there when it was given; otherwise the pipeline's status."""
    if decisions and decisions[-1]["action"] == "approve":
        seen = {tuple(reason) for reason in decisions[-1]["reasons_seen"] or []}
        if all((r["code"], r["field"]) in seen for r in row["review_reasons"]):
            return "reviewed"
    return row["review_status"]


def _reprice(conn: sqlite3.Connection, row: dict) -> None:
    """Recalculate a corrected line's prices from its current values, converted at the rate
    ingest cached for its currency and issue date. With no cached rate the USD price is left
    empty and a Review reason says why: a correction never fetches or estimates a rate."""
    prices = derive_prices(QuotationLine.model_validate(row))
    if row["currency"] is not None:
        issue_date = None if row["issue_date"] is None else date.fromisoformat(row["issue_date"])
        rate = partial(cached_fx_rate, conn)
        if (missing := convert_to_usd(prices, row["currency"], issue_date, rate)) is not None:
            row["review_reasons"].append(
                ReviewReason(code=ReasonCode.FX_UNAVAILABLE, field="currency", message=missing)
            )
    row.update(prices.model_dump())


def _json(value: str | None) -> object:
    return None if value is None else json.loads(value)
