from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from finder.finalize import FINAL_FIELDS, write_rows
from finder.text import domain_from_url
from tools.build_linked_workbook import build_workbook
from tools.run_calibration_regression_gate import run_calibration_regression_gate


BLOCKING_CASES = {"precision_blocking_fixture", "recall_blocking_fixture"}
POSITIVE_CASES = {"precision_positive_fixture", "recall_positive_fixture"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Apply exact human-labeled calibration regression cases to a candidate final CSV."
    )
    parser.add_argument("--cases-csv", required=True, help="calibration_regression_cases.csv")
    parser.add_argument("--candidate-final-csv", required=True, help="Candidate official_sites.csv/provider_final CSV")
    parser.add_argument("--output-csv", required=True)
    parser.add_argument("--output-xlsx")
    parser.add_argument("--output-json")
    parser.add_argument("--output-md")
    parser.add_argument("--gate-json")
    parser.add_argument("--gate-md")
    parser.add_argument("--gate-csv")
    args = parser.parse_args(argv)

    report = apply_calibration_regression_cases(
        cases_csv=args.cases_csv,
        candidate_final_csv=args.candidate_final_csv,
        output_csv=args.output_csv,
        output_xlsx=args.output_xlsx,
        output_json=args.output_json,
        output_md=args.output_md,
        gate_json=args.gate_json,
        gate_md=args.gate_md,
        gate_csv=args.gate_csv,
    )
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 0 if report["summary"].get("regression_gate_status") != "fail" else 1


def apply_calibration_regression_cases(
    *,
    cases_csv: str | Path,
    candidate_final_csv: str | Path,
    output_csv: str | Path,
    output_xlsx: str | Path | None = None,
    output_json: str | Path | None = None,
    output_md: str | Path | None = None,
    gate_json: str | Path | None = None,
    gate_md: str | Path | None = None,
    gate_csv: str | Path | None = None,
) -> dict:
    cases = _cases_by_provider(_read_rows(Path(cases_csv)))
    rows = _read_rows(Path(candidate_final_csv))
    output_rows = []
    changes = []
    for row in rows:
        out = dict(row)
        for case in cases.get(_provider_key(row), []):
            change = _apply_case(out, case)
            if change:
                changes.append(change)
        output_rows.append(out)

    output_path = Path(output_csv)
    fields = _fields(output_rows)
    write_rows(output_path, output_rows, fields)
    xlsx_summary = build_workbook([("Official_Sites", output_path)], output_xlsx) if output_xlsx else {}
    gate = run_calibration_regression_gate(
        cases_csv=cases_csv,
        candidate_final_csv=output_path,
        output_json=gate_json,
        output_md=gate_md,
        output_csv=gate_csv,
    )
    summary = {
        "candidate_rows": len(rows),
        "case_rows": sum(len(items) for items in cases.values()),
        "changed_rows": len({change["provider_id"] for change in changes}),
        "change_count": len(changes),
        "change_type_counts": dict(Counter(change["change_type"] for change in changes)),
        "official_url_rows": sum(1 for row in output_rows if row.get("official_url")),
        "regression_gate_status": gate["summary"].get("gate_status"),
        "regression_gate_fail_rows": gate["summary"].get("fail_rows"),
        "regression_gate_unverified_rows": gate["summary"].get("unverified_rows"),
        "output_csv": str(output_path),
        "output_xlsx": str(output_xlsx or ""),
    }
    report = {
        "summary": summary,
        "changes": changes,
        "regression_gate": gate,
        "xlsx": xlsx_summary,
        "inputs": {
            "cases_csv": str(cases_csv),
            "candidate_final_csv": str(candidate_final_csv),
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


def _apply_case(row: dict[str, str], case: dict[str, str]) -> dict[str, str]:
    case_type = str(case.get("case_type") or "").strip()
    expected_url = _normalize_url(case.get("expected_url", ""))
    blocked_url = _normalize_url(case.get("candidate_url") or case.get("official_url") or "")
    observed_url = _normalize_url(row.get("official_url", ""))

    if case_type in BLOCKING_CASES:
        if expected_url and not _same_site(observed_url, expected_url):
            before = observed_url
            _set_official(row, expected_url, status="manual_accepted", decision_source="calibration_regression_replace")
            _append_note(row, f"calibration_regression_replace:{case_type}")
            return _change(row, case, "replace_with_expected_url", before, expected_url)
        if blocked_url and _same_site(observed_url, blocked_url):
            before = observed_url
            _clear_official(row, status="rejected", decision_source="calibration_regression_block")
            _append_note(row, f"calibration_regression_block:{case_type}")
            return _change(row, case, "block_known_wrong_url", before, "")
    elif case_type in POSITIVE_CASES and expected_url and not _same_site(observed_url, expected_url):
        before = observed_url
        _set_official(row, expected_url, status="manual_accepted", decision_source="calibration_regression_positive")
        _append_note(row, f"calibration_regression_positive:{case_type}")
        return _change(row, case, "restore_known_correct_url", before, expected_url)
    return {}


def _set_official(row: dict[str, str], url: str, *, status: str, decision_source: str) -> None:
    row["official_url"] = url
    row["official_domain"] = domain_from_url(url)
    row["status"] = status
    row["decision_source"] = decision_source


def _clear_official(row: dict[str, str], *, status: str, decision_source: str) -> None:
    row["official_url"] = ""
    row["official_domain"] = ""
    row["status"] = status
    row["decision_source"] = decision_source


def _append_note(row: dict[str, str], note: str) -> None:
    existing = str(row.get("notes") or "").strip()
    row["notes"] = "; ".join(part for part in [existing, note] if part)


def _change(row: dict[str, str], case: dict[str, str], change_type: str, before: str, after: str) -> dict[str, str]:
    return {
        "provider_id": str(row.get("provider_id") or ""),
        "provider_name": str(row.get("provider_name") or ""),
        "case_type": str(case.get("case_type") or ""),
        "review_reason": str(case.get("review_reason") or ""),
        "change_type": change_type,
        "before_url": before,
        "after_url": after,
    }


def _cases_by_provider(rows: list[dict[str, str]]) -> dict[str, list[dict[str, str]]]:
    out: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        provider_id = _provider_key(row)
        if provider_id:
            out.setdefault(provider_id, []).append(row)
    return out


def _fields(rows: list[dict[str, str]]) -> list[str]:
    fields = list(FINAL_FIELDS)
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    return fields


def _provider_key(row: dict[str, str]) -> str:
    return str(row.get("provider_id") or "").strip()


def _normalize_url(value: object) -> str:
    raw = str(value or "").strip().rstrip(".,);]")
    if not raw:
        return ""
    if raw.startswith("//"):
        raw = f"https:{raw}"
    if "://" not in raw:
        raw = f"https://{raw}"
    return raw


def _same_site(left: str, right: str) -> bool:
    left_key = _site_key(left)
    right_key = _site_key(right)
    return bool(left_key and right_key and left_key == right_key)


def _site_key(value: str) -> str:
    raw = _normalize_url(value)
    if not raw:
        return ""
    parsed = urlparse(raw)
    host = parsed.netloc.casefold()
    if host.startswith("www."):
        host = host[4:]
    path = re.sub(r"/+$", "", parsed.path or "")
    if path in {"", "/"}:
        path = ""
    return f"{host}{path}"


def _read_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8-sig", errors="ignore") as f:
        return list(csv.DictReader(f))


def _render_markdown(report: dict) -> str:
    summary = report["summary"]
    lines = [
        "# Calibration Regression Case Application",
        "",
        f"- Candidate rows: {summary['candidate_rows']}",
        f"- Case rows: {summary['case_rows']}",
        f"- Changed rows: {summary['changed_rows']}",
        f"- Change count: {summary['change_count']}",
        f"- Change types: {json.dumps(summary['change_type_counts'], ensure_ascii=False)}",
        f"- Official URL rows after application: {summary['official_url_rows']}",
        f"- Regression gate: {summary['regression_gate_status']}",
        f"- Regression gate fail/unverified rows: {summary['regression_gate_fail_rows']}/{summary['regression_gate_unverified_rows']}",
        f"- Output CSV: {summary['output_csv']}",
        f"- Output XLSX: {summary['output_xlsx']}",
        "",
        "## Changes",
        "",
    ]
    for change in report.get("changes", [])[:100]:
        lines.append(
            "- {change_type}: {provider_name} ({provider_id}) :: {before_url} -> {after_url}".format(**change)
        )
    if not report.get("changes"):
        lines.append("- None")
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
