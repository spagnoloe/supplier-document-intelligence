"""`sdi ingest <folder>`: process every Source in a folder and print a summary."""

import argparse
from pathlib import Path

from sdi.read_model import DocumentStatus, ReadModel


def main() -> None:
    parser = argparse.ArgumentParser(prog="sdi")
    commands = parser.add_subparsers(dest="command", required=True)
    ingest_cmd = commands.add_parser("ingest", help="Ingest every Source in a folder.")
    ingest_cmd.add_argument("folder", type=Path)
    args = parser.parse_args()

    if args.command == "ingest":
        run_ingest(args.folder)


def run_ingest(folder: Path) -> None:
    # Imported here so `sdi --help` stays fast and works without an API key.
    from langchain_community.cache import SQLiteCache
    from langchain_core.globals import set_llm_cache

    from sdi.config import load_settings
    from sdi.extraction.gemini import GeminiExtractor
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
        fx_source=lambda currency, on: None,  # FX conversion arrives with derived prices.
        store=Store(settings.db_path),
        max_concurrency=settings.max_concurrency,
    )
    ingested = {str(p) for p in sources}
    statuses = ReadModel(settings.db_path).document_statuses()
    print(format_summary([s for s in statuses if s.source in ingested]))


def format_summary(statuses: list[DocumentStatus]) -> str:
    rows = [("STATUS", "LINES", "SOURCE", "DETAIL")]
    for s in statuses:
        detail = (s.error if s.status == "failed" else s.document_type) or ""
        rows.append((s.status, str(s.line_count), Path(s.source).name, detail))
    widths = [max(len(row[i]) for row in rows) for i in range(3)]
    return "\n".join(
        "  ".join(cell.ljust(w) for cell, w in zip(row[:3], widths, strict=True)) + "  " + row[3]
        for row in rows
    )
