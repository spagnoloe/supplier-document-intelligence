"""SQLite store: the source of truth for ingested Sources, Quotations and Quotation lines.

Writes are per Source and replace whatever an earlier run stored for it, so re-running a
folder never duplicates rows.
"""

import json
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Literal

from sdi.extraction.schema import Quotation
from sdi.validation import LineVerification

ProcessingStatus = Literal["processed", "failed", "skipped_unsupported_type"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY,
    source_path TEXT NOT NULL UNIQUE,
    file_type TEXT,
    status TEXT NOT NULL CHECK (status IN ('processed', 'failed', 'skipped_unsupported_type')),
    document_type TEXT,
    error TEXT,
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
    review_status TEXT NOT NULL CHECK (review_status IN ('needs_review', 'auto_ok')),
    field_verification TEXT NOT NULL,
    headline_unit_price REAL,
    headline_pack_price REAL,
    net_unit_price REAL,
    net_pack_price REAL
);
-- No CHECK on code: new Review reason codes need no schema change.
CREATE TABLE IF NOT EXISTS review_reasons (
    id INTEGER PRIMARY KEY,
    line_id INTEGER NOT NULL REFERENCES quotation_lines(id) ON DELETE CASCADE,
    code TEXT NOT NULL,
    field TEXT,
    message TEXT NOT NULL
);
"""


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

    def save_processed(
        self,
        source_path: str,
        file_type: str | None,
        quotation: Quotation,
        verifications: list[LineVerification],
    ) -> None:
        with connect(self.db_path) as conn:
            doc_id = self._replace_document(
                conn, source_path, file_type, "processed", quotation.document_type
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
                        "review_status": verification.review_status,
                        "field_verification": verification.fields,
                        **verification.prices.model_dump(),
                    },
                )
                for reason in verification.reasons:
                    _insert(conn, "review_reasons", {"line_id": line_id, **reason.model_dump()})

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
            },
        )


def _insert(conn: sqlite3.Connection, table: str, values: dict) -> int:
    """Insert one row whose keys are column names; lists and dicts are stored as JSON."""
    row = {k: json.dumps(v) if isinstance(v, list | dict) else v for k, v in values.items()}
    columns = ", ".join(row)
    params = ", ".join(f":{k}" for k in row)
    row_id = conn.execute(f"INSERT INTO {table} ({columns}) VALUES ({params})", row).lastrowid
    assert row_id is not None
    return row_id
