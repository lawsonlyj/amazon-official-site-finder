from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from finder.finalize import FINAL_FIELDS, write_rows
from tools.build_linked_workbook import build_workbook
from tools.evaluate_workflow_balance import evaluate_balance_from_details
from tools.run_calibration_regression_gate import run_calibration_regression_gate


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Simulate holding selected manual-review lanes out of automatic official-site output."
    )
    parser.add_argument("--final-csv", required=True, help="Candidate official_sites.csv to transform.")
    parser.add_argument("--review-task-csv", required=True, help="review_task.csv/manual_official_site_review_task.csv.")
    parser.add_argument(
        "--hold-review-reason",
        action="append",
        default=[],
        help="review_reason to move from automatic official output to needs_review. Repeatable or comma-separated.",
    )
    parser.add_argument("--output-csv", required=True)
    parser.add_argument("--output-xlsx")
    parser.add_argument("--labeled-details", help="Optional labeled balance details CSV/JSON for metric evaluation.")
    parser.add_argument("--run-dir", help="Optional run dir for balance annotations.")
    parser.add_argument("--cases-csv", help="Optional calibration_regression_cases.csv for gate evaluation.")
    parser.add_argument("--summary-json")
    parser.add_argument("--summary-md")
    args = parser.parse_args(argv)

    summary = simulate_review_lane_output_policy(
        final_csv=args.final_csv,
        review_task_csv=args.review_task_csv,
        hold_review_reasons=args.hold_review_reason,
        output_csv=args.output_csv,
        output_xlsx=args.output_xlsx,
        labeled_details=args.labeled_details,
        run_dir=args.run_dir,
        cases_csv=args.cases_csv,
        summary_json=args.summary_json,
        summary_md=args.summary_md,
    )
    print(json.dumps(summary["summary"], ensure_ascii=False, indent=2))
    return 0


def simulate_review_lane_output_policy(
    *,
    final_csv: str | Path,
    review_task_csv: str | Path,
    hold_review_reasons: list[str],
    output_csv: str | Path,
    output_xlsx: str | Path | None = None,
    labeled_details: str | Path | None = None,
    run_dir: str | Path | None = None,
    cases_csv: str | Path | None = None,
    summary_json: str | Path | None = None,
    summary_md: str | Path | None = None,
) -> dict:
    final_rows = _read_rows(Path(final_csv))
    review_rows = _read_rows(Path(review_task_csv))
    hold_reasons = _normalize_reasons(hold_review_reasons)
    review_index = _review_reason_index(review_rows)

    output_rows = []
    held_rows = []
    for row in final_rows:
        key = _row_key(row)
        reason = review_index.get(key, "")
        if reason in hold_reasons and row.get("official_url"):
            held = _held_row(row, reason)
            output_rows.append(held)
            held_rows.append(held)
        else:
            output_rows.append(dict(row))

    output_csv = Path(output_csv)
    write_rows(output_csv, output_rows, FINAL_FIELDS)
    xlsx_summary = build_workbook([("Review_Lane_Policy", output_csv)], output_xlsx) if output_xlsx else {}

    balance = {}
    if labeled_details:
        balance = evaluate_balance_from_details(
            labeled_details=labeled_details,
            candidate_final=output_csv,
            run_dir=run_dir,
        )
    regression_gate = {}
    if cases_csv:
        regression_gate = run_calibration_regression_gate(
            cases_csv=cases_csv,
            candidate_final_csv=output_csv,
        )

    summary = {
        "input_rows": len(final_rows),
        "output_rows": len(output_rows),
        "held_rows": len(held_rows),
        "held_review_reasons": sorted(hold_reasons),
        "held_reason_counts": dict(Counter(row.get("source_status", "") for row in held_rows)),
        "held_provider_ids": [row.get("provider_id", "") for row in held_rows],
        "official_url_rows": sum(1 for row in output_rows if row.get("official_url")),
        "unresolved_or_needs_review_rows": sum(1 for row in output_rows if not row.get("official_url")),
        "balance_overall": balance.get("overall", {}),
        "regression_gate_summary": regression_gate.get("summary", {}),
        "output_csv": str(output_csv),
        "output_xlsx": str(output_xlsx or ""),
    }
    report = {
        "summary": summary,
        "held_rows": [
            {
                "provider_id": row.get("provider_id", ""),
                "provider_name": row.get("provider_name", ""),
                "review_reason": row.get("source_status", ""),
                "provider_detail_url": row.get("provider_detail_url", ""),
                "held_url": _held_url(row),
            }
            for row in held_rows
        ],
        "balance": balance,
        "regression_gate": regression_gate,
        "inputs": {
            "final_csv": str(final_csv),
            "review_task_csv": str(review_task_csv),
            "labeled_details": str(labeled_details or ""),
            "run_dir": str(run_dir or ""),
            "cases_csv": str(cases_csv or ""),
        },
    }
    if summary_json:
        path = Path(summary_json)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if summary_md:
        path = Path(summary_md)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_render_markdown(report), encoding="utf-8")
    return report


def _held_row(row: dict[str, str], reason: str) -> dict[str, str]:
    out = {field: row.get(field, "") for field in FINAL_FIELDS}
    held_url = out.get("official_url", "")
    out["official_url"] = ""
    out["official_domain"] = ""
    out["status"] = "needs_review"
    out["source_status"] = reason
    out["notes"] = _append_note(out.get("notes", ""), f"review_lane_holdout:{reason}; held_url:{held_url}")
    return out


def _held_url(row: dict[str, str]) -> str:
    notes = row.get("notes", "")
    marker = "held_url:"
    if marker not in notes:
        return ""
    return notes.split(marker, 1)[1].split(";", 1)[0].strip()


def _append_note(existing: str, note: str) -> str:
    if not existing:
        return note
    return f"{existing}; {note}"


def _normalize_reasons(values: list[str]) -> set[str]:
    out: set[str] = set()
    for value in values:
        for item in str(value or "").split(","):
            item = item.strip()
            if item:
                out.add(item)
    return out


def _review_reason_index(rows: list[dict[str, str]]) -> dict[str, str]:
    out = {}
    for row in rows:
        key = _row_key(row)
        reason = str(row.get("review_reason") or "").strip()
        if key and reason:
            out[key] = reason
    return out


def _row_key(row: dict[str, str]) -> str:
    provider_id = str(row.get("provider_id") or "").strip()
    if provider_id:
        return f"id:{provider_id}"
    provider_name = str(row.get("provider_name") or "").strip().casefold()
    return f"name:{provider_name}" if provider_name else ""


def _read_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _render_markdown(report: dict) -> str:
    summary = report["summary"]
    balance = summary.get("balance_overall") or {}
    gate = summary.get("regression_gate_summary") or {}
    lines = [
        "# Review Lane Output Policy Simulation",
        "",
        f"- Held rows: {summary['held_rows']}",
        f"- Held review reasons: {', '.join(summary['held_review_reasons']) or 'None'}",
        f"- Official URL rows: {summary['official_url_rows']}",
        f"- Unresolved/needs-review rows: {summary['unresolved_or_needs_review_rows']}",
        f"- Output CSV: {summary['output_csv']}",
        f"- Output XLSX: {summary['output_xlsx'] or 'not written'}",
        "",
        "## Balance",
        "",
        f"- Accuracy: {balance.get('overall_accuracy')}",
        f"- Auto precision: {balance.get('auto_precision')}",
        f"- Official recall: {balance.get('official_recall')}",
        f"- False official rows: {balance.get('false_official_rows')}",
        f"- Over-rejected rows: {balance.get('over_rejected_rows')}",
        "",
        "## Regression Gate",
        "",
        f"- Status: {gate.get('gate_status') or 'not_run'}",
        f"- Pass/fail/unverified: {gate.get('pass_rows')}/{gate.get('fail_rows')}/{gate.get('unverified_rows')}",
        "",
        "## Held Rows",
        "",
    ]
    if not report["held_rows"]:
        lines.append("- None")
    else:
        for row in report["held_rows"][:100]:
            lines.append(
                "- {provider_name} ({provider_id}) :: {review_reason} :: {held_url}".format(**row)
            )
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
