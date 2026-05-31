from __future__ import annotations

import argparse
import csv
import importlib.machinery
import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from finder.cli import load_dotenv
from finder.doctor import doctor
from finder.input_normalizer import normalize_provider_rows
from finder.query_builder import build_queries
from finder.scoring import load_config
from finder.search_sources import smoke_test_configured_sources


PROJECT_ROOT = Path(__file__).resolve().parents[1]
VENDOR_DIR = PROJECT_ROOT / ".vendor_eval"
OPTIONAL_MODULES = ["ddgs", "trafilatura", "rapidfuzz", "playwright"]

REFERENCE_LINKS = [
    ("Brave Search API", "https://brave.com/search/api/"),
    ("Exa Search API", "https://docs.exa.ai/reference/search"),
    ("SerpAPI Google Search API", "https://serpapi.com/search-api"),
    ("Tavily Search API", "https://docs.tavily.com/api-reference/endpoint/search"),
    ("Firecrawl Search API", "https://docs.firecrawl.dev/api-reference/endpoint/search"),
    ("Serper", "https://serper.dev/"),
    ("Trafilatura", "https://trafilatura.readthedocs.io/"),
    ("RapidFuzz", "https://rapidfuzz.github.io/RapidFuzz/"),
    ("Playwright Python", "https://playwright.dev/python/docs/intro"),
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write a production preflight report for the website finder workflow.")
    parser.add_argument("--source", required=True, help="Raw Amazon GSPN provider CSV.")
    parser.add_argument("--run-dir", default="outputs/production_run", help="Planned production run directory.")
    parser.add_argument("--config", default="config/scoring.json")
    parser.add_argument("--labels", help="Optional labeled expected domains CSV.")
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--per-query", type=int, default=5)
    parser.add_argument("--max-queries", type=int, default=6)
    parser.add_argument("--max-candidates", type=int, default=10)
    parser.add_argument("--min-domain-accuracy", type=float, default=0.9)
    parser.add_argument("--min-auto-precision", type=float, default=0.95)
    parser.add_argument("--min-official-url-rate", type=float, default=0.9)
    parser.add_argument("--max-unresolved-rate", type=float, default=0.1)
    parser.add_argument("--output-md", default="outputs/production_preflight.md")
    parser.add_argument("--output-json", default="outputs/production_preflight.json")
    parser.add_argument("--live-check", action="store_true", help="Call each configured search API once.")
    parser.add_argument("--soft-fail", action="store_true", help="Write the report and return 0 even when not ready.")
    args = parser.parse_args(argv)

    load_dotenv(Path(".env"))
    report = build_preflight_report(
        source_csv=args.source,
        run_dir=args.run_dir,
        config_path=args.config,
        labels_csv=args.labels,
        batch_size=args.batch_size,
        per_query=args.per_query,
        max_queries=args.max_queries,
        max_candidates=args.max_candidates,
        min_domain_accuracy=args.min_domain_accuracy,
        min_auto_precision=args.min_auto_precision,
        min_official_url_rate=args.min_official_url_rate,
        max_unresolved_rate=args.max_unresolved_rate,
        live_check=args.live_check,
    )
    Path(args.output_json).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output_json).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    Path(args.output_md).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output_md).write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 0 if report["summary"]["ready_for_production_run"] or args.soft_fail else 1


def build_preflight_report(
    *,
    source_csv: str | Path,
    run_dir: str | Path,
    config_path: str | Path = "config/scoring.json",
    labels_csv: str | Path | None = None,
    batch_size: int = 100,
    per_query: int = 5,
    max_queries: int = 6,
    max_candidates: int = 10,
    min_domain_accuracy: float = 0.9,
    min_auto_precision: float = 0.95,
    min_official_url_rate: float = 0.9,
    max_unresolved_rate: float = 0.1,
    live_check: bool = False,
) -> dict[str, Any]:
    source_path = Path(source_csv)
    config = load_config(config_path)
    doctor_result = doctor(str(source_path))
    providers = normalize_provider_rows(source_path) if source_path.exists() else []
    provider_count = len(providers)
    label_count = _count_labels(labels_csv) if labels_csv else None
    optional_dependency_status = {name: _module_status(name) for name in OPTIONAL_MODULES}
    optional_dependencies = {name: status["available"] for name, status in optional_dependency_status.items()}
    live_search_checks = smoke_test_configured_sources(per_query=1) if live_check else []
    failures = _readiness_failures(doctor_result, provider_count, live_search_checks=live_search_checks)
    warnings = _readiness_warnings(
        labels_csv=labels_csv,
        label_count=label_count,
        config=config,
        optional_dependencies=optional_dependencies,
        doctor_result=doctor_result,
        live_check=live_check,
        min_official_url_rate=min_official_url_rate,
        max_unresolved_rate=max_unresolved_rate,
    )
    commands = _recommended_commands(
        source_csv=source_path,
        run_dir=Path(run_dir),
        labels_csv=Path(labels_csv) if labels_csv else None,
        config_path=Path(config_path),
        batch_size=batch_size,
        per_query=per_query,
        max_queries=max_queries,
        max_candidates=max_candidates,
        min_domain_accuracy=min_domain_accuracy,
        min_auto_precision=min_auto_precision,
        min_official_url_rate=min_official_url_rate,
        max_unresolved_rate=max_unresolved_rate,
    )
    return {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_csv": str(source_path),
        "run_dir": str(run_dir),
        "config_path": str(config_path),
        "labels_csv": str(labels_csv or ""),
        "summary": {
            "ready_for_production_run": not failures,
            "normalized_provider_count": provider_count,
            "configured_sources": doctor_result.get("configured_sources", []),
            "production_ready": doctor_result.get("production_ready", False),
            "readiness_failures": failures,
            "readiness_warnings": warnings,
        },
        "doctor": doctor_result,
        "thresholds": {
            "min_domain_accuracy": min_domain_accuracy,
            "min_auto_precision": min_auto_precision,
            "min_official_url_rate": min_official_url_rate,
            "max_unresolved_rate": max_unresolved_rate,
        },
        "parameters": {
            "batch_size": batch_size,
            "per_query": per_query,
            "max_queries": max_queries,
            "max_candidates": max_candidates,
            "live_check": live_check,
        },
        "scale_estimate": _scale_estimate(
            providers=providers,
            configured_sources=doctor_result.get("configured_sources", []),
            config=config,
            max_queries=max_queries,
            max_candidates=max_candidates,
        ),
        "optional_dependencies": optional_dependencies,
        "optional_dependency_status": optional_dependency_status,
        "live_search_checks": live_search_checks,
        "dynamic_rendering": config.get("dynamic_rendering", {}),
        "recommended_sources": [
            "Use Brave Search API as the primary production search source.",
            "Use Exa Search API as the semantic second-pass recall source if available.",
            "Use SerpAPI or Serper as Google SERP comparison sources if available.",
            "Use Tavily or Firecrawl as supplemental discovery sources.",
            "Use DDGS only for exploratory no-key smoke tests.",
        ],
        "handoff_outputs": [
            "manifest.json",
            "official_sites.csv",
            "official_sites.xlsx",
            "unresolved.csv",
            "quality.md",
            "quality.json",
            "review_task.csv",
            "review_task.xlsx",
            "details/input/providers.csv",
            "details/first_pass/enriched.csv",
            "details/second_pass/results.csv",
        ],
        "recommended_commands": commands,
        "reference_links": [{"label": label, "url": url} for label, url in REFERENCE_LINKS],
    }


def render_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    status = "READY" if summary["ready_for_production_run"] else "NOT READY"
    lines = [
        "# Production Preflight Report",
        "",
        f"- Status: `{status}`",
        f"- Source CSV: `{report['source_csv']}`",
        f"- Normalized providers: `{summary['normalized_provider_count']}`",
        f"- Configured sources: `{', '.join(summary['configured_sources']) or 'none'}`",
        f"- Production search ready: `{summary['production_ready']}`",
        f"- Planned run directory: `{report['run_dir']}`",
        "",
        "## Readiness",
        "",
    ]
    if summary["readiness_failures"]:
        lines.extend(f"- Failure: {item}" for item in summary["readiness_failures"])
    else:
        lines.append("- No blocking failures detected.")
    if summary["readiness_warnings"]:
        lines.extend(f"- Warning: {item}" for item in summary["readiness_warnings"])
    else:
        lines.append("- No warnings detected.")

    lines.extend(
        [
            "",
            "## Scale Estimate",
            "",
        ]
    )
    for key, value in report.get("scale_estimate", {}).items():
        lines.append(f"- `{key}`: `{value}`")

    lines.extend(
        [
            "",
            "## Live Search Checks",
            "",
        ]
    )
    if report.get("live_search_checks"):
        for check in report["live_search_checks"]:
            status = "ok" if check.get("ok") else "failed"
            detail = check.get("sample_url") or check.get("error") or "no detail"
            lines.append(f"- `{check.get('source')}`: `{status}`; candidates `{check.get('candidate_count')}`; {detail}")
    else:
        lines.append("- Not run. Use `--live-check` after configuring search API keys.")

    lines.extend(
        [
            "",
            "## Optional Dependencies",
            "",
        ]
    )
    for name, available in report["optional_dependencies"].items():
        status = report.get("optional_dependency_status", {}).get(name, {})
        source = status.get("source", "unknown")
        lines.append(f"- `{name}`: `{available}` ({source})")

    lines.extend(
        [
            "",
            "## Recommended Production Command",
            "",
            "```bash",
            report["recommended_commands"]["production_pipeline"],
            "```",
            "",
            "## Quality Gates",
            "",
        ]
    )
    for key, value in report["thresholds"].items():
        lines.append(f"- `{key}`: `{value}`")

    lines.extend(["", "## Handoff Outputs", ""])
    lines.extend(f"- `{item}`" for item in report["handoff_outputs"])
    lines.extend(["", "## Reference Links", ""])
    lines.extend(f"- [{item['label']}]({item['url']})" for item in report["reference_links"])
    lines.append("")
    return "\n".join(lines)


def _module_status(name: str) -> dict[str, str | bool]:
    spec = importlib.util.find_spec(name)
    if spec is not None:
        return {"available": True, "source": "python_path"}
    if VENDOR_DIR.exists() and importlib.machinery.PathFinder.find_spec(name, [str(VENDOR_DIR)]) is not None:
        return {"available": True, "source": ".vendor_eval"}
    return {"available": False, "source": "missing"}


def _count_labels(labels_csv: str | Path | None) -> int | None:
    if not labels_csv:
        return None
    path = Path(labels_csv)
    if not path.exists():
        return 0
    with path.open(newline="", encoding="utf-8-sig") as f:
        return sum(1 for _ in csv.DictReader(f))


def _readiness_failures(doctor_result: dict, provider_count: int, *, live_search_checks: list[dict] | None = None) -> list[str]:
    failures = []
    if not doctor_result.get("input_exists"):
        failures.append("Input CSV does not exist.")
    if provider_count <= 0:
        failures.append("Input CSV did not normalize to any providers.")
    if not doctor_result.get("production_ready"):
        failures.append("No production search API key is configured.")
    failed_production_checks = [
        check.get("source")
        for check in live_search_checks or []
        if check.get("source") in {"serpapi", "brave", "tavily", "serper", "firecrawl"} and not check.get("ok")
    ]
    if failed_production_checks:
        failures.append(f"Live search API check failed for: {', '.join(failed_production_checks)}.")
    return failures


def _readiness_warnings(
    *,
    labels_csv: str | Path | None,
    label_count: int | None,
    config: dict,
    optional_dependencies: dict[str, bool],
    doctor_result: dict,
    live_check: bool,
    min_official_url_rate: float,
    max_unresolved_rate: float,
) -> list[str]:
    warnings = []
    if not labels_csv:
        warnings.append("No labeled sample CSV was provided; accuracy gates will be skipped.")
    elif label_count == 0:
        warnings.append("Labeled sample CSV is empty or missing.")
    if not optional_dependencies.get("trafilatura"):
        warnings.append("trafilatura is not installed; static page extraction will use the lightweight fallback.")
    if not optional_dependencies.get("rapidfuzz"):
        warnings.append("rapidfuzz is not installed; fuzzy matching will use a slower/weaker standard-library fallback.")
    dynamic = config.get("dynamic_rendering", {})
    if dynamic.get("enabled") and not optional_dependencies.get("playwright"):
        warnings.append("Dynamic rendering is enabled but playwright is not installed.")
    if doctor_result.get("production_ready") and not live_check:
        warnings.append("Production key is present but live API smoke test was not run; add --live-check before full scale.")
    if min_official_url_rate < 0.8:
        warnings.append("min_official_url_rate is loose for a production handoff.")
    if max_unresolved_rate > 0.2:
        warnings.append("max_unresolved_rate is loose for a production handoff.")
    return warnings


def _scale_estimate(
    *,
    providers: list[dict],
    configured_sources: list[str],
    config: dict,
    max_candidates: int,
    max_queries: int,
) -> dict[str, int | float]:
    if not providers:
        return {
            "providers": 0,
            "avg_queries_per_provider": 0,
            "configured_search_sources": len(configured_sources),
            "estimated_search_requests": 0,
            "estimated_candidates_to_score": 0,
            "estimated_page_fetches_upper_bound": 0,
        }
    query_counts = [
        min(len(build_queries(provider)), max_queries) if max_queries > 0 else len(build_queries(provider))
        for provider in providers
    ]
    avg_queries = round(sum(query_counts) / len(query_counts), 2)
    search_sources = [source for source in configured_sources if source != "domain_guess"]
    candidates_to_score = len(providers) * max_candidates
    fetch_candidates = len(providers) * min(max_candidates, int(config.get("max_fetch_candidates", max_candidates) or max_candidates))
    site_path_count = 1 + max(0, int(config.get("max_supporting_paths", 8) or 0))
    return {
        "providers": len(providers),
        "avg_queries_per_provider": avg_queries,
        "configured_search_sources": len(search_sources),
        "estimated_search_requests": int(sum(query_counts) * len(search_sources)),
        "estimated_candidates_to_score": candidates_to_score,
        "estimated_candidates_to_fetch": fetch_candidates,
        "estimated_page_fetches_upper_bound": fetch_candidates * site_path_count,
    }


def _recommended_commands(
    *,
    source_csv: Path,
    run_dir: Path,
    labels_csv: Path | None,
    config_path: Path,
    batch_size: int,
    per_query: int,
    max_queries: int,
    max_candidates: int,
    min_domain_accuracy: float,
    min_auto_precision: float,
    min_official_url_rate: float,
    max_unresolved_rate: float,
) -> dict[str, str]:
    labels_arg = f" --labels {labels_csv}" if labels_csv else ""
    return {
        "install_optional_dependencies": "python3 -m pip install --target .vendor_eval -r requirements-optional.txt",
        "production_pipeline": (
            f"PYTHONPATH=.vendor_eval:. python3 tools/run_pipeline.py --source {source_csv} --run-dir {run_dir}{labels_arg} "
            f"--config {config_path} --batch-size {batch_size} --per-query {per_query} "
            f"--max-queries {max_queries} --max-candidates {max_candidates} --min-domain-accuracy {min_domain_accuracy} "
            f"--min-auto-precision {min_auto_precision} --min-official-url-rate {min_official_url_rate} "
            f"--max-unresolved-rate {max_unresolved_rate}"
        ),
        "apply_review_after_manual_fill": (
            f"PYTHONPATH=.vendor_eval:. python3 tools/apply_review.py --run-dir {run_dir} "
            f"--review {run_dir / 'details/first_pass/review_sheet.csv'}"
            + (f" --labels {labels_csv}" if labels_csv else "")
            + (
                f" --min-domain-accuracy {min_domain_accuracy} --min-auto-precision {min_auto_precision} "
                f"--min-official-url-rate {min_official_url_rate} --max-unresolved-rate {max_unresolved_rate}"
            )
        ),
        "build_manual_review_task": (
            f"PYTHONPATH=.vendor_eval:. python3 tools/build_manual_review_task.py --run-dir {run_dir} --write-xlsx"
        ),
        "review_learning_after_manual_fill": (
            f"PYTHONPATH=.vendor_eval:. python3 tools/run_review_learning.py --run-dir {run_dir} "
            f"--review {run_dir / 'review_task.xlsx'} --write-xlsx"
            + (f" --labels {labels_csv}" if labels_csv else "")
        ),
    }


if __name__ == "__main__":
    raise SystemExit(main())
