from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


DETAIL_FIELDS = [
    "provider_id",
    "provider_name",
    "sample_reason",
    "review_reason",
    "agent_b_decision",
    "reason_for_unsure",
    "official_url",
    "candidate_url",
    "manual_decision",
    "manual_url",
    "normalized_decision",
    "normalized_manual_url",
    "lane_kind",
    "calibration_outcome",
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate filled high-value calibration review labels.")
    parser.add_argument("--sample", required=True, help="Filled calibration sample CSV/XLSX.")
    parser.add_argument("--output-json")
    parser.add_argument("--output-md")
    parser.add_argument("--output-csv", help="Optional row-level normalized calibration outcomes.")
    args = parser.parse_args(argv)

    report = evaluate_calibration_review_sample(
        sample=args.sample,
        output_json=args.output_json,
        output_md=args.output_md,
        output_csv=args.output_csv,
    )
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 0


def evaluate_calibration_review_sample(
    *,
    sample: str | Path,
    output_json: str | Path | None = None,
    output_md: str | Path | None = None,
    output_csv: str | Path | None = None,
) -> dict:
    rows = _read_table(Path(sample))
    details = [_detail(row) for row in rows]
    labeled = [row for row in details if row["normalized_decision"]]
    decisive = [row for row in labeled if row["normalized_decision"] != "unsure"]
    summary = {
        "sample_rows": len(details),
        "labeled_rows": len(labeled),
        "decisive_rows": len(decisive),
        "manual_decision_counts": dict(Counter(row["normalized_decision"] for row in labeled)),
        "candidate_correct_rows": sum(1 for row in details if row["calibration_outcome"] == "candidate_correct"),
        "candidate_incorrect_rows": sum(1 for row in details if row["calibration_outcome"] == "candidate_incorrect"),
        "recall_useful_rows": sum(1 for row in details if row["calibration_outcome"] == "recall_candidate_useful"),
        "recall_not_useful_rows": sum(1 for row in details if row["calibration_outcome"] == "recall_candidate_not_useful"),
        "unsure_rows": sum(1 for row in details if row["calibration_outcome"] == "manual_unsure"),
    }
    report = {
        "summary": summary,
        "by_sample_reason": _group_stats(details, "sample_reason"),
        "by_review_reason": _group_stats(details, "review_reason"),
        "by_agent_b_decision": _group_stats(details, "agent_b_decision"),
        "recommendations": _recommendations(details),
        "details": details,
        "inputs": {"sample": str(sample)},
    }
    if output_json:
        path = Path(output_json)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if output_md:
        path = Path(output_md)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_render_markdown(report), encoding="utf-8")
    if output_csv:
        _write_rows(Path(output_csv), details, DETAIL_FIELDS)
    return report


def _detail(row: dict[str, str]) -> dict[str, str]:
    decision = _decision(row)
    lane_kind = _lane_kind(row)
    outcome = _outcome(decision, lane_kind)
    return {
        "provider_id": _first(row, "provider_id"),
        "provider_name": _first(row, "provider_name"),
        "sample_reason": _first(row, "sample_reason"),
        "review_reason": _first(row, "review_reason"),
        "agent_b_decision": _first(row, "agent_b_decision"),
        "reason_for_unsure": _first(row, "reason_for_unsure"),
        "official_url": _normalize_url(_first(row, "official_url")),
        "candidate_url": _normalize_url(_first(row, "candidate_url", "current_or_candidate_url", "official_url")),
        "manual_decision": _first(row, "manual_decision", "your_decision", "decision"),
        "manual_url": _first(row, "manual_url", "your_true_official_url", "true_official_url"),
        "normalized_decision": decision,
        "normalized_manual_url": _normalize_url(_first(row, "manual_url", "your_true_official_url", "true_official_url")),
        "lane_kind": lane_kind,
        "calibration_outcome": outcome,
    }


def _lane_kind(row: dict[str, str]) -> str:
    sample_reason = _first(row, "sample_reason")
    review_reason = _first(row, "review_reason")
    if review_reason == "recall_unresolved_top_candidate" or sample_reason == "recall_candidate_label":
        return "recall"
    if review_reason.startswith("precision_") or sample_reason in {
        "agent_b_accept_risky_lane",
        "agent_b_reject_check",
        "generic_identity_label",
        "slug_extension_label",
        "second_pass_threshold_label",
        "low_confidence_label",
        "timeout_needs_manual",
    }:
        return "precision"
    return "general"


def _outcome(decision: str, lane_kind: str) -> str:
    if not decision:
        return "unlabeled"
    if decision == "unsure":
        return "manual_unsure"
    if lane_kind == "recall":
        if decision in {"accept", "replace"}:
            return "recall_candidate_useful"
        return "recall_candidate_not_useful"
    if decision == "accept":
        return "candidate_correct"
    if decision in {"replace", "reject"}:
        return "candidate_incorrect"
    return "unlabeled"


def _group_stats(rows: list[dict[str, str]], field: str) -> dict[str, dict]:
    out: dict[str, dict] = {}
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        grouped[row.get(field, "") or "(blank)"].append(row)
    for key, items in sorted(grouped.items()):
        labeled = [row for row in items if row["normalized_decision"]]
        decisive = [row for row in labeled if row["normalized_decision"] != "unsure"]
        decisions = Counter(row["normalized_decision"] for row in labeled)
        outcomes = Counter(row["calibration_outcome"] for row in items)
        out[key] = {
            "rows": len(items),
            "labeled_rows": len(labeled),
            "decisive_rows": len(decisive),
            "decision_counts": dict(decisions),
            "outcome_counts": dict(outcomes),
            "candidate_correct_rate": _ratio(outcomes.get("candidate_correct", 0), len(decisive)),
            "candidate_incorrect_rate": _ratio(outcomes.get("candidate_incorrect", 0), len(decisive)),
            "recall_useful_rate": _ratio(outcomes.get("recall_candidate_useful", 0), len(decisive)),
        }
    return out


def _recommendations(details: list[dict[str, str]]) -> list[str]:
    labeled = [row for row in details if row["normalized_decision"]]
    decisive = [row for row in labeled if row["normalized_decision"] != "unsure"]
    if not labeled:
        return [
            "No filled calibration labels yet. Fill manual_decision, manual_url, and notes before changing thresholds or review lanes."
        ]

    recommendations: list[str] = []
    precision_rows = [row for row in decisive if row["lane_kind"] == "precision"]
    precision_bad = [row for row in precision_rows if row["calibration_outcome"] == "candidate_incorrect"]
    recall_rows = [row for row in decisive if row["lane_kind"] == "recall"]
    recall_useful = [row for row in recall_rows if row["calibration_outcome"] == "recall_candidate_useful"]
    agent_b_accepts = [
        row for row in decisive if row["agent_b_decision"] == "accept" or row["sample_reason"] == "agent_b_accept_risky_lane"
    ]
    risky_accept_bad = [row for row in agent_b_accepts if row["calibration_outcome"] in {"candidate_incorrect", "recall_candidate_not_useful"}]
    if risky_accept_bad:
        recommendations.append(
            "Keep AgentB risky accepts in manual review; human labels still show incorrect accepted candidates in risky lanes."
        )
    elif len(agent_b_accepts) >= 10:
        recommendations.append(
            "AgentB risky accepts had no labeled corrections in this sample; consider a narrow release rule only for the exact evidence pattern."
        )

    if precision_bad:
        recommendations.append(
            "Do not globally lower acceptance thresholds yet; precision lanes still contain bad official-site candidates."
        )
    elif len(precision_rows) >= 10:
        recommendations.append(
            "Precision-lane labels show no bad candidates in this sample; consider narrowing the reviewed lane rather than changing the global threshold."
        )

    generic_or_slug = [
        row
        for row in decisive
        if row["review_reason"] in {"precision_generic_identity_term_risk", "precision_slug_extension_identity_risk"}
    ]
    generic_or_slug_bad = [row for row in generic_or_slug if row["calibration_outcome"] == "candidate_incorrect"]
    generic_or_slug_good = [row for row in generic_or_slug if row["calibration_outcome"] == "candidate_correct"]
    if generic_or_slug_bad:
        recommendations.append("Keep generic-name and slug-extension identity constraints; the sample still has same-name/domain-shape mistakes.")
    elif len(generic_or_slug_good) >= 5:
        recommendations.append(
            "Generic-name and slug-extension labels are mostly correct; consider requiring only manual review when service/country evidence is also weak."
        )

    if recall_useful:
        recommendations.append(
            "Add recall examples from accepted/replaced unresolved rows to query and low-score strong-identity tests instead of lowering the global threshold."
        )
    if recall_rows and _ratio(len(recall_useful), len(recall_rows)) is not None and len(recall_useful) < len(recall_rows):
        recommendations.append("Keep unresolved recall rows as human/AgentB evidence only; not every top candidate is useful.")

    timeout_rows = [row for row in decisive if row["sample_reason"] == "timeout_needs_manual" or row["reason_for_unsure"] == "agent_b_row_timeout"]
    timeout_useful = [
        row
        for row in timeout_rows
        if row["calibration_outcome"] in {"candidate_correct", "recall_candidate_useful"}
    ]
    if timeout_rows and _ratio(len(timeout_useful), len(timeout_rows)) and _ratio(len(timeout_useful), len(timeout_rows)) >= 0.5:
        recommendations.append("Retry AgentB timeout rows with resume/longer timeout before manual review; many timed-out rows are useful candidates.")
    elif timeout_rows:
        recommendations.append("Keep timeout rows in manual review priority; current labels do not justify auto-accepting timed-out candidates.")

    if not recommendations:
        recommendations.append("Labels are mixed or sparse; keep current threshold and collect more calibration rows before changing rules.")
    return recommendations


def _decision(row: dict[str, str]) -> str:
    raw = _first(row, "manual_decision", "your_decision", "decision").casefold()
    aliases = {
        "accept": "accept",
        "accepted": "accept",
        "approve": "accept",
        "approved": "accept",
        "correct": "accept",
        "yes": "accept",
        "true": "accept",
        "正确": "accept",
        "对": "accept",
        "replace": "replace",
        "replacement": "replace",
        "修正": "replace",
        "替换": "replace",
        "reject": "reject",
        "rejected": "reject",
        "no": "reject",
        "false": "reject",
        "wrong": "reject",
        "incorrect": "reject",
        "错误": "reject",
        "错": "reject",
        "无官网": "reject",
        "unsure": "unsure",
        "unknown": "unsure",
        "uncertain": "unsure",
        "不确定": "unsure",
    }
    return aliases.get(raw, raw if raw in {"accept", "replace", "reject", "unsure"} else "")


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


def _read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _write_rows(path: Path, rows: list[dict[str, str]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _render_markdown(report: dict) -> str:
    summary = report["summary"]
    lines = [
        "# Calibration Review Evaluation",
        "",
        "## Summary",
        "",
        f"- Sample rows: {summary['sample_rows']}",
        f"- Labeled rows: {summary['labeled_rows']}",
        f"- Decisive rows: {summary['decisive_rows']}",
        f"- Candidate correct rows: {summary['candidate_correct_rows']}",
        f"- Candidate incorrect rows: {summary['candidate_incorrect_rows']}",
        f"- Recall useful rows: {summary['recall_useful_rows']}",
        f"- Recall not useful rows: {summary['recall_not_useful_rows']}",
        "",
        "## Recommendations",
        "",
    ]
    for item in report["recommendations"]:
        lines.append(f"- {item}")
    lines.extend(["", "## Sample Reasons", ""])
    for reason, stats in report["by_sample_reason"].items():
        decisions = ", ".join(f"{key}={value}" for key, value in stats["decision_counts"].items()) or "none"
        outcomes = ", ".join(f"{key}={value}" for key, value in stats["outcome_counts"].items()) or "none"
        lines.append(f"- {reason}: rows={stats['rows']}, labeled={stats['labeled_rows']}, decisions=({decisions}), outcomes=({outcomes})")
    lines.extend(["", "## Review Reasons", ""])
    for reason, stats in report["by_review_reason"].items():
        outcomes = ", ".join(f"{key}={value}" for key, value in stats["outcome_counts"].items()) or "none"
        lines.append(f"- {reason}: rows={stats['rows']}, labeled={stats['labeled_rows']}, outcomes=({outcomes})")
    lines.append("")
    return "\n".join(lines)


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
    return f"{parsed.scheme or 'https'}://{parsed.netloc}{parsed.path or ''}".rstrip("/")


def _ratio(num: int, den: int) -> float | None:
    return round(num / den, 4) if den else None


if __name__ == "__main__":
    raise SystemExit(main())
