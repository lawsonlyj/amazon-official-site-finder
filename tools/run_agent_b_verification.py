from __future__ import annotations

import argparse
import csv
import json
import multiprocessing as mp
import os
import re
import queue
import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from finder.cli import load_dotenv
from finder.html_extract import extract_html
from finder.http import fetch_text
from finder.logo import logo_evidence
from finder.geo import local_search_terms
from finder.scoring import _candidate_roots, is_excluded_domain, load_config, score_candidate
from finder.search_sources import SearchCandidate, collect_candidates_for_queries
from finder.text import domain_from_url, normalize_text, tokens
from tools.build_linked_workbook import build_workbook
from tools.output_layout import (
    DEFAULT_MATCHED_REVIEW_CONFIDENCE_CUTOFF,
    DEFAULT_SECOND_PASS_REVIEW_CONFIDENCE_CUTOFF,
    WORKFLOW_VERSION,
    agent_b_paths,
    first_existing,
    publish_agent_b_aliases,
)


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
    "review_reason",
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
    parser.add_argument("--resume", action="store_true", help="Reuse existing output rows and keep incremental progress.")
    parser.add_argument(
        "--row-timeout",
        type=int,
        default=_default_row_timeout(),
        help="Maximum seconds per row before recording an unsure timeout result. 0 disables it.",
    )
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
        resume=args.resume,
        row_timeout=args.row_timeout,
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
    resume: bool = False,
    row_timeout: int = 0,
) -> dict:
    run_dir = Path(run_dir)
    config = load_config(config_path)
    rows = _verification_input_rows(run_dir, include_all_final=include_all_final)
    if limit:
        rows = rows[:limit]

    canonical = agent_b_paths(run_dir)
    output_csv_path = Path(output_csv) if output_csv else canonical["csv"]
    output_jsonl_path = Path(output_jsonl) if output_jsonl else canonical["jsonl"]
    output_xlsx_path = Path(output_xlsx) if output_xlsx else canonical["xlsx"]

    existing_rows = _index_existing_rows(output_csv_path) if resume else {}
    existing_json = _index_existing_json(output_jsonl_path) if resume else {}
    result_rows = []
    json_rows = []
    resumed_rows = 0
    processed_rows = 0
    for index, row in enumerate(rows, 1):
        key = _row_key(row)
        if key and key in existing_rows:
            result_rows.append(existing_rows[key])
            json_rows.append(existing_json.get(key) or _details_from_output_row(existing_rows[key]))
            resumed_rows += 1
            continue
        print(f"agent-b {index}/{len(rows)} {row.get('provider_name', '')}", file=sys.stderr)
        result = _verify_row_with_timeout(row, config=config, per_query=per_query, row_timeout=row_timeout)
        result_rows.append(result["row"])
        json_rows.append(result["details"])
        processed_rows += 1
        _write_rows(output_csv_path, result_rows, AGENT_B_FIELDS)
        _write_jsonl(output_jsonl_path, json_rows)

    _write_rows(output_csv_path, result_rows, AGENT_B_FIELDS)
    _write_jsonl(output_jsonl_path, json_rows)

    xlsx_summary = {}
    if write_xlsx:
        xlsx_summary = build_workbook([("AgentB_Verification", output_csv_path)], output_xlsx_path)

    summary = {
        "workflow_version": WORKFLOW_VERSION,
        "input_rows": len(rows),
        "output_rows": len(result_rows),
        "processed_rows": processed_rows,
        "resumed_rows": resumed_rows,
        "timeout_rows": sum(1 for row in result_rows if row.get("reason_for_unsure") == "agent_b_row_timeout"),
        "decision_counts": _counts(result_rows, "agent_b_decision"),
        "outputs": {
            "csv": str(output_csv_path),
            "jsonl": str(output_jsonl_path),
            "xlsx": str(output_xlsx_path) if write_xlsx else "",
        },
        "xlsx": xlsx_summary,
    }
    summary_path = canonical["summary"] if not output_csv else run_dir / "agent_b_verification_summary.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    aliases = publish_agent_b_aliases(
        run_dir,
        {
            "csv": output_csv_path,
            "jsonl": output_jsonl_path,
            "xlsx": output_xlsx_path,
            "summary": summary_path,
        },
    )
    summary["legacy_aliases"] = aliases
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    _update_manifest(run_dir / "manifest.json", summary)
    return summary


def verify_row(row: dict[str, str], *, config: dict, per_query: int = 2) -> dict:
    provider_name = row.get("provider_name", "")
    candidate_url = _candidate_url(row)
    provider_locations = _parse_locations(row.get("provider_locations", ""))
    candidate = _verify_url(candidate_url, row, config) if candidate_url else _empty_verification()
    search_queries = _independent_queries(provider_name, provider_locations)
    if _should_run_independent_search(candidate, row):
        search_candidates = _safe_collect(search_queries, per_query=per_query)
    else:
        search_candidates = []
    replacement = _best_replacement(row, search_candidates, candidate_url, config)

    review_reason = row.get("review_reason", "")
    decision, manual_url, confidence, unsure_reason = _decide(candidate, replacement, review_reason)
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
        "review_reason": review_reason,
    }
    return {
        "row": out_row,
        "details": {
            "provider_id": out_row["provider_id"],
            "provider_name": provider_name,
            "candidate": candidate,
            "replacement": replacement,
            "search_queries": search_queries,
            "independent_search_ran": bool(search_candidates),
            "decision": decision,
        },
    }


def _verify_row_with_timeout(row: dict[str, str], *, config: dict, per_query: int, row_timeout: int) -> dict:
    if row_timeout <= 0:
        return verify_row(row, config=config, per_query=per_query)
    context = _process_timeout_context()
    if context is None:
        return verify_row(row, config=config, per_query=per_query)
    output_queue = context.Queue(maxsize=1)
    process = context.Process(target=_verify_row_worker, args=(output_queue, row, config, per_query))
    process.daemon = True
    process.start()
    process.join(row_timeout)
    if process.is_alive():
        process.terminate()
        process.join(2)
        if process.is_alive():
            process.kill()
            process.join(1)
        print(f"warning: AgentB row timed out after {row_timeout}s: {row.get('provider_name', '')}", file=sys.stderr)
        return _timeout_result(row, row_timeout)
    try:
        message = output_queue.get(timeout=1)
    except queue.Empty:
        return _timeout_result(row, row_timeout, reason="agent_b_row_no_result")
    if message.get("ok"):
        return message["result"]
    print(
        f"warning: AgentB row failed: {message.get('error_type', 'error')}: {message.get('error', '')}",
        file=sys.stderr,
    )
    return _timeout_result(row, row_timeout, reason=f"agent_b_row_error:{message.get('error_type', 'error')}")


def _verify_row_worker(output_queue, row: dict[str, str], config: dict, per_query: int) -> None:
    try:
        output_queue.put({"ok": True, "result": verify_row(row, config=config, per_query=per_query)})
    except Exception as exc:
        output_queue.put({"ok": False, "error_type": type(exc).__name__, "error": str(exc)})


def _process_timeout_context():
    try:
        if "fork" in mp.get_all_start_methods():
            return mp.get_context("fork")
        return mp.get_context()
    except (RuntimeError, ValueError):
        return None


def _timeout_result(row: dict[str, str], row_timeout: int, *, reason: str = "agent_b_row_timeout") -> dict:
    candidate_url = _candidate_url(row)
    search_queries = _independent_queries(row.get("provider_name", ""), _parse_locations(row.get("provider_locations", "")))
    out_row = {
        "provider_id": row.get("provider_id", ""),
        "provider_name": row.get("provider_name", ""),
        "provider_detail_url": row.get("provider_detail_url", ""),
        "candidate_url": candidate_url,
        "candidate_domain": domain_from_url(candidate_url),
        "agent_b_decision": "unsure",
        "manual_decision": "unsure",
        "manual_url": "",
        "confidence": "0",
        "evidence_score": "0",
        "evidence_urls": "",
        "supporting_facts": "",
        "counter_evidence": reason,
        "reason_for_unsure": reason,
        "notes": f"AgentB unsure: row timed out after {row_timeout}s" if reason == "agent_b_row_timeout" else f"AgentB unsure: {reason}",
        "independent_search_queries": "; ".join(search_queries),
        "replacement_url": "",
        "replacement_domain": "",
        "source_status": row.get("status", ""),
        "source_confidence": row.get("confidence", ""),
        "review_reason": row.get("review_reason", ""),
    }
    return {
        "row": out_row,
        "details": {
            "provider_id": out_row["provider_id"],
            "provider_name": out_row["provider_name"],
            "candidate": {
                "url": candidate_url,
                "domain": domain_from_url(candidate_url),
                "score": 0,
                "evidence_urls": [],
                "supporting_facts": [],
                "counter_evidence": [reason],
            },
            "replacement": {},
            "search_queries": search_queries,
            "decision": "unsure",
            "timeout_seconds": row_timeout,
        },
    }


def _verification_input_rows(run_dir: Path, *, include_all_final: bool) -> list[dict[str, str]]:
    final_path = first_existing(
        run_dir,
        "official_sites.csv",
        "provider_final_official_websites_second_pass.csv",
        "details/first_pass/final.csv",
        "provider_final_official_websites.csv",
    )
    final_rows = _read_rows(final_path)
    final_by_key = {_row_key(row): row for row in final_rows if _row_key(row)}
    second_pass = _index_rows(
        first_existing(run_dir, "details/second_pass/results.csv", "unresolved_second_pass_results.csv")
        or run_dir / "details/second_pass/results.csv"
    )
    manual_task = first_existing(run_dir, "review_task.csv", "manual_official_site_review_task.csv")
    if manual_task and manual_task.exists() and not include_all_final:
        task_rows = _read_rows(manual_task)
        out = []
        for task_row in task_rows:
            merged = dict(final_by_key.get(_row_key(task_row), {}))
            merged.update({key: value for key, value in task_row.items() if value})
            out.append(merged)
        return out
    out = []
    for row in final_rows:
        merged = dict(row)
        second = second_pass.get(_row_key(row), {})
        if second.get("previous_top_candidate_url") and not merged.get("top_candidate_url"):
            merged["top_candidate_url"] = second["previous_top_candidate_url"]
        if include_all_final or _is_high_risk_for_agent_b(merged, second):
            out.append(merged)
    return out


def _is_high_risk_for_agent_b(row: dict[str, str], second_pass_row: dict[str, str]) -> bool:
    status = row.get("status", "")
    confidence = _to_int(row.get("confidence"))
    evidence = (row.get("evidence_summary") or second_pass_row.get("evidence_summary") or "").casefold()
    candidate_url = _candidate_url(row) or second_pass_row.get("official_url", "") or second_pass_row.get("previous_top_candidate_url", "")
    if not row.get("official_url"):
        return True
    if status == "manual_accepted" or second_pass_row.get("accepted_for_final") == "true":
        return True
    if status != "matched":
        return True
    if confidence < DEFAULT_MATCHED_REVIEW_CONFIDENCE_CUTOFF:
        return True
    if "identity_cap_" in evidence or "page_industry_mismatch:" in evidence:
        return True
    if _looks_non_independent(candidate_url):
        return True
    if _high_confidence_ambiguous_identity_risk(row, evidence, confidence):
        return True
    if _has_generic_or_ambiguous_name(row.get("provider_name", "")) and not _has_strong_identity_evidence(evidence):
        return True
    has_logo = "listing_logo_visual_match" in evidence or "listing_logo_visual_near_match" in evidence
    has_name = "page_contains_exact_provider_name" in evidence or "page_contains_provider_name_tokens" in evidence
    has_service = "page_contains_amazon_service_keywords" in evidence or "page_mentions_amazon_spn" in evidence
    if has_logo and not (has_name or has_service):
        return True
    if "provider_name_not_found" in evidence:
        return True
    return False


def _has_strong_identity_evidence(evidence: str) -> bool:
    if "identity_cap_" in evidence or "page_industry_mismatch:" in evidence:
        return False
    has_page_name = (
        "page_contains_exact_provider_name" in evidence
        or "page_contains_provider_name_tokens" in evidence
        or "page_fuzzy_provider_name_match" in evidence
    )
    has_name = has_page_name or "search_result_contains_exact_name" in evidence or "domain_exact_provider_slug" in evidence
    has_service = (
        "page_contains_amazon_service_keywords" in evidence
        or "page_contains_some_service_keywords" in evidence
        or "page_mentions_amazon_spn" in evidence
        or "search_snippet_contains_amazon_service_keywords" in evidence
    )
    has_domain_or_logo = "domain_exact_provider_slug" in evidence or "listing_logo_visual_match" in evidence
    return has_name and has_service and (has_page_name or has_domain_or_logo)


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
    supporting_facts: list[str] = []
    counter_evidence: list[str] = []
    evidence_urls: list[str] = []
    counter_evidence.extend(_page_risks(url, {}))
    page_roles: list[str] = []
    organizations: list[dict[str, str]] = []
    texts = []
    logo_checked = False
    max_pages = _max_pages_to_fetch()
    for root in _candidate_roots(url):
        root_had_fetch = False
        for path in SUPPORTING_PATHS:
            if len(evidence_urls) >= max_pages:
                break
            fetched = fetch_text(root + path)
            if not fetched.get("ok") or not fetched.get("text"):
                continue
            root_had_fetch = True
            final_url = fetched.get("final_url") or root + path
            evidence_urls.append(final_url)
            if not logo_checked and provider.get("listing_logo_url"):
                logo = logo_evidence(provider.get("listing_logo_url", ""), fetched.get("text", ""), final_url)
                logo_checked = True
                if logo.get("matched"):
                    supporting_facts.append("listing_logo_visual_match")
                elif float(logo.get("score") or 0) >= 0.78:
                    supporting_facts.append("listing_logo_visual_near_match")
            html = fetched.get("text", "")
            extracted = extract_html(html, final_url)
            page_text = " ".join(
                [
                    str(extracted.get("title") or ""),
                    str(extracted.get("meta") or ""),
                    str(extracted.get("h1") or ""),
                    str(extracted.get("h2") or ""),
                    str(extracted.get("nav") or ""),
                    str(extracted.get("footer") or ""),
                    str(extracted.get("text") or ""),
                    _organizations_text(extracted.get("organizations") or []),
                ]
            )
            texts.append(page_text)
            if extracted.get("h1"):
                supporting_facts.append("page_h1_seen")
            if extracted.get("footer"):
                supporting_facts.append("footer_text_seen")
            if extracted.get("mailto_links"):
                supporting_facts.append("mailto_link_found")
            if extracted.get("tel_links"):
                supporting_facts.append("telephone_link_found")
            for role in _page_roles(final_url, extracted):
                supporting_facts.append(f"page_role:{role}")
                page_roles.append(role)
            for risk in _page_risks(final_url, extracted):
                counter_evidence.append(risk)
            orgs = extracted.get("organizations") or []
            if orgs:
                organizations.extend([org for org in orgs if isinstance(org, dict)])
        if root_had_fetch:
            if domain_from_url(root) != domain_from_url(url):
                supporting_facts.append("canonical_domain_variant_fetch_ok")
            break
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
    if "mailto_link_found" in supporting_facts:
        score += 5
    if "telephone_link_found" in supporting_facts:
        score += 3
    if any(term in combined for term in ["contact us", "about us", "privacy policy", "terms of service", "terms and conditions"]):
        score += 8
        supporting_facts.append("standard_company_pages_found")
    if {"page_role:about", "page_role:contact"} <= set(supporting_facts):
        score += 5
        supporting_facts.append("about_and_contact_pages_found")
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
    org_facts = _organization_facts(organizations, provider, combined)
    supporting_facts.extend(org_facts["supporting_facts"])
    counter_evidence.extend(org_facts["counter_evidence"])
    score += org_facts["score"]
    if organizations:
        score += 5
        supporting_facts.append("schema_org_organization_seen")
    if "listing_logo_visual_match" in supporting_facts:
        score += 18
    elif "listing_logo_visual_near_match" in supporting_facts:
        score += 8
    if _looks_non_independent(url):
        score -= 35
        counter_evidence.append("candidate_not_independent_official_site")
    if _has_blocking_page_risk(counter_evidence):
        score -= 35
    if _has_identity_gap(provider, combined, supporting_facts):
        score -= 12
        counter_evidence.append("identity_gap_location_or_service_context_missing")
    return {
        "url": url,
        "domain": domain_from_url(url),
        "score": max(-100, min(100, score)),
        "evidence_urls": _dedupe(evidence_urls)[:12],
        "supporting_facts": _dedupe(supporting_facts),
        "counter_evidence": _dedupe(counter_evidence),
        "page_roles": _dedupe(page_roles),
        "organizations": organizations[:10],
    }


def _should_run_independent_search(candidate: dict, row: dict[str, str]) -> bool:
    if not _candidate_url(row):
        return True
    score = int(candidate.get("score") or 0)
    counters = set(candidate.get("counter_evidence") or [])
    if score < 70:
        return True
    if counters & {
        "candidate_pages_not_fetchable",
        "provider_name_not_found_on_candidate_pages",
        "candidate_not_independent_official_site",
        "candidate_looks_platform_profile",
        "candidate_looks_directory_page",
        "candidate_looks_parked_or_domain_sale",
        "identity_gap_location_or_service_context_missing",
    }:
        return True
    return False


def _organizations_text(organizations: list[dict[str, str]]) -> str:
    parts = []
    for org in organizations:
        for key in ["name", "legalName", "url", "address", "contactPoint"]:
            value = org.get(key)
            if value:
                parts.append(str(value))
    return " ".join(parts)


def _organization_facts(organizations: list[dict[str, str]], provider: dict[str, str], combined_text: str) -> dict:
    if not organizations:
        return {"score": 0, "supporting_facts": [], "counter_evidence": []}
    supporting: list[str] = []
    counters: list[str] = []
    score = 0
    name_norm = normalize_text(provider.get("provider_name", ""))
    provider_tokens = tokens(provider.get("provider_name", ""))
    org_blob = normalize_text(_organizations_text(organizations))
    if name_norm and name_norm in org_blob:
        supporting.append("schema_org_name_matches_provider")
        score += 15
    elif provider_tokens and sum(1 for token in provider_tokens if token in org_blob) >= min(2, len(provider_tokens)):
        supporting.append("schema_org_name_token_match")
        score += 8
    if any(org.get("legalName") for org in organizations):
        supporting.append("schema_org_legal_name_seen")
        score += 6
    if any(org.get("address") for org in organizations):
        supporting.append("schema_org_address_seen")
        score += 5
    if any(org.get("contactPoint") for org in organizations):
        supporting.append("schema_org_contact_point_seen")
        score += 5
    for location in _parse_locations(provider.get("provider_locations", "")):
        loc = normalize_text(location)
        if loc and (loc in org_blob or loc in combined_text):
            supporting.append(f"schema_or_page_location_matches:{location}")
            score += 5
            break
    if org_blob and name_norm and name_norm not in org_blob and "schema_org_name_matches_provider" not in supporting:
        counters.append("schema_org_name_does_not_confirm_provider")
    return {"score": score, "supporting_facts": supporting, "counter_evidence": counters}


def _page_roles(url: str, extracted: dict[str, object]) -> list[str]:
    path = urlparse(url if "://" in url else f"https://{url}").path.casefold()
    text = normalize_text(
        " ".join(
            [
                str(extracted.get("title") or ""),
                str(extracted.get("h1") or ""),
                str(extracted.get("nav") or ""),
                str(extracted.get("footer") or ""),
            ]
        )
    )
    roles = []
    if path in {"", "/"}:
        roles.append("home")
    if "about" in path or "about us" in text:
        roles.append("about")
    if "contact" in path or "contact us" in text or extracted.get("mailto_links"):
        roles.append("contact")
    if "service" in path or "services" in text:
        roles.append("services")
    if "privacy" in path or "privacy policy" in text:
        roles.append("privacy")
    if "terms" in path or "terms and conditions" in text or "terms of service" in text:
        roles.append("terms")
    return _dedupe(roles)


def _page_risks(url: str, extracted: dict[str, object]) -> list[str]:
    parsed = urlparse(url if "://" in url else f"https://{url}")
    path = parsed.path.casefold()
    domain = domain_from_url(url)
    text = normalize_text(
        " ".join(
            [
                str(extracted.get("title") or ""),
                str(extracted.get("meta") or ""),
                str(extracted.get("h1") or ""),
                str(extracted.get("h2") or ""),
                str(extracted.get("footer") or ""),
                str(extracted.get("text") or "")[:6000],
            ]
        )
    )
    risks = []
    if any(marker in path for marker in ["/profile", "/profiles/", "/company/", "/companies/"]):
        risks.append("candidate_looks_platform_profile")
    if any(marker in path for marker in ["/login", "/signin", "/sign-in", "/app", "/dashboard"]):
        risks.append("candidate_looks_login_or_app_page")
    if any(marker in path for marker in ["/docs", "/documentation", "/help", "/support", "/kb", "/api"]):
        risks.append("candidate_looks_docs_help_support_page")
    if any(marker in text for marker in ["domain for sale", "buy this domain", "this domain may be for sale", "sedo domain parking", "hugedomains"]):
        risks.append("candidate_looks_parked_or_domain_sale")
    directory_markers = [
        "claim this profile",
        "company profile",
        "business directory",
        "supplier profile",
        "view profile",
        "review this company",
    ]
    if any(marker in text for marker in directory_markers):
        risks.append("candidate_looks_directory_page")
    directory_domains = {"clutch.co", "goodfirms.co", "crunchbase.com", "trustpilot.com", "indiamart.com", "kompass.com"}
    if domain in directory_domains or any(domain.endswith(f".{item}") for item in directory_domains):
        risks.append("candidate_looks_directory_page")
    return _dedupe(risks)


def _has_blocking_page_risk(counter_evidence: list[str]) -> bool:
    blocking = {
        "candidate_looks_platform_profile",
        "candidate_looks_directory_page",
        "candidate_looks_login_or_app_page",
        "candidate_looks_docs_help_support_page",
        "candidate_looks_parked_or_domain_sale",
    }
    return bool(blocking & set(counter_evidence))


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


def _decide(candidate: dict, replacement: dict[str, str], review_reason: str = "") -> tuple[str, str, int, str]:
    score = int(candidate.get("score") or 0)
    counters = set(candidate.get("counter_evidence") or [])
    facts = set(candidate.get("supporting_facts") or [])
    if _is_recall_review_reason(review_reason):
        replacement_score = _to_int(replacement.get("score"))
        confidence = max(score, replacement_score)
        return "unsure", "", max(0, min(69, confidence)), "recall_candidate_needs_human_confirmation"
    if replacement.get("url") and review_reason:
        replacement_score = _to_int(replacement.get("score"))
        confidence = max(score, replacement_score)
        return "unsure", "", max(0, min(69, confidence)), "replacement_candidate_needs_human_confirmation"
    if review_reason and counters:
        return "unsure", "", min(69, max(0, score)), "high_risk_counter_evidence_needs_human_confirmation"
    if _is_precision_high_risk_reason(review_reason) and "listing_logo_visual_match" not in facts:
        return "unsure", "", min(69, score), "high_risk_identity_needs_human_confirmation"
    if score >= 70 and "candidate_not_independent_official_site" not in counters and not _has_blocking_page_risk(list(counters)):
        return "accept", "", min(100, score), ""
    if replacement.get("url"):
        confidence = max(70, min(95, int(float(replacement.get("score") or 70))))
        return "replace", replacement["url"], confidence, ""
    if score <= 20 and counters:
        return "reject", "", min(90, max(50, 100 - score)), ""
    return "unsure", "", max(0, min(69, score)), "insufficient_or_conflicting_evidence"


def _is_precision_high_risk_reason(reason: str) -> bool:
    return reason in {
        "precision_low_confidence_auto_match",
        "precision_second_pass_accepted_70_84",
        "precision_generic_identity_term_risk",
        "precision_slug_extension_identity_risk",
    }


def _is_recall_review_reason(reason: str) -> bool:
    return reason.startswith("recall_unresolved")


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
    for term in local_search_terms(locations)[:4]:
        queries.append(f'"{name}" "{term}"')
    return [query for query in queries if name]


def _candidate_url(row: dict[str, str]) -> str:
    for key in ["official_url", "top_candidate_url", "candidate_1_url", "previous_top_candidate_url"]:
        if row.get(key):
            return row[key]
    return ""


def _empty_verification() -> dict:
    return {
        "url": "",
        "domain": "",
        "score": 0,
        "evidence_urls": [],
        "supporting_facts": [],
        "counter_evidence": [],
        "page_roles": [],
        "organizations": [],
    }


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


def _has_generic_or_ambiguous_name(name: str) -> bool:
    provider_tokens = tokens(name)
    if not provider_tokens:
        return False
    generic_tokens = {
        "amazon",
        "account",
        "agency",
        "consulting",
        "consultancy",
        "digital",
        "ecom",
        "ecommerce",
        "e-commerce",
        "global",
        "growth",
        "management",
        "marketplace",
        "media",
        "seller",
        "sellers",
        "service",
        "services",
        "solution",
        "solutions",
    }
    meaningful = [token for token in provider_tokens if token not in generic_tokens]
    return len(meaningful) <= 1 or len("".join(provider_tokens)) <= 4


def _high_confidence_ambiguous_identity_risk(row: dict[str, str], evidence: str, confidence: int) -> bool:
    if confidence < 85 or not _has_generic_or_ambiguous_name(row.get("provider_name", "")):
        return False
    if "listing_logo_visual_match" in evidence:
        return False
    name = row.get("provider_name", "").casefold()
    if "consult" in name or "seller" in name:
        return True
    return "domain_contains_provider_slug" in evidence and "domain_exact_provider_slug" not in evidence


def _to_int(value: object) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def _has_identity_gap(provider: dict[str, str], combined_text: str, supporting_facts: list[str]) -> bool:
    if not combined_text:
        return True
    name = provider.get("provider_name", "")
    provider_tokens = tokens(name)
    generic_tokens = {
        "amazon",
        "account",
        "management",
        "service",
        "services",
        "seller",
        "sellers",
        "ecommerce",
        "e-commerce",
        "marketplace",
        "boosting",
        "growth",
        "consulting",
        "consultancy",
    }
    meaningful_tokens = [token for token in provider_tokens if token not in generic_tokens]
    if provider_tokens and not meaningful_tokens and "page_contains_exact_provider_name" not in supporting_facts:
        return True
    locations = _parse_locations(provider.get("provider_locations", ""))
    if locations and not any(normalize_text(location) in combined_text for location in locations):
        has_strong_identity = any(
            fact in supporting_facts
            for fact in ["page_contains_exact_provider_name", "legal_entity_marker_found", "contact_email_found"]
        )
        if not has_strong_identity:
            return True
    return False


def _max_pages_to_fetch() -> int:
    try:
        return max(1, int(os.getenv("FINDER_AGENT_B_MAX_PAGES", "4")))
    except ValueError:
        return 4


def _default_row_timeout() -> int:
    try:
        return max(0, int(os.getenv("FINDER_AGENT_B_ROW_TIMEOUT", "0")))
    except ValueError:
        return 0


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


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _index_existing_rows(path: Path) -> dict[str, dict[str, str]]:
    return {_row_key(row): row for row in _read_rows(path) if _row_key(row)}


def _index_existing_json(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    out: dict[str, dict] = {}
    with path.open(encoding="utf-8") as f:
        for line in f:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            key = _row_key(row)
            if key:
                out[key] = row
    return out


def _details_from_output_row(row: dict[str, str]) -> dict:
    return {
        "provider_id": row.get("provider_id", ""),
        "provider_name": row.get("provider_name", ""),
        "candidate": {
            "url": row.get("candidate_url", ""),
            "domain": row.get("candidate_domain", ""),
            "score": _to_int(row.get("evidence_score")),
            "evidence_urls": [item.strip() for item in row.get("evidence_urls", "").split(";") if item.strip()],
            "supporting_facts": [item.strip() for item in row.get("supporting_facts", "").split(";") if item.strip()],
            "counter_evidence": [item.strip() for item in row.get("counter_evidence", "").split(";") if item.strip()],
            "page_roles": [],
            "organizations": [],
        },
        "replacement": {
            "url": row.get("replacement_url", ""),
            "domain": row.get("replacement_domain", ""),
        },
        "search_queries": [item.strip() for item in row.get("independent_search_queries", "").split(";") if item.strip()],
        "decision": row.get("agent_b_decision", ""),
    }


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
    manifest.setdefault("legacy_aliases", {})["agent_b"] = summary.get("legacy_aliases", {})
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
