from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path


def audit_results(input_csv: str | Path, review_csv: str | Path | None = None) -> dict:
    rows = _read_rows(input_csv)
    status_counts = Counter(row.get("status", "") for row in rows)
    confidences = [_to_int(row.get("confidence")) for row in rows if row.get("confidence")]
    matched = [row for row in rows if row.get("status") == "matched"]
    review = [row for row in rows if row.get("status") == "needs_review"]
    weak = [row for row in rows if row.get("status") in {"low_confidence", "not_found"}]

    if review_csv:
        _write_review_queue(review + weak, review_csv)

    return {
        "total_rows": len(rows),
        "status_counts": dict(status_counts),
        "matched_rows": len(matched),
        "needs_review_rows": len(review),
        "unresolved_rows": len(weak),
        "min_confidence": min(confidences) if confidences else None,
        "max_confidence": max(confidences) if confidences else None,
        "avg_confidence": round(sum(confidences) / len(confidences), 1) if confidences else None,
        "top_review_rows": [
            {
                "provider_id": row.get("provider_id", ""),
                "provider_name": row.get("provider_name", ""),
                "official_url": row.get("official_url", ""),
                "confidence": row.get("confidence", ""),
                "status": row.get("status", ""),
                "evidence_summary": row.get("evidence_summary", ""),
            }
            for row in (review + weak)[:10]
        ],
    }


def _read_rows(input_csv: str | Path) -> list[dict[str, str]]:
    with Path(input_csv).open(newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _to_int(value: str | None) -> int:
    try:
        return int(float(value or "0"))
    except ValueError:
        return 0


def _write_review_queue(rows: list[dict[str, str]], output_csv: str | Path) -> None:
    output = Path(output_csv)
    output.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "provider_id",
        "provider_name",
        "provider_detail_url",
        "listing_logo_url",
        "official_url",
        "official_domain",
        "confidence",
        "status",
        "evidence_summary",
        "manual_decision",
        "manual_url",
        "notes",
    ]
    with output.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "provider_id": row.get("provider_id", ""),
                    "provider_name": row.get("provider_name", ""),
                    "provider_detail_url": row.get("provider_detail_url", "") or row.get("detail_url", ""),
                    "listing_logo_url": row.get("listing_logo_url", ""),
                    "official_url": row.get("official_url", ""),
                    "official_domain": row.get("official_domain", ""),
                    "confidence": row.get("confidence", ""),
                    "status": row.get("status", ""),
                    "evidence_summary": row.get("evidence_summary", ""),
                    "manual_decision": "",
                    "manual_url": "",
                    "notes": "",
                }
            )
