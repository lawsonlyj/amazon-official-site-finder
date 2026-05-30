from __future__ import annotations

import argparse
import csv
import json
import sys
import zipfile
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


REQUIRED_FIELDS = [
    "provider_id",
    "provider_name",
    "provider_detail_url",
    "review_reason",
    "candidate_url",
    "official_url",
    "manual_decision",
    "manual_url",
    "notes",
    "review_instruction",
    "optimization_use",
]
VALID_MANUAL_DECISIONS = {"accept", "replace", "reject", "unsure"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify a protected-lane review task before handing it to reviewers.")
    parser.add_argument("--csv", required=True, help="protected_lanes_next_review_task.csv")
    parser.add_argument("--summary-json", help="protected_lanes_next_review_task_summary.json")
    parser.add_argument("--xlsx", help="protected_lanes_next_review_task.xlsx")
    parser.add_argument("--output-json")
    parser.add_argument("--output-md")
    parser.add_argument(
        "--allow-filled",
        action="store_true",
        help="Allow non-empty manual_decision values when verifying a returned filled task.",
    )
    parser.add_argument(
        "--require-filled",
        action="store_true",
        help="Require every row to have a valid manual_decision. Implies --allow-filled.",
    )
    args = parser.parse_args(argv)

    report = verify_protected_lane_review_task(
        csv_path=args.csv,
        summary_json=args.summary_json,
        xlsx_path=args.xlsx,
        output_json=args.output_json,
        output_md=args.output_md,
        allow_filled=args.allow_filled or args.require_filled,
        require_filled=args.require_filled,
    )
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 0 if report["summary"]["passed"] else 1


def verify_protected_lane_review_task(
    *,
    csv_path: str | Path,
    summary_json: str | Path | None = None,
    xlsx_path: str | Path | None = None,
    output_json: str | Path | None = None,
    output_md: str | Path | None = None,
    allow_filled: bool = False,
    require_filled: bool = False,
) -> dict:
    csv_file = Path(csv_path)
    rows, headers = _read_rows(csv_file)
    summary = _read_json(Path(summary_json)) if summary_json else {}
    failures: list[dict[str, str]] = []

    missing_fields = [field for field in REQUIRED_FIELDS if field not in headers]
    if missing_fields:
        failures.append({"check": "required_fields", "message": f"Missing required fields: {', '.join(missing_fields)}"})

    duplicate_keys = _duplicate_keys(rows)
    if duplicate_keys:
        failures.append({"check": "duplicate_provider_reason", "message": f"Duplicate provider/reason keys: {len(duplicate_keys)}"})

    missing_provider_detail = [row for row in rows if not str(row.get("provider_detail_url") or "").strip()]
    if missing_provider_detail:
        failures.append(
            {"check": "provider_detail_url", "message": f"Rows missing provider_detail_url: {len(missing_provider_detail)}"}
        )

    missing_candidate_or_official = [
        row for row in rows if not str(row.get("candidate_url") or row.get("official_url") or "").strip()
    ]
    if missing_candidate_or_official:
        failures.append(
            {
                "check": "candidate_or_official_url",
                "message": f"Rows missing both candidate_url and official_url: {len(missing_candidate_or_official)}",
            }
        )

    filled_rows = [row for row in rows if str(row.get("manual_decision") or "").strip()]
    blank_decision_rows = [row for row in rows if not str(row.get("manual_decision") or "").strip()]
    invalid_decision_rows = [
        row
        for row in filled_rows
        if str(row.get("manual_decision") or "").strip().casefold() not in VALID_MANUAL_DECISIONS
    ]
    replace_missing_url_rows = [
        row
        for row in rows
        if str(row.get("manual_decision") or "").strip().casefold() == "replace"
        and not str(row.get("manual_url") or "").strip()
    ]
    if filled_rows and not allow_filled:
        failures.append({"check": "manual_decision_blank", "message": f"Manual decision already filled: {len(filled_rows)}"})
    if require_filled and blank_decision_rows:
        failures.append({"check": "manual_decision_required", "message": f"Rows missing manual_decision: {len(blank_decision_rows)}"})
    if allow_filled and invalid_decision_rows:
        failures.append(
            {
                "check": "invalid_manual_decision",
                "message": f"Rows with invalid manual_decision: {len(invalid_decision_rows)}",
            }
        )
    if allow_filled and replace_missing_url_rows:
        failures.append(
            {
                "check": "replace_missing_manual_url",
                "message": f"Replace rows missing manual_url: {len(replace_missing_url_rows)}",
            }
        )

    summary_task_rows = _to_int(summary.get("task_rows"))
    if summary_json and summary_task_rows != len(rows):
        failures.append(
            {"check": "summary_task_rows", "message": f"Summary task_rows={summary_task_rows}, csv rows={len(rows)}"}
        )

    reason_counts = dict(Counter(row.get("review_reason", "") for row in rows))
    summary_reasons = {str(key): _to_int(value) for key, value in (summary.get("reason_counts") or {}).items()}
    if summary_json and summary_reasons != reason_counts:
        failures.append(
            {
                "check": "summary_reason_counts",
                "message": f"Summary reason_counts={summary_reasons}, csv reason_counts={reason_counts}",
            }
        )

    hyperlink_count = _hyperlink_formula_count(Path(xlsx_path)) if xlsx_path else 0
    if xlsx_path and rows and hyperlink_count <= 0:
        failures.append({"check": "xlsx_hyperlinks", "message": "XLSX contains no HYPERLINK formulas."})

    report = {
        "summary": {
            "passed": not failures,
            "row_count": len(rows),
            "required_field_count": len(REQUIRED_FIELDS),
            "missing_required_fields": missing_fields,
            "duplicate_key_count": len(duplicate_keys),
            "missing_provider_detail_url_rows": len(missing_provider_detail),
            "missing_candidate_or_official_url_rows": len(missing_candidate_or_official),
            "filled_manual_decision_rows": len(filled_rows),
            "blank_manual_decision_rows": len(blank_decision_rows),
            "invalid_manual_decision_rows": len(invalid_decision_rows),
            "replace_missing_manual_url_rows": len(replace_missing_url_rows),
            "valid_manual_decision_values": sorted(VALID_MANUAL_DECISIONS),
            "reason_counts": reason_counts,
            "summary_task_rows": summary_task_rows if summary_json else None,
            "xlsx_hyperlink_formula_count": hyperlink_count if xlsx_path else None,
            "failure_count": len(failures),
        },
        "failures": failures,
        "inputs": {
            "csv": str(csv_file),
            "summary_json": str(summary_json or ""),
            "xlsx": str(xlsx_path or ""),
            "allow_filled": bool(allow_filled),
            "require_filled": bool(require_filled),
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


def _read_rows(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        headers = list(reader.fieldnames or [])
    return rows, headers


def _duplicate_keys(rows: list[dict[str, str]]) -> set[tuple[str, str]]:
    seen: set[tuple[str, str]] = set()
    duplicates: set[tuple[str, str]] = set()
    for row in rows:
        key = (str(row.get("provider_id") or "").strip(), str(row.get("review_reason") or "").strip())
        if not all(key):
            continue
        if key in seen:
            duplicates.add(key)
        seen.add(key)
    return duplicates


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _hyperlink_formula_count(path: Path) -> int:
    if not path.exists():
        return 0
    count = 0
    with zipfile.ZipFile(path) as workbook:
        for name in workbook.namelist():
            if name.startswith("xl/worksheets/") and name.endswith(".xml"):
                count += workbook.read(name).decode("utf-8", errors="ignore").count("HYPERLINK(")
    return count


def _render_markdown(report: dict) -> str:
    summary = report["summary"]
    lines = [
        "# Protected-Lane Review Task Verification",
        "",
        f"- Passed: {str(summary['passed']).lower()}",
        f"- Rows: {summary['row_count']}",
        f"- Duplicate keys: {summary['duplicate_key_count']}",
        f"- Missing provider_detail_url rows: {summary['missing_provider_detail_url_rows']}",
        f"- Missing candidate/official URL rows: {summary['missing_candidate_or_official_url_rows']}",
        f"- Filled manual_decision rows: {summary['filled_manual_decision_rows']}",
        f"- Blank manual_decision rows: {summary['blank_manual_decision_rows']}",
        f"- Invalid manual_decision rows: {summary['invalid_manual_decision_rows']}",
        f"- Replace rows missing manual_url: {summary['replace_missing_manual_url_rows']}",
        f"- XLSX hyperlink formulas: {summary['xlsx_hyperlink_formula_count']}",
        "",
        "## Failures",
        "",
    ]
    if report["failures"]:
        for failure in report["failures"]:
            lines.append(f"- {failure['check']}: {failure['message']}")
    else:
        lines.append("- None")
    lines.append("")
    return "\n".join(lines)


def _to_int(value: object) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
