# Amazon Official Site Finder

This project takes an Amazon GSPN/SPN provider CSV and finds each provider's independent official website. It produces a final CSV, a clickable XLSX, unresolved rows, evidence, a quality report, and a manual-review learning loop.

## Quick Start

Use the normal script when you already have `.env` configured:

```bash
cp .env.example .env
# Fill BRAVE_API_KEY and preferably EXA_API_KEY in .env
./run_workflow.sh "/path/to/provider_details.csv" "outputs/my_run"
```

Use the Codex-assisted script when API keys are stored in local key files:

```bash
./run_codex_assisted.sh \
  --brave-key-file "/path/to/brave_key.txt" \
  --exa-key-file "/path/to/exa_key.txt" \
  --source "/path/to/provider_details.csv" \
  --run-dir "outputs/my_run" \
  --run-agent-b
```

`--run-agent-b` is optional. It adds the `agent-loop-v2` B step after the standard second-pass outputs are verified: candidate-first verification plus optimization recommendations. Add `--apply-agent-optimizations` only when A should apply safe B recommendations such as repeated excluded-domain additions.

If using Codex, ask:

```text
Use amazon-official-site-finder skill.
Brave key file: /path/to/brave_key.txt
Exa key file: /path/to/exa_key.txt
Input CSV: /path/to/provider_details.csv
Output directory: outputs/my_run
Please configure, run, verify, and report the final output files. Do not print API keys.
```

After the run, review the simplified task workbook:

```text
outputs/my_run/manual_official_site_review_task.xlsx
```

Fill only `manual_decision`, `manual_url`, and `notes`. After that, hand the filled workbook back to Codex. The worker does not need to run another shell command.

In Codex, say:

```text
Use amazon-official-site-finder skill.
Run directory: outputs/my_run
Filled review file: outputs/my_run/manual_official_site_review_task.xlsx
Please apply the review feedback, optimize the workflow where the learning report shows safe repeated patterns, verify everything, and report the final output files.
```

## What Runs, In Order

For a normal worker, the easiest path is one command:

```bash
./run_workflow.sh "/path/to/provider_details.csv" "outputs/my_run"
```

That command runs the workflow in this order:

1. `run_workflow.sh`
   Checks `.env`, installs optional Python dependencies into `.vendor_eval` if needed, then calls preflight, pipeline, and output verification.

2. `tools/preflight_report.py`
   Confirms the input CSV exists, API keys are configured, optional dependencies are available, and live search APIs respond.

3. `tools/run_pipeline.py`
   Orchestrates the full provider workflow: normalize input, search candidates, score official websites, build review rows, finalize outputs, run quality gates, and trigger second-pass.

4. `finder/`
   Core library used by the pipeline. It normalizes Amazon rows, builds search queries, calls search sources, fetches pages, scores candidates, and finalizes rows.

5. `tools/run_unresolved_second_pass.py`
   Runs the Brave/Exa second-pass on unresolved providers and accepts only candidates meeting the configured threshold and identity checks.

6. `tools/build_manual_review_task.py`
   Builds the simplified manual review CSV/XLSX. This is the worker-facing task file for checking uncertain official websites and unresolved top candidates.

7. `tools/build_linked_workbook.py`
   Creates the clickable XLSX workbook so URLs can be opened directly in spreadsheet software.

8. `tools/verify_run_outputs.py`
   Verifies the final CSV, unresolved CSV, quality JSON, and XLSX hyperlink formulas.

9. Optional B loop
   `tools/run_agent_b_verification.py` re-checks the existing official/top-candidate URL first, inspects company pages and lightweight independent search corroboration, and writes structured `accept`/`replace`/`reject`/`unsure` evidence while preserving `manual_decision`, `manual_url`, and `notes`. `tools/run_agent_c_recommendations.py` is now the recommendation half of B, and `tools/apply_agent_optimizations.py` is A's safe apply step.

For Codex-assisted usage, `run_codex_assisted.sh` runs first. It calls `tools/configure_env_from_key_files.py` to create `.env` from local key files without printing secrets, then hands off to `run_workflow.sh`.

After manual review, the user gives the filled workbook to Codex. Codex calls `run_review_cycle.sh` internally, which runs `tools/run_review_learning.py`, merges the filled review with the second-pass decisions, writes reviewed final outputs, creates manual labels, runs the quality gate again, and writes a learning report. In Codex mode, Codex also enables safe config optimization for repeated rejected directory/platform patterns and reports whether anything changed.

The review cycle also runs `tools/run_agent_c_recommendations.py`. AgentC reads AgentB verification and manual-review learning reports, then writes recommendation files. When `--update-config` is enabled, `tools/apply_agent_optimizations.py` applies only safe, explainable excluded-domain additions; query, threshold, and identity-constraint ideas remain recommendations and labels until a maintainer implements them with tests.

## Main Outputs

After a successful run, the most important files are:

```text
outputs/my_run/provider_final_official_websites_second_pass.csv
outputs/my_run/provider_official_websites_second_pass_with_clickable_links.xlsx
outputs/my_run/provider_unresolved_second_pass.csv
outputs/my_run/quality_gate_provider_second_pass_final.json
outputs/my_run/manual_official_site_review_task.xlsx
outputs/my_run/manual_official_site_review_task.csv
outputs/my_run/agent_b_verification_results.csv
outputs/my_run/agent_b_verification_results.xlsx
outputs/my_run/agent_c_optimization_recommendations.md
```

Use the XLSX for manual browsing and the CSV for downstream processing.

After applying manual review, the important reviewed files are:

```text
outputs/my_run/provider_final_official_websites_reviewed.csv
outputs/my_run/provider_official_websites_reviewed_with_clickable_links.xlsx
outputs/my_run/provider_unresolved_reviewed.csv
outputs/my_run/manual_review_learning_report.md
outputs/my_run/manual_review_labels.csv
outputs/my_run/agent_c_optimization_recommendations.md
```

## Project Directory

```text
amazon-official-site-finder/
  README.md
  Makefile
  .env.example
  .gitignore
  requirements-optional.txt
  run_workflow.sh
  run_codex_assisted.sh
  run_review_cycle.sh

  config/
    scoring.json

  finder/
    core workflow package

  tools/
    workflow command scripts

  tests/
    automated tests and fixtures

  codex-skills/
    amazon-official-site-finder/

  docs/
    PROJECT_STRUCTURE_CN.md
    guides/
      PDF user guides
```

More detailed Chinese directory notes are in [docs/PROJECT_STRUCTURE_CN.md](docs/PROJECT_STRUCTURE_CN.md).

## Make Targets

```bash
make test
make install-optional
make pipeline SOURCE_CSV=/path/to/provider_details.csv RUN_DIR=outputs/my_run
make second-pass RUN_DIR=outputs/my_run
make review-task RUN_DIR=outputs/my_run
make agent-b RUN_DIR=outputs/my_run
make review-learning RUN_DIR=outputs/my_run REVIEW=/path/to/filled_review.xlsx
make agent-c RUN_DIR=outputs/my_run
make apply-agent-optimizations RUN_DIR=outputs/my_run
make verify RUN_DIR=outputs/my_run
```

`SOURCE_CSV` is intentionally not hard-coded; each user should pass their own input file path.
`review-task` and `review-learning` are mainly for Codex or advanced maintenance. A normal worker should fill the review workbook and give the filled file path to Codex.

## Tests

```bash
python3 -m unittest discover -s tests
```

The tests cover input normalization, search-source parsing, scoring, second-pass behavior, XLSX generation, output verification, quality gates, manual review task generation, manual review learning, and Codex key-file configuration.

## Local Files Not Committed

These are generated locally and ignored by Git:

```text
.env
.vendor_eval/
.cache/
outputs/
evidence/
.spreadsheet_build/
```
