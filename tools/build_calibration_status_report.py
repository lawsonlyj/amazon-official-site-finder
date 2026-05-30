from __future__ import annotations

import argparse
import json
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a top-level calibration convergence/status report.")
    parser.add_argument("--calibration-cycle-json", required=True)
    parser.add_argument("--balance-report-json")
    parser.add_argument("--threshold-boundary-json")
    parser.add_argument("--sample-eval-json")
    parser.add_argument("--output-json")
    parser.add_argument("--output-md")
    args = parser.parse_args(argv)

    report = build_calibration_status_report(
        calibration_cycle_json=args.calibration_cycle_json,
        balance_report_json=args.balance_report_json,
        threshold_boundary_json=args.threshold_boundary_json,
        sample_eval_json=args.sample_eval_json,
        output_json=args.output_json,
        output_md=args.output_md,
    )
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 0


def build_calibration_status_report(
    *,
    calibration_cycle_json: str | Path,
    balance_report_json: str | Path | None = None,
    threshold_boundary_json: str | Path | None = None,
    sample_eval_json: str | Path | None = None,
    output_json: str | Path | None = None,
    output_md: str | Path | None = None,
) -> dict:
    cycle = _read_json(calibration_cycle_json)
    balance = _read_json(balance_report_json) if balance_report_json else cycle.get("balance_report", {})
    threshold = _read_json(threshold_boundary_json) if threshold_boundary_json else cycle.get("threshold_boundary", {})
    sample_eval = _read_json(sample_eval_json) if sample_eval_json else {}

    cycle_summary = cycle.get("summary", {})
    balance_summary = balance.get("summary", {})
    threshold_summary = threshold.get("summary", {})
    sample_summary = sample_eval.get("summary", {})

    threshold_status = _threshold_status(cycle_summary, threshold_summary)
    pattern_status = _pattern_release_status(cycle_summary, balance_summary, threshold_summary)
    lane_status = _lane_status(cycle_summary, balance_summary, sample_summary)
    workflow_status = _workflow_status(cycle_summary, sample_summary, threshold_status, pattern_status, lane_status)
    open_requirements = _open_requirements(cycle_summary, sample_summary, threshold_status, pattern_status, lane_status)
    next_actions = _next_actions(workflow_status, open_requirements, lane_status)

    report = {
        "summary": {
            "workflow_status": workflow_status,
            "threshold_status": threshold_status["status"],
            "pattern_release_status": pattern_status["status"],
            "review_lane_status": lane_status["status"],
            "recommended_global_accept_threshold": _first_present(
                cycle_summary.get("recommended_global_accept_threshold"),
                threshold_summary.get("recommended_global_accept_threshold"),
            ),
            "recommended_second_pass_threshold": _first_present(
                cycle_summary.get("recommended_second_pass_threshold"),
                threshold_summary.get("recommended_second_pass_threshold"),
            ),
            "filled_labeled_rows": _first_present(
                cycle_summary.get("filled_eval_labeled_rows"),
                sample_summary.get("labeled_rows"),
            ),
            "filled_decisive_rows": _first_present(
                cycle_summary.get("filled_eval_decisive_rows"),
                sample_summary.get("decisive_rows"),
            ),
            "protected_review_lane_count": _to_int(
                _first_present(
                    cycle_summary.get("protected_review_lane_count"),
                    balance_summary.get("protected_review_lane_count"),
                )
            ),
            "lane_needs_more_label_rows": _to_int(sample_summary.get("lane_needs_more_label_rows")),
            "lane_candidate_for_change_rows": _to_int(sample_summary.get("lane_candidate_for_change_rows")),
            "pattern_release_correct_rows": _to_int(
                _first_present(
                    cycle_summary.get("pattern_release_correct_rows"),
                    balance_summary.get("pattern_release_correct_rows"),
                    threshold_summary.get("selected_actionable_correct_rows"),
                )
            ),
            "pattern_release_wrong_rows": _to_int(
                _first_present(
                    cycle_summary.get("pattern_release_wrong_rows"),
                    balance_summary.get("pattern_release_wrong_rows"),
                    threshold_summary.get("selected_actionable_wrong_rows"),
                )
            ),
            "open_requirement_count": len(open_requirements),
        },
        "threshold": threshold_status,
        "pattern_release": pattern_status,
        "review_lanes": lane_status,
        "open_requirements": open_requirements,
        "next_actions": next_actions,
        "inputs": {
            "calibration_cycle_json": str(calibration_cycle_json),
            "balance_report_json": str(balance_report_json or ""),
            "threshold_boundary_json": str(threshold_boundary_json or ""),
            "sample_eval_json": str(sample_eval_json or ""),
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


def _threshold_status(cycle_summary: dict, threshold_summary: dict) -> dict:
    recommended_global = _first_present(
        cycle_summary.get("recommended_global_accept_threshold"),
        threshold_summary.get("recommended_global_accept_threshold"),
    )
    recommended_second = _first_present(
        cycle_summary.get("recommended_second_pass_threshold"),
        threshold_summary.get("recommended_second_pass_threshold"),
    )
    change = threshold_summary.get("global_threshold_change")
    if recommended_global == 75 and recommended_second == 75 and change in {"keep_current", "", None}:
        return {
            "status": "stable_keep_current",
            "reason": "Current labeled evidence still recommends first-pass and second-pass threshold 75.",
        }
    return {
        "status": "review_threshold_change",
        "reason": "Threshold recommendations changed or are incomplete; inspect threshold boundary details before applying.",
    }


def _pattern_release_status(cycle_summary: dict, balance_summary: dict, threshold_summary: dict) -> dict:
    recommendation = _first_present(
        cycle_summary.get("recommended_pattern_release"),
        balance_summary.get("recommended_pattern_release"),
    )
    correct = _to_int(
        _first_present(
            cycle_summary.get("pattern_release_correct_rows"),
            balance_summary.get("pattern_release_correct_rows"),
            threshold_summary.get("selected_actionable_correct_rows"),
        )
    )
    wrong = _to_int(
        _first_present(
            cycle_summary.get("pattern_release_wrong_rows"),
            balance_summary.get("pattern_release_wrong_rows"),
            threshold_summary.get("selected_actionable_wrong_rows"),
        )
    )
    if recommendation == "narrow_pattern_release_candidate" and correct > 0 and wrong == 0:
        return {
            "status": "guarded_candidate",
            "reason": "A narrow pattern release recovers labeled over-rejected sites with zero labeled wrong releases.",
            "correct_rows": correct,
            "wrong_rows": wrong,
        }
    if wrong > 0:
        return {
            "status": "blocked_by_wrong_release",
            "reason": "Pattern release would release labeled wrong candidates.",
            "correct_rows": correct,
            "wrong_rows": wrong,
        }
    return {
        "status": "not_ready",
        "reason": "No actionable clean pattern release is currently available.",
        "correct_rows": correct,
        "wrong_rows": wrong,
    }


def _lane_status(cycle_summary: dict, balance_summary: dict, sample_summary: dict) -> dict:
    protected_lanes = _first_present(
        cycle_summary.get("protected_review_lane_count"),
        balance_summary.get("protected_review_lane_count"),
        0,
    )
    needs_more = _to_int(sample_summary.get("lane_needs_more_label_rows"))
    candidate_for_change = _to_int(sample_summary.get("lane_candidate_for_change_rows"))
    keep_review = _to_int(sample_summary.get("lane_keep_review_rows"))
    labeled = _to_int(sample_summary.get("labeled_rows"))
    if labeled == 0 and needs_more:
        status = "needs_human_labels"
        reason = "The current calibration sample is not filled; lane decisions cannot be changed yet."
    elif keep_review:
        status = "protected_by_filled_labels"
        reason = "Filled labels still show at least one lane that must remain in manual review."
    elif candidate_for_change:
        status = "candidate_for_downgrade"
        reason = "Filled labels found lane candidates for downgrade, but regression tests are still required."
    elif needs_more:
        status = "needs_more_labels"
        reason = "Lane labels are not yet sufficient for routing changes."
    elif _to_int(protected_lanes):
        status = "protected_lanes_present"
        reason = "Historical labeled evidence still protects one or more review lanes."
    else:
        status = "stable"
        reason = "No protected or under-labeled review lanes were reported."
    return {
        "status": status,
        "reason": reason,
        "protected_review_lane_count": _to_int(protected_lanes),
        "needs_more_label_rows": needs_more,
        "candidate_for_change_rows": candidate_for_change,
        "keep_review_rows": keep_review,
    }


def _workflow_status(
    cycle_summary: dict,
    sample_summary: dict,
    threshold_status: dict,
    pattern_status: dict,
    lane_status: dict,
) -> str:
    labeled = _to_int(
        _first_present(
            cycle_summary.get("filled_eval_labeled_rows"),
            sample_summary.get("labeled_rows"),
        )
    )
    if not labeled:
        return "not_converged_needs_human_labels"
    if threshold_status["status"] != "stable_keep_current":
        return "not_converged_threshold_review_needed"
    if lane_status["status"] in {"protected_by_filled_labels", "needs_more_labels"}:
        return "partially_converged_keep_review_lanes"
    if lane_status["status"] == "candidate_for_downgrade" or pattern_status["status"] == "guarded_candidate":
        return "candidate_changes_require_regression"
    return "converged_current_rules"


def _open_requirements(
    cycle_summary: dict,
    sample_summary: dict,
    threshold_status: dict,
    pattern_status: dict,
    lane_status: dict,
) -> list[dict]:
    out = []
    labeled = _to_int(
        _first_present(
            cycle_summary.get("filled_eval_labeled_rows"),
            sample_summary.get("labeled_rows"),
        )
    )
    if not labeled:
        out.append(
            {
                "id": "fill_calibration_sample",
                "status": "open",
                "reason": "No filled calibration labels are available.",
                "action": "Fill manual_decision/manual_url/notes in the calibration sample, then rerun the calibration cycle with --filled-sample.",
            }
        )
    if threshold_status["status"] != "stable_keep_current":
        out.append(
            {
                "id": "review_threshold_recommendation",
                "status": "open",
                "reason": threshold_status["reason"],
                "action": "Inspect threshold boundary and add regression tests before changing score thresholds.",
            }
        )
    if pattern_status["status"] == "guarded_candidate":
        out.append(
            {
                "id": "guarded_pattern_release",
                "status": "candidate",
                "reason": pattern_status["reason"],
                "action": "Keep the guarded pattern release enabled only with risky-subdomain guards and spot-check rows.",
            }
        )
    elif pattern_status["status"] == "blocked_by_wrong_release":
        out.append(
            {
                "id": "block_pattern_release",
                "status": "open",
                "reason": pattern_status["reason"],
                "action": "Do not apply this pattern release; add blocking regression fixtures.",
            }
        )
    if lane_status["status"] in {"needs_human_labels", "needs_more_labels"}:
        out.append(
            {
                "id": "collect_lane_labels",
                "status": "open",
                "reason": lane_status["reason"],
                "action": "Prioritize calibration labels for under-labeled review lanes before reducing manual review.",
            }
        )
    if lane_status["status"] == "candidate_for_downgrade":
        out.append(
            {
                "id": "lane_downgrade_candidate",
                "status": "candidate",
                "reason": lane_status["reason"],
                "action": "Add regression tests and downgrade only the exact clean evidence lane, not the global threshold.",
            }
        )
    return out


def _next_actions(workflow_status: str, open_requirements: list[dict], lane_status: dict) -> list[str]:
    if workflow_status == "not_converged_needs_human_labels":
        return [
            "Fill the latest calibration XLSX before changing thresholds or review-lane routing.",
            "Focus labels on lanes reported as needs_more_labels, especially second-pass low-score accepts.",
        ]
    if workflow_status == "candidate_changes_require_regression":
        return [
            "Add focused regression tests for each candidate rule or lane downgrade.",
            "Apply only narrow guarded rules; keep global threshold unchanged.",
        ]
    if workflow_status == "partially_converged_keep_review_lanes":
        return [
            "Keep protected review lanes active.",
            "Use filled wrong rows as blocking fixtures before more tuning.",
        ]
    if open_requirements:
        return [item["action"] for item in open_requirements[:3]]
    return ["Keep current thresholds and monitor future calibration samples."]


def _render_markdown(report: dict) -> str:
    summary = report["summary"]
    lines = [
        "# Calibration Status Report",
        "",
        "## Summary",
        "",
        f"- Workflow status: {summary['workflow_status']}",
        f"- Threshold status: {summary['threshold_status']}",
        f"- Pattern release status: {summary['pattern_release_status']}",
        f"- Review lane status: {summary['review_lane_status']}",
        f"- Recommended thresholds: {summary['recommended_global_accept_threshold']}/{summary['recommended_second_pass_threshold']}",
        f"- Filled decisive rows: {summary['filled_decisive_rows']}",
        f"- Protected review lanes: {summary['protected_review_lane_count']}",
        f"- Lane needs-more-label rows: {summary['lane_needs_more_label_rows']}",
        f"- Pattern release correct/wrong rows: {summary['pattern_release_correct_rows']}/{summary['pattern_release_wrong_rows']}",
        "",
        "## Open Requirements",
        "",
    ]
    if report["open_requirements"]:
        for item in report["open_requirements"]:
            lines.append(f"- {item['id']} ({item['status']}): {item['action']}")
    else:
        lines.append("- None")
    lines.extend(["", "## Next Actions", ""])
    for item in report["next_actions"]:
        lines.append(f"- {item}")
    lines.append("")
    return "\n".join(lines)


def _read_json(path_value: str | Path | None) -> dict:
    if not path_value:
        return {}
    path = Path(path_value)
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _first_present(*values: object) -> object:
    for value in values:
        if value is not None and value != "":
            return value
    return None


def _to_int(value: object) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
