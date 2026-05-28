from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from pathlib import Path

from .audit import audit_results
from .doctor import doctor
from .finalize import finalize_results
from .input_normalizer import normalize_provider_rows, read_normalized_csv, write_normalized_csv
from .query_builder import build_queries
from .scoring import choose_best, load_config
from .search_sources import collect_candidates, configured_sources


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def main(argv: list[str] | None = None) -> int:
    load_dotenv(Path(".env"))
    parser = argparse.ArgumentParser(description="Find official websites for Amazon GSPN providers.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_prepare = sub.add_parser("prepare", help="Normalize the Amazon GSPN provider CSV.")
    p_prepare.add_argument("--input", required=True)
    p_prepare.add_argument("--output", required=True)

    p_doctor = sub.add_parser("doctor", help="Check input file and search-source configuration.")
    p_doctor.add_argument("--input")

    p_preview = sub.add_parser("preview-queries", help="Show generated search queries for a few providers.")
    p_preview.add_argument("--input", required=True)
    p_preview.add_argument("--limit", type=int, default=10)

    p_run = sub.add_parser("run", help="Search the web and score official-site candidates.")
    p_run.add_argument("--input", required=True, help="Normalized CSV from prepare, or original GSPN CSV with --raw-input.")
    p_run.add_argument("--output", required=True)
    p_run.add_argument("--evidence", required=True)
    p_run.add_argument("--raw-input", action="store_true")
    p_run.add_argument("--limit", type=int, default=0, help="Optional provider limit for testing.")
    p_run.add_argument("--offset", type=int, default=0, help="Skip this many providers before running.")
    p_run.add_argument("--per-query", type=int, default=10, help="Maximum results to request per search query.")
    p_run.add_argument("--max-queries", type=int, default=0, help="Maximum generated search queries to run per provider.")
    p_run.add_argument("--append", action="store_true", help="Append to existing output/evidence files.")
    p_run.add_argument("--resume", action="store_true", help="Skip provider_ids already present in the output CSV.")
    p_run.add_argument("--config", default="config/scoring.json")
    p_run.add_argument(
        "--max-candidates",
        type=int,
        default=0,
        help="Maximum candidate URLs to score per provider. Defaults to config max_candidates_to_score.",
    )

    p_audit = sub.add_parser("audit-results", help="Summarize output quality and optionally write a review queue.")
    p_audit.add_argument("--input", required=True)
    p_audit.add_argument("--review-output")

    p_finalize = sub.add_parser(
        "finalize-results",
        help="Merge automatic results and manual review decisions into the final website CSV.",
    )
    p_finalize.add_argument("--input", required=True)
    p_finalize.add_argument("--output", required=True)
    p_finalize.add_argument("--review")
    p_finalize.add_argument("--unresolved-output")

    args = parser.parse_args(argv)
    if args.command == "prepare":
        providers = normalize_provider_rows(args.input)
        write_normalized_csv(providers, args.output)
        print(f"wrote {len(providers)} normalized providers to {args.output}")
        return 0
    if args.command == "doctor":
        print(json.dumps(doctor(args.input), ensure_ascii=False, indent=2))
        return 0
    if args.command == "preview-queries":
        providers = read_normalized_csv(args.input)
        for provider in providers[: args.limit]:
            print(provider["provider_name"])
            for query in build_queries(provider):
                print(f"  - {query}")
        return 0
    if args.command == "run":
        providers = normalize_provider_rows(args.input) if args.raw_input else read_normalized_csv(args.input)
        if args.offset:
            providers = providers[args.offset :]
        if args.limit:
            providers = providers[: args.limit]
        if args.resume and Path(args.output).exists():
            done_ids = read_done_provider_ids(args.output)
            providers = [p for p in providers if p.get("provider_id", "") not in done_ids]
            print(f"resume: skipping {len(done_ids)} provider_ids already in {args.output}", file=sys.stderr)
        sources = configured_sources()
        if not sources:
            print(
                "warning: no search API keys configured; only direct domain guesses will be scored. "
                "Set SERPAPI_API_KEY, BRAVE_API_KEY, TAVILY_API_KEY, SERPER_API_KEY, FIRECRAWL_API_KEY, "
                "or DDGS_ENABLED=1 for discovery.",
                file=sys.stderr,
            )
        config = load_config(args.config)
        run_workflow(
            providers,
            args.output,
            args.evidence,
            config,
            per_query=args.per_query,
            append=args.append or args.resume,
            max_candidates=args.max_candidates or None,
            max_queries=args.max_queries or None,
        )
        return 0
    if args.command == "audit-results":
        print(json.dumps(audit_results(args.input, args.review_output), ensure_ascii=False, indent=2))
        return 0
    if args.command == "finalize-results":
        print(
            json.dumps(
                finalize_results(
                    args.input,
                    args.output,
                    review_csv=args.review,
                    unresolved_csv=args.unresolved_output,
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    return 2


def read_done_provider_ids(output_csv: str | Path) -> set[str]:
    with Path(output_csv).open(newline="", encoding="utf-8-sig") as f:
        return {row.get("provider_id", "") for row in csv.DictReader(f) if row.get("provider_id", "")}


def run_workflow(
    providers: list[dict],
    output_csv: str,
    evidence_jsonl: str,
    config: dict,
    *,
    per_query: int = 10,
    append: bool = False,
    max_candidates: int | None = None,
    max_queries: int | None = None,
) -> None:
    Path(output_csv).parent.mkdir(parents=True, exist_ok=True)
    Path(evidence_jsonl).parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "provider_id",
        "provider_name",
        "official_url",
        "official_domain",
        "confidence",
        "status",
        "evidence_summary",
        "candidate_count",
        "scored_candidate_count",
        "service_apis",
        "provider_locations",
    ]
    candidate_limit = max_candidates if max_candidates is not None else int(config.get("max_candidates_to_score", 0) or 0)
    output_exists = Path(output_csv).exists()
    mode = "a" if append else "w"
    with Path(output_csv).open(mode, newline="", encoding="utf-8") as out_f, Path(evidence_jsonl).open(
        mode, encoding="utf-8"
    ) as evidence_f:
        writer = csv.DictWriter(out_f, fieldnames=fields)
        if not append or not output_exists:
            writer.writeheader()
        for idx, provider in enumerate(providers, 1):
            print(f"[{idx}/{len(providers)}] {provider['provider_name']}", file=sys.stderr)
            candidates = collect_candidates(provider, per_query=per_query, max_queries=max_queries)
            candidates_to_score = limit_candidates_for_scoring(candidates, candidate_limit)
            result = choose_best(provider, candidates_to_score, config)
            writer.writerow(
                {
                    "provider_id": provider.get("provider_id", ""),
                    "provider_name": provider.get("provider_name", ""),
                    "official_url": result["official_url"],
                    "official_domain": result["official_domain"],
                    "confidence": result["confidence"],
                    "status": result["status"],
                    "evidence_summary": result["evidence_summary"],
                    "candidate_count": len(candidates),
                    "scored_candidate_count": len(candidates_to_score),
                    "service_apis": json.dumps(provider.get("service_apis", []), ensure_ascii=False),
                    "provider_locations": json.dumps(provider.get("provider_locations", []), ensure_ascii=False),
                }
            )
            evidence_f.write(
                json.dumps(
                    {
                        "provider_id": provider.get("provider_id", ""),
                        "provider_name": provider.get("provider_name", ""),
                        "result": {k: v for k, v in result.items() if k != "candidates"},
                        "candidate_count": len(candidates),
                        "scored_candidate_count": len(candidates_to_score),
                        "candidates": result["candidates"],
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
            out_f.flush()
            evidence_f.flush()


def limit_candidates_for_scoring(candidates: list, limit: int | None) -> list:
    if not limit or limit <= 0 or len(candidates) <= limit:
        return candidates
    domain_guesses = [candidate for candidate in candidates if getattr(candidate, "source", "") == "domain_guess"]
    other_candidates = [candidate for candidate in candidates if getattr(candidate, "source", "") != "domain_guess"]
    room_for_others = max(0, limit - len(domain_guesses))
    return other_candidates[:room_for_others] + domain_guesses


if __name__ == "__main__":
    raise SystemExit(main())
