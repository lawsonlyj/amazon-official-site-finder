from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from finder.scoring import load_config
from tools.output_layout import agent_a_paths, first_existing, publish_agent_a_aliases


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Apply only safe operation optimization suggestions.")
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--recommendations-json")
    parser.add_argument("--config", default="config/scoring.json")
    parser.add_argument("--apply", action="store_true", help="Actually write safe config changes.")
    args = parser.parse_args(argv)

    summary = apply_agent_optimizations(
        run_dir=args.run_dir,
        recommendations_json=args.recommendations_json,
        config_path=args.config,
        apply=args.apply,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def apply_agent_optimizations(
    *,
    run_dir: str | Path,
    recommendations_json: str | Path | None = None,
    config_path: str | Path = "config/scoring.json",
    apply: bool = False,
) -> dict:
    run_dir = Path(run_dir)
    recommendations_path = Path(recommendations_json) if recommendations_json else (
        first_existing(
            run_dir,
            "check_suggestion/suggestions.json",
            "agent_b/suggestions.json",
            "agent_c_optimization_recommendations.json",
        )
        or run_dir / "check_suggestion/suggestions.json"
    )
    config_path = Path(config_path)
    data = json.loads(recommendations_path.read_text(encoding="utf-8")) if recommendations_path.exists() else {}
    recommendations = data.get("recommendations", [])
    config = load_config(config_path)
    existing = set(config.get("excluded_domains", []))
    additions = []
    skipped = []
    identity_examples = []
    human_review_examples = []
    no_official_examples = []
    url_reachability_examples = []
    for item in recommendations:
        if item.get("action") == "write_identity_regression_fixtures" and item.get("safe_artifact"):
            identity_examples.extend(item.get("examples") or [])
            continue
        if item.get("action") == "write_human_review_regression_fixtures" and item.get("safe_artifact"):
            human_review_examples.extend(item.get("examples") or [])
            continue
        if item.get("action") == "write_no_official_regression_fixtures" and item.get("safe_artifact"):
            no_official_examples.extend(item.get("examples") or [])
            continue
        if item.get("action") == "verify_url_variants_before_accept" and item.get("safe_artifact"):
            url_reachability_examples.extend(item.get("examples") or [])
            continue
        if item.get("action") != "add_to_excluded_domains" or not item.get("safe_to_apply"):
            skipped.append({"type": item.get("type", ""), "reason": "not_safe_config_action"})
            continue
        domain = str(item.get("domain") or "").strip().lower()
        if not domain:
            continue
        if domain in existing:
            skipped.append({"domain": domain, "reason": "already_present"})
            continue
        additions.append(domain)
        existing.add(domain)
    if apply and additions:
        config["excluded_domains"] = list(config.get("excluded_domains", [])) + additions
        config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    canonical = agent_a_paths(run_dir)
    fixture_path = canonical["identity_cases"]
    fixtures_written = 0
    if apply and identity_examples:
        fixtures_written = _write_identity_fixtures(fixture_path, identity_examples)
    human_fixture_path = canonical["human_cases"]
    human_fixtures_written = 0
    if apply and human_review_examples:
        human_fixtures_written = _write_human_review_fixtures(human_fixture_path, human_review_examples)
    no_official_fixture_path = canonical["no_official_cases"]
    no_official_fixtures_written = 0
    if apply and no_official_examples:
        no_official_fixtures_written = _write_human_review_fixtures(no_official_fixture_path, no_official_examples)
    reachability_fixture_path = canonical["reachability_cases"]
    reachability_fixtures_written = 0
    if apply and url_reachability_examples:
        reachability_fixtures_written = _write_human_review_fixtures(reachability_fixture_path, url_reachability_examples)
    summary = {
        "updated": bool(apply and additions),
        "artifacts_updated": bool(
            apply
            and (fixtures_written or human_fixtures_written or no_official_fixtures_written or reachability_fixtures_written)
        ),
        "apply_requested": apply,
        "added_excluded_domains": additions if apply else [],
        "pending_excluded_domains": additions if not apply else [],
        "identity_regression_fixture_rows": fixtures_written if apply else 0,
        "identity_regression_fixture": str(fixture_path) if apply and fixtures_written else "",
        "human_review_regression_fixture_rows": human_fixtures_written if apply else 0,
        "human_review_regression_fixture": str(human_fixture_path) if apply and human_fixtures_written else "",
        "no_official_regression_fixture_rows": no_official_fixtures_written if apply else 0,
        "no_official_regression_fixture": str(no_official_fixture_path) if apply and no_official_fixtures_written else "",
        "url_reachability_regression_fixture_rows": reachability_fixtures_written if apply else 0,
        "url_reachability_regression_fixture": str(reachability_fixture_path) if apply and reachability_fixtures_written else "",
        "skipped": skipped,
        "config_path": str(config_path),
        "recommendations_json": str(recommendations_path),
        "outputs": {
            "applied": str(canonical["applied"]),
            "identity_cases": str(fixture_path),
            "human_cases": str(human_fixture_path),
            "no_official_cases": str(no_official_fixture_path),
            "reachability_cases": str(reachability_fixture_path),
        },
    }
    canonical["applied"].parent.mkdir(parents=True, exist_ok=True)
    canonical["applied"].write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    aliases = publish_agent_a_aliases(run_dir, canonical)
    summary["legacy_aliases"] = aliases
    canonical["applied"].write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    _update_manifest(run_dir / "manifest.json", summary)
    return summary


def _write_identity_fixtures(path: Path, examples: list[dict]) -> int:
    fields = [
        "provider_id",
        "provider_name",
        "candidate_url",
        "candidate_domain",
        "agent_b_decision",
        "evidence_score",
        "counter_evidence",
        "reason_for_unsure",
        "expected_outcome",
    ]
    rows = []
    seen = set()
    for example in examples:
        key = (example.get("provider_id", ""), example.get("candidate_url", ""))
        if key in seen:
            continue
        seen.add(key)
        row = {field: str(example.get(field, "")) for field in fields}
        row["expected_outcome"] = "needs_identity_review"
        rows.append(row)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def _write_human_review_fixtures(path: Path, examples: list[dict]) -> int:
    fields = [
        "provider_id",
        "provider_name",
        "provider_detail_url",
        "candidate_url",
        "manual_decision",
        "manual_url",
        "confidence",
        "error_type",
        "notes",
        "note_tags",
        "expected_outcome",
    ]
    rows = []
    seen = set()
    for example in examples:
        key = (example.get("provider_id", ""), example.get("candidate_url", ""), example.get("manual_url", ""))
        if key in seen:
            continue
        seen.add(key)
        row = {field: _fixture_value(example.get(field, "")) for field in fields}
        rows.append(row)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def _fixture_value(value: object) -> str:
    if isinstance(value, list):
        return "; ".join(str(item) for item in value if str(item))
    return str(value or "")


def _update_manifest(path: Path, summary: dict) -> None:
    if not path.exists():
        return
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["operation_optimization"] = summary
    manifest.setdefault("outputs", {})["operation_optimization_applied"] = summary.get("outputs", {}).get(
        "applied", str(path.parent / "operation_optimization/applied.json")
    )
    manifest.setdefault("legacy_aliases", {})["operation_optimization"] = summary.get("legacy_aliases", {})
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
