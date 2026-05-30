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


DETAIL_FIELDS = [
    "provider_id",
    "provider_name",
    "case_type",
    "assertion",
    "review_reason",
    "expected_url",
    "blocked_url",
    "observed_url",
    "observed_status",
    "gate_result",
    "failure_reason",
    "notes",
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate candidate workflow outputs against calibration regression cases.")
    parser.add_argument("--cases-csv", required=True, help="calibration_regression_cases.csv from a filled calibration cycle.")
    parser.add_argument("--candidate-final-csv", required=True, help="Candidate official_sites.csv/provider_final CSV to validate.")
    parser.add_argument("--output-json")
    parser.add_argument("--output-md")
    parser.add_argument("--output-csv")
    args = parser.parse_args(argv)

    report = run_calibration_regression_gate(
        cases_csv=args.cases_csv,
        candidate_final_csv=args.candidate_final_csv,
        output_json=args.output_json,
        output_md=args.output_md,
        output_csv=args.output_csv,
    )
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 1 if report["summary"]["gate_status"] == "fail" else 0


def run_calibration_regression_gate(
    *,
    cases_csv: str | Path,
    candidate_final_csv: str | Path,
    output_json: str | Path | None = None,
    output_md: str | Path | None = None,
    output_csv: str | Path | None = None,
) -> dict:
    cases = _read_rows(Path(cases_csv))
    outputs = {_key(row): row for row in _read_rows(Path(candidate_final_csv)) if _key(row)}
    details = [_evaluate_case(case, outputs.get(_key(case), {})) for case in cases]
    counts = Counter(row["gate_result"] for row in details)
    summary = {
        "gate_status": "fail" if counts.get("fail") or counts.get("unverified") else "pass",
        "case_rows": len(details),
        "pass_rows": counts.get("pass", 0),
        "fail_rows": counts.get("fail", 0),
        "unverified_rows": counts.get("unverified", 0),
        "case_type_counts": dict(Counter(row["case_type"] for row in details)),
        "failure_reason_counts": dict(Counter(row["failure_reason"] for row in details if row["failure_reason"])),
    }
    report = {
        "summary": summary,
        "details": details,
        "inputs": {
            "cases_csv": str(cases_csv),
            "candidate_final_csv": str(candidate_final_csv),
        },
    }
    if output_csv:
        _write_rows(Path(output_csv), details, DETAIL_FIELDS)
    if output_json:
        path = Path(output_json)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if output_md:
        path = Path(output_md)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_render_markdown(report), encoding="utf-8")
    return report


def _evaluate_case(case: dict[str, str], output: dict[str, str]) -> dict[str, str]:
    case_type = str(case.get("case_type") or "")
    observed_url = _first(output, "official_url", "manual_url")
    observed_status = _first(output, "status", "source_status")
    expected_url = str(case.get("expected_url") or "")
    blocked_url = _blocked_url(case)
    result = "pass"
    reason = ""
    if not output:
        result = "unverified"
        reason = "provider_missing_from_candidate_output"
    elif case_type in {"precision_blocking_fixture", "recall_blocking_fixture"}:
        if observed_url and _same_site(observed_url, blocked_url):
            result = "fail"
            reason = "blocked_candidate_was_auto_accepted"
        elif expected_url and observed_url and not _same_site(observed_url, expected_url):
            result = "pass"
            reason = "candidate_changed_away_from_blocked_url"
        else:
            result = "pass"
    elif case_type in {"precision_positive_fixture", "recall_positive_fixture"}:
        if not observed_url:
            result = "fail"
            reason = "positive_fixture_over_rejected"
        elif expected_url and not _same_site(observed_url, expected_url):
            result = "fail"
            reason = "positive_fixture_changed_to_different_site"
        else:
            result = "pass"
    return {
        "provider_id": str(case.get("provider_id") or ""),
        "provider_name": str(case.get("provider_name") or ""),
        "case_type": case_type,
        "assertion": str(case.get("assertion") or ""),
        "review_reason": str(case.get("review_reason") or ""),
        "expected_url": expected_url,
        "blocked_url": blocked_url,
        "observed_url": observed_url,
        "observed_status": observed_status,
        "gate_result": result,
        "failure_reason": reason,
        "notes": str(case.get("notes") or ""),
    }


def _blocked_url(case: dict[str, str]) -> str:
    return str(case.get("candidate_url") or case.get("official_url") or "").strip()


def _same_site(left: str, right: str) -> bool:
    left_key = _site_key(left)
    right_key = _site_key(right)
    return bool(left_key and right_key and left_key == right_key)


def _site_key(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    host = parsed.netloc.casefold()
    if host.startswith("www."):
        host = host[4:]
    path = re.sub(r"/+$", "", parsed.path or "")
    if path in {"", "/"}:
        path = ""
    return f"{host}{path}"


def _key(row: dict[str, str]) -> str:
    return str(row.get("provider_id") or "").strip()


def _first(row: dict[str, str], *keys: str) -> str:
    for key in keys:
        value = str(row.get(key) or "").strip()
        if value:
            return value
    return ""


def _render_markdown(report: dict) -> str:
    summary = report["summary"]
    lines = [
        "# Calibration Regression Gate",
        "",
        f"- Gate status: {summary['gate_status']}",
        f"- Case rows: {summary['case_rows']}",
        f"- Pass/fail/unverified: {summary['pass_rows']}/{summary['fail_rows']}/{summary['unverified_rows']}",
        "",
        "## Failure Reasons",
        "",
    ]
    if summary["failure_reason_counts"]:
        for key, value in sorted(summary["failure_reason_counts"].items()):
            lines.append(f"- {key}: {value}")
    else:
        lines.append("- None")
    failed = [row for row in report["details"] if row["gate_result"] != "pass"]
    if failed:
        lines.extend(["", "## Failed Or Unverified Cases", ""])
        for row in failed:
            lines.append(
                "- {gate_result}: {provider_name} ({provider_id}) :: {failure_reason} :: observed={observed_url} blocked={blocked_url} expected={expected_url}".format(
                    **row
                )
            )
    lines.append("")
    return "\n".join(lines)


def _read_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _write_rows(path: Path, rows: list[dict[str, str]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    raise SystemExit(main())
