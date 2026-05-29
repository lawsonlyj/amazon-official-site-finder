#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 2 ]]; then
  echo "Usage: ./run_workflow.sh /path/to/input.csv outputs/run_dir [labels.csv] [--run-agent-b] [--apply-agent-optimizations] [--agent-b-limit N] [--human-review file.xlsx]" >&2
  exit 2
fi

SOURCE_CSV="$1"
RUN_DIR="$2"
LABELS_CSV=""
RUN_AGENT_B=0
APPLY_AGENT_OPTIMIZATIONS=0
AGENT_B_LIMIT=0
HUMAN_REVIEW_FILE=""
shift 2

while [[ $# -gt 0 ]]; do
  case "$1" in
    --run-agent-b)
      RUN_AGENT_B=1
      ;;
    --apply-agent-optimizations)
      APPLY_AGENT_OPTIMIZATIONS=1
      RUN_AGENT_B=1
      ;;
    --agent-b-limit)
      AGENT_B_LIMIT="${2:-0}"
      shift
      ;;
    --human-review)
      HUMAN_REVIEW_FILE="${2:-}"
      RUN_AGENT_B=1
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
  --second-pass-accept-threshold 75 \
  --second-pass-write-xlsx \
  --min-domain-accuracy 0.8 \
  --min-auto-precision 0.95 \
  --min-official-url-rate 0.5 \
  --max-unresolved-rate 0.6

python3 tools/verify_run_outputs.py \
  --run-dir "$RUN_DIR" \
  --final official_sites.csv \
  --unresolved unresolved.csv \
  --quality quality.json \
  --xlsx "$RUN_DIR/official_sites.xlsx"

if [[ "$RUN_AGENT_B" == "1" ]]; then
  AGENT_B_ARGS=(--run-dir "$RUN_DIR" --write-xlsx)
  if [[ "$AGENT_B_LIMIT" != "0" ]]; then
    AGENT_B_ARGS+=(--limit "$AGENT_B_LIMIT")
  fi
  PYTHONPATH=.vendor_eval:. python3 tools/run_agent_b_verification.py "${AGENT_B_ARGS[@]}"
  AGENT_C_ARGS=(--run-dir "$RUN_DIR")
  if [[ -n "$HUMAN_REVIEW_FILE" ]]; then
    AGENT_C_ARGS+=(--human-review "$HUMAN_REVIEW_FILE")
  fi
  PYTHONPATH=.vendor_eval:. python3 tools/run_agent_b_recommendations.py "${AGENT_C_ARGS[@]}"
  if [[ "$APPLY_AGENT_OPTIMIZATIONS" == "1" ]]; then
    PYTHONPATH=.vendor_eval:. python3 tools/apply_agent_optimizations.py --run-dir "$RUN_DIR" --apply
  fi
fi

echo "Done."
echo "Final CSV: $RUN_DIR/official_sites.csv"
echo "Clickable XLSX: $RUN_DIR/official_sites.xlsx"
echo "Unresolved CSV: $RUN_DIR/unresolved.csv"
echo "Manual review CSV: $RUN_DIR/review_task.csv"
echo "Manual review XLSX: $RUN_DIR/review_task.xlsx"
if [[ "$RUN_AGENT_B" == "1" ]]; then
  echo "AgentB verification CSV: $RUN_DIR/agent_b/check.csv"
  echo "AgentB verification XLSX: $RUN_DIR/agent_b/check.xlsx"
  echo "AgentB suggestions: $RUN_DIR/agent_b/suggestions.md"
fi
