from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


CASE_FIELDS = [
    "case_type",
    "provider_id",
    "provider_name",
    "review_reason",
    "lane_kind",
    "sample_reason",
    "pattern_scope",
    "pattern_match",
    "agent_b_decision",
    "official_url",
    "candidate_url",
    "expected_url",
    "manual_decision",
    "calibration_outcome",
    "assertion",
    "notes",
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export calibration labels as regression cases.")
    parser.add_argument("--sample-eval-json", required=True, help="Filled evaluation JSON from evaluate_calibration_review_sample.py.")
    parser.add_argument("--output-csv", required=True)
    parser.add_argument("--output-json")
    parser.add_argument("--output-md")
    args = parser.parse_args(argv)

    report = build_calibration_regression_cases(
        sample_eval_json=args.sample_eval_json,
        output_csv=args.output_csv,
        output_json=args.output_json,
        output_md=args.output_md,
    )
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 0


def build_calibration_regression_cases(
    *,
    sample_eval_json: str | Path,
    output_csv: str | Path,
    output_json: str | Path | None = None,
    output_md: str | Path | None = None,
) -> dict:
    sample_eval = _read_json(Path(sample_eval_json))
    cases = [_case(row) for row in sample_eval.get("details", [])]
    cases = [case for case in cases if case]
    summary = {
        "case_rows": len(cases),
        "case_type_counts": dict(Counter(case["case_type"] for case in cases)),
        "precision_blocking_fixture_rows": sum(1 for case in cases if case["case_type"] == "precision_blocking_fixture"),
        "recall_blocking_fixture_rows": sum(1 for case in cases if case["case_type"] == "recall_blocking_fixture"),
        "positive_fixture_rows": sum(1 for case in cases if case["case_type"].endswith("_positive_fixture")),
    }
    report = {
        "summary": summary,
        "cases": cases,
        "inputs": {"sample_eval_json": str(sample_eval_json)},
        "outputs": {
            "csv": str(output_csv),
            "json": str(output_json or ""),
            "md": str(output_md or ""),
        },
    }
    _write_rows(Path(output_csv), cases, CASE_FIELDS)
    if output_json:
        path = Path(output_json)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if output_md:
        path = Path(output_md)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_render_markdown(report), encoding="utf-8")
    return report


def _case(row: dict) -> dict[str, str]:
    if str(row.get("decision_quality_issue") or "").strip():
        return {}
    decision = str(row.get("normalized_decision") or "").strip()
    if not decision or decision == "unsure":
        return {}
    outcome = str(row.get("calibration_outcome") or "").strip()
    lane_kind = str(row.get("lane_kind") or "").strip()
    case_type = _case_type(lane_kind, outcome)
    if not case_type:
        return {}
    return {
        "case_type": case_type,
        "provider_id": str(row.get("provider_id") or ""),
        "provider_name": str(row.get("provider_name") or ""),
        "review_reason": str(row.get("review_reason") or ""),
        "lane_kind": lane_kind,
        "sample_reason": str(row.get("sample_reason") or ""),
        "pattern_scope": str(row.get("pattern_scope") or ""),
        "pattern_match": str(row.get("pattern_match") or ""),
        "agent_b_decision": str(row.get("agent_b_decision") or ""),
        "official_url": str(row.get("official_url") or ""),
        "candidate_url": str(row.get("candidate_url") or ""),
        "expected_url": _expected_url(row, outcome),
        "manual_decision": decision,
        "calibration_outcome": outcome,
        "assertion": _assertion(case_type),
        "notes": _notes(row, case_type),
    }


def _case_type(lane_kind: str, outcome: str) -> str:
    if lane_kind == "recall":
        if outcome == "recall_candidate_useful":
            return "recall_positive_fixture"
        if outcome == "recall_candidate_not_useful":
            return "recall_blocking_fixture"
    if outcome == "candidate_correct":
        return "precision_positive_fixture"
    if outcome == "candidate_incorrect":
        return "precision_blocking_fixture"
    return ""


def _expected_url(row: dict, outcome: str) -> str:
    replacement = str(row.get("normalized_manual_url") or "").strip()
    if replacement:
        return replacement
    if outcome in {"candidate_correct", "recall_candidate_useful"}:
        return str(row.get("official_url") or row.get("candidate_url") or "")
    return ""


def _assertion(case_type: str) -> str:
    if case_type == "precision_blocking_fixture":
        return "candidate_url_or_official_url_must_not_auto_accept"
    if case_type == "recall_blocking_fixture":
        return "candidate_must_not_auto_release_from_unresolved"
    if case_type == "recall_positive_fixture":
        return "candidate_can_seed_recall_pattern_only_with_same_evidence"
    if case_type == "precision_positive_fixture":
        return "candidate_should_remain_accepted_for_same_evidence_lane"
    return "manual_review_required"


def _notes(row: dict, case_type: str) -> str:
    pieces = [
        f"case_type={case_type}",
        f"review_reason={row.get('review_reason') or ''}",
        f"manual_decision={row.get('normalized_decision') or ''}",
    ]
    pattern = str(row.get("pattern_match") or "").strip()
    if pattern:
        pieces.append(f"pattern_match={pattern}")
    return "; ".join(pieces)


def _render_markdown(report: dict) -> str:
    summary = report["summary"]
    lines = [
        "# Calibration Regression Cases",
        "",
        "These rows are generated from decisive filled calibration labels. Use blocking cases before changing scoring, thresholds, or release routing.",
        "",
        "## Summary",
        "",
        f"- Case rows: {summary['case_rows']}",
        f"- Precision blocking fixtures: {summary['precision_blocking_fixture_rows']}",
        f"- Recall blocking fixtures: {summary['recall_blocking_fixture_rows']}",
        f"- Positive fixtures: {summary['positive_fixture_rows']}",
        "",
        "## Case Types",
        "",
    ]
    for key, value in sorted(summary["case_type_counts"].items()):
        lines.append(f"- {key}: {value}")
    lines.extend(["", "## Cases", ""])
    for case in report["cases"][:50]:
        lines.append(
            "- {case_type}: {provider_name} ({provider_id}) :: {review_reason} :: {assertion}".format(**case)
        )
    lines.append("")
    return "\n".join(lines)


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _write_rows(path: Path, rows: list[dict[str, str]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    raise SystemExit(main())
