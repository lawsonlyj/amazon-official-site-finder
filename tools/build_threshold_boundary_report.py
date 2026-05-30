from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.output_layout import DEFAULT_SECOND_PASS_ACCEPT_THRESHOLD


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build an accept/review/release boundary report from calibration metrics.")
    parser.add_argument("--labeled-eval-json", required=True)
    parser.add_argument("--pattern-release-json")
    parser.add_argument("--policy-report-json")
    parser.add_argument("--output-json")
    parser.add_argument("--output-md")
    args = parser.parse_args(argv)

    report = build_threshold_boundary_report(
        labeled_eval_json=args.labeled_eval_json,
        pattern_release_json=args.pattern_release_json,
        policy_report_json=args.policy_report_json,
        output_json=args.output_json,
        output_md=args.output_md,
    )
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 0


def build_threshold_boundary_report(
    *,
    labeled_eval_json: str | Path,
    pattern_release_json: str | Path | None = None,
    policy_report_json: str | Path | None = None,
    output_json: str | Path | None = None,
    output_md: str | Path | None = None,
) -> dict:
    labeled = _read_json(labeled_eval_json)
    policy = _read_json(policy_report_json) if policy_report_json else {}
    pattern = _read_json(pattern_release_json) if pattern_release_json else {}
    thresholds = [_normalize_threshold_row(row) for row in labeled.get("threshold_simulations", [])]
    thresholds = [row for row in thresholds if row.get("threshold") is not None]
    if not thresholds:
        overall = labeled.get("overall", {})
        thresholds = [_normalize_threshold_row({**overall, "threshold": DEFAULT_SECOND_PASS_ACCEPT_THRESHOLD})]
    thresholds.sort(key=lambda row: row["threshold"])

    current_threshold = _current_threshold(policy, thresholds)
    current = _threshold_by_value(thresholds, current_threshold) or thresholds[0]
    best = _best_threshold(thresholds)
    precision_boundary = _precision_boundary(thresholds, current)
    review_band = _review_band(current, precision_boundary)
    recall_release = _raw_agent_b_release(labeled.get("agent_b_recall_release_simulations", []), policy)
    pattern_boundary = _pattern_boundary(pattern, policy)
    summary = {
        "recommended_global_accept_threshold": current_threshold,
        "recommended_second_pass_threshold": _to_int(
            (policy.get("summary") or {}).get("recommended_second_pass_threshold"),
            DEFAULT_SECOND_PASS_ACCEPT_THRESHOLD,
        ),
        "best_labeled_accuracy_threshold": best.get("threshold"),
        "best_labeled_accuracy": best.get("overall_accuracy"),
        "current_labeled_accuracy": current.get("overall_accuracy"),
        "precision_watch_min": review_band.get("min"),
        "precision_watch_max": review_band.get("max"),
        "global_threshold_change": _global_threshold_change(current, best),
        "raw_agent_b_recall_release": recall_release.get("recommendation"),
        "calibrated_pattern_release": pattern_boundary.get("recommendation"),
        "selected_actionable_correct_rows": pattern_boundary.get("correct_recovery_rows"),
        "selected_actionable_wrong_rows": pattern_boundary.get("wrong_release_rows"),
    }
    report = {
        "summary": summary,
        "thresholds": {
            "current": current,
            "best_by_labeled_accuracy": best,
            "simulations": thresholds,
            "precision_boundary": precision_boundary,
            "review_band": review_band,
        },
        "agent_b_recall_release": recall_release,
        "pattern_release": pattern_boundary,
        "recommendations": _recommendations(summary, current, best, precision_boundary, recall_release, pattern_boundary),
        "inputs": {
            "labeled_eval_json": str(labeled_eval_json),
            "pattern_release_json": str(pattern_release_json or ""),
            "policy_report_json": str(policy_report_json or ""),
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


def _current_threshold(policy: dict, thresholds: list[dict]) -> int:
    policy_threshold = _to_int((policy.get("summary") or {}).get("recommended_first_pass_threshold"), 0)
    if policy_threshold:
        return policy_threshold
    if thresholds:
        return _to_int(thresholds[0].get("threshold"), DEFAULT_SECOND_PASS_ACCEPT_THRESHOLD)
    return DEFAULT_SECOND_PASS_ACCEPT_THRESHOLD


def _normalize_threshold_row(row: dict) -> dict:
    return {
        "threshold": _to_int(row.get("threshold"), None),
        "overall_accuracy": _to_float(row.get("overall_accuracy")),
        "auto_precision": _to_float(row.get("auto_precision")),
        "official_recall": _to_float(row.get("official_recall")),
        "false_official_rows": _to_int(row.get("false_official_rows"), 0),
        "over_rejected_rows": _to_int(row.get("over_rejected_rows"), 0),
        "official_output_rows": _to_int(row.get("official_output_rows"), 0),
        "correct_official_rows": _to_int(row.get("correct_official_rows"), 0),
    }


def _best_threshold(thresholds: list[dict]) -> dict:
    return max(
        thresholds,
        key=lambda row: (
            row.get("overall_accuracy") or 0,
            row.get("official_recall") or 0,
            -(row.get("false_official_rows") or 0),
            -(row.get("threshold") or 999),
        ),
    )


def _precision_boundary(thresholds: list[dict], current: dict) -> dict:
    candidates = []
    current_false = current.get("false_official_rows") or 0
    current_accuracy = current.get("overall_accuracy") or 0
    for row in thresholds:
        if row.get("threshold") == current.get("threshold"):
            continue
        if (row.get("false_official_rows") or 0) >= current_false:
            continue
        tradeoff = _tradeoff(row, current)
        tradeoff["recommended_use"] = (
            "review_lane_only"
            if (row.get("overall_accuracy") or 0) <= current_accuracy
            else "global_threshold_candidate"
        )
        candidates.append(tradeoff)
    if not candidates:
        return {
            "threshold": None,
            "recommended_use": "none",
            "reason": "No simulated threshold reduced false official rows.",
            "candidates": [],
        }
    candidates.sort(
        key=lambda row: (
            row["recommended_use"] != "global_threshold_candidate",
            -(row.get("accuracy_delta") or 0),
            row.get("over_rejected_delta") or 0,
            row.get("threshold") or 999,
        )
    )
    chosen = candidates[0]
    return {
        **chosen,
        "reason": (
            "Use this as a review boundary, not a global accept threshold, when it reduces false officials but does not improve labeled accuracy."
            if chosen["recommended_use"] == "review_lane_only"
            else "This threshold improves labeled accuracy and can be considered as a global candidate."
        ),
        "candidates": candidates,
    }


def _review_band(current: dict, precision_boundary: dict) -> dict:
    threshold = _to_int(precision_boundary.get("threshold"), 0)
    current_threshold = _to_int(current.get("threshold"), DEFAULT_SECOND_PASS_ACCEPT_THRESHOLD)
    if not threshold or threshold <= current_threshold:
        return {
            "min": current_threshold,
            "max": current_threshold + 9,
            "reason": "No narrower labeled precision boundary exists; keep the default high-risk review band above the accept threshold.",
        }
    return {
        "min": current_threshold,
        "max": threshold - 1,
        "reason": "Rows accepted in this band should remain accepted but prioritized for AgentB/manual precision checks.",
    }


def _raw_agent_b_release(simulations: list[dict], policy: dict) -> dict:
    policy_value = (policy.get("summary") or {}).get("raw_agent_b_recall_release")
    zero_wrong = [
        row
        for row in simulations
        if _to_int(row.get("wrong_release_rows"), 0) == 0 and _to_int(row.get("correct_recovery_rows"), 0) > 0
    ]
    if zero_wrong:
        chosen = max(zero_wrong, key=lambda row: (_to_int(row.get("correct_recovery_rows"), 0), -_to_int(row.get("agent_b_evidence_threshold"), 999)))
        return {
            "recommendation": "narrow_threshold_candidate",
            "threshold": _to_int(chosen.get("agent_b_evidence_threshold"), 0),
            "correct_recovery_rows": _to_int(chosen.get("correct_recovery_rows"), 0),
            "wrong_release_rows": 0,
            "reason": "At least one AgentB evidence threshold had labeled recall gain without wrong releases.",
        }
    if not simulations:
        return {
            "recommendation": policy_value or "not_evaluated",
            "threshold": None,
            "correct_recovery_rows": 0,
            "wrong_release_rows": 0,
            "reason": "No AgentB recall-release simulation data.",
        }
    best = max(
        simulations,
        key=lambda row: (
            _to_float(row.get("release_precision")) or 0,
            _to_int(row.get("correct_recovery_rows"), 0),
            -_to_int(row.get("wrong_release_rows"), 0),
        ),
    )
    return {
        "recommendation": "manual_only",
        "threshold": _to_int(best.get("agent_b_evidence_threshold"), 0),
        "correct_recovery_rows": _to_int(best.get("correct_recovery_rows"), 0),
        "wrong_release_rows": _to_int(best.get("wrong_release_rows"), 0),
        "reason": "Every simulated raw AgentB recall-release threshold released at least one labeled wrong candidate.",
    }


def _pattern_boundary(pattern: dict, policy: dict) -> dict:
    summary = pattern.get("summary") or {}
    policy_value = (policy.get("summary") or {}).get("calibrated_pattern_release")
    correct = _to_int(summary.get("selected_actionable_correct_recovery_rows"), 0)
    wrong = _to_int(summary.get("selected_actionable_wrong_release_rows"), 0)
    pattern_count = _to_int(summary.get("selected_actionable_pattern_count"), 0)
    if pattern_count and correct > 0 and wrong == 0:
        recommendation = policy_value or "enabled_with_guard"
        reason = "Selected actionable evidence patterns recovered labeled official sites with zero labeled wrong releases."
    elif pattern_count and wrong > 0:
        recommendation = "disabled_labeled_wrong_release"
        reason = "Selected actionable evidence patterns released labeled wrong candidates."
    elif pattern_count:
        recommendation = "manual_only_no_labeled_gain"
        reason = "Selected actionable evidence patterns did not recover labeled official sites."
    else:
        recommendation = policy_value or "not_evaluated"
        reason = "No selected actionable pattern-release set was available."
    return {
        "recommendation": recommendation,
        "pattern_count": pattern_count,
        "correct_recovery_rows": correct,
        "wrong_release_rows": wrong,
        "accuracy": summary.get("selected_actionable_accuracy"),
        "auto_precision": summary.get("selected_actionable_auto_precision"),
        "official_recall": summary.get("selected_actionable_official_recall"),
        "reason": reason,
        "patterns": [
            {"pattern": item.get("pattern", ""), "features": item.get("features", [])}
            for item in pattern.get("selected_actionable_pattern_set", [])
        ],
    }


def _global_threshold_change(current: dict, best: dict) -> str:
    if best.get("threshold") == current.get("threshold"):
        return "keep_current"
    if (best.get("overall_accuracy") or 0) <= (current.get("overall_accuracy") or 0):
        return "keep_current"
    return "candidate_change"


def _recommendations(
    summary: dict,
    current: dict,
    best: dict,
    precision_boundary: dict,
    recall_release: dict,
    pattern_boundary: dict,
) -> list[str]:
    out = [
        f"Keep the global accept threshold at {summary['recommended_global_accept_threshold']} unless new labels beat the current labeled accuracy and recall.",
    ]
    if best.get("threshold") != current.get("threshold"):
        out.append(
            "A different threshold has similar or better precision tradeoffs, but it should not replace the global threshold unless it improves labeled accuracy without a recall loss."
        )
    if precision_boundary.get("threshold"):
        out.append(
            f"Use scores {summary['precision_watch_min']}-{summary['precision_watch_max']} as the high-value precision review band; raising the global threshold to {precision_boundary['threshold']} is a review-lane signal, not a default accept rule."
        )
    if recall_release.get("recommendation") == "manual_only":
        out.append("Do not auto-release unresolved rows from raw AgentB recall candidates; keep them as manual or pattern-mined evidence.")
    if str(pattern_boundary.get("recommendation", "")).startswith("enabled_with_guard"):
        out.append("Allow only selected actionable pattern release with the risky-subdomain guard and manual spot checks.")
    return out


def _tradeoff(row: dict, current: dict) -> dict:
    return {
        "threshold": row.get("threshold"),
        "accuracy_delta": _delta(row.get("overall_accuracy"), current.get("overall_accuracy")),
        "precision_delta": _delta(row.get("auto_precision"), current.get("auto_precision")),
        "recall_delta": _delta(row.get("official_recall"), current.get("official_recall")),
        "false_official_delta": (row.get("false_official_rows") or 0) - (current.get("false_official_rows") or 0),
        "over_rejected_delta": (row.get("over_rejected_rows") or 0) - (current.get("over_rejected_rows") or 0),
        "row": row,
    }


def _threshold_by_value(thresholds: list[dict], value: int) -> dict | None:
    for row in thresholds:
        if row.get("threshold") == value:
            return row
    return None


def _render_markdown(report: dict) -> str:
    summary = report["summary"]
    lines = [
        "# Threshold Boundary Report",
        "",
        "## Decision",
        "",
    ]
    for item in report["recommendations"]:
        lines.append(f"- {item}")
    lines.extend(
        [
            "",
            "## Summary",
            "",
            f"- Global accept threshold: {summary['recommended_global_accept_threshold']}",
            f"- Second-pass threshold: {summary['recommended_second_pass_threshold']}",
            f"- Best labeled-accuracy threshold: {summary['best_labeled_accuracy_threshold']}",
            f"- Precision review band: {summary['precision_watch_min']}-{summary['precision_watch_max']}",
            f"- Raw AgentB recall release: {summary['raw_agent_b_recall_release']}",
            f"- Calibrated pattern release: {summary['calibrated_pattern_release']}",
            f"- Selected actionable correct/wrong rows: {summary['selected_actionable_correct_rows']}/{summary['selected_actionable_wrong_rows']}",
            "",
            "## Threshold Simulations",
            "",
        ]
    )
    for row in report["thresholds"]["simulations"]:
        lines.append(
            "- threshold={threshold}: accuracy={accuracy}, precision={precision}, recall={recall}, false_official={false}, over_rejected={over}".format(
                threshold=row.get("threshold"),
                accuracy=row.get("overall_accuracy"),
                precision=row.get("auto_precision"),
                recall=row.get("official_recall"),
                false=row.get("false_official_rows"),
                over=row.get("over_rejected_rows"),
            )
        )
    lines.append("")
    return "\n".join(lines)


def _read_json(path: str | Path | None) -> dict:
    if not path:
        return {}
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _to_int(value: object, default: int | None = 0) -> int | None:
    if value is None or value == "":
        return default
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _to_float(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        return round(float(value), 4)
    except (TypeError, ValueError):
        return None


def _delta(value: object, baseline: object) -> float | int | None:
    left = _to_float(value)
    right = _to_float(baseline)
    if left is None or right is None:
        return None
    result = round(left - right, 4)
    if result.is_integer():
        return int(result)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
