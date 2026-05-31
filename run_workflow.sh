#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 2 ]]; then
  echo "Usage: ./run_workflow.sh /path/to/input.csv outputs/run_dir [labels.csv] [--run-check-suggestion] [--apply-operation-optimizations] [--check-limit N] [--human-review file.xlsx] [--pattern-release-json file.json]" >&2
  exit 2
fi

SOURCE_CSV="$1"
RUN_DIR="$2"
LABELS_CSV=""
RUN_CHECK_SUGGESTION=0
APPLY_OPERATION_OPTIMIZATIONS=0
CHECK_LIMIT=0
HUMAN_REVIEW_FILE=""
PATTERN_RELEASE_JSONS=()
shift 2

while [[ $# -gt 0 ]]; do
  case "$1" in
    --run-check-suggestion|--run-agent-b)
      RUN_CHECK_SUGGESTION=1
      ;;
    --apply-operation-optimizations|--apply-agent-optimizations)
      APPLY_OPERATION_OPTIMIZATIONS=1
      RUN_CHECK_SUGGESTION=1
      ;;
    --check-limit|--agent-b-limit)
      CHECK_LIMIT="${2:-0}"
      shift
      ;;
    --human-review)
      HUMAN_REVIEW_FILE="${2:-}"
      RUN_CHECK_SUGGESTION=1
      shift
      ;;
    --pattern-release-json)
      PATTERN_RELEASE_JSONS+=("${2:-}")
      RUN_CHECK_SUGGESTION=1
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

if [[ "$RUN_CHECK_SUGGESTION" == "1" ]]; then
  CHECK_ARGS=(--run-dir "$RUN_DIR" --write-xlsx)
  if [[ "$CHECK_LIMIT" != "0" ]]; then
    CHECK_ARGS+=(--limit "$CHECK_LIMIT")
  fi
  PYTHONPATH=.vendor_eval:. python3 tools/run_agent_b_verification.py "${CHECK_ARGS[@]}"
  SUGGESTION_ARGS=(--run-dir "$RUN_DIR")
  if [[ -n "$HUMAN_REVIEW_FILE" ]]; then
    SUGGESTION_ARGS+=(--human-review "$HUMAN_REVIEW_FILE")
  fi
  PYTHONPATH=.vendor_eval:. python3 tools/run_agent_b_recommendations.py "${SUGGESTION_ARGS[@]}"
  if [[ "$APPLY_OPERATION_OPTIMIZATIONS" == "1" ]]; then
    PYTHONPATH=.vendor_eval:. python3 tools/apply_agent_optimizations.py --run-dir "$RUN_DIR" --apply
  fi
  if [[ "${#PATTERN_RELEASE_JSONS[@]}" -gt 0 ]]; then
    PATTERN_RELEASE_ARGS=(--run-dir "$RUN_DIR" --write-xlsx)
    if [[ -n "$LABELS_CSV" ]]; then
      PATTERN_RELEASE_ARGS+=(--labels "$LABELS_CSV")
    fi
    for pattern_json in "${PATTERN_RELEASE_JSONS[@]}"; do
      PATTERN_RELEASE_ARGS+=(--pattern-json "$pattern_json")
    done
    PYTHONPATH=.vendor_eval:. python3 tools/apply_pattern_release_to_run.py "${PATTERN_RELEASE_ARGS[@]}"
    python3 tools/verify_run_outputs.py \
      --run-dir "$RUN_DIR" \
      --final official_sites.csv \
      --unresolved unresolved.csv \
      --quality quality.json \
      --xlsx "$RUN_DIR/official_sites.xlsx"
  fi
fi

echo "Done."
echo "Final CSV: $RUN_DIR/official_sites.csv"
echo "Clickable XLSX: $RUN_DIR/official_sites.xlsx"
echo "Unresolved CSV: $RUN_DIR/unresolved.csv"
echo "Manual review CSV: $RUN_DIR/review_task.csv"
echo "Manual review XLSX: $RUN_DIR/review_task.xlsx"
if [[ "$RUN_CHECK_SUGGESTION" == "1" ]]; then
  echo "Check CSV: $RUN_DIR/check_suggestion/check.csv"
  echo "Check XLSX: $RUN_DIR/check_suggestion/check.xlsx"
  echo "Suggestions: $RUN_DIR/check_suggestion/suggestions.md"
fi
if [[ "${#PATTERN_RELEASE_JSONS[@]}" -gt 0 ]]; then
  echo "Pattern release summary: $RUN_DIR/operation_optimization/pattern_release_applied.json"
fi
