---
name: amazon-official-site-finder
description: Use when a user wants Codex to run the Amazon GSPN/SPN provider official-website finder end to end from a same-format provider CSV, including using local Brave/Exa API key files, creating .env, cloning or locating the repo, running the workflow, verifying outputs, creating manual review tasks, applying filled review feedback, troubleshooting, unresolved review, and sampling.
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

Then Codex should do the rest: locate or clone the repo, create/update `.env` from the key files, run the workflow, verify outputs, create the simplified manual review workbook, and report final absolute paths.

When the user later provides a filled manual review workbook, they should only need to tell Codex:

```text
Use amazon-official-site-finder.
Run directory: outputs/my_run
Filled review file: /path/to/review_task.xlsx
```

Then Codex should apply the feedback, run safe workflow optimization from the learning report, verify the reviewed outputs, and report the final reviewed files. Do not ask the user to run `run_review_cycle.sh` themselves.

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
  --run-dir "outputs/my_run" \
  --run-agent-b
```

This command creates/updates `.env` and then runs the full workflow. The configure step prints only a boolean summary and must not print secrets.
If the user did not provide an output directory, omit `--run-dir`; the script will create `outputs/codex_run_YYYYMMDD_HHMMSS`.
Use `--run-agent-b` when the user asks for the AgentB optimization loop or candidate-first verification. B checks only high-risk rows by default, writes verification evidence plus suggestions, and the scorer uses country/language search hints, country/location corroboration, service consistency, and high-similarity Amazon listing logo matches as positive identity evidence. Logo-only evidence is review risk, not an automatic accept. Ambiguous-name candidates can still auto-accept when page-level provider identity and marketplace/service evidence agree, which avoids excessive unresolved rows; however specific high-confidence `consulting`/`seller` style names and slug-extended domains without exact logo corroboration are still sent to B/manual review, and AgentB keeps 70-84 score rows in those lanes as `unsure`. High-confidence second-pass accepts at 85+ are not included in the default review task. For rows coming from `review_task`, B keeps recall and replacement candidates as evidence but does not auto-fill a `replace` decision. `--human-review /path/to/filled_review.xlsx` lets B use filled human review notes, including no-official labels, as regression evidence. Add `--apply-agent-optimizations` when A should apply only safe B recommendations and write regression artifacts. Legacy public filenames are still generated.

For large AgentB checks, use `python3 tools/run_agent_b_verification.py --run-dir outputs/my_run --resume --write-xlsx` so progress is written incrementally and interrupted runs can continue. For batch validation, add `--row-timeout 15 --per-query 1` to mark slow rows `unsure` rather than letting one site block the whole run.

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

This runs preflight, first pass, second pass, simplified manual review task generation, XLSX generation, and verification.

## Expected Outputs

```text
outputs/my_run/official_sites.csv
outputs/my_run/official_sites.xlsx
outputs/my_run/unresolved.csv
outputs/my_run/quality.json
outputs/my_run/review_task.csv
outputs/my_run/review_task.xlsx
outputs/my_run/agent_b/check.csv
outputs/my_run/agent_b/check.xlsx
outputs/my_run/agent_b/suggestions.md
outputs/my_run/manifest.json
```

Report these files with absolute paths. Legacy public filenames are still generated for compatibility, including `provider_final_official_websites_second_pass.csv`, `provider_official_websites_second_pass_with_clickable_links.xlsx`, and `manual_official_site_review_task.xlsx`.

## Verification

`run_workflow.sh` already verifies the final CSV, unresolved CSV, quality JSON, and clickable XLSX. If you need to re-check manually, run:

```bash
python3 tools/verify_run_outputs.py \
  --run-dir "outputs/my_run" \
  --final official_sites.csv \
  --unresolved unresolved.csv \
  --quality quality.json \
  --xlsx "outputs/my_run/official_sites.xlsx"
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
- The manual review task XLSX absolute path.
- Whether quality verification passed.
- Any blocker, without exposing keys.

## Manual Review Task

The workflow now creates a simplified worker-facing review workbook:

```text
outputs/my_run/review_task.xlsx
```

Tell the user to fill only:

- `manual_decision`: `accept`, `replace`, `reject`, or `unsure`.
- `manual_url`: required for `replace`; optional for `accept` when the shown `official_url` is already correct.
- `notes`: optional short reason.

After the user fills the workbook, they should hand the file path back to Codex. They do not need to run shell commands.

## Review Learning Loop

When the user provides a filled review file, Codex should run this from the repo root:

```bash
./run_review_cycle.sh \
  "outputs/my_run" \
  "/path/to/filled_manual_review_task.xlsx" \
  --update-config
```

This calls `tools/run_review_learning.py`, which merges the filled manual decisions with existing second-pass decisions, writes reviewed final outputs, creates `reviewed/labels.csv`, reruns the quality gate, writes `reviewed/learning.md`, and applies only safe repeated excluded-domain config additions.
It also writes AgentB suggestions and, with `--update-config`, applies only safe AgentB excluded-domain recommendations plus human/identity/no-official/reachability regression artifacts.

For calibration work, run `tools/evaluate_workflow_balance.py` against the baseline final CSV, candidate final CSV, and filled yellow-row review workbook to compare false official URLs, over-rejected correct sites, precision, recall, unresolved rows, manual-review workload, and AgentB unresolved recall auto-release risk before deciding whether a threshold/rule change is better. Then run `tools/build_balance_report.py` with the labeled balance JSON plus larger unlabeled review/AgentB batches to create a repeatable recommendation report. Use `tools/run_calibration_cycle.py` to generate the next calibration package in one command; after the sample is filled, rerun with `--filled-sample` to summarize pattern accept/reject recommendations and write `pattern_rule_candidates.json/md`. Treat `candidate_for_rule` items as advisory until A adds regression tests; keep `needs_more_labels` patterns in calibration samples; do not auto-release `reject_pattern` rows. If the user asks whether any narrower rule can be safely relaxed, run `tools/mine_evidence_patterns.py`; treat zero-error evidence combinations as validation candidates until more human labels confirm them.

When more labels are needed, run `tools/build_calibration_review_sample.py` with the batch `review_task.csv` and `agent_b/check.csv` to produce a small high-value XLSX. Add `--pattern-json` from `tools/mine_evidence_patterns.py` when the next review should validate narrow candidate rules, and set `--max-per-pattern` to keep labels balanced across candidate patterns. Ask the worker to fill `manual_decision`, `manual_url`, and `notes`; then run `tools/evaluate_calibration_review_sample.py` on the filled CSV/XLSX. Use its lane-level, `pattern_match`, and `pattern_rule_candidates` recommendations before changing thresholds or widening/narrowing review lanes.

Expected reviewed outputs:

```text
outputs/my_run/reviewed/official_sites.csv
outputs/my_run/reviewed/official_sites.xlsx
outputs/my_run/reviewed/unresolved.csv
outputs/my_run/reviewed/learning.md
outputs/my_run/reviewed/labels.csv
outputs/my_run/agent_b/suggestions.md
outputs/my_run/agent_a/applied.json
```

After running review learning, inspect `reviewed/learning.md` and `reviewed/learning.json`. Only make workflow/config changes when the report shows repeated safe patterns, such as repeated rejected directory/platform domains. Then run tests and rerun the relevant workflow step.

Codex follow-up checklist after a filled review file:

1. Run `./run_review_cycle.sh "$RUN_DIR" "$FILLED_REVIEW" --update-config`.
2. Read `reviewed/learning.json` and `reviewed/learning.md`.
3. If `config_update.updated=true`, report the added excluded domains and run `PYTHONPATH=.vendor_eval:. python3 -m unittest discover -s tests`.
4. Verify reviewed outputs with `tools/verify_run_outputs.py` if the shell script did not complete verification.
5. Final response must list the reviewed final CSV, reviewed clickable XLSX, reviewed unresolved CSV, learning report, manual labels, quality status, and any config optimization applied.

## Review Guidance

For precision, prioritize rows whose `status=manual_accepted` and `confidence < 70`.

For recall, inspect `unresolved.csv` and `details/second_pass/results.csv`; focus on unresolved rows with a non-empty candidate URL and confidence near 50-74. Legacy files `provider_unresolved_second_pass.csv` and `unresolved_second_pass_results.csv` are also generated.

Accepted official URLs must not be Amazon/Seller Central, social/video platforms, directories, parked/domain-sale pages, login/app/staging/suspended pages, or marketplace profiles.

## Troubleshooting

- `HTTP 402`: Brave quota/payment issue. Refill quota and rerun with same `RUN_DIR`; resume is enabled by default.
- Missing production source: fill `.env` with `BRAVE_API_KEY` and preferably `EXA_API_KEY`.
- Key file parsing failed: confirm the user gave the key file itself, not a folder or screenshot. The file can be plain text, env style, or JSON.
- Slow second pass: keep Exa semantic query count low and use seed verification rather than broad Exa over every ordinary query.
- Risky accepted URL: add the domain/path marker to `config/scoring.json` or `_risky_auto_accept_url`, add a test, rerun second pass.
