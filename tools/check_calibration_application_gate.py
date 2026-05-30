from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


APPLICATION_GATES = {
    "global_threshold_change",
    "review_lane_change",
    "pattern_release_change",
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check whether a calibration application gate allows a workflow change.")
    parser.add_argument("--status-json", required=True, help="calibration_status.json from run_calibration_cycle.py.")
    parser.add_argument("--gate", required=True, choices=sorted(APPLICATION_GATES), help="Application gate to evaluate.")
    parser.add_argument(
        "--allow-candidate",
        action="store_true",
        help="Allow candidate gates with no blockers for a controlled manual/regression-covered rollout.",
    )
    parser.add_argument("--output-json")
    parser.add_argument("--output-md")
    args = parser.parse_args(argv)

    report = check_calibration_application_gate(
        status_json=args.status_json,
        gate=args.gate,
        allow_candidate=args.allow_candidate,
        output_json=args.output_json,
        output_md=args.output_md,
    )
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 0 if report["summary"]["allowed"] else 1


def check_calibration_application_gate(
    *,
    status_json: str | Path,
    gate: str,
    allow_candidate: bool = False,
    output_json: str | Path | None = None,
    output_md: str | Path | None = None,
) -> dict:
    if gate not in APPLICATION_GATES:
        raise ValueError(f"Unsupported application gate: {gate}")
    status_path = Path(status_json)
    status = json.loads(status_path.read_text(encoding="utf-8"))
    gates = status.get("application_gates") or {}
    gate_data = gates.get(gate) or {}
    gate_status = str(gate_data.get("status") or "missing")
    blockers = [str(item) for item in gate_data.get("blockers") or [] if str(item)]
    can_apply_now = bool(gate_data.get("can_apply_now"))
    allowed, decision_reason = _allowed_decision(
        gate_status=gate_status,
        can_apply_now=can_apply_now,
        blockers=blockers,
        allow_candidate=allow_candidate,
    )
    summary = {
        "gate": gate,
        "gate_status": gate_status,
        "can_apply_now": can_apply_now,
        "allow_candidate": bool(allow_candidate),
        "allowed": allowed,
        "blockers": blockers,
        "reason": str(gate_data.get("reason") or ""),
        "required_action": str(gate_data.get("required_action") or ""),
        "decision_reason": decision_reason,
    }
    report = {
        "summary": summary,
        "inputs": {"status_json": str(status_path)},
        "gate": gate_data,
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


def _allowed_decision(*, gate_status: str, can_apply_now: bool, blockers: list[str], allow_candidate: bool) -> tuple[bool, str]:
    if can_apply_now:
        return True, "gate_can_apply_now"
    if gate_status == "candidate" and allow_candidate and not blockers:
        return True, "candidate_allowed_for_controlled_rollout"
    if gate_status == "candidate" and not allow_candidate:
        return False, "candidate_requires_explicit_allow_candidate"
    if blockers:
        return False, "gate_has_blockers"
    if gate_status == "not_recommended":
        return False, "gate_not_recommended"
    if gate_status == "missing":
        return False, "gate_missing"
    return False, "gate_not_allowed"


def _render_markdown(report: dict) -> str:
    summary = report["summary"]
    lines = [
        "# Calibration Application Gate",
        "",
        f"- Gate: {summary['gate']}",
        f"- Gate status: {summary['gate_status']}",
        f"- Allowed: {str(summary['allowed']).lower()}",
        f"- Can apply now: {str(summary['can_apply_now']).lower()}",
        f"- Allow candidate: {str(summary['allow_candidate']).lower()}",
        f"- Decision reason: {summary['decision_reason']}",
        f"- Reason: {summary['reason'] or 'not recorded'}",
        f"- Required action: {summary['required_action'] or 'not recorded'}",
        "",
        "## Blockers",
        "",
    ]
    blockers = summary.get("blockers") or []
    if blockers:
        for blocker in blockers:
            lines.append(f"- {blocker}")
    else:
        lines.append("- None")
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
