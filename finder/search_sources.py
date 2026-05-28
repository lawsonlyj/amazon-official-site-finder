from __future__ import annotations

import os
import sys
import time
import urllib.parse
from dataclasses import dataclass, asdict
from typing import Iterable

from .http import request_json
from .query_builder import build_queries
from .text import domain_from_url, slug, url_like_candidates


@dataclass
class SearchCandidate:
    url: str
    title: str = ""
    snippet: str = ""
    source: str = ""
    query: str = ""
    rank: int = 0
    evidence_url: str = ""

    def asdict(self) -> dict:
        return asdict(self)


def configured_sources() -> list[str]:
    sources = []
    if os.getenv("SERPAPI_API_KEY"):
        sources.append("serpapi")
    if os.getenv("BRAVE_API_KEY"):
        sources.append("brave")
    if os.getenv("TAVILY_API_KEY"):
        sources.append("tavily")
    if os.getenv("SERPER_API_KEY"):
        sources.append("serper")
    if os.getenv("FIRECRAWL_API_KEY"):
        sources.append("firecrawl")
    if os.getenv("EXA_API_KEY"):
        sources.append("exa")
    if os.getenv("DDGS_ENABLED", "").strip().lower() in {"1", "true", "yes"}:
        sources.append("ddgs")
    return sources


def smoke_test_configured_sources(query: str = '"Amazon" official website', *, per_query: int = 1) -> list[dict]:
    return [smoke_test_source(source, query=query, per_query=per_query) for source in configured_sources()]


def smoke_test_source(source: str, query: str = '"Amazon" official website', *, per_query: int = 1) -> dict:
    try:
        if source == "serpapi":
            candidates = _search_serpapi(query, per_query=per_query, use_cache=False)
        elif source == "brave":
            candidates = _search_brave(query, per_query=per_query, use_cache=False)
        elif source == "tavily":
            candidates = _search_tavily(query, per_query=per_query, use_cache=False)
        elif source == "serper":
            candidates = _search_serper(query, per_query=per_query, use_cache=False)
        elif source == "firecrawl":
            candidates = _search_firecrawl(query, per_query=per_query, use_cache=False)
        elif source == "exa":
            candidates = _search_exa(query, per_query=per_query, use_cache=False)
        elif source == "ddgs":
            candidates = _search_ddgs(query, per_query=per_query)
        else:
            raise ValueError(f"unknown source: {source}")
    except Exception as exc:
        return {
            "source": source,
            "ok": False,
            "candidate_count": 0,
            "error_type": type(exc).__name__,
            "error": _sanitize_error(str(exc)),
        }
    return {
        "source": source,
        "ok": bool(candidates),
        "candidate_count": len(candidates),
        "sample_url": candidates[0].url if candidates else "",
        "error_type": "",
        "error": "" if candidates else "No candidates returned.",
    }


def collect_candidates(provider: dict, *, per_query: int = 10, max_queries: int | None = None) -> list[SearchCandidate]:
    candidates: list[SearchCandidate] = []
    queries = build_queries(provider)
    if max_queries and max_queries > 0:
        queries = queries[:max_queries]
    attempts: dict[str, int] = {}
    failures: dict[str, int] = {}

    def run_search(source: str, query: str, fn) -> None:
        attempts[source] = attempts.get(source, 0) + 1
        try:
            candidates.extend(fn())
        except Exception as exc:
            failures[source] = failures.get(source, 0) + 1
            print(f"warning: {source} search failed for {query!r}: {type(exc).__name__}: {exc}", file=sys.stderr)

    for query in queries:
        if os.getenv("SERPAPI_API_KEY"):
            run_search("serpapi", query, lambda: _search_serpapi(query, per_query=per_query))
        if os.getenv("BRAVE_API_KEY"):
            run_search("brave", query, lambda: _search_brave(query, per_query=per_query))
        if os.getenv("TAVILY_API_KEY"):
            run_search("tavily", query, lambda: _search_tavily(query, per_query=per_query))
        if os.getenv("SERPER_API_KEY"):
            run_search("serper", query, lambda: _search_serper(query, per_query=per_query))
        if os.getenv("FIRECRAWL_API_KEY"):
            run_search("firecrawl", query, lambda: _search_firecrawl(query, per_query=per_query))
        if os.getenv("EXA_API_KEY"):
            run_search("exa", query, lambda: _search_exa(query, per_query=per_query))
        if os.getenv("DDGS_ENABLED", "").strip().lower() in {"1", "true", "yes"}:
            candidates.extend(_safe_search("ddgs", query, lambda: _search_ddgs(query, per_query=per_query)))
    _raise_if_production_search_degraded(provider, attempts, failures)
    candidates.extend(_domain_guesses(provider))
    return dedupe_candidates(_expand_urls_from_snippets(candidates))


def collect_candidates_for_queries(
    queries: list[str],
    *,
    per_query: int = 10,
    source_queries: dict[str, list[str]] | None = None,
    skip_sources: set[str] | None = None,
) -> list[SearchCandidate]:
    candidates: list[SearchCandidate] = []
    attempts: dict[str, int] = {}
    failures: dict[str, int] = {}
    skip_sources = skip_sources or set()

    def run_search(source: str, query: str, fn) -> None:
        attempts[source] = attempts.get(source, 0) + 1
        try:
            candidates.extend(fn())
        except Exception as exc:
            failures[source] = failures.get(source, 0) + 1
            print(f"warning: {source} search failed for {query!r}: {type(exc).__name__}: {exc}", file=sys.stderr)

    for query in queries:
        if os.getenv("SERPAPI_API_KEY") and "serpapi" not in skip_sources:
            run_search("serpapi", query, lambda query=query: _search_serpapi(query, per_query=per_query))
        if os.getenv("BRAVE_API_KEY") and "brave" not in skip_sources:
            run_search("brave", query, lambda query=query: _search_brave(query, per_query=per_query))
        if os.getenv("TAVILY_API_KEY") and "tavily" not in skip_sources:
            run_search("tavily", query, lambda query=query: _search_tavily(query, per_query=per_query))
        if os.getenv("SERPER_API_KEY") and "serper" not in skip_sources:
            run_search("serper", query, lambda query=query: _search_serper(query, per_query=per_query))
        if os.getenv("FIRECRAWL_API_KEY") and "firecrawl" not in skip_sources:
            run_search("firecrawl", query, lambda query=query: _search_firecrawl(query, per_query=per_query))
        if os.getenv("EXA_API_KEY") and "exa" not in skip_sources:
            run_search("exa", query, lambda query=query: _search_exa(query, per_query=per_query))
        if os.getenv("DDGS_ENABLED", "").strip().lower() in {"1", "true", "yes"} and "ddgs" not in skip_sources:
            candidates.extend(_safe_search("ddgs", query, lambda query=query: _search_ddgs(query, per_query=per_query)))
    for query in (source_queries or {}).get("exa", []):
        if os.getenv("EXA_API_KEY"):
            run_search("exa", query, lambda query=query: _search_exa(query, per_query=per_query))
    _raise_if_production_search_degraded({"provider_name": "unresolved second pass"}, attempts, failures)
    return dedupe_candidates(_expand_urls_from_snippets(candidates))


def dedupe_candidates(candidates: Iterable[SearchCandidate]) -> list[SearchCandidate]:
    seen: set[tuple[str, str]] = set()
    out: list[SearchCandidate] = []
    for c in candidates:
        if not c.url:
            continue
        domain = domain_from_url(c.url)
        if not domain:
            continue
        key = (domain, c.url.rstrip("/"))
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out


def _safe_search(source: str, query: str, fn) -> list[SearchCandidate]:
    attempts = max(1, int(os.getenv("FINDER_SEARCH_RETRIES", "2")) + 1)
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as exc:
            if attempt < attempts and _retryable_search_exception(exc):
                time.sleep(max(0.0, float(os.getenv("FINDER_SEARCH_RETRY_DELAY", "1"))) * attempt)
                continue
            print(f"warning: {source} search failed for {query!r}: {type(exc).__name__}: {exc}", file=sys.stderr)
            return []
    return []


def _retryable_search_exception(exc: Exception) -> bool:
    text = f"{type(exc).__name__}: {exc}".lower()
    return "timeout" in text or "timed out" in text or "connecttimeout" in text


def _raise_if_production_search_degraded(provider: dict, attempts: dict[str, int], failures: dict[str, int]) -> None:
    if os.getenv("FINDER_ALLOW_SEARCH_DEGRADATION", "").strip().lower() in {"1", "true", "yes"}:
        return
    production_sources = [
        source
        for source in ["serpapi", "brave", "tavily", "serper", "firecrawl", "exa"]
        if attempts.get(source, 0) > 0
    ]
    if not production_sources:
        return
    all_failed = all(failures.get(source, 0) >= attempts.get(source, 0) for source in production_sources)
    if all_failed:
        provider_name = provider.get("provider_name", "")
        detail = ", ".join(f"{source}:{failures.get(source, 0)}/{attempts.get(source, 0)}" for source in production_sources)
        raise RuntimeError(
            f"All production search API calls failed for provider {provider_name!r}; "
            f"stopping to avoid domain-guess-only degradation ({detail})."
        )


def _search_serpapi(query: str, *, per_query: int, use_cache: bool = True) -> list[SearchCandidate]:
    params = {
        "engine": "google",
        "q": query,
        "api_key": os.environ["SERPAPI_API_KEY"],
        "num": str(per_query),
        "hl": "en",
        "gl": "us",
    }
    url = "https://serpapi.com/search.json?" + urllib.parse.urlencode(params)
    data = request_json(url, cache_namespace="search", use_cache=use_cache)
    out = []
    for idx, item in enumerate(data.get("organic_results", [])[:per_query], 1):
        out.append(
            SearchCandidate(
                url=item.get("link", ""),
                title=item.get("title", ""),
                snippet=item.get("snippet", ""),
                source="serpapi",
                query=query,
                rank=idx,
            )
        )
    return out


def _search_brave(query: str, *, per_query: int, use_cache: bool = True) -> list[SearchCandidate]:
    params = {"q": query, "count": str(per_query), "country": "US", "search_lang": "en"}
    url = "https://api.search.brave.com/res/v1/web/search?" + urllib.parse.urlencode(params)
    data = request_json(
        url,
        headers={"X-Subscription-Token": os.environ["BRAVE_API_KEY"]},
        cache_namespace="search",
        use_cache=use_cache,
    )
    out = []
    for idx, item in enumerate((data.get("web") or {}).get("results", [])[:per_query], 1):
        out.append(
            SearchCandidate(
                url=item.get("url", ""),
                title=item.get("title", ""),
                snippet=item.get("description", ""),
                source="brave",
                query=query,
                rank=idx,
            )
        )
    return out


def _search_tavily(query: str, *, per_query: int, use_cache: bool = True) -> list[SearchCandidate]:
    data = request_json(
        "https://api.tavily.com/search",
        method="POST",
        headers={"Authorization": f"Bearer {os.environ['TAVILY_API_KEY']}"},
        payload={
            "query": query,
            "search_depth": "basic",
            "max_results": per_query,
            "include_answer": False,
            "include_raw_content": os.getenv("FINDER_TAVILY_RAW_CONTENT", "markdown"),
        },
        cache_namespace="search",
        use_cache=use_cache,
    )
    out = []
    for idx, item in enumerate(data.get("results", [])[:per_query], 1):
        out.append(
            SearchCandidate(
                url=item.get("url", ""),
                title=item.get("title", ""),
                snippet=(item.get("raw_content") or item.get("content") or "")[:1000],
                source="tavily",
                query=query,
                rank=idx,
            )
        )
    return out


def _search_serper(query: str, *, per_query: int, use_cache: bool = True) -> list[SearchCandidate]:
    data = request_json(
        "https://google.serper.dev/search",
        method="POST",
        headers={"X-API-KEY": os.environ["SERPER_API_KEY"]},
        payload={"q": query, "num": per_query},
        cache_namespace="search",
        use_cache=use_cache,
    )
    out = []
    for idx, item in enumerate(data.get("organic", [])[:per_query], 1):
        out.append(
            SearchCandidate(
                url=item.get("link", ""),
                title=item.get("title", ""),
                snippet=item.get("snippet", ""),
                source="serper",
                query=query,
                rank=idx,
            )
        )
    return out


def _search_firecrawl(query: str, *, per_query: int, use_cache: bool = True) -> list[SearchCandidate]:
    data = request_json(
        "https://api.firecrawl.dev/v2/search",
        method="POST",
        headers={"Authorization": f"Bearer {os.environ['FIRECRAWL_API_KEY']}"},
        payload={
            "query": query,
            "limit": per_query,
            "sources": ["web"],
            "scrapeOptions": {
                "formats": [{"type": "markdown"}],
                "onlyMainContent": True,
            },
        },
        cache_namespace="search",
        use_cache=use_cache,
    )
    web_results = (data.get("data") or {}).get("web", [])
    out = []
    for idx, item in enumerate(web_results[:per_query], 1):
        snippet = item.get("markdown") or item.get("description") or item.get("html") or ""
        out.append(
            SearchCandidate(
                url=item.get("url", ""),
                title=item.get("title", ""),
                snippet=snippet[:1000],
                source="firecrawl",
                query=query,
                rank=idx,
            )
        )
    return out


def _search_exa(query: str, *, per_query: int, use_cache: bool = True) -> list[SearchCandidate]:
    data = request_json(
        "https://api.exa.ai/search",
        method="POST",
        headers={"x-api-key": os.environ["EXA_API_KEY"]},
        payload={
            "query": query,
            "numResults": per_query,
            "contents": {
                "text": {"maxCharacters": 1000},
                "highlights": {"query": query, "maxCharacters": 800},
            },
        },
        cache_namespace="search",
        use_cache=use_cache,
    )
    out = []
    for idx, item in enumerate(data.get("results", [])[:per_query], 1):
        highlights = item.get("highlights") or []
        if isinstance(highlights, str):
            highlights = [highlights]
        snippet_parts = [
            str(item.get("summary") or ""),
            str(item.get("text") or ""),
            " ".join(str(part) for part in highlights),
        ]
        out.append(
            SearchCandidate(
                url=item.get("url", ""),
                title=item.get("title", ""),
                snippet=" ".join(part for part in snippet_parts if part).strip()[:1000],
                source="exa",
                query=query,
                rank=idx,
            )
        )
    return out


def _search_ddgs(query: str, *, per_query: int) -> list[SearchCandidate]:
    out = []
    for idx, item in enumerate(_ddgs_text(query, per_query), 1):
        out.append(
            SearchCandidate(
                url=item.get("href") or item.get("url") or "",
                title=item.get("title", ""),
                snippet=item.get("body", ""),
                source="ddgs",
                query=query,
                rank=idx,
            )
        )
    return out


def _ddgs_text(query: str, per_query: int) -> list[dict]:
    try:
        from ddgs import DDGS
    except ImportError as exc:
        raise RuntimeError("DDGS_ENABLED=1 requires `pip install -r requirements-optional.txt`.") from exc
    return list(DDGS().text(query, max_results=per_query))


def _domain_guesses(provider: dict) -> list[SearchCandidate]:
    name = provider.get("provider_name", "")
    base = slug(name)
    if not base:
        return []
    guesses = [f"https://www.{base}.com", f"https://{base}.com"]
    return [
        SearchCandidate(url=url, title="domain guess", source="domain_guess", query=name, rank=i)
        for i, url in enumerate(guesses, 1)
    ]


def _expand_urls_from_snippets(candidates: list[SearchCandidate]) -> list[SearchCandidate]:
    out = list(candidates)
    for c in candidates:
        for raw in url_like_candidates(" ".join([c.title, c.snippet])):
            if raw.startswith("http"):
                url = raw
            else:
                url = f"https://{raw}"
            out.append(
                SearchCandidate(
                    url=url,
                    title=f"URL mentioned by {c.source}",
                    snippet=c.snippet,
                    source=f"{c.source}_snippet_url",
                    query=c.query,
                    rank=c.rank,
                    evidence_url=c.url,
                )
            )
    return out


def _sanitize_error(message: str) -> str:
    sanitized = message
    for key in [
        "SERPAPI_API_KEY",
        "BRAVE_API_KEY",
        "TAVILY_API_KEY",
        "SERPER_API_KEY",
        "FIRECRAWL_API_KEY",
        "EXA_API_KEY",
    ]:
        value = os.getenv(key)
        if value:
            sanitized = sanitized.replace(value, "[redacted]")
    return sanitized[:500]
