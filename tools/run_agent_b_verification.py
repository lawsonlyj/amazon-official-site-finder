from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from finder.cli import load_dotenv
from finder.html_extract import extract_html
from finder.http import fetch_text
from finder.scoring import is_excluded_domain, load_config, score_candidate
from finder.search_sources import SearchCandidate, collect_candidates_for_queries
from finder.text import domain_from_url, normalize_text, tokens
from tools.build_linked_workbook import build_workbook


AGENT_B_FIELDS = [
    "provider_id",
    "provider_name",
    "provider_detail_url",
    "candidate_url",
    "candidate_domain",
    "agent_b_decision",
    "manual_decision",
    "manual_url",
    "confidence",
    "evidence_score",
    "evidence_urls",
    "supporting_facts",
    "counter_evidence",
    "reason_for_unsure",
    "notes",
    "independent_search_queries",
    "replacement_url",
    "replacement_domain",
    "source_status",
    "source_confidence",
]

SUPPORTING_PATHS = ["/", "/about", "/contact", "/services", "/privacy", "/terms", "/about-us", "/contact-us"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run AgentB candidate-first official-site verification.")
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--config", default="config/scoring.json")
    parser.add_argument("--output-csv")
    parser.add_argument("--output-jsonl")
    parser.add_argument("--output-xlsx")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--per-query", type=int, default=2)
    parser.add_argument("--write-xlsx", action="store_true")
    parser.add_argument("--include-all-final", action="store_true")
    args = parser.parse_args(argv)

    load_dotenv(Path(".env"))
    summary = run_agent_b_verification(
        run_dir=args.run_dir,
        config_path=args.config,
        output_csv=args.output_csv,
        output_jsonl=args.output_jsonl,
        output_xlsx=args.output_xlsx,
        limit=args.limit or None,
        per_query=args.per_query,
        write_xlsx=args.write_xlsx,
        include_all_final=args.include_all_final,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def run_agent_b_verification(
    *,
    run_dir: str | Path,
    config_path: str | Path = "config/scoring.json",
    output_csv: str | Path | None = None,
    output_jsonl: str | Path | None = None,
    output_xlsx: str | Path | None = None,
    limit: int | None = None,
    per_query: int = 2,
    write_xlsx: bool = True,
    include_all_final: bool = False,
) -> dict:
    run_dir = Path(run_dir)
    config = load_config(config_path)
    rows = _verification_input_rows(run_dir, include_all_final=include_all_final)
    if limit:
        rows = rows[:limit]

    output_csv_path = Path(output_csv) if output_csv else run_dir / "agent_b_verification_results.csv"
    output_jsonl_path = Path(output_jsonl) if output_jsonl else run_dir / "agent_b_verification_results.jsonl"
    output_xlsx_path = Path(output_xlsx) if output_xlsx else run_dir / "agent_b_verification_results.xlsx"

    result_rows = []
    json_rows = []
    for index, row in enumerate(rows, 1):
        print(f"agent-b {index}/{len(rows)} {row.get('provider_name', '')}", file=sys.stderr)
        result = verify_row(row, config=config, per_query=per_query)
        result_rows.append(result["row"])
        json_rows.append(result["details"])

    _write_rows(output_csv_path, result_rows, AGENT_B_FIELDS)
    with output_jsonl_path.open("w", encoding="utf-8") as f:
        for item in json_rows:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")

    xlsx_summary = {}
    if write_xlsx:
        xlsx_summary = build_workbook([("AgentB_Verification", output_csv_path)], output_xlsx_path)

    summary = {
        "input_rows": len(rows),
        "output_rows": len(result_rows),
        "decision_counts": _counts(result_rows, "agent_b_decision"),
        "outputs": {
            "csv": str(output_csv_path),
            "jsonl": str(output_jsonl_path),
            "xlsx": str(output_xlsx_path) if write_xlsx else "",
        },
        "xlsx": xlsx_summary,
    }
    (run_dir / "agent_b_verification_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    _update_manifest(run_dir / "manifest.json", summary)
    return summary


def verify_row(row: dict[str, str], *, config: dict, per_query: int = 2) -> dict:
    provider_name = row.get("provider_name", "")
    candidate_url = _candidate_url(row)
    provider_locations = _parse_locations(row.get("provider_locations", ""))
    candidate = _verify_url(candidate_url, row, config) if candidate_url else _empty_verification()
    search_queries = _independent_queries(provider_name, provider_locations)
    search_candidates = _safe_collect(search_queries, per_query=per_query)
    replacement = _best_replacement(row, search_candidates, candidate_url, config)

    decision, manual_url, confidence, unsure_reason = _decide(candidate, replacement)
    notes = _notes_for(decision, candidate, replacement)
    out_row = {
        "provider_id": row.get("provider_id", ""),
        "provider_name": provider_name,
        "provider_detail_url": row.get("provider_detail_url", ""),
        "candidate_url": candidate_url,
        "candidate_domain": domain_from_url(candidate_url),
        "agent_b_decision": decision,
        "manual_decision": decision,
        "manual_url": manual_url,
        "confidence": str(confidence),
        "evidence_score": str(candidate["score"]),
        "evidence_urls": "; ".join(candidate["evidence_urls"]),
        "supporting_facts": "; ".join(candidate["supporting_facts"]),
        "counter_evidence": "; ".join(candidate["counter_evidence"]),
        "reason_for_unsure": unsure_reason,
        "notes": notes,
        "independent_search_queries": "; ".join(search_queries),
        "replacement_url": replacement.get("url", ""),
        "replacement_domain": replacement.get("domain", ""),
        "source_status": row.get("status", ""),
        "source_confidence": row.get("confidence", ""),
    }
    return {
        "row": out_row,
        "details": {
            "provider_id": out_row["provider_id"],
            "provider_name": provider_name,
            "candidate": candidate,
            "replacement": replacement,
            "search_queries": search_queries,
            "decision": decision,
        },
    }


def _verification_input_rows(run_dir: Path, *, include_all_final: bool) -> list[dict[str, str]]:
    final_rows = _read_rows(_first_existing([run_dir / "provider_final_official_websites_second_pass.csv", run_dir / "provider_final_official_websites.csv"]))
    final_by_key = {_row_key(row): row for row in final_rows if _row_key(row)}
    manual_task = run_dir / "manual_official_site_review_task.csv"
    if manual_task.exists() and not include_all_final:
        task_rows = _read_rows(manual_task)
        out = []
        for task_row in task_rows:
            merged = dict(final_by_key.get(_row_key(task_row), {}))
            merged.update({key: value for key, value in task_row.items() if value})
            out.append(merged)
        return out
    second_pass = _index_rows(run_dir / "unresolved_second_pass_results.csv")
    out = []
    for row in final_rows:
        merged = dict(row)
        second = second_pass.get(_row_key(row), {})
        if second.get("previous_top_candidate_url") and not merged.get("top_candidate_url"):
            merged["top_candidate_url"] = second["previous_top_candidate_url"]
        out.append(merged)
    return out


def _verify_url(url: str, provider: dict[str, str], config: dict) -> dict:
    if not url:
        return _empty_verification()
    if is_excluded_domain(url, config):
        return {
            **_empty_verification(),
            "url": url,
            "domain": domain_from_url(url),
            "score": -100,
            "counter_evidence": ["excluded_domain"],
        }
    root = _root_url(url)
    supporting_facts: list[str] = []
    counter_evidence: list[str] = []
    evidence_urls: list[str] = []
    schema_org = False
    texts = []
    max_pages = _max_pages_to_fetch()
    for path in SUPPORTING_PATHS:
        if len(evidence_urls) >= max_pages:
            break
        fetched = fetch_text(root + path)
        if not fetched.get("ok") or not fetched.get("text"):
            continue
        final_url = fetched.get("final_url") or root + path
        evidence_urls.append(final_url)
        extracted = extract_html(fetched.get("text", ""), final_url)
        page_text = " ".join([str(extracted.get("title") or ""), str(extracted.get("meta") or ""), str(extracted.get("text") or "")])
        texts.append(page_text)
        if "schema.org/organization" in fetched.get("text", "").casefold() or '"@type"' in fetched.get("text", "").casefold():
            schema_org = True
    combined = normalize_text(" ".join(texts))
    name = provider.get("provider_name", "")
    name_norm = normalize_text(name)
    provider_tokens = tokens(name)
    score = 0
    if evidence_urls:
        score += 15
        supporting_facts.append("candidate_pages_fetch_ok")
    else:
        counter_evidence.append("candidate_pages_not_fetchable")
    if name_norm and name_norm in combined:
        score += 30
        supporting_facts.append("page_contains_exact_provider_name")
    elif provider_tokens and sum(1 for token in provider_tokens if token in combined) >= min(2, len(provider_tokens)):
        score += 18
        supporting_facts.append("page_contains_provider_name_tokens")
    else:
        counter_evidence.append("provider_name_not_found_on_candidate_pages")
    if any(marker in combined for marker in ["llc", "ltd", "limited", "gmbh", "sarl", "s.r.l", "inc", "private limited"]):
        score += 8
        supporting_facts.append("legal_entity_marker_found")
    if re.search(r"[\w.+-]+@[\w.-]+\.[a-z]{2,}", combined):
        score += 8
        supporting_facts.append("contact_email_found")
    if any(term in combined for term in ["contact us", "about us", "privacy policy", "terms of service", "terms and conditions"]):
        score += 8
        supporting_facts.append("standard_company_pages_found")
    service_hits = [kw for kw in config.get("service_keywords", []) if normalize_text(kw) in combined]
    if len(service_hits) >= 3:
        score += 15
        supporting_facts.append("service_content_matches_amazon_provider")
    elif service_hits:
        score += 6
        supporting_facts.append("some_service_content_matches")
    for location in _parse_locations(provider.get("provider_locations", "")):
        if normalize_text(location) and normalize_text(location) in combined:
            score += 7
            supporting_facts.append(f"location_matches:{location}")
            break
        if schema_org:
            score += 5
            supporting_facts.append("schema_org_organization_seen")
        if _looks_non_independent(url):
            score -= 35
            counter_evidence.append("candidate_not_independent_official_site")
        if score >= 75 and len(evidence_urls) >= 2:
            break
    return {
        "url": url,
        "domain": domain_from_url(url),
        "score": max(-100, min(100, score)),
        "evidence_urls": _dedupe(evidence_urls)[:12],
        "supporting_facts": _dedupe(supporting_facts),
        "counter_evidence": _dedupe(counter_evidence),
    }


def _best_replacement(
    provider: dict[str, str],
    candidates: list[SearchCandidate],
    current_url: str,
    config: dict,
) -> dict[str, str]:
    current_domain = domain_from_url(current_url)
    scored = []
    for candidate in candidates[:12]:
        if domain_from_url(candidate.url) == current_domain:
            continue
        try:
            scored.append(score_candidate(provider, candidate, config))
        except Exception:
            continue
    viable = [item for item in scored if not item.get("reject") and item.get("score", 0) >= 70]
    viable.sort(key=lambda item: item.get("score", 0), reverse=True)
    if not viable:
        return {}
    best = viable[0]
    return {
        "url": str(best.get("url") or ""),
        "domain": str(best.get("domain") or ""),
        "score": str(best.get("score") or ""),
        "facts": "; ".join(str(reason) for reason in best.get("reasons", [])[:8]),
    }


def _decide(candidate: dict, replacement: dict[str, str]) -> tuple[str, str, int, str]:
    score = int(candidate.get("score") or 0)
    counters = set(candidate.get("counter_evidence") or [])
    if score >= 70 and "candidate_not_independent_official_site" not in counters:
        return "accept", "", min(100, score), ""
    if replacement.get("url"):
        confidence = max(70, min(95, int(float(replacement.get("score") or 70))))
        return "replace", replacement["url"], confidence, ""
    if score <= 20 and counters:
        return "reject", "", min(90, max(50, 100 - score)), ""
    return "unsure", "", max(0, min(69, score)), "insufficient_or_conflicting_evidence"


def _notes_for(decision: str, candidate: dict, replacement: dict[str, str]) -> str:
    if decision == "replace":
        return f"AgentB replacement: {replacement.get('facts', '')}".strip()
    facts = candidate.get("supporting_facts") or candidate.get("counter_evidence") or []
    return f"AgentB {decision}: {'; '.join(facts[:5])}".strip()


def _safe_collect(queries: list[str], *, per_query: int) -> list[SearchCandidate]:
    try:
        return collect_candidates_for_queries(queries, per_query=per_query)
    except Exception as exc:
        print(f"warning: AgentB independent search failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return []


def _independent_queries(name: str, locations: list[str]) -> list[str]:
    queries = [f'"{name}" official website', f'"{name}" contact']
    if locations:
        queries.append(f'"{name}" "{locations[0]}"')
    return [query for query in queries if name]


def _candidate_url(row: dict[str, str]) -> str:
    for key in ["official_url", "top_candidate_url", "candidate_1_url", "previous_top_candidate_url"]:
        if row.get(key):
            return row[key]
    return ""


def _empty_verification() -> dict:
    return {"url": "", "domain": "", "score": 0, "evidence_urls": [], "supporting_facts": [], "counter_evidence": []}


def _root_url(url: str) -> str:
    parsed = urlparse(url if "://" in url else f"https://{url}")
    return f"{parsed.scheme or 'https'}://{parsed.netloc or parsed.path}".rstrip("/")


def _looks_non_independent(url: str) -> bool:
    domain = domain_from_url(url)
    path = urlparse(url if "://" in url else f"https://{url}").path.casefold()
    risky_domains = {"linkedin.com", "facebook.com", "instagram.com", "youtube.com", "crunchbase.com", "trustpilot.com"}
    if domain in risky_domains or any(domain.endswith(f".{item}") for item in risky_domains):
        return True
    return any(marker in path for marker in ["/profile", "/company/", "/login", "/signin", "/sign-in"])


def _max_pages_to_fetch() -> int:
    try:
        return max(1, int(os.getenv("FINDER_AGENT_B_MAX_PAGES", "4")))
    except ValueError:
        return 4


def _parse_locations(value: str) -> list[str]:
    if not value:
        return []
    try:
        data = json.loads(value)
        if isinstance(data, list):
            return [str(item) for item in data if str(item)]
    except json.JSONDecodeError:
        pass
    return [part.strip() for part in value.split(";") if part.strip()]


def _read_rows(path: Path | None) -> list[dict[str, str]]:
    if not path or not path.exists():
        return []
    with path.open(newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _write_rows(path: Path, rows: list[dict[str, str]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _index_rows(path: Path) -> dict[str, dict[str, str]]:
    return {_row_key(row): row for row in _read_rows(path) if _row_key(row)}


def _row_key(row: dict[str, str]) -> str:
    provider_id = (row.get("provider_id") or "").strip()
    if provider_id:
        return f"id:{provider_id}"
    provider_name = (row.get("provider_name") or "").strip().casefold()
    return f"name:{provider_name}" if provider_name else ""


def _first_existing(paths: list[Path]) -> Path | None:
    for path in paths:
        if path.exists():
            return path
    return None


def _dedupe(values: list[str]) -> list[str]:
    out = []
    for value in values:
        if value and value not in out:
            out.append(value)
    return out


def _counts(rows: list[dict[str, str]], field: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for row in rows:
        value = row.get(field, "")
        out[value] = out.get(value, 0) + 1
    return out


def _update_manifest(path: Path, summary: dict) -> None:
    if not path.exists():
        return
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["agent_b_verification"] = summary
    manifest.setdefault("outputs", {}).update({f"agent_b_{key}": value for key, value in summary["outputs"].items()})
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
