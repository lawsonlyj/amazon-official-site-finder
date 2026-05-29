from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.build_linked_workbook import build_workbook


SAMPLE_FIELDS = [
    "sample_priority",
    "sample_reason",
    "review_reason",
    "agent_b_decision",
    "agent_b_confidence",
    "reason_for_unsure",
    "provider_id",
    "provider_name",
    "provider_detail_url",
    "official_url",
    "official_domain",
    "candidate_url",
    "candidate_domain",
    "replacement_url",
    "replacement_domain",
    "status",
    "source_status",
    "source_confidence",
    "evidence_score",
    "supporting_facts",
    "counter_evidence",
    "evidence_urls",
    "evidence_summary",
    "service_apis",
    "provider_locations",
    "manual_decision",
    "manual_url",
    "notes",
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a high-value manual calibration sample from review and AgentB outputs.")
    parser.add_argument("--review-csv", required=True)
    parser.add_argument("--agent-b-csv", required=True)
    parser.add_argument("--output-csv", required=True)
    parser.add_argument("--output-xlsx")
    parser.add_argument("--max-rows", type=int, default=50)
    parser.add_argument("--max-per-reason", type=int, default=10)
    args = parser.parse_args(argv)

    summary = build_calibration_review_sample(
        review_csv=args.review_csv,
        agent_b_csv=args.agent_b_csv,
        output_csv=args.output_csv,
        output_xlsx=args.output_xlsx,
        max_rows=args.max_rows,
        max_per_reason=args.max_per_reason,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def build_calibration_review_sample(
    *,
    review_csv: str | Path,
    agent_b_csv: str | Path,
    output_csv: str | Path,
    output_xlsx: str | Path | None = None,
    max_rows: int = 50,
    max_per_reason: int = 10,
) -> dict:
    review_rows = _read_rows(Path(review_csv))
    agent_rows = {_row_key(row): row for row in _read_rows(Path(agent_b_csv)) if _row_key(row)}
    candidates = [_sample_row(row, agent_rows.get(_row_key(row), {})) for row in review_rows]
    candidates = sorted(candidates, key=_sort_key)
    selected = _select_balanced(candidates, max_rows=max_rows, max_per_reason=max_per_reason)
    _write_rows(Path(output_csv), selected, SAMPLE_FIELDS)
    xlsx_summary = {}
    if output_xlsx:
        xlsx_summary = build_workbook([("Calibration_Review", output_csv)], output_xlsx)
    return {
        "review_rows": len(review_rows),
        "agent_b_rows": len(agent_rows),
        "sample_rows": len(selected),
        "output_csv": str(output_csv),
        "output_xlsx": str(output_xlsx or ""),
        "reason_counts": dict(Counter(row["review_reason"] for row in selected)),
        "sample_reason_counts": dict(Counter(row["sample_reason"] for row in selected)),
        "agent_b_decision_counts": dict(Counter(row["agent_b_decision"] for row in selected)),
        "xlsx": xlsx_summary,
    }


def _sample_row(review_row: dict[str, str], agent_row: dict[str, str]) -> dict[str, str]:
    priority, sample_reason = _priority(review_row, agent_row)
    return {
        "sample_priority": str(priority),
        "sample_reason": sample_reason,
        "review_reason": review_row.get("review_reason", ""),
        "agent_b_decision": agent_row.get("agent_b_decision", ""),
        "agent_b_confidence": agent_row.get("confidence", ""),
        "reason_for_unsure": agent_row.get("reason_for_unsure", ""),
        "provider_id": review_row.get("provider_id", "") or agent_row.get("provider_id", ""),
        "provider_name": review_row.get("provider_name", "") or agent_row.get("provider_name", ""),
        "provider_detail_url": review_row.get("provider_detail_url", "") or agent_row.get("provider_detail_url", ""),
        "official_url": review_row.get("official_url", ""),
        "official_domain": review_row.get("official_domain", ""),
        "candidate_url": agent_row.get("candidate_url", "") or review_row.get("top_candidate_url", "") or review_row.get("official_url", ""),
        "candidate_domain": agent_row.get("candidate_domain", "") or review_row.get("top_candidate_domain", ""),
        "replacement_url": agent_row.get("replacement_url", ""),
        "replacement_domain": agent_row.get("replacement_domain", ""),
        "status": review_row.get("status", ""),
        "source_status": review_row.get("source_status", "") or agent_row.get("source_status", ""),
        "source_confidence": agent_row.get("source_confidence", "") or review_row.get("confidence", ""),
        "evidence_score": agent_row.get("evidence_score", ""),
        "supporting_facts": agent_row.get("supporting_facts", ""),
        "counter_evidence": agent_row.get("counter_evidence", ""),
        "evidence_urls": agent_row.get("evidence_urls", ""),
        "evidence_summary": review_row.get("evidence_summary", ""),
        "service_apis": review_row.get("service_apis", ""),
        "provider_locations": review_row.get("provider_locations", ""),
        "manual_decision": "",
        "manual_url": "",
        "notes": "",
    }


def _priority(review_row: dict[str, str], agent_row: dict[str, str]) -> tuple[int, str]:
    review_reason = review_row.get("review_reason", "")
    decision = agent_row.get("agent_b_decision", "")
    unsure = agent_row.get("reason_for_unsure", "")
    if unsure == "agent_b_row_timeout":
        return 100, "timeout_needs_manual"
    if decision == "reject":
        return 92, "agent_b_reject_check"
    if decision == "accept" and review_reason.startswith("precision_"):
        return 88, "agent_b_accept_risky_lane"
    if review_reason == "recall_unresolved_top_candidate":
        return 80, "recall_candidate_label"
    if decision == "unsure":
        return 70, "agent_b_unsure_label"
    if "slug_extension" in review_reason:
        return 68, "slug_extension_label"
    if "generic_identity" in review_reason:
        return 66, "generic_identity_label"
    if "second_pass_accepted" in review_reason:
        return 62, "second_pass_threshold_label"
    if "low_confidence" in review_reason:
        return 58, "low_confidence_label"
    return 40, "general_calibration"


def _select_balanced(rows: list[dict[str, str]], *, max_rows: int, max_per_reason: int) -> list[dict[str, str]]:
    selected: list[dict[str, str]] = []
    selected_keys: set[str] = set()
    reason_counts: Counter[str] = Counter()
    for row in rows:
        if len(selected) >= max_rows:
            break
        reason = row.get("review_reason", "")
        if reason_counts[reason] >= max_per_reason:
            continue
        selected.append(row)
        selected_keys.add(_row_key(row))
        reason_counts[reason] += 1
    if len(selected) < max_rows:
        for row in rows:
            if len(selected) >= max_rows:
                break
            key = _row_key(row)
            if key not in selected_keys:
                selected.append(row)
                selected_keys.add(key)
    return selected


def _sort_key(row: dict[str, str]) -> tuple[int, int, str, str]:
    try:
        confidence = int(float(row.get("agent_b_confidence") or row.get("source_confidence") or 0))
    except ValueError:
        confidence = 0
    return (-int(row.get("sample_priority") or 0), confidence, row.get("review_reason", ""), row.get("provider_name", ""))


def _row_key(row: dict[str, str]) -> str:
    provider_id = (row.get("provider_id") or "").strip()
    if provider_id:
        return f"id:{provider_id}"
    provider_name = (row.get("provider_name") or "").strip().casefold()
    return f"name:{provider_name}" if provider_name else ""


def _read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _write_rows(path: Path, rows: list[dict[str, str]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    raise SystemExit(main())
