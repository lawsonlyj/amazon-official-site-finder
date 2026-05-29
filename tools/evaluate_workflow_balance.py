from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from finder.text import domain_from_url


DETAIL_FIELDS = [
    "provider_id",
    "provider_name",
    "label_source",
    "expected_kind",
    "expected_domain",
    "expected_url",
    "output_status",
    "output_confidence",
    "output_domain",
    "output_url",
    "outcome",
    "manual_review_required",
    "manual_review_reason",
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate precision/coverage balance from human review labels.")
    parser.add_argument("--baseline-final", required=True, help="Baseline final CSV. Non-reviewed rows are treated as correct labels.")
    parser.add_argument("--candidate-final", required=True, help="Candidate workflow final CSV to evaluate.")
    parser.add_argument("--human-review", required=True, help="Filled human review CSV/XLSX with corrected yellow rows.")
    parser.add_argument("--run-dir", help="Optional candidate run dir, used to count review_task rows.")
    parser.add_argument(
        "--simulate-thresholds",
        default="",
        help="Comma-separated matched confidence thresholds to simulate by moving lower matched rows to unresolved.",
    )
    parser.add_argument("--output-json")
    parser.add_argument("--output-csv")
    args = parser.parse_args(argv)

    summary = evaluate_balance(
        baseline_final=args.baseline_final,
        candidate_final=args.candidate_final,
        human_review=args.human_review,
        run_dir=args.run_dir,
        output_json=args.output_json,
        output_csv=args.output_csv,
        simulate_thresholds=args.simulate_thresholds,
    )
    print(json.dumps(summary["overall"], ensure_ascii=False, indent=2))
    return 0


def evaluate_balance(
    *,
    baseline_final: str | Path,
    candidate_final: str | Path,
    human_review: str | Path,
    run_dir: str | Path | None = None,
    output_json: str | Path | None = None,
    output_csv: str | Path | None = None,
    simulate_thresholds: str | list[int] | None = None,
) -> dict:
    baseline_rows = _read_rows(Path(baseline_final))
    candidate_rows = _index_rows(_read_rows(Path(candidate_final)))
    review_rows = _index_rows(_read_table(Path(human_review)))
    labels = [_label_from_row(row, review_rows.get(_row_key(row), {})) for row in baseline_rows]
    labels = [label for label in labels if label]
    details = [_evaluate_label(label, _candidate_for_label(label, candidate_rows)) for label in labels]
    review_task_rows = None
    review_task_path = None
    if run_dir:
        review_task_path = _find_review_task(Path(run_dir))
        if review_task_path:
            review_task_rows = _read_rows(review_task_path)
            details = _annotate_manual_review(details, review_task_rows)
    overall = _summarize(details)
    if run_dir:
        run_dir = Path(run_dir)
        if review_task_rows is not None:
            overall.update(_summarize_manual_review_capture(details, len(review_task_rows)))
        unresolved = _find_unresolved(run_dir)
        if unresolved.exists():
            overall["unresolved_rows"] = len(_read_rows(unresolved))
    summary = {
        "overall": overall,
        "threshold_simulations": _threshold_simulations(labels, candidate_rows, simulate_thresholds),
        "inputs": {
            "baseline_final": str(baseline_final),
            "candidate_final": str(candidate_final),
            "human_review": str(human_review),
            "run_dir": str(run_dir) if run_dir else "",
            "review_task": str(review_task_path) if review_task_path else "",
        },
        "details": details,
    }
    if output_json:
        path = Path(output_json)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    if output_csv:
        _write_rows(Path(output_csv), details, DETAIL_FIELDS)
    return summary


def _threshold_simulations(
    labels: list[dict[str, str]],
    candidate_rows: dict[str, dict[str, str]],
    thresholds: str | list[int] | None,
) -> list[dict]:
    if not thresholds:
        return []
    if isinstance(thresholds, str):
        values = [item.strip() for item in thresholds.split(",") if item.strip()]
        threshold_values = []
        for value in values:
            try:
                threshold_values.append(int(float(value)))
            except ValueError:
                continue
    else:
        threshold_values = [int(value) for value in thresholds]
    out = []
    for threshold in threshold_values:
        rows = []
        for label in labels:
            candidate = dict(_candidate_for_label(label, candidate_rows))
            if _should_drop_for_threshold(candidate, threshold):
                candidate["official_url"] = ""
                candidate["official_domain"] = ""
                candidate["status"] = "unresolved"
                candidate["decision_source"] = f"threshold_sim:{threshold}"
            rows.append(_evaluate_label(label, candidate))
        overall = _summarize(rows)
        overall["threshold"] = threshold
        out.append(overall)
    return out


def _should_drop_for_threshold(row: dict[str, str], threshold: int) -> bool:
    if row.get("status") != "matched":
        return False
    if not row.get("official_url"):
        return False
    try:
        confidence = int(float(row.get("confidence") or 0))
    except (TypeError, ValueError):
        return True
    return confidence < threshold


def _label_from_row(row: dict[str, str], review_row: dict[str, str]) -> dict[str, str] | None:
    decision = _decision(review_row)
    provider_id = row.get("provider_id", "")
    provider_name = row.get("provider_name", "")
    if decision == "unsure":
        return None
    if decision == "replace":
        manual_url = _normalize_url(_first(review_row, "manual_url", "your_true_official_url", "true_official_url"))
        if manual_url:
            return _label(provider_id, provider_name, "human_replace", "official", manual_url)
        return _label(provider_id, provider_name, "human_replace_missing_url", "no_official", "")
    if decision == "reject":
        manual_url = _normalize_url(_first(review_row, "manual_url", "your_true_official_url", "true_official_url"))
        if manual_url:
            return _label(provider_id, provider_name, "human_reject_with_url", "official", manual_url)
        return _label(provider_id, provider_name, "human_reject", "no_official", "")
    if decision == "accept":
        manual_url = _normalize_url(_first(review_row, "manual_url", "your_true_official_url", "true_official_url"))
        accepted_url = manual_url or _normalize_url(_first(review_row, "official_url", "current_or_candidate_url", "candidate_url"))
        if accepted_url:
            return _label(provider_id, provider_name, "human_accept", "official", accepted_url)
        return _label(provider_id, provider_name, "human_accept_no_url", "no_official", "")
    baseline_url = _normalize_url(row.get("official_url", ""))
    if baseline_url:
        return _label(provider_id, provider_name, "baseline_unmarked_correct", "official", baseline_url)
    return _label(provider_id, provider_name, "baseline_unmarked_no_official", "no_official", "")


def _label(provider_id: str, provider_name: str, source: str, kind: str, url: str) -> dict[str, str]:
    return {
        "provider_id": provider_id,
        "provider_name": provider_name,
        "label_source": source,
        "expected_kind": kind,
        "expected_url": url,
        "expected_domain": domain_from_url(url) if url else "",
    }


def _evaluate_label(label: dict[str, str], row: dict[str, str]) -> dict[str, str]:
    output_url = _normalize_url(row.get("official_url", ""))
    output_domain = domain_from_url(row.get("official_domain", "") or output_url) if output_url else ""
    expected_kind = label["expected_kind"]
    expected_domain = label["expected_domain"]
    if expected_kind == "official":
        if output_domain and output_domain == expected_domain:
            outcome = "correct_official"
        elif output_domain:
            outcome = "false_official"
        else:
            outcome = "over_rejected"
    else:
        outcome = "false_official" if output_domain else "correct_no_official"
    return {
        **label,
        "output_status": row.get("status", ""),
        "output_confidence": row.get("confidence", ""),
        "output_domain": output_domain,
        "output_url": output_url,
        "outcome": outcome,
    }


def _candidate_for_label(label: dict[str, str], rows: dict[str, dict[str, str]]) -> dict[str, str]:
    provider_id = label.get("provider_id", "").strip()
    if provider_id and f"id:{provider_id}" in rows:
        return rows[f"id:{provider_id}"]
    provider_name = label.get("provider_name", "").strip().casefold()
    return rows.get(f"name:{provider_name}", {})


def _summarize(details: list[dict[str, str]]) -> dict:
    total = len(details)
    expected_official = sum(1 for row in details if row["expected_kind"] == "official")
    expected_no_official = total - expected_official
    correct_official = sum(1 for row in details if row["outcome"] == "correct_official")
    correct_no_official = sum(1 for row in details if row["outcome"] == "correct_no_official")
    false_official = sum(1 for row in details if row["outcome"] == "false_official")
    over_rejected = sum(1 for row in details if row["outcome"] == "over_rejected")
    official_outputs = correct_official + false_official
    return {
        "labeled_rows": total,
        "expected_official_rows": expected_official,
        "expected_no_official_rows": expected_no_official,
        "official_output_rows": official_outputs,
        "correct_official_rows": correct_official,
        "correct_no_official_rows": correct_no_official,
        "false_official_rows": false_official,
        "over_rejected_rows": over_rejected,
        "auto_precision": _ratio(correct_official, official_outputs),
        "official_recall": _ratio(correct_official, expected_official),
        "overall_accuracy": _ratio(correct_official + correct_no_official, total),
    }


def _annotate_manual_review(details: list[dict[str, str]], review_task_rows: list[dict[str, str]]) -> list[dict[str, str]]:
    review_index = _index_rows(review_task_rows)
    out = []
    for row in details:
        review_row = review_index.get(_row_key(row), {})
        annotated = dict(row)
        annotated["manual_review_required"] = "yes" if review_row else ""
        annotated["manual_review_reason"] = _first(review_row, "review_reason") if review_row else ""
        out.append(annotated)
    return out


def _summarize_manual_review_capture(details: list[dict[str, str]], review_task_rows: int) -> dict:
    reviewed = [row for row in details if row.get("manual_review_required") == "yes"]
    false_official_total = sum(1 for row in details if row["outcome"] == "false_official")
    over_rejected_total = sum(1 for row in details if row["outcome"] == "over_rejected")
    false_official_reviewed = sum(1 for row in reviewed if row["outcome"] == "false_official")
    over_rejected_reviewed = sum(1 for row in reviewed if row["outcome"] == "over_rejected")
    correct_official_reviewed = sum(1 for row in reviewed if row["outcome"] == "correct_official")
    correct_no_official_reviewed = sum(1 for row in reviewed if row["outcome"] == "correct_no_official")
    return {
        "manual_review_rows": review_task_rows,
        "manual_review_labeled_rows": len(reviewed),
        "manual_review_false_official_rows": false_official_reviewed,
        "manual_review_missed_false_official_rows": false_official_total - false_official_reviewed,
        "manual_review_over_rejected_rows": over_rejected_reviewed,
        "manual_review_missed_over_rejected_rows": over_rejected_total - over_rejected_reviewed,
        "manual_review_correct_official_rows": correct_official_reviewed,
        "manual_review_correct_no_official_rows": correct_no_official_reviewed,
        "manual_review_false_official_capture_rate": _ratio(false_official_reviewed, false_official_total),
        "manual_review_over_rejected_capture_rate": _ratio(over_rejected_reviewed, over_rejected_total),
        "manual_review_false_official_share": _ratio(false_official_reviewed, len(reviewed)),
        "manual_review_correct_official_share": _ratio(correct_official_reviewed, len(reviewed)),
    }


def _ratio(num: int, den: int) -> float | None:
    return round(num / den, 4) if den else None


def _find_review_task(run_dir: Path) -> Path | None:
    for name in ("review_task.csv", "manual_official_site_review_task.csv"):
        path = run_dir / name
        if path.exists():
            return path
    return None


def _find_unresolved(run_dir: Path) -> Path:
    for name in ("unresolved.csv", "provider_unresolved_second_pass.csv"):
        path = run_dir / name
        if path.exists():
            return path
    return run_dir / "unresolved.csv"


def _read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _read_table(path: Path) -> list[dict[str, str]]:
    if path.suffix.casefold() == ".xlsx":
        return _read_xlsx(path)
    return _read_rows(path)


def _read_xlsx(path: Path) -> list[dict[str, str]]:
    try:
        from openpyxl import load_workbook
    except ImportError:
        return []
    workbook = load_workbook(path, data_only=False, read_only=True)
    sheet = workbook.active
    rows = list(sheet.iter_rows())
    if not rows:
        return []
    headers = [_cell_text(cell.value) for cell in rows[0]]
    out = []
    for cells in rows[1:]:
        row = {headers[idx]: _cell_text(cells[idx].value) for idx in range(len(headers)) if headers[idx]}
        if any(row.values()):
            out.append(row)
    return out


def _index_rows(rows: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    return {_row_key(row): row for row in rows if _row_key(row)}


def _row_key(row: dict[str, str]) -> str:
    provider_id = str(row.get("provider_id") or "").strip()
    if provider_id:
        return f"id:{provider_id}"
    return f"name:{str(row.get('provider_name') or '').strip().casefold()}"


def _decision(row: dict[str, str]) -> str:
    raw = _first(row, "manual_decision", "your_decision", "decision").casefold()
    aliases = {
        "accept": "accept",
        "approve": "accept",
        "approved": "accept",
        "replace": "replace",
        "reject": "reject",
        "rejected": "reject",
        "unsure": "unsure",
    }
    return aliases.get(raw, raw)


def _first(row: dict[str, str], *keys: str) -> str:
    for key in keys:
        value = row.get(key)
        if value is not None and str(value).strip():
            return _cell_text(value)
    return ""


def _cell_text(value: object) -> str:
    text = str(value or "").strip()
    if text.startswith("="):
        text = text[1:]
    if text.upper().startswith("HYPERLINK("):
        match = re.search(r'HYPERLINK\("([^"]+)"', text, flags=re.IGNORECASE)
        return match.group(1).strip() if match else ""
    return text


def _normalize_url(value: object) -> str:
    raw = str(value or "").strip().replace("\xa0", "").rstrip(".,);]")
    if not raw:
        return ""
    parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    if not parsed.netloc:
        return ""
    path = parsed.path or ""
    return f"{parsed.scheme or 'https'}://{parsed.netloc}{path}".rstrip("/")


def _write_rows(path: Path, rows: list[dict[str, str]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    raise SystemExit(main())
