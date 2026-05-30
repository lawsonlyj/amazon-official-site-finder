from __future__ import annotations

import os
from pathlib import Path

from .search_sources import configured_sources


PRODUCTION_SOURCES = ["serpapi", "brave", "tavily", "serper", "firecrawl", "exa"]
SEARCH_KEYS = [
    "SERPAPI_API_KEY",
    "BRAVE_API_KEY",
    "TAVILY_API_KEY",
    "SERPER_API_KEY",
    "FIRECRAWL_API_KEY",
    "EXA_API_KEY",
    "DDGS_ENABLED",
]


def doctor(input_path: str | None = None) -> dict:
    key_status = {key: bool(os.getenv(key)) for key in SEARCH_KEYS}
    key_status["DDGS_ENABLED"] = os.getenv("DDGS_ENABLED", "").strip().lower() in {"1", "true", "yes"}
    sources = configured_sources()
    return {
        "input_path": input_path or "",
        "input_exists": Path(input_path).exists() if input_path else None,
        "configured_sources": sources,
        "key_status": key_status,
        "production_ready": any(source in sources for source in PRODUCTION_SOURCES),
        "notes": _notes(sources, input_path),
    }


def _notes(sources: list[str], input_path: str | None) -> list[str]:
    notes = []
    if input_path and not Path(input_path).exists():
        notes.append("Input file does not exist.")
    if not sources:
        notes.append("No search sources configured; discovery will fall back to deterministic domain guesses only.")
    if not any(source in sources for source in PRODUCTION_SOURCES):
        notes.append("No production web search provider is configured; DDGS/domain guesses are exploratory fallbacks.")
    return notes
