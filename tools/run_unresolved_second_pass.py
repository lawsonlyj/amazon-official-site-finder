from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from finder.cli import load_dotenv
from finder.finalize import finalize_results
from finder.input_normalizer import read_normalized_csv
from finder.scoring import choose_best, is_excluded_domain, load_config
from finder.search_sources import SearchCandidate, collect_candidates_for_queries
from finder.text import domain_from_url, slug, tokens, url_like_candidates
from tools.build_linked_workbook import build_workbook
from tools.evaluate_labeled_results import read_rows as read_csv_rows
from tools.plan_unresolved_second_pass import build_second_pass_plan, summarize_plan
from tools.quality_gate import evaluate_quality_gate, write_markdown as write_quality_markdown


RESULT_FIELDS = [
    "provider_id",
    "provider_name",
    "provider_detail_url",
    "strategy_tier",
    "previous_status",
    "previous_confidence",
    "previous_top_candidate_url",
    "official_url",
    "official_domain",
    "confidence",
    "status",
    "accepted_for_final",
    "evidence_summary",
    "candidate_count",
    "scored_candidate_count",
    "service_apis",
    "provider_locations",
    "notes",
]

REVIEW_FIELDS = [
    "provider_id",
    "provider_name",
    "manual_decision",
    "manual_url",
    "notes",
    "confidence",
    "source_status",
    "evidence_summary",
    "candidate_count",
    "scored_candidate_count",
    "service_apis",
    "provider_locations",
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run second-pass discovery for unresolved provider rows.")
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--config", default="config/scoring.json")
    parser.add_argument("--labels")
    parser.add_argument("--per-query", type=int, default=3)
    parser.add_argument("--max-search-queries", type=int, default=6)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--accept-threshold", type=int, default=70)
    parser.add_argument("--write-xlsx", action="store_true")
    parser.add_argument("--min-domain-accuracy", type=float, default=0.8)
    parser.add_argument("--min-auto-precision", type=float, default=0.95)
    parser.add_argument("--min-official-url-rate", type=float, default=0.5)
    parser.add_argument("--max-unresolved-rate", type=float, default=0.6)
    args = parser.parse_args(argv)

    load_dotenv(Path(".env"))
    summary = run_unresolved_second_pass(
        run_dir=args.run_dir,
        config_path=args.config,
        labels_csv=args.labels,
        per_query=args.per_query,
        max_search_queries=args.max_search_queries,
        limit=args.limit or None,
        resume=args.resume,
        accept_threshold=args.accept_threshold,
        write_xlsx=args.write_xlsx,
        min_domain_accuracy=args.min_domain_accuracy,
        min_auto_precision=args.min_auto_precision,
        min_official_url_rate=args.min_official_url_rate,
        max_unresolved_rate=args.max_unresolved_rate,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary.get("quality_overall", {}).get("passed", True) else 1


def run_unresolved_second_pass(
    *,
    run_dir: str | Path,
    config_path: str | Path = "config/scoring.json",
    labels_csv: str | Path | None = None,
    per_query: int = 3,
    max_search_queries: int = 6,
    limit: int | None = None,
    resume: bool = False,
    accept_threshold: int = 70,
    write_xlsx: bool = False,
    min_domain_accuracy: float = 0.8,
    min_auto_precision: float = 0.95,
    min_official_url_rate: float = 0.5,
    max_unresolved_rate: float = 0.6,
) -> dict:
    run_dir = Path(run_dir)
    paths = second_pass_paths(run_dir)
    config = _second_pass_config(load_config(config_path), accept_threshold)
    providers = {provider["provider_id"]: provider for provider in read_normalized_csv(run_dir / "providers_normalized.csv")}
    plan_rows = build_second_pass_plan(run_dir)
    if limit:
        plan_rows = plan_rows[:limit]
    _write_rows(paths["plan"], plan_rows, _plan_fields(plan_rows))

    done_ids = _done_provider_ids(paths["results"]) if resume and paths["results"].exists() else set()
    run_rows = [row for row in plan_rows if row.get("provider_id") not in done_ids]
    append = bool(done_ids)
    output = _open_writer(paths["results"], RESULT_FIELDS, append=append)
    evidence_f = paths["evidence"].open("a" if append else "w", encoding="utf-8")
    try:
        for index, plan_row in enumerate(run_rows, 1):
            provider = providers.get(plan_row.get("provider_id"), {})
            result, candidates = _run_one(
                provider,
                plan_row,
                config,
                per_query=per_query,
                max_search_queries=max_search_queries,
            )
            accepted = _accepted(result, config, accept_threshold)
            rescue_used = False
            if not accepted:
                rescue_result, rescue_candidates = _run_seed_rescue(provider, plan_row, config)
                if _accepted(rescue_result, config, accept_threshold):
                    result, candidates, accepted = rescue_result, rescue_candidates, True
                    rescue_used = True
            output["writer"].writerow(_result_row(provider, plan_row, result, candidates, accepted, rescue_used=rescue_used))
            evidence_f.write(
                json.dumps(
                    {
                        "provider_id": plan_row.get("provider_id", ""),
                        "provider_name": plan_row.get("provider_name", ""),
                        "strategy_tier": plan_row.get("strategy_tier", ""),
                        "result": {key: result.get(key) for key in ["official_url", "official_domain", "confidence", "status", "evidence_summary"]},
                        "accepted_for_final": accepted,
                        "rescue_used": rescue_used,
                        "candidate_count": len(candidates),
                        "candidates": result.get("candidates", []),
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
            print(
                f"second-pass {index}/{len(run_rows)} {plan_row.get('provider_name', '')}: "
                f"{result.get('status')} {result.get('confidence')} {result.get('official_url', '')}",
                file=sys.stderr,
            )
    finally:
        output["file"].close()
        evidence_f.close()

    result_rows = _read_rows(paths["results"])
    review_rows = [_review_decision_row(row) for row in result_rows if row.get("accepted_for_final") == "true"]
    _write_rows(paths["review_decisions"], review_rows, REVIEW_FIELDS)

    final_summary = finalize_results(
        run_dir / "provider_official_websites_enriched.csv",
        paths["final"],
        review_csv=paths["review_decisions"],
        unresolved_csv=paths["unresolved"],
    )
    quality = evaluate_quality_gate(
        results_csv=paths["final"],
        config=config,
        labels=read_csv_rows(labels_csv) if labels_csv else None,
        expected_rows=len(providers),
        min_domain_accuracy=min_domain_accuracy,
        min_auto_precision=min_auto_precision,
        min_official_url_rate=min_official_url_rate,
        max_unresolved_rate=max_unresolved_rate,
    )
    write_quality_markdown(quality, paths["quality_md"])
    paths["quality_json"].write_text(json.dumps(quality, ensure_ascii=False, indent=2), encoding="utf-8")

    xlsx = {}
    if write_xlsx:
        xlsx["final"] = build_workbook(
            [
                ("Final_Second_Pass", paths["final"]),
                ("Second_Pass_Results", paths["results"]),
                ("Review_Decisions", paths["review_decisions"]),
            ],
            paths["xlsx"],
        )

    summary = {
        "plan": summarize_plan(plan_rows) | {"output_csv": str(paths["plan"])},
        "processed_rows": len(result_rows),
        "newly_processed_rows": len(run_rows),
        "accepted_rows": len(review_rows),
        "second_pass_status_counts": dict(Counter(row.get("status", "") for row in result_rows)),
        "finalize": final_summary,
        "quality_overall": quality["overall"],
        "outputs": {name: str(path) for name, path in paths.items()},
        "xlsx": xlsx,
    }
    paths["summary"].write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    _update_manifest(run_dir / "manifest.json", summary)
    return summary


def second_pass_paths(run_dir: str | Path) -> dict[str, Path]:
    run_dir = Path(run_dir)
    return {
        "plan": run_dir / "unresolved_second_pass_plan.csv",
        "results": run_dir / "unresolved_second_pass_results.csv",
        "evidence": run_dir / "unresolved_second_pass_evidence.jsonl",
        "review_decisions": run_dir / "unresolved_second_pass_review_decisions.csv",
        "final": run_dir / "provider_final_official_websites_second_pass.csv",
        "unresolved": run_dir / "provider_unresolved_second_pass.csv",
        "quality_md": run_dir / "quality_gate_provider_second_pass_final.md",
        "quality_json": run_dir / "quality_gate_provider_second_pass_final.json",
        "summary": run_dir / "unresolved_second_pass_summary.json",
        "xlsx": run_dir / "provider_official_websites_second_pass_with_clickable_links.xlsx",
    }


def _run_one(
    provider: dict,
    plan_row: dict[str, str],
    config: dict,
    *,
    per_query: int,
    max_search_queries: int,
) -> tuple[dict, list[SearchCandidate]]:
    candidates = _seed_candidates(provider, plan_row, config)
    if _should_search(plan_row):
        queries = _queries_from_plan(plan_row, max_search_queries)
        candidates.extend(
            collect_candidates_for_queries(
                queries,
                per_query=per_query,
                source_queries={"exa": _exa_semantic_queries(provider, plan_row)},
                skip_sources={"exa"},
            )
        )
    candidates = _dedupe_candidates(candidates)
    result = choose_best(provider, candidates, config)
    return result, candidates


def _run_seed_rescue(provider: dict, plan_row: dict[str, str], config: dict) -> tuple[dict, list[SearchCandidate]]:
    rescue_config = _rescue_config(config)
    candidates = _dedupe_candidates(_seed_candidates(provider, plan_row, rescue_config))
    result = choose_best(provider, candidates, rescue_config)
    return result, candidates


def _seed_candidates(provider: dict, plan_row: dict[str, str], config: dict) -> list[SearchCandidate]:
    candidates: list[SearchCandidate] = []
    url = plan_row.get("top_candidate_url", "")
    if url and not is_excluded_domain(url, config):
        candidates.append(
            SearchCandidate(
                url=url,
                title=f"second-pass top candidate for {plan_row.get('provider_name', '')}",
                snippet="",
                source="second_pass_top_candidate",
                query=plan_row.get("notes", ""),
                rank=1,
            )
        )
    candidates.extend(_input_url_candidates(provider, config))
    candidates.extend(_domain_variant_candidates(provider, config))
    return candidates


def _should_search(plan_row: dict[str, str]) -> bool:
    return True


def _queries_from_plan(plan_row: dict[str, str], max_search_queries: int) -> list[str]:
    queries = [plan_row.get(f"query_{idx}", "") for idx in range(1, 9)]
    return [query for query in queries if query][:max_search_queries]


def _exa_semantic_queries(provider: dict, plan_row: dict[str, str]) -> list[str]:
    name = provider.get("provider_name", "") or plan_row.get("provider_name", "")
    if not name:
        return []
    locations = provider.get("provider_locations") or []
    services = provider.get("service_apis") or []
    location = locations[0] if locations else plan_row.get("location", "")
    service = services[0] if services else plan_row.get("services", "").split("; ")[0]
    terms = [
        f'official company website for "{name}" Amazon seller service provider {location}'.strip(),
        f'"{name}" ecommerce agency marketplace services official site contact {location}'.strip(),
        f'"{name}" Amazon Seller Central marketplace agency official website contact'.strip(),
        f'"{name}" about us contact official website {location}'.strip(),
    ]
    if service:
        terms.append(f'official website for "{name}" providing {service} services')
    try:
        limit = int(os.getenv("FINDER_SECOND_PASS_EXA_QUERIES", "3"))
    except ValueError:
        limit = 3
    terms = _dedupe_strings(terms)
    return terms[: max(0, limit)]


def _input_url_candidates(provider: dict, config: dict) -> list[SearchCandidate]:
    text_parts = [
        provider.get("provider_name", ""),
        provider.get("about_listing_text", ""),
        provider.get("service_description", ""),
        " ".join(provider.get("service_apis") or []),
        " ".join(provider.get("service_types") or []),
    ]
    candidates = []
    for rank, raw in enumerate(url_like_candidates(" ".join(text_parts)), 1):
        url = raw if raw.startswith("http") else f"https://{raw}"
        if is_excluded_domain(url, config):
            continue
        candidates.append(
            SearchCandidate(
                url=url,
                title=f"URL mentioned in provider input for {provider.get('provider_name', '')}",
                snippet=provider.get("about_listing_text", "")[:500],
                source="second_pass_input_url",
                query=provider.get("provider_name", ""),
                rank=rank,
            )
        )
    return candidates


def _domain_variant_candidates(provider: dict, config: dict) -> list[SearchCandidate]:
    variants = _brand_slug_variants(provider.get("provider_name", ""))
    tlds = _location_tlds(provider.get("provider_locations") or [])
    candidates = []
    rank = 1
    for variant in variants[:4]:
        for tld in tlds:
            url = f"https://www.{variant}.{tld}"
            if is_excluded_domain(url, config):
                continue
            candidates.append(
                SearchCandidate(
                    url=url,
                    title="second-pass brand domain variant",
                    snippet="",
                    source="second_pass_domain_variant",
                    query=provider.get("provider_name", ""),
                    rank=0,
                )
            )
            rank += 1
    return candidates


def _brand_slug_variants(name: str) -> list[str]:
    provider_tokens = tokens(name)
    variants = [slug(name)]
    generic_trailing = {
        "agency",
        "consulting",
        "consultancy",
        "digital",
        "ecom",
        "ecommerce",
        "global",
        "group",
        "infotech",
        "marketing",
        "media",
        "services",
        "service",
        "solution",
        "solutions",
        "technologies",
        "technology",
    }
    trimmed = list(provider_tokens)
    while len(trimmed) > 1 and trimmed[-1] in generic_trailing:
        trimmed.pop()
        variants.append(slug(" ".join(trimmed)))
    if len(provider_tokens) >= 2:
        variants.append(slug(" ".join(provider_tokens[:2])))
    return _dedupe_strings([variant for variant in variants if len(variant) >= 4])


def _location_tlds(locations: list[str]) -> list[str]:
    tlds = ["com", "co", "io", "net"]
    location_text = " ".join(locations).casefold()
    location_tlds = {
        "united kingdom": ["co.uk", "uk"],
        "uk": ["co.uk", "uk"],
        "india": ["in", "co.in"],
        "germany": ["de"],
        "italy": ["it"],
        "france": ["fr"],
        "spain": ["es"],
        "brazil": ["com.br", "br"],
        "united arab emirates": ["ae"],
        "uae": ["ae"],
        "china": ["cn", "com.cn"],
        "canada": ["ca"],
        "australia": ["com.au", "au"],
        "singapore": ["sg", "com.sg"],
        "netherlands": ["nl"],
        "poland": ["pl"],
    }
    for marker, extras in location_tlds.items():
        if marker in location_text:
            tlds = extras + tlds
            break
    return _dedupe_strings(tlds)


def _dedupe_strings(values: list[str]) -> list[str]:
    seen = set()
    out = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            out.append(value)
    return out


def _second_pass_config(config: dict, accept_threshold: int) -> dict:
    out = dict(config)
    try:
        requested = int(os.getenv("FINDER_SECOND_PASS_MAX_FETCH_CANDIDATES", "6"))
    except ValueError:
        requested = 6
    current = int(out.get("max_fetch_candidates", 0) or 0)
    out["max_fetch_candidates"] = max(current, requested)
    out["auto_match_threshold"] = accept_threshold
    return out


def _rescue_config(config: dict) -> dict:
    out = dict(config)
    try:
        requested = int(os.getenv("FINDER_SECOND_PASS_RESCUE_FETCH_CANDIDATES", "10"))
    except ValueError:
        requested = 10
    out["max_fetch_candidates"] = max(int(out.get("max_fetch_candidates", 0) or 0), requested)
    return out


def _dedupe_candidates(candidates: list[SearchCandidate]) -> list[SearchCandidate]:
    seen: set[tuple[str, str]] = set()
    out = []
    for candidate in candidates:
        if not candidate.url:
            continue
        domain = domain_from_url(candidate.url)
        if not domain:
            continue
        key = (domain, candidate.url.rstrip("/"))
        if key in seen:
            continue
        seen.add(key)
        out.append(candidate)
    return out


def _accepted(result: dict, config: dict, accept_threshold: int) -> bool:
    url = result.get("official_url", "")
    if not url or is_excluded_domain(url, config) or _risky_auto_accept_url(url):
        return False
    if result.get("status") not in {"matched", "needs_review"}:
        return False
    try:
        confidence = int(result.get("confidence") or 0)
    except (TypeError, ValueError):
        return False
    if result.get("status") == "matched" and confidence >= 85:
        return True
    if result.get("status") == "matched" and confidence >= accept_threshold:
        return _has_strong_second_pass_reason(result)
    return _has_verified_second_pass_reason(result, min_score=50)


def _has_strong_second_pass_reason(result: dict) -> bool:
    strong_reasons = {
        "page_contains_exact_provider_name",
        "page_mentions_amazon_spn",
        "page_contains_amazon_service_keywords",
        "listing_logo_visual_match",
    }
    summary = set(str(result.get("evidence_summary", "")).split("; "))
    if summary & strong_reasons:
        return True
    official_domain = domain_from_url(result.get("official_url", ""))
    supporting_sources = set()
    for candidate in result.get("candidates", []):
        if domain_from_url(candidate.get("url", "")) != official_domain:
            continue
        reasons = set(candidate.get("reasons") or [])
        if reasons & strong_reasons:
            return True
        source = candidate.get("source", "")
        if source and source not in {"domain_guess", "second_pass_domain_variant"}:
            supporting_sources.add(source)
    return len(supporting_sources) >= 2


def _has_verified_second_pass_reason(result: dict, *, min_score: int) -> bool:
    try:
        confidence = int(result.get("confidence") or 0)
    except (TypeError, ValueError):
        return False
    if confidence < min_score:
        return False
    official_domain = domain_from_url(result.get("official_url", ""))
    if not official_domain:
        return False
    for candidate in result.get("candidates", []):
        if candidate.get("reject"):
            continue
        if domain_from_url(candidate.get("url", "")) != official_domain:
            continue
        reasons = set(candidate.get("reasons") or [])
        source = candidate.get("source", "")
        page_ok = bool(reasons & {"http_ok_home", "http_ok_supporting_page"})
        domain_identity = bool(
            reasons
            & {
                "domain_exact_provider_slug",
                "domain_contains_provider_slug",
                "domain_fuzzy_provider_match",
            }
        ) or any(reason.startswith("domain_token_match:") for reason in reasons)
        page_identity = bool(
            reasons
            & {
                "page_contains_exact_provider_name",
                "page_contains_provider_name_tokens",
                "page_fuzzy_provider_name_match",
                "listing_logo_visual_match",
            }
        )
        search_identity = bool(
            reasons
            & {
                "search_result_contains_exact_name",
                "search_result_contains_name_tokens",
                "search_result_fuzzy_name_match",
            }
        )
        service_identity = bool(
            reasons
            & {
                "page_contains_amazon_service_keywords",
                "page_mentions_amazon_spn",
                "search_snippet_contains_amazon_service_keywords",
            }
        )
        official_query = "official_website_query_hit" in reasons
        top_result = "top_search_result" in reasons
        search_source = _is_search_evidence_source(source)

        if page_ok and domain_identity and (page_identity or service_identity or (search_identity and official_query)):
            return True
        if page_ok and service_identity and (page_identity or search_identity) and domain_identity:
            return True
        if search_source and domain_identity and search_identity and official_query and (top_result or service_identity):
            return True
    return False


def _is_search_evidence_source(source: str) -> bool:
    if source.endswith("_snippet_url"):
        source = source[: -len("_snippet_url")]
    return source in {"brave", "exa", "serpapi", "serper", "tavily", "firecrawl", "ddgs"}


def _risky_auto_accept_url(url: str) -> bool:
    parsed = urlparse(url if "://" in url else f"https://{url}")
    domain = domain_from_url(url)
    path = (parsed.path or "").casefold()
    query = (parsed.query or "").casefold()
    risky_domains = {
        "hugedomains.com",
        "atom.com",
        "all.biz",
        "inc.com",
        "itch.io",
        "indiamart.com",
        "recaptcha.cloud",
    }
    if domain in risky_domains or any(domain.endswith(f".{item}") for item in risky_domains):
        return True
    risky_host_markers = [
        "zendesk.",
        "hubspotpagebuilder.",
        "myshopify.com",
        "app.",
        "apps.",
        "staging.",
        "develop.",
        "onboarding.",
        "refund.",
    ]
    if any(marker in domain for marker in risky_host_markers):
        return True
    risky_path_markers = [
        "domain_profile",
        "/name/",
        "suspendedpage",
        "/login",
        "/password",
        "/signin",
        "/sign-in",
    ]
    return any(marker in path or marker in query for marker in risky_path_markers)


def _result_row(
    provider: dict,
    plan_row: dict[str, str],
    result: dict,
    candidates: list[SearchCandidate],
    accepted: bool,
    *,
    rescue_used: bool = False,
) -> dict[str, str]:
    return {
        "provider_id": plan_row.get("provider_id", ""),
        "provider_name": plan_row.get("provider_name", ""),
        "provider_detail_url": plan_row.get("provider_detail_url", ""),
        "strategy_tier": plan_row.get("strategy_tier", ""),
        "previous_status": plan_row.get("status", ""),
        "previous_confidence": plan_row.get("confidence", ""),
        "previous_top_candidate_url": plan_row.get("top_candidate_url", ""),
        "official_url": result.get("official_url", ""),
        "official_domain": result.get("official_domain", ""),
        "confidence": str(result.get("confidence", "")),
        "status": result.get("status", ""),
        "accepted_for_final": str(bool(accepted)).lower(),
        "evidence_summary": result.get("evidence_summary", ""),
        "candidate_count": str(len(candidates)),
        "scored_candidate_count": str(len(result.get("candidates", []))),
        "service_apis": json.dumps(provider.get("service_apis", []), ensure_ascii=False),
        "provider_locations": json.dumps(provider.get("provider_locations", []), ensure_ascii=False),
        "notes": _result_note(accepted, rescue_used),
    }


def _result_note(accepted: bool, rescue_used: bool) -> str:
    if accepted and rescue_used:
        return "second_pass_rescue_verify_accept"
    if accepted:
        return "second_pass_auto_accept"
    return "second_pass_review_required"


def _review_decision_row(row: dict[str, str]) -> dict[str, str]:
    return {
        "provider_id": row.get("provider_id", ""),
        "provider_name": row.get("provider_name", ""),
        "manual_decision": "replace",
        "manual_url": row.get("official_url", ""),
        "notes": f"{row.get('notes', 'second_pass_auto_accept')} confidence={row.get('confidence', '')} tier={row.get('strategy_tier', '')}",
        "confidence": row.get("confidence", ""),
        "source_status": row.get("status", ""),
        "evidence_summary": row.get("evidence_summary", ""),
        "candidate_count": row.get("candidate_count", ""),
        "scored_candidate_count": row.get("scored_candidate_count", ""),
        "service_apis": row.get("service_apis", ""),
        "provider_locations": row.get("provider_locations", ""),
    }


def _open_writer(path: Path, fields: list[str], *, append: bool) -> dict:
    path.parent.mkdir(parents=True, exist_ok=True)
    file = path.open("a" if append else "w", newline="", encoding="utf-8")
    writer = csv.DictWriter(file, fieldnames=fields)
    if not append:
        writer.writeheader()
    return {"file": file, "writer": writer}


def _write_rows(path: Path, rows: list[dict[str, str]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _read_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _done_provider_ids(path: Path) -> set[str]:
    return {row.get("provider_id", "") for row in _read_rows(path) if row.get("provider_id", "")}


def _plan_fields(rows: list[dict[str, str]]) -> list[str]:
    fields = []
    for row in rows:
        for key in row.keys():
            if key not in fields:
                fields.append(key)
    return fields or ["provider_id"]


def _update_manifest(path: Path, summary: dict) -> None:
    if not path.exists():
        return
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["second_pass"] = summary
    manifest.setdefault("summary", {}).update(
        {
            "second_pass_processed_rows": summary["processed_rows"],
            "second_pass_accepted_rows": summary["accepted_rows"],
            "second_pass_quality_passed": summary["quality_overall"]["passed"],
            "second_pass_unresolved_rows": summary["finalize"]["unresolved_rows"],
        }
    )
    manifest.setdefault("outputs", {}).update(
        {
            "second_pass_plan": summary["outputs"]["plan"],
            "second_pass_results": summary["outputs"]["results"],
            "second_pass_evidence": summary["outputs"]["evidence"],
            "second_pass_review_decisions": summary["outputs"]["review_decisions"],
            "second_pass_final": summary["outputs"]["final"],
            "second_pass_unresolved": summary["outputs"]["unresolved"],
            "second_pass_quality_md": summary["outputs"]["quality_md"],
            "second_pass_quality_json": summary["outputs"]["quality_json"],
            "second_pass_xlsx": summary["outputs"]["xlsx"],
        }
    )
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
