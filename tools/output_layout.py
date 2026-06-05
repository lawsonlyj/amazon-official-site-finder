from __future__ import annotations

import shutil
from pathlib import Path


WORKFLOW_VERSION = "operation-check-v6.4-calibrated-release"
DEFAULT_SECOND_PASS_ACCEPT_THRESHOLD = 75
DEFAULT_MATCHED_REVIEW_CONFIDENCE_CUTOFF = 83
DEFAULT_SECOND_PASS_REVIEW_CONFIDENCE_CUTOFF = 85


def run_root(run_dir: str | Path) -> Path:
    return Path(run_dir)


def pipeline_paths(run_dir: str | Path) -> dict[str, Path]:
    root = run_root(run_dir)
    return {
        "manifest": root / "manifest.json",
        "deduped_input": root / "details/input/deduped_input.csv",
        "deduped_input_xlsx": root / "details/input/deduped_input.xlsx",
        "dedupe_report_json": root / "details/input/dedupe_report.json",
        "dedupe_report_md": root / "details/input/dedupe_report.md",
        "normalized": root / "details/input/providers.csv",
        "results": root / "details/first_pass/search.csv",
        "results_enriched": root / "details/first_pass/enriched.csv",
        "evidence": root / "details/first_pass/evidence.jsonl",
        "review_queue": root / "details/first_pass/review_queue.csv",
        "review_sheet": root / "details/first_pass/review_sheet.csv",
        "final": root / "details/first_pass/final.csv",
        "unresolved": root / "details/first_pass/unresolved.csv",
        "quality_md": root / "details/first_pass/quality.md",
        "quality_json": root / "details/first_pass/quality.json",
    }


def second_pass_paths(run_dir: str | Path) -> dict[str, Path]:
    root = run_root(run_dir)
    return {
        "plan": root / "details/second_pass/plan.csv",
        "results": root / "details/second_pass/results.csv",
        "evidence": root / "details/second_pass/evidence.jsonl",
        "review_decisions": root / "details/second_pass/decisions.csv",
        "final": root / "official_sites.csv",
        "unresolved": root / "unresolved.csv",
        "quality_md": root / "quality.md",
        "quality_json": root / "quality.json",
        "summary": root / "details/second_pass/summary.json",
        "xlsx": root / "official_sites.xlsx",
    }


def review_task_paths(run_dir: str | Path) -> dict[str, Path]:
    root = run_root(run_dir)
    return {
        "csv": root / "review_task.csv",
        "xlsx": root / "review_task.xlsx",
    }


def agent_b_paths(run_dir: str | Path) -> dict[str, Path]:
    root = run_root(run_dir)
    return {
        "csv": root / "check_suggestion/check.csv",
        "jsonl": root / "check_suggestion/check.jsonl",
        "xlsx": root / "check_suggestion/check.xlsx",
        "summary": root / "check_suggestion/summary.json",
    }


def agent_b_suggestion_paths(run_dir: str | Path) -> dict[str, Path]:
    root = run_root(run_dir)
    return {
        "json": root / "check_suggestion/suggestions.json",
        "md": root / "check_suggestion/suggestions.md",
    }


def agent_a_paths(run_dir: str | Path) -> dict[str, Path]:
    root = run_root(run_dir)
    return {
        "applied": root / "operation_optimization/applied.json",
        "identity_cases": root / "operation_optimization/identity_cases.csv",
        "human_cases": root / "operation_optimization/human_cases.csv",
        "no_official_cases": root / "operation_optimization/no_official_cases.csv",
        "reachability_cases": root / "operation_optimization/reachability_cases.csv",
    }


def check_agent_paths(run_dir: str | Path) -> dict[str, Path]:
    root = run_root(run_dir)
    return {
        "csv": root / "development/check_agent/check.csv",
        "jsonl": root / "development/check_agent/check.jsonl",
        "summary": root / "development/check_agent/summary.json",
    }


def optimization_agent_paths(run_dir: str | Path) -> dict[str, Path]:
    root = run_root(run_dir)
    return {
        "json": root / "development/optimization_agent/decision.json",
        "md": root / "development/optimization_agent/decision.md",
    }


def development_cycle_paths(run_dir: str | Path, cycle: int | str) -> dict[str, Path]:
    root = run_root(run_dir)
    cycle_name = str(cycle)
    if not cycle_name.startswith("cycle_"):
        cycle_name = f"cycle_{cycle_name}"
    return {
        "json": root / f"development/{cycle_name}/metrics.json",
        "md": root / f"development/{cycle_name}/metrics.md",
    }


def reviewed_paths(run_dir: str | Path) -> dict[str, Path]:
    root = run_root(run_dir)
    return {
        "combined_review": root / "reviewed/combined_decisions.csv",
        "manual_labels": root / "reviewed/labels.csv",
        "final": root / "reviewed/official_sites.csv",
        "unresolved": root / "reviewed/unresolved.csv",
        "quality_md": root / "reviewed/quality.md",
        "quality_json": root / "reviewed/quality.json",
        "summary": root / "reviewed/learning.json",
        "report_md": root / "reviewed/learning.md",
        "xlsx": root / "reviewed/official_sites.xlsx",
    }


def first_existing(run_dir: str | Path, *paths: str | Path) -> Path | None:
    root = run_root(run_dir)
    for item in paths:
        path = Path(item)
        path = path if path.is_absolute() else root / path
        if path.exists():
            return path
    return None


def ensure_parent(path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def copy_file_alias(source: str | Path, alias: str | Path) -> None:
    source_path = Path(source)
    alias_path = Path(alias)
    if not source_path.exists() or source_path.resolve() == alias_path.resolve():
        return
    alias_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_path, alias_path)


def copy_aliases(pairs: list[tuple[str | Path, str | Path]]) -> None:
    for source, alias in pairs:
        copy_file_alias(source, alias)


def publish_first_pass_outputs(run_dir: str | Path, paths: dict[str, Path]) -> None:
    """Copy the first-pass results to the canonical root filenames."""
    root = run_root(run_dir)
    copy_aliases(
        [
            (paths["final"], root / "official_sites.csv"),
            (paths["unresolved"], root / "unresolved.csv"),
            (paths["quality_md"], root / "quality.md"),
            (paths["quality_json"], root / "quality.json"),
        ]
    )
