from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from finder.text import domain_from_url


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate AgentC optimization recommendations from AgentB and review learning.")
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--agent-b-csv")
    parser.add_argument("--learning-summary")
    parser.add_argument("--output-json")
    parser.add_argument("--output-md")
    args = parser.parse_args(argv)

    summary = run_agent_c_recommendations(
        run_dir=args.run_dir,
        agent_b_csv=args.agent_b_csv,
        learning_summary=args.learning_summary,
        output_json=args.output_json,
        output_md=args.output_md,
    )
    print(json.dumps(summary["overall"], ensure_ascii=False, indent=2))
    return 0


def run_agent_c_recommendations(
    *,
    run_dir: str | Path,
    agent_b_csv: str | Path | None = None,
    learning_summary: str | Path | None = None,
    output_json: str | Path | None = None,
    output_md: str | Path | None = None,
) -> dict:
    run_dir = Path(run_dir)
    agent_b_path = Path(agent_b_csv) if agent_b_csv else run_dir / "agent_b_verification_results.csv"
    learning_path = Path(learning_summary) if learning_summary else run_dir / "manual_review_learning_summary.json"
    output_json_path = Path(output_json) if output_json else run_dir / "agent_c_optimization_recommendations.json"
    output_md_path = Path(output_md) if output_md else run_dir / "agent_c_optimization_recommendations.md"

    agent_b_rows = _read_rows(agent_b_path)
    learning = _read_json(learning_path)
    recommendations = analyze_recommendations(agent_b_rows, learning)
    summary = {
        "overall": {
            "agent_b_rows": len(agent_b_rows),
            "recommendation_count": len(recommendations),
            "safe_config_action_count": sum(1 for item in recommendations if item.get("safe_to_apply")),
        },
        "recommendations": recommendations,
        "inputs": {"agent_b_csv": str(agent_b_path), "learning_summary": str(learning_path)},
        "outputs": {"json": str(output_json_path), "md": str(output_md_path)},
    }
    output_json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_markdown(output_md_path, summary)
    _update_manifest(run_dir / "manifest.json", summary)
    return summary


def analyze_recommendations(agent_b_rows: list[dict[str, str]], learning: dict) -> list[dict]:
    recommendations = []
    rejected_domains = Counter()
    replace_queries = Counter()
    low_score_accepts = []
    same_name_conflicts = []

    for row in agent_b_rows:
        decision = row.get("agent_b_decision") or row.get("manual_decision")
        candidate_domain = domain_from_url(row.get("candidate_domain") or row.get("candidate_url", ""))
        if decision == "reject" and candidate_domain:
            rejected_domains[candidate_domain] += 1
        if decision == "replace":
            for query in [item.strip() for item in row.get("independent_search_queries", "").split(";") if item.strip()]:
                replace_queries[query] += 1
        if decision == "accept" and _to_int(row.get("evidence_score")) < 70:
            low_score_accepts.append(row)
        counter = row.get("counter_evidence", "").casefold()
        unsure = row.get("reason_for_unsure", "").casefold()
        if "name" in counter or "conflict" in unsure or decision == "unsure":
            same_name_conflicts.append(row)

    for domain, count in rejected_domains.items():
        if count >= 2 or _looks_like_bad_domain(domain):
            recommendations.append(
                {
                    "type": "excluded_domain",
                    "title": "Repeated rejected bad domain",
                    "domain": domain,
                    "count": count,
                    "safe_to_apply": True,
                    "action": "add_to_excluded_domains",
                    "reason": "Multiple AgentB rejects or known directory/platform domain.",
                }
            )

    repeated_replace_queries = [query for query, count in replace_queries.items() if count >= 2]
    if repeated_replace_queries:
        recommendations.append(
            {
                "type": "second_pass_query",
                "title": "Repeated replacement query pattern",
                "queries": repeated_replace_queries[:10],
                "count": sum(replace_queries[query] for query in repeated_replace_queries),
                "safe_to_apply": False,
                "action": "review_second_pass_query_templates",
                "reason": "Several replacements were found by the same independent query shape.",
            }
        )

    if len(low_score_accepts) >= 2:
        recommendations.append(
            {
                "type": "threshold_review",
                "title": "Low-score accepts confirmed by AgentB",
                "count": len(low_score_accepts),
                "safe_to_apply": False,
                "action": "evaluate_strong_evidence_rule_or_threshold",
                "reason": "Confirmed low-score accepts should become labels before any threshold change.",
            }
        )

    if len(same_name_conflicts) >= 3:
        recommendations.append(
            {
                "type": "identity_constraint",
                "title": "Same-name or insufficient identity conflicts",
                "count": len(same_name_conflicts),
                "safe_to_apply": False,
                "safe_artifact": True,
                "action": "write_identity_regression_fixtures",
                "reason": "Repeated uncertainty suggests identity constraints, not automatic URL rules.",
                "examples": [
                    {
                        "provider_id": row.get("provider_id", ""),
                        "provider_name": row.get("provider_name", ""),
                        "candidate_url": row.get("candidate_url", ""),
                        "candidate_domain": row.get("candidate_domain", ""),
                        "agent_b_decision": row.get("agent_b_decision", ""),
                        "evidence_score": row.get("evidence_score", ""),
                        "counter_evidence": row.get("counter_evidence", ""),
                        "reason_for_unsure": row.get("reason_for_unsure", ""),
                    }
                    for row in same_name_conflicts[:50]
                ],
            }
        )

    learning_opt = (learning or {}).get("optimization", {})
    for domain in learning_opt.get("safe_excluded_domain_candidates", []) or []:
        if not any(item.get("domain") == domain for item in recommendations):
            recommendations.append(
                {
                    "type": "excluded_domain",
                    "title": "Manual review safe excluded-domain candidate",
                    "domain": domain,
                    "count": 1,
                    "safe_to_apply": True,
                    "action": "add_to_excluded_domains",
                    "reason": "Manual review learning marked this as a safe repeated/config candidate.",
                }
            )

    if not recommendations and (agent_b_rows or learning):
        recommendations.append(
            {
                "type": "labels_only",
                "title": "No repeated safe rule change",
                "safe_to_apply": False,
                "action": "keep_as_labels_and_evidence",
                "reason": "Patterns are single-case or not safe enough for automatic config changes.",
            }
        )
    return recommendations


def _write_markdown(path: Path, summary: dict) -> None:
    lines = [
        "# AgentC Optimization Recommendations",
        "",
        "## Overall",
        "",
        "| Metric | Value |",
        "|---|---:|",
    ]
    for key, value in summary["overall"].items():
        lines.append(f"| {key} | {value} |")
    lines.extend(["", "## Recommendations", ""])
    for item in summary["recommendations"]:
        safe = "yes" if item.get("safe_to_apply") else "no"
        lines.append(f"- **{item.get('title')}** (`{item.get('type')}`, safe_to_apply={safe}): {item.get('reason')}")
        if item.get("domain"):
            lines.append(f"  - domain: `{item['domain']}`")
        if item.get("queries"):
            lines.append(f"  - queries: {', '.join(f'`{query}`' for query in item['queries'])}")
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def _read_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _looks_like_bad_domain(domain: str) -> bool:
    markers = {
        "linkedin.com",
        "facebook.com",
        "instagram.com",
        "youtube.com",
        "crunchbase.com",
        "trustpilot.com",
        "clutch.co",
        "goodfirms.co",
        "opencorporates.com",
    }
    return domain in markers or any(domain.endswith(f".{marker}") for marker in markers)


def _to_int(value: object) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def _update_manifest(path: Path, summary: dict) -> None:
    if not path.exists():
        return
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["agent_c_recommendations"] = summary
    manifest.setdefault("outputs", {}).update({f"agent_c_{key}": value for key, value in summary["outputs"].items()})
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
