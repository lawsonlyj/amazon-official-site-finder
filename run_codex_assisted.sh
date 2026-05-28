#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat >&2 <<'USAGE'
Usage:
  ./run_codex_assisted.sh \
    --brave-key-file /path/to/brave_key.txt \
    --exa-key-file /path/to/exa_key.txt \
    --source /path/to/provider_details.csv

Optional:
  --run-dir outputs/my_run
  --labels /path/to/golden_expected_websites.csv
USAGE
}

BRAVE_KEY_FILE=""
EXA_KEY_FILE=""
SOURCE_CSV=""
RUN_DIR=""
LABELS_CSV=""

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

python3 tools/configure_env_from_key_files.py "${CONFIGURE_ARGS[@]}"

if [[ -n "$LABELS_CSV" ]]; then
  ./run_workflow.sh "$SOURCE_CSV" "$RUN_DIR" "$LABELS_CSV"
else
  ./run_workflow.sh "$SOURCE_CSV" "$RUN_DIR"
fi
