from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from finder.geo import local_search_terms
from finder.text import domain_from_url, normalize_text
from tools.output_layout import first_existing


OUTPUT_FIELDS = [
    "provider_id",
    "provider_name",
    "status",
    "confidence",
    "provider_detail_url",
    "location",
    "services",
    "top_candidate_url",
    "top_candidate_domain",
    "top_candidate_score",
    "top_candidate_source",
    "strategy_tier",
    "recommended_tool",
    "accept_if",
    "query_1",
    "query_2",
    "query_3",
    "query_4",
    "query_5",
    "query_6",
    "query_7",
    "query_8",
    "notes",
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a second-pass search plan for unresolved providers.")
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)

    rows = build_second_pass_plan(args.run_dir)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    print(json.dumps(summarize_plan(rows) | {"output_csv": str(output)}, ensure_ascii=False, indent=2))
    return 0


def build_second_pass_plan(run_dir: str | Path) -> list[dict[str, str]]:
    run_dir = Path(run_dir)
    providers_path = first_existing(run_dir, "details/input/providers.csv", "providers_normalized.csv")
    review_sheet_path = first_existing(run_dir, "details/first_pass/review_sheet.csv", "provider_review_sheet_enhanced.csv")
    if not providers_path:
        raise FileNotFoundError(f"normalized providers CSV not found in {run_dir}")
    if not review_sheet_path:
        raise FileNotFoundError(f"review sheet CSV not found in {run_dir}")
    providers = _provider_index(providers_path)
    review_rows = _read_rows(review_sheet_path)
    rows = []
    for row in review_rows:
        provider = providers.get(row.get("provider_id", ""), {})
        rows.append(_plan_row(row, provider))
    return rows


def summarize_plan(rows: list[dict[str, str]]) -> dict:
    out: dict[str, object] = {"rows": len(rows), "tiers": {}, "statuses": {}}
    for row in rows:
        out["tiers"][row["strategy_tier"]] = out["tiers"].get(row["strategy_tier"], 0) + 1
        out["statuses"][row["status"]] = out["statuses"].get(row["status"], 0) + 1
    return out


def _plan_row(row: dict[str, str], provider: dict[str, str]) -> dict[str, str]:
    name = row.get("provider_name", "")
    services = _loads(provider.get("service_apis") or "[]")
    locations = _loads(provider.get("provider_locations") or "[]")
    service = services[0] if services else ""
    location = locations[0] if locations else ""
    top_url = row.get("candidate_1_url", "")
    top_domain = domain_from_url(row.get("candidate_1_domain", "") or top_url) if top_url or row.get("candidate_1_domain") else ""
    tier, tool, accept_if, notes = _strategy(row, top_domain)
    queries = _queries(name=name, location=location, service=service, top_domain=top_domain, tier=tier)
    padded = queries[:8] + [""] * max(0, 8 - len(queries))

    return {
        "provider_id": row.get("provider_id", ""),
        "provider_name": name,
        "status": row.get("status", ""),
        "confidence": row.get("confidence", ""),
        "provider_detail_url": row.get("provider_detail_url", ""),
        "location": location,
        "services": "; ".join(services),
        "top_candidate_url": top_url,
        "top_candidate_domain": top_domain,
        "top_candidate_score": row.get("candidate_1_score", ""),
        "top_candidate_source": row.get("candidate_1_source", ""),
        "strategy_tier": tier,
        "recommended_tool": tool,
        "accept_if": accept_if,
        "query_1": padded[0],
        "query_2": padded[1],
        "query_3": padded[2],
        "query_4": padded[3],
        "query_5": padded[4],
        "query_6": padded[5],
        "query_7": padded[6],
        "query_8": padded[7],
        "notes": notes,
    }


def _strategy(row: dict[str, str], top_domain: str) -> tuple[str, str, str, str]:
    status = row.get("status", "")
    source = row.get("candidate_1_source", "")
    score = _to_int(row.get("candidate_1_score"))
    if status == "needs_review":
        return (
            "A_verify_top_candidate",
            "Firecrawl/Tavily raw content; Playwright only for JS-only pages",
            "score >=70 after page/search evidence; no excluded/social/directory domain",
            "candidate already exists; verify it and still run targeted search for a stronger replacement",
        )
    if status == "not_found":
        return (
            "D_registry_social_then_manual",
            "SerpApi/Brave broad search plus OpenCorporates/Wikidata/RDAP as supporting evidence",
            "new independent domain scores >=70 after page/search evidence",
            "all saved candidates were excluded or unusable",
        )
    if source == "domain_guess" or score >= 35:
        return (
            "B_verify_or_expand_domain_guess",
            "Brave+SerpApi second opinion, then Firecrawl/Tavily scrape candidate",
            "guessed or replacement domain scores >=70 after page/search evidence",
            "exact domain guess exists but current evidence is too thin for auto-match",
        )
    return (
        "C_broaden_search",
        "Exa/Tavily advanced search, SerpApi Google SERP, country/language-specific queries",
        "new candidate beats weak current candidate and scores >=70",
        "current top candidate is weak or probably unrelated",
    )


def _queries(*, name: str, location: str, service: str, top_domain: str, tier: str) -> list[str]:
    clean = _clean_name(name)
    service = service or "Amazon service provider"
    queries = [
        f'"{name}" official website',
        f'"{name}" website -linkedin -facebook -instagram -amazon',
        f'"{name}" "contact"',
        f'"{name}" "{service}"',
        f'"{name}" "{location}" website' if location else "",
    ]
    if clean and clean.casefold() != name.casefold():
        queries.append(f'"{clean}" official website')
    if top_domain:
        queries.extend([f'site:{top_domain} "{name}"', f'"{name}" "{top_domain}"'])
    queries.extend(_country_queries(name, location))
    for term in local_search_terms([location])[:5]:
        queries.append(f'"{name}" "{term}"')
    if tier == "D_registry_social_then_manual":
        queries.extend([f'"{name}" LinkedIn website', f'"{name}" Crunchbase website'])
    return _dedupe([query for query in queries if query])


def _country_queries(name: str, location: str) -> list[str]:
    loc = location.casefold()
    if "india" in loc:
        return [f'"{name}" MCA', f'"{name}" GST website']
    if "united states" in loc:
        return [f'"{name}" LLC official site', f'"{name}" "contact us"']
    if "germany" in loc:
        return [f'"{name}" GmbH website', f'"{name}" "Impressum"']
    if "united kingdom" in loc:
        return [f'"{name}" Companies House website', f'"{name}" "contact"']
    if "china" in loc:
        return [f'"{name}" 官网', f'"{name}" official site']
    if "italy" in loc:
        return [f'"{name}" "sito ufficiale"', f'"{name}" "partita iva"']
    if "spain" in loc:
        return [f'"{name}" "sitio web oficial"', f'"{name}" CIF']
    if "france" in loc:
        return [f'"{name}" "site officiel"', f'"{name}" SIREN']
    return []


def _clean_name(name: str) -> str:
    normalized = normalize_text(name)
    return re.sub(r"\s+", " ", normalized).strip()


def _provider_index(path: Path) -> dict[str, dict[str, str]]:
    return {row.get("provider_id", ""): row for row in _read_rows(path)}


def _read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _loads(value: str) -> list[str]:
    try:
        data = json.loads(value or "[]")
    except json.JSONDecodeError:
        return []
    return [str(item) for item in data if str(item)]


def _to_int(value) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def _dedupe(values: list[str]) -> list[str]:
    out = []
    for value in values:
        if value and value not in out:
            out.append(value)
    return out


if __name__ == "__main__":
    raise SystemExit(main())
