from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from finder.text import domain_from_url
from tools.build_linked_workbook import build_workbook


TASK_FIELDS = [
    "review_reason",
    "provider_id",
    "provider_name",
    "provider_detail_url",
    "official_url",
    "official_domain",
    "status",
    "confidence",
    "decision_source",
    "source_status",
    "top_candidate_url",
    "top_candidate_domain",
    "top_candidate_score",
    "candidate_1_url",
    "candidate_1_domain",
    "candidate_1_score",
    "evidence_summary",
    "service_apis",
    "provider_locations",
    "manual_decision",
    "manual_url",
    "notes",
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a simplified clickable manual review task from a run directory.")
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--output-csv")
    parser.add_argument("--output-xlsx")
    parser.add_argument("--write-xlsx", action="store_true")
    parser.add_argument("--include-matched-confidence-below", type=int, default=85)
    args = parser.parse_args(argv)

    summary = build_manual_review_task(
        run_dir=args.run_dir,
        output_csv=args.output_csv,
        output_xlsx=args.output_xlsx,
        write_xlsx=args.write_xlsx,
        include_matched_confidence_below=args.include_matched_confidence_below,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def build_manual_review_task(
    *,
    run_dir: str | Path,
    output_csv: str | Path | None = None,
    output_xlsx: str | Path | None = None,
    write_xlsx: bool = True,
    include_matched_confidence_below: int = 85,
) -> dict:
    run_dir = Path(run_dir)
    final_path = _first_existing(
        [
            run_dir / "provider_final_official_websites_second_pass.csv",
            run_dir / "provider_final_official_websites.csv",
        ]
    )
    if not final_path:
        raise FileNotFoundError(f"final result CSV not found in {run_dir}")

    final_rows = _read_rows(final_path)
    second_pass_rows = _index_rows(run_dir / "unresolved_second_pass_results.csv")
    review_rows = _index_rows(run_dir / "provider_review_sheet_enhanced.csv")
    task_rows = [
        _task_row(row, second_pass_rows.get(_row_key(row), {}), review_rows.get(_row_key(row), {}))
        for row in final_rows
        if _needs_manual_review(row, second_pass_rows.get(_row_key(row), {}), include_matched_confidence_below)
    ]
    task_rows = sorted(task_rows, key=_sort_key)

    output_csv_path = Path(output_csv) if output_csv else run_dir / "manual_official_site_review_task.csv"
    _write_rows(output_csv_path, task_rows, TASK_FIELDS)
    xlsx_summary = {}
    output_xlsx_path = Path(output_xlsx) if output_xlsx else run_dir / "manual_official_site_review_task.xlsx"
    if write_xlsx:
        xlsx_summary = build_workbook([("Manual_Review_Task", output_csv_path)], output_xlsx_path)

    return {
        "review_rows": len(task_rows),
        "source_final_csv": str(final_path),
        "output_csv": str(output_csv_path),
        "output_xlsx": str(output_xlsx_path) if write_xlsx else "",
        "xlsx": xlsx_summary,
        "reason_counts": _reason_counts(task_rows),
    }


def _needs_manual_review(row: dict[str, str], second_pass_row: dict[str, str], confidence_cutoff: int) -> bool:
    status = row.get("status", "")
    confidence = _to_int(row.get("confidence"))
    if not row.get("official_url"):
        return True
    if status == "manual_accepted":
        return True
    if status != "matched":
        return True
    if confidence < confidence_cutoff:
        return True
    if second_pass_row.get("accepted_for_final") == "true" and confidence < confidence_cutoff:
        return True
    return False


def _task_row(row: dict[str, str], second_pass_row: dict[str, str], review_row: dict[str, str]) -> dict[str, str]:
    top_url = _top_candidate_url(row, second_pass_row, review_row)
    top_domain = domain_from_url(top_url)
    official_url = row.get("official_url") or top_url
    official_domain = domain_from_url(row.get("official_domain") or official_url)
    return {
        "review_reason": _review_reason(row, second_pass_row),
        "provider_id": row.get("provider_id", ""),
        "provider_name": row.get("provider_name", ""),
        "provider_detail_url": row.get("provider_detail_url", ""),
        "official_url": official_url,
        "official_domain": official_domain,
        "status": row.get("status", ""),
        "confidence": row.get("confidence", ""),
        "decision_source": row.get("decision_source", ""),
        "source_status": row.get("source_status", ""),
        "top_candidate_url": top_url,
        "top_candidate_domain": top_domain,
        "top_candidate_score": second_pass_row.get("confidence", "") or review_row.get("candidate_1_score", ""),
        "candidate_1_url": top_url,
        "candidate_1_domain": top_domain,
        "candidate_1_score": second_pass_row.get("confidence", "") or review_row.get("candidate_1_score", ""),
        "evidence_summary": row.get("evidence_summary", "") or second_pass_row.get("evidence_summary", ""),
        "service_apis": row.get("service_apis", ""),
        "provider_locations": row.get("provider_locations", ""),
        "manual_decision": "",
        "manual_url": "",
        "notes": "",
    }


def _review_reason(row: dict[str, str], second_pass_row: dict[str, str]) -> str:
    status = row.get("status", "")
    confidence = _to_int(row.get("confidence"))
    if not row.get("official_url"):
        if second_pass_row.get("official_url") or second_pass_row.get("previous_top_candidate_url"):
            return "recall_unresolved_top_candidate"
        return "recall_unresolved_manual_search"
    if status == "manual_accepted" and confidence < 70:
        return "precision_second_pass_accepted_lt70"
    if status == "manual_accepted" and confidence < 85:
        return "precision_second_pass_accepted_70_84"
    if status == "manual_accepted":
        return "precision_second_pass_accepted_85_plus"
    if confidence < 85:
        return "precision_low_confidence_auto_match"
    return "spot_check_non_matched_status"


def _top_candidate_url(row: dict[str, str], second_pass_row: dict[str, str], review_row: dict[str, str]) -> str:
    for value in [
        row.get("official_url", ""),
        second_pass_row.get("official_url", ""),
        second_pass_row.get("previous_top_candidate_url", ""),
        review_row.get("candidate_1_url", ""),
    ]:
        if value:
            return value
    return ""


def _read_rows(path: str | Path) -> list[dict[str, str]]:
    with Path(path).open(newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _write_rows(path: str | Path, rows: list[dict[str, str]], fields: list[str]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _index_rows(path: Path) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    return {_row_key(row): row for row in _read_rows(path) if _row_key(row)}


def _row_key(row: dict[str, str]) -> str:
    provider_id = (row.get("provider_id") or "").strip()
    if provider_id:
        return f"id:{provider_id}"
    return f"name:{(row.get('provider_name') or '').strip().casefold()}"


def _first_existing(paths: list[Path]) -> Path | None:
    for path in paths:
        if path.exists():
            return path
    return None


def _sort_key(row: dict[str, str]) -> tuple[int, int, str]:
    priority = {
        "precision_second_pass_accepted_lt70": 0,
        "precision_second_pass_accepted_70_84": 1,
        "precision_second_pass_accepted_85_plus": 2,
        "precision_low_confidence_auto_match": 3,
        "recall_unresolved_top_candidate": 4,
        "recall_unresolved_manual_search": 5,
    }
    return (priority.get(row.get("review_reason", ""), 9), _to_int(row.get("confidence")), row.get("provider_name", ""))


def _reason_counts(rows: list[dict[str, str]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        reason = row.get("review_reason", "")
        counts[reason] = counts.get(reason, 0) + 1
    return counts


def _to_int(value: object) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
