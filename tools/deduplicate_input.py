from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from finder.input_normalizer import DESCRIPTION_MARKERS, normalize_provider_rows, write_normalized_csv
from finder.text import compact_space
from tools.build_linked_workbook import build_workbook
from tools.output_layout import pipeline_paths


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Deduplicate Amazon provider CSV rows before workflow execution.")
    parser.add_argument("--source", required=True, help="Raw or already deduplicated provider CSV.")
    parser.add_argument("--run-dir", help="Run directory. Defaults output paths under details/input/.")
    parser.add_argument("--output-csv", help="Deduplicated provider CSV output path.")
    parser.add_argument("--output-xlsx", help="Optional clickable XLSX copy of the deduplicated provider CSV.")
    parser.add_argument("--report-json", help="Deduplication report JSON output path.")
    parser.add_argument("--report-md", help="Deduplication report Markdown output path.")
    parser.add_argument("--write-xlsx", action="store_true", help="Write the XLSX output when using --run-dir defaults.")
    args = parser.parse_args(argv)

    if args.run_dir:
        paths = pipeline_paths(args.run_dir)
        output_csv = Path(args.output_csv) if args.output_csv else paths["deduped_input"]
        output_xlsx = Path(args.output_xlsx) if args.output_xlsx else paths["deduped_input_xlsx"]
        report_json = Path(args.report_json) if args.report_json else paths["dedupe_report_json"]
        report_md = Path(args.report_md) if args.report_md else paths["dedupe_report_md"]
    else:
        if not args.output_csv:
            raise SystemExit("--output-csv is required when --run-dir is not provided.")
        output_csv = Path(args.output_csv)
        output_xlsx = Path(args.output_xlsx) if args.output_xlsx else None
        report_json = Path(args.report_json) if args.report_json else None
        report_md = Path(args.report_md) if args.report_md else None

    if args.write_xlsx and output_xlsx is None:
        output_xlsx = output_csv.with_suffix(".xlsx")

    summary = deduplicate_input(
        source_csv=args.source,
        output_csv=output_csv,
        output_xlsx=output_xlsx,
        report_json=report_json,
        report_md=report_md,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def deduplicate_input(
    *,
    source_csv: str | Path,
    output_csv: str | Path,
    output_xlsx: str | Path | None = None,
    report_json: str | Path | None = None,
    report_md: str | Path | None = None,
) -> dict[str, Any]:
    source_path = Path(source_csv)
    output_path = Path(output_csv)
    if not source_path.exists():
        raise FileNotFoundError(f"source CSV does not exist: {source_path}")

    providers = normalize_provider_rows(source_path)
    report = build_dedupe_report(source_path, providers, output_path)
    write_normalized_csv(providers, output_path)

    if output_xlsx:
        build_workbook([("deduped_input", output_path)], output_xlsx)
        report["output_xlsx"] = str(Path(output_xlsx))
    if report_json:
        report_json_path = Path(report_json)
        report_json_path.parent.mkdir(parents=True, exist_ok=True)
        report_json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if report_md:
        report_md_path = Path(report_md)
        report_md_path.parent.mkdir(parents=True, exist_ok=True)
        report_md_path.write_text(render_dedupe_report(report), encoding="utf-8")
    return report


def build_dedupe_report(source_csv: str | Path, providers: list[dict[str, Any]], output_csv: str | Path) -> dict[str, Any]:
    source_path = Path(source_csv)
    rows, headers = _read_raw_rows(source_path)
    key_counts: Counter[str] = Counter()
    name_by_key: dict[str, str] = {}
    skipped_rows = 0
    valid_rows = 0
    for row in rows:
        provider_id = compact_space(row.get("provider_id", ""))
        provider_name = compact_space(row.get("provider_name", ""))
        if not provider_id or provider_id in DESCRIPTION_MARKERS or provider_name in DESCRIPTION_MARKERS:
            skipped_rows += 1
            continue
        key = provider_id or provider_name.casefold()
        key_counts[key] += 1
        name_by_key.setdefault(key, provider_name)
        valid_rows += 1

    duplicate_items = [(key, count) for key, count in key_counts.items() if count > 1]
    duplicate_items.sort(key=lambda item: (-item[1], name_by_key.get(item[0], ""), item[0]))
    top_duplicates = [
        {
            "provider_key": key,
            "provider_name": name_by_key.get(key, ""),
            "source_rows": count,
            "duplicate_extra_rows": count - 1,
        }
        for key, count in duplicate_items[:25]
    ]

    provider_source_rows = [int(provider.get("source_rows") or 0) for provider in providers]
    return {
        "source_csv": str(source_path),
        "output_csv": str(Path(output_csv)),
        "schema": "deduplicated_provider_schema",
        "input_columns": headers,
        "raw_csv_data_rows": len(rows),
        "valid_provider_rows": valid_rows,
        "skipped_description_or_empty_rows": skipped_rows,
        "output_provider_rows": len(providers),
        "duplicate_provider_keys": len(duplicate_items),
        "duplicate_extra_rows": sum(count - 1 for _, count in duplicate_items),
        "max_source_rows_per_provider": max(provider_source_rows) if provider_source_rows else 0,
        "top_duplicates": top_duplicates,
    }


def render_dedupe_report(report: dict[str, Any]) -> str:
    lines = [
        "# Deduplication Report",
        "",
        f"- Source CSV: `{report['source_csv']}`",
        f"- Deduplicated CSV: `{report['output_csv']}`",
        f"- Raw CSV data rows: `{report['raw_csv_data_rows']}`",
        f"- Valid provider rows: `{report['valid_provider_rows']}`",
        f"- Output provider rows: `{report['output_provider_rows']}`",
        f"- Duplicate provider keys: `{report['duplicate_provider_keys']}`",
        f"- Duplicate extra rows removed: `{report['duplicate_extra_rows']}`",
        f"- Max source rows for one provider: `{report['max_source_rows_per_provider']}`",
        "",
        "The workflow runs search and scoring from the deduplicated provider CSV. Duplicate service rows are merged into JSON list fields such as `service_apis`, `provider_locations`, `provider_languages`, and `service_types`.",
    ]
    top_duplicates = report.get("top_duplicates") or []
    if top_duplicates:
        lines.extend(["", "## Top Duplicates", "", "| provider_key | provider_name | source_rows | extra_rows |", "| --- | --- | ---: | ---: |"])
        for item in top_duplicates:
            lines.append(
                "| {provider_key} | {provider_name} | {source_rows} | {duplicate_extra_rows} |".format(
                    provider_key=_escape_md(item.get("provider_key", "")),
                    provider_name=_escape_md(item.get("provider_name", "")),
                    source_rows=item.get("source_rows", 0),
                    duplicate_extra_rows=item.get("duplicate_extra_rows", 0),
                )
            )
    lines.append("")
    return "\n".join(lines)


def _read_raw_rows(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        return rows, list(reader.fieldnames or [])


def _escape_md(value: object) -> str:
    return str(value or "").replace("|", "\\|").replace("\n", " ")


if __name__ == "__main__":
    raise SystemExit(main())
