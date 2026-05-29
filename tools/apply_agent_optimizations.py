from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from finder.scoring import load_config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Apply only safe AgentC optimization recommendations.")
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
    recommendations_path = Path(recommendations_json) if recommendations_json else run_dir / "agent_c_optimization_recommendations.json"
    config_path = Path(config_path)
    data = json.loads(recommendations_path.read_text(encoding="utf-8")) if recommendations_path.exists() else {}
    recommendations = data.get("recommendations", [])
    config = load_config(config_path)
    existing = set(config.get("excluded_domains", []))
    additions = []
    skipped = []
    for item in recommendations:
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
    summary = {
        "updated": bool(apply and additions),
        "apply_requested": apply,
        "added_excluded_domains": additions if apply else [],
        "pending_excluded_domains": additions if not apply else [],
        "skipped": skipped,
        "config_path": str(config_path),
        "recommendations_json": str(recommendations_path),
    }
    (run_dir / "agent_a_applied_optimizations_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    _update_manifest(run_dir / "manifest.json", summary)
    return summary


def _update_manifest(path: Path, summary: dict) -> None:
    if not path.exists():
        return
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["agent_a_applied_optimizations"] = summary
    manifest.setdefault("outputs", {})["agent_a_applied_optimizations_summary"] = str(
        path.parent / "agent_a_applied_optimizations_summary.json"
    )
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
