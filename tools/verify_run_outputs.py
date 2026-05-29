from __future__ import annotations

import argparse
import csv
import json
import sys
import zipfile
from pathlib import Path


FORMULA_ERRORS = ["#REF!", "#DIV/0!", "#VALUE!", "#NAME?", "#N/A"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify handoff artifacts in a pipeline run directory.")
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--expected-rows", type=int, default=0)
    parser.add_argument("--expected-unresolved", type=int)
    parser.add_argument("--xlsx", help="Optional clickable XLSX workbook to inspect.")
    parser.add_argument("--final", default="official_sites.csv")
    parser.add_argument("--unresolved", default="unresolved.csv")
    parser.add_argument("--quality", default="quality.json")
    args = parser.parse_args(argv)

    summary = verify_run_outputs(
        args.run_dir,
        expected_rows=args.expected_rows or None,
        expected_unresolved=args.expected_unresolved,
        xlsx=args.xlsx,
        final_csv=args.final,
        unresolved_csv=args.unresolved,
        quality_json=args.quality,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["passed"] else 1


def verify_run_outputs(
    run_dir: str | Path,
    *,
    expected_rows: int | None = None,
    expected_unresolved: int | None = None,
    xlsx: str | Path | None = None,
    final_csv: str | Path = "official_sites.csv",
    unresolved_csv: str | Path = "unresolved.csv",
    quality_json: str | Path = "quality.json",
) -> dict:
    run_dir = Path(run_dir)
    failures: list[str] = []
    final_path = _resolve(run_dir, final_csv)
    unresolved_path = _resolve(run_dir, unresolved_csv)
    quality_path = _resolve(run_dir, quality_json)

    final_rows = _read_rows(final_path, failures)
    unresolved_rows = _read_rows(unresolved_path, failures)
    quality = _read_json(quality_path, failures)
    overall = quality.get("overall", {}) if isinstance(quality, dict) else {}

    if expected_rows is not None and len(final_rows) != expected_rows:
        failures.append(f"final_rows:{len(final_rows)}!=expected:{expected_rows}")
    if expected_unresolved is not None and len(unresolved_rows) != expected_unresolved:
        failures.append(f"unresolved_rows:{len(unresolved_rows)}!=expected:{expected_unresolved}")

    final_unresolved_count = sum(1 for row in final_rows if _is_unresolved_final_row(row))
    if len(unresolved_rows) != final_unresolved_count:
        failures.append(f"unresolved_csv_rows:{len(unresolved_rows)}!=final_unresolved:{final_unresolved_count}")

    detail_url_rows = sum(1 for row in final_rows if row.get("provider_detail_url"))
    if final_rows and detail_url_rows != len(final_rows):
        failures.append(f"missing_provider_detail_url_rows:{len(final_rows) - detail_url_rows}")

    if overall:
        if not overall.get("passed"):
            failures.append("quality_gate:not_passed")
        if int(overall.get("excluded_official_url_rows") or 0) != 0:
            failures.append(f"excluded_official_url_rows:{overall.get('excluded_official_url_rows')}")
        if int(overall.get("duplicate_provider_ids") or 0) != 0:
            failures.append(f"duplicate_provider_ids:{overall.get('duplicate_provider_ids')}")
        if int(overall.get("malformed_official_url_rows") or 0) != 0:
            failures.append(f"malformed_official_url_rows:{overall.get('malformed_official_url_rows')}")
        if expected_rows is not None and int(overall.get("total_rows") or 0) != expected_rows:
            failures.append(f"quality_total_rows:{overall.get('total_rows')}!=expected:{expected_rows}")

    xlsx_summary = {}
    if xlsx:
        xlsx_summary = _inspect_xlsx(Path(xlsx), failures)

    return {
        "passed": not failures,
        "failures": failures,
        "run_dir": str(run_dir),
        "final_csv": str(final_path),
        "unresolved_csv": str(unresolved_path),
        "quality_json": str(quality_path),
        "final_rows": len(final_rows),
        "unresolved_rows": len(unresolved_rows),
        "provider_detail_url_rows": detail_url_rows,
        "official_url_rows": sum(1 for row in final_rows if row.get("official_url")),
        "quality_passed": bool(overall.get("passed")) if overall else False,
        "xlsx": xlsx_summary,
    }


def _read_rows(path: Path, failures: list[str]) -> list[dict[str, str]]:
    if not path.exists():
        failures.append(f"missing_file:{path}")
        return []
    with path.open(newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _is_unresolved_final_row(row: dict[str, str]) -> bool:
    return not row.get("official_url") or row.get("status") in {"unresolved", "rejected", "invalid_manual_decision"}


def _resolve(run_dir: Path, path: str | Path) -> Path:
    path = Path(path)
    return path if path.is_absolute() else run_dir / path


def _read_json(path: Path, failures: list[str]) -> dict:
    if not path.exists():
        failures.append(f"missing_file:{path}")
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        failures.append(f"invalid_json:{path}:{exc}")
        return {}


def _inspect_xlsx(path: Path, failures: list[str]) -> dict:
    if not path.exists():
        failures.append(f"missing_file:{path}")
        return {}
    hyperlink_formulas = 0
    formula_errors = 0
    sheet_xml_files = 0
    with zipfile.ZipFile(path) as z:
        for name in z.namelist():
            if not name.startswith("xl/worksheets/sheet") or not name.endswith(".xml"):
                continue
            sheet_xml_files += 1
            text = z.read(name).decode("utf-8", errors="replace")
            hyperlink_formulas += text.count("HYPERLINK(")
            formula_errors += sum(text.count(error) for error in FORMULA_ERRORS)
    if hyperlink_formulas <= 0:
        failures.append("xlsx_hyperlink_formulas:0")
    if formula_errors:
        failures.append(f"xlsx_formula_errors:{formula_errors}")
    return {
        "path": str(path),
        "sheet_xml_files": sheet_xml_files,
        "hyperlink_formulas": hyperlink_formulas,
        "formula_errors": formula_errors,
    }


if __name__ == "__main__":
    raise SystemExit(main())
