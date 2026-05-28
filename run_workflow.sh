#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 2 || $# -gt 3 ]]; then
  echo "Usage: ./run_workflow.sh /path/to/input.csv outputs/run_dir [labels.csv]" >&2
  exit 2
fi

SOURCE_CSV="$1"
RUN_DIR="$2"
LABELS_CSV="${3:-}"

cd "$(dirname "$0")"

if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "Created .env. Fill BRAVE_API_KEY and optional EXA_API_KEY, then rerun this command." >&2
  exit 2
fi

if [[ ! -d .vendor_eval ]]; then
  python3 -m pip install --target .vendor_eval -r requirements-optional.txt
fi

PREFLIGHT_ARGS=(--source "$SOURCE_CSV" --run-dir "$RUN_DIR" --live-check)
PIPELINE_ARGS=(--source "$SOURCE_CSV" --run-dir "$RUN_DIR")
if [[ -n "$LABELS_CSV" ]]; then
  PREFLIGHT_ARGS+=(--labels "$LABELS_CSV")
  PIPELINE_ARGS+=(--labels "$LABELS_CSV")
fi

PYTHONPATH=.vendor_eval:. python3 tools/preflight_report.py "${PREFLIGHT_ARGS[@]}"

PYTHONPATH=.vendor_eval:. python3 tools/run_pipeline.py \
  "${PIPELINE_ARGS[@]}" \
  --batch-size 50 \
  --per-query 3 \
  --max-queries 6 \
  --max-candidates 10 \
  --resume \
  --run-second-pass \
  --second-pass-per-query 3 \
  --second-pass-max-search-queries 6 \
  --second-pass-accept-threshold 70 \
  --second-pass-write-xlsx \
  --min-domain-accuracy 0.8 \
  --min-auto-precision 0.95 \
  --min-official-url-rate 0.5 \
  --max-unresolved-rate 0.6

python3 tools/verify_run_outputs.py \
  --run-dir "$RUN_DIR" \
  --final provider_final_official_websites_second_pass.csv \
  --unresolved provider_unresolved_second_pass.csv \
  --quality quality_gate_provider_second_pass_final.json \
  --xlsx "$RUN_DIR/provider_official_websites_second_pass_with_clickable_links.xlsx"

echo "Done."
echo "Final CSV: $RUN_DIR/provider_final_official_websites_second_pass.csv"
echo "Clickable XLSX: $RUN_DIR/provider_official_websites_second_pass_with_clickable_links.xlsx"
echo "Manual review CSV: $RUN_DIR/manual_official_site_review_task.csv"
echo "Manual review XLSX: $RUN_DIR/manual_official_site_review_task.xlsx"
