#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 2 ]]; then
  echo "Usage: ./run_review_cycle.sh outputs/run_dir /path/to/filled_manual_review.csv-or.xlsx [labels.csv] [--update-config]" >&2
  exit 2
fi

RUN_DIR="$1"
REVIEW_FILE="$2"
LABELS_CSV=""
UPDATE_CONFIG=0
shift 2

while [[ $# -gt 0 ]]; do
  case "$1" in
    --update-config)
      UPDATE_CONFIG=1
      ;;
    -*)
      echo "Unknown option: $1" >&2
      exit 2
      ;;
    *)
      if [[ -n "$LABELS_CSV" ]]; then
        echo "Only one labels CSV can be provided." >&2
        exit 2
      fi
      LABELS_CSV="$1"
      ;;
  esac
  shift
done

cd "$(dirname "$0")"

if [[ ! -d .vendor_eval ]]; then
  python3 -m pip install --target .vendor_eval -r requirements-optional.txt
fi

ARGS=(--run-dir "$RUN_DIR" --review "$REVIEW_FILE" --write-xlsx)
if [[ -n "$LABELS_CSV" ]]; then
  ARGS+=(--labels "$LABELS_CSV")
fi
if [[ "$UPDATE_CONFIG" == "1" ]]; then
  ARGS+=(--update-config)
fi

PYTHONPATH=.vendor_eval:. python3 tools/run_review_learning.py "${ARGS[@]}"

PYTHONPATH=.vendor_eval:. python3 tools/run_agent_c_recommendations.py --run-dir "$RUN_DIR" --human-review "$REVIEW_FILE"
if [[ "$UPDATE_CONFIG" == "1" ]]; then
  PYTHONPATH=.vendor_eval:. python3 tools/apply_agent_optimizations.py --run-dir "$RUN_DIR" --apply
fi

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
echo "AgentC recommendations: $RUN_DIR/agent_c_optimization_recommendations.md"
if [[ "$UPDATE_CONFIG" == "1" ]]; then
  echo "Config optimization: enabled for safe repeated patterns."
fi
