from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.output_layout import DEFAULT_SECOND_PASS_ACCEPT_THRESHOLD


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the final threshold/rule policy report from calibration artifacts.")
    parser.add_argument("--baseline-eval-json", required=True)
    parser.add_argument("--calibrated-eval-json", required=True)
    parser.add_argument("--pattern-release-json", required=True)
    parser.add_argument("--balance-report-json")
    parser.add_argument(
        "--batch-application-json",
        action="append",
        default=[],
        help="Output from tools/apply_pattern_release_to_run.py. Repeatable.",
    )
    parser.add_argument("--output-json")
    parser.add_argument("--output-md")
    args = parser.parse_args(argv)

    report = build_release_policy_report(
        baseline_eval_json=args.baseline_eval_json,
        calibrated_eval_json=args.calibrated_eval_json,
        pattern_release_json=args.pattern_release_json,
        balance_report_json=args.balance_report_json,
        batch_application_jsons=args.batch_application_json,
        output_json=args.output_json,
        output_md=args.output_md,
    )
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 0


def build_release_policy_report(
    *,
    baseline_eval_json: str | Path,
    calibrated_eval_json: str | Path,
    pattern_release_json: str | Path,
    balance_report_json: str | Path | None = None,
    batch_application_jsons: list[str | Path] | None = None,
    output_json: str | Path | None = None,
    output_md: str | Path | None = None,
) -> dict:
    baseline = _overall(_read_json(baseline_eval_json))
    calibrated = _overall(_read_json(calibrated_eval_json))
    pattern = _read_json(pattern_release_json)
    balance = _read_json(balance_report_json) if balance_report_json else {}
    batches = [_batch_summary(_read_json(path), path) for path in batch_application_jsons or []]
    deltas = _metric_deltas(baseline, calibrated)
    threshold = _recommended_threshold(balance)
    pattern_summary = pattern.get("summary", {})
    selected_wrong = _to_int(pattern_summary.get("selected_actionable_wrong_release_rows"))
    selected_correct = _to_int(pattern_summary.get("selected_actionable_correct_recovery_rows"))
    raw_agent_b_wrong = _to_int((balance.get("summary") or {}).get("agent_b_recall_release_wrong_rows"))
    raw_agent_b_correct = _to_int((balance.get("summary") or {}).get("agent_b_recall_release_correct_rows"))
    quality_blockers = [batch for batch in batches if not batch.get("quality_passed")]
    unlabelled_released = sum(batch.get("released_rows", 0) for batch in batches if not batch.get("is_labeled_calibration"))

    calibrated_policy = _calibrated_policy(
        deltas=deltas,
        selected_correct=selected_correct,
        selected_wrong=selected_wrong,
        quality_blockers=quality_blockers,
        unlabelled_released=unlabelled_released,
    )
    recommendations = _recommendations(
        threshold=threshold,
        raw_agent_b_correct=raw_agent_b_correct,
        raw_agent_b_wrong=raw_agent_b_wrong,
        calibrated_policy=calibrated_policy,
        batches=batches,
    )
    report = {
        "summary": {
            "recommended_first_pass_threshold": threshold,
            "recommended_second_pass_threshold": DEFAULT_SECOND_PASS_ACCEPT_THRESHOLD,
            "raw_agent_b_recall_release": "manual_only" if raw_agent_b_wrong > 0 else "not_evaluated_or_clean",
            "calibrated_pattern_release": calibrated_policy,
            "baseline_accuracy": baseline.get("overall_accuracy"),
            "calibrated_accuracy": calibrated.get("overall_accuracy"),
            "accuracy_delta": deltas.get("overall_accuracy"),
            "baseline_false_official_rows": baseline.get("false_official_rows"),
            "calibrated_false_official_rows": calibrated.get("false_official_rows"),
            "false_official_delta": deltas.get("false_official_rows"),
            "baseline_over_rejected_rows": baseline.get("over_rejected_rows"),
            "calibrated_over_rejected_rows": calibrated.get("over_rejected_rows"),
            "over_rejected_delta": deltas.get("over_rejected_rows"),
            "selected_actionable_patterns": pattern_summary.get("selected_actionable_pattern_count"),
            "selected_actionable_correct_rows": selected_correct,
            "selected_actionable_wrong_rows": selected_wrong,
            "unlabeled_batch_released_rows": unlabelled_released,
            "batch_count": len(batches),
        },
        "baseline_overall": baseline,
        "calibrated_overall": calibrated,
        "deltas": deltas,
        "pattern_release_summary": pattern_summary,
        "balance_summary": balance.get("summary", {}),
        "batch_applications": batches,
        "recommendations": recommendations,
        "inputs": {
            "baseline_eval_json": str(baseline_eval_json),
            "calibrated_eval_json": str(calibrated_eval_json),
            "pattern_release_json": str(pattern_release_json),
            "balance_report_json": str(balance_report_json or ""),
            "batch_application_jsons": [str(path) for path in batch_application_jsons or []],
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


def _recommended_threshold(balance: dict) -> int:
    value = (balance.get("summary") or {}).get("recommended_threshold")
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return DEFAULT_SECOND_PASS_ACCEPT_THRESHOLD


def _calibrated_policy(
    *,
    deltas: dict,
    selected_correct: int,
    selected_wrong: int,
    quality_blockers: list[dict],
    unlabelled_released: int,
) -> str:
    if quality_blockers:
        return "disabled_quality_blocker"
    if selected_wrong > 0:
        return "disabled_labeled_wrong_release"
    if selected_correct <= 0:
        return "manual_only_no_labeled_gain"
    if (deltas.get("false_official_rows") or 0) > 0:
        return "disabled_false_official_increase"
    if (deltas.get("overall_accuracy") or 0) <= 0:
        return "manual_only_no_accuracy_gain"
    if unlabelled_released:
        return "enabled_with_guard_and_spot_check"
    return "enabled_with_guard_no_batch_release"


def _recommendations(
    *,
    threshold: int,
    raw_agent_b_correct: int,
    raw_agent_b_wrong: int,
    calibrated_policy: str,
    batches: list[dict],
) -> list[str]:
    out = [
        f"Keep first-pass and second-pass accept thresholds at {threshold}; do not globally lower thresholds.",
    ]
    if raw_agent_b_wrong > 0:
        out.append(
            f"Do not auto-release unresolved rows by raw Check and Suggestion score: the labeled simulation recovers {raw_agent_b_correct} correct row(s) but releases {raw_agent_b_wrong} wrong row(s)."
        )
    if calibrated_policy.startswith("enabled_with_guard"):
        out.append(
            "Use calibrated selected actionable pattern release only with the risky-subdomain guard and keep released rows in the manual-review spot-check lane."
        )
    elif calibrated_policy.startswith("manual_only"):
        out.append("Keep calibrated pattern matches as manual-review evidence until more labels show a clean gain.")
    else:
        out.append("Disable calibrated pattern release until the blocking condition is resolved.")
    if any(batch.get("released_rows", 0) == 0 for batch in batches):
        out.append("When a larger batch has no guarded release candidates, treat that as convergence evidence, not a reason to relax the guard.")
    out.append("Next useful labels should focus on unresolved recall candidates and precision risks, especially candidates blocked only by docs/help/support/app subdomain rules.")
    return out


def _batch_summary(data: dict, path: str | Path) -> dict:
    run_dir = str(data.get("run_dir", ""))
    return {
        "path": str(path),
        "run_dir": run_dir,
        "released_rows": _to_int(data.get("released_rows")),
        "official_url_rows": _to_int(data.get("official_url_rows")),
        "unresolved_rows": _to_int(data.get("unresolved_rows")),
        "quality_passed": bool(data.get("quality_passed")),
        "status_counts": data.get("status_counts", {}),
        "released_provider_names": data.get("released_provider_names", []),
        "released_domains": data.get("released_domains", {}),
        "is_labeled_calibration": "100" in run_dir or "balance_tuned" in run_dir,
    }


def _metric_deltas(baseline: dict, calibrated: dict) -> dict:
    keys = [
        "overall_accuracy",
        "auto_precision",
        "official_recall",
        "false_official_rows",
        "over_rejected_rows",
        "official_output_rows",
        "correct_official_rows",
    ]
    return {key: _delta(calibrated.get(key), baseline.get(key)) for key in keys}


def _overall(data: dict) -> dict:
    return data.get("overall", data)


def _read_json(path: str | Path | None) -> dict:
    if not path:
        return {}
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _render_markdown(report: dict) -> str:
    summary = report["summary"]
    lines = [
        "# Release Policy Report",
        "",
        "## Decision",
        "",
    ]
    for item in report["recommendations"]:
        lines.append(f"- {item}")
    lines.extend(
        [
            "",
            "## Metrics",
            "",
            f"- First-pass threshold: {summary['recommended_first_pass_threshold']}",
            f"- Second-pass threshold: {summary['recommended_second_pass_threshold']}",
            f"- Calibrated pattern release: {summary['calibrated_pattern_release']}",
            f"- Accuracy: {summary['baseline_accuracy']} -> {summary['calibrated_accuracy']} ({summary['accuracy_delta']:+})",
            f"- False official rows: {summary['baseline_false_official_rows']} -> {summary['calibrated_false_official_rows']} ({summary['false_official_delta']:+})",
            f"- Over-rejected rows: {summary['baseline_over_rejected_rows']} -> {summary['calibrated_over_rejected_rows']} ({summary['over_rejected_delta']:+})",
            f"- Selected actionable correct/wrong rows: {summary['selected_actionable_correct_rows']}/{summary['selected_actionable_wrong_rows']}",
            f"- Unlabeled batch released rows: {summary['unlabeled_batch_released_rows']}",
            "",
            "## Batch Applications",
            "",
        ]
    )
    for batch in report["batch_applications"]:
        lines.append(
            "- {run_dir}: released={released}, official={official}, unresolved={unresolved}, quality={quality}".format(
                run_dir=batch.get("run_dir"),
                released=batch.get("released_rows"),
                official=batch.get("official_url_rows"),
                unresolved=batch.get("unresolved_rows"),
                quality=batch.get("quality_passed"),
            )
        )
    lines.append("")
    return "\n".join(lines)


def _to_int(value: object) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def _delta(value: object, baseline: object) -> float | int | None:
    if value is None or baseline is None:
        return None
    try:
        left = float(value)
        right = float(baseline)
    except (TypeError, ValueError):
        return None
    result = round(left - right, 4)
    if result.is_integer():
        return int(result)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
