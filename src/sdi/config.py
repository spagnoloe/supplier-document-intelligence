"""Runtime settings, read from the environment and `.env` (see `.env.example`)."""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    extraction_model: str
    second_read_model: str
    db_path: Path
    llm_cache_path: Path
    max_concurrency: int


def load_settings() -> Settings:
    load_dotenv()
    if not os.environ.get("GOOGLE_API_KEY"):
        raise SystemExit("GOOGLE_API_KEY is not set. Copy .env.example to .env and add your key.")
    return Settings(
        extraction_model=os.environ.get("SDI_EXTRACTION_MODEL", "gemini-3.5-flash-lite"),
        second_read_model=os.environ.get("SDI_SECOND_READ_MODEL", "gemini-3.5-flash"),
        db_path=Path(os.environ.get("SDI_DB_PATH", "sdi.db")),
        llm_cache_path=Path(os.environ.get("SDI_LLM_CACHE_PATH", ".cache/llm.db")),
        max_concurrency=int(os.environ.get("SDI_MAX_CONCURRENCY", "2")),
    )
