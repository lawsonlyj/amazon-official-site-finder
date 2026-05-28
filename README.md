# Amazon GSPN Official Website Finder

This workflow takes Amazon Service Provider Network (GSPN/SPN) provider data and outputs the likely independent official website for each provider.

The current source file is:

```text
/Users/luojianyin/Documents/Codex/2026-05-22/https-sellercentral-europe-amazon-com-gspn/gspn_output_v3_live_20260522_192420/final_csv_with_descriptions/provider_details_final_with_field_descriptions.csv
```

## Why This Needs Scoring

The input file does not contain a website column. It contains Amazon GSPN provider metadata:

- `provider_id`
- `provider_name`
- `service_api`
- `detail_url`
- `listing_logo_url`
- `about_listing_text`
- `service_types_json`
- `provider_locations_json`
- `provider_languages_json`
- pricing and review/listing metadata

Amazon SPN providers are vetted third-party providers listed by Amazon, but Amazon states the provider listings are informational and that sellers contract directly with providers. The independent provider website must therefore be discovered outside the Amazon listing.

The workflow treats web search, GitHub, directory pages, company registries, and direct domain guesses as candidate discovery sources. The final output is selected by evidence scoring rather than raw search rank.

## Output

The main output CSV has one row per provider:

```csv
provider_id,provider_name,official_url,official_domain,confidence,status,evidence_summary,candidate_count,scored_candidate_count,service_apis,provider_locations
```

Statuses:

- `matched`: score is high enough for automatic use.
- `needs_review`: plausible candidate, but should be manually checked.
- `low_confidence`: no URL emitted because the best candidate is weak.
- `not_found`: no viable non-excluded candidate was found.

An evidence JSONL file is also written. It contains the scored candidate list and reasons for each decision.

After review, the final handoff CSV is written by `finalize-results` or by `tools/apply_review.py` for an existing pipeline run directory:

```csv
provider_id,provider_name,provider_detail_url,listing_logo_url,official_url,official_domain,status,decision_source,confidence,source_status,evidence_summary,candidate_count,scored_candidate_count,service_apis,provider_locations,notes
```

Manual review decisions use `accept`, `replace`, or `reject` in the review queue. `replace` uses `manual_url`; blank decisions leave non-matched rows unresolved.

## Setup

This project uses only the Python standard library.

```bash
cd /Users/luojianyin/Documents/官网搜索
cp .env.example .env
```

Add at least one search key to `.env` for full discovery:

```bash
SERPAPI_API_KEY=...
BRAVE_API_KEY=...
TAVILY_API_KEY=...
SERPER_API_KEY=...
FIRECRAWL_API_KEY=...
EXA_API_KEY=...
DDGS_ENABLED=0
```

The CLI automatically loads `.env` from the current working directory.

For portable handoff-oriented execution, start with [REPRODUCIBILITY_CN.md](/Users/luojianyin/Documents/官网搜索/REPRODUCIBILITY_CN.md) and [RUNBOOK_CN.md](/Users/luojianyin/Documents/官网搜索/RUNBOOK_CN.md). Common commands are also available through `make`:

```bash
make test
make install-optional
make prepare
make doctor
make preflight
make sample
make eval-sample
make finalize-demo
make rescore-ddgs-10
make quality-demo
make quality-ddgs-10
make review-demo
make pipeline-demo
make pipeline SOURCE_CSV=/path/to/input.csv RUN_DIR=outputs/new_run
make build-xlsx RUN_DIR=outputs/new_run
make second-pass RUN_DIR=outputs/new_run
```

For the simplest portable handoff on another computer, use:

```bash
./run_workflow.sh /path/to/provider_details.csv outputs/new_run
```

This command installs optional Python dependencies if needed, runs live API preflight, executes the full pipeline, runs the Brave+Exa unresolved second-pass, writes the final CSV/XLSX, and verifies the handoff files.

## Step 1: Normalize the Amazon Input

The source CSV has 2200 rows, but many rows are the same provider repeated across service categories or market combinations. Normalize it to one row per provider:

```bash
python3 -m finder.cli prepare \
  --input /Users/luojianyin/Documents/Codex/2026-05-22/https-sellercentral-europe-amazon-com-gspn/gspn_output_v3_live_20260522_192420/final_csv_with_descriptions/provider_details_final_with_field_descriptions.csv \
  --output outputs/providers_normalized.csv
```

Current result from the provided file:

```text
2200 source rows -> 1184 unique providers
```

## Step 2: Preview Search Queries

```bash
python3 -m finder.cli preview-queries \
  --input outputs/providers_normalized.csv \
  --limit 5
```

Each provider gets queries like:

```text
"247EASYSUPPORT" official website
"247EASYSUPPORT" Amazon service provider
"247EASYSUPPORT" "Account Management"
"247EASYSUPPORT" Seller Central
"247EASYSUPPORT" "India" website
247easysupport website
site:github.com "247EASYSUPPORT"
site:github.com "247EASYSUPPORT" website
```

## Step 3: Run Discovery and Scoring

Before a handoff or production run, write the readiness report:

```bash
PYTHONPATH=.vendor_eval:. python3 tools/preflight_report.py \
  --source /Users/luojianyin/Documents/Codex/2026-05-22/https-sellercentral-europe-amazon-com-gspn/gspn_output_v3_live_20260522_192420/final_csv_with_descriptions/provider_details_final_with_field_descriptions.csv \
  --run-dir outputs/production_run \
  --labels tests/fixtures/golden_expected_websites.csv \
  --soft-fail
```

It writes `outputs/production_preflight.md` and `outputs/production_preflight.json`, including configured sources, normalized provider count, missing blockers, recommended commands, quality gates, and handoff outputs. Remove `--soft-fail` in automation when a missing production search source should fail the job.

After adding production keys, run the same command with `--live-check` to verify authentication and response parsing before spending a full batch. Recommended setup is `BRAVE_API_KEY` plus optional `EXA_API_KEY`: Brave handles exact SERP recall, while Exa only runs semantic second-pass queries.

The current 1184-provider run completed the first pass with 731 matched and 453 unresolved rows. The latest Brave+Exa plus seed-verification second-pass accepts 288 additional rows, producing 1019 official-url rows and 165 unresolved rows with the quality gate passing.

End-to-end pipeline run:

```bash
PYTHONPATH=.vendor_eval:. python3 tools/run_pipeline.py \
  --source /Users/luojianyin/Documents/Codex/2026-05-22/https-sellercentral-europe-amazon-com-gspn/gspn_output_v3_live_20260522_192420/final_csv_with_descriptions/provider_details_final_with_field_descriptions.csv \
  --run-dir outputs/production_run \
  --labels tests/fixtures/golden_expected_websites.csv \
  --batch-size 100 \
  --per-query 5 \
  --max-queries 6 \
  --max-candidates 30 \
  --min-official-url-rate 0.9 \
  --max-unresolved-rate 0.1
```

The pipeline writes `manifest.json`, normalized input, raw results, evidence JSONL, review queue, final CSV, unresolved rows, and quality-gate reports into the run directory. It stops by default when no production search source is configured, or when all configured production search calls fail for a provider, to avoid domain-guess-only degradation. Use `--allow-exploratory` only for no-key smoke tests.

The pipeline also writes `provider_review_sheet_enhanced.csv`. This is the hand-review sheet: it expands the top evidence candidates, scores, sources, queries, and reasons into columns next to `manual_decision`, `manual_url`, and `notes`. It re-applies the current excluded-domain rules before ranking saved evidence candidates.

Small test run:

```bash
python3 -m finder.cli run \
  --input outputs/providers_normalized.csv \
  --output outputs/sample_results.csv \
  --evidence evidence/sample_evidence.jsonl \
  --limit 20 \
  --per-query 5 \
  --max-candidates 30
```

Full run:

```bash
python3 -m finder.cli run \
  --input outputs/providers_normalized.csv \
  --output outputs/provider_official_websites.csv \
  --evidence evidence/provider_official_websites_evidence.jsonl
```

Batch/resume run for long jobs:

```bash
python3 -m finder.cli run \
  --input outputs/providers_normalized.csv \
  --output outputs/provider_official_websites.csv \
  --evidence evidence/provider_official_websites_evidence.jsonl \
  --offset 0 \
  --limit 100 \
  --per-query 5 \
  --max-candidates 30

python3 -m finder.cli run \
  --input outputs/providers_normalized.csv \
  --output outputs/provider_official_websites.csv \
  --evidence evidence/provider_official_websites_evidence.jsonl \
  --offset 0 \
  --limit 200 \
  --per-query 5 \
  --resume \
  --max-candidates 30
```

Without API keys, the workflow only uses direct domain guesses such as `providername.com`; this is useful for smoke tests but not enough for production coverage.

For no-key exploratory testing, install optional tools and enable DDGS:

```bash
python3 -m pip install --target .vendor_eval -r requirements-optional.txt
DDGS_ENABLED=1 FINDER_HTTP_TIMEOUT=8 PYTHONPATH=.vendor_eval:. python3 -m finder.cli run \
  --input outputs/providers_normalized.csv \
  --output outputs/sample_ddgs_results.csv \
  --evidence evidence/sample_ddgs_evidence.jsonl \
  --limit 20 \
  --per-query 3 \
  --max-candidates 20
```

For JS-only sites, Playwright is available as an optional dynamic-rendering fallback. It is disabled by default in `config/scoring.json` because it is slower than normal HTTP extraction. To enable it for targeted production runs, install browser binaries and set `dynamic_rendering.enabled` to `true`:

```bash
python3 -m playwright install chromium
```

Build a portable clickable XLSX from any completed run directory:

```bash
python3 tools/build_linked_workbook.py \
  --sheet Final=outputs/new_run/provider_final_official_websites.csv \
  --sheet Auto_Results=outputs/new_run/provider_official_websites_enriched.csv \
  --sheet Review_Queue=outputs/new_run/provider_review_sheet_enhanced.csv \
  --output outputs/new_run/provider_official_websites_final_with_clickable_links.xlsx
```

Run unresolved second-pass on any completed run directory:

```bash
python3 tools/run_unresolved_second_pass.py \
  --run-dir outputs/new_run \
  --labels tests/fixtures/golden_expected_websites.csv \
  --per-query 3 \
  --max-search-queries 6 \
  --accept-threshold 70 \
  --write-xlsx
```

Current second-pass strategy:

- Brave runs targeted exact queries: official website, website excluding social/Amazon, contact, service, location, and GitHub site-query.
- Exa runs only semantic follow-up queries, such as “official company website for {name} Amazon seller service provider {location}”.
- Unaccepted rows then run a no-API seed-verification rescue over existing top candidates, provider-input URLs, and conservative brand-domain variants.
- `>=85` scores auto-accept unless the URL is risky; `70-84` only auto-accepts with strong page evidence or multiple supporting sources; `50-69` can auto-accept only when the candidate has domain identity plus page/search/service evidence; weaker rows stay review/unresolved.
- Parked/domain-sale/directory/social/login/password/suspended URLs are excluded or blocked from auto-accept.

Verify a completed run before handoff:

```bash
python3 tools/verify_run_outputs.py \
  --run-dir outputs/new_run \
  --expected-rows <normalized_provider_count> \
  --xlsx outputs/new_run/provider_official_websites_final_with_clickable_links.xlsx
```

## Test

```bash
python3 -m unittest discover -s tests
python3 -m compileall finder tests tools
```

Current result:

```text
Ran 54 tests
OK
```

The current smoke-test output for the provided file is:

```text
247EASYSUPPORT -> https://www.247easysupport.com/ -> matched, 84
247Tasker -> https://www.247tasker.com/ -> needs_review, 64
5starsecommerce -> https://5starsecommerce.com/ -> matched, 79
9THSIGHT PRIVATE LIMITED -> https://9thsight.com/ -> matched, 84
A2Z-ECOM -> low_confidence, 35
```

Operational checks:

```bash
python3 -m finder.cli doctor --input outputs/providers_normalized.csv
python3 -m finder.cli audit-results \
  --input outputs/sample_results.csv \
  --review-output outputs/review_queue.csv

python3 -m finder.cli finalize-results \
  --input outputs/sample_results.csv \
  --review tests/fixtures/manual_review_demo.csv \
  --output outputs/final_demo_official_websites.csv \
  --unresolved-output outputs/final_demo_unresolved.csv

python3 tools/apply_review.py \
  --run-dir outputs/pipeline_demo \
  --review tests/fixtures/manual_review_demo.csv \
  --labels tests/fixtures/golden_expected_websites.csv \
  --min-domain-accuracy 1.0 \
  --min-auto-precision 1.0 \
  --min-official-url-rate 1.0 \
  --max-unresolved-rate 0.0

python3 tools/evaluate_labeled_results.py \
  --labels tests/fixtures/golden_expected_websites.csv \
  --results outputs/final_demo_official_websites.csv \
  --output-md outputs/labeled_eval_final_demo.md \
  --output-json outputs/labeled_eval_final_demo.json
```

Current labeled baseline evaluation:

```text
5 labeled providers
4 domain matches
0.8 domain accuracy
1.0 auto-match precision
```

Current finalized demo evaluation after manual review decisions:

```text
5 labeled providers
5 domain matches
1.0 domain accuracy
1.0 auto-match precision
```

After changing scoring rules or excluded domains, saved evidence can be rescored without re-running web search. Saved candidate scores are reused when present, and older evidence without scores falls back to the normal scoring path:

```bash
python3 tools/rescore_evidence.py \
  --providers outputs/providers_normalized.csv \
  --evidence evidence/ddgs_10_capped_evidence.jsonl \
  --output outputs/ddgs_10_capped_rescored.csv
```

Run the quality gate before handoff:

```bash
python3 tools/quality_gate.py \
  --results outputs/final_demo_official_websites.csv \
  --labels tests/fixtures/golden_expected_websites.csv \
  --expected-rows 5 \
  --min-domain-accuracy 1.0 \
  --min-auto-precision 1.0 \
  --min-official-url-rate 1.0 \
  --max-unresolved-rate 0.0 \
  --output-md outputs/quality_gate_final_demo.md \
  --output-json outputs/quality_gate_final_demo.json
```

Current quality gate reports:

- [quality_gate_final_demo.md](/Users/luojianyin/Documents/官网搜索/outputs/quality_gate_final_demo.md)
- [quality_gate_ddgs_10_capped_rescored.md](/Users/luojianyin/Documents/官网搜索/outputs/quality_gate_ddgs_10_capped_rescored.md)

## Candidate Sources

The workflow supports these sources:

- SerpAPI Google Search: general web discovery and `site:github.com` queries.
- Brave Search API: independent web index discovery.
- Exa Search API: semantic second-pass discovery with optional returned text/highlights.
- Tavily Search API: LLM-oriented web search snippets.
- Serper Google Search API: lower-cost Google SERP candidate source.
- Firecrawl Search: search plus optional scrape-oriented results.
- DDGS: no-key exploratory search when optional dependencies are installed.
- GitHub information is included through `site:github.com` search queries; the workflow does not require the GitHub API.
- Direct domain guesses: deterministic fallback from provider name.
- Candidate page crawling: homepage plus common pages such as `/about`, `/contact`, `/services`, `/privacy`, `/terms`.

See [TOOL_EVALUATION_CN.md](/Users/luojianyin/Documents/官网搜索/TOOL_EVALUATION_CN.md) for the tool research and local trials.

The generated local evaluation summary is [tool_eval_summary.md](/Users/luojianyin/Documents/官网搜索/outputs/tool_eval_summary.md).

The labeled baseline evaluation is [labeled_eval_sample_results.md](/Users/luojianyin/Documents/官网搜索/outputs/labeled_eval_sample_results.md).

The capped DDGS 10-row rescored evaluation is [labeled_eval_ddgs_10_capped_rescored.md](/Users/luojianyin/Documents/官网搜索/outputs/labeled_eval_ddgs_10_capped_rescored.md).

The end-to-end demo manifest is [manifest.json](/Users/luojianyin/Documents/官网搜索/outputs/pipeline_demo/manifest.json).

The enhanced review sheet demo is [provider_review_sheet_enhanced.csv](/Users/luojianyin/Documents/官网搜索/outputs/pipeline_demo/provider_review_sheet_enhanced.csv).

## Scoring Rules

The scoring file is `config/scoring.json`.

Strong positive signals:

- Provider name matches domain.
- Provider name appears in page title/body.
- Optional RapidFuzz fuzzy matching improves legal-suffix/name/domain similarity scoring when installed.
- Optional Trafilatura extraction improves static website body extraction when installed.
- Optional Playwright dynamic rendering can rescue JS-only candidates when enabled.
- Page mentions Amazon SPN, Seller Central, marketplace services, FBA, PPC, compliance, cataloging, or related services.
- Provider location appears on the page.
- Search result is high rank for an official-site query.

Rejected or low-value domains include:

- Amazon/Seller Central/media Amazon domains.
- Social networks.
- Marketplaces and freelance platforms.
- Company registries and directory/review sites.
- Scam/reputation pages.

Those pages can still be useful as evidence sources, but they should not be emitted as the independent official website.

## Recommended Review Process

Use `matched` rows directly only after sampling for quality. For `needs_review`, open the evidence JSONL and check:

1. Is the selected domain controlled by the provider?
2. Does the site mention the exact provider name or legal entity?
3. Does it offer the same Amazon/SPN service category?
4. Is it merely a profile page on another platform?

For production, keep the evidence JSONL. It is the audit trail for why a website was selected.
