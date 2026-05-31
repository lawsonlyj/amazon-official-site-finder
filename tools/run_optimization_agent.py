from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from finder.cli import load_dotenv
from tools.llm_agent_client import AgentClientError, AgentConfigurationError, OpenAIJsonClient
from tools.output_layout import WORKFLOW_VERSION, first_existing, optimization_agent_paths


DECISIONS = {"apply_candidate", "block", "needs_more_labels", "needs_simulation", "needs_regression_test"}

SYSTEM_PROMPT = """You are OptimizationAgent for a development-only official website workflow.
Read CheckAgent outputs, human labels, metric reports, and deterministic gate summaries.
You may recommend what should happen next, but you must not directly modify production rules or final results.
Return one JSON object with: overall_decision, should_apply_now, recommendations, blocked_reasons, needed_labels, needed_tests, risk_assessment.
Prefer block or needs_more_labels when evidence is single-case, likely overfit, or deterministic gates do not pass."""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run development-only LLM OptimizationAgent over CheckAgent and gate artifacts.")
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--check-agent-csv")
    parser.add_argument("--human-labels")
    parser.add_argument("--balance-report-json")
    parser.add_argument("--convergence-audit-json")
    parser.add_argument("--application-gates-json")
    parser.add_argument("--output-json")
    parser.add_argument("--output-md")
    parser.add_argument("--model")
    args = parser.parse_args(argv)

    load_dotenv(Path(".env"))
    if args.model:
        import os

        os.environ["FINDER_OPTIMIZATION_AGENT_MODEL"] = args.model
    summary = run_optimization_agent(
        run_dir=args.run_dir,
        check_agent_csv=args.check_agent_csv,
        human_labels=args.human_labels,
        balance_report_json=args.balance_report_json,
        convergence_audit_json=args.convergence_audit_json,
        application_gates_json=args.application_gates_json,
        output_json=args.output_json,
        output_md=args.output_md,
    )
    print(json.dumps(summary["overall"], ensure_ascii=False, indent=2))
    return 0 if summary["overall"].get("status") == "completed" else 2


def run_optimization_agent(
    *,
    run_dir: str | Path,
    check_agent_csv: str | Path | None = None,
    human_labels: str | Path | None = None,
    balance_report_json: str | Path | None = None,
    convergence_audit_json: str | Path | None = None,
    application_gates_json: str | Path | None = None,
    output_json: str | Path | None = None,
    output_md: str | Path | None = None,
    client: Any | None = None,
) -> dict[str, Any]:
    run_dir = Path(run_dir)
    canonical = optimization_agent_paths(run_dir)
    output_json_path = Path(output_json) if output_json else canonical["json"]
    output_md_path = Path(output_md) if output_md else canonical["md"]
    check_path = Path(check_agent_csv) if check_agent_csv else (
        first_existing(run_dir, "development/check_agent/check.csv") or run_dir / "development/check_agent/check.csv"
    )
    human_path = Path(human_labels) if human_labels else first_existing(run_dir, "reviewed/labels.csv", "manual_review_labels.csv")
    balance_path = Path(balance_report_json) if balance_report_json else _find_latest(run_dir, "balance_report.json")
    audit_path = Path(convergence_audit_json) if convergence_audit_json else _find_latest(run_dir, "convergence_audit.json")
    gates_path = Path(application_gates_json) if application_gates_json else _find_latest(run_dir, "calibration_application_gates.json")

    payload = {
        "check_agent": {
            "path": str(check_path),
            "summary": _summarize_check_rows(_read_rows(check_path)),
            "sample_rows": _read_rows(check_path)[:40],
        },
        "human_labels": {
            "path": str(human_path or ""),
            "summary": _summarize_human_labels(_read_rows(human_path) if human_path else []),
        },
        "balance_report": _read_json(balance_path),
        "convergence_audit": _read_json(audit_path),
        "application_gates": _read_json(gates_path),
        "deterministic_policy": {
            "agent_cannot_modify_production": True,
            "operation_can_apply_only_after_gate": True,
            "single_case_changes_are_overfit_risk": True,
        },
    }
    gate_allows = _gate_allows_apply(payload["application_gates"])
    try:
        active_client = client or OpenAIJsonClient.from_env(model_env="FINDER_OPTIMIZATION_AGENT_MODEL")
    except AgentConfigurationError as exc:
        summary = _blocked_summary(
            output_json_path=output_json_path,
            output_md_path=output_md_path,
            reason="missing_openai_api_key",
            detail=str(exc),
            payload=payload,
        )
        return summary
    try:
        raw = active_client.complete_json(system_prompt=SYSTEM_PROMPT, user_payload=payload, max_tokens=1800)
    except AgentClientError as exc:
        summary = _blocked_summary(
            output_json_path=output_json_path,
            output_md_path=output_md_path,
            reason="openai_api_error",
            detail=str(exc),
            payload=payload,
        )
        return summary

    normalized = _normalize_decision(raw, gate_allows=gate_allows)
    summary = {
        "workflow_version": WORKFLOW_VERSION,
        "overall": {
            "status": "completed",
            "overall_decision": normalized["overall_decision"],
            "agent_requested_apply": bool(raw.get("should_apply_now")),
            "deterministic_gate_allows_apply": gate_allows,
            "effective_apply_allowed": normalized["should_apply_now"],
            "recommendation_count": len(normalized["recommendations"]),
            "blocked_reason_count": len(normalized["blocked_reasons"]),
        },
        "decision": normalized,
        "raw_agent_output": raw,
        "inputs": {
            "check_agent_csv": str(check_path),
            "human_labels": str(human_path or ""),
            "balance_report_json": str(balance_path or ""),
            "convergence_audit_json": str(audit_path or ""),
            "application_gates_json": str(gates_path or ""),
        },
        "outputs": {"json": str(output_json_path), "md": str(output_md_path)},
    }
    _write_outputs(output_json_path, output_md_path, summary)
    _update_manifest(run_dir / "manifest.json", summary)
    return summary


def _normalize_decision(raw: dict[str, Any], *, gate_allows: bool) -> dict[str, Any]:
    decision = str(raw.get("overall_decision", "")).strip().lower()
    if decision not in DECISIONS:
        decision = "block"
    blocked_reasons = _listify(raw.get("blocked_reasons"))
    should_apply_now = bool(raw.get("should_apply_now")) and gate_allows and decision == "apply_candidate"
    if bool(raw.get("should_apply_now")) and not gate_allows:
        should_apply_now = False
        if "deterministic_gate_not_passed" not in blocked_reasons:
            blocked_reasons.append("deterministic_gate_not_passed")
        if decision == "apply_candidate":
            decision = "needs_regression_test"
    return {
        "overall_decision": decision,
        "should_apply_now": should_apply_now,
        "recommendations": _list_of_dicts(raw.get("recommendations")),
        "blocked_reasons": blocked_reasons,
        "needed_labels": _listify(raw.get("needed_labels")),
        "needed_tests": _listify(raw.get("needed_tests")),
        "risk_assessment": str(raw.get("risk_assessment", "")).strip(),
    }


def _blocked_summary(
    *,
    output_json_path: Path,
    output_md_path: Path,
    reason: str,
    detail: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    summary = {
        "workflow_version": WORKFLOW_VERSION,
        "overall": {
            "status": "blocked",
            "reason": reason,
            "detail": detail,
            "effective_apply_allowed": False,
        },
        "decision": {
            "overall_decision": "block",
            "should_apply_now": False,
            "recommendations": [],
            "blocked_reasons": [reason],
            "needed_labels": [],
            "needed_tests": [],
            "risk_assessment": detail,
        },
        "inputs": {
            "check_agent_csv": payload["check_agent"]["path"],
            "human_labels": payload["human_labels"]["path"],
        },
        "outputs": {"json": str(output_json_path), "md": str(output_md_path)},
    }
    _write_outputs(output_json_path, output_md_path, summary)
    return summary


def _gate_allows_apply(gates: dict[str, Any]) -> bool:
    if not gates:
        return False
    checks = gates.get("checks") or []
    if checks:
        return any(bool(check.get("allowed") or check.get("can_apply_now")) for check in checks)
    summary = gates.get("summary") or {}
    return int(summary.get("allowed_gate_count") or 0) > 0


def _summarize_check_rows(rows: list[dict[str, str]]) -> dict[str, Any]:
    decisions: dict[str, int] = {}
    for row in rows:
        decision = row.get("check_agent_decision", "")
        decisions[decision] = decisions.get(decision, 0) + 1
    return {"rows": len(rows), "decision_counts": decisions}


def _summarize_human_labels(rows: list[dict[str, str]]) -> dict[str, Any]:
    decisions: dict[str, int] = {}
    for row in rows:
        decision = row.get("manual_decision", "")
        decisions[decision] = decisions.get(decision, 0) + 1
    return {"rows": len(rows), "manual_decision_counts": decisions}


def _read_rows(path: Path | None) -> list[dict[str, str]]:
    if not path or not path.exists():
        return []
    with path.open(newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


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


def _write_outputs(json_path: Path, md_path: Path, summary: dict[str, Any]) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(_render_markdown(summary), encoding="utf-8")


def _render_markdown(summary: dict[str, Any]) -> str:
    overall = summary.get("overall", {})
    decision = summary.get("decision", {})
    lines = [
        "# OptimizationAgent Decision",
        "",
        f"- Status: {overall.get('status', '')}",
        f"- Overall decision: {decision.get('overall_decision', '')}",
        f"- Effective apply allowed: {decision.get('should_apply_now', False)}",
        f"- Blocked reasons: {', '.join(decision.get('blocked_reasons', []))}",
        f"- Needed labels: {', '.join(decision.get('needed_labels', []))}",
        f"- Needed tests: {', '.join(decision.get('needed_tests', []))}",
        "",
        "## Risk Assessment",
        "",
        str(decision.get("risk_assessment", "")),
    ]
    return "\n".join(lines) + "\n"


def _update_manifest(path: Path, summary: dict[str, Any]) -> None:
    if not path.exists():
        return
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["development_optimization_agent"] = summary
    manifest.setdefault("outputs", {}).update(summary.get("outputs", {}))
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def _listify(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    return [text] if text else []


def _list_of_dicts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


if __name__ == "__main__":
    raise SystemExit(main())
