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
    parser.add_argument(
        "--pattern-release-json",
        action="append",
        default=[],
        help="Optional output from tools/simulate_pattern_release.py. Repeatable.",
    )
    parser.add_argument("--output-json")
    parser.add_argument("--output-md")
    args = parser.parse_args(argv)

    report = build_balance_report(
        labeled_eval_json=args.labeled_eval_json,
        batch_review_csv=args.batch_review_csv,
        batch_agent_b_csv=args.batch_agent_b_csv,
        batch_total_rows=args.batch_total_rows,
        pattern_release_jsons=args.pattern_release_json,
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
    pattern_release_jsons: list[str | Path] | None = None,
    output_json: str | Path | None = None,
    output_md: str | Path | None = None,
) -> dict:
    labeled = json.loads(Path(labeled_eval_json).read_text(encoding="utf-8"))
    overall = labeled.get("overall", {})
    threshold_recommendation = _threshold_recommendation(labeled.get("threshold_simulations", []))
    recall_release = _recall_release_recommendation(labeled.get("agent_b_recall_release_simulations", []))
    pattern_release = _pattern_release_recommendation(pattern_release_jsons or [])
    lane_policy = _manual_review_lane_policy(
        labeled.get("manual_review_lanes", []),
        labeled.get("manual_review_lane_drop_simulations", []),
    )
    batch_review = _review_summary(Path(batch_review_csv), batch_total_rows) if batch_review_csv else {}
    batch_agent_b = _agent_b_summary(Path(batch_agent_b_csv)) if batch_agent_b_csv else {}
    recommendations = _recommendations(
        overall,
        threshold_recommendation,
        recall_release,
        pattern_release,
        lane_policy,
        batch_review,
        batch_agent_b,
    )
    report = {
        "summary": {
            "recommended_threshold": threshold_recommendation.get("recommended_threshold"),
            "recommended_threshold_reason": threshold_recommendation.get("reason", ""),
            "recommended_agent_b_recall_release": recall_release.get("recommendation"),
            "recommended_agent_b_recall_release_threshold": recall_release.get("threshold"),
            "agent_b_recall_release_correct_rows": recall_release.get("correct_recovery_rows"),
            "agent_b_recall_release_wrong_rows": recall_release.get("wrong_release_rows"),
            "recommended_pattern_release": pattern_release.get("recommendation"),
            "pattern_release_pattern_count": pattern_release.get("pattern_count"),
            "pattern_release_correct_rows": pattern_release.get("correct_recovery_rows"),
            "pattern_release_wrong_rows": pattern_release.get("wrong_release_rows"),
            "pattern_release_accuracy": pattern_release.get("overall_accuracy"),
            "pattern_release_auto_precision": pattern_release.get("auto_precision"),
            "pattern_release_official_recall": pattern_release.get("official_recall"),
            "labeled_rows": overall.get("labeled_rows"),
            "auto_precision": overall.get("auto_precision"),
            "official_recall": overall.get("official_recall"),
            "false_official_rows": overall.get("false_official_rows"),
            "over_rejected_rows": overall.get("over_rejected_rows"),
            "manual_review_rows": overall.get("manual_review_rows"),
            "manual_review_false_official_capture_rate": overall.get("manual_review_false_official_capture_rate"),
            "agent_b_false_official_accept_rate": overall.get("agent_b_false_official_accept_rate"),
            "protected_review_lane_count": lane_policy.get("protected_lane_count"),
            "protected_review_lanes": lane_policy.get("protected_review_lanes"),
            "protected_review_lane_rows": lane_policy.get("protected_review_lane_rows"),
            "spot_check_candidate_lanes": lane_policy.get("spot_check_candidate_lanes"),
            "more_label_review_lanes": lane_policy.get("more_label_review_lanes"),
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
        "agent_b_recall_release": recall_release,
        "agent_b_recall_release_simulations": labeled.get("agent_b_recall_release_simulations", []),
        "pattern_release": pattern_release,
        "manual_review_lane_policy": lane_policy,
        "manual_review_lanes": labeled.get("manual_review_lanes", []),
        "manual_review_lane_drop_simulations": labeled.get("manual_review_lane_drop_simulations", []),
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


def _recall_release_recommendation(simulations: list[dict]) -> dict:
    if not simulations:
        return {"recommendation": "not_evaluated", "reason": "No AgentB recall release simulation data."}
    zero_wrong = [
        row
        for row in simulations
        if int(row.get("wrong_release_rows") or 0) == 0 and int(row.get("correct_recovery_rows") or 0) > 0
    ]
    if zero_wrong:
        chosen = max(
            zero_wrong,
            key=lambda row: (int(row.get("correct_recovery_rows") or 0), -int(row.get("agent_b_evidence_threshold") or 0)),
        )
        return {
            "recommendation": "narrow_auto_release_candidate",
            "threshold": chosen.get("agent_b_evidence_threshold"),
            "correct_recovery_rows": chosen.get("correct_recovery_rows"),
            "wrong_release_rows": chosen.get("wrong_release_rows"),
            "reason": "A simulated AgentB evidence threshold recovers labeled official sites without releasing labeled wrong candidates.",
            "chosen": chosen,
        }
    best = max(
        simulations,
        key=lambda row: (
            float(row.get("release_precision") or 0),
            int(row.get("correct_recovery_rows") or 0),
            -int(row.get("wrong_release_rows") or 0),
        ),
    )
    return {
        "recommendation": "manual_only",
        "threshold": best.get("agent_b_evidence_threshold"),
        "correct_recovery_rows": best.get("correct_recovery_rows"),
        "wrong_release_rows": best.get("wrong_release_rows"),
        "reason": "Every simulated AgentB recall-release threshold releases at least one labeled wrong candidate; keep recall candidates manual-only.",
        "chosen": best,
    }


def _pattern_release_recommendation(paths: list[str | Path]) -> dict:
    candidates = []
    for path_value in paths:
        path = Path(path_value)
        if not path.exists():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        summary = data.get("summary", {})
        selected = data.get("selected_actionable_release_summary") or {}
        selected_overall = selected.get("simulated_overall") or {}
        correct = _to_int(selected.get("correct_recovery_rows") or summary.get("selected_actionable_correct_recovery_rows"))
        wrong = _to_int(selected.get("wrong_release_rows") or summary.get("selected_actionable_wrong_release_rows"))
        pattern_count = _to_int(selected.get("pattern_count") or summary.get("selected_actionable_pattern_count"))
        if not pattern_count:
            pattern_count = len(data.get("selected_actionable_pattern_set") or [])
        baseline_accuracy = summary.get("baseline_overall_accuracy")
        overall_accuracy = selected_overall.get("overall_accuracy") or summary.get("selected_actionable_accuracy")
        candidate = {
            "path": str(path),
            "recommendation": "not_recommended",
            "reason": "No selected actionable pattern set was available.",
            "pattern_count": pattern_count,
            "correct_recovery_rows": correct,
            "wrong_release_rows": wrong,
            "baseline_accuracy": baseline_accuracy,
            "overall_accuracy": overall_accuracy,
            "accuracy_delta": _delta(overall_accuracy, baseline_accuracy),
            "auto_precision": selected_overall.get("auto_precision") or summary.get("selected_actionable_auto_precision"),
            "official_recall": selected_overall.get("official_recall") or summary.get("selected_actionable_official_recall"),
            "released_correct_provider_ids": selected.get("released_correct_provider_ids", []),
            "released_wrong_provider_ids": selected.get("released_wrong_provider_ids", []),
            "patterns": [
                {
                    "pattern": item.get("pattern", ""),
                    "features": item.get("features", []),
                    "correct_recovery_rows": item.get("correct_recovery_rows"),
                    "wrong_release_rows": item.get("wrong_release_rows"),
                }
                for item in data.get("selected_actionable_pattern_set", [])
            ],
        }
        if pattern_count and correct > 0 and wrong == 0:
            candidate["recommendation"] = "narrow_pattern_release_candidate"
            candidate["reason"] = (
                "Selected actionable evidence patterns recover labeled over-rejected official sites without releasing labeled wrong candidates."
            )
        elif pattern_count and wrong > 0:
            candidate["reason"] = "Selected actionable evidence patterns would release labeled wrong candidates."
        elif pattern_count:
            candidate["reason"] = "Selected actionable evidence patterns did not recover labeled official sites."
        candidates.append(candidate)
    if not candidates:
        return {"recommendation": "not_evaluated", "reason": "No pattern release simulation data."}
    candidates.sort(
        key=lambda row: (
            row.get("recommendation") != "narrow_pattern_release_candidate",
            row.get("wrong_release_rows", 0),
            -row.get("correct_recovery_rows", 0),
            -(float(row.get("overall_accuracy") or 0)),
        )
    )
    chosen = candidates[0]
    return {**chosen, "candidates": candidates}


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


def _manual_review_lane_policy(lanes: list[dict], drop_simulations: list[dict]) -> dict:
    drops = {row.get("drop_review_reason", ""): row for row in drop_simulations}
    protected = []
    spot_check_candidates = []
    more_labels = []
    for lane in lanes:
        reason = lane.get("review_reason", "")
        if not reason:
            continue
        drop = drops.get(reason, {})
        false_missed = _to_int(drop.get("known_false_official_missed_if_dropped") or lane.get("false_official_rows"))
        over_rejected_missed = _to_int(
            drop.get("known_over_rejected_missed_if_dropped") or lane.get("over_rejected_rows")
        )
        labeled_rows = _to_int(lane.get("labeled_rows"))
        review_task_rows = _to_int(lane.get("review_task_rows"))
        row = {
            "review_reason": reason,
            "review_task_rows": review_task_rows,
            "labeled_rows": labeled_rows,
            "false_official_rows": _to_int(lane.get("false_official_rows")),
            "over_rejected_rows": _to_int(lane.get("over_rejected_rows")),
            "risk_rows": _to_int(lane.get("risk_rows")),
            "risk_share_of_labeled_lane": lane.get("risk_share_of_labeled_lane"),
            "known_false_official_missed_if_dropped": false_missed,
            "known_over_rejected_missed_if_dropped": over_rejected_missed,
            "known_correct_reviews_removed_if_dropped": _to_int(
                drop.get("known_correct_reviews_removed_if_dropped")
            ),
        }
        if false_missed or over_rejected_missed:
            reasons = []
            if false_missed:
                reasons.append(f"would miss {false_missed} labeled false official row(s)")
            if over_rejected_missed:
                reasons.append(f"would miss {over_rejected_missed} labeled over-rejected official row(s)")
            protected.append({**row, "protection_reason": "; ".join(reasons)})
        elif labeled_rows >= 3 and row["known_correct_reviews_removed_if_dropped"] >= labeled_rows:
            spot_check_candidates.append(
                {**row, "candidate_reason": "labeled rows are clean so far; keep as sampled spot-check before removing"}
            )
        else:
            more_labels.append({**row, "candidate_reason": "not enough labeled evidence to remove or keep permanently"})

    protected.sort(key=lambda row: (-row["risk_rows"], -row["review_task_rows"], row["review_reason"]))
    spot_check_candidates.sort(key=lambda row: (-row["review_task_rows"], row["review_reason"]))
    more_labels.sort(key=lambda row: (-row["review_task_rows"], row["review_reason"]))
    return {
        "protected_lane_count": len(protected),
        "protected_review_lanes": [row["review_reason"] for row in protected],
        "protected_review_lane_rows": sum(row["review_task_rows"] for row in protected),
        "spot_check_candidate_lanes": [row["review_reason"] for row in spot_check_candidates],
        "more_label_review_lanes": [row["review_reason"] for row in more_labels],
        "protected": protected,
        "spot_check_candidates": spot_check_candidates,
        "needs_more_labels": more_labels,
    }


def _recommendations(
    overall: dict,
    threshold: dict,
    recall_release: dict,
    pattern_release: dict,
    lane_policy: dict,
    batch_review: dict,
    batch_agent_b: dict,
) -> list[str]:
    out = []
    recommended = threshold.get("recommended_threshold")
    if recommended is not None:
        out.append(f"Keep auto-accept threshold at {recommended}; do not globally tighten unless new labels change the tie.")
    if overall.get("manual_review_false_official_capture_rate") == 1.0:
        out.append("Keep current high-risk review lanes; labeled false official rows are fully captured.")
    protected_lanes = lane_policy.get("protected") or []
    if protected_lanes:
        names = ", ".join(row["review_reason"] for row in protected_lanes[:5])
        suffix = "" if len(protected_lanes) <= 5 else f", plus {len(protected_lanes) - 5} more"
        out.append(
            "Do not remove protected review lanes yet: "
            f"{names}{suffix}. Dropping them would miss labeled false official or over-rejected rows."
        )
    spot_check_lanes = lane_policy.get("spot_check_candidates") or []
    if spot_check_lanes:
        names = ", ".join(row["review_reason"] for row in spot_check_lanes)
        out.append(f"Treat clean lanes as spot-check candidates, not automatic removals yet: {names}.")
    if lane_policy.get("needs_more_labels"):
        names = ", ".join(row["review_reason"] for row in lane_policy["needs_more_labels"])
        out.append(f"Collect more labels before changing low-evidence review lanes: {names}.")
    if overall.get("agent_b_false_official_accept_rate") == 0.0:
        out.append("Keep AgentB conservative on high-risk rows; it is not releasing labeled false official rows.")
    if recall_release.get("recommendation") == "manual_only":
        out.append("Keep AgentB unresolved recall candidates manual-only; simulated auto-release would add labeled wrong official URLs.")
    elif recall_release.get("recommendation") == "narrow_auto_release_candidate":
        out.append(
            f"Consider a narrow AgentB recall release at evidence threshold {recall_release.get('threshold')} after adding regression tests."
        )
    if pattern_release.get("recommendation") == "narrow_pattern_release_candidate":
        out.append(
            "Prefer narrow pattern release over global threshold relaxation: "
            f"{pattern_release.get('pattern_count')} actionable pattern(s) recover "
            f"{pattern_release.get('correct_recovery_rows')} labeled over-rejected row(s) with "
            f"{pattern_release.get('wrong_release_rows')} labeled wrong release(s)."
        )
    elif pattern_release.get("recommendation") == "not_recommended":
        out.append("Do not release unresolved candidates by evidence pattern yet; the selected pattern set is not clean enough.")
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
            f"- Protected review lanes: {summary.get('protected_review_lane_count')}",
            f"- Protected review lane rows: {summary.get('protected_review_lane_rows')}",
            "",
            "## Thresholds",
            "",
            f"- Recommended threshold: {summary.get('recommended_threshold')}",
            f"- Reason: {summary.get('recommended_threshold_reason')}",
            f"- AgentB recall release: {summary.get('recommended_agent_b_recall_release')}",
            f"- AgentB recall release threshold: {summary.get('recommended_agent_b_recall_release_threshold')}",
            f"- AgentB recall release correct/wrong rows: {summary.get('agent_b_recall_release_correct_rows')}/{summary.get('agent_b_recall_release_wrong_rows')}",
            f"- Pattern release: {summary.get('recommended_pattern_release')}",
            f"- Pattern release correct/wrong rows: {summary.get('pattern_release_correct_rows')}/{summary.get('pattern_release_wrong_rows')}",
            f"- Pattern release accuracy: {summary.get('pattern_release_accuracy')}",
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
    lane_policy = report.get("manual_review_lane_policy", {})
    if lane_policy.get("protected"):
        lines.extend(["", "### Protected Lanes", ""])
        lines.append("| Review reason | Rows | Labeled | Risk | Why protected |")
        lines.append("| --- | ---: | ---: | ---: | --- |")
        for row in lane_policy["protected"]:
            lines.append(
                "| {reason} | {rows} | {labeled} | {risk} | {why} |".format(
                    reason=row.get("review_reason"),
                    rows=row.get("review_task_rows"),
                    labeled=row.get("labeled_rows"),
                    risk=row.get("risk_rows"),
                    why=row.get("protection_reason"),
                )
            )
    if lane_policy.get("spot_check_candidates"):
        lines.extend(["", "### Spot-Check Candidates", ""])
        for row in lane_policy["spot_check_candidates"]:
            lines.append(
                "- {reason}: rows={rows}, labeled={labeled}, clean so far; keep sampled until more labels confirm.".format(
                    reason=row.get("review_reason"),
                    rows=row.get("review_task_rows"),
                    labeled=row.get("labeled_rows"),
                )
            )
    if lane_policy.get("needs_more_labels"):
        lines.extend(["", "### Needs More Labels", ""])
        for row in lane_policy["needs_more_labels"]:
            lines.append(
                "- {reason}: rows={rows}, labeled={labeled}".format(
                    reason=row.get("review_reason"),
                    rows=row.get("review_task_rows"),
                    labeled=row.get("labeled_rows"),
                )
            )
    lines.extend(["", "## AgentB Decisions", ""])
    for reason, counts in report.get("batch_agent_b", {}).get("reason_decision_counts", {}).items():
        parts = ", ".join(f"{key}={value}" for key, value in counts.items())
        lines.append(f"- {reason}: {parts}")
    if report.get("agent_b_recall_release_simulations"):
        lines.extend(["", "## AgentB Recall Release Simulation", ""])
        for row in report["agent_b_recall_release_simulations"]:
            lines.append(
                "- threshold {threshold}: release={release}, correct={correct}, wrong={wrong}, precision={precision}".format(
                    threshold=row.get("agent_b_evidence_threshold"),
                    release=row.get("release_rows"),
                    correct=row.get("correct_recovery_rows"),
                    wrong=row.get("wrong_release_rows"),
                    precision=row.get("release_precision"),
                )
            )
    lines.append("")
    pattern_release = report.get("pattern_release", {})
    if pattern_release.get("patterns"):
        lines.extend(["", "## Pattern Release", ""])
        lines.append(f"- Recommendation: {pattern_release.get('recommendation')}")
        lines.append(f"- Reason: {pattern_release.get('reason')}")
        lines.append(f"- Accuracy delta: {pattern_release.get('accuracy_delta')}")
        for row in pattern_release.get("patterns", []):
            lines.append(
                "- correct={correct}, wrong={wrong}: {pattern}".format(
                    correct=row.get("correct_recovery_rows"),
                    wrong=row.get("wrong_release_rows"),
                    pattern=row.get("pattern"),
                )
            )
    return "\n".join(lines)


def _read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _ratio(num: int, den: int) -> float | None:
    return round(num / den, 4) if den else None


def _to_int(value: object) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def _delta(value: object, baseline: object) -> float | None:
    if value is None or baseline is None:
        return None
    try:
        return round(float(value) - float(baseline), 4)
    except (TypeError, ValueError):
        return None


if __name__ == "__main__":
    raise SystemExit(main())
