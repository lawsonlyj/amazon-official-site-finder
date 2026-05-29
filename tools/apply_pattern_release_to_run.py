from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from finder.finalize import FINAL_FIELDS, write_rows
from finder.scoring import load_config
from finder.text import domain_from_url
from tools.apply_pattern_release_experiment import (
    _append_note,
    _can_release,
    _load_release_patterns,
    _matching_pattern,
    _normalize_url,
    _row_key,
)
from tools.build_linked_workbook import build_workbook
from tools.build_manual_review_task import build_manual_review_task
from tools.evaluate_labeled_results import read_rows as read_csv_rows
from tools.output_layout import (
    agent_b_paths,
    first_existing,
    publish_second_pass_aliases,
    second_pass_paths,
)
from tools.quality_gate import evaluate_quality_gate, write_markdown as write_quality_markdown


UNRESOLVED_STATUSES = {"unresolved", "rejected", "invalid_manual_decision", "needs_review", "low_confidence", "not_found"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Apply calibrated pattern-release rules to a run's canonical outputs.")
    parser.add_argument("--run-dir", required=True)
    parser.add_argument(
        "--pattern-json",
        action="append",
        required=True,
        help="Pattern release simulation JSON. Repeatable.",
    )
    parser.add_argument("--config", default="config/scoring.json")
    parser.add_argument("--labels")
    parser.add_argument("--write-xlsx", action="store_true")
    parser.add_argument("--no-backup", action="store_true")
    parser.add_argument("--summary-json")
    args = parser.parse_args(argv)

    summary = apply_pattern_release_to_run(
        run_dir=args.run_dir,
        pattern_jsons=args.pattern_json,
        config_path=args.config,
        labels_csv=args.labels,
        write_xlsx=args.write_xlsx,
        backup=not args.no_backup,
        summary_json=args.summary_json,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def apply_pattern_release_to_run(
    *,
    run_dir: str | Path,
    pattern_jsons: list[str | Path],
    config_path: str | Path = "config/scoring.json",
    labels_csv: str | Path | None = None,
    write_xlsx: bool = True,
    backup: bool = True,
    summary_json: str | Path | None = None,
) -> dict:
    run_dir = Path(run_dir)
    paths = second_pass_paths(run_dir)
    source_final_path = first_existing(
        run_dir,
        paths["final"],
        "provider_final_official_websites_second_pass.csv",
        "provider_final_official_websites.csv",
    )
    if not source_final_path:
        raise FileNotFoundError(f"official site final CSV not found in {run_dir}")
    final_path = paths["final"]
    unresolved_path = paths["unresolved"]
    source_unresolved_path = first_existing(
        run_dir,
        paths["unresolved"],
        "provider_unresolved_second_pass.csv",
        "provider_unresolved.csv",
    )
    quality_json_path = paths["quality_json"]
    quality_md_path = paths["quality_md"]
    xlsx_path = paths["xlsx"]
    agent_b_csv = agent_b_paths(run_dir)["csv"]
    if not agent_b_csv.exists():
        raise FileNotFoundError(f"AgentB check.csv not found: {agent_b_csv}")

    backup_dir = _backup_outputs(
        [
            source_final_path,
            source_unresolved_path or unresolved_path,
            final_path,
            unresolved_path,
            quality_json_path,
            quality_md_path,
            xlsx_path,
            run_dir / "review_task.csv",
            run_dir / "review_task.xlsx",
        ],
        enabled=backup,
    )

    final_rows = _read_rows(source_final_path)
    agent_rows = {_row_key(row): row for row in _read_rows(agent_b_csv) if _row_key(row)}
    patterns = _load_release_patterns(pattern_jsons, include_non_actionable=False)

    released_rows = []
    out_rows = []
    for row in final_rows:
        agent_row = agent_rows.get(_row_key(row), {})
        pattern = _matching_pattern(row, agent_row, patterns)
        if pattern and _can_release(row, agent_row):
            released = _released_row(row, agent_row, pattern)
            released_rows.append(released)
            out_rows.append(released)
        else:
            out_rows.append(dict(row))

    unresolved_rows = [row for row in out_rows if _is_unresolved(row)]
    write_rows(final_path, out_rows, FINAL_FIELDS)
    write_rows(unresolved_path, unresolved_rows, FINAL_FIELDS)
    xlsx_summary = build_workbook([("Official_Sites", final_path)], xlsx_path) if write_xlsx else {}

    labels = read_csv_rows(labels_csv) if labels_csv else None
    quality = evaluate_quality_gate(
        results_csv=final_path,
        config=load_config(config_path),
        labels=labels,
        expected_rows=len(out_rows),
    )
    write_quality_markdown(quality, quality_md_path)
    quality_json_path.write_text(json.dumps(quality, ensure_ascii=False, indent=2), encoding="utf-8")
    aliases = publish_second_pass_aliases(run_dir, paths)
    review_task = build_manual_review_task(run_dir=run_dir, write_xlsx=True)

    summary = {
        "run_dir": str(run_dir),
        "pattern_jsons": [str(path) for path in pattern_jsons],
        "source_final_csv": str(source_final_path),
        "source_unresolved_csv": str(source_unresolved_path or ""),
        "agent_b_csv": str(agent_b_csv),
        "pattern_count": len(patterns),
        "input_rows": len(final_rows),
        "released_rows": len(released_rows),
        "released_provider_ids": [row.get("provider_id", "") for row in released_rows],
        "released_provider_names": [row.get("provider_name", "") for row in released_rows],
        "released_domains": dict(Counter(row.get("official_domain", "") for row in released_rows)),
        "final_csv": str(final_path),
        "unresolved_csv": str(unresolved_path),
        "quality_json": str(quality_json_path),
        "xlsx": str(xlsx_path) if write_xlsx else "",
        "backup_dir": str(backup_dir) if backup_dir else "",
        "official_url_rows": sum(1 for row in out_rows if row.get("official_url")),
        "unresolved_rows": len(unresolved_rows),
        "status_counts": dict(Counter(row.get("status", "") for row in out_rows)),
        "quality_passed": quality["overall"]["passed"],
        "quality_failures": quality["overall"]["failures"],
        "review_task": review_task,
        "legacy_aliases": aliases,
        "workbook": xlsx_summary,
    }

    summary_path = Path(summary_json) if summary_json else run_dir / "agent_a/pattern_release_applied.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    summary["summary_json"] = str(summary_path)
    _update_manifest(run_dir, summary)
    return summary


def _released_row(row: dict[str, str], agent_row: dict[str, str], pattern: dict) -> dict[str, str]:
    url = _normalize_url(agent_row.get("candidate_url", ""))
    domain = domain_from_url(agent_row.get("candidate_domain") or url)
    return {
        **{field: row.get(field, "") for field in FINAL_FIELDS},
        "official_url": url,
        "official_domain": domain,
        "status": "calibrated_released",
        "decision_source": "calibrated_pattern_release",
        "confidence": agent_row.get("evidence_score") or agent_row.get("confidence") or row.get("confidence", ""),
        "source_status": row.get("status", ""),
        "evidence_summary": _append_note(
            row.get("evidence_summary", ""),
            f"pattern_release:{pattern.get('pattern', '')}",
        ),
        "notes": _append_note(row.get("notes", ""), "calibrated_pattern_release"),
    }


def _is_unresolved(row: dict[str, str]) -> bool:
    return not row.get("official_url") or row.get("status") in UNRESOLVED_STATUSES


def _backup_outputs(paths: list[Path], *, enabled: bool) -> Path | None:
    if not enabled:
        return None
    existing = [path for path in paths if path.exists()]
    if not existing:
        return None
    root = existing[0].parents[0]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_dir = root / "details/pattern_release/backups" / stamp
    backup_dir.mkdir(parents=True, exist_ok=True)
    for path in existing:
        relative_name = path.name
        shutil.copy2(path, backup_dir / relative_name)
    return backup_dir


def _update_manifest(run_dir: Path, summary: dict) -> None:
    manifest_path = run_dir / "manifest.json"
    if not manifest_path.exists():
        return
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return
    manifest["pattern_release_application"] = summary
    manifest.setdefault("summary", {}).update(
        {
            "pattern_release_applied_rows": summary["released_rows"],
            "official_url_rows": summary["official_url_rows"],
            "unresolved_rows": summary["unresolved_rows"],
            "manual_review_rows": summary["review_task"]["review_rows"],
            "quality_passed": summary["quality_passed"],
            "quality_failures": summary["quality_failures"],
        }
    )
    manifest.setdefault("outputs", {}).update(
        {
            "pattern_release_summary": summary["summary_json"],
            "final": summary["final_csv"],
            "unresolved": summary["unresolved_csv"],
            "quality_json": summary["quality_json"],
            "xlsx": summary["xlsx"],
        }
    )
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


if __name__ == "__main__":
    raise SystemExit(main())
