from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a threshold/rule convergence audit from calibration outputs.")
    parser.add_argument("--status-json", required=True, help="calibration_status.json from run_calibration_cycle.py.")
    parser.add_argument("--labeled-balance-json", required=True, help="Labeled balance JSON with threshold simulations.")
    parser.add_argument("--protected-task-summary-json", help="Optional protected_lanes_next_review_task_summary.json.")
    parser.add_argument("--protected-priority-task-summary-json", help="Optional protected_lanes_priority_task_summary.json.")
    parser.add_argument("--output-json")
    parser.add_argument("--output-md")
    args = parser.parse_args(argv)

    report = build_convergence_audit(
        status_json=args.status_json,
        labeled_balance_json=args.labeled_balance_json,
        protected_task_summary_json=args.protected_task_summary_json,
        protected_priority_task_summary_json=args.protected_priority_task_summary_json,
        output_json=args.output_json,
        output_md=args.output_md,
    )
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 0


def build_convergence_audit(
    *,
    status_json: str | Path,
    labeled_balance_json: str | Path,
    protected_task_summary_json: str | Path | None = None,
    protected_priority_task_summary_json: str | Path | None = None,
    output_json: str | Path | None = None,
    output_md: str | Path | None = None,
) -> dict:
    status = _read_json(Path(status_json))
    balance = _read_json(Path(labeled_balance_json))
    protected_task = _read_json(Path(protected_task_summary_json)) if protected_task_summary_json else {}
    protected_priority_task = (
        _read_json(Path(protected_priority_task_summary_json)) if protected_priority_task_summary_json else {}
    )

    status_summary = status.get("summary", {})
    threshold = _threshold_decision(status_summary, balance.get("threshold_simulations") or [])
    review_lanes = _review_lane_decision(status, protected_task, protected_priority_task)
    pattern_release = _pattern_release_decision(status)
    delivery = status.get("delivery_recommendation") or {}
    state = _convergence_state(status_summary, threshold, review_lanes, pattern_release)
    next_actions = _next_actions(status, threshold, review_lanes, pattern_release, delivery)
    report = {
        "summary": {
            "convergence_state": state,
            "threshold_decision": threshold["decision"],
            "recommended_global_accept_threshold": threshold["current_global_threshold"],
            "recommended_second_pass_threshold": threshold["current_second_pass_threshold"],
            "current_threshold_ties_best_accuracy": threshold["current_ties_best_accuracy"],
            "review_lane_decision": review_lanes["decision"],
            "protected_review_lane_count": review_lanes["protected_review_lane_count"],
            "protected_lanes_next_review_task_rows": review_lanes["next_task_rows"],
            "protected_lanes_priority_task_rows": review_lanes["priority_task_rows"],
            "pattern_release_decision": pattern_release["decision"],
            "pattern_release_gate_status": pattern_release["gate_status"],
            "regression_gate_status": str(status_summary.get("regression_gate_status") or ""),
            "delivery_decision": delivery.get("decision", ""),
            "delivery_output_csv": delivery.get("output_csv", ""),
            "delivery_output_xlsx": delivery.get("output_xlsx", ""),
            "delivery_is_rule_release": delivery.get("is_rule_release", False),
            "filled_decisive_rows": _to_int(status_summary.get("filled_decisive_rows")),
            "next_action_count": len(next_actions),
        },
        "threshold": threshold,
        "review_lanes": review_lanes,
        "pattern_release": pattern_release,
        "delivery_recommendation": delivery,
        "next_actions": next_actions,
        "inputs": {
            "status_json": str(status_json),
            "labeled_balance_json": str(labeled_balance_json),
            "protected_task_summary_json": str(protected_task_summary_json or ""),
            "protected_priority_task_summary_json": str(protected_priority_task_summary_json or ""),
        },
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


def _threshold_decision(status_summary: dict, simulations: list[dict]) -> dict:
    current_global = _to_int(status_summary.get("recommended_global_accept_threshold"))
    current_second = _to_int(status_summary.get("recommended_second_pass_threshold"))
    threshold_status = str(status_summary.get("threshold_status") or "")
    normalized = [_threshold_row(row) for row in simulations if row]
    accuracy_rows = [row for row in normalized if row.get("overall_accuracy") is not None]
    best_accuracy = max((row["overall_accuracy"] for row in accuracy_rows), default=None)
    current_row = next((row for row in normalized if row["threshold"] == current_global), {})
    best_rows = [row for row in accuracy_rows if best_accuracy is not None and _same_float(row["overall_accuracy"], best_accuracy)]
    current_ties_best = bool(current_row) and best_accuracy is not None and _same_float(
        current_row.get("overall_accuracy"), best_accuracy
    )
    stricter_rows = [row for row in normalized if row["threshold"] > current_global]
    next_stricter = min(stricter_rows, key=lambda row: row["threshold"], default={})
    if not normalized:
        decision = "insufficient_labeled_threshold_data"
        reason = "No threshold simulations were available."
    elif threshold_status == "stable_keep_current" and current_global == 75 and current_second == 75 and current_ties_best:
        decision = "keep_current_75_75"
        reason = "The current 75/75 thresholds tie the best labeled accuracy, while stricter thresholds reduce recall."
    elif current_ties_best and threshold_status == "stable_keep_current":
        decision = "keep_current"
        reason = "The current threshold ties the best labeled accuracy."
    else:
        decision = "review_threshold_change"
        reason = "The current threshold does not match the status recommendation or does not tie the best labeled accuracy."
    return {
        "decision": decision,
        "reason": reason,
        "threshold_status": threshold_status,
        "current_global_threshold": current_global,
        "current_second_pass_threshold": current_second,
        "current_threshold_metrics": current_row,
        "best_accuracy": best_accuracy,
        "best_accuracy_thresholds": [row["threshold"] for row in best_rows],
        "current_ties_best_accuracy": current_ties_best,
        "next_stricter_threshold_metrics": next_stricter,
        "simulations": normalized,
    }


def _review_lane_decision(status: dict, protected_task: dict, protected_priority_task: dict) -> dict:
    summary = status.get("summary", {})
    gates = status.get("application_gates") or {}
    review_gate = gates.get("review_lane_change") or {}
    task_rows = _to_int(protected_task.get("task_rows"))
    priority_rows = _to_int(protected_priority_task.get("task_rows"))
    protected_count = _to_int(summary.get("protected_review_lane_count"))
    if str(review_gate.get("status") or "") == "blocked":
        decision = "keep_protected_lanes"
        reason = str(review_gate.get("reason") or "Protected lanes still need review.")
    elif task_rows:
        decision = "collect_protected_lane_labels"
        reason = "Protected-lane review rows remain available for the next small label batch."
    else:
        decision = "no_review_lane_change"
        reason = "No review-lane change is currently recommended."
    return {
        "decision": decision,
        "reason": reason,
        "gate_status": str(review_gate.get("status") or ""),
        "gate_blockers": [str(item) for item in review_gate.get("blockers") or [] if str(item)],
        "protected_review_lane_count": protected_count,
        "next_task_rows": task_rows,
        "next_task_csv": str(protected_task.get("output_csv") or ""),
        "next_task_xlsx": str(protected_task.get("output_xlsx") or ""),
        "priority_task_rows": priority_rows,
        "priority_task_csv": str(protected_priority_task.get("output_csv") or ""),
        "priority_task_xlsx": str(protected_priority_task.get("output_xlsx") or ""),
        "priority_reason_counts": protected_priority_task.get("priority_reason_counts") or {},
        "reason_counts": protected_task.get("reason_counts") or {},
        "agent_b_decision_counts": protected_task.get("agent_b_decision_counts") or {},
        "targets": protected_task.get("targets") or [],
    }


def _pattern_release_decision(status: dict) -> dict:
    summary = status.get("summary", {})
    gates = status.get("application_gates") or {}
    gate = gates.get("pattern_release_change") or {}
    gate_status = str(gate.get("status") or "")
    blockers = [str(item) for item in gate.get("blockers") or [] if str(item)]
    if gate_status == "candidate" and not blockers:
        decision = "guarded_candidate_requires_explicit_allow"
        reason = "Pattern release passed current spot checks but still requires explicit controlled rollout approval."
    elif blockers:
        decision = "blocked"
        reason = str(gate.get("reason") or "Pattern release gate has blockers.")
    else:
        decision = "not_ready"
        reason = str(gate.get("reason") or "Pattern release is not ready.")
    return {
        "decision": decision,
        "reason": reason,
        "gate_status": gate_status,
        "gate_blockers": blockers,
        "can_apply_now": bool(gate.get("can_apply_now")),
        "required_action": str(gate.get("required_action") or ""),
        "pattern_release_status": str(summary.get("pattern_release_status") or ""),
        "correct_rows": _to_int(summary.get("pattern_release_correct_rows")),
        "wrong_rows": _to_int(summary.get("pattern_release_wrong_rows")),
        "source_path": str(summary.get("pattern_release_source_path") or ""),
        "source_kind": str(summary.get("pattern_release_source_kind") or ""),
    }


def _convergence_state(
    status_summary: dict,
    threshold: dict,
    review_lanes: dict,
    pattern_release: dict,
) -> str:
    workflow_status = str(status_summary.get("workflow_status") or "")
    if threshold["decision"].startswith("review_threshold"):
        return "not_converged_threshold_review_needed"
    if review_lanes["decision"] in {"keep_protected_lanes", "collect_protected_lane_labels"}:
        return "partially_converged_keep_protected_lanes"
    if pattern_release["decision"] == "guarded_candidate_requires_explicit_allow":
        return "threshold_stable_pattern_candidate"
    if workflow_status == "converged_current_rules":
        return "converged_current_rules"
    return workflow_status or "unknown"


def _next_actions(
    status: dict,
    threshold: dict,
    review_lanes: dict,
    pattern_release: dict,
    delivery: dict | None = None,
) -> list[str]:
    actions: list[str] = []
    if threshold["decision"] in {"keep_current_75_75", "keep_current"}:
        actions.append("Keep first-pass and second-pass thresholds unchanged.")
    else:
        actions.append("Review threshold simulations before changing score thresholds.")
    delivery = delivery or {}
    if delivery.get("decision") == "use_regression_overlay_final":
        output = delivery.get("output_xlsx") or delivery.get("output_csv") or "the regression overlay final"
        actions.append(
            f"Use {output} for current delivery; do not treat the overlay as a generalized scoring-rule release."
        )
    if review_lanes.get("priority_task_rows"):
        actions.append(
            f"Fill {review_lanes.get('priority_task_xlsx') or 'the protected-lane priority review task'} "
            f"({review_lanes['priority_task_rows']} rows) first when review capacity is limited."
        )
        if review_lanes.get("next_task_rows"):
            actions.append(
                f"Fill {review_lanes.get('next_task_xlsx') or 'the full protected-lane review task'} "
                f"({review_lanes['next_task_rows']} rows) before reducing protected review lanes."
            )
    elif review_lanes.get("next_task_rows"):
        actions.append(
            f"Fill {review_lanes.get('next_task_xlsx') or 'the protected-lane review task'} "
            f"({review_lanes['next_task_rows']} rows) before reducing protected review lanes."
        )
    elif review_lanes["decision"] == "keep_protected_lanes":
        actions.append("Keep protected review lanes active until new labels support a narrower lane change.")
    if pattern_release["decision"] == "guarded_candidate_requires_explicit_allow":
        actions.append("Treat pattern release as a guarded candidate only; require explicit allow-candidate rollout.")
    has_priority_task = bool(review_lanes.get("priority_task_rows"))
    full_task_path = str(review_lanes.get("next_task_xlsx") or "")
    for action in status.get("next_actions") or []:
        if has_priority_task and _is_stale_full_task_first_action(str(action), full_task_path):
            continue
        if action and action not in actions:
            actions.append(str(action))
    return actions


def _is_stale_full_task_first_action(action: str, full_task_path: str) -> bool:
    if not full_task_path or full_task_path not in action:
        return False
    normalized = action.casefold()
    return "next small label batch" in normalized or "first when review capacity is limited" in normalized


def _threshold_row(row: dict) -> dict:
    return {
        "threshold": _to_int(row.get("threshold")),
        "overall_accuracy": _to_float(row.get("overall_accuracy")),
        "auto_precision": _to_float(row.get("auto_precision")),
        "official_recall": _to_float(row.get("official_recall")),
        "false_official_rows": _to_int(row.get("false_official_rows")),
        "over_rejected_rows": _to_int(row.get("over_rejected_rows")),
        "official_output_rows": _to_int(row.get("official_output_rows")),
    }


def _render_markdown(report: dict) -> str:
    summary = report["summary"]
    threshold = report["threshold"]
    lanes = report["review_lanes"]
    pattern = report["pattern_release"]
    lines = [
        "# Convergence Audit",
        "",
        "## Summary",
        "",
        f"- Convergence state: {summary['convergence_state']}",
        f"- Threshold decision: {summary['threshold_decision']}",
        f"- Recommended thresholds: {summary['recommended_global_accept_threshold']}/{summary['recommended_second_pass_threshold']}",
        f"- Current threshold ties best accuracy: {str(summary['current_threshold_ties_best_accuracy']).lower()}",
        f"- Review-lane decision: {summary['review_lane_decision']}",
        f"- Protected-lane next task rows: {summary['protected_lanes_next_review_task_rows']}",
        f"- Protected-lane priority task rows: {summary['protected_lanes_priority_task_rows']}",
        f"- Pattern-release decision: {summary['pattern_release_decision']}",
        f"- Pattern-release gate: {summary['pattern_release_gate_status']}",
        f"- Regression gate: {summary['regression_gate_status']}",
        f"- Delivery decision: {summary.get('delivery_decision') or 'not_evaluated'}",
        f"- Delivery output XLSX: {summary.get('delivery_output_xlsx') or 'not_available'}",
        f"- Delivery is rule release: {str(summary.get('delivery_is_rule_release')).lower()}",
        "",
        "## Threshold Evidence",
        "",
        f"- Reason: {threshold['reason']}",
        f"- Best accuracy thresholds: {', '.join(str(item) for item in threshold['best_accuracy_thresholds']) or 'not available'}",
    ]
    current = threshold.get("current_threshold_metrics") or {}
    if current:
        lines.append(
            "- Current metrics: accuracy={overall_accuracy}, precision={auto_precision}, recall={official_recall}, false_official={false_official_rows}, over_rejected={over_rejected_rows}".format(
                **current
            )
        )
    stricter = threshold.get("next_stricter_threshold_metrics") or {}
    if stricter:
        lines.append(
            "- Next stricter threshold {threshold}: accuracy={overall_accuracy}, precision={auto_precision}, recall={official_recall}, false_official={false_official_rows}, over_rejected={over_rejected_rows}".format(
                **stricter
            )
        )
    lines.extend(
        [
            "",
            "## Review Lanes",
            "",
            f"- Reason: {lanes['reason']}",
            f"- Protected lane count: {lanes['protected_review_lane_count']}",
            f"- Priority review task: {lanes['priority_task_xlsx'] or 'not available'}",
            f"- Next review task: {lanes['next_task_xlsx'] or 'not available'}",
            f"- Priority reason counts: {json.dumps(lanes['priority_reason_counts'], ensure_ascii=False, sort_keys=True)}",
            f"- Reason counts: {json.dumps(lanes['reason_counts'], ensure_ascii=False, sort_keys=True)}",
            "",
            "## Pattern Release",
            "",
            f"- Reason: {pattern['reason']}",
            f"- Correct/wrong rows: {pattern['correct_rows']}/{pattern['wrong_rows']}",
            f"- Source: {pattern['source_path'] or 'not recorded'}",
            "",
            "## Delivery Recommendation",
            "",
            f"- Decision: {report.get('delivery_recommendation', {}).get('decision') or 'not_evaluated'}",
            f"- Output CSV: {report.get('delivery_recommendation', {}).get('output_csv') or 'not_available'}",
            f"- Output XLSX: {report.get('delivery_recommendation', {}).get('output_xlsx') or 'not_available'}",
            f"- Is rule release: {str(report.get('delivery_recommendation', {}).get('is_rule_release')).lower()}",
            f"- Reason: {report.get('delivery_recommendation', {}).get('reason') or 'not_available'}",
            "",
            "## Next Actions",
            "",
        ]
    )
    for action in report["next_actions"]:
        lines.append(f"- {action}")
    lines.append("")
    return "\n".join(lines)


def _read_json(path: Path) -> dict:
    if not path or not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _to_int(value: object) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def _to_float(value: object) -> float | None:
    try:
        return round(float(value), 4)
    except (TypeError, ValueError):
        return None


def _same_float(left: object, right: object, *, tolerance: float = 1e-9) -> bool:
    if left is None or right is None:
        return False
    return abs(float(left) - float(right)) <= tolerance


if __name__ == "__main__":
    raise SystemExit(main())
