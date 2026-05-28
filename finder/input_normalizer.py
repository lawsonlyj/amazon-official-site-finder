from __future__ import annotations

import csv
import json
from collections import OrderedDict
from pathlib import Path
from typing import Any

from .text import compact_space


DESCRIPTION_MARKERS = {
    "Amazon SPN 服务商唯一 ID；主表关联键之一",
    "服务商名称",
}


def _loads_list(value: str) -> list[Any]:
    try:
        parsed = json.loads(value or "[]")
        return parsed if isinstance(parsed, list) else []
    except json.JSONDecodeError:
        return []


def normalize_provider_rows(input_csv: str | Path) -> list[dict[str, Any]]:
    providers: OrderedDict[str, dict[str, Any]] = OrderedDict()
    with Path(input_csv).open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            provider_id = compact_space(row.get("provider_id", ""))
            provider_name = compact_space(row.get("provider_name", ""))
            if not provider_id or provider_id in DESCRIPTION_MARKERS or provider_name in DESCRIPTION_MARKERS:
                continue
            key = provider_id or provider_name.lower()
            item = providers.setdefault(
                key,
                {
                    "provider_id": provider_id,
                    "provider_name": provider_name,
                    "service_apis": [],
                    "detail_urls": [],
                    "listing_logo_url": compact_space(row.get("listing_logo_url", "")),
                    "provider_locations": [],
                    "provider_languages": [],
                    "service_types": [],
                    "about_listing_text": "",
                    "service_description": "",
                    "source_rows": 0,
                },
            )
            item["source_rows"] += 1
            _append_unique(item["service_apis"], compact_space(row.get("service_api", "")))
            _append_unique(item["detail_urls"], compact_space(row.get("detail_url", "")))
            for field, target in [
                ("provider_locations_json", "provider_locations"),
                ("provider_languages_json", "provider_languages"),
                ("service_types_json", "service_types"),
            ]:
                for value in _loads_list(row.get(field, "")):
                    if isinstance(value, str):
                        _append_unique(item[target], compact_space(value))
            if not item["about_listing_text"]:
                item["about_listing_text"] = compact_space(row.get("about_listing_text", ""))[:1200]
            if not item["service_description"]:
                item["service_description"] = compact_space(row.get("service_description", ""))[:400]
    return list(providers.values())


def write_normalized_csv(providers: list[dict[str, Any]], output_csv: str | Path) -> None:
    output = Path(output_csv)
    output.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "provider_id",
        "provider_name",
        "service_apis",
        "provider_locations",
        "provider_languages",
        "service_types",
        "listing_logo_url",
        "detail_url",
        "about_listing_text",
        "source_rows",
    ]
    with output.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for p in providers:
            writer.writerow(
                {
                    "provider_id": p["provider_id"],
                    "provider_name": p["provider_name"],
                    "service_apis": json.dumps(p["service_apis"], ensure_ascii=False),
                    "provider_locations": json.dumps(p["provider_locations"], ensure_ascii=False),
                    "provider_languages": json.dumps(p["provider_languages"], ensure_ascii=False),
                    "service_types": json.dumps(p["service_types"], ensure_ascii=False),
                    "listing_logo_url": p["listing_logo_url"],
                    "detail_url": p["detail_urls"][0] if p["detail_urls"] else "",
                    "about_listing_text": p["about_listing_text"],
                    "source_rows": p["source_rows"],
                }
            )


def read_normalized_csv(input_csv: str | Path) -> list[dict[str, Any]]:
    with Path(input_csv).open(newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    providers = []
    for row in rows:
        item = dict(row)
        for field in ["service_apis", "provider_locations", "provider_languages", "service_types"]:
            item[field] = _loads_list(row.get(field, ""))
        item["detail_urls"] = [row["detail_url"]] if row.get("detail_url") else []
        item["source_rows"] = int(row.get("source_rows") or 0)
        providers.append(item)
    return providers


def _append_unique(values: list[str], value: str) -> None:
    if value and value not in values:
        values.append(value)
