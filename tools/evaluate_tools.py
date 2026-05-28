from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from finder.input_normalizer import normalize_provider_rows, read_normalized_csv
from finder.query_builder import build_queries
from finder.scoring import load_config
from finder.text import domain_from_url, normalize_text, slug


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate optional search/extraction tools on real GSPN providers.")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--raw-input", action="store_true")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--max-results", type=int, default=5)
    parser.add_argument("--config", default="config/scoring.json")
    args = parser.parse_args(argv)

    providers = normalize_provider_rows(args.input) if args.raw_input else read_normalized_csv(args.input)
    config = load_config(args.config)
    rows = []
    for provider in providers[: args.limit]:
        print(f"evaluating {provider['provider_name']}", file=sys.stderr)
        rows.extend(evaluate_provider(provider, args.max_results, config))
    write_rows(rows, args.output)
    print(f"wrote {len(rows)} rows to {args.output}")
    return 0


def evaluate_provider(provider: dict, max_results: int, config: dict) -> list[dict]:
    try:
        from ddgs import DDGS
    except ImportError as exc:
        raise RuntimeError("Install optional tools first: python3 -m pip install -r requirements-optional.txt") from exc
    try:
        import trafilatura
    except ImportError as exc:
        raise RuntimeError("Install optional tools first: python3 -m pip install -r requirements-optional.txt") from exc

    out = []
    seen = set()
    for query in build_queries(provider)[:6]:
        try:
            results = list(DDGS().text(query, max_results=max_results))
        except Exception as exc:
            out.append(_error_row(provider, query, "ddgs", exc))
            continue
        for rank, item in enumerate(results, 1):
            url = item.get("href") or item.get("url") or ""
            if not url:
                continue
            key = (query, url)
            if key in seen:
                continue
            seen.add(key)
            domain = domain_from_url(url)
            excluded = any(part in domain for part in config.get("excluded_domains", []))
            extracted = ""
            extract_error = ""
            if not excluded:
                try:
                    downloaded = trafilatura.fetch_url(url)
                    extracted = trafilatura.extract(downloaded or "", include_links=False, include_tables=False) or ""
                except Exception as exc:
                    extract_error = f"{type(exc).__name__}: {exc}"
            out.append(
                {
                    "provider_id": provider.get("provider_id", ""),
                    "provider_name": provider.get("provider_name", ""),
                    "query": query,
                    "rank": rank,
                    "url": url,
                    "domain": domain,
                    "excluded": excluded,
                    "title": item.get("title", ""),
                    "snippet": item.get("body", ""),
                    "extract_chars": len(extracted),
                    "provider_name_in_text": normalize_text(provider.get("provider_name", "")) in normalize_text(extracted),
                    "service_terms_in_text": _service_terms_in_text(extracted),
                    "domain_slug_match": slug(provider.get("provider_name", "")) in slug(_domain_label(domain)),
                    "extract_error": extract_error,
                }
            )
    return out


def _service_terms_in_text(text: str) -> bool:
    normalized = normalize_text(text)
    terms = ["amazon", "seller central", "marketplace", "ppc", "fba", "catalog", "compliance", "ecommerce"]
    return sum(1 for term in terms if normalize_text(term) in normalized) >= 2


def _domain_label(domain: str) -> str:
    parts = domain.split(".")
    if len(parts) >= 2:
        return parts[-2]
    return domain


def _error_row(provider: dict, query: str, tool: str, exc: Exception) -> dict:
    return {
        "provider_id": provider.get("provider_id", ""),
        "provider_name": provider.get("provider_name", ""),
        "query": query,
        "rank": "",
        "url": "",
        "domain": "",
        "excluded": "",
        "title": "",
        "snippet": "",
        "extract_chars": 0,
        "provider_name_in_text": "",
        "service_terms_in_text": "",
        "domain_slug_match": "",
        "extract_error": f"{tool}: {type(exc).__name__}: {exc}",
    }


def write_rows(rows: list[dict], output_csv: str | Path) -> None:
    output = Path(output_csv)
    output.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "provider_id",
        "provider_name",
        "query",
        "rank",
        "url",
        "domain",
        "excluded",
        "title",
        "snippet",
        "extract_chars",
        "provider_name_in_text",
        "service_terms_in_text",
        "domain_slug_match",
        "extract_error",
    ]
    with output.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    raise SystemExit(main())
