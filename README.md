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
  --run-agent-b \
  --pattern-release-json outputs/calibration_cycle/pattern_release_simulation.json
```

`--run-agent-b` is optional. It adds the AgentB step after AgentA's main workflow: high-risk candidate verification plus optimization suggestions. AgentB now checks only rows that are likely to need attention, such as low-confidence accepts, lower-confidence second-pass/manual accepts, unresolved rows with candidates, platform/profile URLs, generic names, logo-only evidence, weak identity/service matches, and specific high-confidence identity risks where `consulting`/`seller` style names or slug-extended domains still need human corroboration. For rows coming from the high-risk review task, B keeps recall and replacement candidates as evidence but marks them `unsure` instead of auto-filling a `replace` decision. Add `--human-review /path/to/filled_review.xlsx` to let B turn filled human-review notes into regression fixtures and safer rule recommendations. Add `--apply-agent-optimizations` only when A should apply safe B recommendations such as repeated excluded-domain additions and write regression artifacts. Add `--pattern-release-json /path/to/pattern_release_simulation.json` only after calibration has selected actionable zero-wrong evidence patterns; this applies those patterns to unresolved rows in the run's canonical `official_sites.csv/xlsx`, refreshes `unresolved.csv`, `quality.json`, `review_task.*`, and records the change in `agent_a/pattern_release_applied.json`. Calibrated release still blocks docs/help/support/api/app/login-style subdomains because those are usually documentation, support, or app surfaces rather than independent official homepages.

The current workflow version is `agent-loop-v6.3-calibrated-release`. This version adds country/language-aware search terms, country TLD and page-location corroboration, identity caps for same-name/service/country conflicts, logo-only risk handling, and no-official regression fixtures learned from human review. It also relaxes the ambiguous-name cap when a candidate has page-level provider identity plus at least weak marketplace/service evidence, which reduces over-rejection of otherwise correct official sites. Logo similarity is useful positive evidence, but a logo alone is not enough to auto-accept a site. The manual-review builder now tracks review capture metrics, reviews second-pass accepts below 85, and uses narrow `generic_identity` / `slug_extension` lanes instead of broad ambiguous-name review. AgentB keeps 70-84 score rows in those lanes as `unsure` unless exact logo evidence supports the candidate. The balance evaluator also reports AgentB false-official catch rate, correct-official accept rate, unresolved recovery quality, and whether selected actionable evidence-pattern releases improve recall without adding labeled wrong official URLs. Calibrated pattern release is now an explicit workflow option rather than a global threshold relaxation, and it refuses risky non-homepage subdomains even when their evidence pattern matched.

For larger AgentB checks, run `python3 tools/run_agent_b_verification.py --run-dir outputs/my_run --resume --write-xlsx`. The command writes progress incrementally and reuses completed rows, so interrupted 300-row or full-run checks can continue without starting over. Add `--row-timeout 15 --per-query 1` for batch validation when slow sites should be marked `unsure` instead of blocking the whole run.

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
outputs/my_run/review_task.xlsx
```

Fill only `manual_decision`, `manual_url`, and `notes`. After that, hand the filled workbook back to Codex. The worker does not need to run another shell command.

In Codex, say:

```text
Use amazon-official-site-finder skill.
Run directory: outputs/my_run
Filled review file: outputs/my_run/review_task.xlsx
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
   Core library used by the pipeline. It normalizes Amazon rows, builds country/language-aware search queries, calls search sources, fetches pages, scores candidates, and finalizes rows. The scorer requires identity, service, and location evidence to agree before high-risk candidates can be accepted automatically.

5. `tools/run_unresolved_second_pass.py`
   Runs the Brave/Exa second-pass on unresolved providers. Its default accept threshold is `75`, aligned with first-pass `auto_match_threshold=75`, and it still requires identity evidence, URL risk checks, and no blocking identity cap.

6. `tools/build_manual_review_task.py`
   Builds the simplified manual review CSV/XLSX. This is the worker-facing task file for checking uncertain official websites and unresolved top candidates.

7. `tools/build_linked_workbook.py`
   Creates the clickable XLSX workbook so URLs can be opened directly in spreadsheet software.

8. `tools/verify_run_outputs.py`
   Verifies the final CSV, unresolved CSV, quality JSON, and XLSX hyperlink formulas.

9. Optional B loop
   `tools/run_agent_b_verification.py` re-checks high-risk official/top-candidate URLs first, inspects company pages and lightweight independent search corroboration, and writes structured `accept`/`replace`/`reject`/`unsure` evidence while preserving `manual_decision`, `manual_url`, and `notes`. It also uses country/language search hints when building independent queries. `tools/run_agent_b_recommendations.py` writes B suggestions, and `tools/apply_agent_optimizations.py` is A's safe apply step. When `--pattern-release-json` is provided, `tools/apply_pattern_release_to_run.py` applies the calibrated selected actionable pattern set to unresolved rows, refreshes canonical output files, and keeps the released rows in the review task as `precision_calibrated_pattern_release` spot-checks. The legacy `tools/run_agent_c_recommendations.py` wrapper still works.

For Codex-assisted usage, `run_codex_assisted.sh` runs first. It calls `tools/configure_env_from_key_files.py` to create `.env` from local key files without printing secrets, then hands off to `run_workflow.sh`.

After manual review, the user gives the filled workbook to Codex. Codex calls `run_review_cycle.sh` internally, which runs `tools/run_review_learning.py`, merges the filled review with the second-pass decisions, writes reviewed final outputs, creates manual labels, runs the quality gate again, and writes a learning report. In Codex mode, Codex also enables safe config optimization for repeated rejected directory/platform patterns and reports whether anything changed.

The review cycle also runs `tools/run_agent_b_recommendations.py`. B reads AgentB verification, filled human-review notes, and manual-review learning reports, then writes suggestion files. When `--update-config` is enabled, `tools/apply_agent_optimizations.py` applies only safe, explainable excluded-domain additions and writes human/identity/no-official/reachability regression fixtures. Query, threshold, and new identity-constraint logic changes should still be implemented deliberately with tests.

For threshold/rule tuning, use `tools/evaluate_workflow_balance.py` with a baseline final CSV, a candidate final CSV, and the filled yellow-row review workbook. It reports false official URLs, over-rejected correct sites, automatic precision, official-site recall, unresolved rows, manual-review workload, and a simulation of whether AgentB unresolved recall candidates can be safely auto-released at different evidence thresholds. Then use `tools/build_balance_report.py` to combine the labeled balance JSON with larger unlabeled review/AgentB batches and `--pattern-release-json` from `tools/simulate_pattern_release.py`; the report recommends the global threshold separately from any narrow selected actionable pattern-release set. Use `tools/run_calibration_cycle.py` to generate the next review package in one command: recall/precision evidence-pattern reports, recall pattern release simulation, threshold boundary report, a balanced pattern-validation sample XLSX, an empty evaluation report, and a cycle summary. When that sample is filled, rerun the same command with `--filled-sample /path/to/filled.xlsx` to add filled-label summaries plus `pattern_rule_candidates.json/md`. These files split patterns into `candidate_for_rule`, `needs_more_labels`, and `reject_pattern`, with an explicit required action for each. Use `tools/mine_evidence_patterns.py`, `tools/simulate_pattern_release.py`, and `tools/build_threshold_boundary_report.py` directly when you need to inspect whether any specific evidence combinations have zero labeled mistakes and what precision/recall/accuracy would look like if A released them. `simulate_pattern_release.py` now evaluates both individual actionable patterns and a selected actionable pattern set, so A can prefer narrow identity-plus-corroboration releases over broad threshold relaxation. Use `tools/apply_pattern_release_experiment.py` to write an experimental final CSV/XLSX for a candidate pattern before changing production workflow behavior; after deciding to apply a calibrated set to a run, use `tools/apply_pattern_release_to_run.py` or pass the same JSON to `run_workflow.sh --pattern-release-json`. Finally, use `tools/build_release_policy_report.py` to merge baseline/candidate balance metrics, threshold simulations, pattern-release simulation, and batch application summaries into the final policy report. These reports record whether thresholds should stay fixed, which score band should be treated as high-value review rather than globally rejected, whether raw AgentB recall candidates remain manual-only, and whether calibrated pattern release is allowed under the risky-subdomain guard. Treat those outputs as validation candidates until more labels confirm them.

To collect the next small set of high-value human labels from a larger batch, use `tools/build_calibration_review_sample.py` with the batch `review_task.csv` and `agent_b/check.csv`. It prioritizes timeout rows, AgentB rejects, risky-lane accepts, unresolved recall candidates, and AgentB unsure rows so new labels are useful for deciding whether to tighten, relax, or keep the current rules. Add one or more `--pattern-json` files from `tools/mine_evidence_patterns.py` when the next review should validate specific evidence-combination candidates; use `--max-per-pattern` to keep the review set from over-sampling one pattern. After the worker fills `manual_decision`, `manual_url`, and `notes`, run `tools/evaluate_calibration_review_sample.py` on that same CSV/XLSX. It summarizes which review lanes are still catching bad official URLs, which unresolved candidates are useful recall examples, whether AgentB risky accepts can be released more broadly, whether timeout rows should be retried before manual review, and whether each `pattern_match` should be rejected, kept for more labels, or promoted to a rule candidate. Candidate rules are advisory only: A should add regression tests and apply the exact pattern deliberately before changing production acceptance or review-lane behavior.

## Main Outputs

After a successful run, the most important files are:

```text
outputs/my_run/official_sites.csv
outputs/my_run/official_sites.xlsx
outputs/my_run/unresolved.csv
outputs/my_run/quality.json
outputs/my_run/review_task.xlsx
outputs/my_run/review_task.csv
outputs/my_run/agent_b/check.csv
outputs/my_run/agent_b/check.xlsx
outputs/my_run/agent_b/suggestions.md
```

Use the XLSX for manual browsing and the CSV for downstream processing. Legacy names such as `provider_final_official_websites_second_pass.csv`, `provider_official_websites_second_pass_with_clickable_links.xlsx`, and `manual_official_site_review_task.xlsx` are still generated for compatibility.

After applying manual review, the important reviewed files are:

```text
outputs/my_run/reviewed/official_sites.csv
outputs/my_run/reviewed/official_sites.xlsx
outputs/my_run/reviewed/unresolved.csv
outputs/my_run/reviewed/learning.md
outputs/my_run/reviewed/labels.csv
outputs/my_run/agent_b/suggestions.md
outputs/my_run/agent_a/applied.json
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
make agent-b-suggestions RUN_DIR=outputs/my_run
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
