#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat >&2 <<'USAGE'
Usage:
  ./run_codex_assisted.sh \
    --brave-key-file /path/to/brave_key.txt \
    --exa-key-file /path/to/exa_key.txt \
    --openai-key-file /path/to/openai_key.txt \
    --source /path/to/provider_details.csv

Optional:
  --run-dir outputs/my_run
  --labels /path/to/golden_expected_websites.csv
  --run-check-suggestion
  --run-check-agent
  --run-optimization-agent
  --apply-operation-optimizations
  --check-limit N
  --human-review /path/to/filled_review.xlsx
  --pattern-release-json /path/to/pattern_release_simulation.json
  --balance-report-json /path/to/balance_report.json
  --convergence-audit-json /path/to/convergence_audit.json
  --application-gates-json /path/to/calibration_application_gates.json
  --development-cycle N
USAGE
}

BRAVE_KEY_FILE=""
EXA_KEY_FILE=""
OPENAI_KEY_FILE=""
SOURCE_CSV=""
RUN_DIR=""
LABELS_CSV=""
RUN_CHECK_SUGGESTION=0
RUN_CHECK_AGENT=0
RUN_OPTIMIZATION_AGENT=0
APPLY_OPERATION_OPTIMIZATIONS=0
CHECK_LIMIT=0
HUMAN_REVIEW_FILE=""
PATTERN_RELEASE_JSONS=()
BALANCE_REPORT_JSON=""
CONVERGENCE_AUDIT_JSON=""
APPLICATION_GATES_JSON=""
DEVELOPMENT_CYCLE=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --brave-key-file)
      BRAVE_KEY_FILE="${2:-}"
      shift 2
      ;;
    --exa-key-file)
      EXA_KEY_FILE="${2:-}"
      shift 2
      ;;
    --openai-key-file)
      OPENAI_KEY_FILE="${2:-}"
      shift 2
      ;;
    --source)
      SOURCE_CSV="${2:-}"
      shift 2
      ;;
    --run-dir)
      RUN_DIR="${2:-}"
      shift 2
      ;;
    --labels)
      LABELS_CSV="${2:-}"
      shift 2
      ;;
    --run-check-suggestion|--run-agent-b)
      RUN_CHECK_SUGGESTION=1
      shift
      ;;
    --run-check-agent)
      RUN_CHECK_AGENT=1
      RUN_CHECK_SUGGESTION=1
      shift
      ;;
    --run-optimization-agent)
      RUN_OPTIMIZATION_AGENT=1
      RUN_CHECK_AGENT=1
      RUN_CHECK_SUGGESTION=1
      shift
      ;;
    --apply-operation-optimizations|--apply-agent-optimizations)
      APPLY_OPERATION_OPTIMIZATIONS=1
      RUN_CHECK_SUGGESTION=1
      shift
      ;;
    --check-limit|--agent-b-limit)
      CHECK_LIMIT="${2:-0}"
      shift 2
      ;;
    --human-review)
      HUMAN_REVIEW_FILE="${2:-}"
      RUN_CHECK_SUGGESTION=1
      shift 2
      ;;
    --pattern-release-json)
      PATTERN_RELEASE_JSONS+=("${2:-}")
      RUN_CHECK_SUGGESTION=1
      shift 2
      ;;
    --balance-report-json)
      BALANCE_REPORT_JSON="${2:-}"
      shift 2
      ;;
    --convergence-audit-json)
      CONVERGENCE_AUDIT_JSON="${2:-}"
      shift 2
      ;;
    --application-gates-json)
      APPLICATION_GATES_JSON="${2:-}"
      shift 2
      ;;
    --development-cycle)
      DEVELOPMENT_CYCLE="${2:-}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      exit 2
      ;;
  esac
done

if [[ -z "$BRAVE_KEY_FILE" || -z "$SOURCE_CSV" ]]; then
  usage
  exit 2
fi

if [[ -z "$RUN_DIR" ]]; then
  RUN_DIR="outputs/codex_run_$(date +%Y%m%d_%H%M%S)"
fi

cd "$(dirname "$0")"

CONFIGURE_ARGS=(--brave-key-file "$BRAVE_KEY_FILE" --env .env)
if [[ -n "$EXA_KEY_FILE" ]]; then
  CONFIGURE_ARGS+=(--exa-key-file "$EXA_KEY_FILE")
fi
if [[ -n "$OPENAI_KEY_FILE" ]]; then
  CONFIGURE_ARGS+=(--openai-key-file "$OPENAI_KEY_FILE")
fi

python3 tools/configure_env_from_key_files.py "${CONFIGURE_ARGS[@]}"

WORKFLOW_ARGS=("$SOURCE_CSV" "$RUN_DIR")
if [[ -n "$LABELS_CSV" ]]; then
  WORKFLOW_ARGS+=("$LABELS_CSV")
fi
if [[ "$RUN_CHECK_SUGGESTION" == "1" ]]; then
  WORKFLOW_ARGS+=(--run-check-suggestion)
  if [[ "$RUN_CHECK_AGENT" == "1" ]]; then
    WORKFLOW_ARGS+=(--run-check-agent)
  fi
  if [[ "$RUN_OPTIMIZATION_AGENT" == "1" ]]; then
    WORKFLOW_ARGS+=(--run-optimization-agent)
  fi
  if [[ "$APPLY_OPERATION_OPTIMIZATIONS" == "1" ]]; then
    WORKFLOW_ARGS+=(--apply-operation-optimizations)
  fi
  if [[ "$CHECK_LIMIT" != "0" ]]; then
    WORKFLOW_ARGS+=(--check-limit "$CHECK_LIMIT")
  fi
  if [[ -n "$HUMAN_REVIEW_FILE" ]]; then
    WORKFLOW_ARGS+=(--human-review "$HUMAN_REVIEW_FILE")
  fi
  for pattern_json in "${PATTERN_RELEASE_JSONS[@]}"; do
    WORKFLOW_ARGS+=(--pattern-release-json "$pattern_json")
  done
fi
if [[ -n "$BALANCE_REPORT_JSON" ]]; then
  WORKFLOW_ARGS+=(--balance-report-json "$BALANCE_REPORT_JSON")
fi
if [[ -n "$CONVERGENCE_AUDIT_JSON" ]]; then
  WORKFLOW_ARGS+=(--convergence-audit-json "$CONVERGENCE_AUDIT_JSON")
fi
if [[ -n "$APPLICATION_GATES_JSON" ]]; then
  WORKFLOW_ARGS+=(--application-gates-json "$APPLICATION_GATES_JSON")
fi
if [[ -n "$DEVELOPMENT_CYCLE" ]]; then
  WORKFLOW_ARGS+=(--development-cycle "$DEVELOPMENT_CYCLE")
fi
./run_workflow.sh "${WORKFLOW_ARGS[@]}"
