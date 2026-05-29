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
    parser.add_argument("--filled-sample", help="Optional filled calibration sample CSV/XLSX to evaluate in the same cycle.")
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
        filled_sample=args.filled_sample,
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
    filled_sample: str | Path | None = None,
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
    filled_eval_json = out_dir / f"{sample_prefix}_eval_filled.json"
    filled_eval_md = out_dir / f"{sample_prefix}_eval_filled.md"
    filled_eval_csv = out_dir / f"{sample_prefix}_eval_filled_details.csv"
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
    filled_eval = {}
    if filled_sample:
        filled_eval = evaluate_calibration_review_sample(
            sample=filled_sample,
            output_json=filled_eval_json,
            output_md=filled_eval_md,
            output_csv=filled_eval_csv,
        )
    pattern_recommendation_counts = _pattern_recommendation_counts(filled_eval)
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
            "filled_eval_labeled_rows": filled_eval.get("summary", {}).get("labeled_rows") if filled_eval else None,
            "filled_eval_decisive_rows": filled_eval.get("summary", {}).get("decisive_rows") if filled_eval else None,
            "filled_pattern_recommendation_counts": pattern_recommendation_counts,
        },
        "inputs": {
            "labeled_eval_json": str(labeled_eval_json),
            "labeled_agent_b_csv": str(labeled_agent_b_csv),
            "review_csv": str(review_csv),
            "batch_agent_b_csv": str(batch_agent_b_csv),
            "filled_sample": str(filled_sample or ""),
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
            "filled_eval_json": str(filled_eval_json) if filled_sample else "",
            "filled_eval_md": str(filled_eval_md) if filled_sample else "",
            "filled_eval_csv": str(filled_eval_csv) if filled_sample else "",
            "summary_json": str(summary_json),
            "summary_md": str(summary_md),
        },
        "recall_recommendations": recall_report.get("recommendations", []),
        "precision_recommendations": precision_report.get("recommendations", []),
        "sample": sample_summary,
        "empty_evaluation_summary": empty_eval.get("summary", {}),
        "filled_evaluation_summary": filled_eval.get("summary", {}) if filled_eval else {},
        "filled_pattern_recommendations": filled_eval.get("pattern_recommendations", []) if filled_eval else [],
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
        f"- Filled evaluation labeled rows: {summary['filled_eval_labeled_rows']}",
        f"- Filled evaluation decisive rows: {summary['filled_eval_decisive_rows']}",
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
    if report.get("filled_pattern_recommendations"):
        lines.extend(["", "## Filled Pattern Recommendations", ""])
        counts = summary.get("filled_pattern_recommendation_counts", {})
        for key, value in sorted(counts.items()):
            lines.append(f"- {key}: {value}")
        lines.append("")
        for item in report["filled_pattern_recommendations"][:20]:
            lines.append(
                "- {recommendation}: rows={rows}, decisive={decisive}, support={support}, block={block} :: {pattern}".format(
                    recommendation=item.get("recommendation"),
                    rows=item.get("rows"),
                    decisive=item.get("decisive_rows"),
                    support=item.get("supporting_rows"),
                    block=item.get("blocking_rows"),
                    pattern=item.get("pattern"),
                )
            )
    lines.append("")
    return "\n".join(lines)


def _pattern_recommendation_counts(filled_eval: dict) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in filled_eval.get("pattern_recommendations", []) if filled_eval else []:
        recommendation = str(item.get("recommendation") or "")
        if recommendation:
            counts[recommendation] = counts.get(recommendation, 0) + 1
    return counts


if __name__ == "__main__":
    raise SystemExit(main())
