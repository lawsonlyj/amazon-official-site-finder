---
name: amazon-official-site-finder
description: Use when a user wants to run, troubleshoot, audit, or explain the Amazon GSPN/SPN provider official-website finder workflow from a same-format provider CSV, including Brave/Exa API setup, output verification, unresolved review, and sampling.
---

# Amazon Official Site Finder

Use this skill to operate the local `amazon-official-site-finder` repo. The workflow takes an Amazon GSPN/SPN provider CSV and outputs each provider's independent official website, evidence, unresolved rows, and a clickable XLSX.

## Required Inputs

- Same-format provider CSV.
- Repo path, defaulting to the current working directory if it contains `run_workflow.sh`.
- `.env` with `BRAVE_API_KEY`; recommend `EXA_API_KEY` for second-pass recall.

Do not print API keys. If `.env` is missing, copy `.env.example` and tell the user to fill keys.

## Main Command

```bash
./run_workflow.sh "/path/to/provider_details.csv" "outputs/my_run"
```

This runs preflight, first pass, second pass, XLSX generation, and verification.

## Expected Outputs

```text
outputs/my_run/provider_final_official_websites_second_pass.csv
outputs/my_run/provider_official_websites_second_pass_with_clickable_links.xlsx
outputs/my_run/provider_unresolved_second_pass.csv
outputs/my_run/unresolved_second_pass_results.csv
outputs/my_run/unresolved_second_pass_evidence.jsonl
outputs/my_run/quality_gate_provider_second_pass_final.json
outputs/my_run/manifest.json
```

Report these files with absolute paths.

## Verification

Run:

```bash
python3 tools/verify_run_outputs.py \
  --run-dir "outputs/my_run" \
  --final provider_final_official_websites_second_pass.csv \
  --unresolved provider_unresolved_second_pass.csv \
  --quality quality_gate_provider_second_pass_final.json \
  --xlsx "outputs/my_run/provider_official_websites_second_pass_with_clickable_links.xlsx"
```

Also run unit tests after code or scoring changes:

```bash
PYTHONPATH=.vendor_eval:. python3 -m unittest discover -s tests
```

## Review Guidance

For precision, prioritize rows whose `status=manual_accepted` and `confidence < 70`.

For recall, inspect `provider_unresolved_second_pass.csv` and `unresolved_second_pass_results.csv`; focus on unresolved rows with a non-empty candidate URL and confidence near 50-69.

Accepted official URLs must not be Amazon/Seller Central, social/video platforms, directories, parked/domain-sale pages, login/app/staging/suspended pages, or marketplace profiles.

## Troubleshooting

- `HTTP 402`: Brave quota/payment issue. Refill quota and rerun with same `RUN_DIR`; resume is enabled by default.
- Missing production source: fill `.env` with `BRAVE_API_KEY` and preferably `EXA_API_KEY`.
- Slow second pass: keep Exa semantic query count low and use seed verification rather than broad Exa over every ordinary query.
- Risky accepted URL: add the domain/path marker to `config/scoring.json` or `_risky_auto_accept_url`, add a test, rerun second pass.
