from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from finder.text import domain_from_url
from finder.text import tokens
from tools.build_linked_workbook import build_workbook
from tools.output_layout import (
    DEFAULT_MATCHED_REVIEW_CONFIDENCE_CUTOFF,
    DEFAULT_SECOND_PASS_REVIEW_CONFIDENCE_CUTOFF,
    first_existing,
    publish_review_task_aliases,
    review_task_paths,
)


TASK_FIELDS = [
    "review_reason",
    "provider_id",
    "provider_name",
    "provider_detail_url",
    "official_url",
    "official_domain",
    "status",
    "confidence",
    "decision_source",
    "source_status",
    "top_candidate_url",
    "top_candidate_domain",
    "top_candidate_score",
    "candidate_1_url",
    "candidate_1_domain",
    "candidate_1_score",
    "evidence_summary",
    "service_apis",
    "provider_locations",
    "manual_decision",
    "manual_url",
    "notes",
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a simplified clickable manual review task from a run directory.")
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--output-csv")
    parser.add_argument("--output-xlsx")
    parser.add_argument("--write-xlsx", action="store_true")
    parser.add_argument("--include-matched-confidence-below", type=int, default=DEFAULT_MATCHED_REVIEW_CONFIDENCE_CUTOFF)
    args = parser.parse_args(argv)

    summary = build_manual_review_task(
        run_dir=args.run_dir,
        output_csv=args.output_csv,
        output_xlsx=args.output_xlsx,
        write_xlsx=args.write_xlsx,
        include_matched_confidence_below=args.include_matched_confidence_below,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def build_manual_review_task(
    *,
    run_dir: str | Path,
    output_csv: str | Path | None = None,
    output_xlsx: str | Path | None = None,
    write_xlsx: bool = True,
    include_matched_confidence_below: int = DEFAULT_MATCHED_REVIEW_CONFIDENCE_CUTOFF,
) -> dict:
    run_dir = Path(run_dir)
    final_path = first_existing(
        run_dir,
        "official_sites.csv",
        "provider_final_official_websites_second_pass.csv",
        "details/first_pass/final.csv",
        "provider_final_official_websites.csv",
    )
    if not final_path:
        raise FileNotFoundError(f"final result CSV not found in {run_dir}")

    final_rows = _read_rows(final_path)
    second_pass_rows = _index_rows(
        first_existing(run_dir, "details/second_pass/results.csv", "unresolved_second_pass_results.csv")
        or run_dir / "details/second_pass/results.csv"
    )
    review_rows = _index_rows(
        first_existing(run_dir, "details/first_pass/review_sheet.csv", "provider_review_sheet_enhanced.csv")
        or run_dir / "details/first_pass/review_sheet.csv"
    )
    task_rows = [
        _task_row(row, second_pass_rows.get(_row_key(row), {}), review_rows.get(_row_key(row), {}))
        for row in final_rows
        if _needs_manual_review(row, second_pass_rows.get(_row_key(row), {}), include_matched_confidence_below)
    ]
    task_rows = sorted(task_rows, key=_sort_key)

    canonical = review_task_paths(run_dir)
    output_csv_path = Path(output_csv) if output_csv else canonical["csv"]
    _write_rows(output_csv_path, task_rows, TASK_FIELDS)
    xlsx_summary = {}
    output_xlsx_path = Path(output_xlsx) if output_xlsx else canonical["xlsx"]
    if write_xlsx:
        xlsx_summary = build_workbook([("Manual_Review_Task", output_csv_path)], output_xlsx_path)
    aliases = publish_review_task_aliases(run_dir, {"csv": output_csv_path, "xlsx": output_xlsx_path})

    return {
        "review_rows": len(task_rows),
        "matched_review_confidence_below": include_matched_confidence_below,
        "second_pass_review_confidence_below": DEFAULT_SECOND_PASS_REVIEW_CONFIDENCE_CUTOFF,
        "source_final_csv": str(final_path),
        "output_csv": str(output_csv_path),
        "output_xlsx": str(output_xlsx_path) if write_xlsx else "",
        "legacy_aliases": aliases,
        "xlsx": xlsx_summary,
        "reason_counts": _reason_counts(task_rows),
    }


def _needs_manual_review(row: dict[str, str], second_pass_row: dict[str, str], confidence_cutoff: int) -> bool:
    status = row.get("status", "")
    confidence = _to_int(row.get("confidence"))
    if not row.get("official_url"):
        return True
    if status == "manual_accepted":
        return confidence < DEFAULT_SECOND_PASS_REVIEW_CONFIDENCE_CUTOFF
    if status != "matched":
        return True
    if confidence < confidence_cutoff:
        return True
    evidence = (row.get("evidence_summary") or second_pass_row.get("evidence_summary") or "").casefold()
    if "identity_cap_" in evidence or "page_industry_mismatch:" in evidence:
        return True
    if _high_confidence_ambiguous_identity_risk(row, evidence, confidence):
        return True
    if second_pass_row.get("accepted_for_final") == "true" and confidence < confidence_cutoff:
        return True
    return False


def _task_row(row: dict[str, str], second_pass_row: dict[str, str], review_row: dict[str, str]) -> dict[str, str]:
    top_url = _top_candidate_url(row, second_pass_row, review_row)
    top_domain = domain_from_url(top_url)
    official_url = row.get("official_url") or top_url
    official_domain = domain_from_url(row.get("official_domain") or official_url)
    return {
        "review_reason": _review_reason(row, second_pass_row),
        "provider_id": row.get("provider_id", ""),
        "provider_name": row.get("provider_name", ""),
        "provider_detail_url": row.get("provider_detail_url", ""),
        "official_url": official_url,
        "official_domain": official_domain,
        "status": row.get("status", ""),
        "confidence": row.get("confidence", ""),
        "decision_source": row.get("decision_source", ""),
        "source_status": row.get("source_status", ""),
        "top_candidate_url": top_url,
        "top_candidate_domain": top_domain,
        "top_candidate_score": second_pass_row.get("confidence", "") or review_row.get("candidate_1_score", ""),
        "candidate_1_url": top_url,
        "candidate_1_domain": top_domain,
        "candidate_1_score": second_pass_row.get("confidence", "") or review_row.get("candidate_1_score", ""),
        "evidence_summary": row.get("evidence_summary", "") or second_pass_row.get("evidence_summary", ""),
        "service_apis": row.get("service_apis", ""),
        "provider_locations": row.get("provider_locations", ""),
        "manual_decision": "",
        "manual_url": "",
        "notes": "",
    }


def _review_reason(row: dict[str, str], second_pass_row: dict[str, str]) -> str:
    status = row.get("status", "")
    confidence = _to_int(row.get("confidence"))
    if not row.get("official_url"):
        if second_pass_row.get("official_url") or second_pass_row.get("previous_top_candidate_url"):
            return "recall_unresolved_top_candidate"
        return "recall_unresolved_manual_search"
    if status == "calibrated_released":
        return "precision_calibrated_pattern_release"
    if status == "manual_accepted" and confidence < 70:
        return "precision_second_pass_accepted_lt70"
    if status == "manual_accepted" and confidence < DEFAULT_SECOND_PASS_REVIEW_CONFIDENCE_CUTOFF:
        return "precision_second_pass_accepted_70_84"
    if status == "manual_accepted":
        return "precision_second_pass_accepted_85_plus"
    evidence = (row.get("evidence_summary") or second_pass_row.get("evidence_summary") or "").casefold()
    if "identity_cap_" in evidence or "page_industry_mismatch:" in evidence:
        return "precision_identity_constraint_risk"
    if _high_confidence_generic_identity_term_risk(row, evidence, confidence):
        return "precision_generic_identity_term_risk"
    if _high_confidence_slug_extension_risk(row, evidence, confidence):
        return "precision_slug_extension_identity_risk"
    if confidence < DEFAULT_MATCHED_REVIEW_CONFIDENCE_CUTOFF:
        return "precision_low_confidence_auto_match"
    return "spot_check_non_matched_status"


def _top_candidate_url(row: dict[str, str], second_pass_row: dict[str, str], review_row: dict[str, str]) -> str:
    for value in [
        row.get("official_url", ""),
        second_pass_row.get("official_url", ""),
        second_pass_row.get("previous_top_candidate_url", ""),
        review_row.get("candidate_1_url", ""),
    ]:
        if value:
            return value
    return ""


def _read_rows(path: str | Path) -> list[dict[str, str]]:
    with Path(path).open(newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _write_rows(path: str | Path, rows: list[dict[str, str]], fields: list[str]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _index_rows(path: Path) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    return {_row_key(row): row for row in _read_rows(path) if _row_key(row)}


def _row_key(row: dict[str, str]) -> str:
    provider_id = (row.get("provider_id") or "").strip()
    if provider_id:
        return f"id:{provider_id}"
    return f"name:{(row.get('provider_name') or '').strip().casefold()}"


def _first_existing(paths: list[Path]) -> Path | None:
    for path in paths:
        if path.exists():
            return path
    return None


def _sort_key(row: dict[str, str]) -> tuple[int, int, str]:
    priority = {
        "precision_second_pass_accepted_lt70": 0,
        "precision_second_pass_accepted_70_84": 1,
        "precision_second_pass_accepted_85_plus": 2,
        "precision_identity_constraint_risk": 3,
        "precision_generic_identity_term_risk": 4,
        "precision_slug_extension_identity_risk": 5,
        "precision_ambiguous_name_risk": 6,
        "precision_calibrated_pattern_release": 6,
        "precision_low_confidence_auto_match": 7,
        "recall_unresolved_top_candidate": 8,
        "recall_unresolved_manual_search": 9,
    }
    return (priority.get(row.get("review_reason", ""), 9), _to_int(row.get("confidence")), row.get("provider_name", ""))


def _reason_counts(rows: list[dict[str, str]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        reason = row.get("review_reason", "")
        counts[reason] = counts.get(reason, 0) + 1
    return counts


def _to_int(value: object) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def _ambiguous_provider_name(name: str) -> bool:
    provider_tokens = tokens(name)
    if not provider_tokens:
        return False
    generic = {
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
        "management",
        "marketplace",
        "media",
        "seller",
        "service",
        "services",
        "solution",
        "solutions",
        "brand",
        "brands",
    }
    meaningful = [token for token in provider_tokens if token not in generic]
    return len(meaningful) <= 1 or len("".join(provider_tokens)) <= 4


def _high_confidence_ambiguous_identity_risk(row: dict[str, str], evidence: str, confidence: int) -> bool:
    return _high_confidence_generic_identity_term_risk(row, evidence, confidence) or _high_confidence_slug_extension_risk(
        row, evidence, confidence
    )


def _high_confidence_generic_identity_term_risk(row: dict[str, str], evidence: str, confidence: int) -> bool:
    if confidence < 85 or not _ambiguous_provider_name(row.get("provider_name", "")):
        return False
    return _has_generic_identity_term(row.get("provider_name", "")) and "listing_logo_visual_match" not in evidence


def _high_confidence_slug_extension_risk(row: dict[str, str], evidence: str, confidence: int) -> bool:
    if confidence < 85 or not _ambiguous_provider_name(row.get("provider_name", "")):
        return False
    if "listing_logo_visual_match" in evidence:
        return False
    return "domain_contains_provider_slug" in evidence and "domain_exact_provider_slug" not in evidence


def _has_generic_identity_term(name: str) -> bool:
    text = name.casefold()
    return "consult" in text or "seller" in text


def _has_strong_identity_summary(evidence: str) -> bool:
    if "identity_cap_" in evidence or "page_industry_mismatch:" in evidence:
        return False
    has_page_name = (
        "page_contains_exact_provider_name" in evidence
        or "page_contains_provider_name_tokens" in evidence
        or "page_fuzzy_provider_name_match" in evidence
    )
    has_name = has_page_name or "search_result_contains_exact_name" in evidence or "domain_exact_provider_slug" in evidence
    has_service = (
        "page_contains_amazon_service_keywords" in evidence
        or "page_contains_some_service_keywords" in evidence
        or "page_mentions_amazon_spn" in evidence
        or "search_snippet_contains_amazon_service_keywords" in evidence
    )
    has_domain_or_logo = "domain_exact_provider_slug" in evidence or "listing_logo_visual_match" in evidence
    return has_name and has_service and (has_page_name or has_domain_or_logo)


if __name__ == "__main__":
    raise SystemExit(main())
