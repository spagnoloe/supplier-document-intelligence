"""SQLite store: the source of truth for ingested Sources, Quotations and Quotation lines.

Writes are per Source and replace whatever an earlier run stored for it, so re-running a
folder never duplicates rows.
"""

import json
import sqlite3
from contextlib import closing, contextmanager
from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from sdi.extraction.schema import Quotation
from sdi.fx import FxRate
from sdi.validation import LineVerification, ReviewReason

ProcessingStatus = Literal["processed", "failed", "skipped_unsupported_type"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY,
    source_path TEXT NOT NULL UNIQUE,
    file_type TEXT,
    status TEXT NOT NULL CHECK (status IN ('processed', 'failed', 'skipped_unsupported_type')),
    document_type TEXT,
    error TEXT,
    -- 1 when the model read the Source as images (a photo or a scanned PDF).
    read_as_images INTEGER NOT NULL DEFAULT 0 CHECK (read_as_images IN (0, 1)),
    processed_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS quotations (
    id INTEGER PRIMARY KEY,
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    supplier TEXT,
    quote_ref TEXT,
    rfq_ref TEXT,
    issue_date TEXT,
    valid_until TEXT,
    currency TEXT,
    incoterm TEXT,
    incoterm_place TEXT,
    evidence TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS quotation_lines (
    id INTEGER PRIMARY KEY,
    quotation_id INTEGER NOT NULL REFERENCES quotations(id) ON DELETE CASCADE,
    line_no INTEGER NOT NULL,
    brand_name TEXT,
    active_ingredients TEXT NOT NULL,
    dosage_form TEXT,
    uom TEXT,
    pack_size REAL,
    unit_price REAL,
    pack_price REAL,
    currency TEXT,
    discount_pct REAL,
    quantity REAL,
    line_total REAL,
    price_tiers TEXT NOT NULL,
    revision_note TEXT,
    evidence TEXT NOT NULL,
    -- verification_status is this Source's own outcome; review_status adds reconciliation.
    verification_status TEXT NOT NULL
        CHECK (verification_status IN ('needs_review', 'auto_ok')),
    review_status TEXT NOT NULL CHECK (review_status IN ('needs_review', 'auto_ok')),
    field_verification TEXT NOT NULL,
    headline_unit_price REAL,
    headline_pack_price REAL,
    net_unit_price REAL,
    net_pack_price REAL,
    usd_per_uom REAL,
    fx_rate REAL,
    fx_rate_date TEXT,
    fx_source TEXT
);
CREATE TABLE IF NOT EXISTS fx_rates (
    currency TEXT NOT NULL,
    requested_date TEXT NOT NULL,
    rate REAL NOT NULL,
    rate_date TEXT NOT NULL,
    source TEXT NOT NULL,
    PRIMARY KEY (currency, requested_date)
);
-- No CHECK on code: new Review reason codes need no schema change.
CREATE TABLE IF NOT EXISTS review_reasons (
    id INTEGER PRIMARY KEY,
    line_id INTEGER NOT NULL REFERENCES quotation_lines(id) ON DELETE CASCADE,
    origin TEXT NOT NULL CHECK (origin IN ('verification', 'reconciliation')),
    code TEXT NOT NULL,
    field TEXT,
    message TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS conflicts (
    id INTEGER PRIMARY KEY,
    supplier TEXT,
    quote_ref TEXT,
    field TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS conflict_values (
    id INTEGER PRIMARY KEY,
    conflict_id INTEGER NOT NULL REFERENCES conflicts(id) ON DELETE CASCADE,
    line_id INTEGER NOT NULL REFERENCES quotation_lines(id) ON DELETE CASCADE,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS price_comparisons (
    id INTEGER PRIMARY KEY,
    -- Set when the lines' Incoterms differ, so their prices aren't like-for-like.
    incoterm_warning TEXT
);
CREATE TABLE IF NOT EXISTS price_comparison_lines (
    id INTEGER PRIMARY KEY,
    comparison_id INTEGER NOT NULL REFERENCES price_comparisons(id) ON DELETE CASCADE,
    line_id INTEGER NOT NULL REFERENCES quotation_lines(id) ON DELETE CASCADE
);
"""


class ConflictRecord(BaseModel):
    """One field that matched lines of one Quotation disagree on, with each line's value."""

    supplier: str | None
    quote_ref: str | None
    field: str
    values: dict[int, object]  # line id → that line's value


class PriceComparisonRecord(BaseModel):
    """Lines for one Product from different Quotations, to be set side by side."""

    line_ids: list[int]
    incoterm_warning: str | None


# SQL value for a column added since a database was created, for the rows it already holds.
# Before reconciliation existed, a line's own verification outcome was its review status, and
# every reason came from verification.
_BACKFILLS = {
    ("quotation_lines", "verification_status"): "review_status",
    ("review_reasons", "origin"): "'verification'",
    ("documents", "read_as_images"): "0",
}


@contextmanager
def connect(db_path: Path):
    with closing(sqlite3.connect(db_path, timeout=30)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        with conn:
            yield conn


class Store:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with connect(self.db_path) as conn:
            conn.executescript(SCHEMA)
            _add_missing_columns(conn)

    def save_processed(
        self,
        source_path: str,
        file_type: str | None,
        quotation: Quotation,
        verifications: list[LineVerification],
        *,
        read_as_images: bool = False,
    ) -> None:
        with connect(self.db_path) as conn:
            doc_id = self._replace_document(
                conn,
                source_path,
                file_type,
                "processed",
                quotation.document_type,
                read_as_images=read_as_images,
            )
            terms = quotation.model_dump(mode="json", exclude={"document_type", "lines"})
            quotation_id = _insert(conn, "quotations", {"document_id": doc_id, **terms})
            lines = zip(quotation.lines, verifications, strict=True)
            for line_no, (line, verification) in enumerate(lines, start=1):
                values = line.model_dump(mode="json")
                line_id = _insert(
                    conn,
                    "quotation_lines",
                    {
                        "quotation_id": quotation_id,
                        "line_no": line_no,
                        **values,
                        "verification_status": verification.review_status,
                        "review_status": verification.review_status,
                        "field_verification": verification.fields,
                        **verification.prices.model_dump(mode="json"),
                    },
                )
                for reason in verification.reasons:
                    _insert_reason(conn, line_id, "verification", reason)

    def save_reconciliation(
        self,
        conflicts: list[ConflictRecord],
        reasons: list[tuple[int, ReviewReason]],
        comparisons: list[PriceComparisonRecord],
    ) -> None:
        """Replace every earlier reconciliation outcome, so a re-ingested Source never leaves
        stale Conflicts, Price comparisons or flags on the lines it was compared with."""
        with connect(self.db_path) as conn:
            conn.execute("DELETE FROM conflicts")
            conn.execute("DELETE FROM price_comparisons")
            conn.execute("DELETE FROM review_reasons WHERE origin = 'reconciliation'")
            conn.execute("UPDATE quotation_lines SET review_status = verification_status")
            for conflict in conflicts:
                conflict_id = _insert(conn, "conflicts", conflict.model_dump(exclude={"values"}))
                for line_id, value in conflict.values.items():
                    _insert(
                        conn,
                        "conflict_values",
                        {
                            "conflict_id": conflict_id,
                            "line_id": line_id,
                            "value": json.dumps(value),
                        },
                    )
            for comparison in comparisons:
                comparison_id = _insert(
                    conn, "price_comparisons", comparison.model_dump(exclude={"line_ids"})
                )
                for line_id in comparison.line_ids:
                    _insert(
                        conn,
                        "price_comparison_lines",
                        {"comparison_id": comparison_id, "line_id": line_id},
                    )
            for line_id, reason in reasons:
                _insert_reason(conn, line_id, "reconciliation", reason)
            # A line in a Conflict always needs review (ADR-0004).
            conn.execute(
                """UPDATE quotation_lines SET review_status = 'needs_review'
                   WHERE id IN (SELECT line_id FROM conflict_values)"""
            )

    def fx_rate(self, currency: str, on: date) -> FxRate | None:
        with connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT rate, rate_date, source FROM fx_rates"
                " WHERE currency = ? AND requested_date = ?",
                (currency, on.isoformat()),
            ).fetchone()
        return None if row is None else FxRate(**dict(row))

    def save_fx_rate(self, currency: str, on: date, rate: FxRate) -> None:
        with connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO fx_rates VALUES (?, ?, ?, ?, ?)",
                (currency, on.isoformat(), rate.rate, rate.rate_date.isoformat(), rate.source),
            )

    def save_skipped(self, source_path: str, file_type: str | None, document_type: str) -> None:
        with connect(self.db_path) as conn:
            self._replace_document(
                conn, source_path, file_type, "skipped_unsupported_type", document_type
            )

    def save_failed(self, source_path: str, file_type: str | None, error: str) -> None:
        with connect(self.db_path) as conn:
            self._replace_document(conn, source_path, file_type, "failed", None, error)

    @staticmethod
    def _replace_document(
        conn: sqlite3.Connection,
        source_path: str,
        file_type: str | None,
        status: ProcessingStatus,
        document_type: str | None,
        error: str | None = None,
        *,
        read_as_images: bool = False,
    ) -> int:
        conn.execute("DELETE FROM documents WHERE source_path = ?", (source_path,))
        return _insert(
            conn,
            "documents",
            {
                "source_path": source_path,
                "file_type": file_type,
                "status": status,
                "document_type": document_type,
                "error": error,
                "read_as_images": int(read_as_images),
            },
        )


def _add_missing_columns(conn: sqlite3.Connection) -> None:
    """A database from an earlier schema gains the columns added since, so an existing
    sdi.db keeps working; nothing is dropped or changed. Existing rows get the column's
    backfill value, so a column the code relies on is never left empty."""
    with closing(sqlite3.connect(":memory:")) as reference:
        reference.executescript(SCHEMA)
        tables = reference.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        for (table,) in tables.fetchall():
            existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            for _, column, column_type, *_ in reference.execute(f"PRAGMA table_info({table})"):
                if column not in existing:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")
                    if (backfill := _BACKFILLS.get((table, column))) is not None:
                        conn.execute(f"UPDATE {table} SET {column} = {backfill}")


def _insert_reason(
    conn: sqlite3.Connection, line_id: int, origin: str, reason: ReviewReason
) -> None:
    _insert(conn, "review_reasons", {"line_id": line_id, "origin": origin, **reason.model_dump()})


def _insert(conn: sqlite3.Connection, table: str, values: dict) -> int:
    """Insert one row whose keys are column names; lists and dicts are stored as JSON."""
    row = {k: json.dumps(v) if isinstance(v, list | dict) else v for k, v in values.items()}
    columns = ", ".join(row)
    params = ", ".join(f":{k}" for k in row)
    row_id = conn.execute(f"INSERT INTO {table} ({columns}) VALUES ({params})", row).lastrowid
    assert row_id is not None
    return row_id
