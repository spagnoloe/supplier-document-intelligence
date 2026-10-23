"""`sdi ingest <folder>`: process every Source in a folder and print a summary.
`sdi export <file.csv>`: write every Quotation line, with corrections applied, as CSV.
`sdi eval <folder>...`: ingest into a fresh database and score it against the gold set."""

import argparse
import hashlib
import json
import tempfile
import threading
from pathlib import Path
from typing import TYPE_CHECKING

from sdi.read_model import DocumentStatus, ReadModel

if TYPE_CHECKING:
    from sdi.config import Settings


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="sdi")
    commands = parser.add_subparsers(dest="command", required=True)
    ingest_cmd = commands.add_parser("ingest", help="Ingest every Source in a folder.")
    ingest_cmd.add_argument("folder", type=Path)
    ingest_cmd.add_argument(
        "--force",
        action="store_true",
        help="Read every Source again, even if neither it nor the models or prompt changed "
        "(e.g. after a change to validation). Their review starts afresh.",
    )
    export_cmd = commands.add_parser(
        "export", help="Write every Quotation line, with corrections applied, as CSV."
    )
    export_cmd.add_argument("file", type=Path)
    eval_cmd = commands.add_parser(
        "eval", help="Ingest folders into a fresh database and score them against a gold set."
    )
    eval_cmd.add_argument("folders", type=Path, nargs="+")
    eval_cmd.add_argument("--gold", type=Path, default=Path("eval/gold"))
    eval_cmd.add_argument(
        "--out", type=Path, default=Path("eval/results"), help="Writes <out>.md and <out>.json."
    )
    eval_cmd.add_argument(
        "--cached-only",
        action="store_true",
        help="Answer every model request from the LLM cache; a request it can't answer fails "
        "like an unavailable model, so the run is reproducible and free.",
    )
    args = parser.parse_args(argv)

    if args.command == "ingest":
        run_ingest(args.folder, force=args.force)
    elif args.command == "export":
        run_export(args.file)
    elif args.command == "eval":
        run_eval(args.folders, gold=args.gold, out=args.out, cached_only=args.cached_only)


def run_ingest(folder: Path, *, force: bool = False) -> None:
    from sdi.config import load_settings

    settings = load_settings()
    _use_llm_cache(settings, cached_only=False)
    sources = _sources(folder)
    _ingest(sources, settings, settings.db_path, force=force)
    print(_summary(settings.db_path, sources))


def run_eval(folders: list[Path], *, gold: Path, out: Path, cached_only: bool) -> None:
    """A fresh database every run: ingest skips unchanged Sources, so scoring an existing
    database after a change to validation would report the numbers from before it."""
    from sdi.config import load_settings
    from sdi.evaluation import format_report, load_gold, score

    settings = load_settings()
    cache = _use_llm_cache(settings, cached_only=cached_only)
    sources = [source for folder in folders for source in _sources(folder)]
    gold_set = load_gold(gold)
    labelled = {g.source for g in gold_set}
    if unlabelled := sorted(p.name for p in sources if p.name not in labelled):
        raise SystemExit(f"No gold labels for: {', '.join(unlabelled)}")
    if missing := sorted(labelled - {p.name for p in sources}):
        raise SystemExit(f"Gold labels for Sources not found: {', '.join(missing)}")

    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "eval.db"
        _ingest(sources, settings, db_path)
        print(_summary(db_path, sources) + "\n")
        report = score(gold_set, ReadModel(db_path))

    out.parent.mkdir(parents=True, exist_ok=True)
    markdown = format_report(report)
    out.with_suffix(".md").write_text(markdown)
    out.with_suffix(".json").write_text(report.model_dump_json(indent=2) + "\n")
    print(markdown)
    print(f"Wrote {out.with_suffix('.md')} and {out.with_suffix('.json')}")
    outcome = "failed, as --cached-only" if cached_only else "sent to the model"
    print(f"Model requests not answered from the LLM cache: {cache.misses} ({outcome})")


def _sources(folder: Path) -> list[Path]:
    if not folder.is_dir():
        raise SystemExit(f"{folder} is not a folder")
    return sorted(p for p in folder.iterdir() if p.is_file() and not p.name.startswith("."))


def _ingest(
    sources: list[Path], settings: "Settings", db_path: Path, *, force: bool = False
) -> None:
    # Imported here so `sdi --help` stays fast and works without an API key.
    from sdi.extraction.gemini import GeminiExtractor
    from sdi.fx import frankfurter
    from sdi.pipeline import ingest
    from sdi.store import Store

    ingest(
        sources,
        GeminiExtractor(settings.extraction_model),
        fx_source=frankfurter,
        store=Store(db_path),
        second_reader=GeminiExtractor(settings.second_read_model, seed_base=1000),
        max_concurrency=settings.max_concurrency,
        extraction_version=extraction_version(settings),
        force=force,
    )


def _summary(db_path: Path, sources: list[Path]) -> str:
    ingested = {str(p) for p in sources}
    statuses = ReadModel(db_path).document_statuses()
    return format_summary([s for s in statuses if s.source in ingested])


class LlmCacheMiss(Exception):
    """A model request the LLM cache can't answer, in a run that may not call the model."""


def _use_llm_cache(settings: "Settings", *, cached_only: bool):
    """Install LangChain's SQLite LLM cache, counting the requests it can't answer (each one
    a call to the model, or with `cached_only` an LlmCacheMiss instead)."""
    from langchain_community.cache import SQLiteCache
    from langchain_core.globals import set_llm_cache

    class CountingCache(SQLiteCache):
        misses = 0
        _lock = threading.Lock()

        def lookup(self, prompt, llm_string):
            hit = super().lookup(prompt, llm_string)
            if hit is None:
                with self._lock:
                    self.misses += 1
                if cached_only:
                    raise LlmCacheMiss("no cached response, and the run is --cached-only")
            return hit

    settings.llm_cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache = CountingCache(database_path=str(settings.llm_cache_path))
    set_llm_cache(cache)
    return cache


def extraction_version(settings: "Settings") -> str:
    """What a Source's extraction depends on besides its content: the models, the prompt and
    the schema the model fills."""
    from sdi.extraction.prompt import EXTRACTION_PROMPT
    from sdi.extraction.schema import Quotation

    parts = [
        settings.extraction_model,
        settings.second_read_model,
        EXTRACTION_PROMPT,
        json.dumps(Quotation.model_json_schema(), sort_keys=True),
    ]
    return hashlib.sha256("\0".join(parts).encode()).hexdigest()


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
