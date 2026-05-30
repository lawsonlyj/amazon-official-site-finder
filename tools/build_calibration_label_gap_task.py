from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.build_linked_workbook import build_workbook


LABEL_GAP_PREFIX_FIELDS = [
    "label_priority",
    "label_target_decisive_rows",
    "label_decisive_rows_needed",
    "label_goal",
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a focused calibration label-gap review task.")
    parser.add_argument("--status-json", required=True, help="calibration_status.json from run_calibration_cycle.py.")
    parser.add_argument("--sample-csv", help="Optional sample CSV. Defaults to artifacts.sample_csv in status JSON.")
    parser.add_argument("--output-csv", required=True)
    parser.add_argument("--output-xlsx")
    parser.add_argument(
        "--priority",
        action="append",
        choices=["high", "medium", "normal", "low"],
        help="Priority to include. Repeatable. Default includes all priorities with a positive gap.",
    )
    args = parser.parse_args(argv)

    summary = build_calibration_label_gap_task(
        status_json=args.status_json,
        sample_csv=args.sample_csv,
        output_csv=args.output_csv,
        output_xlsx=args.output_xlsx,
        priorities=args.priority,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def build_calibration_label_gap_task(
    *,
    status_json: str | Path,
    output_csv: str | Path,
    sample_csv: str | Path | None = None,
    output_xlsx: str | Path | None = None,
    priorities: list[str] | None = None,
) -> dict:
    status = _read_json(Path(status_json))
    sample_value = sample_csv or status.get("artifacts", {}).get("sample_csv") or ""
    sample_path = Path(sample_value) if sample_value else Path()
    sample_rows = _read_rows(sample_path) if sample_value and sample_path.is_file() else []
    headers = list(sample_rows[0].keys()) if sample_rows else []
    allowed_priorities = set(priorities or ["high", "medium", "normal", "low"])
    selected: list[dict[str, str]] = []
    target_summaries = []

    for target in status.get("label_targets", []):
        priority = str(target.get("priority") or "")
        if priority not in allowed_priorities:
            continue
        needed = _to_int(target.get("decisive_rows_needed"))
        if needed <= 0:
            continue
        reason = str(target.get("review_reason") or "")
        candidates = [
            row
            for row in sample_rows
            if row.get("review_reason") == reason and not str(row.get("manual_decision") or "").strip()
        ]
        picked = candidates[:needed]
        for row in picked:
            selected.append(_label_gap_row(row, target))
        target_summaries.append(
            {
                "review_reason": reason,
                "priority": priority,
                "needed": needed,
                "selected": len(picked),
                "available_unlabeled": len(candidates),
            }
        )

    fields = _fields(headers)
    output_csv_path = Path(output_csv)
    _write_rows(output_csv_path, selected, fields)
    xlsx_summary = {}
    if output_xlsx:
        xlsx_summary = build_workbook([("Label_Gap_Task", output_csv_path)], output_xlsx)
    return {
        "status_json": str(status_json),
        "sample_csv": str(sample_path),
        "output_csv": str(output_csv_path),
        "output_xlsx": str(output_xlsx or ""),
        "task_rows": len(selected),
        "priority_counts": dict(Counter(row.get("label_priority", "") for row in selected)),
        "reason_counts": dict(Counter(row.get("review_reason", "") for row in selected)),
        "targets": target_summaries,
        "xlsx": xlsx_summary,
    }


def _label_gap_row(row: dict[str, str], target: dict) -> dict[str, str]:
    out = dict(row)
    out["label_priority"] = str(target.get("priority") or "")
    out["label_target_decisive_rows"] = str(target.get("target_decisive_rows") or "")
    out["label_decisive_rows_needed"] = str(target.get("decisive_rows_needed") or "")
    out["label_goal"] = str(target.get("label_goal") or "")
    return out


def _fields(sample_headers: list[str]) -> list[str]:
    return [*LABEL_GAP_PREFIX_FIELDS, *[field for field in sample_headers if field not in LABEL_GAP_PREFIX_FIELDS]]


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _write_rows(path: Path, rows: list[dict[str, str]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    effective_fields = fields or [*LABEL_GAP_PREFIX_FIELDS, "message"]
    effective_rows = rows or []
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=effective_fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(effective_rows)


def _to_int(value: object) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
