from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a threshold/review-lane balance report from workflow metrics.")
    parser.add_argument("--labeled-eval-json", required=True)
    parser.add_argument("--batch-review-csv")
    parser.add_argument("--batch-agent-b-csv")
    parser.add_argument("--batch-total-rows", type=int, default=0)
    parser.add_argument("--output-json")
    parser.add_argument("--output-md")
    args = parser.parse_args(argv)

    report = build_balance_report(
        labeled_eval_json=args.labeled_eval_json,
        batch_review_csv=args.batch_review_csv,
        batch_agent_b_csv=args.batch_agent_b_csv,
        batch_total_rows=args.batch_total_rows,
        output_json=args.output_json,
        output_md=args.output_md,
    )
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 0


def build_balance_report(
    *,
    labeled_eval_json: str | Path,
    batch_review_csv: str | Path | None = None,
    batch_agent_b_csv: str | Path | None = None,
    batch_total_rows: int = 0,
    output_json: str | Path | None = None,
    output_md: str | Path | None = None,
) -> dict:
    labeled = json.loads(Path(labeled_eval_json).read_text(encoding="utf-8"))
    overall = labeled.get("overall", {})
    threshold_recommendation = _threshold_recommendation(labeled.get("threshold_simulations", []))
    batch_review = _review_summary(Path(batch_review_csv), batch_total_rows) if batch_review_csv else {}
    batch_agent_b = _agent_b_summary(Path(batch_agent_b_csv)) if batch_agent_b_csv else {}
    recommendations = _recommendations(overall, threshold_recommendation, batch_review, batch_agent_b)
    report = {
        "summary": {
            "recommended_threshold": threshold_recommendation.get("recommended_threshold"),
            "recommended_threshold_reason": threshold_recommendation.get("reason", ""),
            "labeled_rows": overall.get("labeled_rows"),
            "auto_precision": overall.get("auto_precision"),
            "official_recall": overall.get("official_recall"),
            "false_official_rows": overall.get("false_official_rows"),
            "over_rejected_rows": overall.get("over_rejected_rows"),
            "manual_review_rows": overall.get("manual_review_rows"),
            "manual_review_false_official_capture_rate": overall.get("manual_review_false_official_capture_rate"),
            "agent_b_false_official_accept_rate": overall.get("agent_b_false_official_accept_rate"),
            "batch_review_rows": batch_review.get("rows"),
            "batch_review_rate": batch_review.get("review_rate"),
            "batch_agent_b_rows": batch_agent_b.get("rows"),
            "batch_agent_b_unsure_rate": batch_agent_b.get("unsure_rate"),
            "batch_agent_b_timeout_rows": batch_agent_b.get("timeout_rows"),
        },
        "thresholds": {
            "recommendation": threshold_recommendation,
            "simulations": labeled.get("threshold_simulations", []),
        },
        "labeled_overall": overall,
        "batch_review": batch_review,
        "batch_agent_b": batch_agent_b,
        "recommendations": recommendations,
    }
    if output_json:
        path = Path(output_json)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if output_md:
        path = Path(output_md)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_render_markdown(report), encoding="utf-8")
    return report


def _threshold_recommendation(simulations: list[dict]) -> dict:
    if not simulations:
        return {"recommended_threshold": None, "reason": "No threshold simulation data."}
    current = simulations[0]
    max_accuracy = max(float(row.get("overall_accuracy") or 0) for row in simulations)
    best_accuracy = [row for row in simulations if float(row.get("overall_accuracy") or 0) == max_accuracy]
    chosen = max(best_accuracy, key=lambda row: (float(row.get("official_recall") or 0), -int(row.get("false_official_rows") or 0)))
    reason = (
        "Keeps the highest recall among thresholds tied for best labeled accuracy."
        if chosen.get("threshold") == current.get("threshold")
        else "Improves labeled accuracy without lower recall than other best-accuracy candidates."
    )
    return {
        "current_threshold": current.get("threshold"),
        "recommended_threshold": chosen.get("threshold"),
        "reason": reason,
        "current": current,
        "chosen": chosen,
    }


def _review_summary(path: Path, total_rows: int) -> dict:
    rows = _read_rows(path)
    reason_counts = Counter(row.get("review_reason", "") for row in rows)
    return {
        "path": str(path),
        "rows": len(rows),
        "total_rows": total_rows,
        "review_rate": _ratio(len(rows), total_rows),
        "reason_counts": dict(reason_counts),
    }


def _agent_b_summary(path: Path) -> dict:
    rows = _read_rows(path)
    decisions = Counter(row.get("agent_b_decision", "") for row in rows)
    reason_decisions: dict[str, Counter] = defaultdict(Counter)
    for row in rows:
        reason_decisions[row.get("review_reason", "")][row.get("agent_b_decision", "")] += 1
    timeout_rows = sum(1 for row in rows if row.get("reason_for_unsure") == "agent_b_row_timeout")
    return {
        "path": str(path),
        "rows": len(rows),
        "decision_counts": dict(decisions),
        "reason_decision_counts": {reason: dict(counts) for reason, counts in sorted(reason_decisions.items())},
        "timeout_rows": timeout_rows,
        "timeout_rate": _ratio(timeout_rows, len(rows)),
        "accept_rate": _ratio(decisions.get("accept", 0), len(rows)),
        "unsure_rate": _ratio(decisions.get("unsure", 0), len(rows)),
        "replace_rate": _ratio(decisions.get("replace", 0), len(rows)),
    }


def _recommendations(overall: dict, threshold: dict, batch_review: dict, batch_agent_b: dict) -> list[str]:
    out = []
    recommended = threshold.get("recommended_threshold")
    if recommended is not None:
        out.append(f"Keep auto-accept threshold at {recommended}; do not globally tighten unless new labels change the tie.")
    if overall.get("manual_review_false_official_capture_rate") == 1.0:
        out.append("Keep current high-risk review lanes; labeled false official rows are fully captured.")
    if overall.get("agent_b_false_official_accept_rate") == 0.0:
        out.append("Keep AgentB conservative on high-risk rows; it is not releasing labeled false official rows.")
    if batch_review.get("review_rate") and batch_review["review_rate"] > 0.4:
        out.append("Review workload is high on the batch sample; require more labels before widening review lanes.")
    if batch_agent_b.get("timeout_rate") and batch_agent_b["timeout_rate"] > 0.1:
        out.append("Use AgentB row timeout/resume for batch checks and treat timeout rows as manual-review priority.")
    if batch_agent_b.get("replace_rate") == 0:
        out.append("Keep replacement candidates as evidence-only for high-risk review tasks.")
    return out


def _render_markdown(report: dict) -> str:
    summary = report["summary"]
    lines = [
        "# Workflow Balance Report",
        "",
        "## Recommendation",
        "",
    ]
    for item in report["recommendations"]:
        lines.append(f"- {item}")
    lines.extend(
        [
            "",
            "## Labeled Calibration",
            "",
            f"- Labeled rows: {summary.get('labeled_rows')}",
            f"- Auto precision: {summary.get('auto_precision')}",
            f"- Official recall: {summary.get('official_recall')}",
            f"- False official rows: {summary.get('false_official_rows')}",
            f"- Over-rejected rows: {summary.get('over_rejected_rows')}",
            f"- Manual review rows: {summary.get('manual_review_rows')}",
            f"- Manual false-official capture rate: {summary.get('manual_review_false_official_capture_rate')}",
            f"- AgentB false-official accept rate: {summary.get('agent_b_false_official_accept_rate')}",
            "",
            "## Thresholds",
            "",
            f"- Recommended threshold: {summary.get('recommended_threshold')}",
            f"- Reason: {summary.get('recommended_threshold_reason')}",
            "",
            "## Batch Review",
            "",
            f"- Review rows: {summary.get('batch_review_rows')}",
            f"- Review rate: {summary.get('batch_review_rate')}",
            f"- AgentB rows: {summary.get('batch_agent_b_rows')}",
            f"- AgentB unsure rate: {summary.get('batch_agent_b_unsure_rate')}",
            f"- AgentB timeout rows: {summary.get('batch_agent_b_timeout_rows')}",
            "",
            "## Review Lanes",
            "",
        ]
    )
    for reason, count in report.get("batch_review", {}).get("reason_counts", {}).items():
        lines.append(f"- {reason}: {count}")
    lines.extend(["", "## AgentB Decisions", ""])
    for reason, counts in report.get("batch_agent_b", {}).get("reason_decision_counts", {}).items():
        parts = ", ".join(f"{key}={value}" for key, value in counts.items())
        lines.append(f"- {reason}: {parts}")
    lines.append("")
    return "\n".join(lines)


def _read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _ratio(num: int, den: int) -> float | None:
    return round(num / den, 4) if den else None


if __name__ == "__main__":
    raise SystemExit(main())
