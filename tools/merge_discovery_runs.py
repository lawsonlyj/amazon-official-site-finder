from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from finder.input_normalizer import read_normalized_csv


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Merge multiple official-site discovery result/evidence runs.")
    parser.add_argument("--providers", required=True, help="Normalized providers CSV used for final row order.")
    parser.add_argument(
        "--run",
        action="append",
        required=True,
        help="Run spec in the form label=results.csv:evidence.jsonl. Earlier runs have priority.",
    )
    parser.add_argument("--output-results", required=True)
    parser.add_argument("--output-evidence", required=True)
    parser.add_argument("--missing-output")
    args = parser.parse_args(argv)

    summary = merge_runs(
        providers_csv=args.providers,
        run_specs=args.run,
        output_results=args.output_results,
        output_evidence=args.output_evidence,
        missing_output=args.missing_output,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def merge_runs(
    *,
    providers_csv: str | Path,
    run_specs: list[str],
    output_results: str | Path,
    output_evidence: str | Path,
    missing_output: str | Path | None = None,
) -> dict[str, Any]:
    providers = read_normalized_csv(providers_csv)
    provider_order = [provider.get("provider_id", "") for provider in providers if provider.get("provider_id")]
    run_inputs = [_parse_run_spec(spec) for spec in run_specs]

    result_by_id: dict[str, dict[str, str]] = {}
    evidence_by_id: dict[str, dict[str, Any]] = {}
    source_counts: dict[str, int] = {}
    duplicate_result_rows = 0

    for label, results_path, evidence_path in run_inputs:
        for row in _read_csv_rows(results_path):
            provider_id = (row.get("provider_id") or "").strip()
            if not provider_id:
                continue
            if provider_id in result_by_id:
                duplicate_result_rows += 1
                continue
            row["discovery_run"] = label
            result_by_id[provider_id] = row
            source_counts[label] = source_counts.get(label, 0) + 1
        for evidence in _read_evidence_rows(evidence_path):
            provider_id = (evidence.get("provider_id") or "").strip()
            if not provider_id or provider_id in evidence_by_id:
                continue
            evidence["discovery_run"] = label
            evidence_by_id[provider_id] = evidence

    merged_rows = [result_by_id[provider_id] for provider_id in provider_order if provider_id in result_by_id]
    missing_rows = [provider for provider in providers if provider.get("provider_id", "") not in result_by_id]
    _write_csv(output_results, merged_rows)
    _write_evidence(output_evidence, [evidence_by_id[pid] for pid in provider_order if pid in evidence_by_id])
    if missing_output:
        _write_csv(missing_output, missing_rows)

    return {
        "provider_count": len(provider_order),
        "merged_rows": len(merged_rows),
        "missing_rows": len(missing_rows),
        "source_counts": source_counts,
        "duplicate_result_rows_skipped": duplicate_result_rows,
        "output_results": str(output_results),
        "output_evidence": str(output_evidence),
        "missing_output": str(missing_output or ""),
    }


def _parse_run_spec(spec: str) -> tuple[str, Path, Path]:
    if "=" not in spec or ":" not in spec:
        raise ValueError("--run must be label=results.csv:evidence.jsonl")
    label, rest = spec.split("=", 1)
    results, evidence = rest.split(":", 1)
    label = label.strip()
    if not label:
        raise ValueError("--run label cannot be blank")
    return label, Path(results), Path(evidence)


def _read_csv_rows(path: str | Path) -> list[dict[str, str]]:
    with Path(path).open(newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _read_evidence_rows(path: str | Path) -> list[dict[str, Any]]:
    rows = []
    with Path(path).open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def _write_csv(path: str | Path, rows: list[dict[str, str]]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    fields = _csv_fields(rows)
    with output.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _csv_fields(rows: list[dict[str, str]]) -> list[str]:
    preferred = [
        "provider_id",
        "provider_name",
        "official_url",
        "official_domain",
        "confidence",
        "status",
        "evidence_summary",
        "candidate_count",
        "scored_candidate_count",
        "service_apis",
        "provider_locations",
        "discovery_run",
    ]
    seen = set(preferred)
    extra = []
    for row in rows:
        for field in row:
            if field not in seen:
                seen.add(field)
                extra.append(field)
    return preferred + extra


def _write_evidence(path: str | Path, rows: list[dict[str, Any]]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
