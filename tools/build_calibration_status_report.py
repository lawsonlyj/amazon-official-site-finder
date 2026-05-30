from __future__ import annotations

import argparse
import csv
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

    artifacts = _sample_artifacts(cycle, sample_eval_json)
    threshold_status = _threshold_status(cycle_summary, threshold_summary)
    label_targets = _label_targets(cycle, balance, sample_eval, artifacts)
    pattern_status = _pattern_release_status(cycle_summary, balance_summary, threshold_summary, label_targets)
    lane_status = _lane_status(cycle_summary, balance_summary, sample_summary, label_targets)
    lane_change_candidates = _lane_change_candidates(label_targets, lane_status)
    regression_gate_status = _regression_gate_status(cycle_summary)
    delivery_recommendation = cycle.get("delivery_recommendation") or _delivery_recommendation(cycle_summary, artifacts)
    labeling_instructions = _labeling_instructions()
    workflow_status = _workflow_status(
        cycle_summary,
        sample_summary,
        threshold_status,
        pattern_status,
        lane_status,
        regression_gate_status,
    )
    open_requirements = _open_requirements(
        cycle_summary,
        sample_summary,
        threshold_status,
        pattern_status,
        lane_status,
        regression_gate_status,
    )
    application_gates = _application_gates(
        workflow_status,
        threshold_status,
        pattern_status,
        lane_status,
        regression_gate_status,
        open_requirements,
    )
    next_actions = _next_actions(
        workflow_status,
        open_requirements,
        lane_status,
        artifacts,
        label_targets,
        delivery_recommendation,
    )

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
            "decision_quality_issue_rows": _to_int(sample_summary.get("decision_quality_issue_rows")),
            "invalid_manual_decision_rows": _to_int(sample_summary.get("invalid_manual_decision_rows")),
            "replace_missing_manual_url_rows": _to_int(sample_summary.get("replace_missing_manual_url_rows")),
            "lane_change_candidate_count": len(lane_change_candidates),
            "deferred_lane_change_candidate_count": sum(
                1 for item in lane_change_candidates if item.get("status") == "deferred_until_remaining_label_gaps_close"
            ),
            "ready_lane_change_candidate_count": sum(
                1 for item in lane_change_candidates if item.get("status") == "ready_for_regression"
            ),
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
            "pattern_release_source_path": _first_present(
                cycle_summary.get("recommended_pattern_release_source_path"),
                balance_summary.get("pattern_release_source_path"),
                "",
            ),
            "pattern_release_source_kind": _first_present(
                cycle_summary.get("recommended_pattern_release_source_kind"),
                balance_summary.get("pattern_release_source_kind"),
                "",
            ),
            "open_requirement_count": len(open_requirements),
            "label_target_count": len(label_targets),
            "high_priority_label_target_count": sum(1 for target in label_targets if target.get("priority") == "high"),
            "decisive_rows_needed": sum(_to_int(target.get("decisive_rows_needed")) for target in label_targets),
            "high_priority_decisive_rows_needed": sum(
                _to_int(target.get("decisive_rows_needed")) for target in label_targets if target.get("priority") == "high"
            ),
            "label_gap_task_rows": _to_int(cycle_summary.get("label_gap_task_rows")),
            "label_gap_high_priority_task_rows": _to_int(cycle_summary.get("label_gap_high_priority_task_rows")),
            "protected_lanes_priority_task_rows": _to_int(cycle_summary.get("protected_lanes_priority_task_rows")),
            "regression_gate_status": regression_gate_status["status"],
            "regression_gate_fail_rows": regression_gate_status["fail_rows"],
            "regression_gate_unverified_rows": regression_gate_status["unverified_rows"],
            "delivery_decision": delivery_recommendation.get("decision", ""),
            "delivery_output_csv": delivery_recommendation.get("output_csv", ""),
            "delivery_output_xlsx": delivery_recommendation.get("output_xlsx", ""),
            "delivery_is_rule_release": delivery_recommendation.get("is_rule_release", False),
        },
        "threshold": threshold_status,
        "pattern_release": pattern_status,
        "review_lanes": lane_status,
        "regression_gate": regression_gate_status,
        "delivery_recommendation": delivery_recommendation,
        "application_gates": application_gates,
        "artifacts": artifacts,
        "label_targets": label_targets,
        "lane_change_candidates": lane_change_candidates,
        "labeling_instructions": labeling_instructions,
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


def _pattern_release_status(
    cycle_summary: dict,
    balance_summary: dict,
    threshold_summary: dict,
    label_targets: list[dict] | None = None,
) -> dict:
    recommendation = _first_present(
        cycle_summary.get("recommended_pattern_release"),
        balance_summary.get("recommended_pattern_release"),
    )
    source_kind = _first_present(
        cycle_summary.get("recommended_pattern_release_source_kind"),
        balance_summary.get("pattern_release_source_kind"),
        "",
    )
    source_path = _first_present(
        cycle_summary.get("recommended_pattern_release_source_path"),
        balance_summary.get("pattern_release_source_path"),
        "",
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
        if source_kind == "supplied_prior":
            spot_check = _pattern_release_spot_check_status(label_targets or [], correct)
            if spot_check.get("validated"):
                return {
                    "status": "current_guarded_candidate",
                    "reason": "The supplied prior pattern release passed current-batch spot-check labels with zero blockers.",
                    "correct_rows": correct,
                    "wrong_rows": wrong,
                    "source_kind": source_kind,
                    "source_path": source_path,
                    "spot_check_labeled_rows": spot_check.get("labeled_rows"),
                    "spot_check_blocking_rows": spot_check.get("blocking_rows"),
                }
            return {
                "status": "historical_guarded_candidate",
                "reason": "A supplied prior pattern release has clean historical labels but still needs current-batch spot-check labels before widening automation.",
                "correct_rows": correct,
                "wrong_rows": wrong,
                "source_kind": source_kind,
                "source_path": source_path,
            }
        return {
            "status": "current_guarded_candidate",
            "reason": "A current-cycle narrow pattern release recovers labeled over-rejected sites with zero labeled wrong releases.",
            "correct_rows": correct,
            "wrong_rows": wrong,
            "source_kind": source_kind or "current_cycle",
            "source_path": source_path,
        }
    if wrong > 0:
        return {
            "status": "blocked_by_wrong_release",
            "reason": "Pattern release would release labeled wrong candidates.",
            "correct_rows": correct,
            "wrong_rows": wrong,
            "source_kind": source_kind,
            "source_path": source_path,
        }
    return {
        "status": "not_ready",
        "reason": "No actionable clean pattern release is currently available.",
        "correct_rows": correct,
        "wrong_rows": wrong,
        "source_kind": source_kind,
        "source_path": source_path,
    }


def _pattern_release_spot_check_status(label_targets: list[dict], correct_rows: int) -> dict:
    for target in label_targets:
        if target.get("review_reason") != "precision_calibrated_pattern_release":
            continue
        labeled = _to_int(target.get("decisive_rows"))
        blocking = _to_int(target.get("blocking_rows"))
        return {
            "validated": labeled >= min(correct_rows, _to_int(target.get("rows"))) and blocking == 0,
            "labeled_rows": labeled,
            "blocking_rows": blocking,
        }
    return {"validated": False, "labeled_rows": 0, "blocking_rows": 0}


def _lane_status(cycle_summary: dict, balance_summary: dict, sample_summary: dict, label_targets: list[dict] | None = None) -> dict:
    protected_lanes = _first_present(
        cycle_summary.get("protected_review_lane_count"),
        balance_summary.get("protected_review_lane_count"),
        0,
    )
    label_targets = label_targets or []
    decisive_rows_needed = sum(_to_int(target.get("decisive_rows_needed")) for target in label_targets)
    high_priority_decisive_rows_needed = sum(
        _to_int(target.get("decisive_rows_needed")) for target in label_targets if target.get("priority") == "high"
    )
    needs_more = _to_int(sample_summary.get("lane_needs_more_label_rows"))
    candidate_for_change = _to_int(sample_summary.get("lane_candidate_for_change_rows"))
    keep_review = _to_int(sample_summary.get("lane_keep_review_rows"))
    labeled = _to_int(sample_summary.get("labeled_rows"))
    if labeled == 0 and (needs_more or decisive_rows_needed):
        status = "needs_human_labels"
        reason = "The current calibration sample is not filled; lane decisions cannot be changed yet."
    elif keep_review:
        status = "protected_by_filled_labels"
        reason = "Filled labels still show at least one lane that must remain in manual review."
    elif decisive_rows_needed:
        status = "needs_more_labels"
        reason = "Remaining decisive-label gaps must be filled before reducing manual review or changing rules."
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
        "decisive_rows_needed": decisive_rows_needed,
        "high_priority_decisive_rows_needed": high_priority_decisive_rows_needed,
    }


def _lane_change_candidates(label_targets: list[dict], lane_status: dict) -> list[dict]:
    total_gap = _to_int(lane_status.get("decisive_rows_needed"))
    out = []
    for target in label_targets:
        if target.get("recommendation") != "candidate_for_review_downgrade":
            continue
        own_gap = _to_int(target.get("decisive_rows_needed"))
        if own_gap:
            status = "needs_more_labels_for_candidate_lane"
            action = "Fill the remaining labels for this lane before considering any routing change."
        elif total_gap:
            status = "deferred_until_remaining_label_gaps_close"
            action = "Keep this candidate queued; wait for remaining label gaps and then add regression tests before applying."
        else:
            status = "ready_for_regression"
            action = "Add focused regression tests and then downgrade only this exact evidence lane."
        out.append(
            {
                "review_reason": str(target.get("review_reason") or ""),
                "candidate_kind": "review_lane_downgrade",
                "status": status,
                "priority": str(target.get("priority") or ""),
                "rows": _to_int(target.get("rows")),
                "labeled_rows": _to_int(target.get("labeled_rows")),
                "decisive_rows": _to_int(target.get("decisive_rows")),
                "support_rows": _to_int(target.get("support_rows")),
                "blocking_rows": _to_int(target.get("blocking_rows")),
                "support_rate": target.get("support_rate"),
                "support_rate_wilson_lower_80": target.get("support_rate_wilson_lower_80"),
                "blocking_rate_wilson_upper_80": target.get("blocking_rate_wilson_upper_80"),
                "evidence_strength": str(target.get("evidence_strength") or ""),
                "target_decisive_rows": _to_int(target.get("target_decisive_rows")),
                "decisive_rows_needed": own_gap,
                "blocking_decisive_rows_needed": max(0, total_gap - own_gap),
                "recommendation": str(target.get("recommendation") or ""),
                "required_action": action,
            }
        )
    return out


def _regression_gate_status(cycle_summary: dict) -> dict:
    case_rows = _to_int(cycle_summary.get("filled_regression_case_rows"))
    raw_status = str(cycle_summary.get("regression_gate_status") or "").strip()
    fail_rows = _to_int(cycle_summary.get("regression_gate_fail_rows"))
    unverified_rows = _to_int(cycle_summary.get("regression_gate_unverified_rows"))
    if not case_rows:
        status = "not_needed"
        reason = "No filled regression cases are available yet."
    elif raw_status == "pass" and fail_rows == 0 and unverified_rows == 0:
        status = "pass"
        reason = "Candidate output passed all filled calibration regression cases."
    elif raw_status or fail_rows or unverified_rows:
        status = "failed"
        reason = f"Regression gate has fail_rows={fail_rows}, unverified_rows={unverified_rows}."
    else:
        status = "not_run"
        reason = "Filled regression cases exist, but no candidate output has been checked by the regression gate."
    return {
        "status": status,
        "raw_status": raw_status,
        "case_rows": case_rows,
        "fail_rows": fail_rows,
        "unverified_rows": unverified_rows,
        "reason": reason,
    }


def _workflow_status(
    cycle_summary: dict,
    sample_summary: dict,
    threshold_status: dict,
    pattern_status: dict,
    lane_status: dict,
    regression_gate_status: dict,
) -> str:
    if _to_int(sample_summary.get("decision_quality_issue_rows")):
        return "not_converged_fix_fill_quality"
    labeled = _to_int(
        _first_present(
            cycle_summary.get("filled_eval_labeled_rows"),
            sample_summary.get("labeled_rows"),
        )
    )
    if not labeled:
        return "not_converged_needs_human_labels"
    if regression_gate_status["status"] == "failed":
        return "not_converged_regression_gate_failed"
    if threshold_status["status"] != "stable_keep_current":
        return "not_converged_threshold_review_needed"
    if lane_status["status"] in {"protected_by_filled_labels", "needs_more_labels"}:
        return "partially_converged_keep_review_lanes"
    if lane_status["status"] == "candidate_for_downgrade" or pattern_status["status"] in {
        "current_guarded_candidate",
        "historical_guarded_candidate",
    }:
        if regression_gate_status["status"] == "pass":
            return "candidate_changes_regression_passed"
        return "candidate_changes_require_regression"
    if regression_gate_status["status"] == "not_run":
        return "not_converged_regression_gate_not_run"
    return "converged_current_rules"


def _open_requirements(
    cycle_summary: dict,
    sample_summary: dict,
    threshold_status: dict,
    pattern_status: dict,
    lane_status: dict,
    regression_gate_status: dict,
) -> list[dict]:
    out = []
    quality_issue_rows = _to_int(sample_summary.get("decision_quality_issue_rows"))
    if quality_issue_rows:
        invalid = _to_int(sample_summary.get("invalid_manual_decision_rows"))
        missing_url = _to_int(sample_summary.get("replace_missing_manual_url_rows"))
        out.append(
            {
                "id": "fix_calibration_fill_quality",
                "status": "open",
                "reason": f"Filled calibration labels contain {quality_issue_rows} quality issue row(s): invalid_decision={invalid}, replace_missing_manual_url={missing_url}.",
                "action": "Fix invalid manual_decision values and add manual_url for replace rows before using these labels for threshold or rule changes.",
            }
        )
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
    if regression_gate_status["status"] == "failed":
        out.append(
            {
                "id": "fix_regression_gate_failures",
                "status": "open",
                "reason": regression_gate_status["reason"],
                "action": "Fix candidate workflow changes until calibration_regression_gate has zero failed and zero unverified rows.",
            }
        )
    elif regression_gate_status["status"] == "not_run":
        out.append(
            {
                "id": "run_regression_gate",
                "status": "candidate",
                "reason": regression_gate_status["reason"],
                "action": "Run tools/run_calibration_regression_gate.py against the candidate official_sites.csv before applying threshold or routing changes.",
            }
        )
    if pattern_status["status"] == "current_guarded_candidate":
        out.append(
            {
                "id": "guarded_pattern_release",
                "status": "candidate",
                "reason": pattern_status["reason"],
                "action": "Keep the guarded pattern release enabled only with risky-subdomain guards and spot-check rows.",
            }
        )
    elif pattern_status["status"] == "historical_guarded_candidate":
        out.append(
            {
                "id": "validate_historical_pattern_release",
                "status": "candidate",
                "reason": pattern_status["reason"],
                "action": "Treat the supplied prior pattern release as a historical candidate; require current label-gap spot-checks before widening automatic release.",
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
    if lane_status.get("status") != "candidate_for_downgrade" and _to_int(
        lane_status.get("candidate_for_change_rows")
    ):
        out.append(
            {
                "id": "defer_lane_downgrade_candidate",
                "status": "candidate",
                "reason": "At least one lane has clean filled labels, but other decisive-label gaps remain open.",
                "action": "Keep the lane downgrade candidate queued; apply it only after remaining label gaps close and regression tests cover the exact evidence lane.",
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


def _application_gates(
    workflow_status: str,
    threshold_status: dict,
    pattern_status: dict,
    lane_status: dict,
    regression_gate_status: dict,
    open_requirements: list[dict],
) -> dict:
    blockers = [item["id"] for item in open_requirements if item.get("status") == "open"]
    gate_blocker = _regression_gate_blocker(regression_gate_status)
    if gate_blocker:
        blockers = [gate_blocker, *[item for item in blockers if item != gate_blocker]]
    return {
        "global_threshold_change": _threshold_application_gate(threshold_status, regression_gate_status, blockers),
        "review_lane_change": _review_lane_application_gate(workflow_status, lane_status, regression_gate_status, blockers),
        "pattern_release_change": _pattern_release_application_gate(workflow_status, pattern_status, regression_gate_status, blockers),
    }


def _threshold_application_gate(threshold_status: dict, regression_gate_status: dict, blockers: list[str]) -> dict:
    if regression_gate_status["status"] in {"failed", "not_run"}:
        return _blocked_gate("global_threshold_change", blockers, "Regression gate must pass before any threshold change.")
    if threshold_status["status"] == "stable_keep_current":
        return {
            "status": "not_recommended",
            "can_apply_now": False,
            "blockers": [],
            "reason": "Current evidence recommends keeping first-pass and second-pass thresholds unchanged.",
            "required_action": "Keep thresholds unchanged unless a later threshold boundary report recommends a change.",
        }
    return {
        "status": "candidate",
        "can_apply_now": False,
        "blockers": blockers,
        "reason": threshold_status.get("reason", ""),
        "required_action": "Review threshold boundary metrics and pass regression gate before changing thresholds.",
    }


def _review_lane_application_gate(
    workflow_status: str,
    lane_status: dict,
    regression_gate_status: dict,
    blockers: list[str],
) -> dict:
    if regression_gate_status["status"] in {"failed", "not_run"}:
        return _blocked_gate("review_lane_change", blockers, "Regression gate must pass before reducing manual review.")
    if workflow_status == "candidate_changes_regression_passed" and lane_status["status"] == "candidate_for_downgrade":
        return {
            "status": "candidate",
            "can_apply_now": False,
            "blockers": [],
            "reason": lane_status.get("reason", ""),
            "required_action": "Apply only a narrow lane downgrade after confirming regression coverage for the exact evidence lane.",
        }
    return _blocked_gate("review_lane_change", blockers, lane_status.get("reason", "Review lanes are not ready for routing changes."))


def _pattern_release_application_gate(
    workflow_status: str,
    pattern_status: dict,
    regression_gate_status: dict,
    blockers: list[str],
) -> dict:
    if pattern_status["status"] == "blocked_by_wrong_release":
        return _blocked_gate("pattern_release_change", ["block_pattern_release"], pattern_status.get("reason", "Pattern release is blocked."))
    if regression_gate_status["status"] in {"failed", "not_run"}:
        return _blocked_gate("pattern_release_change", blockers, "Regression gate must pass before widening pattern release.")
    if pattern_status["status"] == "current_guarded_candidate":
        return {
            "status": "candidate",
            "can_apply_now": False,
            "blockers": [],
            "reason": pattern_status.get("reason", ""),
            "required_action": "Keep any pattern release guarded and narrow; do not widen beyond the validated evidence pattern.",
        }
    if pattern_status["status"] == "historical_guarded_candidate":
        return _blocked_gate(
            "pattern_release_change",
            ["validate_historical_pattern_release", *blockers],
            pattern_status.get("reason", "Historical pattern release still needs current-batch validation."),
        )
    return _blocked_gate("pattern_release_change", blockers, pattern_status.get("reason", "Pattern release is not ready."))


def _regression_gate_blocker(regression_gate_status: dict) -> str:
    if regression_gate_status["status"] == "failed":
        return "fix_regression_gate_failures"
    if regression_gate_status["status"] == "not_run":
        return "run_regression_gate"
    return ""


def _blocked_gate(name: str, blockers: list[str], reason: str) -> dict:
    return {
        "status": "blocked",
        "can_apply_now": False,
        "blockers": list(dict.fromkeys(blockers)),
        "reason": reason,
        "required_action": f"Resolve blockers before applying {name}.",
    }


def _sample_artifacts(cycle: dict, sample_eval_json: str | Path | None) -> dict:
    outputs = cycle.get("outputs", {})
    return {
        "sample_csv": str(outputs.get("sample_csv") or ""),
        "sample_xlsx": str(outputs.get("sample_xlsx") or ""),
        "label_gap_csv": str(outputs.get("label_gap_csv") or ""),
        "label_gap_xlsx": str(outputs.get("label_gap_xlsx") or ""),
        "label_gap_high_priority_csv": str(outputs.get("label_gap_high_priority_csv") or ""),
        "label_gap_high_priority_xlsx": str(outputs.get("label_gap_high_priority_xlsx") or ""),
        "protected_lanes_next_review_task_csv": str(outputs.get("protected_lanes_next_review_task_csv") or ""),
        "protected_lanes_next_review_task_xlsx": str(outputs.get("protected_lanes_next_review_task_xlsx") or ""),
        "protected_lanes_next_review_task_summary_json": str(
            outputs.get("protected_lanes_next_review_task_summary_json") or ""
        ),
        "protected_lanes_next_review_task_verification_json": str(
            outputs.get("protected_lanes_next_review_task_verification_json") or ""
        ),
        "protected_lanes_next_review_task_verification_md": str(
            outputs.get("protected_lanes_next_review_task_verification_md") or ""
        ),
        "protected_lanes_priority_task_csv": str(outputs.get("protected_lanes_priority_task_csv") or ""),
        "protected_lanes_priority_task_xlsx": str(outputs.get("protected_lanes_priority_task_xlsx") or ""),
        "protected_lanes_priority_task_summary_json": str(
            outputs.get("protected_lanes_priority_task_summary_json") or ""
        ),
        "protected_lanes_priority_task_handoff_md": str(outputs.get("protected_lanes_priority_task_handoff_md") or ""),
        "protected_lanes_priority_task_verification_json": str(
            outputs.get("protected_lanes_priority_task_verification_json") or ""
        ),
        "protected_lanes_priority_task_verification_md": str(
            outputs.get("protected_lanes_priority_task_verification_md") or ""
        ),
        "regression_overlay_csv": str(outputs.get("regression_overlay_csv") or ""),
        "regression_overlay_xlsx": str(outputs.get("regression_overlay_xlsx") or ""),
        "regression_overlay_gate_json": str(outputs.get("regression_overlay_gate_json") or ""),
        "regression_overlay_gate_md": str(outputs.get("regression_overlay_gate_md") or ""),
        "regression_overlay_balance_json": str(outputs.get("regression_overlay_balance_json") or ""),
        "regression_overlay_balance_csv": str(outputs.get("regression_overlay_balance_csv") or ""),
        "sample_eval_json": str(outputs.get("eval_json") or sample_eval_json or ""),
        "filled_eval_json": str(outputs.get("filled_eval_json") or ""),
        "regression_cases_csv": str(outputs.get("regression_cases_csv") or ""),
        "regression_gate_json": str(outputs.get("regression_gate_json") or ""),
        "regression_gate_md": str(outputs.get("regression_gate_md") or ""),
    }


def _label_targets(cycle: dict, balance: dict, sample_eval: dict, artifacts: dict) -> list[dict]:
    by_review = dict(sample_eval.get("by_review_reason") or {})
    for reason, rows in _sample_review_reason_counts(artifacts.get("sample_csv")).items():
        existing = dict(by_review.get(reason) or {})
        existing["rows"] = max(_to_int(existing.get("rows")), rows)
        existing.setdefault("labeled_rows", 0)
        existing.setdefault("decisive_rows", 0)
        by_review[reason] = existing

    lane_recommendations = {
        row.get("review_reason", ""): row for row in sample_eval.get("lane_recommendations", []) if row.get("review_reason")
    }
    cycle_summary = cycle.get("summary", {})
    balance_summary = balance.get("summary", {})
    protected = set(
        _as_list(_first_present(cycle_summary.get("protected_review_lanes"), balance_summary.get("protected_review_lanes")))
    )
    needs_more = set(
        _as_list(_first_present(cycle_summary.get("more_label_review_lanes"), balance_summary.get("more_label_review_lanes")))
    )
    spot_check = set(
        _as_list(_first_present(cycle_summary.get("spot_check_candidate_lanes"), balance_summary.get("spot_check_candidate_lanes")))
    )

    targets = []
    for reason, stats in by_review.items():
        if not reason:
            continue
        recommendation = lane_recommendations.get(reason, {}).get("recommendation") or _default_lane_recommendation(
            reason, protected, needs_more, spot_check
        )
        rows = _to_int(stats.get("rows"))
        decisive_rows = _to_int(stats.get("decisive_rows"))
        lane_eval = lane_recommendations.get(reason, {})
        target_decisive_rows = _target_decisive_rows(reason, recommendation, rows, protected, needs_more, spot_check)
        scenario_actions = _label_target_scenario_actions(reason, recommendation, protected, needs_more, spot_check)
        targets.append(
            {
                "review_reason": reason,
                "priority": _label_priority(reason, recommendation, protected, needs_more, spot_check),
                "rows": rows,
                "labeled_rows": _to_int(stats.get("labeled_rows")),
                "decisive_rows": decisive_rows,
                "support_rows": _to_int(lane_eval.get("support_rows")),
                "blocking_rows": _to_int(lane_eval.get("blocking_rows")),
                "support_rate": lane_eval.get("support_rate"),
                "support_rate_wilson_lower_80": lane_eval.get("support_rate_wilson_lower_80"),
                "blocking_rate_wilson_upper_80": lane_eval.get("blocking_rate_wilson_upper_80"),
                "evidence_strength": str(lane_eval.get("evidence_strength") or ""),
                "target_decisive_rows": target_decisive_rows,
                "decisive_rows_needed": max(0, target_decisive_rows - decisive_rows),
                "recommendation": recommendation,
                "label_goal": _label_goal(reason, recommendation, protected, needs_more, spot_check),
                "if_clean_action": scenario_actions["if_clean_action"],
                "if_blocked_action": scenario_actions["if_blocked_action"],
                "if_unsure_action": scenario_actions["if_unsure_action"],
            }
        )
    priority_order = {"high": 0, "medium": 1, "normal": 2, "low": 3}
    targets.sort(key=lambda row: (priority_order.get(row["priority"], 9), row["review_reason"]))
    return targets


def _sample_review_reason_counts(path_value: str | Path | None) -> dict[str, int]:
    if not path_value:
        return {}
    path = Path(path_value)
    if not path.exists():
        return {}
    counts: dict[str, int] = {}
    with path.open(newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            reason = str(row.get("review_reason") or "").strip()
            if reason:
                counts[reason] = counts.get(reason, 0) + 1
    return counts


def _default_lane_recommendation(reason: str, protected: set[str], needs_more: set[str], spot_check: set[str]) -> str:
    if reason in needs_more:
        return "needs_more_labels"
    if reason in spot_check:
        return "spot_check_candidate"
    if reason in protected:
        return "keep_review_lane"
    return "needs_more_labels"


def _label_priority(reason: str, recommendation: str, protected: set[str], needs_more: set[str], spot_check: set[str]) -> str:
    if reason in needs_more or recommendation == "candidate_for_review_downgrade":
        return "high"
    if reason in spot_check or reason in protected or recommendation in {"spot_check_candidate", "keep_review_lane"}:
        return "medium"
    if recommendation == "needs_more_labels":
        return "normal"
    return "normal"


def _target_decisive_rows(
    reason: str,
    recommendation: str,
    rows: int,
    protected: set[str],
    needs_more: set[str],
    spot_check: set[str],
) -> int:
    if rows <= 0:
        return 0
    if reason in needs_more:
        return min(rows, 5)
    if reason in spot_check or recommendation == "spot_check_candidate":
        return min(rows, 3)
    if reason in protected or recommendation == "keep_review_lane":
        return min(rows, 3)
    if recommendation in {"needs_more_labels", "candidate_for_review_downgrade"}:
        return min(rows, 5)
    return min(rows, 5)


def _label_goal(reason: str, recommendation: str, protected: set[str], needs_more: set[str], spot_check: set[str]) -> str:
    if reason in needs_more:
        return "Fill every sampled row; this lane is the current evidence bottleneck before routing can change."
    if reason in spot_check or recommendation == "spot_check_candidate":
        return "Spot-check released rows; any reject/replace blocks wider automatic release for this pattern."
    if reason in protected or recommendation == "keep_review_lane":
        return "Confirm whether this risky lane still captures wrong candidates or over-rejected correct sites."
    if recommendation == "needs_more_labels":
        return "Fill enough decisive labels to classify whether this lane stays manual or can be narrowed."
    if recommendation == "candidate_for_review_downgrade":
        return "Validate with regression cases before reducing manual review for this exact lane."
    return "Fill decisive labels so the lane can be classified."


def _label_target_scenario_actions(
    reason: str,
    recommendation: str,
    protected: set[str],
    needs_more: set[str],
    spot_check: set[str],
) -> dict[str, str]:
    if reason == "precision_second_pass_accepted_lt70" or reason in needs_more:
        return {
            "if_clean_action": "Queue a narrow review-lane downgrade candidate for this exact sub-70 second-pass evidence lane; require regression tests before applying.",
            "if_blocked_action": "Keep sub-70 second-pass accepts manual-only and add the wrong rows as regression fixtures.",
            "if_unsure_action": "Keep this lane in high-priority label gaps until enough decisive accept/reject/replace labels exist.",
        }
    if reason == "precision_calibrated_pattern_release" or reason in spot_check or recommendation == "spot_check_candidate":
        return {
            "if_clean_action": "Keep the calibrated pattern as a guarded release candidate and continue current-batch spot checks before widening automation.",
            "if_blocked_action": "Block wider pattern release and add the wrong rows as pattern regression fixtures.",
            "if_unsure_action": "Keep pattern release guarded; collect more spot-check labels before widening automation.",
        }
    if reason == "recall_unresolved_top_candidate":
        return {
            "if_clean_action": "Use accept/replace rows to mine exact recall patterns; do not lower global thresholds from this lane alone.",
            "if_blocked_action": "Keep unresolved top candidates manual-only and add bad candidate features to AgentB/risky URL checks.",
            "if_unsure_action": "Keep unresolved recall rows manual-only and refine evidence fields for future review.",
        }
    if reason in protected or recommendation == "keep_review_lane":
        return {
            "if_clean_action": "Consider a narrow routing downgrade only for this exact protected lane after regression tests and no remaining label gaps.",
            "if_blocked_action": "Keep this lane protected and add the wrong rows as regression fixtures for AgentA scoring/risk rules.",
            "if_unsure_action": "Keep this lane protected; unsure labels are not decisive enough to reduce manual review.",
        }
    if recommendation == "candidate_for_review_downgrade":
        return {
            "if_clean_action": "Proceed to focused regression tests, then downgrade only this exact evidence lane.",
            "if_blocked_action": "Cancel the downgrade candidate and keep the lane protected.",
            "if_unsure_action": "Defer the downgrade candidate until decisive labels replace unsure rows.",
        }
    return {
        "if_clean_action": "Treat clean labels as evidence for a narrow future rule, not a global threshold change.",
        "if_blocked_action": "Keep matching rows in review and add wrong rows to regression coverage.",
        "if_unsure_action": "Collect more decisive labels before changing thresholds or routing.",
    }


def _labeling_instructions() -> dict:
    return {
        "fields_to_fill": ["manual_decision", "manual_url", "notes"],
        "manual_decision_values": {
            "accept": "The shown official_url/candidate_url is the correct independent official site.",
            "replace": "The shown URL is wrong or missing, and manual_url contains the correct official site.",
            "reject": "No trustworthy official site is proven by the shown URL/candidate evidence.",
            "unsure": "Evidence is insufficient or conflicts remain after review.",
        },
        "manual_url_required_when": ["replace"],
        "notes_guidance": "Use short reasons such as wrong_company, service_mismatch, country_mismatch, platform_page, unreachable, or correct_country_language_site.",
    }


def _as_list(value: object) -> list[str]:
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value if str(item)]
    return []


def _delivery_recommendation(cycle_summary: dict, artifacts: dict) -> dict:
    raw_gate_status = _normalize_gate_status(cycle_summary.get("regression_gate_status"))
    overlay_gate_status = _normalize_gate_status(cycle_summary.get("regression_overlay_gate_status"))
    changed_rows = _to_int(
        _first_present(
            cycle_summary.get("regression_overlay_changed_rows"),
            cycle_summary.get("regression_overlay_change_count"),
        )
    )
    output_csv = str(artifacts.get("regression_overlay_csv") or cycle_summary.get("delivery_output_csv") or "")
    output_xlsx = str(artifacts.get("regression_overlay_xlsx") or cycle_summary.get("delivery_output_xlsx") or "")
    has_overlay_output = bool(output_csv or output_xlsx)
    deltas = [
        _to_float_or_none(cycle_summary.get("regression_overlay_balance_accuracy_delta")),
        _to_float_or_none(cycle_summary.get("regression_overlay_balance_precision_delta")),
        _to_float_or_none(cycle_summary.get("regression_overlay_balance_recall_delta")),
    ]
    known_deltas = [value for value in deltas if value is not None]
    balance_not_worse = not any(value < 0 for value in known_deltas)
    raw_gate_failed = raw_gate_status in {"fail", "failed"}

    if has_overlay_output and overlay_gate_status == "pass" and (changed_rows > 0 or raw_gate_failed) and balance_not_worse:
        decision = "use_regression_overlay_final"
        reason = (
            "Use the exact human-label regression overlay as the current deliverable; "
            "rules remain unconverged until the candidate regression gate passes."
        )
        selected_csv = output_csv
        selected_xlsx = output_xlsx
    elif has_overlay_output and overlay_gate_status == "pass":
        decision = "use_candidate_final"
        reason = "The regression overlay passes but does not improve this run; use the candidate final output."
        selected_csv = ""
        selected_xlsx = ""
    elif has_overlay_output:
        decision = "do_not_use_regression_overlay"
        reason = "The regression overlay output exists, but its gate did not pass."
        selected_csv = ""
        selected_xlsx = ""
    else:
        decision = "use_candidate_final"
        reason = "No regression overlay final is available for this run."
        selected_csv = ""
        selected_xlsx = ""
    return {
        "decision": decision,
        "reason": reason,
        "output_csv": selected_csv,
        "output_xlsx": selected_xlsx,
        "is_rule_release": False,
        "raw_candidate_gate_status": raw_gate_status,
        "raw_candidate_gate_fail_rows": cycle_summary.get("regression_gate_fail_rows"),
        "overlay_gate_status": overlay_gate_status,
        "overlay_changed_rows": changed_rows,
        "overlay_accuracy": cycle_summary.get("regression_overlay_balance_accuracy"),
        "overlay_precision": cycle_summary.get("regression_overlay_balance_auto_precision"),
        "overlay_recall": cycle_summary.get("regression_overlay_balance_official_recall"),
        "overlay_accuracy_delta": cycle_summary.get("regression_overlay_balance_accuracy_delta"),
        "overlay_precision_delta": cycle_summary.get("regression_overlay_balance_precision_delta"),
        "overlay_recall_delta": cycle_summary.get("regression_overlay_balance_recall_delta"),
    }


def _delivery_next_action(delivery: dict) -> str:
    if delivery.get("decision") != "use_regression_overlay_final":
        return ""
    output = delivery.get("output_xlsx") or delivery.get("output_csv") or "the regression overlay final"
    return (
        f"Use {output} for the current deliverable, but keep it separate from scoring-rule convergence; "
        "this overlay applies exact human regression labels only."
    )


def _normalize_gate_status(value: object) -> str:
    text = str(value or "").strip().lower()
    return {"passed": "pass"}.get(text, text)


def _next_actions(
    workflow_status: str,
    open_requirements: list[dict],
    lane_status: dict,
    artifacts: dict | None = None,
    label_targets: list[dict] | None = None,
    delivery_recommendation: dict | None = None,
) -> list[str]:
    artifacts = artifacts or {}
    label_targets = label_targets or []
    delivery_action = _delivery_next_action(delivery_recommendation or {})

    def with_delivery(actions: list[str]) -> list[str]:
        return ([delivery_action] if delivery_action else []) + actions

    if workflow_status == "not_converged_fix_fill_quality":
        fix_actions = [item["action"] for item in open_requirements if item.get("id") == "fix_calibration_fill_quality"]
        return with_delivery(
            fix_actions or ["Fix calibration fill-quality issues before changing thresholds or review-lane routing."]
        )
    if workflow_status == "not_converged_regression_gate_failed":
        gate_actions = [item["action"] for item in open_requirements if item.get("id") == "fix_regression_gate_failures"]
        return with_delivery(gate_actions or ["Fix regression gate failures before applying threshold or review-lane routing changes."])
    if workflow_status == "not_converged_regression_gate_not_run":
        gate_actions = [item["action"] for item in open_requirements if item.get("id") == "run_regression_gate"]
        return with_delivery(gate_actions or ["Run the calibration regression gate before declaring the current rules converged."])
    sample_xlsx = artifacts.get("sample_xlsx") or "the latest calibration XLSX"
    label_gap_xlsx = artifacts.get("label_gap_xlsx") or sample_xlsx
    high_priority_xlsx = artifacts.get("label_gap_high_priority_xlsx") or label_gap_xlsx
    open_targets = [target for target in label_targets if _to_int(target.get("decisive_rows_needed")) > 0]
    high_priority = [target for target in open_targets if target.get("priority") == "high"]
    if workflow_status == "not_converged_needs_human_labels" or lane_status.get("status") in {
        "needs_human_labels",
        "needs_more_labels",
    }:
        focus_targets = high_priority or open_targets
        focus = ", ".join(str(target.get("review_reason") or "") for target in focus_targets[:3])
        focus = focus or "needs_more_labels lanes"
        first_target = high_priority_xlsx if high_priority else label_gap_xlsx
        return with_delivery([
            f"Fill {first_target} before changing thresholds or review-lane routing.",
            f"Prioritize high-value labels for: {focus}.",
        ])
    if workflow_status == "candidate_changes_require_regression":
        gate_actions = [item["action"] for item in open_requirements if item.get("id") == "run_regression_gate"]
        if gate_actions:
            return with_delivery([
                gate_actions[0],
                "Use the regression gate result before applying any threshold or review-lane routing change.",
            ])
        return with_delivery([
            "Add focused regression tests for each candidate rule or lane downgrade.",
            "Apply only narrow guarded rules; keep global threshold unchanged.",
        ])
    if workflow_status == "candidate_changes_regression_passed":
        return with_delivery([
            "Regression gate passed; review the candidate rule/lane change and keep it narrow.",
            "Do not change the global threshold unless the threshold boundary report also recommends it.",
        ])
    if workflow_status == "partially_converged_keep_review_lanes":
        protected_task = artifacts.get("protected_lanes_next_review_task_xlsx")
        priority_task = artifacts.get("protected_lanes_priority_task_xlsx")
        return with_delivery([
            "Keep protected review lanes active.",
            f"Use {priority_task or protected_task or 'the protected-lane priority review task'} first when review capacity is limited.",
            f"Use {protected_task or 'the full protected-lane next review task'} before reducing protected review lanes.",
            "Use filled wrong rows as blocking fixtures before more tuning.",
        ])
    if open_requirements:
        return with_delivery([item["action"] for item in open_requirements[:3]])
    return with_delivery(["Keep current thresholds and monitor future calibration samples."])


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
        f"- Decision quality issue rows: {summary['decision_quality_issue_rows']}",
        f"- Invalid manual decisions: {summary['invalid_manual_decision_rows']}",
        f"- Replace rows missing manual_url: {summary['replace_missing_manual_url_rows']}",
        f"- Protected review lanes: {summary['protected_review_lane_count']}",
        f"- Lane needs-more-label rows: {summary['lane_needs_more_label_rows']}",
        f"- Lane change candidates: {summary['lane_change_candidate_count']} total, {summary['deferred_lane_change_candidate_count']} deferred, {summary['ready_lane_change_candidate_count']} ready",
        f"- Pattern release correct/wrong rows: {summary['pattern_release_correct_rows']}/{summary['pattern_release_wrong_rows']}",
        f"- Pattern release source: {summary.get('pattern_release_source_path') or 'not_evaluated'}",
        f"- Pattern release source kind: {summary.get('pattern_release_source_kind') or 'not_evaluated'}",
        f"- Label targets: {summary['label_target_count']} total, {summary['high_priority_label_target_count']} high priority",
        f"- Decisive labels still needed: {summary['decisive_rows_needed']} total, {summary['high_priority_decisive_rows_needed']} high priority",
        f"- Label-gap task rows: {summary['label_gap_task_rows']} total, {summary['label_gap_high_priority_task_rows']} high priority",
        f"- Regression gate status: {summary['regression_gate_status']}",
        f"- Regression gate fail/unverified rows: {summary['regression_gate_fail_rows']}/{summary['regression_gate_unverified_rows']}",
        f"- Delivery decision: {summary.get('delivery_decision') or 'not_evaluated'}",
        f"- Delivery output XLSX: {summary.get('delivery_output_xlsx') or 'not_available'}",
        f"- Delivery is rule release: {str(summary.get('delivery_is_rule_release')).lower()}",
        "",
        "## Application Gates",
        "",
    ]
    for name, gate in report.get("application_gates", {}).items():
        blockers = ", ".join(gate.get("blockers") or []) or "none"
        lines.append(
            f"- {name}: status={gate.get('status')}, can_apply_now={gate.get('can_apply_now')}, blockers={blockers}; {gate.get('required_action')}"
        )
    lines.extend(
        [
            "",
            "## Artifacts",
            "",
            f"- Sample XLSX: {report['artifacts'].get('sample_xlsx') or 'not recorded'}",
            f"- Sample CSV: {report['artifacts'].get('sample_csv') or 'not recorded'}",
            f"- Label-gap XLSX: {report['artifacts'].get('label_gap_xlsx') or 'not recorded'}",
            f"- High-priority label-gap XLSX: {report['artifacts'].get('label_gap_high_priority_xlsx') or 'not recorded'}",
            f"- Protected-lane next review XLSX: {report['artifacts'].get('protected_lanes_next_review_task_xlsx') or 'not recorded'}",
            f"- Protected-lane priority review XLSX: {report['artifacts'].get('protected_lanes_priority_task_xlsx') or 'not recorded'}",
            f"- Protected-lane priority handoff MD: {report['artifacts'].get('protected_lanes_priority_task_handoff_md') or 'not recorded'}",
            f"- Regression cases CSV: {report['artifacts'].get('regression_cases_csv') or 'not recorded'}",
            f"- Regression gate report: {report['artifacts'].get('regression_gate_md') or 'not recorded'}",
            f"- Regression overlay XLSX: {report['artifacts'].get('regression_overlay_xlsx') or 'not recorded'}",
            f"- Regression overlay balance JSON: {report['artifacts'].get('regression_overlay_balance_json') or 'not recorded'}",
            "",
            "## Delivery Recommendation",
            "",
            f"- Decision: {report.get('delivery_recommendation', {}).get('decision') or 'not_evaluated'}",
            f"- Output CSV: {report.get('delivery_recommendation', {}).get('output_csv') or 'not_available'}",
            f"- Output XLSX: {report.get('delivery_recommendation', {}).get('output_xlsx') or 'not_available'}",
            f"- Is rule release: {str(report.get('delivery_recommendation', {}).get('is_rule_release')).lower()}",
            f"- Reason: {report.get('delivery_recommendation', {}).get('reason') or 'not_available'}",
            "",
            "## Label Targets",
            "",
        ]
    )
    if report["label_targets"]:
        for target in report["label_targets"]:
            lines.append(
                "- {review_reason} ({priority}, rows={rows}, labeled={labeled_rows}, "
                "decisive={decisive_rows}/{target_decisive_rows}, needed={decisive_rows_needed}, "
                "support={support_rows}, block={blocking_rows}, strength={evidence_strength}, "
                "support_lower80={support_rate_wilson_lower_80}, block_upper80={blocking_rate_wilson_upper_80}, "
                "recommendation={recommendation}): {label_goal}".format(**target)
            )
    else:
        lines.append("- None")
    lines.extend(["", "## Lane Change Candidates", ""])
    if report["lane_change_candidates"]:
        for item in report["lane_change_candidates"]:
            lines.append(
                "- {review_reason} ({status}, decisive={decisive_rows}/{target_decisive_rows}, "
                "strength={evidence_strength}, support_lower80={support_rate_wilson_lower_80}, "
                "block_upper80={blocking_rate_wilson_upper_80}, blocking_gap={blocking_decisive_rows_needed}): "
                "{required_action}".format(**item)
            )
    else:
        lines.append("- None")
    lines.extend(
        [
            "",
            "## Labeling Instructions",
            "",
            "- Fill only manual_decision, manual_url, and notes.",
            "- Use accept when the shown URL is correct, replace with manual_url when another official site is correct, reject when the candidate is wrong or unproven, and unsure when evidence conflicts.",
            "",
            "## Open Requirements",
            "",
        ]
    )
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


def _to_float_or_none(value: object) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


if __name__ == "__main__":
    raise SystemExit(main())
