#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 2 || $# -gt 3 ]]; then
  echo "Usage: ./run_review_cycle.sh outputs/run_dir /path/to/filled_manual_review.csv-or.xlsx [labels.csv]" >&2
  exit 2
fi

RUN_DIR="$1"
REVIEW_FILE="$2"
LABELS_CSV="${3:-}"

cd "$(dirname "$0")"

if [[ ! -d .vendor_eval ]]; then
  python3 -m pip install --target .vendor_eval -r requirements-optional.txt
fi

ARGS=(--run-dir "$RUN_DIR" --review "$REVIEW_FILE" --write-xlsx)
if [[ -n "$LABELS_CSV" ]]; then
  ARGS+=(--labels "$LABELS_CSV")
fi

PYTHONPATH=.vendor_eval:. python3 tools/run_review_learning.py "${ARGS[@]}"

python3 tools/verify_run_outputs.py \
  --run-dir "$RUN_DIR" \
  --final provider_final_official_websites_reviewed.csv \
  --unresolved provider_unresolved_reviewed.csv \
  --quality quality_gate_provider_reviewed.json \
  --xlsx "$RUN_DIR/provider_official_websites_reviewed_with_clickable_links.xlsx"

echo "Done."
echo "Reviewed final CSV: $RUN_DIR/provider_final_official_websites_reviewed.csv"
echo "Reviewed clickable XLSX: $RUN_DIR/provider_official_websites_reviewed_with_clickable_links.xlsx"
echo "Learning report: $RUN_DIR/manual_review_learning_report.md"
