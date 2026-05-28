from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse, urlunparse

from .text import domain_from_url


FINAL_FIELDS = [
    "provider_id",
    "provider_name",
    "provider_detail_url",
    "listing_logo_url",
    "official_url",
    "official_domain",
    "status",
    "decision_source",
    "confidence",
    "source_status",
    "evidence_summary",
    "candidate_count",
    "scored_candidate_count",
    "service_apis",
    "provider_locations",
    "notes",
]

ACCEPTED_MANUAL_DECISIONS = {
    "accept": "accept",
    "approve": "accept",
    "approved": "accept",
    "confirm": "accept",
    "confirmed": "accept",
    "yes": "accept",
    "接受": "accept",
    "确认": "accept",
    "replace": "replace",
    "override": "replace",
    "替换": "replace",
    "reject": "reject",
    "rejected": "reject",
    "not_found": "reject",
    "no": "reject",
    "拒绝": "reject",
    "否": "reject",
}


def finalize_results(
    results_csv: str | Path,
    output_csv: str | Path,
    *,
    review_csv: str | Path | None = None,
    unresolved_csv: str | Path | None = None,
) -> dict:
    result_rows = read_rows(results_csv)
    review_rows = read_rows(review_csv) if review_csv else []
    final_rows, unresolved_rows, summary = finalize_rows(result_rows, review_rows)
    write_rows(output_csv, final_rows, FINAL_FIELDS)
    if unresolved_csv:
        write_rows(unresolved_csv, unresolved_rows, FINAL_FIELDS)
    summary["output_csv"] = str(output_csv)
    if unresolved_csv:
        summary["unresolved_csv"] = str(unresolved_csv)
    return summary


def finalize_rows(
    result_rows: list[dict[str, str]],
    review_rows: list[dict[str, str]] | None = None,
) -> tuple[list[dict[str, str]], list[dict[str, str]], dict]:
    review_index = _build_review_index(review_rows or [])
    final_rows = []
    for result_row in result_rows:
        review_row = _review_for(result_row, review_index)
        final_rows.append(_finalize_row(result_row, review_row))

    unresolved_rows = [row for row in final_rows if not row.get("official_url") or row.get("status") == "rejected"]
    status_counts = Counter(row.get("status", "") for row in final_rows)
    decision_counts = Counter(row.get("decision_source", "") for row in final_rows)
    summary = {
        "total_rows": len(result_rows),
        "final_rows": len(final_rows),
        "official_url_rows": sum(1 for row in final_rows if row.get("official_url")),
        "unresolved_rows": len(unresolved_rows),
        "status_counts": dict(status_counts),
        "decision_source_counts": dict(decision_counts),
    }
    return final_rows, unresolved_rows, summary


def read_rows(path: str | Path | None) -> list[dict[str, str]]:
    if not path:
        return []
    with Path(path).open(newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def write_rows(path: str | Path, rows: list[dict[str, str]], fields: list[str]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _build_review_index(rows: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    index: dict[str, dict[str, str]] = {}
    for row in rows:
        provider_id = row.get("provider_id", "").strip()
        provider_name = row.get("provider_name", "").strip().casefold()
        if provider_id:
            index[f"id:{provider_id}"] = row
        if provider_name:
            index[f"name:{provider_name}"] = row
    return index


def _review_for(result_row: dict[str, str], review_index: dict[str, dict[str, str]]) -> dict[str, str] | None:
    provider_id = result_row.get("provider_id", "").strip()
    provider_name = result_row.get("provider_name", "").strip().casefold()
    if provider_id and f"id:{provider_id}" in review_index:
        return review_index[f"id:{provider_id}"]
    if provider_name and f"name:{provider_name}" in review_index:
        return review_index[f"name:{provider_name}"]
    return None


def _finalize_row(result_row: dict[str, str], review_row: dict[str, str] | None) -> dict[str, str]:
    source_status = result_row.get("status", "")
    decision = _manual_decision(review_row)
    notes = _notes(review_row)

    if decision == "reject":
        return _base_final_row(
            result_row,
            official_url="",
            official_domain="",
            status="rejected",
            decision_source="manual_reject",
            notes=notes,
        )

    if decision in {"accept", "replace"}:
        url_source = review_row.get("manual_url", "") if review_row else ""
        if decision == "accept" and not url_source:
            url_source = review_row.get("official_url", "") if review_row else ""
        if decision == "accept" and not url_source:
            url_source = review_row.get("candidate_1_url", "") if review_row else ""
        if decision == "accept" and not url_source:
            url_source = result_row.get("official_url", "")
        url = _normalize_url(url_source)
        if not url:
            return _base_final_row(
                result_row,
                official_url="",
                official_domain="",
                status="unresolved",
                decision_source=f"manual_{decision}_missing_url",
                notes=_append_note(notes, f"manual_{decision}_missing_url"),
            )
        final_source_row = _with_review_evidence(result_row, review_row)
        return _base_final_row(
            final_source_row,
            official_url=url,
            official_domain=domain_from_url(url),
            status="manual_accepted",
            decision_source=f"manual_{decision}",
            notes=notes,
        )

    if decision and decision.startswith("invalid:"):
        return _base_final_row(
            result_row,
            official_url="",
            official_domain="",
            status="invalid_manual_decision",
            decision_source="invalid_manual_decision",
            notes=_append_note(notes, decision),
        )

    if source_status == "matched":
        url = _normalize_url(result_row.get("official_url", ""))
        return _base_final_row(
            result_row,
            official_url=url,
            official_domain=domain_from_url(result_row.get("official_domain", "") or url) if url else "",
            status="matched",
            decision_source="auto_matched",
            notes=notes,
        )

    return _base_final_row(
        result_row,
        official_url="",
        official_domain="",
        status="unresolved",
        decision_source="pending_review",
        notes=notes,
    )


def _manual_decision(review_row: dict[str, str] | None) -> str:
    if not review_row:
        return ""
    raw = (review_row.get("manual_decision") or "").strip().casefold()
    if not raw and (review_row.get("manual_url") or "").strip():
        return "replace"
    if not raw:
        return ""
    decision = ACCEPTED_MANUAL_DECISIONS.get(raw)
    return decision or f"invalid:{raw}"


def _normalize_url(value: str) -> str:
    value = (value or "").strip().rstrip(".,);]")
    if not value:
        return ""
    if value.startswith("//"):
        value = f"https:{value}"
    if "://" not in value:
        value = f"https://{value}"
    return _homepage_for_risky_path(value)


def _with_review_evidence(result_row: dict[str, str], review_row: dict[str, str] | None) -> dict[str, str]:
    if not review_row:
        return result_row
    out = dict(result_row)
    for key in [
        "confidence",
        "evidence_summary",
        "candidate_count",
        "scored_candidate_count",
        "service_apis",
        "provider_locations",
    ]:
        if review_row.get(key):
            out[key] = review_row[key]
    if review_row.get("source_status"):
        out["status"] = review_row["source_status"]
    return out


def _homepage_for_risky_path(url: str) -> str:
    parsed = urlparse(url)
    first_segment = (parsed.path or "").strip("/").split("/", 1)[0].casefold()
    risky_segments = {"app", "apps", "auth", "login", "password", "sign-in", "signin", "user", "users"}
    if first_segment in risky_segments:
        return urlunparse((parsed.scheme or "https", parsed.netloc, "/", "", "", ""))
    return url


def _base_final_row(
    result_row: dict[str, str],
    *,
    official_url: str,
    official_domain: str,
    status: str,
    decision_source: str,
    notes: str,
) -> dict[str, str]:
    return {
        "provider_id": result_row.get("provider_id", ""),
        "provider_name": result_row.get("provider_name", ""),
        "provider_detail_url": result_row.get("provider_detail_url", "") or result_row.get("detail_url", ""),
        "listing_logo_url": result_row.get("listing_logo_url", ""),
        "official_url": official_url,
        "official_domain": official_domain,
        "status": status,
        "decision_source": decision_source,
        "confidence": result_row.get("confidence", ""),
        "source_status": result_row.get("status", ""),
        "evidence_summary": result_row.get("evidence_summary", ""),
        "candidate_count": result_row.get("candidate_count", ""),
        "scored_candidate_count": result_row.get("scored_candidate_count", ""),
        "service_apis": result_row.get("service_apis", ""),
        "provider_locations": result_row.get("provider_locations", ""),
        "notes": notes,
    }


def _notes(review_row: dict[str, str] | None) -> str:
    if not review_row:
        return ""
    return (review_row.get("notes") or "").strip()


def _append_note(existing: str, note: str) -> str:
    if not existing:
        return note
    return f"{existing}; {note}"
