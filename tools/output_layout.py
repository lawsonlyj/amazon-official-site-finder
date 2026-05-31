from __future__ import annotations

import os
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


def legacy_aliases_enabled() -> bool:
    value = os.getenv("FINDER_WRITE_LEGACY_ALIASES", "").strip().casefold()
    return value in {"1", "true", "yes", "on"}


def publish_first_pass_aliases(run_dir: str | Path, paths: dict[str, Path]) -> dict[str, str]:
    root = run_root(run_dir)
    canonical = {
        "official_sites": root / "official_sites.csv",
        "unresolved": root / "unresolved.csv",
        "quality_md": root / "quality.md",
        "quality_json": root / "quality.json",
    }
    copy_aliases(
        [
            (paths["final"], canonical["official_sites"]),
            (paths["unresolved"], canonical["unresolved"]),
            (paths["quality_md"], canonical["quality_md"]),
            (paths["quality_json"], canonical["quality_json"]),
        ]
    )
    if not legacy_aliases_enabled():
        return {}
    aliases = {
        "provider_final_official_websites": root / "provider_final_official_websites.csv",
        "provider_unresolved": root / "provider_unresolved.csv",
        "quality_gate_provider_final_md": root / "quality_gate_provider_final.md",
        "quality_gate_provider_final_json": root / "quality_gate_provider_final.json",
    }
    copy_aliases(
        [
            (paths["final"], aliases["provider_final_official_websites"]),
            (paths["unresolved"], aliases["provider_unresolved"]),
            (paths["quality_md"], aliases["quality_gate_provider_final_md"]),
            (paths["quality_json"], aliases["quality_gate_provider_final_json"]),
        ]
    )
    return {key: str(value) for key, value in aliases.items()}


def publish_second_pass_aliases(run_dir: str | Path, paths: dict[str, Path]) -> dict[str, str]:
    if not legacy_aliases_enabled():
        return {}
    root = run_root(run_dir)
    aliases = {
        "unresolved_second_pass_plan": root / "unresolved_second_pass_plan.csv",
        "unresolved_second_pass_results": root / "unresolved_second_pass_results.csv",
        "unresolved_second_pass_evidence": root / "unresolved_second_pass_evidence.jsonl",
        "unresolved_second_pass_review_decisions": root / "unresolved_second_pass_review_decisions.csv",
        "provider_final_official_websites_second_pass": root / "provider_final_official_websites_second_pass.csv",
        "provider_unresolved_second_pass": root / "provider_unresolved_second_pass.csv",
        "quality_gate_provider_second_pass_final_md": root / "quality_gate_provider_second_pass_final.md",
        "quality_gate_provider_second_pass_final_json": root / "quality_gate_provider_second_pass_final.json",
        "unresolved_second_pass_summary": root / "unresolved_second_pass_summary.json",
        "provider_official_websites_second_pass_with_clickable_links": root / "provider_official_websites_second_pass_with_clickable_links.xlsx",
    }
    copy_aliases(
        [
            (paths["plan"], aliases["unresolved_second_pass_plan"]),
            (paths["results"], aliases["unresolved_second_pass_results"]),
            (paths["evidence"], aliases["unresolved_second_pass_evidence"]),
            (paths["review_decisions"], aliases["unresolved_second_pass_review_decisions"]),
            (paths["final"], aliases["provider_final_official_websites_second_pass"]),
            (paths["unresolved"], aliases["provider_unresolved_second_pass"]),
            (paths["quality_md"], aliases["quality_gate_provider_second_pass_final_md"]),
            (paths["quality_json"], aliases["quality_gate_provider_second_pass_final_json"]),
            (paths["summary"], aliases["unresolved_second_pass_summary"]),
            (paths["xlsx"], aliases["provider_official_websites_second_pass_with_clickable_links"]),
        ]
    )
    return {key: str(value) for key, value in aliases.items()}


def publish_review_task_aliases(run_dir: str | Path, paths: dict[str, Path]) -> dict[str, str]:
    if not legacy_aliases_enabled():
        return {}
    root = run_root(run_dir)
    aliases = {
        "manual_official_site_review_task_csv": root / "manual_official_site_review_task.csv",
        "manual_official_site_review_task_xlsx": root / "manual_official_site_review_task.xlsx",
    }
    copy_aliases(
        [
            (paths["csv"], aliases["manual_official_site_review_task_csv"]),
            (paths["xlsx"], aliases["manual_official_site_review_task_xlsx"]),
        ]
    )
    return {key: str(value) for key, value in aliases.items()}


def publish_agent_b_aliases(run_dir: str | Path, paths: dict[str, Path]) -> dict[str, str]:
    if not legacy_aliases_enabled():
        return {}
    root = run_root(run_dir)
    aliases = {
        "agent_b_check_csv": root / "agent_b/check.csv",
        "agent_b_check_jsonl": root / "agent_b/check.jsonl",
        "agent_b_check_xlsx": root / "agent_b/check.xlsx",
        "agent_b_summary": root / "agent_b/summary.json",
        "agent_b_verification_results_csv": root / "agent_b_verification_results.csv",
        "agent_b_verification_results_jsonl": root / "agent_b_verification_results.jsonl",
        "agent_b_verification_results_xlsx": root / "agent_b_verification_results.xlsx",
        "agent_b_verification_summary": root / "agent_b_verification_summary.json",
    }
    copy_aliases(
        [
            (paths["csv"], aliases["agent_b_check_csv"]),
            (paths["jsonl"], aliases["agent_b_check_jsonl"]),
            (paths["xlsx"], aliases["agent_b_check_xlsx"]),
            (paths["summary"], aliases["agent_b_summary"]),
            (paths["csv"], aliases["agent_b_verification_results_csv"]),
            (paths["jsonl"], aliases["agent_b_verification_results_jsonl"]),
            (paths["xlsx"], aliases["agent_b_verification_results_xlsx"]),
            (paths["summary"], aliases["agent_b_verification_summary"]),
        ]
    )
    return {key: str(value) for key, value in aliases.items()}


def publish_agent_b_suggestion_aliases(run_dir: str | Path, paths: dict[str, Path]) -> dict[str, str]:
    if not legacy_aliases_enabled():
        return {}
    root = run_root(run_dir)
    aliases = {
        "agent_b_suggestions_json": root / "agent_b/suggestions.json",
        "agent_b_suggestions_md": root / "agent_b/suggestions.md",
        "agent_c_optimization_recommendations_json": root / "agent_c_optimization_recommendations.json",
        "agent_c_optimization_recommendations_md": root / "agent_c_optimization_recommendations.md",
    }
    copy_aliases(
        [
            (paths["json"], aliases["agent_b_suggestions_json"]),
            (paths["md"], aliases["agent_b_suggestions_md"]),
            (paths["json"], aliases["agent_c_optimization_recommendations_json"]),
            (paths["md"], aliases["agent_c_optimization_recommendations_md"]),
        ]
    )
    return {key: str(value) for key, value in aliases.items()}


def publish_agent_a_aliases(run_dir: str | Path, paths: dict[str, Path]) -> dict[str, str]:
    if not legacy_aliases_enabled():
        return {}
    root = run_root(run_dir)
    aliases = {
        "agent_a_applied": root / "agent_a/applied.json",
        "agent_a_identity_cases": root / "agent_a/identity_cases.csv",
        "agent_a_human_cases": root / "agent_a/human_cases.csv",
        "agent_a_no_official_cases": root / "agent_a/no_official_cases.csv",
        "agent_a_reachability_cases": root / "agent_a/reachability_cases.csv",
        "agent_a_applied_optimizations_summary": root / "agent_a_applied_optimizations_summary.json",
        "agent_identity_constraint_regression_cases": root / "agent_identity_constraint_regression_cases.csv",
        "agent_human_review_regression_cases": root / "agent_human_review_regression_cases.csv",
        "agent_no_official_regression_cases": root / "agent_no_official_regression_cases.csv",
        "agent_url_reachability_regression_cases": root / "agent_url_reachability_regression_cases.csv",
    }
    copy_aliases(
        [
            (paths["applied"], aliases["agent_a_applied"]),
            (paths["identity_cases"], aliases["agent_a_identity_cases"]),
            (paths["human_cases"], aliases["agent_a_human_cases"]),
            (paths["no_official_cases"], aliases["agent_a_no_official_cases"]),
            (paths["reachability_cases"], aliases["agent_a_reachability_cases"]),
            (paths["applied"], aliases["agent_a_applied_optimizations_summary"]),
            (paths["identity_cases"], aliases["agent_identity_constraint_regression_cases"]),
            (paths["human_cases"], aliases["agent_human_review_regression_cases"]),
            (paths["no_official_cases"], aliases["agent_no_official_regression_cases"]),
            (paths["reachability_cases"], aliases["agent_url_reachability_regression_cases"]),
        ]
    )
    return {key: str(value) for key, value in aliases.items()}


def publish_reviewed_aliases(run_dir: str | Path, paths: dict[str, Path]) -> dict[str, str]:
    if not legacy_aliases_enabled():
        return {}
    root = run_root(run_dir)
    aliases = {
        "manual_review_combined_decisions": root / "manual_review_combined_decisions.csv",
        "manual_review_labels": root / "manual_review_labels.csv",
        "provider_final_official_websites_reviewed": root / "provider_final_official_websites_reviewed.csv",
        "provider_unresolved_reviewed": root / "provider_unresolved_reviewed.csv",
        "quality_gate_provider_reviewed_md": root / "quality_gate_provider_reviewed.md",
        "quality_gate_provider_reviewed_json": root / "quality_gate_provider_reviewed.json",
        "manual_review_learning_summary": root / "manual_review_learning_summary.json",
        "manual_review_learning_report": root / "manual_review_learning_report.md",
        "provider_official_websites_reviewed_with_clickable_links": root / "provider_official_websites_reviewed_with_clickable_links.xlsx",
    }
    copy_aliases(
        [
            (paths["combined_review"], aliases["manual_review_combined_decisions"]),
            (paths["manual_labels"], aliases["manual_review_labels"]),
            (paths["final"], aliases["provider_final_official_websites_reviewed"]),
            (paths["unresolved"], aliases["provider_unresolved_reviewed"]),
            (paths["quality_md"], aliases["quality_gate_provider_reviewed_md"]),
            (paths["quality_json"], aliases["quality_gate_provider_reviewed_json"]),
            (paths["summary"], aliases["manual_review_learning_summary"]),
            (paths["report_md"], aliases["manual_review_learning_report"]),
            (paths["xlsx"], aliases["provider_official_websites_reviewed_with_clickable_links"]),
        ]
    )
    return {key: str(value) for key, value in aliases.items()}
