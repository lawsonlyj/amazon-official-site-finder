# Amazon Official Site Finder

This repository provides a reusable, structured workflow for finding independent official websites for Amazon GSPN/SPN provider rows.

The default GitHub workflow is the **Workflow Body**: search, score, second-pass, output files, quality checks, and a small manual review workbook. It does not require autonomous agents or an OpenAI API key. The Codex-assisted path can optionally add visual verification after the deterministic workflow finishes.

Maintainer-only calibration and optimization tools are documented separately in [docs/DEVELOPMENT_WORKFLOW_CN.md](docs/DEVELOPMENT_WORKFLOW_CN.md).

## Who Should Run What

### Normal Users: Workflow Body

Run this when you want official-site results for a provider CSV.

```bash
cp .env.example .env
# Fill BRAVE_API_KEY and preferably EXA_API_KEY in .env
./run_workflow.sh "/path/to/provider_details.csv" "outputs/my_run"
```

Or, when Codex has local key files:

```bash
./run_codex_assisted.sh \
  --brave-key-file "/path/to/brave_key.txt" \
  --exa-key-file "/path/to/exa_key.txt" \
  --source "/path/to/provider_details.csv" \
  --run-dir "outputs/my_run"
```

### Maintainers: Development Workflow

Run this only when you are improving the workflow itself, validating new rules, or applying calibrated suggestions. The maintainer flow is separate from the reusable Workflow Body:

```text
Operation and Optimization -> CheckAgent -> human review -> OptimizationAgent -> deterministic gate -> Operation and Optimization
```

`CheckAgent` and `OptimizationAgent` are development-stage agent roles. They may judge evidence and suggest changes, but they do not directly change production results or scoring rules. The deterministic gate must pass before Operation and Optimization absorbs a rule or regression fixture.

Start a development run like this:

```bash
./run_codex_assisted.sh \
  --brave-key-file "/path/to/brave_key.txt" \
  --exa-key-file "/path/to/exa_key.txt" \
  --source "/path/to/provider_details.csv" \
  --run-dir "outputs/dev_run" \
  --run-check-suggestion
```

To run the real development agents, add `OPENAI_API_KEY` to `.env` and explicitly opt in:

```bash
./run_workflow.sh "/path/to/provider_details.csv" "outputs/dev_run" \
  --run-check-agent \
  --run-optimization-agent \
  --application-gates-json "outputs/dev_run/calibration_cycle/calibration_application_gates.json" \
  --development-cycle 1
```

Optional maintainer flags include `--human-review`, `--run-check-agent`, `--run-optimization-agent`, `--apply-operation-optimizations`, and `--pattern-release-json`. They are not part of the normal user path.

Legacy flags such as `--run-agent-b` and `--apply-agent-optimizations` are still accepted for old scripts. The old `agent_c` wrapper has been removed; suggestion behavior belongs to CheckAgent / Check and Suggestion in the development workflow.

## Inputs

The source CSV should keep the same Amazon provider format used by GSPN/SPN exports:

- Row 1: field names.
- Row 2: optional field descriptions.
- Row 3+: provider rows.

The Workflow Body now starts with an explicit deduplication step. Duplicate service rows for the same `provider_id` are merged into one provider row before search starts; service names, locations, languages, and service types are preserved as JSON list fields.

Production search needs:

- `BRAVE_API_KEY`
- `EXA_API_KEY` recommended for second-pass recall

Local files that contain API keys must not be committed.

## Workflow Body Outputs

After a successful normal run, use these files:

```text
outputs/my_run/official_sites.csv
outputs/my_run/official_sites.xlsx
outputs/my_run/unresolved.csv
outputs/my_run/quality.json
outputs/my_run/quality.md
outputs/my_run/review_task.csv
outputs/my_run/review_task.xlsx
outputs/my_run/manifest.json
```

`official_sites.xlsx` contains clickable links. `review_task.xlsx` is the small human review workbook for uncertain rows.

Intermediate evidence lives under:

```text
outputs/my_run/details/input/
outputs/my_run/details/first_pass/
outputs/my_run/details/second_pass/
```

The input folder includes the deduplication artifacts used by the run:

```text
outputs/my_run/details/input/deduped_input.csv
outputs/my_run/details/input/deduped_input.xlsx
outputs/my_run/details/input/dedupe_report.json
outputs/my_run/details/input/dedupe_report.md
```

Older public filenames such as `provider_final_official_websites_second_pass.csv`, `provider_official_websites_second_pass_with_clickable_links.xlsx`, and `manual_official_site_review_task.xlsx` are still accepted as fallback inputs for historical run directories. New runs only write the short canonical filenames shown above; duplicate legacy output writing has been removed.

## Manual Review

The workflow creates:

```text
outputs/my_run/review_task.xlsx
```

Reviewers should fill only:

- `manual_decision`: `accept`, `replace`, `reject`, or `unsure`
- `manual_url`: required for `replace`; optional for `accept` when the shown URL is correct
- `notes`: optional reason

Then run, or ask Codex to run:

```bash
./run_review_cycle.sh "outputs/my_run" "/path/to/filled_review_task.xlsx"
```

Reviewed outputs are:

```text
outputs/my_run/reviewed/official_sites.csv
outputs/my_run/reviewed/official_sites.xlsx
outputs/my_run/reviewed/unresolved.csv
outputs/my_run/reviewed/quality.json
outputs/my_run/reviewed/learning.md
outputs/my_run/reviewed/labels.csv
```

## What The Workflow Body Runs

1. `tools/deduplicate_input.py`
2. `tools/preflight_report.py`
3. `tools/run_pipeline.py`
4. `finder/` scoring/search/fetch logic
5. `tools/run_unresolved_second_pass.py`
6. `tools/build_manual_review_task.py`
7. `tools/build_linked_workbook.py`
8. `tools/verify_run_outputs.py`

The first-pass and second-pass default accept thresholds are both `75`. Second pass still requires strong evidence and URL-risk checks.

## Codex-Assisted Visual Verification

When higher precision is needed on uncertain rows, Codex can add a visual verification pass after `run_workflow.sh` completes. This is the only normal Workflow Body step where Codex directly judges candidates; `run_workflow.sh` itself remains pure script.

```bash
python3 -m playwright install chromium
python3 tools/build_visual_verification_task.py --run-dir "outputs/my_run"
```

Codex then opens `outputs/my_run/visual_verification/grids/grid_*.png`, fills `manual_decision`, `manual_url`, and `notes` in the generated task, and applies the verdicts:

```bash
python3 tools/apply_visual_verification.py --run-dir "outputs/my_run" \
  --verdicts "outputs/my_run/visual_verification/visual_verification_task.xlsx"
```

The apply step overwrites the canonical `official_sites.csv`, `official_sites.xlsx`, `unresolved.csv`, `quality.json`, `review_task.*`, and `manifest.json` in place. Use `unsure` for bot-blocked or visually inconclusive pages.

## Maintainer Development Outputs

When `--run-check-suggestion` is enabled, additional development artifacts are written:

```text
outputs/dev_run/check_suggestion/check.csv
outputs/dev_run/check_suggestion/check.xlsx
outputs/dev_run/check_suggestion/suggestions.json
outputs/dev_run/check_suggestion/suggestions.md
outputs/dev_run/operation_optimization/applied.json
```

When real Development Workflow agents are explicitly enabled, additional artifacts are written:

```text
outputs/dev_run/development/check_agent/check.csv
outputs/dev_run/development/check_agent/check.jsonl
outputs/dev_run/development/check_agent/summary.json
outputs/dev_run/development/optimization_agent/decision.json
outputs/dev_run/development/optimization_agent/decision.md
outputs/dev_run/development/cycle_N/metrics.json
outputs/dev_run/development/cycle_N/metrics.md
```

These files are for calibration, regression fixtures, and safe workflow improvement. They are not required for normal users.

See [docs/DEVELOPMENT_WORKFLOW_CN.md](docs/DEVELOPMENT_WORKFLOW_CN.md) for the development workflow, including the CheckAgent, OptimizationAgent, human-label, and deterministic-gate boundaries.

## Make Targets

Normal user targets:

```bash
make test
make install-optional
make pipeline SOURCE_CSV=/path/to/provider_details.csv RUN_DIR=outputs/my_run
make second-pass RUN_DIR=outputs/my_run
make review-task RUN_DIR=outputs/my_run
make review-learning RUN_DIR=outputs/my_run REVIEW=/path/to/filled_review.xlsx
make verify RUN_DIR=outputs/my_run
```

Maintainer targets:

```bash
make check-suggestion RUN_DIR=outputs/dev_run
make check-suggestions RUN_DIR=outputs/dev_run
make apply-operation-optimizations RUN_DIR=outputs/dev_run
```

## Tests

```bash
PYTHONPATH=.vendor_eval:. python3 -m unittest discover -s tests
bash -n run_workflow.sh run_codex_assisted.sh run_review_cycle.sh
python3 -m py_compile finder/*.py tools/*.py
git diff --check
```

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
