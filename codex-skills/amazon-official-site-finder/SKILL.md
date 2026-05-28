---
name: amazon-official-site-finder
description: Use when a user wants Codex to run the Amazon GSPN/SPN provider official-website finder end to end from a same-format provider CSV, including using local Brave/Exa API key files, creating .env, cloning or locating the repo, running the workflow, verifying outputs, troubleshooting, unresolved review, and sampling.
---

# Amazon Official Site Finder

Use this skill to operate the `amazon-official-site-finder` workflow. The workflow takes an Amazon GSPN/SPN provider CSV and outputs each provider's independent official website, evidence, unresolved rows, and a clickable XLSX.

## Default User Experience

When the user has Codex and already placed two API key files plus the input CSV on their computer, they should only need to tell Codex:

```text
Use amazon-official-site-finder.
Brave key file: /path/to/brave_key.txt
Exa key file: /path/to/exa_key.txt
Input CSV: /path/to/provider_details.csv
Output directory: outputs/my_run
```

Then Codex should do the rest: locate or clone the repo, create/update `.env` from the key files, run the workflow, verify outputs, and report final absolute paths.

Never print API key contents. Avoid showing `.env` values. If a key file path is missing or unreadable, ask only for the missing path.

## Required Inputs

- Same-format provider CSV.
- Brave Search API key file path. This is required for production search.
- Exa API key file path. This is strongly recommended and should be used for second-pass recall.
- Optional output directory. If absent, use `outputs/codex_run_YYYYMMDD_HHMMSS`.

Key files may contain a plain key, `BRAVE_API_KEY=...`, `EXA_API_KEY=...`, or JSON with `api_key`/`key` fields.

## Repo Resolution

1. If the current working directory contains `run_workflow.sh`, use it.
2. Else, if `$AMAZON_OFFICIAL_SITE_FINDER_REPO` points to a directory with `run_workflow.sh`, use it.
3. Else, search common local folders for `amazon-official-site-finder/run_workflow.sh`.
4. Else, clone the repo into a user-writable folder:

```bash
git clone https://github.com/lawsonlyj/amazon-official-site-finder.git ~/amazon-official-site-finder
```

If clone fails because the repo is private or GitHub auth is missing, tell the user to grant this computer access to `lawsonlyj/amazon-official-site-finder`, then retry.

## End-to-End Command Sequence

Run this from the repo root. Substitute the paths provided by the user.

```bash
./run_codex_assisted.sh \
  --brave-key-file "/path/to/brave_key.txt" \
  --exa-key-file "/path/to/exa_key.txt" \
  --source "/path/to/provider_details.csv" \
  --run-dir "outputs/my_run"
```

This command creates/updates `.env` and then runs the full workflow. The configure step prints only a boolean summary and must not print secrets.
If the user did not provide an output directory, omit `--run-dir`; the script will create `outputs/codex_run_YYYYMMDD_HHMMSS`.

If you need to run the two steps separately:

```bash
python3 tools/configure_env_from_key_files.py \
  --brave-key-file "/path/to/brave_key.txt" \
  --exa-key-file "/path/to/exa_key.txt" \
  --env .env

./run_workflow.sh "/path/to/provider_details.csv" "outputs/my_run"
```

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

`run_workflow.sh` already verifies the final CSV, unresolved CSV, quality JSON, and clickable XLSX. If you need to re-check manually, run:

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

## Final Response

Report:

- The final CSV absolute path.
- The clickable XLSX absolute path.
- The unresolved CSV absolute path and count if available.
- Whether quality verification passed.
- Any blocker, without exposing keys.

## Review Guidance

For precision, prioritize rows whose `status=manual_accepted` and `confidence < 70`.

For recall, inspect `provider_unresolved_second_pass.csv` and `unresolved_second_pass_results.csv`; focus on unresolved rows with a non-empty candidate URL and confidence near 50-69.

Accepted official URLs must not be Amazon/Seller Central, social/video platforms, directories, parked/domain-sale pages, login/app/staging/suspended pages, or marketplace profiles.

## Troubleshooting

- `HTTP 402`: Brave quota/payment issue. Refill quota and rerun with same `RUN_DIR`; resume is enabled by default.
- Missing production source: fill `.env` with `BRAVE_API_KEY` and preferably `EXA_API_KEY`.
- Key file parsing failed: confirm the user gave the key file itself, not a folder or screenshot. The file can be plain text, env style, or JSON.
- Slow second pass: keep Exa semantic query count low and use seed verification rather than broad Exa over every ordinary query.
- Risky accepted URL: add the domain/path marker to `config/scoring.json` or `_risky_auto_accept_url`, add a test, rerun second pass.
