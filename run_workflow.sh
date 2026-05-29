#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 2 ]]; then
  echo "Usage: ./run_workflow.sh /path/to/input.csv outputs/run_dir [labels.csv] [--run-agent-b] [--agent-b-limit N]" >&2
  exit 2
fi

SOURCE_CSV="$1"
RUN_DIR="$2"
LABELS_CSV=""
RUN_AGENT_B=0
AGENT_B_LIMIT=0
shift 2

while [[ $# -gt 0 ]]; do
  case "$1" in
    --run-agent-b)
      RUN_AGENT_B=1
      ;;
    --agent-b-limit)
      AGENT_B_LIMIT="${2:-0}"
      shift
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

if [[ "$RUN_AGENT_B" == "1" ]]; then
  AGENT_B_ARGS=(--run-dir "$RUN_DIR" --write-xlsx)
  if [[ "$AGENT_B_LIMIT" != "0" ]]; then
    AGENT_B_ARGS+=(--limit "$AGENT_B_LIMIT")
  fi
  PYTHONPATH=.vendor_eval:. python3 tools/run_agent_b_verification.py "${AGENT_B_ARGS[@]}"
fi

echo "Done."
echo "Final CSV: $RUN_DIR/provider_final_official_websites_second_pass.csv"
echo "Clickable XLSX: $RUN_DIR/provider_official_websites_second_pass_with_clickable_links.xlsx"
echo "Manual review CSV: $RUN_DIR/manual_official_site_review_task.csv"
echo "Manual review XLSX: $RUN_DIR/manual_official_site_review_task.xlsx"
if [[ "$RUN_AGENT_B" == "1" ]]; then
  echo "AgentB verification CSV: $RUN_DIR/agent_b_verification_results.csv"
  echo "AgentB verification XLSX: $RUN_DIR/agent_b_verification_results.xlsx"
fi
