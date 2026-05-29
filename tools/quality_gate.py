from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from finder.scoring import is_excluded_domain, load_config
from finder.text import domain_from_url
from tools.evaluate_labeled_results import evaluate as evaluate_labeled
from tools.evaluate_labeled_results import read_rows as read_csv_rows


VALID_STATUSES = {
    "matched",
    "needs_review",
    "low_confidence",
    "not_found",
    "manual_accepted",
    "calibrated_released",
    "rejected",
    "unresolved",
    "invalid_manual_decision",
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run quality gates for official website result CSVs.")
    parser.add_argument("--results", required=True)
    parser.add_argument("--labels")
    parser.add_argument("--config", default="config/scoring.json")
    parser.add_argument("--expected-rows", type=int, default=0)
    parser.add_argument("--min-domain-accuracy", type=float, default=0.8)
    parser.add_argument("--min-auto-precision", type=float, default=0.95)
    parser.add_argument("--min-official-url-rate", type=float, default=0.0)
    parser.add_argument("--max-unresolved-rate", type=float, default=1.0)
    parser.add_argument("--max-excluded-official-urls", type=int, default=0)
    parser.add_argument("--output-md")
    parser.add_argument("--output-json")
    args = parser.parse_args(argv)

    labels = read_csv_rows(args.labels) if args.labels else None
    summary = evaluate_quality_gate(
        results_csv=args.results,
        config=load_config(args.config),
        labels=labels,
        expected_rows=args.expected_rows or None,
        min_domain_accuracy=args.min_domain_accuracy,
        min_auto_precision=args.min_auto_precision,
        min_official_url_rate=args.min_official_url_rate,
        max_unresolved_rate=args.max_unresolved_rate,
        max_excluded_official_urls=args.max_excluded_official_urls,
    )
    if args.output_md:
        write_markdown(summary, args.output_md)
    if args.output_json:
        output = Path(args.output_json)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary["overall"], ensure_ascii=False, indent=2))
    return 0 if summary["overall"]["passed"] else 1


def evaluate_quality_gate(
    *,
    results_csv: str | Path,
    config: dict,
    labels: list[dict[str, str]] | None = None,
    expected_rows: int | None = None,
    min_domain_accuracy: float = 0.8,
    min_auto_precision: float = 0.95,
    min_official_url_rate: float = 0.0,
    max_unresolved_rate: float = 1.0,
    max_excluded_official_urls: int = 0,
) -> dict[str, Any]:
    rows = read_csv_rows(results_csv)
    failures = []
    warnings = []
    status_counts = Counter(row.get("status", "") for row in rows)
    duplicate_provider_ids = _duplicates(row.get("provider_id", "") for row in rows if row.get("provider_id"))
    unknown_status_rows = [row for row in rows if row.get("status", "") not in VALID_STATUSES]
    excluded_rows = _excluded_official_rows(rows, config)
    malformed_url_rows = _malformed_official_urls(rows)
    missing_accepted_urls = [
        _row_ref(row) for row in rows if row.get("status") in {"matched", "manual_accepted"} and not row.get("official_url")
    ]
    invalid_manual_rows = [_row_ref(row) for row in rows if row.get("status") == "invalid_manual_decision"]
    official_url_rows = sum(1 for row in rows if row.get("official_url"))
    unresolved_rows = [
        row
        for row in rows
        if row.get("status") in {"needs_review", "low_confidence", "not_found", "unresolved", "invalid_manual_decision"}
        or not row.get("official_url")
    ]
    official_url_rate = _ratio(official_url_rows, len(rows)) or 0.0
    unresolved_rate = _ratio(len(unresolved_rows), len(rows)) or 0.0

    if not rows:
        failures.append("result_csv_has_no_rows")
    if expected_rows is not None and len(rows) != expected_rows:
        failures.append(f"row_count_mismatch:expected_{expected_rows}:actual_{len(rows)}")
    if duplicate_provider_ids:
        failures.append(f"duplicate_provider_ids:{len(duplicate_provider_ids)}")
    if unknown_status_rows:
        failures.append(f"unknown_status_rows:{len(unknown_status_rows)}")
    if len(excluded_rows) > max_excluded_official_urls:
        failures.append(f"excluded_official_urls:{len(excluded_rows)}")
    if malformed_url_rows:
        failures.append(f"malformed_official_urls:{len(malformed_url_rows)}")
    if missing_accepted_urls:
        failures.append(f"accepted_rows_missing_url:{len(missing_accepted_urls)}")
    if invalid_manual_rows:
        failures.append(f"invalid_manual_decisions:{len(invalid_manual_rows)}")
    if official_url_rate < min_official_url_rate:
        failures.append(f"official_url_rate_below_threshold:{official_url_rate}")
    if unresolved_rate > max_unresolved_rate:
        failures.append(f"unresolved_rate_above_threshold:{unresolved_rate}")

    labeled_summary = None
    if labels is not None:
        labeled_summary = evaluate_labeled(labels, rows)
        domain_accuracy = labeled_summary["overall"]["domain_accuracy"]
        auto_precision = labeled_summary["overall"]["auto_match_precision"]
        if domain_accuracy is not None and domain_accuracy < min_domain_accuracy:
            failures.append(f"domain_accuracy_below_threshold:{domain_accuracy}")
        if auto_precision is not None and auto_precision < min_auto_precision:
            failures.append(f"auto_precision_below_threshold:{auto_precision}")
        if labeled_summary["overall"]["missing_labeled_rows"]:
            failures.append(f"missing_labeled_rows:{labeled_summary['overall']['missing_labeled_rows']}")
        if auto_precision is None:
            warnings.append("no_auto_matched_labeled_rows_for_precision")

    overall = {
        "passed": not failures,
        "total_rows": len(rows),
        "status_counts": dict(status_counts),
        "official_url_rows": official_url_rows,
        "official_url_rate": official_url_rate,
        "unresolved_rows": len(unresolved_rows),
        "unresolved_rate": unresolved_rate,
        "excluded_official_url_rows": len(excluded_rows),
        "duplicate_provider_ids": len(duplicate_provider_ids),
        "unknown_status_rows": len(unknown_status_rows),
        "malformed_official_url_rows": len(malformed_url_rows),
        "failures": failures,
        "warnings": warnings,
    }
    if labeled_summary:
        overall.update(
            {
                "labeled_domain_accuracy": labeled_summary["overall"]["domain_accuracy"],
                "labeled_auto_match_precision": labeled_summary["overall"]["auto_match_precision"],
                "labeled_evaluated_rows": labeled_summary["overall"]["evaluated_rows"],
            }
        )
    return {
        "overall": overall,
        "excluded_official_url_rows": excluded_rows,
        "malformed_official_url_rows": malformed_url_rows,
        "duplicate_provider_ids": duplicate_provider_ids,
        "unknown_status_rows": [_row_ref(row) for row in unknown_status_rows[:50]],
        "missing_accepted_urls": missing_accepted_urls[:50],
        "invalid_manual_rows": invalid_manual_rows[:50],
        "unresolved_rows": [_row_ref(row) for row in unresolved_rows[:50]],
        "labeled_evaluation": labeled_summary,
    }


def write_markdown(summary: dict[str, Any], output_md: str | Path) -> None:
    output = Path(output_md)
    output.parent.mkdir(parents=True, exist_ok=True)
    overall = summary["overall"]
    lines = [
        "# Result Quality Gate",
        "",
        f"Status: {'PASS' if overall['passed'] else 'FAIL'}",
        "",
        "## Overall",
        "",
        "| Metric | Value |",
        "|---|---:|",
    ]
    for key, value in overall.items():
        if key in {"failures", "warnings", "status_counts"}:
            continue
        lines.append(f"| {key} | {value} |")

    lines.extend(["", "## Status Counts", "", "| Status | Rows |", "|---|---:|"])
    for status, count in sorted(overall["status_counts"].items()):
        lines.append(f"| {status or '(blank)'} | {count} |")

    if overall["failures"]:
        lines.extend(["", "## Failures", ""])
        for failure in overall["failures"]:
            lines.append(f"- {failure}")
    if overall["warnings"]:
        lines.extend(["", "## Warnings", ""])
        for warning in overall["warnings"]:
            lines.append(f"- {warning}")
    if summary["excluded_official_url_rows"]:
        lines.extend(["", "## Excluded Official URLs", ""])
        for row in summary["excluded_official_url_rows"][:25]:
            lines.append(f"- {row['provider_name']}: `{row['official_domain']}`")
    output.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def _excluded_official_rows(rows: list[dict[str, str]], config: dict) -> list[dict[str, str]]:
    out = []
    for row in rows:
        official = row.get("official_domain") or row.get("official_url", "")
        if official and is_excluded_domain(official, config):
            out.append(
                {
                    "provider_id": row.get("provider_id", ""),
                    "provider_name": row.get("provider_name", ""),
                    "official_url": row.get("official_url", ""),
                    "official_domain": domain_from_url(official),
                    "status": row.get("status", ""),
                }
            )
    return out


def _malformed_official_urls(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    out = []
    for row in rows:
        url = (row.get("official_url") or "").strip()
        if url and not url.startswith(("http://", "https://")):
            out.append(_row_ref(row))
    return out


def _duplicates(values) -> list[str]:
    counts = Counter(value for value in values if value)
    return sorted(value for value, count in counts.items() if count > 1)


def _ratio(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return round(numerator / denominator, 3)


def _row_ref(row: dict[str, str]) -> dict[str, str]:
    return {
        "provider_id": row.get("provider_id", ""),
        "provider_name": row.get("provider_name", ""),
        "status": row.get("status", ""),
        "official_url": row.get("official_url", ""),
        "official_domain": row.get("official_domain", ""),
    }


if __name__ == "__main__":
    raise SystemExit(main())
