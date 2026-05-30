from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.build_linked_workbook import build_workbook


PRIORITY_PREFIX_FIELDS = [
    "priority_rank",
    "priority_reason",
    "decision_impact",
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a small high-value task from protected-lane rows.")
    parser.add_argument("--source-csv", required=True, help="protected_lanes_next_review_task.csv")
    parser.add_argument("--output-csv", required=True)
    parser.add_argument("--output-xlsx")
    parser.add_argument("--output-json")
    parser.add_argument("--output-md")
    parser.add_argument("--max-rows", type=int, default=16)
    parser.add_argument("--max-per-reason", type=int, default=4)
    args = parser.parse_args(argv)

    summary = build_protected_lane_priority_task(
        source_csv=args.source_csv,
        output_csv=args.output_csv,
        output_xlsx=args.output_xlsx,
        output_json=args.output_json,
        output_md=args.output_md,
        max_rows=args.max_rows,
        max_per_reason=args.max_per_reason,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def build_protected_lane_priority_task(
    *,
    source_csv: str | Path,
    output_csv: str | Path,
    output_xlsx: str | Path | None = None,
    output_json: str | Path | None = None,
    output_md: str | Path | None = None,
    max_rows: int = 16,
    max_per_reason: int = 4,
) -> dict:
    source_path = Path(source_csv)
    rows = _read_rows(source_path)
    headers = list(rows[0].keys()) if rows else []
    candidates = [row for row in rows if not _row_has_manual_decision(row)]
    selected = _balanced_select(candidates, max_rows=max_rows, max_per_reason=max_per_reason)
    output_rows = [_priority_row(row, rank) for rank, row in enumerate(selected, 1)]
    fields = [*PRIORITY_PREFIX_FIELDS, *[field for field in headers if field not in PRIORITY_PREFIX_FIELDS]]
    output_csv_path = Path(output_csv)
    _write_rows(output_csv_path, output_rows, fields)
    xlsx_summary = {}
    if output_xlsx:
        xlsx_summary = build_workbook([("Priority_Protected", output_csv_path)], output_xlsx)

    reason_counts = Counter(row.get("review_reason", "") for row in output_rows)
    priority_reason_counts = Counter(row.get("priority_reason", "") for row in output_rows)
    summary = {
        "source_csv": str(source_path),
        "output_csv": str(output_csv_path),
        "output_xlsx": str(output_xlsx or ""),
        "output_md": str(output_md or ""),
        "task_rows": len(output_rows),
        "max_rows": max_rows,
        "max_per_reason": max_per_reason,
        "source_rows": len(rows),
        "eligible_unfilled_rows": len(candidates),
        "reason_counts": dict(reason_counts),
        "priority_reason_counts": dict(priority_reason_counts),
        "agent_b_decision_counts": dict(Counter(row.get("agent_b_decision", "") for row in output_rows)),
        "selection_policy": (
            "Balanced high-value first batch from protected lanes: choose boundary rows with counter-evidence, "
            "unfetchable pages, missing provider-name evidence, country/language risk, logo-only/near-match evidence, "
            "generic-name collisions, unresolved recall candidates, and slug-extension identity risk."
        ),
        "recommended_use": (
            "Fill this smaller task first when review capacity is limited; fill the full protected-lane task before "
            "changing protected-lane policy."
        ),
        "manual_fields": ["manual_decision", "manual_url", "notes"],
        "decision_values": ["accept", "replace", "reject", "unsure"],
        "xlsx": xlsx_summary,
    }
    if output_json:
        path = Path(output_json)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    if output_md:
        path = Path(output_md)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_render_markdown(summary, output_rows), encoding="utf-8")
    return summary


def _render_markdown(summary: dict, rows: list[dict[str, str]]) -> str:
    lines = [
        "# Protected-Lane Priority Review Handoff",
        "",
        "## Purpose",
        "",
        "This is the first small protected-lane label batch. Fill it before changing thresholds, review-lane routing, or guarded pattern-release rules.",
        "",
        "## Fill Fields",
        "",
        "- manual_decision: accept, replace, reject, or unsure",
        "- manual_url: required for replace; optional for accept when the shown URL is correct",
        "- notes: short reason such as correct_official, wrong_company, country_mismatch, service_mismatch, logo_only_not_enough, unreachable, no_official",
        "",
        "## Summary",
        "",
        f"- Rows: {summary.get('task_rows')}",
        f"- Source rows: {summary.get('source_rows')}",
        f"- Eligible unfilled rows: {summary.get('eligible_unfilled_rows')}",
        f"- CSV: {summary.get('output_csv')}",
        f"- XLSX: {summary.get('output_xlsx')}",
        "",
        "## Review Reasons",
        "",
    ]
    for key, value in sorted((summary.get("reason_counts") or {}).items()):
        lines.append(f"- {key}: {value}")
    lines.extend(["", "## Priority Reasons", ""])
    for key, value in sorted((summary.get("priority_reason_counts") or {}).items()):
        lines.append(f"- {key}: {value}")
    lines.extend(["", "## Rows", ""])
    for row in rows:
        candidate = row.get("official_url") or row.get("candidate_url") or ""
        lines.append(
            "- {rank}. {provider} | {review_reason} | {priority_reason} | {candidate}".format(
                rank=row.get("priority_rank"),
                provider=row.get("provider_name"),
                review_reason=row.get("review_reason"),
                priority_reason=row.get("priority_reason"),
                candidate=candidate,
            )
        )
        impact = row.get("decision_impact") or ""
        if impact:
            lines.append(f"  - impact: {impact}")
    lines.append("")
    return "\n".join(lines)


def _priority_row(row: dict[str, str], rank: int) -> dict[str, str]:
    out = dict(row)
    reason = _priority_reason(row)
    out["priority_rank"] = str(rank)
    out["priority_reason"] = reason
    out["decision_impact"] = _decision_impact(row, reason)
    out["manual_decision"] = ""
    out["manual_url"] = ""
    out["notes"] = ""
    return out


def _balanced_select(rows: list[dict[str, str]], *, max_rows: int, max_per_reason: int) -> list[dict[str, str]]:
    if max_rows == 0:
        return []
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get("review_reason") or "")].append(row)
    for reason_rows in grouped.values():
        reason_rows.sort(key=_sort_key)
    reason_order = sorted(grouped, key=lambda reason: (-_best_reason_score(grouped[reason]), reason))
    selected: list[dict[str, str]] = []
    reason_counts: Counter[str] = Counter()
    while True:
        before = len(selected)
        for reason in reason_order:
            if max_rows > 0 and len(selected) >= max_rows:
                break
            if max_per_reason > 0 and reason_counts[reason] >= max_per_reason:
                continue
            bucket = grouped[reason]
            if not bucket:
                continue
            selected.append(bucket.pop(0))
            reason_counts[reason] += 1
        if len(selected) == before or (max_rows > 0 and len(selected) >= max_rows):
            break
    return selected


def _best_reason_score(rows: list[dict[str, str]]) -> int:
    return max((_priority_score(row) for row in rows), default=0)


def _sort_key(row: dict[str, str]) -> tuple:
    return (
        -_priority_score(row),
        _reason_rank(str(row.get("review_reason") or "")),
        -_to_int(row.get("sample_priority")),
        row.get("provider_name", ""),
        row.get("provider_id", ""),
    )


def _priority_score(row: dict[str, str]) -> int:
    blob = _evidence_blob(row)
    score = 0
    if str(row.get("agent_b_decision") or "").strip() != "accept":
        score += 20
    if str(row.get("counter_evidence") or "").strip():
        score += 35
    if "candidate_pages_not_fetchable" in blob:
        score += 34
    if "provider_name_not_found_on_candidate_pages" in blob:
        score += 32
    if "country_conflict" in blob:
        score += 30
    if "identity_cap_logo_only_evidence" in blob:
        score += 28
    if "listing_logo_visual_near_match" in blob:
        score += 24
    if "listing_logo_visual_match" in blob:
        score += 18
    if _has_non_ascii(str(row.get("provider_name") or "")):
        score += 16
    if _is_generic_or_short_name(str(row.get("provider_name") or "")):
        score += 14
    confidence = _to_int(row.get("source_confidence") or row.get("agent_b_confidence"))
    if 70 <= confidence <= 84:
        score += 18
    elif 55 <= confidence < 70:
        score += 14
    elif confidence < 55 and confidence > 0:
        score += 12
    if str(row.get("review_reason") or "") == "precision_slug_extension_identity_risk":
        score += 16
    if str(row.get("review_reason") or "") == "recall_unresolved_top_candidate":
        score += 12
    return score + min(_to_int(row.get("sample_priority")), 100)


def _priority_reason(row: dict[str, str]) -> str:
    review_reason = str(row.get("review_reason") or "")
    provider_name = str(row.get("provider_name") or "")
    blob = _evidence_blob(row)
    if "country_conflict" in blob:
        return "country_conflict_boundary"
    if "identity_cap_logo_only_evidence" in blob:
        return "logo_only_evidence_boundary"
    if "listing_logo_visual_near_match" in blob:
        return "logo_near_match_boundary"
    if "candidate_pages_not_fetchable" in blob:
        return "unfetchable_candidate_boundary"
    if "provider_name_not_found_on_candidate_pages" in blob:
        return "missing_provider_name_boundary"
    if review_reason == "precision_slug_extension_identity_risk":
        if str(row.get("agent_b_decision") or "") == "accept":
            return "slug_extension_agentb_accept"
        if "fuzzy" in blob:
            return "slug_extension_fuzzy_identity"
        if _is_generic_or_short_name(provider_name):
            return "slug_extension_generic_identity"
        return "slug_extension_identity_boundary"
    if review_reason == "recall_unresolved_top_candidate":
        if "service_content_matches_amazon_provider" in blob or "some_service_content_matches" in blob:
            return "recall_name_gap_with_service_match"
        return "recall_unresolved_candidate_boundary"
    if review_reason == "precision_low_confidence_auto_match":
        if _has_non_ascii(provider_name):
            return "localized_low_confidence_boundary"
        if _is_descriptive_provider_name(provider_name):
            return "descriptive_provider_name_boundary"
        return "low_confidence_auto_match_boundary"
    if review_reason == "precision_generic_identity_term_risk":
        if _is_generic_or_short_name(provider_name):
            return "generic_short_name_collision"
        if _has_non_ascii(provider_name):
            return "localized_generic_identity_boundary"
        return "generic_identity_boundary"
    return "protected_lane_boundary"


def _decision_impact(row: dict[str, str], priority_reason: str) -> str:
    review_reason = str(row.get("review_reason") or "")
    if review_reason == "recall_unresolved_top_candidate":
        return (
            "Accept/replace labels can justify a narrow recall rule only if rejects stay at zero; reject/unsure keeps "
            "raw AgentB recall manual-only."
        )
    if "logo" in priority_reason:
        return "Tests whether logo evidence can support identity only with name/service/country corroboration."
    if "country" in priority_reason or "localized" in priority_reason:
        return "Tests country and local-language matching so localized official sites are not over-rejected."
    if "unfetchable" in priority_reason:
        return "Tests whether search/domain evidence alone is insufficient when candidate pages cannot be fetched."
    if "missing_provider_name" in priority_reason:
        return "Tests whether service/legal/schema evidence can compensate for missing provider-name page evidence."
    if "slug_extension" in priority_reason:
        return "Tests whether slug-extension identity evidence can be released under guards or must stay protected."
    if "generic" in priority_reason or "descriptive" in priority_reason:
        return "Tests same-name/generic-name identity constraints before narrowing precision review lanes."
    return "Labels update protected-lane calibration evidence; one row does not change rules by itself."


def _reason_rank(reason: str) -> int:
    order = {
        "precision_slug_extension_identity_risk": 0,
        "precision_generic_identity_term_risk": 1,
        "precision_low_confidence_auto_match": 2,
        "recall_unresolved_top_candidate": 3,
    }
    return order.get(reason, 9)


def _evidence_blob(row: dict[str, str]) -> str:
    keys = [
        "supporting_facts",
        "counter_evidence",
        "evidence_summary",
        "reason_for_unsure",
        "provider_locations",
        "service_apis",
    ]
    return " ".join(str(row.get(key) or "") for key in keys).casefold()


def _is_generic_or_short_name(value: str) -> bool:
    tokens = re.findall(r"[a-z0-9]+", value.casefold())
    if len(tokens) <= 2:
        return True
    generic = {
        "agency",
        "consulting",
        "consultoria",
        "consultoría",
        "ecommerce",
        "ecom",
        "expert",
        "experts",
        "marketplace",
        "seller",
        "sellers",
        "services",
        "solutions",
        "support",
    }
    return sum(1 for token in tokens if token in generic) >= 2


def _is_descriptive_provider_name(value: str) -> bool:
    text = value.casefold()
    return any(marker in text for marker in ["full-service", "account management", "amazon", "scaling"])


def _has_non_ascii(value: str) -> bool:
    return any(ord(char) > 127 for char in value)


def _row_has_manual_decision(row: dict[str, str]) -> bool:
    return bool(str(row.get("manual_decision") or row.get("your_decision") or row.get("decision") or "").strip())


def _read_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _write_rows(path: Path, rows: list[dict[str, str]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    effective_fields = fields or [*PRIORITY_PREFIX_FIELDS, "message"]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=effective_fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _to_int(value: object) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
