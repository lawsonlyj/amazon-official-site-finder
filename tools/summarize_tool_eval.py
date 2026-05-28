from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Summarize tool evaluation CSV output.")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output-md", required=True)
    parser.add_argument("--output-json")
    args = parser.parse_args(argv)

    rows = read_rows(args.input)
    summary = summarize(rows)
    write_markdown(summary, args.output_md)
    if args.output_json:
        Path(args.output_json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output_json).write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary["overall"], ensure_ascii=False, indent=2))
    return 0


def read_rows(input_csv: str | Path) -> list[dict[str, str]]:
    with Path(input_csv).open(newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def summarize(rows: list[dict[str, str]]) -> dict:
    by_provider: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        by_provider[row["provider_name"]].append(row)

    providers = []
    for provider_name, provider_rows in by_provider.items():
        usable = [r for r in provider_rows if r.get("url") and r.get("excluded") == "False"]
        excluded = [r for r in provider_rows if r.get("excluded") == "True"]
        extracted = [r for r in usable if _to_int(r.get("extract_chars")) > 200]
        strong = [
            r
            for r in usable
            if r.get("domain_slug_match") == "True"
            and (r.get("service_terms_in_text") == "True" or r.get("provider_name_in_text") == "True")
        ]
        providers.append(
            {
                "provider_name": provider_name,
                "total_results": len(provider_rows),
                "usable_results": len(usable),
                "excluded_results": len(excluded),
                "extractable_results": len(extracted),
                "strong_candidate_results": len(strong),
                "top_strong_candidates": [
                    {
                        "url": r.get("url", ""),
                        "domain": r.get("domain", ""),
                        "query": r.get("query", ""),
                        "rank": r.get("rank", ""),
                        "extract_chars": _to_int(r.get("extract_chars")),
                    }
                    for r in strong[:5]
                ],
            }
        )

    overall = {
        "providers": len(providers),
        "total_results": len(rows),
        "usable_results": sum(p["usable_results"] for p in providers),
        "excluded_results": sum(p["excluded_results"] for p in providers),
        "extractable_results": sum(p["extractable_results"] for p in providers),
        "strong_candidate_results": sum(p["strong_candidate_results"] for p in providers),
    }
    return {"overall": overall, "providers": providers}


def write_markdown(summary: dict, output_md: str | Path) -> None:
    output = Path(output_md)
    output.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Tool Evaluation Summary",
        "",
        "## Overall",
        "",
        "| Metric | Value |",
        "|---|---:|",
    ]
    for key, value in summary["overall"].items():
        lines.append(f"| {key} | {value} |")
    lines.extend(["", "## By Provider", ""])
    lines.append(
        "| Provider | Total | Usable | Excluded | Extractable | Strong Candidates |"
    )
    lines.append("|---|---:|---:|---:|---:|---:|")
    for provider in summary["providers"]:
        lines.append(
            f"| {provider['provider_name']} | {provider['total_results']} | {provider['usable_results']} | "
            f"{provider['excluded_results']} | {provider['extractable_results']} | {provider['strong_candidate_results']} |"
        )
    lines.extend(["", "## Strong Candidate Examples", ""])
    for provider in summary["providers"]:
        if not provider["top_strong_candidates"]:
            continue
        lines.append(f"### {provider['provider_name']}")
        for item in provider["top_strong_candidates"]:
            lines.append(
                f"- `{item['domain']}` rank {item['rank']} from `{item['query']}`: {item['url']}"
            )
        lines.append("")
    output.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def _to_int(value: str | None) -> int:
    try:
        return int(float(value or "0"))
    except ValueError:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
