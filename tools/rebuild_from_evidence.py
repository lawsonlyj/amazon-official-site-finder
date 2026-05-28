from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from finder.audit import audit_results
from finder.finalize import finalize_results
from finder.scoring import load_config
from tools.build_linked_workbook import build_workbook
from tools.build_review_sheet import build_review_sheet, write_review_sheet
from tools.enrich_result_links import enrich_result_links
from tools.quality_gate import evaluate_quality_gate, write_markdown as write_quality_markdown
from tools.rescore_evidence import rescore_evidence


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Deterministically rebuild run outputs from saved evidence JSONL.")
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--labels")
    parser.add_argument("--config", default="config/scoring.json")
    parser.add_argument("--expected-rows", type=int, default=0)
    parser.add_argument("--min-domain-accuracy", type=float, default=0.8)
    parser.add_argument("--min-auto-precision", type=float, default=0.95)
    parser.add_argument("--min-official-url-rate", type=float, default=0.5)
    parser.add_argument("--max-unresolved-rate", type=float, default=0.6)
    parser.add_argument("--build-xlsx", action="store_true")
    args = parser.parse_args(argv)

    summary = rebuild_from_evidence(
        run_dir=args.run_dir,
        labels_csv=args.labels,
        config_path=args.config,
        expected_rows=args.expected_rows or None,
        min_domain_accuracy=args.min_domain_accuracy,
        min_auto_precision=args.min_auto_precision,
        min_official_url_rate=args.min_official_url_rate,
        max_unresolved_rate=args.max_unresolved_rate,
        build_xlsx=args.build_xlsx,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["quality_overall"]["passed"] else 1


def rebuild_from_evidence(
    *,
    run_dir: str | Path,
    labels_csv: str | Path | None = None,
    config_path: str | Path = "config/scoring.json",
    expected_rows: int | None = None,
    min_domain_accuracy: float = 0.8,
    min_auto_precision: float = 0.95,
    min_official_url_rate: float = 0.5,
    max_unresolved_rate: float = 0.6,
    build_xlsx: bool = False,
) -> dict:
    run_dir = Path(run_dir)
    paths = {
        "manifest": run_dir / "manifest.json",
        "normalized": run_dir / "providers_normalized.csv",
        "evidence": run_dir / "provider_official_websites_evidence.jsonl",
        "results": run_dir / "provider_official_websites.csv",
        "results_enriched": run_dir / "provider_official_websites_enriched.csv",
        "review_queue": run_dir / "provider_review_queue.csv",
        "review_sheet": run_dir / "provider_review_sheet_enhanced.csv",
        "final": run_dir / "provider_final_official_websites.csv",
        "unresolved": run_dir / "provider_unresolved.csv",
        "quality_md": run_dir / "quality_gate_provider_final.md",
        "quality_json": run_dir / "quality_gate_provider_final.json",
        "snapshot_csv": run_dir / "needs_review_low_confidence_top_candidate_snapshot.csv",
        "final_xlsx": run_dir / "provider_official_websites_final_with_clickable_links.xlsx",
        "snapshot_xlsx": run_dir / "needs_review_low_confidence_top_candidate_snapshot.xlsx",
    }
    _require(paths["normalized"])
    _require(paths["evidence"])

    config = load_config(config_path)
    providers = _read_rows(paths["normalized"])
    expected = expected_rows or len(providers)
    rescore = rescore_evidence(paths["evidence"], providers, paths["results"], config)
    enrich = enrich_result_links(paths["normalized"], paths["results"], paths["results_enriched"])
    audit = audit_results(paths["results_enriched"], paths["review_queue"])
    review_rows = build_review_sheet(results_csv=paths["results_enriched"], evidence_jsonl=paths["evidence"], config=config)
    write_review_sheet(review_rows, paths["review_sheet"], top_candidates=5)
    finalize = finalize_results(paths["results_enriched"], paths["final"], review_csv=paths["review_queue"], unresolved_csv=paths["unresolved"])
    quality = evaluate_quality_gate(
        results_csv=paths["final"],
        config=config,
        labels=_read_rows(labels_csv) if labels_csv else None,
        expected_rows=expected,
        min_domain_accuracy=min_domain_accuracy,
        min_auto_precision=min_auto_precision,
        min_official_url_rate=min_official_url_rate,
        max_unresolved_rate=max_unresolved_rate,
    )
    write_quality_markdown(quality, paths["quality_md"])
    paths["quality_json"].write_text(json.dumps(quality, ensure_ascii=False, indent=2), encoding="utf-8")
    snapshot = write_top_candidate_snapshot(paths["results_enriched"], paths["review_sheet"], paths["snapshot_csv"])

    xlsx = {}
    if build_xlsx:
        xlsx["final"] = build_workbook(
            [
                ("Final", paths["final"]),
                ("Auto_Results", paths["results_enriched"]),
                ("Review_Queue", paths["review_sheet"]),
            ],
            paths["final_xlsx"],
        )
        xlsx["snapshot"] = build_workbook(
            [("Needs_Review_Low_Confidence", paths["snapshot_csv"])],
            paths["snapshot_xlsx"],
        )

    summary = {
        "rebuilt_at": datetime.now(timezone.utc).isoformat(),
        "run_dir": str(run_dir),
        "rescore": rescore,
        "enrich": enrich,
        "audit": audit,
        "review_rows": len(review_rows),
        "finalize": finalize,
        "quality_overall": quality["overall"],
        "snapshot": snapshot,
        "xlsx": xlsx,
        "hashes": _hash_outputs(paths),
    }
    _update_manifest(paths["manifest"], summary, paths)
    return summary


def write_top_candidate_snapshot(results_csv: Path, review_sheet_csv: Path, output_csv: Path) -> dict:
    auto = {row["provider_id"]: row for row in _read_rows(results_csv)}
    review_rows = [row for row in _read_rows(review_sheet_csv) if row.get("status") in {"needs_review", "low_confidence"}]
    fields = [
        "provider_id",
        "provider_name",
        "provider_detail_url",
        "listing_logo_url",
        "status",
        "confidence",
        "official_url",
        "official_domain",
        "original_official_url",
        "original_official_domain",
        "top_candidate_score",
        "top_candidate_source",
        "top_candidate_rank",
        "top_candidate_query",
        "top_candidate_reasons",
        "candidate_2_url",
        "candidate_2_domain",
        "candidate_2_score",
        "candidate_2_source",
        "candidate_2_reasons",
        "candidate_3_url",
        "candidate_3_domain",
        "candidate_3_score",
        "candidate_3_source",
        "candidate_3_reasons",
        "candidate_count",
        "scored_candidate_count",
        "service_apis",
        "provider_locations",
        "evidence_summary",
    ]
    out_rows = []
    for row in review_rows:
        auto_row = auto.get(row.get("provider_id"), {})
        out_rows.append(
            {
                "provider_id": row.get("provider_id", ""),
                "provider_name": row.get("provider_name", ""),
                "provider_detail_url": row.get("provider_detail_url", ""),
                "listing_logo_url": row.get("listing_logo_url", ""),
                "status": row.get("status", ""),
                "confidence": row.get("confidence", ""),
                "official_url": row.get("candidate_1_url", ""),
                "official_domain": row.get("candidate_1_domain", ""),
                "original_official_url": auto_row.get("official_url", ""),
                "original_official_domain": auto_row.get("official_domain", ""),
                "top_candidate_score": row.get("candidate_1_score", ""),
                "top_candidate_source": row.get("candidate_1_source", ""),
                "top_candidate_rank": row.get("candidate_1_rank", ""),
                "top_candidate_query": row.get("candidate_1_query", ""),
                "top_candidate_reasons": row.get("candidate_1_reasons", ""),
                "candidate_2_url": row.get("candidate_2_url", ""),
                "candidate_2_domain": row.get("candidate_2_domain", ""),
                "candidate_2_score": row.get("candidate_2_score", ""),
                "candidate_2_source": row.get("candidate_2_source", ""),
                "candidate_2_reasons": row.get("candidate_2_reasons", ""),
                "candidate_3_url": row.get("candidate_3_url", ""),
                "candidate_3_domain": row.get("candidate_3_domain", ""),
                "candidate_3_score": row.get("candidate_3_score", ""),
                "candidate_3_source": row.get("candidate_3_source", ""),
                "candidate_3_reasons": row.get("candidate_3_reasons", ""),
                "candidate_count": row.get("candidate_count", ""),
                "scored_candidate_count": row.get("scored_candidate_count", ""),
                "service_apis": auto_row.get("service_apis", ""),
                "provider_locations": auto_row.get("provider_locations", ""),
                "evidence_summary": row.get("evidence_summary", ""),
            }
        )
    with output_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(out_rows)
    return {
        "output_csv": str(output_csv),
        "rows": len(out_rows),
        "needs_review": sum(1 for row in out_rows if row["status"] == "needs_review"),
        "low_confidence": sum(1 for row in out_rows if row["status"] == "low_confidence"),
    }


def _update_manifest(path: Path, rebuild_summary: dict, paths: dict[str, Path]) -> None:
    manifest = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    manifest["outputs"] = {
        name: str(file_path)
        for name, file_path in paths.items()
        if name not in {"snapshot_csv", "final_xlsx", "snapshot_xlsx"}
    }
    manifest["outputs"]["snapshot_csv"] = str(paths["snapshot_csv"])
    manifest["outputs"]["final_xlsx"] = str(paths["final_xlsx"])
    manifest["outputs"]["snapshot_xlsx"] = str(paths["snapshot_xlsx"])
    manifest["summary"] = {
        "status": "complete",
        "production_ready": (manifest.get("summary") or {}).get("production_ready"),
        "configured_sources": (manifest.get("summary") or {}).get("configured_sources", []),
        "quality_passed": rebuild_summary["quality_overall"]["passed"],
        "result_rows": rebuild_summary["audit"]["total_rows"],
        "final_rows": rebuild_summary["finalize"]["final_rows"],
        "official_url_rows": rebuild_summary["finalize"]["official_url_rows"],
        "unresolved_rows": rebuild_summary["finalize"]["unresolved_rows"],
        "quality_failures": rebuild_summary["quality_overall"]["failures"],
        "enhanced_review_rows": rebuild_summary["review_rows"],
    }
    manifest["rebuild_from_evidence"] = rebuild_summary
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def _hash_outputs(paths: dict[str, Path]) -> dict[str, str]:
    out = {}
    for name in [
        "normalized",
        "evidence",
        "results",
        "results_enriched",
        "review_queue",
        "review_sheet",
        "final",
        "unresolved",
        "quality_json",
        "snapshot_csv",
    ]:
        path = paths[name]
        if path.exists():
            out[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


def _read_rows(path: str | Path | None) -> list[dict[str, str]]:
    if not path:
        return []
    with Path(path).open(newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _require(path: Path) -> None:
    if not path.exists():
        raise FileNotFoundError(path)


if __name__ == "__main__":
    raise SystemExit(main())
