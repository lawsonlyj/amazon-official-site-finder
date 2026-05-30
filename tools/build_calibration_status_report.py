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
    pattern_status = _pattern_release_status(cycle_summary, balance_summary, threshold_summary)
    label_targets = _label_targets(cycle, balance, sample_eval, artifacts)
    lane_status = _lane_status(cycle_summary, balance_summary, sample_summary, label_targets)
    lane_change_candidates = _lane_change_candidates(label_targets, lane_status)
    labeling_instructions = _labeling_instructions()
    workflow_status = _workflow_status(cycle_summary, sample_summary, threshold_status, pattern_status, lane_status)
    open_requirements = _open_requirements(cycle_summary, sample_summary, threshold_status, pattern_status, lane_status)
    next_actions = _next_actions(workflow_status, open_requirements, lane_status, artifacts, label_targets)

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
        },
        "threshold": threshold_status,
        "pattern_release": pattern_status,
        "review_lanes": lane_status,
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


def _pattern_release_status(cycle_summary: dict, balance_summary: dict, threshold_summary: dict) -> dict:
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


def _workflow_status(
    cycle_summary: dict,
    sample_summary: dict,
    threshold_status: dict,
    pattern_status: dict,
    lane_status: dict,
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
    if threshold_status["status"] != "stable_keep_current":
        return "not_converged_threshold_review_needed"
    if lane_status["status"] in {"protected_by_filled_labels", "needs_more_labels"}:
        return "partially_converged_keep_review_lanes"
    if lane_status["status"] == "candidate_for_downgrade" or pattern_status["status"] in {
        "current_guarded_candidate",
        "historical_guarded_candidate",
    }:
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


def _sample_artifacts(cycle: dict, sample_eval_json: str | Path | None) -> dict:
    outputs = cycle.get("outputs", {})
    return {
        "sample_csv": str(outputs.get("sample_csv") or ""),
        "sample_xlsx": str(outputs.get("sample_xlsx") or ""),
        "label_gap_csv": str(outputs.get("label_gap_csv") or ""),
        "label_gap_xlsx": str(outputs.get("label_gap_xlsx") or ""),
        "label_gap_high_priority_csv": str(outputs.get("label_gap_high_priority_csv") or ""),
        "label_gap_high_priority_xlsx": str(outputs.get("label_gap_high_priority_xlsx") or ""),
        "sample_eval_json": str(outputs.get("eval_json") or sample_eval_json or ""),
        "filled_eval_json": str(outputs.get("filled_eval_json") or ""),
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


def _next_actions(
    workflow_status: str,
    open_requirements: list[dict],
    lane_status: dict,
    artifacts: dict | None = None,
    label_targets: list[dict] | None = None,
) -> list[str]:
    artifacts = artifacts or {}
    label_targets = label_targets or []
    if workflow_status == "not_converged_fix_fill_quality":
        fix_actions = [item["action"] for item in open_requirements if item.get("id") == "fix_calibration_fill_quality"]
        return fix_actions or ["Fix calibration fill-quality issues before changing thresholds or review-lane routing."]
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
        return [
            f"Fill {first_target} before changing thresholds or review-lane routing.",
            f"Prioritize high-value labels for: {focus}.",
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
        "",
        "## Artifacts",
        "",
        f"- Sample XLSX: {report['artifacts'].get('sample_xlsx') or 'not recorded'}",
        f"- Sample CSV: {report['artifacts'].get('sample_csv') or 'not recorded'}",
        f"- Label-gap XLSX: {report['artifacts'].get('label_gap_xlsx') or 'not recorded'}",
        f"- High-priority label-gap XLSX: {report['artifacts'].get('label_gap_high_priority_xlsx') or 'not recorded'}",
        "",
        "## Label Targets",
        "",
    ]
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


if __name__ == "__main__":
    raise SystemExit(main())
