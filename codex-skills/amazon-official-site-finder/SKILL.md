---
name: amazon-official-site-finder
description: Use when a user wants Codex to run the Amazon GSPN/SPN provider official-website finder end to end from a same-format provider CSV, including using local Brave/Exa API key files, creating .env, cloning or locating the repo, running the workflow, verifying outputs, creating manual review tasks, applying filled review feedback, troubleshooting, unresolved review, and sampling.
---

# Amazon Official Site Finder

Use this skill to operate the `amazon-official-site-finder` workflow. The workflow takes an Amazon GSPN/SPN provider CSV and outputs each provider's independent official website, evidence, unresolved rows, and a clickable XLSX.

## Workflow Terminology

Separate two modes in user-facing responses:

- **Workflow Body**: normal reusable workflow for GitHub users. It runs structured search, scoring, second-pass, review task generation, output verification, and reviewed output generation. This is the default.
- **Development Workflow**: maintainer workflow for calibration and improvement. It is `Operation and Optimization -> CheckAgent -> human review -> OptimizationAgent -> deterministic gate -> Operation and Optimization`. CheckAgent and OptimizationAgent are development-stage agent roles; the normal Workflow Body remains structured and deterministic.

Legacy script names still include `agent_b`, `agent_c`, and `agent_optimizations` for compatibility. Treat `agent_c` as a legacy wrapper for suggestion behavior, not as a current standalone role. Do not describe the normal Workflow Body as autonomous multi-agent execution. Only use Development Workflow tools when the user explicitly asks to optimize, calibrate, compare rules, run CheckAgent/Check and Suggestion, or work on maintainer/development flow.

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

Then Codex should apply the feedback, verify the reviewed outputs, and report the final reviewed files. Do not ask the user to run `run_review_cycle.sh` themselves.

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
New runs write short canonical filenames by default; legacy names and flags are read as fallback and can be written with `FINDER_WRITE_LEGACY_ALIASES=1`.

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

This runs explicit provider deduplication, preflight, first pass, second pass, simplified manual review task generation, XLSX generation, and verification. Duplicate service rows for the same `provider_id` are merged before search starts.

## Expected Outputs

```text
outputs/my_run/official_sites.csv
outputs/my_run/official_sites.xlsx
outputs/my_run/unresolved.csv
outputs/my_run/quality.json
outputs/my_run/review_task.csv
outputs/my_run/review_task.xlsx
outputs/my_run/manifest.json
outputs/my_run/details/input/deduped_input.csv
outputs/my_run/details/input/deduped_input.xlsx
outputs/my_run/details/input/dedupe_report.md
```

Report these files with absolute paths. Legacy public filenames are not written by default; old files such as `provider_final_official_websites_second_pass.csv`, `provider_official_websites_second_pass_with_clickable_links.xlsx`, and `manual_official_site_review_task.xlsx` are still accepted as fallback inputs, and can be written for external compatibility by setting `FINDER_WRITE_LEGACY_ALIASES=1`.

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
  "/path/to/filled_manual_review_task.xlsx"
```

This calls `tools/run_review_learning.py`, which merges the filled manual decisions with existing second-pass decisions, writes reviewed final outputs, creates `reviewed/labels.csv`, reruns the quality gate, and writes `reviewed/learning.md`.

## Development Workflow

Use the development workflow only when the user asks to optimize, calibrate, compare rules, run Check and Suggestion, or continue workflow development. The normal GitHub user path should not include these steps.

The development loop is:

```text
Operation and Optimization
  -> rules-based main workflow: search, score, second-pass, official-site output, review task
CheckAgent
  -> real agent role for high-risk rows only: accept / reject / replace / unsure, evidence, counter-evidence, reasons, suggestions
Human review
  -> small high-value labels to calibrate CheckAgent and scoring rules
OptimizationAgent
  -> real agent role that reads CheckAgent suggestions, human labels, and metric reports, then decides whether a change is useful or needs more labels/simulation/tests
Deterministic gate
  -> fixed tests, metrics, and regression checks; only passing changes may be applied
Operation and Optimization
  -> absorb safe rules or regression fixtures and rerun the workflow
```

When using this skill as Codex, keep Workflow Body outputs and Development Workflow outputs separate in the final report. Do not let CheckAgent or OptimizationAgent directly overwrite production results or config; changes must pass the deterministic gate first.

Development command:

```bash
./run_codex_assisted.sh \
  --brave-key-file "/path/to/brave_key.txt" \
  --exa-key-file "/path/to/exa_key.txt" \
  --source "/path/to/provider_details.csv" \
  --run-dir "outputs/dev_run" \
  --run-check-suggestion
```

CheckAgent / Check and Suggestion checks high-risk rows, writes evidence plus suggestions, and uses structured DOM evidence from candidate pages. Add `--human-review /path/to/filled_review.xlsx` to use filled human labels as regression evidence. Add `--apply-operation-optimizations` only when Operation and Optimization should apply safe recommendations and write regression artifacts after the gate passes.

For repository developers working on the skill/workflow itself, real LLM agents are explicit opt-in and require `OPENAI_API_KEY` in `.env`:

```bash
./run_workflow.sh "/path/to/provider_details.csv" "outputs/dev_run" \
  --run-check-agent \
  --run-optimization-agent \
  --application-gates-json "outputs/dev_run/calibration_cycle/calibration_application_gates.json" \
  --development-cycle 1
```

Real CheckAgent writes `development/check_agent/*`; real OptimizationAgent writes `development/optimization_agent/*`; cycle metrics write `development/cycle_N/*`. These scripts fail closed when the OpenAI key/API is unavailable and must not overwrite Workflow Body outputs.

For large Check and Suggestion runs, use `python3 tools/run_agent_b_verification.py --run-dir outputs/dev_run --resume --write-xlsx`. For batch validation, add `--row-timeout 15 --per-query 1`.

For calibration work, follow `docs/DEVELOPMENT_WORKFLOW_CN.md`. Treat CheckAgent and OptimizationAgent conclusions as advisory until Operation and Optimization adds regression tests and a deterministic gate passes.

Expected reviewed outputs:

```text
outputs/my_run/reviewed/official_sites.csv
outputs/my_run/reviewed/official_sites.xlsx
outputs/my_run/reviewed/unresolved.csv
outputs/my_run/reviewed/learning.md
outputs/my_run/reviewed/labels.csv
```

After running review learning, inspect `reviewed/learning.md` and `reviewed/learning.json`. Only make workflow/config changes when the report shows repeated safe patterns, such as repeated rejected directory/platform domains. Then run tests and rerun the relevant workflow step.

Codex follow-up checklist after a filled review file:

1. Run `./run_review_cycle.sh "$RUN_DIR" "$FILLED_REVIEW"`.
2. Read `reviewed/learning.json` and `reviewed/learning.md`.
3. If the user explicitly asked for development optimization, rerun with `--update-config`, report any added excluded domains, and run `PYTHONPATH=.vendor_eval:. python3 -m unittest discover -s tests`.
4. Verify reviewed outputs with `tools/verify_run_outputs.py` if the shell script did not complete verification.
5. Final response must list the reviewed final CSV, reviewed clickable XLSX, reviewed unresolved CSV, learning report, manual labels, quality status, and any config optimization applied.

## Review Guidance

For precision, prioritize rows whose `status=manual_accepted` and `confidence < 70`.

For recall, inspect `unresolved.csv` and `details/second_pass/results.csv`; focus on unresolved rows with a non-empty candidate URL and confidence near 50-74. Legacy files such as `provider_unresolved_second_pass.csv` and `unresolved_second_pass_results.csv` are still readable when opening older run directories.

Accepted official URLs must not be Amazon/Seller Central, social/video platforms, directories, parked/domain-sale pages, login/app/staging/suspended pages, or marketplace profiles.

## Troubleshooting

- `HTTP 402`: Brave quota/payment issue. Refill quota and rerun with same `RUN_DIR`; resume is enabled by default.
- Missing production source: fill `.env` with `BRAVE_API_KEY` and preferably `EXA_API_KEY`.
- Key file parsing failed: confirm the user gave the key file itself, not a folder or screenshot. The file can be plain text, env style, or JSON.
- Slow second pass: keep Exa semantic query count low and use seed verification rather than broad Exa over every ordinary query.
- Risky accepted URL: add the domain/path marker to `config/scoring.json` or `_risky_auto_accept_url`, add a test, rerun second pass.
