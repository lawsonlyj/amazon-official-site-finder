from __future__ import annotations

"""Apply filled visual-verification verdicts and OVERWRITE the canonical second-pass outputs.

The agent fills the visual verification task (manual_decision / manual_url / notes) by looking
at the rendered screenshots. This module merges those verdicts on top of the deterministic
second-pass decisions and rewrites official_sites.csv / unresolved.csv / quality.json /
official_sites.xlsx / review_task.* / manifest.json in place.

Decision semantics (same vocabulary as the manual review loop):
  accept  -> keep the candidate official site
  replace -> use manual_url instead
  reject  -> drop it to unresolved
  unsure  -> leave the deterministic decision untouched
"""

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from finder.finalize import finalize_results
from finder.scoring import load_config
from tools.build_linked_workbook import build_workbook
from tools.build_manual_review_task import build_manual_review_task
from tools.evaluate_labeled_results import read_rows as read_csv_rows
from tools.output_layout import first_existing, second_pass_paths
from tools.quality_gate import evaluate_quality_gate, write_markdown as write_quality_markdown
from tools.run_review_learning import _normalize_manual_review_rows, _read_table

COMBINED_FIELDS = [
    "provider_id",
    "provider_name",
    "manual_decision",
    "manual_url",
    "official_url",
    "candidate_1_url",
    "notes",
    "source_status",
    "evidence_summary",
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Apply visual verification verdicts and overwrite canonical second-pass outputs.")
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--verdicts", required=True, help="Filled visual verification task CSV or XLSX.")
    parser.add_argument("--config", default="config/scoring.json")
    parser.add_argument("--labels")
    parser.add_argument("--write-xlsx", action="store_true", default=True)
    args = parser.parse_args(argv)

    summary = apply_visual_verification(
        run_dir=args.run_dir,
        verdicts_path=args.verdicts,
        config_path=args.config,
        labels_csv=args.labels,
        write_xlsx=args.write_xlsx,
    )
    print(json.dumps(summary["overall"], ensure_ascii=False, indent=2))
    return 0 if summary["quality_overall"].get("passed", True) else 1


def apply_visual_verification(
    *,
    run_dir: str | Path,
    verdicts_path: str | Path,
    config_path: str | Path = "config/scoring.json",
    labels_csv: str | Path | None = None,
    write_xlsx: bool = True,
) -> dict[str, Any]:
    run_dir = Path(run_dir)
    paths = second_pass_paths(run_dir)
    config = load_config(config_path)

    enriched = first_existing(run_dir, "details/first_pass/enriched.csv", "provider_official_websites_enriched.csv")
    if not enriched:
        raise FileNotFoundError(f"first-pass enriched CSV not found in {run_dir}")

    base_decisions = _read_rows(paths["review_decisions"]) if paths["review_decisions"].exists() else []
    raw_verdicts = _read_table(verdicts_path)
    verdict_rows, skipped = _normalize_manual_review_rows(raw_verdicts)
    combined = _combine(base_decisions, verdict_rows)
    combined_path = run_dir / "visual_verification/combined_decisions.csv"
    _write_rows(combined_path, combined, COMBINED_FIELDS)

    final_summary = finalize_results(
        enriched,
        paths["final"],
        review_csv=combined_path,
        unresolved_csv=paths["unresolved"],
    )

    quality = evaluate_quality_gate(
        results_csv=paths["final"],
        config=config,
        labels=read_csv_rows(labels_csv) if labels_csv and Path(labels_csv).exists() else None,
        expected_rows=_expected_rows(run_dir, final_summary),
        min_domain_accuracy=0.8,
        min_auto_precision=0.95,
        min_official_url_rate=0.5,
        max_unresolved_rate=0.6,
    )
    write_quality_markdown(quality, paths["quality_md"])
    paths["quality_json"].write_text(json.dumps(quality, ensure_ascii=False, indent=2), encoding="utf-8")

    if write_xlsx:
        build_workbook(
            [
                ("Final_Second_Pass", paths["final"]),
                ("Second_Pass_Results", paths["results"]),
                ("Visual_Verification_Decisions", combined_path),
            ],
            paths["xlsx"],
        )
    review_task = build_manual_review_task(run_dir=run_dir, write_xlsx=write_xlsx)
    _update_manifest(run_dir / "manifest.json", final_summary, quality, verdict_rows, skipped)

    overall = {
        "applied_verdicts": len(verdict_rows),
        "skipped_unsure_or_blank": len(skipped),
        "base_decisions": len(base_decisions),
        "combined_decisions": len(combined),
        "final_rows": final_summary["final_rows"],
        "official_url_rows": final_summary["official_url_rows"],
        "unresolved_rows": final_summary["unresolved_rows"],
        "quality_passed": quality["overall"]["passed"],
        "manual_review_rows": review_task["review_rows"],
    }
    summary = {
        "overall": overall,
        "decision_counts": _decision_counts(verdict_rows),
        "finalize": final_summary,
        "quality_overall": quality["overall"],
        "outputs": {
            "official_sites_csv": str(paths["final"]),
            "official_sites_xlsx": str(paths["xlsx"]),
            "unresolved_csv": str(paths["unresolved"]),
            "quality_json": str(paths["quality_json"]),
            "combined_decisions": str(combined_path),
        },
    }
    (run_dir / "visual_verification/apply_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return summary


def _combine(base_rows: list[dict[str, str]], verdict_rows: list[dict[str, str]]) -> list[dict[str, str]]:
    combined: dict[str, dict[str, str]] = {}
    for row in base_rows:
        key = _row_key(row)
        if key:
            combined[key] = {field: row.get(field, "") for field in COMBINED_FIELDS}
    for row in verdict_rows:
        key = _row_key(row)
        if key:
            combined[key] = {field: row.get(field, "") for field in COMBINED_FIELDS}
    return list(combined.values())


def _decision_counts(rows: list[dict[str, str]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        decision = row.get("manual_decision", "")
        counts[decision] = counts.get(decision, 0) + 1
    return counts


def _expected_rows(run_dir: Path, final_summary: dict) -> int | None:
    manifest_path = run_dir / "manifest.json"
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            manifest = {}
        total = (manifest.get("parameters") or {}).get("total_to_run")
        try:
            if total:
                return int(total)
        except (TypeError, ValueError):
            pass
    try:
        return int(final_summary.get("final_rows") or 0) or None
    except (TypeError, ValueError):
        return None


def _update_manifest(path: Path, final_summary: dict, quality: dict, verdicts: list, skipped: list) -> None:
    if not path.exists():
        return
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["visual_verification"] = {
        "applied_at": datetime.now(timezone.utc).isoformat(),
        "applied_verdicts": len(verdicts),
        "skipped": len(skipped),
        "finalize": final_summary,
        "quality_overall": quality["overall"],
    }
    manifest.setdefault("summary", {}).update(
        {
            "quality_passed": quality["overall"]["passed"],
            "quality_failures": quality["overall"].get("failures", []),
            "official_url_rows": final_summary["official_url_rows"],
            "unresolved_rows": final_summary["unresolved_rows"],
            "visual_verification_applied_rows": len(verdicts),
        }
    )
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


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


def _row_key(row: dict[str, str]) -> str:
    provider_id = (row.get("provider_id") or "").strip()
    if provider_id:
        return f"id:{provider_id}"
    return f"name:{(row.get('provider_name') or '').strip().casefold()}"


if __name__ == "__main__":
    raise SystemExit(main())
