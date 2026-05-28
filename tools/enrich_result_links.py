from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from finder.input_normalizer import read_normalized_csv


INSERT_AFTER = "provider_name"
ENRICH_FIELDS = ["provider_detail_url", "listing_logo_url"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Add original Amazon provider detail links to result CSVs.")
    parser.add_argument("--providers", required=True, help="Normalized provider CSV containing detail_url.")
    parser.add_argument("--input", required=True, help="Result/final/review CSV to enrich.")
    parser.add_argument("--output", required=True, help="Output CSV with provider_detail_url and listing_logo_url.")
    args = parser.parse_args(argv)

    summary = enrich_result_links(args.providers, args.input, args.output)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def enrich_result_links(providers_csv: str | Path, input_csv: str | Path, output_csv: str | Path) -> dict:
    provider_index = _provider_index(providers_csv)
    rows, fields = _read_rows(input_csv)
    output_fields = _output_fields(fields)
    enriched_rows = []
    missing_provider_links = 0
    for row in rows:
        provider = _provider_for(row, provider_index)
        detail_url = (provider.get("detail_url") or row.get("provider_detail_url") or row.get("detail_url") or "").strip()
        listing_logo_url = (provider.get("listing_logo_url") or row.get("listing_logo_url") or "").strip()
        if not detail_url:
            missing_provider_links += 1
        enriched = dict(row)
        enriched["provider_detail_url"] = detail_url
        enriched["listing_logo_url"] = listing_logo_url
        enriched_rows.append(enriched)
    _write_rows(output_csv, enriched_rows, output_fields)
    return {
        "input_rows": len(rows),
        "output_rows": len(enriched_rows),
        "provider_detail_url_rows": sum(1 for row in enriched_rows if row.get("provider_detail_url")),
        "missing_provider_detail_url_rows": missing_provider_links,
        "output_csv": str(output_csv),
    }


def _provider_index(providers_csv: str | Path) -> dict[str, dict[str, str]]:
    index: dict[str, dict[str, str]] = {}
    for provider in read_normalized_csv(providers_csv):
        provider_id = (provider.get("provider_id") or "").strip()
        provider_name = (provider.get("provider_name") or "").strip().casefold()
        if provider_id:
            index[f"id:{provider_id}"] = provider
        if provider_name:
            index[f"name:{provider_name}"] = provider
    return index


def _provider_for(row: dict[str, str], provider_index: dict[str, dict[str, str]]) -> dict[str, str]:
    provider_id = (row.get("provider_id") or "").strip()
    provider_name = (row.get("provider_name") or "").strip().casefold()
    if provider_id and f"id:{provider_id}" in provider_index:
        return provider_index[f"id:{provider_id}"]
    if provider_name and f"name:{provider_name}" in provider_index:
        return provider_index[f"name:{provider_name}"]
    return {}


def _read_rows(input_csv: str | Path) -> tuple[list[dict[str, str]], list[str]]:
    with Path(input_csv).open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        return list(reader), list(reader.fieldnames or [])


def _output_fields(fields: list[str]) -> list[str]:
    out = [field for field in fields if field not in ENRICH_FIELDS and field != "detail_url"]
    insert_at = out.index(INSERT_AFTER) + 1 if INSERT_AFTER in out else min(2, len(out))
    return out[:insert_at] + ENRICH_FIELDS + out[insert_at:]


def _write_rows(output_csv: str | Path, rows: list[dict[str, str]], fields: list[str]) -> None:
    output = Path(output_csv)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    raise SystemExit(main())
