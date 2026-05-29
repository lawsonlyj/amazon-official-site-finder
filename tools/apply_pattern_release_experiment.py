from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from finder.finalize import FINAL_FIELDS, write_rows
from finder.text import domain_from_url
from tools.build_linked_workbook import build_workbook
from tools.mine_evidence_patterns import features_for_review_agent_row


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Apply evidence-pattern releases to an experimental final CSV.")
    parser.add_argument("--final-csv", required=True, help="Base official_sites.csv to copy.")
    parser.add_argument("--agent-b-csv", required=True, help="AgentB check.csv with candidate evidence.")
    parser.add_argument(
        "--pattern-json",
        action="append",
        required=True,
        help="Pattern release simulation JSON or rule candidate JSON. Repeatable.",
    )
    parser.add_argument("--output-csv", required=True)
    parser.add_argument("--output-xlsx")
    parser.add_argument("--summary-json")
    parser.add_argument("--summary-md")
    parser.add_argument(
        "--include-non-actionable",
        action="store_true",
        help="Also allow non-actionable safe_patterns from pattern_release_simulation.json. Default uses actionable patterns only.",
    )
    args = parser.parse_args(argv)

    summary = apply_pattern_release_experiment(
        final_csv=args.final_csv,
        agent_b_csv=args.agent_b_csv,
        pattern_jsons=args.pattern_json,
        output_csv=args.output_csv,
        output_xlsx=args.output_xlsx,
        summary_json=args.summary_json,
        summary_md=args.summary_md,
        include_non_actionable=args.include_non_actionable,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def apply_pattern_release_experiment(
    *,
    final_csv: str | Path,
    agent_b_csv: str | Path,
    pattern_jsons: list[str | Path],
    output_csv: str | Path,
    output_xlsx: str | Path | None = None,
    summary_json: str | Path | None = None,
    summary_md: str | Path | None = None,
    include_non_actionable: bool = False,
) -> dict:
    final_rows = _read_rows(Path(final_csv))
    agent_rows = {_row_key(row): row for row in _read_rows(Path(agent_b_csv)) if _row_key(row)}
    patterns = _load_release_patterns(pattern_jsons, include_non_actionable=include_non_actionable)
    released = []
    out_rows = []
    for row in final_rows:
        agent_row = agent_rows.get(_row_key(row), {})
        pattern = _matching_pattern(row, agent_row, patterns)
        if pattern and _can_release(row, agent_row):
            released_row = _released_row(row, agent_row, pattern)
            out_rows.append(released_row)
            released.append(released_row)
        else:
            out_rows.append(dict(row))
    write_rows(output_csv, out_rows, FINAL_FIELDS)
    xlsx_summary = {}
    if output_xlsx:
        xlsx_summary = build_workbook([("Pattern_Release", output_csv)], output_xlsx)
    summary = {
        "base_final_csv": str(final_csv),
        "agent_b_csv": str(agent_b_csv),
        "pattern_jsons": [str(path) for path in pattern_jsons],
        "output_csv": str(output_csv),
        "output_xlsx": str(output_xlsx or ""),
        "input_rows": len(final_rows),
        "pattern_count": len(patterns),
        "released_rows": len(released),
        "released_provider_ids": [row.get("provider_id", "") for row in released],
        "released_provider_names": [row.get("provider_name", "") for row in released],
        "released_domains": dict(Counter(row.get("official_domain", "") for row in released)),
        "status_counts": dict(Counter(row.get("status", "") for row in out_rows)),
        "xlsx": xlsx_summary,
    }
    if summary_json:
        path = Path(summary_json)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    if summary_md:
        path = Path(summary_md)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_render_markdown(summary), encoding="utf-8")
    return summary


def _load_release_patterns(
    paths: list[str | Path],
    *,
    include_non_actionable: bool,
) -> list[dict]:
    patterns: list[dict] = []
    seen: set[tuple[str, ...]] = set()
    for path_like in paths:
        data = json.loads(Path(path_like).read_text(encoding="utf-8"))
        selected = data.get("selected_actionable_pattern_set")
        items = list(selected if isinstance(selected, list) else data.get("actionable_safe_patterns") or [])
        if include_non_actionable:
            items.extend(data.get("safe_patterns") or [])
        candidates = data.get("candidate_for_rule")
        if isinstance(candidates, list):
            items.extend(candidates)
        if isinstance(data.get("pattern_rule_candidates"), dict):
            items.extend(data["pattern_rule_candidates"].get("candidate_for_rule") or [])
        for item in items:
            features = _features_for_pattern(item)
            if not features:
                continue
            key = tuple(sorted(features))
            if key in seen:
                continue
            seen.add(key)
            patterns.append(
                {
                    "pattern": item.get("pattern") or " AND ".join(sorted(features)),
                    "features": features,
                    "correct_recovery_rows": _to_int(item.get("correct_recovery_rows") or item.get("supporting_rows")),
                    "wrong_release_rows": _to_int(item.get("wrong_release_rows") or item.get("blocking_rows")),
                    "actionable": bool(item.get("actionable", True)),
                }
            )
    patterns.sort(key=lambda item: (-item["correct_recovery_rows"], item["wrong_release_rows"], len(item["features"])))
    return patterns


def _features_for_pattern(item: dict) -> set[str]:
    features = item.get("features")
    if isinstance(features, list):
        return {str(feature).strip() for feature in features if str(feature).strip()}
    pattern = str(item.get("pattern") or "")
    return {part.strip() for part in pattern.split(" AND ") if part.strip()}


def _matching_pattern(row: dict[str, str], agent_row: dict[str, str], patterns: list[dict]) -> dict | None:
    if not agent_row:
        return None
    features = features_for_review_agent_row(row, agent_row)
    for pattern in patterns:
        if pattern["features"] <= features:
            return pattern
    return None


def _can_release(row: dict[str, str], agent_row: dict[str, str]) -> bool:
    if row.get("official_url"):
        return False
    if row.get("status") not in {"unresolved", "low_confidence", "not_found", "needs_review", ""}:
        return False
    if agent_row.get("review_reason") and not agent_row.get("review_reason", "").startswith("recall_unresolved"):
        return False
    candidate_url = _normalize_url(agent_row.get("candidate_url", ""))
    if not candidate_url or not domain_from_url(candidate_url):
        return False
    if agent_row.get("counter_evidence") and "candidate_not_independent_official_site" in agent_row["counter_evidence"]:
        return False
    return True


def _released_row(row: dict[str, str], agent_row: dict[str, str], pattern: dict) -> dict[str, str]:
    out = {field: row.get(field, "") for field in FINAL_FIELDS}
    url = _normalize_url(agent_row.get("candidate_url", ""))
    domain = domain_from_url(agent_row.get("candidate_domain") or url)
    out.update(
        {
            "official_url": url,
            "official_domain": domain,
            "status": "experimental_released",
            "decision_source": "pattern_release_experiment",
            "confidence": agent_row.get("evidence_score") or agent_row.get("confidence") or row.get("confidence", ""),
            "source_status": row.get("status", ""),
            "evidence_summary": _append_note(
                row.get("evidence_summary", ""),
                f"pattern_release:{pattern.get('pattern', '')}",
            ),
            "notes": _append_note(row.get("notes", ""), "experimental_pattern_release"),
        }
    )
    return out


def _render_markdown(summary: dict) -> str:
    lines = [
        "# Pattern Release Experiment",
        "",
        f"- Input rows: {summary['input_rows']}",
        f"- Patterns: {summary['pattern_count']}",
        f"- Released rows: {summary['released_rows']}",
        f"- Output CSV: {summary['output_csv']}",
        f"- Output XLSX: {summary['output_xlsx']}",
        "",
        "## Released Rows",
        "",
    ]
    if not summary["released_provider_ids"]:
        lines.append("- None")
    else:
        for provider_id, name in zip(summary["released_provider_ids"], summary["released_provider_names"]):
            lines.append(f"- {provider_id}: {name}")
    lines.append("")
    return "\n".join(lines)


def _normalize_url(value: object) -> str:
    raw = str(value or "").strip().rstrip(".,);]")
    if not raw:
        return ""
    parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    if not parsed.netloc:
        return ""
    path = parsed.path or "/"
    return f"{parsed.scheme or 'https'}://{parsed.netloc}{path}".rstrip("/") + ("/" if path == "/" else "")


def _append_note(existing: str, note: str) -> str:
    if not note:
        return existing
    if not existing:
        return note
    return f"{existing}; {note}"


def _read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _row_key(row: dict[str, str]) -> str:
    provider_id = (row.get("provider_id") or "").strip()
    if provider_id:
        return f"id:{provider_id}"
    provider_name = (row.get("provider_name") or "").strip().casefold()
    return f"name:{provider_name}" if provider_name else ""


def _to_int(value: object) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
