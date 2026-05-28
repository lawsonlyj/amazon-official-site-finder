from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from finder.audit import audit_results
from finder.cli import load_dotenv, read_done_provider_ids, run_workflow
from finder.doctor import doctor
from finder.finalize import finalize_results
from finder.input_normalizer import normalize_provider_rows, read_normalized_csv, write_normalized_csv
from finder.scoring import load_config
from tools.build_review_sheet import build_review_sheet, write_review_sheet
from tools.enrich_result_links import enrich_result_links
from tools.evaluate_labeled_results import read_rows as read_csv_rows
from tools.quality_gate import evaluate_quality_gate, write_markdown as write_quality_markdown
from tools.run_unresolved_second_pass import run_unresolved_second_pass


class PipelineError(RuntimeError):
    pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the reusable Amazon GSPN official website pipeline.")
    parser.add_argument("--source", required=True, help="Raw Amazon GSPN provider CSV.")
    parser.add_argument("--run-dir", required=True, help="Directory for all outputs from this run.")
    parser.add_argument("--labels", help="Optional labeled expected domains CSV for quality gate.")
    parser.add_argument("--review-decisions", help="Optional manual review CSV with manual_decision/manual_url.")
    parser.add_argument("--config", default="config/scoring.json")
    parser.add_argument("--limit", type=int, default=0, help="Optional total provider limit.")
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--per-query", type=int, default=5)
    parser.add_argument("--max-queries", type=int, default=0, help="Maximum generated search queries to run per provider.")
    parser.add_argument("--max-candidates", type=int, default=30)
    parser.add_argument("--allow-exploratory", action="store_true", help="Allow DDGS/domain-guess runs without production keys.")
    parser.add_argument("--dry-run", action="store_true", help="Write only the run plan/manifest, without searching.")
    parser.add_argument("--resume", action="store_true", help="Skip provider_ids already present in the run directory output CSV.")
    parser.add_argument("--run-second-pass", action="store_true", help="Run second-pass discovery for unresolved rows.")
    parser.add_argument("--second-pass-per-query", type=int, default=3)
    parser.add_argument("--second-pass-max-search-queries", type=int, default=6)
    parser.add_argument("--second-pass-accept-threshold", type=int, default=70)
    parser.add_argument("--second-pass-limit", type=int, default=0)
    parser.add_argument("--second-pass-write-xlsx", action="store_true")
    parser.add_argument("--min-domain-accuracy", type=float, default=0.9)
    parser.add_argument("--min-auto-precision", type=float, default=0.95)
    parser.add_argument("--min-official-url-rate", type=float, default=0.0)
    parser.add_argument("--max-unresolved-rate", type=float, default=1.0)
    args = parser.parse_args(argv)

    load_dotenv(Path(".env"))
    try:
        manifest = run_pipeline(
            source_csv=args.source,
            run_dir=args.run_dir,
            labels_csv=args.labels,
            review_decisions_csv=args.review_decisions,
            config_path=args.config,
            limit=args.limit or None,
            batch_size=args.batch_size,
            per_query=args.per_query,
            max_queries=args.max_queries or None,
            max_candidates=args.max_candidates,
            allow_exploratory=args.allow_exploratory,
            dry_run=args.dry_run,
            resume=args.resume,
            run_second_pass=args.run_second_pass,
            second_pass_per_query=args.second_pass_per_query,
            second_pass_max_search_queries=args.second_pass_max_search_queries,
            second_pass_accept_threshold=args.second_pass_accept_threshold,
            second_pass_limit=args.second_pass_limit or None,
            second_pass_write_xlsx=args.second_pass_write_xlsx,
            min_domain_accuracy=args.min_domain_accuracy,
            min_auto_precision=args.min_auto_precision,
            min_official_url_rate=args.min_official_url_rate,
            max_unresolved_rate=args.max_unresolved_rate,
        )
    except PipelineError as exc:
        print(f"pipeline failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(manifest["summary"], ensure_ascii=False, indent=2))
    if manifest["summary"].get("quality_passed") is False:
        return 1
    return 0


def run_pipeline(
    *,
    source_csv: str | Path,
    run_dir: str | Path,
    labels_csv: str | Path | None = None,
    review_decisions_csv: str | Path | None = None,
    config_path: str | Path = "config/scoring.json",
    limit: int | None = None,
    batch_size: int = 100,
    per_query: int = 5,
    max_queries: int | None = None,
    max_candidates: int = 30,
    allow_exploratory: bool = False,
    dry_run: bool = False,
    resume: bool = False,
    run_second_pass: bool = False,
    second_pass_per_query: int = 3,
    second_pass_max_search_queries: int = 6,
    second_pass_accept_threshold: int = 70,
    second_pass_limit: int | None = None,
    second_pass_write_xlsx: bool = False,
    min_domain_accuracy: float = 0.9,
    min_auto_precision: float = 0.95,
    min_official_url_rate: float = 0.0,
    max_unresolved_rate: float = 1.0,
) -> dict[str, Any]:
    run_dir = Path(run_dir)
    paths = pipeline_paths(run_dir)
    source_csv = Path(source_csv)
    config_path = Path(config_path)
    if batch_size <= 0:
        raise PipelineError("--batch-size must be positive.")
    if not source_csv.exists():
        raise PipelineError(f"source CSV does not exist: {source_csv}")
    if labels_csv and not Path(labels_csv).exists():
        raise PipelineError(f"labels CSV does not exist: {labels_csv}")
    if review_decisions_csv and not Path(review_decisions_csv).exists():
        raise PipelineError(f"review decisions CSV does not exist: {review_decisions_csv}")

    run_dir.mkdir(parents=True, exist_ok=True)
    normalized_count = len(normalize_provider_rows(source_csv))
    total_to_run = min(normalized_count, limit) if limit else normalized_count
    doctor_result = doctor(str(source_csv))
    manifest = build_manifest(
        source_csv=source_csv,
        run_dir=run_dir,
        paths=paths,
        labels_csv=Path(labels_csv) if labels_csv else None,
        review_decisions_csv=Path(review_decisions_csv) if review_decisions_csv else None,
        config_path=config_path,
        normalized_count=normalized_count,
        total_to_run=total_to_run,
        batch_size=batch_size,
        per_query=per_query,
        max_queries=max_queries,
        max_candidates=max_candidates,
        allow_exploratory=allow_exploratory,
        dry_run=dry_run,
        resume=resume,
        run_second_pass=run_second_pass,
        second_pass_per_query=second_pass_per_query,
        second_pass_max_search_queries=second_pass_max_search_queries,
        second_pass_accept_threshold=second_pass_accept_threshold,
        second_pass_limit=second_pass_limit,
        second_pass_write_xlsx=second_pass_write_xlsx,
        doctor_result=doctor_result,
        min_domain_accuracy=min_domain_accuracy,
        min_auto_precision=min_auto_precision,
        min_official_url_rate=min_official_url_rate,
        max_unresolved_rate=max_unresolved_rate,
    )
    write_manifest(paths["manifest"], manifest)
    if dry_run:
        manifest["summary"]["status"] = "dry_run"
        write_manifest(paths["manifest"], manifest)
        return manifest

    if not doctor_result.get("production_ready") and not allow_exploratory:
        manifest["summary"]["status"] = "stopped_no_production_source"
        write_manifest(paths["manifest"], manifest)
        raise PipelineError("no production search source configured; pass --allow-exploratory for DDGS/domain-guess runs.")

    providers = normalize_provider_rows(source_csv)
    write_normalized_csv(providers, paths["normalized"])
    providers = read_normalized_csv(paths["normalized"])
    if limit:
        providers = providers[:limit]
    done_ids = read_done_provider_ids(paths["results"]) if resume and paths["results"].exists() else set()
    providers_to_run = [provider for provider in providers if provider.get("provider_id", "") not in done_ids]
    manifest["summary"]["skipped_existing_rows"] = len(done_ids)
    manifest["summary"]["remaining_rows_to_run"] = len(providers_to_run)
    write_manifest(paths["manifest"], manifest)

    config = load_config(config_path)
    for batch_index, start in enumerate(range(0, len(providers_to_run), batch_size), 1):
        batch = providers_to_run[start : start + batch_size]
        run_workflow(
            batch,
            str(paths["results"]),
            str(paths["evidence"]),
            config,
            per_query=per_query,
            max_queries=max_queries,
            append=batch_index > 1 or bool(done_ids),
            max_candidates=max_candidates,
        )

    enrich_summary = enrich_result_links(paths["normalized"], paths["results"], paths["results_enriched"])
    audit = audit_results(paths["results_enriched"], paths["review_queue"])
    review_sheet_rows = build_review_sheet(
        results_csv=paths["results_enriched"],
        evidence_jsonl=paths["evidence"],
        top_candidates=5,
    )
    write_review_sheet(review_sheet_rows, paths["review_sheet"], top_candidates=5)
    final_summary = finalize_results(
        paths["results_enriched"],
        paths["final"],
        review_csv=review_decisions_csv or paths["review_queue"],
        unresolved_csv=paths["unresolved"],
    )
    labels = read_csv_rows(labels_csv) if labels_csv else None
    quality = evaluate_quality_gate(
        results_csv=paths["final"],
        config=config,
        labels=labels,
        expected_rows=total_to_run,
        min_domain_accuracy=min_domain_accuracy,
        min_auto_precision=min_auto_precision,
        min_official_url_rate=min_official_url_rate,
        max_unresolved_rate=max_unresolved_rate,
    )
    write_quality_markdown(quality, paths["quality_md"])
    paths["quality_json"].write_text(json.dumps(quality, ensure_ascii=False, indent=2), encoding="utf-8")
    manifest["summary"].update(
        {
            "status": "complete",
            "quality_passed": quality["overall"]["passed"],
            "result_rows": audit["total_rows"],
            "final_rows": final_summary["final_rows"],
            "official_url_rows": final_summary["official_url_rows"],
            "unresolved_rows": final_summary["unresolved_rows"],
            "quality_failures": quality["overall"]["failures"],
            "enhanced_review_rows": len(review_sheet_rows),
        }
    )
    manifest["outputs"] = {name: str(path) for name, path in paths.items()}
    manifest["enrich"] = enrich_summary
    manifest["audit"] = audit
    manifest["finalize"] = final_summary
    manifest["quality_overall"] = quality["overall"]
    if run_second_pass:
        second_pass = run_unresolved_second_pass(
            run_dir=run_dir,
            config_path=config_path,
            labels_csv=labels_csv,
            per_query=second_pass_per_query,
            max_search_queries=second_pass_max_search_queries,
            limit=second_pass_limit,
            resume=resume,
            accept_threshold=second_pass_accept_threshold,
            write_xlsx=second_pass_write_xlsx,
            min_domain_accuracy=min_domain_accuracy,
            min_auto_precision=min_auto_precision,
            min_official_url_rate=min_official_url_rate,
            max_unresolved_rate=max_unresolved_rate,
        )
        manifest["second_pass"] = second_pass
        manifest["summary"].update(
            {
                "second_pass_processed_rows": second_pass["processed_rows"],
                "second_pass_accepted_rows": second_pass["accepted_rows"],
                "second_pass_quality_passed": second_pass["quality_overall"]["passed"],
                "second_pass_unresolved_rows": second_pass["finalize"]["unresolved_rows"],
            }
        )
        if not second_pass["quality_overall"]["passed"]:
            manifest["summary"]["quality_passed"] = False
    write_manifest(paths["manifest"], manifest)
    return manifest


def pipeline_paths(run_dir: str | Path) -> dict[str, Path]:
    run_dir = Path(run_dir)
    return {
        "manifest": run_dir / "manifest.json",
        "normalized": run_dir / "providers_normalized.csv",
        "results": run_dir / "provider_official_websites.csv",
        "results_enriched": run_dir / "provider_official_websites_enriched.csv",
        "evidence": run_dir / "provider_official_websites_evidence.jsonl",
        "review_queue": run_dir / "provider_review_queue.csv",
        "review_sheet": run_dir / "provider_review_sheet_enhanced.csv",
        "final": run_dir / "provider_final_official_websites.csv",
        "unresolved": run_dir / "provider_unresolved.csv",
        "quality_md": run_dir / "quality_gate_provider_final.md",
        "quality_json": run_dir / "quality_gate_provider_final.json",
    }


def build_manifest(
    *,
    source_csv: Path,
    run_dir: Path,
    paths: dict[str, Path],
    labels_csv: Path | None,
    review_decisions_csv: Path | None,
    config_path: Path,
    normalized_count: int,
    total_to_run: int,
    batch_size: int,
    per_query: int,
    max_queries: int | None,
    max_candidates: int,
    allow_exploratory: bool,
    dry_run: bool,
    resume: bool,
    run_second_pass: bool,
    second_pass_per_query: int,
    second_pass_max_search_queries: int,
    second_pass_accept_threshold: int,
    second_pass_limit: int | None,
    second_pass_write_xlsx: bool,
    doctor_result: dict,
    min_domain_accuracy: float,
    min_auto_precision: float,
    min_official_url_rate: float,
    max_unresolved_rate: float,
) -> dict[str, Any]:
    batches = math.ceil(total_to_run / batch_size) if total_to_run else 0
    return {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_csv": str(source_csv),
        "run_dir": str(run_dir),
        "config_path": str(config_path),
        "labels_csv": str(labels_csv) if labels_csv else "",
        "review_decisions_csv": str(review_decisions_csv) if review_decisions_csv else "",
        "parameters": {
            "normalized_provider_count": normalized_count,
            "total_to_run": total_to_run,
            "batch_size": batch_size,
            "batches": batches,
            "per_query": per_query,
            "max_queries": max_queries or 0,
            "max_candidates": max_candidates,
            "allow_exploratory": allow_exploratory,
            "dry_run": dry_run,
            "resume": resume,
            "run_second_pass": run_second_pass,
            "second_pass_per_query": second_pass_per_query,
            "second_pass_max_search_queries": second_pass_max_search_queries,
            "second_pass_accept_threshold": second_pass_accept_threshold,
            "second_pass_limit": second_pass_limit or 0,
            "second_pass_write_xlsx": second_pass_write_xlsx,
            "min_domain_accuracy": min_domain_accuracy,
            "min_auto_precision": min_auto_precision,
            "min_official_url_rate": min_official_url_rate,
            "max_unresolved_rate": max_unresolved_rate,
        },
        "doctor": doctor_result,
        "outputs": {name: str(path) for name, path in paths.items()},
        "commands": _equivalent_commands(
            source_csv=source_csv,
            paths=paths,
            labels_csv=labels_csv,
            review_decisions_csv=review_decisions_csv,
            config_path=config_path,
            total_to_run=total_to_run,
            per_query=per_query,
            max_queries=max_queries,
            max_candidates=max_candidates,
            resume=resume,
            run_second_pass=run_second_pass,
            second_pass_per_query=second_pass_per_query,
            second_pass_max_search_queries=second_pass_max_search_queries,
            second_pass_accept_threshold=second_pass_accept_threshold,
            second_pass_limit=second_pass_limit,
            second_pass_write_xlsx=second_pass_write_xlsx,
            min_domain_accuracy=min_domain_accuracy,
            min_auto_precision=min_auto_precision,
            min_official_url_rate=min_official_url_rate,
            max_unresolved_rate=max_unresolved_rate,
        ),
        "summary": {
            "status": "planned",
            "production_ready": doctor_result.get("production_ready"),
            "configured_sources": doctor_result.get("configured_sources", []),
            "quality_passed": None,
        },
    }


def write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def _equivalent_commands(
    *,
    source_csv: Path,
    paths: dict[str, Path],
    labels_csv: Path | None,
    review_decisions_csv: Path | None,
    config_path: Path,
    total_to_run: int,
    per_query: int,
    max_queries: int | None,
    max_candidates: int,
    resume: bool,
    run_second_pass: bool,
    second_pass_per_query: int,
    second_pass_max_search_queries: int,
    second_pass_accept_threshold: int,
    second_pass_limit: int | None,
    second_pass_write_xlsx: bool,
    min_domain_accuracy: float,
    min_auto_precision: float,
    min_official_url_rate: float,
    max_unresolved_rate: float,
) -> list[str]:
    python = "PYTHONPATH=.vendor_eval:. python3"
    max_queries_arg = f" --max-queries {max_queries}" if max_queries else ""
    resume_arg = " --resume" if resume else ""
    commands = [
        f"{python} -m finder.cli prepare --input {source_csv} --output {paths['normalized']}",
        f"{python} -m finder.cli doctor --input {paths['normalized']}",
        (
            f"{python} -m finder.cli run --input {paths['normalized']} --output {paths['results']} "
            f"--evidence {paths['evidence']} --limit {total_to_run} --per-query {per_query} "
            f"--max-candidates {max_candidates}{max_queries_arg}{resume_arg} --config {config_path}"
        ),
        (
            f"{python} tools/enrich_result_links.py --providers {paths['normalized']} --input {paths['results']} "
            f"--output {paths['results_enriched']}"
        ),
        f"{python} -m finder.cli audit-results --input {paths['results_enriched']} --review-output {paths['review_queue']}",
        (
            f"{python} tools/build_review_sheet.py --results {paths['results_enriched']} --evidence {paths['evidence']} "
            f"--output {paths['review_sheet']} --top-candidates 5"
        ),
        (
            f"{python} -m finder.cli finalize-results --input {paths['results_enriched']} "
            f"--review {review_decisions_csv or paths['review_queue']} --output {paths['final']} "
            f"--unresolved-output {paths['unresolved']}"
        ),
    ]
    if labels_csv:
        commands.append(
            (
                f"{python} tools/quality_gate.py --results {paths['final']} --labels {labels_csv} "
                f"--expected-rows {total_to_run} --min-domain-accuracy {min_domain_accuracy} "
                f"--min-auto-precision {min_auto_precision} --min-official-url-rate {min_official_url_rate} "
                f"--max-unresolved-rate {max_unresolved_rate} --output-md {paths['quality_md']} "
                f"--output-json {paths['quality_json']}"
            )
        )
    if run_second_pass:
        second_pass_limit_arg = f" --limit {second_pass_limit}" if second_pass_limit else ""
        second_pass_xlsx_arg = " --write-xlsx" if second_pass_write_xlsx else ""
        second_pass_labels_arg = f" --labels {labels_csv}" if labels_csv else ""
        commands.append(
            (
                f"{python} tools/run_unresolved_second_pass.py --run-dir {paths['manifest'].parent} "
                f"--config {config_path}{second_pass_labels_arg} --per-query {second_pass_per_query} "
                f"--max-search-queries {second_pass_max_search_queries} "
                f"--accept-threshold {second_pass_accept_threshold}{second_pass_limit_arg}{second_pass_xlsx_arg}"
            )
        )
    return commands


if __name__ == "__main__":
    raise SystemExit(main())
