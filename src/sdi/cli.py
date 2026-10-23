"""`sdi ingest <folder>`: process every Source in a folder and print a summary.
`sdi export <file.csv>`: write every Quotation line, with corrections applied, as CSV."""

import argparse
from pathlib import Path

from sdi.read_model import DocumentStatus, ReadModel


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="sdi")
    commands = parser.add_subparsers(dest="command", required=True)
    ingest_cmd = commands.add_parser("ingest", help="Ingest every Source in a folder.")
    ingest_cmd.add_argument("folder", type=Path)
    export_cmd = commands.add_parser(
        "export", help="Write every Quotation line, with corrections applied, as CSV."
    )
    export_cmd.add_argument("file", type=Path)
    args = parser.parse_args(argv)

    if args.command == "ingest":
        run_ingest(args.folder)
    elif args.command == "export":
        run_export(args.file)


def run_ingest(folder: Path) -> None:
    # Imported here so `sdi --help` stays fast and works without an API key.
    from langchain_community.cache import SQLiteCache
    from langchain_core.globals import set_llm_cache

    from sdi.config import load_settings
    from sdi.extraction.gemini import GeminiExtractor
    from sdi.fx import frankfurter
    from sdi.pipeline import ingest
    from sdi.store import Store

    if not folder.is_dir():
        raise SystemExit(f"{folder} is not a folder")
    settings = load_settings()
    settings.llm_cache_path.parent.mkdir(parents=True, exist_ok=True)
    set_llm_cache(SQLiteCache(database_path=str(settings.llm_cache_path)))

    sources = sorted(p for p in folder.iterdir() if p.is_file() and not p.name.startswith("."))
    ingest(
        sources,
        GeminiExtractor(settings.extraction_model),
        fx_source=frankfurter,
        store=Store(settings.db_path),
        second_reader=GeminiExtractor(settings.second_read_model),
        max_concurrency=settings.max_concurrency,
    )
    ingested = {str(p) for p in sources}
    statuses = ReadModel(settings.db_path).document_statuses()
    print(format_summary([s for s in statuses if s.source in ingested]))


def run_export(file: Path) -> None:
    from sdi.config import database_path
    from sdi.export import export_csv
    from sdi.store import Store

    db_path = database_path()
    if not db_path.is_file():
        raise SystemExit(f"No database at {db_path}; run `sdi ingest <folder>` first")
    Store(db_path)  # brings a database from an earlier schema up to date
    with file.open("w", newline="") as out:
        export_csv(ReadModel(db_path), out)
    print(f"Wrote {file}")


def format_summary(statuses: list[DocumentStatus]) -> str:
    # REVIEW counts lines with review status needs_review; the rest are auto_ok.
    rows = [("STATUS", "LINES", "REVIEW", "SOURCE", "DETAIL")]
    for s in statuses:
        detail = (s.error if s.status == "failed" else s.document_type) or ""
        rows.append(
            (s.status, str(s.line_count), str(s.needs_review_count), Path(s.source).name, detail)
        )
    # Every column but the free-text DETAIL is padded to its widest cell.
    widths = [max(len(row[i]) for row in rows) for i in range(len(rows[0]) - 1)]
    return "\n".join(
        "  ".join(cell.ljust(w) for cell, w in zip(row[:-1], widths, strict=True)) + "  " + row[-1]
        for row in rows
    )
