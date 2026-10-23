"""SQLite store: the source of truth for ingested Sources, Quotations and Quotation lines.

Writes are per Source and replace whatever an earlier run stored for it, so re-running a
folder never duplicates rows.
"""

import json
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path

from sdi.extraction.schema import Quotation

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
    evidence TEXT NOT NULL
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

    def save_processed(self, source_path: str, file_type: str, quotation: Quotation) -> None:
        with connect(self.db_path) as conn:
            doc_id = self._replace_document(
                conn, source_path, file_type, "processed", quotation.document_type
            )
            q = quotation.model_dump(mode="json")
            quotation_id = conn.execute(
                """INSERT INTO quotations (document_id, supplier, quote_ref, rfq_ref, issue_date,
                   valid_until, currency, incoterm, incoterm_place, evidence)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    doc_id,
                    q["supplier"],
                    q["quote_ref"],
                    q["rfq_ref"],
                    q["issue_date"],
                    q["valid_until"],
                    q["currency"],
                    q["incoterm"],
                    q["incoterm_place"],
                    json.dumps(q["evidence"]),
                ),
            ).lastrowid
            conn.executemany(
                """INSERT INTO quotation_lines (quotation_id, line_no, brand_name,
                   active_ingredients, dosage_form, uom, pack_size, unit_price, pack_price,
                   currency, discount_pct, quantity, line_total, price_tiers, revision_note,
                   evidence)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        quotation_id,
                        line_no,
                        ln["brand_name"],
                        json.dumps(ln["active_ingredients"]),
                        ln["dosage_form"],
                        ln["uom"],
                        ln["pack_size"],
                        ln["unit_price"],
                        ln["pack_price"],
                        ln["currency"],
                        ln["discount_pct"],
                        ln["quantity"],
                        ln["line_total"],
                        json.dumps(ln["price_tiers"]),
                        ln["revision_note"],
                        json.dumps(ln["evidence"]),
                    )
                    for line_no, ln in enumerate(q["lines"], start=1)
                ],
            )

    def save_skipped(self, source_path: str, file_type: str, document_type: str) -> None:
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
        status: str,
        document_type: str | None,
        error: str | None = None,
    ) -> int:
        conn.execute("DELETE FROM documents WHERE source_path = ?", (source_path,))
        doc_id = conn.execute(
            """INSERT INTO documents (source_path, file_type, status, document_type, error)
               VALUES (?, ?, ?, ?, ?)""",
            (source_path, file_type, status, document_type, error),
        ).lastrowid
        assert doc_id is not None
        return doc_id
