from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from finder.text import domain_from_url


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate finder output against labeled expected official domains.")
    parser.add_argument("--labels", required=True)
    parser.add_argument("--results", required=True)
    parser.add_argument("--output-md", required=True)
    parser.add_argument("--output-json")
    args = parser.parse_args(argv)

    labels = read_rows(args.labels)
    results = read_rows(args.results)
    summary = evaluate(labels, results)
    write_markdown(summary, args.output_md)
    if args.output_json:
        Path(args.output_json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output_json).write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary["overall"], ensure_ascii=False, indent=2))
    return 0


def read_rows(path: str | Path) -> list[dict[str, str]]:
    with Path(path).open(newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def evaluate(labels: list[dict[str, str]], results: list[dict[str, str]]) -> dict:
    result_by_id = {row.get("provider_id", ""): row for row in results if row.get("provider_id", "")}
    result_by_name = {row.get("provider_name", "").casefold(): row for row in results if row.get("provider_name", "")}
    evaluated = []
    missing = []
    for label in labels:
        result = result_by_id.get(label.get("provider_id", "")) or result_by_name.get(
            label.get("provider_name", "").casefold()
        )
        if not result:
            missing.append(_label_summary(label))
            continue
        expected_domain = domain_from_url(label.get("expected_domain") or label.get("expected_url", ""))
        actual_domain = domain_from_url(result.get("official_domain") or result.get("official_url", ""))
        domain_match = bool(expected_domain and actual_domain and expected_domain == actual_domain)
        evaluated.append(
            {
                "provider_id": label.get("provider_id", ""),
                "provider_name": label.get("provider_name", ""),
                "expected_domain": expected_domain,
                "actual_domain": actual_domain,
                "actual_url": result.get("official_url", ""),
                "status": result.get("status", ""),
                "confidence": _to_int(result.get("confidence")),
                "domain_match": domain_match,
                "evidence_summary": result.get("evidence_summary", ""),
            }
        )

    auto_rows = [row for row in evaluated if row["status"] == "matched"]
    review_rows = [row for row in evaluated if row["status"] == "needs_review"]
    unresolved_rows = [row for row in evaluated if row["status"] in {"low_confidence", "not_found"} or not row["actual_domain"]]
    domain_matches = [row for row in evaluated if row["domain_match"]]
    auto_correct = [row for row in auto_rows if row["domain_match"]]

    overall = {
        "labeled_rows": len(labels),
        "result_rows": len(results),
        "evaluated_rows": len(evaluated),
        "missing_labeled_rows": len(missing),
        "domain_matches": len(domain_matches),
        "domain_accuracy": _ratio(len(domain_matches), len(evaluated)),
        "auto_matched_rows": len(auto_rows),
        "auto_match_precision": _ratio(len(auto_correct), len(auto_rows)),
        "needs_review_rows": len(review_rows),
        "unresolved_rows": len(unresolved_rows),
    }
    return {
        "overall": overall,
        "evaluated": evaluated,
        "missing": missing,
        "mismatches": [row for row in evaluated if not row["domain_match"]],
    }


def write_markdown(summary: dict, output_md: str | Path) -> None:
    output = Path(output_md)
    output.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# Labeled Result Evaluation", "", "## Overall", "", "| Metric | Value |", "|---|---:|"]
    for key, value in summary["overall"].items():
        lines.append(f"| {key} | {value} |")

    lines.extend(["", "## Evaluated Rows", ""])
    lines.append("| Provider | Expected | Actual | Status | Confidence | Match |")
    lines.append("|---|---|---|---|---:|---:|")
    for row in summary["evaluated"]:
        lines.append(
            f"| {row['provider_name']} | `{row['expected_domain']}` | `{row['actual_domain']}` | "
            f"{row['status']} | {row['confidence']} | {row['domain_match']} |"
        )

    if summary["missing"]:
        lines.extend(["", "## Missing Labeled Rows", ""])
        for row in summary["missing"]:
            lines.append(f"- {row['provider_name']} (`{row['expected_domain']}`)")

    if summary["mismatches"]:
        lines.extend(["", "## Domain Mismatches / Misses", ""])
        for row in summary["mismatches"]:
            lines.append(
                f"- {row['provider_name']}: expected `{row['expected_domain']}`, "
                f"actual `{row['actual_domain']}` ({row['status']}, {row['confidence']})"
            )
    output.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def _label_summary(label: dict[str, str]) -> dict[str, str]:
    return {
        "provider_id": label.get("provider_id", ""),
        "provider_name": label.get("provider_name", ""),
        "expected_domain": domain_from_url(label.get("expected_domain") or label.get("expected_url", "")),
    }


def _to_int(value: str | None) -> int:
    try:
        return int(float(value or "0"))
    except ValueError:
        return 0


def _ratio(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return round(numerator / denominator, 3)


if __name__ == "__main__":
    raise SystemExit(main())
