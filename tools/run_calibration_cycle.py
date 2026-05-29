from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.build_calibration_review_sample import build_calibration_review_sample
from tools.evaluate_calibration_review_sample import evaluate_calibration_review_sample
from tools.mine_evidence_patterns import mine_evidence_patterns


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the repeatable calibration-material generation cycle.")
    parser.add_argument("--labeled-eval-json", required=True, help="Labeled balance JSON from evaluate_workflow_balance.py.")
    parser.add_argument("--labeled-agent-b-csv", required=True, help="AgentB check.csv for the labeled calibration run.")
    parser.add_argument("--review-csv", required=True, help="Target batch review_task.csv.")
    parser.add_argument("--batch-agent-b-csv", required=True, help="Target batch agent_b/check.csv.")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--sample-prefix", default="pattern_validation_sample_50")
    parser.add_argument("--max-rows", type=int, default=50)
    parser.add_argument("--max-per-reason", type=int, default=12)
    parser.add_argument("--max-per-pattern", type=int, default=5)
    parser.add_argument("--min-support", type=int, default=2)
    parser.add_argument("--max-pattern-size", type=int, default=3)
    args = parser.parse_args(argv)

    report = run_calibration_cycle(
        labeled_eval_json=args.labeled_eval_json,
        labeled_agent_b_csv=args.labeled_agent_b_csv,
        review_csv=args.review_csv,
        batch_agent_b_csv=args.batch_agent_b_csv,
        output_dir=args.output_dir,
        sample_prefix=args.sample_prefix,
        max_rows=args.max_rows,
        max_per_reason=args.max_per_reason,
        max_per_pattern=args.max_per_pattern,
        min_support=args.min_support,
        max_pattern_size=args.max_pattern_size,
    )
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 0


def run_calibration_cycle(
    *,
    labeled_eval_json: str | Path,
    labeled_agent_b_csv: str | Path,
    review_csv: str | Path,
    batch_agent_b_csv: str | Path,
    output_dir: str | Path,
    sample_prefix: str = "pattern_validation_sample_50",
    max_rows: int = 50,
    max_per_reason: int = 12,
    max_per_pattern: int = 5,
    min_support: int = 2,
    max_pattern_size: int = 3,
) -> dict:
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    recall_json = out_dir / "evidence_patterns_recall.json"
    recall_md = out_dir / "evidence_patterns_recall.md"
    precision_json = out_dir / "evidence_patterns_precision.json"
    precision_md = out_dir / "evidence_patterns_precision.md"
    sample_csv = out_dir / f"{sample_prefix}.csv"
    sample_xlsx = out_dir / f"{sample_prefix}.xlsx"
    eval_json = out_dir / f"{sample_prefix}_eval_empty.json"
    eval_md = out_dir / f"{sample_prefix}_eval_empty.md"
    eval_csv = out_dir / f"{sample_prefix}_eval_empty_details.csv"
    summary_json = out_dir / "calibration_cycle_summary.json"
    summary_md = out_dir / "calibration_cycle_summary.md"

    recall_report = mine_evidence_patterns(
        balance_json=labeled_eval_json,
        agent_b_csv=labeled_agent_b_csv,
        scope="recall",
        max_pattern_size=max_pattern_size,
        min_support=min_support,
        output_json=recall_json,
        output_md=recall_md,
    )
    precision_report = mine_evidence_patterns(
        balance_json=labeled_eval_json,
        agent_b_csv=labeled_agent_b_csv,
        scope="precision",
        max_pattern_size=max_pattern_size,
        min_support=min_support,
        output_json=precision_json,
        output_md=precision_md,
    )
    sample_summary = build_calibration_review_sample(
        review_csv=review_csv,
        agent_b_csv=batch_agent_b_csv,
        output_csv=sample_csv,
        output_xlsx=sample_xlsx,
        max_rows=max_rows,
        max_per_reason=max_per_reason,
        max_per_pattern=max_per_pattern,
        pattern_jsons=[recall_json, precision_json],
    )
    empty_eval = evaluate_calibration_review_sample(
        sample=sample_xlsx,
        output_json=eval_json,
        output_md=eval_md,
        output_csv=eval_csv,
    )
    report = {
        "summary": {
            "recall_durable_safe_patterns": recall_report["summary"].get("durable_safe_patterns"),
            "precision_durable_safe_patterns": precision_report["summary"].get("durable_safe_patterns"),
            "sample_rows": sample_summary.get("sample_rows"),
            "pattern_validation_rows": sample_summary.get("sample_reason_counts", {}).get("pattern_candidate_validation", 0),
            "pattern_control_rows": sample_summary.get("sample_reason_counts", {}).get("pattern_control_validation", 0),
            "timeout_rows": sample_summary.get("sample_reason_counts", {}).get("timeout_needs_manual", 0),
            "pattern_count": len(sample_summary.get("pattern_match_counts", {})),
            "max_per_pattern": sample_summary.get("max_per_pattern"),
            "empty_eval_labeled_rows": empty_eval["summary"].get("labeled_rows"),
        },
        "inputs": {
            "labeled_eval_json": str(labeled_eval_json),
            "labeled_agent_b_csv": str(labeled_agent_b_csv),
            "review_csv": str(review_csv),
            "batch_agent_b_csv": str(batch_agent_b_csv),
        },
        "outputs": {
            "recall_json": str(recall_json),
            "recall_md": str(recall_md),
            "precision_json": str(precision_json),
            "precision_md": str(precision_md),
            "sample_csv": str(sample_csv),
            "sample_xlsx": str(sample_xlsx),
            "eval_json": str(eval_json),
            "eval_md": str(eval_md),
            "eval_csv": str(eval_csv),
            "summary_json": str(summary_json),
            "summary_md": str(summary_md),
        },
        "recall_recommendations": recall_report.get("recommendations", []),
        "precision_recommendations": precision_report.get("recommendations", []),
        "sample": sample_summary,
        "empty_evaluation_summary": empty_eval.get("summary", {}),
    }
    summary_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    summary_md.write_text(_render_markdown(report), encoding="utf-8")
    return report


def _render_markdown(report: dict) -> str:
    summary = report["summary"]
    lines = [
        "# Calibration Cycle Summary",
        "",
        "## Summary",
        "",
        f"- Recall durable safe patterns: {summary['recall_durable_safe_patterns']}",
        f"- Precision durable safe patterns: {summary['precision_durable_safe_patterns']}",
        f"- Sample rows: {summary['sample_rows']}",
        f"- Pattern candidate validation rows: {summary['pattern_validation_rows']}",
        f"- Pattern control validation rows: {summary['pattern_control_rows']}",
        f"- Timeout rows: {summary['timeout_rows']}",
        f"- Pattern count: {summary['pattern_count']}",
        f"- Max per pattern: {summary['max_per_pattern']}",
        f"- Empty evaluation labeled rows: {summary['empty_eval_labeled_rows']}",
        "",
        "## Outputs",
        "",
    ]
    for key, value in report["outputs"].items():
        lines.append(f"- {key}: {value}")
    lines.extend(["", "## Recall Recommendations", ""])
    for item in report.get("recall_recommendations", []):
        lines.append(f"- {item}")
    lines.extend(["", "## Precision Recommendations", ""])
    for item in report.get("precision_recommendations", []):
        lines.append(f"- {item}")
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
