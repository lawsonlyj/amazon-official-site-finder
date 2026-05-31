from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from finder.text import domain_from_url
from tools.apply_pattern_release_experiment import (
    _can_release as can_release_pattern,
    _load_release_patterns,
    _matching_pattern as matching_release_pattern,
    _normalize_url,
)
from tools.build_linked_workbook import build_workbook
from tools.mine_evidence_patterns import features_for_review_agent_row


VALIDATION_FIELDS = [
    "provider_id",
    "provider_name",
    "provider_detail_url",
    "candidate_policy_action",
    "candidate_policy_pattern",
    "candidate_policy_source",
    "current_status",
    "current_official_url",
    "current_official_domain",
    "candidate_url",
    "candidate_domain",
    "review_reason",
    "agent_b_decision",
    "confidence",
    "evidence_score",
    "reason_for_unsure",
    "supporting_facts",
    "counter_evidence",
    "known_label_status",
    "expected_kind",
    "expected_url",
    "expected_domain",
    "service_apis",
    "provider_locations",
    "review_instruction",
    "manual_decision",
    "manual_url",
    "notes",
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build a small human validation task for candidate holdout/release policy rules."
    )
    parser.add_argument("--final-csv", required=True, help="Candidate official_sites.csv.")
    parser.add_argument("--review-task-csv", required=True, help="review_task.csv/manual_official_site_review_task.csv.")
    parser.add_argument("--agent-b-csv", required=True, help="Check and Suggestion check.csv.")
    parser.add_argument(
        "--hold-review-reason",
        action="append",
        default=[],
        help="review_reason to validate as a holdout policy. Repeatable or comma-separated.",
    )
    parser.add_argument(
        "--hold-pattern",
        action="append",
        default=[],
        help="Check and Suggestion evidence feature pattern to validate as a holdout policy. Repeatable. Use 'feature AND feature'.",
    )
    parser.add_argument(
        "--release-pattern",
        action="append",
        default=[],
        help="Check and Suggestion evidence feature pattern to validate as a release policy. Repeatable.",
    )
    parser.add_argument(
        "--release-pattern-json",
        action="append",
        default=[],
        help="Pattern release simulation JSON or rule candidate JSON. Repeatable.",
    )
    parser.add_argument(
        "--labeled-details",
        action="append",
        default=[],
        help="Optional labeled balance details JSON/CSV. Repeatable. Decisively labeled rows are skipped by default.",
    )
    parser.add_argument("--include-labeled", action="store_true", help="Include rows that already have decisive labels.")
    parser.add_argument("--output-csv", required=True)
    parser.add_argument("--output-xlsx")
    parser.add_argument("--summary-json")
    parser.add_argument("--summary-md")
    args = parser.parse_args(argv)

    report = build_policy_validation_task(
        final_csv=args.final_csv,
        review_task_csv=args.review_task_csv,
        agent_b_csv=args.agent_b_csv,
        hold_review_reasons=args.hold_review_reason,
        hold_patterns=args.hold_pattern,
        release_patterns=args.release_pattern,
        release_pattern_jsons=args.release_pattern_json,
        labeled_details=args.labeled_details,
        include_labeled=args.include_labeled,
        output_csv=args.output_csv,
        output_xlsx=args.output_xlsx,
        summary_json=args.summary_json,
        summary_md=args.summary_md,
    )
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 0


def build_policy_validation_task(
    *,
    final_csv: str | Path,
    review_task_csv: str | Path,
    agent_b_csv: str | Path,
    hold_review_reasons: list[str] | None = None,
    hold_patterns: list[str] | None = None,
    release_patterns: list[str] | None = None,
    release_pattern_jsons: list[str | Path] | None = None,
    labeled_details: list[str | Path] | None = None,
    include_labeled: bool = False,
    output_csv: str | Path,
    output_xlsx: str | Path | None = None,
    summary_json: str | Path | None = None,
    summary_md: str | Path | None = None,
) -> dict:
    final_rows = _read_rows(Path(final_csv))
    review_rows = _read_rows(Path(review_task_csv))
    agent_rows = {_row_key(row): row for row in _read_rows(Path(agent_b_csv)) if _row_key(row)}
    review_rows_by_key = {_row_key(row): row for row in review_rows if _row_key(row)}
    review_reasons = _review_reason_index(review_rows)
    hold_reason_set = _normalize_values(hold_review_reasons or [])
    parsed_hold_patterns = _parse_feature_patterns(hold_patterns or [])
    parsed_release_patterns = _load_validation_release_patterns(
        release_patterns or [],
        release_pattern_jsons or [],
    )
    labels = _load_label_index(labeled_details or [])

    output_rows: list[dict[str, str]] = []
    skipped_labeled_rows: list[dict[str, str]] = []
    matched_rows: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for row in final_rows:
        key = _row_key(row)
        review_row = review_rows_by_key.get(key, {})
        agent_row = agent_rows.get(key, {})
        label = labels.get(key, {})

        for candidate in _candidate_policy_rows(
            row=row,
            review_row=review_row,
            agent_row=agent_row,
            review_reason=review_reasons.get(key, ""),
            hold_review_reasons=hold_reason_set,
            hold_patterns=parsed_hold_patterns,
            release_patterns=parsed_release_patterns,
        ):
            dedupe_key = (
                candidate.get("provider_id", ""),
                candidate.get("candidate_policy_action", ""),
                candidate.get("candidate_policy_pattern", ""),
            )
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            labeled_candidate = _with_label(candidate, label)
            matched_rows.append(labeled_candidate)
            if not include_labeled and _is_decisively_labeled(label):
                skipped_labeled_rows.append(labeled_candidate)
                continue
            output_rows.append(labeled_candidate)

    output_csv = Path(output_csv)
    _write_rows(output_csv, output_rows, VALIDATION_FIELDS)
    xlsx_summary = build_workbook([("Policy_Validation", output_csv)], output_xlsx) if output_xlsx else {}
    summary = {
        "input_rows": len(final_rows),
        "matched_candidate_rows": len(matched_rows),
        "output_rows": len(output_rows),
        "skipped_labeled_rows": len(skipped_labeled_rows),
        "include_labeled": include_labeled,
        "hold_review_reasons": sorted(hold_reason_set),
        "hold_patterns": [" AND ".join(pattern) for pattern in parsed_hold_patterns],
        "release_patterns": [pattern["pattern"] for pattern in parsed_release_patterns],
        "action_counts": dict(Counter(row.get("candidate_policy_action", "") for row in output_rows)),
        "pattern_counts": dict(Counter(row.get("candidate_policy_pattern", "") for row in output_rows)),
        "known_label_counts": dict(Counter(row.get("known_label_status", "") for row in output_rows)),
        "output_csv": str(output_csv),
        "output_xlsx": str(output_xlsx or ""),
        "xlsx": xlsx_summary,
    }
    report = {
        "summary": summary,
        "rows": output_rows,
        "skipped_labeled_rows": skipped_labeled_rows,
        "inputs": {
            "final_csv": str(final_csv),
            "review_task_csv": str(review_task_csv),
            "agent_b_csv": str(agent_b_csv),
            "labeled_details": [str(path) for path in labeled_details or []],
        },
    }
    if summary_json:
        path = Path(summary_json)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if summary_md:
        path = Path(summary_md)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_render_markdown(report), encoding="utf-8")
    return report


def _candidate_policy_rows(
    *,
    row: dict[str, str],
    review_row: dict[str, str],
    agent_row: dict[str, str],
    review_reason: str,
    hold_review_reasons: set[str],
    hold_patterns: list[tuple[str, ...]],
    release_patterns: list[dict],
) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    hold_pattern = _matching_hold_pattern(review_row, agent_row, hold_patterns)
    if row.get("official_url") and (review_reason in hold_review_reasons or hold_pattern):
        pattern = hold_pattern or f"review_reason:{review_reason}"
        out.append(
            _validation_row(
                row=row,
                review_row=review_row,
                agent_row=agent_row,
                action="holdout",
                pattern=pattern,
                source="hold_pattern" if hold_pattern else "hold_review_reason",
                candidate_url=row.get("official_url", ""),
                candidate_domain=row.get("official_domain", "") or domain_from_url(row.get("official_url", "")),
                instruction="Check whether this current official URL should be held for human review instead of auto-accepted.",
            )
        )

    release_pattern = matching_release_pattern(row, agent_row, release_patterns)
    if release_pattern and can_release_pattern(row, agent_row):
        candidate_url = _normalize_url(agent_row.get("candidate_url", ""))
        out.append(
            _validation_row(
                row=row,
                review_row=review_row,
                agent_row=agent_row,
                action="release",
                pattern=release_pattern.get("pattern", ""),
                source="release_pattern",
                candidate_url=candidate_url,
                candidate_domain=domain_from_url(agent_row.get("candidate_domain") or candidate_url),
                instruction="Check whether this unresolved candidate is strong enough to auto-release in future runs.",
            )
        )
    return out


def _validation_row(
    *,
    row: dict[str, str],
    review_row: dict[str, str],
    agent_row: dict[str, str],
    action: str,
    pattern: str,
    source: str,
    candidate_url: str,
    candidate_domain: str,
    instruction: str,
) -> dict[str, str]:
    return {
        "provider_id": row.get("provider_id", "") or review_row.get("provider_id", "") or agent_row.get("provider_id", ""),
        "provider_name": row.get("provider_name", "")
        or review_row.get("provider_name", "")
        or agent_row.get("provider_name", ""),
        "provider_detail_url": row.get("provider_detail_url", "")
        or review_row.get("provider_detail_url", "")
        or agent_row.get("provider_detail_url", ""),
        "candidate_policy_action": action,
        "candidate_policy_pattern": pattern,
        "candidate_policy_source": source,
        "current_status": row.get("status", ""),
        "current_official_url": row.get("official_url", ""),
        "current_official_domain": row.get("official_domain", "") or domain_from_url(row.get("official_url", "")),
        "candidate_url": candidate_url,
        "candidate_domain": candidate_domain,
        "review_reason": review_row.get("review_reason", "") or agent_row.get("review_reason", ""),
        "agent_b_decision": agent_row.get("agent_b_decision", ""),
        "confidence": agent_row.get("confidence", "") or row.get("confidence", ""),
        "evidence_score": agent_row.get("evidence_score", ""),
        "reason_for_unsure": agent_row.get("reason_for_unsure", ""),
        "supporting_facts": agent_row.get("supporting_facts", ""),
        "counter_evidence": agent_row.get("counter_evidence", ""),
        "known_label_status": "unlabeled",
        "expected_kind": "",
        "expected_url": "",
        "expected_domain": "",
        "service_apis": row.get("service_apis", "") or review_row.get("service_apis", ""),
        "provider_locations": row.get("provider_locations", "") or review_row.get("provider_locations", ""),
        "review_instruction": instruction,
        "manual_decision": "",
        "manual_url": "",
        "notes": "",
    }


def _with_label(row: dict[str, str], label: dict[str, str]) -> dict[str, str]:
    out = dict(row)
    expected_kind = str(label.get("expected_kind") or "").strip()
    out["expected_kind"] = expected_kind
    out["expected_url"] = str(label.get("expected_url") or "").strip()
    out["expected_domain"] = str(label.get("expected_domain") or "").strip()
    if expected_kind in {"official", "no_official"}:
        out["known_label_status"] = f"labeled_{expected_kind}"
    elif expected_kind:
        out["known_label_status"] = f"labeled_{expected_kind}"
    else:
        out["known_label_status"] = "unlabeled"
    return out


def _load_validation_release_patterns(patterns: list[str], pattern_jsons: list[str | Path]) -> list[dict]:
    loaded = _load_release_patterns(pattern_jsons, include_non_actionable=False) if pattern_jsons else []
    seen = {tuple(sorted(item.get("features", []))) for item in loaded}
    for pattern in _parse_feature_patterns(patterns):
        key = tuple(sorted(pattern))
        if key in seen:
            continue
        seen.add(key)
        loaded.append(
            {
                "pattern": " AND ".join(pattern),
                "features": set(pattern),
                "correct_recovery_rows": 0,
                "wrong_release_rows": 0,
                "actionable": True,
            }
        )
    return loaded


def _matching_hold_pattern(
    review_row: dict[str, str],
    agent_row: dict[str, str],
    patterns: list[tuple[str, ...]],
) -> str:
    if not patterns:
        return ""
    if not review_row and not agent_row:
        return ""
    features = features_for_review_agent_row(review_row, agent_row)
    for pattern in patterns:
        if set(pattern) <= features:
            return " AND ".join(pattern)
    return ""


def _review_reason_index(rows: list[dict[str, str]]) -> dict[str, str]:
    out = {}
    for row in rows:
        key = _row_key(row)
        reason = str(row.get("review_reason") or "").strip()
        if key and reason:
            out[key] = reason
    return out


def _load_label_index(paths: list[str | Path]) -> dict[str, dict[str, str]]:
    labels: dict[str, dict[str, str]] = {}
    for path_like in paths:
        path = Path(path_like)
        if not path.exists():
            continue
        if path.suffix.casefold() == ".json":
            data = json.loads(path.read_text(encoding="utf-8"))
            rows = data.get("details", data if isinstance(data, list) else [])
        else:
            rows = _read_rows(path)
        for row in rows:
            if not isinstance(row, dict):
                continue
            key = _row_key(row)
            if key and key not in labels:
                labels[key] = {
                    "expected_kind": str(row.get("expected_kind") or ""),
                    "expected_url": str(row.get("expected_url") or ""),
                    "expected_domain": str(row.get("expected_domain") or ""),
                }
    return labels


def _is_decisively_labeled(label: dict[str, str]) -> bool:
    return str(label.get("expected_kind") or "").strip() in {"official", "no_official"}


def _normalize_values(values: list[str]) -> set[str]:
    out: set[str] = set()
    for value in values:
        for item in str(value or "").split(","):
            item = item.strip()
            if item:
                out.add(item)
    return out


def _parse_feature_patterns(values: list[str]) -> list[tuple[str, ...]]:
    out: list[tuple[str, ...]] = []
    seen: set[tuple[str, ...]] = set()
    for value in values:
        features = tuple(sorted(part.strip() for part in str(value or "").split(" AND ") if part.strip()))
        if features and features not in seen:
            seen.add(features)
            out.append(features)
    return out


def _read_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _write_rows(path: Path, rows: list[dict[str, str]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def _row_key(row: dict[str, str]) -> str:
    provider_id = str(row.get("provider_id") or "").strip()
    if provider_id:
        return f"id:{provider_id}"
    provider_name = str(row.get("provider_name") or "").strip().casefold()
    return f"name:{provider_name}" if provider_name else ""


def _render_markdown(report: dict) -> str:
    summary = report["summary"]
    lines = [
        "# Policy Validation Task",
        "",
        f"- Matched candidate rows: {summary['matched_candidate_rows']}",
        f"- Output rows: {summary['output_rows']}",
        f"- Skipped already labeled rows: {summary['skipped_labeled_rows']}",
        f"- Output CSV: {summary['output_csv']}",
        f"- Output XLSX: {summary['output_xlsx'] or 'not written'}",
        "",
        "## Action Counts",
        "",
    ]
    if summary["action_counts"]:
        for action, count in sorted(summary["action_counts"].items()):
            lines.append(f"- {action}: {count}")
    else:
        lines.append("- None")
    lines.extend(["", "## Rows To Validate", ""])
    if not report["rows"]:
        lines.append("- None")
    else:
        for row in report["rows"][:100]:
            lines.append(
                "- {candidate_policy_action}: {provider_name} ({provider_id}) -> {candidate_url} :: {candidate_policy_pattern}".format(
                    **row
                )
            )
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
