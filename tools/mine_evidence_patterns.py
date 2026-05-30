from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter
from itertools import combinations
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from finder.text import base_domain_label, domain_from_url, slug, tokens


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Mine labeled evidence patterns for safer threshold/rule tuning.")
    parser.add_argument("--balance-json", required=True, help="Output from tools/evaluate_workflow_balance.py.")
    parser.add_argument("--agent-b-csv", required=True, help="AgentB check.csv with supporting_facts/counter_evidence.")
    parser.add_argument("--scope", choices=["recall", "precision"], default="recall")
    parser.add_argument("--max-pattern-size", type=int, default=3)
    parser.add_argument("--min-support", type=int, default=2)
    parser.add_argument("--output-json")
    parser.add_argument("--output-md")
    args = parser.parse_args(argv)

    report = mine_evidence_patterns(
        balance_json=args.balance_json,
        agent_b_csv=args.agent_b_csv,
        scope=args.scope,
        max_pattern_size=args.max_pattern_size,
        min_support=args.min_support,
        output_json=args.output_json,
        output_md=args.output_md,
    )
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 0


def mine_evidence_patterns(
    *,
    balance_json: str | Path,
    agent_b_csv: str | Path,
    scope: str = "recall",
    max_pattern_size: int = 3,
    min_support: int = 2,
    output_json: str | Path | None = None,
    output_md: str | Path | None = None,
) -> dict:
    balance = json.loads(Path(balance_json).read_text(encoding="utf-8"))
    details = balance.get("details", [])
    agent_rows = {_row_key(row): row for row in _read_rows(Path(agent_b_csv)) if _row_key(row)}
    rows = [_pattern_row(row, agent_rows.get(_row_key(row), {}), scope) for row in details]
    rows = [row for row in rows if row]
    patterns = _mine_patterns(rows, max_pattern_size=max_pattern_size)
    durable_safe = [
        row
        for row in patterns
        if row["wrong_release_rows"] == 0 and row["correct_recovery_rows"] >= min_support
    ]
    durable_safe.sort(key=lambda row: (-row["correct_recovery_rows"], len(row["features"]), row["pattern"]))
    durable_safe = _minimal_patterns(durable_safe)
    risky = [row for row in patterns if row["wrong_release_rows"] > 0]
    risky.sort(key=lambda row: (-row["wrong_release_rows"], -row["correct_recovery_rows"], row["pattern"]))
    report = {
        "summary": {
            "scope": scope,
            "rows": len(rows),
            "correct_rows": sum(1 for row in rows if row["target"] == "correct"),
            "wrong_rows": sum(1 for row in rows if row["target"] == "wrong"),
            "patterns": len(patterns),
            "durable_safe_patterns": len(durable_safe),
            "min_support": min_support,
            "max_pattern_size": max_pattern_size,
        },
        "recommendations": _recommendations(scope, durable_safe, risky, min_support),
        "durable_safe_patterns": durable_safe[:25],
        "risky_patterns": risky[:25],
        "all_patterns": patterns[:250],
        "rows": rows,
        "inputs": {
            "balance_json": str(balance_json),
            "agent_b_csv": str(agent_b_csv),
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


def _pattern_row(detail: dict, agent_row: dict[str, str], scope: str) -> dict | None:
    if not agent_row:
        return None
    review_reason = detail.get("manual_review_reason", "") or agent_row.get("review_reason", "")
    if scope == "recall":
        if review_reason != "recall_unresolved_top_candidate":
            return None
        candidate_domain = domain_from_url(
            agent_row.get("candidate_domain", "")
            or agent_row.get("candidate_url", "")
            or detail.get("agent_b_candidate_domain", "")
        )
        if not candidate_domain:
            return None
        target = (
            "correct"
            if detail.get("expected_kind") == "official" and candidate_domain == detail.get("expected_domain")
            else "wrong"
        )
    else:
        if not review_reason.startswith("precision_"):
            return None
        candidate_domain = domain_from_url(
            agent_row.get("candidate_domain", "")
            or agent_row.get("candidate_url", "")
            or detail.get("output_domain", "")
            or detail.get("output_url", "")
        )
        if not candidate_domain:
            return None
        target = "correct" if detail.get("outcome") == "correct_official" else "wrong"
    features = _features(detail, agent_row, candidate_domain)
    return {
        "provider_id": detail.get("provider_id", ""),
        "provider_name": detail.get("provider_name", ""),
        "scope": scope,
        "target": target,
        "expected_domain": detail.get("expected_domain", ""),
        "candidate_domain": candidate_domain,
        "review_reason": review_reason,
        "agent_b_decision": agent_row.get("agent_b_decision", ""),
        "agent_b_score": _numeric(agent_row.get("evidence_score") or detail.get("agent_b_candidate_score")),
        "features": sorted(features),
    }


def features_for_review_agent_row(review_row: dict[str, str], agent_row: dict[str, str]) -> set[str]:
    candidate_domain = domain_from_url(
        agent_row.get("candidate_domain", "")
        or agent_row.get("candidate_url", "")
        or review_row.get("top_candidate_domain", "")
        or review_row.get("top_candidate_url", "")
        or review_row.get("official_domain", "")
        or review_row.get("official_url", "")
    )
    detail = {
        "provider_name": review_row.get("provider_name", "") or agent_row.get("provider_name", ""),
        "manual_review_reason": review_row.get("review_reason", "") or agent_row.get("review_reason", ""),
        "agent_b_reason_for_unsure": agent_row.get("reason_for_unsure", ""),
        "agent_b_candidate_score": agent_row.get("evidence_score", "") or agent_row.get("confidence", ""),
    }
    return _features(detail, agent_row, candidate_domain)


def _features(detail: dict, agent_row: dict[str, str], candidate_domain: str) -> set[str]:
    features = {
        f"review_reason:{detail.get('manual_review_reason', '') or agent_row.get('review_reason', '')}",
        f"agent_b_decision:{agent_row.get('agent_b_decision', '')}",
    }
    reason = agent_row.get("reason_for_unsure") or detail.get("agent_b_reason_for_unsure")
    if reason:
        features.add(f"reason_for_unsure:{reason}")
    score = _numeric(agent_row.get("evidence_score") or detail.get("agent_b_candidate_score"))
    for threshold in [30, 45, 50, 60, 70, 75, 80, 85]:
        features.add(f"agent_b_score>={threshold}" if score >= threshold else f"agent_b_score<{threshold}")
    supporting_facts = set(_split_facts(agent_row.get("supporting_facts", "")))
    for fact in supporting_facts:
        features.add(f"has:{fact}")
        if fact.startswith("location_matches:"):
            features.add("has:location_matches")
    for fact in _identity_fact_markers():
        if fact not in supporting_facts and not any(item.startswith(f"{fact}:") for item in supporting_facts):
            features.add(f"missing:{fact}")
    for fact in _split_facts(agent_row.get("counter_evidence", "")):
        features.add(f"counter:{fact}")
    provider_name = detail.get("provider_name", "") or agent_row.get("provider_name", "")
    provider_tokens = tokens(provider_name)
    generic_tokens = _generic_identity_tokens()
    meaningful_tokens = [token for token in provider_tokens if token not in generic_tokens]
    name_text = provider_name.casefold()
    if provider_tokens:
        features.add(f"provider_token_count:{min(len(provider_tokens), 5)}")
    if len(provider_tokens) == 1:
        features.add("provider_name_shape:single_token")
    if len(meaningful_tokens) <= 1:
        features.add("provider_name_shape:generic_or_one_meaningful_token")
    if len("".join(provider_tokens)) <= 4:
        features.add("provider_name_shape:short")
    for marker in ["consult", "seller", "marketplace", "agency", "amazon", "ecom"]:
        if marker in name_text:
            features.add(f"provider_name_contains:{marker}")
    name_slug = slug(provider_name)
    domain_slug = slug(base_domain_label(candidate_domain))
    if name_slug and domain_slug:
        if name_slug == domain_slug:
            features.add("domain_relation:exact_provider_slug")
        elif name_slug in domain_slug:
            features.add("domain_relation:domain_contains_provider_slug")
        elif domain_slug in name_slug:
            features.add("domain_relation:provider_slug_contains_domain")
        matching_tokens = [token for token in tokens(provider_name) if token in domain_slug]
        if matching_tokens:
            features.add("domain_relation:provider_token_in_domain")
        if len(matching_tokens) >= 2:
            features.add("domain_relation:two_provider_tokens_in_domain")
    return {feature for feature in features if feature and not feature.endswith(":")}


def _identity_fact_markers() -> set[str]:
    return {
        "candidate_pages_fetch_ok",
        "contact_email_found",
        "legal_entity_marker_found",
        "listing_logo_visual_match",
        "listing_logo_visual_near_match",
        "location_matches",
        "page_contains_exact_provider_name",
        "page_contains_provider_name_tokens",
        "schema_org_organization_seen",
        "service_content_matches_amazon_provider",
        "standard_company_pages_found",
    }


def _generic_identity_tokens() -> set[str]:
    return {
        "amazon",
        "account",
        "agency",
        "consulting",
        "consultancy",
        "digital",
        "ecom",
        "ecommerce",
        "global",
        "growth",
        "llc",
        "ltd",
        "management",
        "marketplace",
        "media",
        "seller",
        "sellers",
        "service",
        "services",
        "solution",
        "solutions",
    }


def _mine_patterns(rows: list[dict], *, max_pattern_size: int) -> list[dict]:
    buckets: dict[tuple[str, ...], list[dict]] = {}
    for row in rows:
        features = row["features"]
        for size in range(1, max_pattern_size + 1):
            for pattern in combinations(features, size):
                buckets.setdefault(pattern, []).append(row)
    patterns = []
    for pattern, matched in buckets.items():
        targets = Counter(row["target"] for row in matched)
        correct = [row for row in matched if row["target"] == "correct"]
        wrong = [row for row in matched if row["target"] == "wrong"]
        patterns.append(
            {
                "pattern": " AND ".join(pattern),
                "features": list(pattern),
                "support_rows": len(matched),
                "correct_recovery_rows": len(correct),
                "wrong_release_rows": len(wrong),
                "precision": _ratio(targets.get("correct", 0), len(matched)),
                "correct_provider_ids": [row["provider_id"] for row in correct],
                "wrong_provider_ids": [row["provider_id"] for row in wrong],
            }
        )
    patterns.sort(
        key=lambda row: (
            row["wrong_release_rows"],
            -row["correct_recovery_rows"],
            len(row["features"]),
            row["pattern"],
        )
    )
    return patterns


def _minimal_patterns(patterns: list[dict]) -> list[dict]:
    selected: list[dict] = []
    selected_sets: list[set[str]] = []
    for row in patterns:
        features = set(row["features"])
        if any(existing <= features for existing in selected_sets):
            continue
        selected.append(row)
        selected_sets.append(features)
    return selected


def _recommendations(scope: str, safe: list[dict], risky: list[dict], min_support: int) -> list[str]:
    out = []
    if safe:
        best = safe[0]
        out.append(
            f"Found {len(safe)} zero-error {scope} evidence pattern(s) with support >= {min_support}; treat them as candidates for tests, not production rules, until validated on more labels."
        )
        out.append(f"Best candidate pattern: {best['pattern']} recovers {best['correct_recovery_rows']} labeled row(s).")
    else:
        out.append(
            f"No zero-error {scope} evidence pattern reached support >= {min_support}; keep the current manual/AgentB lane instead of relaxing rules."
        )
    if risky:
        worst = risky[0]
        out.append(
            f"Most dangerous pattern releases {worst['wrong_release_rows']} wrong row(s): {worst['pattern']}."
        )
    return out


def _render_markdown(report: dict) -> str:
    summary = report["summary"]
    lines = [
        "# Evidence Pattern Mining",
        "",
        "## Summary",
        "",
        f"- Scope: {summary['scope']}",
        f"- Rows: {summary['rows']}",
        f"- Correct rows: {summary['correct_rows']}",
        f"- Wrong rows: {summary['wrong_rows']}",
        f"- Durable safe patterns: {summary['durable_safe_patterns']}",
        f"- Min support: {summary['min_support']}",
        "",
        "## Recommendations",
        "",
    ]
    for item in report["recommendations"]:
        lines.append(f"- {item}")
    lines.extend(["", "## Durable Safe Patterns", ""])
    if report["durable_safe_patterns"]:
        for row in report["durable_safe_patterns"][:10]:
            lines.append(
                f"- correct={row['correct_recovery_rows']}, wrong={row['wrong_release_rows']}, precision={row['precision']}: {row['pattern']}"
            )
    else:
        lines.append("- None")
    lines.extend(["", "## Risky Patterns", ""])
    for row in report["risky_patterns"][:10]:
        lines.append(
            f"- correct={row['correct_recovery_rows']}, wrong={row['wrong_release_rows']}, precision={row['precision']}: {row['pattern']}"
        )
    lines.append("")
    return "\n".join(lines)


def _split_facts(value: str) -> list[str]:
    out = []
    for item in str(value or "").split(";"):
        fact = re.sub(r"\s+", "_", item.strip())
        if fact:
            out.append(fact)
    return out


def _row_key(row: dict[str, str]) -> str:
    provider_id = str(row.get("provider_id") or "").strip()
    if provider_id:
        return f"id:{provider_id}"
    provider_name = str(row.get("provider_name") or "").strip().casefold()
    return f"name:{provider_name}" if provider_name else ""


def _read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _numeric(value: object) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _ratio(num: int, den: int) -> float | None:
    return round(num / den, 4) if den else None


if __name__ == "__main__":
    raise SystemExit(main())
