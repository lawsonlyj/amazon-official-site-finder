from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from finder.finalize import finalize_results
from finder.scoring import load_config
from tools.evaluate_labeled_results import read_rows as read_csv_rows
from tools.quality_gate import evaluate_quality_gate, write_markdown as write_quality_markdown
from tools.run_pipeline import PipelineError, pipeline_paths, write_manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Apply a filled manual review CSV to an existing pipeline run directory.")
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--review", help="Filled review CSV. Defaults to manifest review_sheet, then review_queue.")
    parser.add_argument("--labels")
    parser.add_argument("--config")
    parser.add_argument("--min-domain-accuracy", type=float)
    parser.add_argument("--min-auto-precision", type=float)
    parser.add_argument("--min-official-url-rate", type=float)
    parser.add_argument("--max-unresolved-rate", type=float)
    args = parser.parse_args(argv)

    try:
        manifest = apply_review(
            run_dir=args.run_dir,
            review_csv=args.review,
            labels_csv=args.labels,
            config_path=args.config,
            min_domain_accuracy=args.min_domain_accuracy,
            min_auto_precision=args.min_auto_precision,
            min_official_url_rate=args.min_official_url_rate,
            max_unresolved_rate=args.max_unresolved_rate,
        )
    except PipelineError as exc:
        print(f"apply-review failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(manifest["summary"], ensure_ascii=False, indent=2))
    return 0 if manifest["summary"].get("quality_passed") else 1


def apply_review(
    *,
    run_dir: str | Path,
    review_csv: str | Path | None = None,
    labels_csv: str | Path | None = None,
    config_path: str | Path | None = None,
    min_domain_accuracy: float | None = None,
    min_auto_precision: float | None = None,
    min_official_url_rate: float | None = None,
    max_unresolved_rate: float | None = None,
) -> dict[str, Any]:
    run_dir = Path(run_dir)
    paths = pipeline_paths(run_dir)
    manifest_path = paths["manifest"]
    if not manifest_path.exists():
        raise PipelineError(f"manifest not found: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    outputs = {**{name: str(path) for name, path in paths.items()}, **manifest.get("outputs", {})}
    results_path = Path(outputs.get("results", paths["results"]))
    final_path = Path(outputs.get("final", paths["final"]))
    unresolved_path = Path(outputs.get("unresolved", paths["unresolved"]))
    quality_md_path = Path(outputs.get("quality_md", paths["quality_md"]))
    quality_json_path = Path(outputs.get("quality_json", paths["quality_json"]))

    if not results_path.exists():
        raise PipelineError(f"result CSV not found: {results_path}")
    review_path = _resolve_review_path(review_csv, outputs)
    if not review_path.exists():
        raise PipelineError(f"review CSV not found: {review_path}")

    config = load_config(config_path or manifest.get("config_path") or "config/scoring.json")
    labels_path = Path(labels_csv or manifest.get("labels_csv") or "") if (labels_csv or manifest.get("labels_csv")) else None
    labels = read_csv_rows(labels_path) if labels_path and labels_path.exists() else None
    params = manifest.get("parameters", {})
    expected_rows = int(params.get("total_to_run") or 0) or None

    final_summary = finalize_results(
        results_path,
        final_path,
        review_csv=review_path,
        unresolved_csv=unresolved_path,
    )
    quality = evaluate_quality_gate(
        results_csv=final_path,
        config=config,
        labels=labels,
        expected_rows=expected_rows,
        min_domain_accuracy=_pick_float(min_domain_accuracy, params.get("min_domain_accuracy"), 0.9),
        min_auto_precision=_pick_float(min_auto_precision, params.get("min_auto_precision"), 0.95),
        min_official_url_rate=_pick_float(min_official_url_rate, params.get("min_official_url_rate"), 0.0),
        max_unresolved_rate=_pick_float(max_unresolved_rate, params.get("max_unresolved_rate"), 1.0),
    )
    write_quality_markdown(quality, quality_md_path)
    quality_json_path.parent.mkdir(parents=True, exist_ok=True)
    quality_json_path.write_text(json.dumps(quality, ensure_ascii=False, indent=2), encoding="utf-8")

    manifest["summary"].update(
        {
            "status": "review_applied",
            "quality_passed": quality["overall"]["passed"],
            "final_rows": final_summary["final_rows"],
            "official_url_rows": final_summary["official_url_rows"],
            "unresolved_rows": final_summary["unresolved_rows"],
            "quality_failures": quality["overall"]["failures"],
        }
    )
    manifest["post_review"] = {
        "applied_at": datetime.now(timezone.utc).isoformat(),
        "review_csv": str(review_path),
        "finalize": final_summary,
        "quality_overall": quality["overall"],
    }
    manifest["outputs"] = outputs
    write_manifest(manifest_path, manifest)
    return manifest


def _resolve_review_path(review_csv: str | Path | None, outputs: dict[str, str]) -> Path:
    if review_csv:
        return Path(review_csv)
    for key in ["review_sheet", "review_queue"]:
        candidate = outputs.get(key)
        if candidate and Path(candidate).exists():
            return Path(candidate)
    return Path(outputs.get("review_queue", ""))


def _pick_float(explicit: float | None, manifest_value, default: float) -> float:
    if explicit is not None:
        return explicit
    try:
        return float(manifest_value)
    except (TypeError, ValueError):
        return default


if __name__ == "__main__":
    raise SystemExit(main())
