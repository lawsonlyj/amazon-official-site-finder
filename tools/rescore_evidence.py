from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from finder.input_normalizer import read_normalized_csv
from finder.scoring import choose_best, is_excluded_domain, load_config, _summary_reasons
from finder.search_sources import SearchCandidate
from finder.text import domain_from_url


OUTPUT_FIELDS = [
    "provider_id",
    "provider_name",
    "official_url",
    "official_domain",
    "confidence",
    "status",
    "evidence_summary",
    "candidate_count",
    "scored_candidate_count",
    "service_apis",
    "provider_locations",
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Rescore saved evidence JSONL after scoring/config changes.")
    parser.add_argument("--providers", required=True, help="Normalized provider CSV.")
    parser.add_argument("--evidence", required=True, help="Evidence JSONL from finder.cli run.")
    parser.add_argument("--output", required=True)
    parser.add_argument("--config", default="config/scoring.json")
    args = parser.parse_args(argv)

    providers = read_normalized_csv(args.providers)
    summary = rescore_evidence(args.evidence, providers, args.output, load_config(args.config))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def rescore_evidence(evidence_jsonl: str | Path, providers: list[dict], output_csv: str | Path, config: dict) -> dict:
    provider_index = _provider_index(providers)
    evidence_rows = _read_evidence(evidence_jsonl)
    output = Path(output_csv)
    output.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    missing_providers = 0
    with output.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_FIELDS)
        writer.writeheader()
        for evidence in evidence_rows:
            provider = _find_provider(evidence, provider_index)
            if not provider:
                missing_providers += 1
                continue
            scored_candidates = evidence.get("candidates", [])
            result = _choose_best_from_saved_scores(scored_candidates, config)
            if result is None:
                candidates = [_candidate_from_scored(item) for item in scored_candidates]
                result = choose_best(provider, candidates, config)
            writer.writerow(
                {
                    "provider_id": provider.get("provider_id", ""),
                    "provider_name": provider.get("provider_name", ""),
                    "official_url": result["official_url"],
                    "official_domain": result["official_domain"],
                    "confidence": result["confidence"],
                    "status": result["status"],
                    "evidence_summary": result["evidence_summary"],
                    "candidate_count": evidence.get("candidate_count", len(scored_candidates)),
                    "scored_candidate_count": evidence.get("scored_candidate_count", len(scored_candidates)),
                    "service_apis": json.dumps(provider.get("service_apis", []), ensure_ascii=False),
                    "provider_locations": json.dumps(provider.get("provider_locations", []), ensure_ascii=False),
                }
            )
            written += 1
    return {
        "evidence_rows": len(evidence_rows),
        "written_rows": written,
        "missing_providers": missing_providers,
        "output_csv": str(output_csv),
    }


def _choose_best_from_saved_scores(scored_candidates: list[dict], config: dict) -> dict | None:
    if not scored_candidates or any("score" not in item for item in scored_candidates):
        return None

    rescored = [_normalize_saved_score(item, config) for item in scored_candidates]
    viable = [item for item in rescored if not item.get("reject")]
    viable.sort(key=lambda x: _to_int(x.get("score")), reverse=True)
    best = viable[0] if viable else None
    if not best:
        return {
            "official_url": "",
            "official_domain": "",
            "confidence": 0,
            "status": "not_found",
            "evidence_summary": "No non-excluded candidate domains found.",
            "candidates": rescored,
        }

    confidence = max(0, min(100, _to_int(best.get("score"))))
    if confidence >= config.get("auto_match_threshold", 75):
        status = "matched"
    elif confidence >= config.get("review_threshold", 45):
        status = "needs_review"
    else:
        status = "low_confidence"

    url = str(best.get("url") or "")
    domain = domain_from_url(best.get("domain") or url)
    return {
        "official_url": url if status != "low_confidence" else "",
        "official_domain": domain if status != "low_confidence" else "",
        "confidence": confidence,
        "status": status,
        "evidence_summary": "; ".join(_summary_reasons(best.get("reasons") or [])),
        "candidates": rescored[: config.get("max_candidates_per_provider", 60)],
    }


def _normalize_saved_score(item: dict, config: dict) -> dict:
    out = dict(item)
    url = str(out.get("url") or "")
    domain = domain_from_url(out.get("domain") or url)
    out["domain"] = domain
    reasons = list(out.get("reasons") or [])
    if is_excluded_domain(url or domain, config) or is_excluded_domain(domain, config):
        out["score"] = -100
        out["reject"] = True
        if "excluded_domain" not in reasons:
            reasons.insert(0, "excluded_domain")
    out["reasons"] = reasons
    return out


def _provider_index(providers: list[dict]) -> dict[str, dict]:
    index = {}
    for provider in providers:
        if provider.get("provider_id"):
            index[f"id:{provider['provider_id']}"] = provider
        if provider.get("provider_name"):
            index[f"name:{provider['provider_name'].casefold()}"] = provider
    return index


def _find_provider(evidence: dict, index: dict[str, dict]) -> dict | None:
    provider_id = (evidence.get("provider_id") or "").strip()
    provider_name = (evidence.get("provider_name") or "").strip().casefold()
    return index.get(f"id:{provider_id}") or index.get(f"name:{provider_name}")


def _read_evidence(path: str | Path) -> list[dict]:
    rows = []
    with Path(path).open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def _candidate_from_scored(item: dict) -> SearchCandidate:
    return SearchCandidate(
        url=item.get("url", ""),
        title=item.get("title", ""),
        snippet=item.get("snippet", ""),
        source=item.get("source", "evidence"),
        query=item.get("query", ""),
        rank=_to_int(item.get("rank")),
        evidence_url=item.get("evidence_url", ""),
    )


def _to_int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
