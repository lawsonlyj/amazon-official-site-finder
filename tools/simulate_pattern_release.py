from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from finder.text import domain_from_url
from tools.mine_evidence_patterns import features_for_review_agent_row


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Simulate narrow Check and Suggestion evidence-pattern release rules.")
    parser.add_argument("--balance-json", required=True, help="Output from tools/evaluate_workflow_balance.py.")
    parser.add_argument("--agent-b-csv", required=True, help="Check and Suggestion check.csv used by the balance run.")
    parser.add_argument(
        "--pattern-json",
        action="append",
        default=[],
        help="Evidence pattern JSON from mine_evidence_patterns.py or pattern_rule_candidates.json. Repeatable.",
    )
    parser.add_argument("--scope", choices=["recall"], default="recall")
    parser.add_argument("--min-support", type=int, default=2)
    parser.add_argument("--max-wrong", type=int, default=0)
    parser.add_argument("--top", type=int, default=25)
    parser.add_argument("--output-json")
    parser.add_argument("--output-md")
    args = parser.parse_args(argv)

    report = simulate_pattern_release(
        balance_json=args.balance_json,
        agent_b_csv=args.agent_b_csv,
        pattern_jsons=args.pattern_json,
        scope=args.scope,
        min_support=args.min_support,
        max_wrong=args.max_wrong,
        top=args.top,
        output_json=args.output_json,
        output_md=args.output_md,
    )
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 0


def simulate_pattern_release(
    *,
    balance_json: str | Path,
    agent_b_csv: str | Path,
    pattern_jsons: list[str | Path] | None = None,
    scope: str = "recall",
    min_support: int = 2,
    max_wrong: int = 0,
    top: int = 25,
    output_json: str | Path | None = None,
    output_md: str | Path | None = None,
) -> dict:
    balance = json.loads(Path(balance_json).read_text(encoding="utf-8"))
    baseline = balance.get("overall") or balance.get("labeled_overall") or {}
    agent_rows = {_row_key(row): row for row in _read_rows(Path(agent_b_csv)) if _row_key(row)}
    recall_rows = _recall_rows(balance.get("details", []), agent_rows)
    patterns = _load_patterns(pattern_jsons or [])
    simulations = [
        _simulate_pattern(pattern, recall_rows, baseline)
        for pattern in patterns
        if pattern.get("scope", scope) == scope and len(pattern.get("features") or []) > 0
    ]
    simulations = [row for row in simulations if row["release_rows"] > 0]
    simulations.sort(
        key=lambda row: (
            row["wrong_release_rows"],
            -row["correct_recovery_rows"],
            -float(row["simulated_overall"].get("overall_accuracy") or 0),
            len(row["features"]),
            row["pattern"],
        )
    )
    safe = [
        row
        for row in simulations
        if row["correct_recovery_rows"] >= min_support and row["wrong_release_rows"] <= max_wrong
    ]
    actionable_safe = [row for row in safe if row["actionable"]]
    selected_actionable = _select_actionable_pattern_set(
        actionable_safe,
        recall_rows=recall_rows,
        baseline=baseline,
        max_wrong=max_wrong,
    )
    selected_summary = selected_actionable["summary"]
    report = {
        "summary": {
            "scope": scope,
            "baseline_labeled_rows": baseline.get("labeled_rows"),
            "baseline_auto_precision": baseline.get("auto_precision"),
            "baseline_official_recall": baseline.get("official_recall"),
            "baseline_overall_accuracy": baseline.get("overall_accuracy"),
            "baseline_false_official_rows": baseline.get("false_official_rows"),
            "baseline_over_rejected_rows": baseline.get("over_rejected_rows"),
            "recall_rows": len(recall_rows),
            "patterns_loaded": len(patterns),
            "patterns_with_release": len(simulations),
            "safe_pattern_count": len(safe),
            "actionable_safe_pattern_count": len(actionable_safe),
            "min_support": min_support,
            "max_wrong": max_wrong,
            "best_safe_pattern": safe[0]["pattern"] if safe else "",
            "best_safe_correct_recovery_rows": safe[0]["correct_recovery_rows"] if safe else 0,
            "best_safe_wrong_release_rows": safe[0]["wrong_release_rows"] if safe else 0,
            "best_safe_accuracy": safe[0]["simulated_overall"]["overall_accuracy"] if safe else None,
            "best_actionable_safe_pattern": actionable_safe[0]["pattern"] if actionable_safe else "",
            "best_actionable_safe_correct_recovery_rows": actionable_safe[0]["correct_recovery_rows"] if actionable_safe else 0,
            "best_actionable_safe_wrong_release_rows": actionable_safe[0]["wrong_release_rows"] if actionable_safe else 0,
            "best_actionable_safe_accuracy": actionable_safe[0]["simulated_overall"]["overall_accuracy"] if actionable_safe else None,
            "selected_actionable_pattern_count": selected_summary["pattern_count"],
            "selected_actionable_release_rows": selected_summary["release_rows"],
            "selected_actionable_correct_recovery_rows": selected_summary["correct_recovery_rows"],
            "selected_actionable_wrong_release_rows": selected_summary["wrong_release_rows"],
            "selected_actionable_accuracy": selected_summary["simulated_overall"].get("overall_accuracy"),
            "selected_actionable_auto_precision": selected_summary["simulated_overall"].get("auto_precision"),
            "selected_actionable_official_recall": selected_summary["simulated_overall"].get("official_recall"),
        },
        "baseline": baseline,
        "safe_patterns": safe[:top],
        "actionable_safe_patterns": actionable_safe[:top],
        "selected_actionable_pattern_set": selected_actionable["patterns"],
        "selected_actionable_release_summary": selected_summary,
        "all_simulations": simulations[: max(top, 100)],
        "inputs": {
            "balance_json": str(balance_json),
            "agent_b_csv": str(agent_b_csv),
            "pattern_jsons": [str(path) for path in pattern_jsons or []],
        },
    }
    if output_json:
        path = Path(output_json)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if output_md:
        path = Path(output_md)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_render_markdown(report), encoding="utf-8")
    return report


def _recall_rows(details: list[dict], agent_rows: dict[str, dict[str, str]]) -> list[dict]:
    out = []
    for detail in details:
        if detail.get("manual_review_reason") != "recall_unresolved_top_candidate":
            continue
        agent = agent_rows.get(_row_key(detail), {})
        if not agent:
            continue
        candidate_domain = domain_from_url(
            agent.get("candidate_domain")
            or agent.get("candidate_url")
            or detail.get("agent_b_candidate_domain")
        )
        if not candidate_domain:
            continue
        features = features_for_review_agent_row(
            {
                "provider_id": detail.get("provider_id", ""),
                "provider_name": detail.get("provider_name", ""),
                "review_reason": detail.get("manual_review_reason", ""),
                "top_candidate_domain": candidate_domain,
            },
            agent,
        )
        out.append(
            {
                **detail,
                "candidate_domain": candidate_domain,
                "features": features,
            }
        )
    return out


def _load_patterns(pattern_jsons: list[str | Path]) -> list[dict]:
    patterns = []
    seen: set[tuple[str, tuple[str, ...]]] = set()
    for path_like in pattern_jsons:
        path = Path(path_like)
        if not path.exists():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        for item in _pattern_items(data):
            features = _features_for_pattern(item)
            if not features:
                continue
            scope = _pattern_scope(item, data)
            key = (scope, tuple(sorted(features)))
            if key in seen:
                continue
            seen.add(key)
            patterns.append(
                {
                    "pattern": item.get("pattern") or " AND ".join(features),
                    "features": sorted(features),
                    "scope": scope,
                    "source_support_rows": _to_int(
                        item.get("support_rows")
                        or item.get("supporting_rows")
                        or item.get("correct_recovery_rows")
                    ),
                    "source_wrong_rows": _to_int(
                        item.get("wrong_release_rows") or item.get("blocking_rows")
                    ),
                }
            )
    return patterns


def _pattern_items(data: dict) -> list[dict]:
    items: list[dict] = []
    for key in (
        "durable_safe_patterns",
        "actionable_safe_patterns",
        "all_patterns",
        "candidate_for_rule",
        "needs_more_labels",
        "reject_pattern",
    ):
        value = data.get(key)
        if isinstance(value, list):
            items.extend(item for item in value if isinstance(item, dict))
    if isinstance(data.get("pattern_rule_candidates"), dict):
        for value in data["pattern_rule_candidates"].values():
            if isinstance(value, list):
                items.extend(item for item in value if isinstance(item, dict))
    return items


def _features_for_pattern(item: dict) -> set[str]:
    features = item.get("features")
    if isinstance(features, list):
        return {str(feature).strip() for feature in features if str(feature).strip()}
    pattern = str(item.get("pattern") or "")
    return {part.strip() for part in pattern.split(" AND ") if part.strip()}


def _pattern_scope(item: dict, data: dict) -> str:
    return str(item.get("pattern_scope") or item.get("scope") or data.get("summary", {}).get("scope") or "recall")


def _simulate_pattern(pattern: dict, rows: list[dict], baseline: dict) -> dict:
    features = set(pattern.get("features") or [])
    released = [
        row
        for row in rows
        if features <= set(row.get("features") or set()) and not _risky_release_domain(row.get("candidate_domain", ""))
    ]
    correct = [
        row
        for row in released
        if row.get("expected_kind") == "official" and row.get("candidate_domain") == row.get("expected_domain")
    ]
    wrong = [row for row in released if row not in correct]
    simulated = _apply_release_to_overall(baseline, correct, wrong)
    return {
        **pattern,
        "release_rows": len(released),
        "correct_recovery_rows": len(correct),
        "wrong_release_rows": len(wrong),
        "release_precision": _ratio(len(correct), len(released)),
        "accuracy_delta": _delta(simulated.get("overall_accuracy"), baseline.get("overall_accuracy")),
        "official_recall_delta": _delta(simulated.get("official_recall"), baseline.get("official_recall")),
        "auto_precision_delta": _delta(simulated.get("auto_precision"), baseline.get("auto_precision")),
        "released_correct_provider_ids": [row.get("provider_id", "") for row in correct],
        "released_wrong_provider_ids": [row.get("provider_id", "") for row in wrong],
        "released_correct_row_keys": [_row_key(row) for row in correct],
        "released_wrong_row_keys": [_row_key(row) for row in wrong],
        "simulated_overall": simulated,
        "actionable": _is_actionable_pattern(features),
        "actionability_reason": _actionability_reason(features),
    }


def _select_actionable_pattern_set(
    patterns: list[dict],
    *,
    recall_rows: list[dict],
    baseline: dict,
    max_wrong: int,
) -> dict:
    row_by_key = {_row_key(row): row for row in recall_rows if _row_key(row)}
    selected: list[dict] = []
    correct_keys: set[str] = set()
    wrong_keys: set[str] = set()
    candidates = sorted(
        patterns,
        key=lambda row: (
            row["wrong_release_rows"],
            -row["correct_recovery_rows"],
            len(row["features"]),
            row["pattern"],
        ),
    )
    for pattern in candidates:
        pattern_correct = {key for key in pattern.get("released_correct_row_keys", []) if key}
        pattern_wrong = {key for key in pattern.get("released_wrong_row_keys", []) if key}
        new_correct = pattern_correct - correct_keys
        candidate_wrong = wrong_keys | pattern_wrong
        if not new_correct:
            continue
        if len(candidate_wrong) > max_wrong:
            continue
        selected.append(pattern)
        correct_keys |= pattern_correct
        wrong_keys = candidate_wrong

    correct_rows = [row for key, row in row_by_key.items() if key in correct_keys]
    wrong_rows = [row for key, row in row_by_key.items() if key in wrong_keys]
    simulated = _apply_release_to_overall(baseline, correct_rows, wrong_rows)
    return {
        "patterns": selected,
        "summary": {
            "pattern_count": len(selected),
            "release_rows": len(correct_rows) + len(wrong_rows),
            "correct_recovery_rows": len(correct_rows),
            "wrong_release_rows": len(wrong_rows),
            "released_correct_provider_ids": [row.get("provider_id", "") for row in correct_rows],
            "released_wrong_provider_ids": [row.get("provider_id", "") for row in wrong_rows],
            "simulated_overall": simulated,
        },
    }


def _apply_release_to_overall(baseline: dict, correct: list[dict], wrong: list[dict]) -> dict:
    out = dict(baseline)
    correct_official = _to_int(out.get("correct_official_rows"))
    correct_no_official = _to_int(out.get("correct_no_official_rows"))
    false_official = _to_int(out.get("false_official_rows"))
    over_rejected = _to_int(out.get("over_rejected_rows"))

    for row in correct:
        if row.get("outcome") == "over_rejected":
            over_rejected -= 1
        correct_official += 1
    for row in wrong:
        outcome = row.get("outcome")
        if outcome == "over_rejected":
            over_rejected -= 1
        elif outcome == "correct_no_official":
            correct_no_official -= 1
        elif outcome == "correct_official":
            correct_official -= 1
        false_official += 1

    total = _to_int(out.get("labeled_rows"))
    expected_official = _to_int(out.get("expected_official_rows"))
    official_outputs = correct_official + false_official
    out.update(
        {
            "correct_official_rows": correct_official,
            "correct_no_official_rows": correct_no_official,
            "false_official_rows": false_official,
            "over_rejected_rows": over_rejected,
            "official_output_rows": official_outputs,
            "auto_precision": _ratio(correct_official, official_outputs),
            "official_recall": _ratio(correct_official, expected_official),
            "overall_accuracy": _ratio(correct_official + correct_no_official, total),
        }
    )
    return out


def _render_markdown(report: dict) -> str:
    summary = report["summary"]
    lines = [
        "# Pattern Release Simulation",
        "",
        "## Summary",
        "",
        f"- Scope: {summary['scope']}",
        f"- Baseline auto precision: {summary['baseline_auto_precision']}",
        f"- Baseline official recall: {summary['baseline_official_recall']}",
        f"- Baseline overall accuracy: {summary['baseline_overall_accuracy']}",
        f"- Baseline false official rows: {summary['baseline_false_official_rows']}",
        f"- Baseline over-rejected rows: {summary['baseline_over_rejected_rows']}",
        f"- Recall rows: {summary['recall_rows']}",
        f"- Patterns loaded: {summary['patterns_loaded']}",
        f"- Safe pattern count: {summary['safe_pattern_count']}",
        f"- Actionable safe pattern count: {summary['actionable_safe_pattern_count']}",
        f"- Best safe pattern: {summary['best_safe_pattern'] or 'None'}",
        f"- Best safe correct/wrong release: {summary['best_safe_correct_recovery_rows']}/{summary['best_safe_wrong_release_rows']}",
        f"- Best safe accuracy: {summary['best_safe_accuracy']}",
        f"- Best actionable safe pattern: {summary['best_actionable_safe_pattern'] or 'None'}",
        f"- Best actionable safe correct/wrong release: {summary['best_actionable_safe_correct_recovery_rows']}/{summary['best_actionable_safe_wrong_release_rows']}",
        f"- Best actionable safe accuracy: {summary['best_actionable_safe_accuracy']}",
        f"- Selected actionable pattern count: {summary['selected_actionable_pattern_count']}",
        f"- Selected actionable correct/wrong release: {summary['selected_actionable_correct_recovery_rows']}/{summary['selected_actionable_wrong_release_rows']}",
        f"- Selected actionable accuracy: {summary['selected_actionable_accuracy']}",
        "",
        "## Safe Patterns",
        "",
    ]
    for row in report.get("safe_patterns", [])[:20]:
        sim = row["simulated_overall"]
        lines.append(
            "- correct={correct}, wrong={wrong}, accuracy={accuracy}, precision={precision}, recall={recall}: {pattern}".format(
                correct=row["correct_recovery_rows"],
                wrong=row["wrong_release_rows"],
                accuracy=sim.get("overall_accuracy"),
                precision=sim.get("auto_precision"),
                recall=sim.get("official_recall"),
                pattern=row["pattern"],
            )
        )
    if not report.get("safe_patterns"):
        lines.append("- None")
    lines.extend(["", "## Actionable Safe Patterns", ""])
    for row in report.get("actionable_safe_patterns", [])[:20]:
        sim = row["simulated_overall"]
        lines.append(
            "- correct={correct}, wrong={wrong}, accuracy={accuracy}, precision={precision}, recall={recall}: {pattern}".format(
                correct=row["correct_recovery_rows"],
                wrong=row["wrong_release_rows"],
                accuracy=sim.get("overall_accuracy"),
                precision=sim.get("auto_precision"),
                recall=sim.get("official_recall"),
                pattern=row["pattern"],
            )
        )
    if not report.get("actionable_safe_patterns"):
        lines.append("- None")
    lines.extend(["", "## Selected Actionable Pattern Set", ""])
    selected_summary = report.get("selected_actionable_release_summary", {})
    lines.append(
        "- correct={correct}, wrong={wrong}, accuracy={accuracy}, precision={precision}, recall={recall}".format(
            correct=selected_summary.get("correct_recovery_rows", 0),
            wrong=selected_summary.get("wrong_release_rows", 0),
            accuracy=(selected_summary.get("simulated_overall") or {}).get("overall_accuracy"),
            precision=(selected_summary.get("simulated_overall") or {}).get("auto_precision"),
            recall=(selected_summary.get("simulated_overall") or {}).get("official_recall"),
        )
    )
    for row in report.get("selected_actionable_pattern_set", [])[:20]:
        lines.append(f"- {row['pattern']}")
    lines.extend(["", "## Top Simulations", ""])
    for row in report.get("all_simulations", [])[:20]:
        sim = row["simulated_overall"]
        lines.append(
            "- correct={correct}, wrong={wrong}, accuracy={accuracy}, precision={precision}, recall={recall}: {pattern}".format(
                correct=row["correct_recovery_rows"],
                wrong=row["wrong_release_rows"],
                accuracy=sim.get("overall_accuracy"),
                precision=sim.get("auto_precision"),
                recall=sim.get("official_recall"),
                pattern=row["pattern"],
            )
        )
    lines.append("")
    return "\n".join(lines)


def _is_actionable_pattern(features: set[str]) -> bool:
    return _has_identity_anchor(features) and _has_corroboration_anchor(features)


def _actionability_reason(features: set[str]) -> str:
    if _is_actionable_pattern(features):
        return "has_identity_and_corroboration_anchors"
    missing = []
    if not _has_identity_anchor(features):
        missing.append("identity_anchor")
    if not _has_corroboration_anchor(features):
        missing.append("corroboration_anchor")
    return "missing_" + "_and_".join(missing)


def _has_identity_anchor(features: set[str]) -> bool:
    identity_features = {
        "domain_relation:exact_provider_slug",
        "domain_relation:domain_contains_provider_slug",
        "domain_relation:provider_slug_contains_domain",
        "domain_relation:two_provider_tokens_in_domain",
        "has:page_contains_exact_provider_name",
        "has:page_contains_provider_name_tokens",
        "has:page_fuzzy_provider_name_match",
    }
    return bool(features & identity_features)


def _has_corroboration_anchor(features: set[str]) -> bool:
    corroboration_features = {
        "has:service_content_matches_amazon_provider",
        "has:some_service_content_matches",
        "has:schema_org_organization_seen",
        "has:standard_company_pages_found",
        "has:contact_email_found",
        "has:location_matches",
        "has:listing_logo_visual_match",
        "has:listing_logo_visual_near_match",
    }
    return bool(features & corroboration_features)


def _risky_release_domain(domain: str) -> bool:
    labels = domain_from_url(domain).split(".")
    if len(labels) < 3:
        return False
    risky_subdomains = {
        "api",
        "app",
        "apps",
        "auth",
        "developer",
        "developers",
        "dev",
        "docs",
        "help",
        "login",
        "portal",
        "signin",
        "support",
    }
    return labels[0].casefold() in risky_subdomains


def _read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _row_key(row: dict) -> str:
    provider_id = str(row.get("provider_id") or "").strip()
    if provider_id:
        return f"id:{provider_id}"
    provider_name = str(row.get("provider_name") or "").strip().casefold()
    return f"name:{provider_name}" if provider_name else ""


def _to_int(value: object) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def _ratio(num: int, den: int) -> float | None:
    return round(num / den, 4) if den else None


def _delta(value: object, baseline: object) -> float | None:
    if value is None or baseline is None:
        return None
    try:
        return round(float(value) - float(baseline), 4)
    except (TypeError, ValueError):
        return None


if __name__ == "__main__":
    raise SystemExit(main())
