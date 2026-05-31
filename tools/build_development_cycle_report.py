from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.output_layout import WORKFLOW_VERSION, development_cycle_paths, first_existing


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write one Development Workflow cycle metrics report.")
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--cycle", required=True)
    parser.add_argument("--labeled-eval-json")
    parser.add_argument("--check-agent-summary")
    parser.add_argument("--optimization-decision-json")
    parser.add_argument("--application-gates-json")
    parser.add_argument("--output-json")
    parser.add_argument("--output-md")
    args = parser.parse_args(argv)
    report = build_development_cycle_report(
        run_dir=args.run_dir,
        cycle=args.cycle,
        labeled_eval_json=args.labeled_eval_json,
        check_agent_summary=args.check_agent_summary,
        optimization_decision_json=args.optimization_decision_json,
        application_gates_json=args.application_gates_json,
        output_json=args.output_json,
        output_md=args.output_md,
    )
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 0


def build_development_cycle_report(
    *,
    run_dir: str | Path,
    cycle: int | str,
    labeled_eval_json: str | Path | None = None,
    check_agent_summary: str | Path | None = None,
    optimization_decision_json: str | Path | None = None,
    application_gates_json: str | Path | None = None,
    output_json: str | Path | None = None,
    output_md: str | Path | None = None,
) -> dict[str, Any]:
    run_dir = Path(run_dir)
    paths = development_cycle_paths(run_dir, cycle)
    output_json_path = Path(output_json) if output_json else paths["json"]
    output_md_path = Path(output_md) if output_md else paths["md"]
    labeled_path = Path(labeled_eval_json) if labeled_eval_json else first_existing(run_dir, "balance_eval_labeled100_latest.json")
    check_path = Path(check_agent_summary) if check_agent_summary else first_existing(run_dir, "development/check_agent/summary.json")
    optimization_path = Path(optimization_decision_json) if optimization_decision_json else first_existing(run_dir, "development/optimization_agent/decision.json")
    gates_path = Path(application_gates_json) if application_gates_json else _find_latest(run_dir, "calibration_application_gates.json")
    labeled = _read_json(labeled_path)
    check = _read_json(check_path)
    optimization = _read_json(optimization_path)
    gates = _read_json(gates_path)
    overall = labeled.get("overall") or labeled.get("labeled_overall") or labeled.get("summary", {})
    opt_overall = optimization.get("overall", {})
    gate_summary = gates.get("summary", {})
    summary = {
        "cycle": str(cycle),
        "labeled_rows": overall.get("labeled_rows"),
        "auto_precision": overall.get("auto_precision"),
        "official_recall": overall.get("official_recall"),
        "overall_accuracy": overall.get("overall_accuracy"),
        "false_official_rows": overall.get("false_official_rows"),
        "over_rejected_rows": overall.get("over_rejected_rows"),
        "manual_review_rows": overall.get("manual_review_rows"),
        "check_agent_status": check.get("status", ""),
        "check_agent_rows": check.get("output_rows") or check.get("completed_rows") or 0,
        "check_agent_decision_counts": check.get("decision_counts", {}),
        "optimization_status": opt_overall.get("status", ""),
        "optimization_decision": opt_overall.get("overall_decision") or (optimization.get("decision") or {}).get("overall_decision", ""),
        "optimization_effective_apply_allowed": opt_overall.get("effective_apply_allowed", False),
        "gate_allowed_count": gate_summary.get("allowed_gate_count", 0),
        "gate_blocked_count": gate_summary.get("not_allowed_gate_count", 0),
    }
    report = {
        "workflow_version": WORKFLOW_VERSION,
        "summary": summary,
        "inputs": {
            "labeled_eval_json": str(labeled_path or ""),
            "check_agent_summary": str(check_path or ""),
            "optimization_decision_json": str(optimization_path or ""),
            "application_gates_json": str(gates_path or ""),
        },
        "outputs": {"json": str(output_json_path), "md": str(output_md_path)},
    }
    output_json_path.parent.mkdir(parents=True, exist_ok=True)
    output_md_path.parent.mkdir(parents=True, exist_ok=True)
    output_json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    output_md_path.write_text(_render_markdown(report), encoding="utf-8")
    return report


def _read_json(path: Path | None) -> dict[str, Any]:
    if not path or not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _find_latest(run_dir: Path, filename: str) -> Path | None:
    matches = [path for path in run_dir.rglob(filename) if path.is_file()]
    if not matches:
        return None
    return max(matches, key=lambda path: path.stat().st_mtime)


def _render_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    return "\n".join(
        [
            f"# Development Workflow Cycle {summary.get('cycle')}",
            "",
            f"- Precision: {summary.get('auto_precision')}",
            f"- Recall: {summary.get('official_recall')}",
            f"- Accuracy: {summary.get('overall_accuracy')}",
            f"- False official rows: {summary.get('false_official_rows')}",
            f"- Over-rejected rows: {summary.get('over_rejected_rows')}",
            f"- Manual review rows: {summary.get('manual_review_rows')}",
            f"- CheckAgent status: {summary.get('check_agent_status')}",
            f"- CheckAgent rows: {summary.get('check_agent_rows')}",
            f"- OptimizationAgent decision: {summary.get('optimization_decision')}",
            f"- Effective apply allowed: {summary.get('optimization_effective_apply_allowed')}",
            f"- Gate allowed/blocked: {summary.get('gate_allowed_count')}/{summary.get('gate_blocked_count')}",
        ]
    ) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
