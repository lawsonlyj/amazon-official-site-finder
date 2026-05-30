from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.build_linked_workbook import build_workbook


VALID_DECISIONS = {"accept", "replace", "reject", "unsure"}
TRUSTED_SOURCE_MARKERS = (
    "manual_review_combined_decisions",
    "agent_human_review_regression_cases",
    "agent_no_official_regression_cases",
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Reuse trusted historical manual labels for a review task.")
    parser.add_argument("--task-csv", required=True)
    parser.add_argument("--label-path", action="append", required=True, help="Trusted label CSV file or directory. Repeatable.")
    parser.add_argument("--output-csv", required=True)
    parser.add_argument("--output-json")
    parser.add_argument("--output-md")
    parser.add_argument("--output-xlsx")
    args = parser.parse_args(argv)

    report = reuse_historical_labels_for_task(
        task_csv=args.task_csv,
        label_paths=args.label_path,
        output_csv=args.output_csv,
        output_json=args.output_json,
        output_md=args.output_md,
        output_xlsx=args.output_xlsx,
    )
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 0


def reuse_historical_labels_for_task(
    *,
    task_csv: str | Path,
    label_paths: list[str | Path],
    output_csv: str | Path,
    output_json: str | Path | None = None,
    output_md: str | Path | None = None,
    output_xlsx: str | Path | None = None,
) -> dict:
    task_path = Path(task_csv)
    task_rows, task_headers = _read_rows(task_path)
    label_rows = _trusted_labels(_label_files(label_paths))
    labels_by_provider: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in label_rows:
        labels_by_provider[row["provider_id"]].append(row)

    output_rows = []
    reusable_rows = []
    conflict_rows = []
    for row in task_rows:
        out = dict(row)
        provider_id = str(row.get("provider_id") or "").strip()
        labels = labels_by_provider.get(provider_id, [])
        chosen, conflict = _choose_label(labels)
        out["historical_label_status"] = "unlabeled"
        out["historical_label_source"] = ""
        out["historical_label_count"] = str(len(labels))
        if conflict:
            out["historical_label_status"] = "conflict"
            conflict_rows.append(_report_row(row, labels, status="conflict"))
        elif chosen:
            out["manual_decision"] = chosen["manual_decision"]
            if chosen.get("manual_url"):
                out["manual_url"] = chosen["manual_url"]
            out["notes"] = _merge_notes(row.get("notes", ""), chosen)
            out["historical_label_status"] = "reused"
            out["historical_label_source"] = chosen["source_path"]
            reusable_rows.append(_report_row(row, [chosen], status="reused"))
        output_rows.append(out)

    fields = _fields(task_headers)
    output_csv_path = Path(output_csv)
    _write_rows(output_csv_path, output_rows, fields)
    xlsx_summary = {}
    if output_xlsx:
        xlsx_summary = build_workbook([("Review_Task", output_csv_path)], output_xlsx)

    status_counts = Counter(row.get("historical_label_status", "") for row in output_rows)
    decision_counts = Counter(row.get("manual_decision", "") for row in output_rows if row.get("historical_label_status") == "reused")
    report = {
        "summary": {
            "task_rows": len(task_rows),
            "trusted_label_rows": len(label_rows),
            "reused_rows": status_counts.get("reused", 0),
            "conflict_rows": status_counts.get("conflict", 0),
            "unlabeled_rows": status_counts.get("unlabeled", 0),
            "reused_decision_counts": dict(decision_counts),
            "output_csv": str(output_csv_path),
            "output_xlsx": str(output_xlsx or ""),
        },
        "reused": reusable_rows,
        "conflicts": conflict_rows,
        "inputs": {
            "task_csv": str(task_path),
            "label_paths": [str(path) for path in label_paths],
            "trusted_source_markers": list(TRUSTED_SOURCE_MARKERS),
        },
        "xlsx": xlsx_summary,
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


def _trusted_labels(files: list[Path]) -> list[dict[str, str]]:
    labels = []
    for path in files:
        if not _is_trusted_source(path):
            continue
        rows, _headers = _read_rows(path)
        for idx, row in enumerate(rows, 2):
            provider_id = str(row.get("provider_id") or "").strip()
            decision = _normalize_decision(row.get("manual_decision") or row.get("decision") or "")
            if not provider_id or decision not in VALID_DECISIONS:
                continue
            manual_url = str(row.get("manual_url") or row.get("replacement_url") or "").strip()
            if decision == "replace" and not manual_url:
                continue
            labels.append(
                {
                    "provider_id": provider_id,
                    "manual_decision": decision,
                    "manual_url": manual_url,
                    "notes": str(row.get("notes") or row.get("manual_notes") or "").strip(),
                    "source_path": str(path),
                    "source_line": str(idx),
                }
            )
    return labels


def _choose_label(labels: list[dict[str, str]]) -> tuple[dict[str, str] | None, bool]:
    if not labels:
        return None, False
    signatures = {(row["manual_decision"], row.get("manual_url", "")) for row in labels}
    if len(signatures) > 1:
        return None, True
    return labels[0], False


def _report_row(task_row: dict[str, str], labels: list[dict[str, str]], *, status: str) -> dict[str, str]:
    return {
        "provider_id": str(task_row.get("provider_id") or ""),
        "provider_name": str(task_row.get("provider_name") or ""),
        "review_reason": str(task_row.get("review_reason") or ""),
        "status": status,
        "label_count": str(len(labels)),
        "decision": labels[0].get("manual_decision", "") if labels else "",
        "manual_url": labels[0].get("manual_url", "") if labels else "",
        "source_paths": "; ".join(sorted({row.get("source_path", "") for row in labels if row.get("source_path")})),
    }


def _merge_notes(existing: str, label: dict[str, str]) -> str:
    prefix = f"reused_historical_label:{Path(label['source_path']).name}:{label.get('source_line', '')}"
    label_notes = label.get("notes", "")
    parts = [part for part in [prefix, label_notes, existing] if part]
    return " | ".join(parts)


def _label_files(paths: list[str | Path]) -> list[Path]:
    out = []
    for value in paths:
        path = Path(value)
        if path.is_dir():
            out.extend(sorted(path.rglob("*.csv")))
        elif path.suffix.casefold() == ".csv":
            out.append(path)
    return out


def _is_trusted_source(path: Path) -> bool:
    name = path.name.casefold()
    return any(marker in name for marker in TRUSTED_SOURCE_MARKERS)


def _normalize_decision(value: object) -> str:
    raw = str(value or "").strip().casefold()
    aliases = {
        "accepted": "accept",
        "approve": "accept",
        "approved": "accept",
        "correct": "accept",
        "yes": "accept",
        "true": "accept",
        "正确": "accept",
        "对": "accept",
        "replacement": "replace",
        "修正": "replace",
        "替换": "replace",
        "rejected": "reject",
        "no": "reject",
        "false": "reject",
        "wrong": "reject",
        "incorrect": "reject",
        "错误": "reject",
        "错": "reject",
        "unknown": "unsure",
        "uncertain": "unsure",
        "不确定": "unsure",
    }
    return aliases.get(raw, raw)


def _fields(headers: list[str]) -> list[str]:
    fields = list(headers)
    for field in ["manual_decision", "manual_url", "notes", "historical_label_status", "historical_label_source", "historical_label_count"]:
        if field not in fields:
            fields.append(field)
    return fields


def _read_rows(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    with path.open(newline="", encoding="utf-8-sig", errors="ignore") as f:
        reader = csv.DictReader(f)
        return list(reader), list(reader.fieldnames or [])


def _write_rows(path: Path, rows: list[dict[str, str]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _render_markdown(report: dict) -> str:
    summary = report["summary"]
    lines = [
        "# Historical Label Reuse Report",
        "",
        f"- Task rows: {summary['task_rows']}",
        f"- Trusted label rows scanned: {summary['trusted_label_rows']}",
        f"- Reused rows: {summary['reused_rows']}",
        f"- Conflict rows: {summary['conflict_rows']}",
        f"- Still unlabeled rows: {summary['unlabeled_rows']}",
        f"- Output CSV: {summary['output_csv']}",
        f"- Output XLSX: {summary['output_xlsx']}",
        "",
        "## Reused Labels",
        "",
    ]
    for row in report.get("reused", []):
        lines.append(
            "- {provider_name}: {decision} ({review_reason}) from {source_paths}".format(
                provider_name=row["provider_name"],
                decision=row["decision"],
                review_reason=row["review_reason"],
                source_paths=row["source_paths"],
            )
        )
    if not report.get("reused"):
        lines.append("- None")
    lines.extend(["", "## Conflicts", ""])
    for row in report.get("conflicts", []):
        lines.append(f"- {row['provider_name']}: {row['label_count']} conflicting labels")
    if not report.get("conflicts"):
        lines.append("- None")
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
