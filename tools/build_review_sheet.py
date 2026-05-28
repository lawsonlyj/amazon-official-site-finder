from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from finder.scoring import is_excluded_domain, load_config
from finder.text import domain_from_url


REVIEW_STATUSES = {"needs_review", "low_confidence", "not_found", "unresolved", "rejected"}
BASE_FIELDS = [
    "provider_id",
    "provider_name",
    "provider_detail_url",
    "listing_logo_url",
    "review_priority",
    "suggested_action",
    "status",
    "confidence",
    "official_url",
    "official_domain",
    "evidence_summary",
    "candidate_count",
    "scored_candidate_count",
]
MANUAL_FIELDS = ["manual_decision", "manual_url", "notes"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build an enhanced manual review CSV from results and evidence JSONL.")
    parser.add_argument("--results", required=True)
    parser.add_argument("--evidence", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--config", default="config/scoring.json")
    parser.add_argument("--top-candidates", type=int, default=5)
    parser.add_argument("--include-matched", action="store_true")
    args = parser.parse_args(argv)

    rows = build_review_sheet(
        results_csv=args.results,
        evidence_jsonl=args.evidence,
        config=load_config(args.config),
        top_candidates=args.top_candidates,
        include_matched=args.include_matched,
    )
    write_review_sheet(rows, args.output, top_candidates=args.top_candidates)
    print(json.dumps({"review_rows": len(rows), "output_csv": args.output}, ensure_ascii=False, indent=2))
    return 0


def build_review_sheet(
    *,
    results_csv: str | Path,
    evidence_jsonl: str | Path,
    config: dict | None = None,
    top_candidates: int = 5,
    include_matched: bool = False,
) -> list[dict[str, str]]:
    config = config or load_config()
    results = _read_rows(results_csv)
    evidence = _read_evidence_index(evidence_jsonl)
    out = []
    for result in results:
        status = result.get("status", "")
        if not include_matched and status not in REVIEW_STATUSES:
            continue
        evidence_row = _evidence_for(result, evidence)
        candidates = _rank_candidates(evidence_row.get("candidates", []) if evidence_row else [], config)
        row = {
            "provider_id": result.get("provider_id", ""),
            "provider_name": result.get("provider_name", ""),
            "provider_detail_url": result.get("provider_detail_url", "") or result.get("detail_url", ""),
            "listing_logo_url": result.get("listing_logo_url", ""),
            "review_priority": _review_priority(result),
            "suggested_action": _suggested_action(result),
            "status": status,
            "confidence": result.get("confidence", ""),
            "official_url": result.get("official_url", ""),
            "official_domain": result.get("official_domain", ""),
            "evidence_summary": result.get("evidence_summary", ""),
            "candidate_count": result.get("candidate_count", evidence_row.get("candidate_count", "") if evidence_row else ""),
            "scored_candidate_count": result.get(
                "scored_candidate_count", evidence_row.get("scored_candidate_count", "") if evidence_row else ""
            ),
            "manual_decision": "",
            "manual_url": "",
            "notes": "",
        }
        for idx, candidate in enumerate(candidates[:top_candidates], 1):
            row.update(_candidate_columns(candidate, idx))
        out.append(row)
    return sorted(out, key=_review_sort_key)


def write_review_sheet(rows: list[dict[str, str]], output_csv: str | Path, *, top_candidates: int = 5) -> None:
    output = Path(output_csv)
    output.parent.mkdir(parents=True, exist_ok=True)
    fields = BASE_FIELDS + _candidate_fields(top_candidates) + MANUAL_FIELDS
    with output.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _read_rows(path: str | Path) -> list[dict[str, str]]:
    with Path(path).open(newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _read_evidence_index(path: str | Path) -> dict[str, dict[str, Any]]:
    index = {}
    with Path(path).open(encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            row = json.loads(line)
            provider_id = (row.get("provider_id") or "").strip()
            provider_name = (row.get("provider_name") or "").strip().casefold()
            if provider_id:
                index[f"id:{provider_id}"] = row
            if provider_name:
                index[f"name:{provider_name}"] = row
    return index


def _evidence_for(result: dict[str, str], evidence: dict[str, dict[str, Any]]) -> dict[str, Any]:
    provider_id = (result.get("provider_id") or "").strip()
    provider_name = (result.get("provider_name") or "").strip().casefold()
    return evidence.get(f"id:{provider_id}") or evidence.get(f"name:{provider_name}") or {}


def _rank_candidates(candidates: list[dict[str, Any]], config: dict) -> list[dict[str, Any]]:
    candidates = [_normalize_candidate_for_review(candidate, config) for candidate in candidates]
    return sorted(
        candidates,
        key=lambda item: (
            bool(item.get("reject")),
            -_to_int(item.get("score")),
            _to_int(item.get("rank")),
            item.get("domain", ""),
        ),
    )


def _normalize_candidate_for_review(candidate: dict[str, Any], config: dict) -> dict[str, Any]:
    out = dict(candidate)
    url = str(out.get("url") or "")
    domain = domain_from_url(out.get("domain") or url) if url or out.get("domain") else ""
    out["domain"] = domain
    reasons = list(out.get("reasons") or [])
    if domain and (is_excluded_domain(url, config) or is_excluded_domain(domain, config)):
        out["score"] = -100
        out["reject"] = True
        if "excluded_domain" not in reasons:
            reasons.insert(0, "excluded_domain")
    out["reasons"] = reasons
    return out


def _candidate_columns(candidate: dict[str, Any], idx: int) -> dict[str, str]:
    url = str(candidate.get("url") or "")
    domain = str(candidate.get("domain") or domain_from_url(url)) if url else ""
    reasons = candidate.get("reasons") or []
    return {
        f"candidate_{idx}_url": url,
        f"candidate_{idx}_domain": domain,
        f"candidate_{idx}_score": str(candidate.get("score", "")),
        f"candidate_{idx}_reject": str(bool(candidate.get("reject"))),
        f"candidate_{idx}_source": str(candidate.get("source") or ""),
        f"candidate_{idx}_rank": str(candidate.get("rank") or ""),
        f"candidate_{idx}_query": str(candidate.get("query") or ""),
        f"candidate_{idx}_reasons": "; ".join(str(reason) for reason in reasons),
        f"candidate_{idx}_page_title": str(candidate.get("page_title") or ""),
        f"candidate_{idx}_evidence_url": str(candidate.get("evidence_url") or ""),
    }


def _candidate_fields(top_candidates: int) -> list[str]:
    fields = []
    for idx in range(1, top_candidates + 1):
        fields.extend(
            [
                f"candidate_{idx}_url",
                f"candidate_{idx}_domain",
                f"candidate_{idx}_score",
                f"candidate_{idx}_reject",
                f"candidate_{idx}_source",
                f"candidate_{idx}_rank",
                f"candidate_{idx}_query",
                f"candidate_{idx}_reasons",
                f"candidate_{idx}_page_title",
                f"candidate_{idx}_evidence_url",
            ]
        )
    return fields


def _review_priority(result: dict[str, str]) -> str:
    summary = result.get("evidence_summary", "")
    status = result.get("status", "")
    if status in {"not_found", "low_confidence", "unresolved"}:
        return "high"
    if "javascript_page_requires_dynamic_review" in summary or "page_requires_javascript" in summary:
        return "high"
    if status == "needs_review":
        return "medium"
    return "low"


def _suggested_action(result: dict[str, str]) -> str:
    summary = result.get("evidence_summary", "")
    status = result.get("status", "")
    if "javascript_page_requires_dynamic_review" in summary or "page_requires_javascript" in summary:
        return "open_candidate_in_browser"
    if result.get("official_url") and status == "needs_review":
        return "verify_candidate_or_replace"
    if status in {"low_confidence", "not_found", "unresolved"}:
        return "manual_search_required"
    return "spot_check"


def _review_sort_key(row: dict[str, str]) -> tuple[int, int, str]:
    priority_rank = {"high": 0, "medium": 1, "low": 2}
    confidence = _to_int(row.get("confidence"))
    return (priority_rank.get(row.get("review_priority", ""), 9), confidence, row.get("provider_name", ""))


def _to_int(value) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
