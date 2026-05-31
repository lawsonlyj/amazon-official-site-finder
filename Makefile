SOURCE_CSV ?=
RUN_DIR ?= outputs/my_run
LABELS ?= tests/fixtures/golden_expected_websites.csv
REVIEW ?=
PYTHONPATH_VALUE ?= .vendor_eval:.

.PHONY: help test install-optional pipeline second-pass review-task check-suggestion agent-b review-learning check-suggestions agent-b-suggestions agent-c apply-operation-optimizations apply-agent-optimizations verify

help:
	@echo "Targets:"
	@echo "  Workflow body:"
	@echo "  test              Run unit tests and compile checks"
	@echo "  install-optional  Install optional dependencies into .vendor_eval"
	@echo "  pipeline          Run full workflow; requires SOURCE_CSV=/path/to/input.csv"
	@echo "  second-pass       Re-run unresolved second-pass for RUN_DIR"
	@echo "  review-task       Rebuild simplified manual review CSV/XLSX for RUN_DIR"
	@echo "  review-learning   Apply filled REVIEW=/path/to/review.xlsx and write learning report"
	@echo "  verify            Verify final handoff outputs for RUN_DIR"
	@echo ""
	@echo "  Development workflow:"
	@echo "  check-suggestion  Run candidate-first high-risk checks for RUN_DIR"
	@echo "  check-suggestions Generate Check and Suggestion optimization suggestions for RUN_DIR"
	@echo "  apply-operation-optimizations Apply safe operation optimization suggestions"
	@echo ""
	@echo "Example:"
	@echo "  make pipeline SOURCE_CSV=/path/to/provider_details.csv RUN_DIR=outputs/my_run"

test:
	python3 -m unittest discover -s tests
	python3 -m compileall finder tests tools

install-optional:
	python3 -m pip install --target .vendor_eval -r requirements-optional.txt

pipeline:
	@if [ -z "$(SOURCE_CSV)" ]; then echo "Set SOURCE_CSV=/path/to/provider_details.csv"; exit 2; fi
	PYTHONPATH=$(PYTHONPATH_VALUE) python3 tools/run_pipeline.py \
	  --source "$(SOURCE_CSV)" \
	  --run-dir "$(RUN_DIR)" \
	  --labels "$(LABELS)" \
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

second-pass:
	PYTHONPATH=$(PYTHONPATH_VALUE) python3 tools/run_unresolved_second_pass.py \
	  --run-dir "$(RUN_DIR)" \
	  --labels "$(LABELS)" \
	  --per-query 3 \
	  --max-search-queries 6 \
	  --accept-threshold 75 \
	  --write-xlsx

review-task:
	PYTHONPATH=$(PYTHONPATH_VALUE) python3 tools/build_manual_review_task.py \
	  --run-dir "$(RUN_DIR)" \
	  --write-xlsx

check-suggestion:
	PYTHONPATH=$(PYTHONPATH_VALUE) python3 tools/run_agent_b_verification.py \
	  --run-dir "$(RUN_DIR)" \
	  --write-xlsx

agent-b: check-suggestion

review-learning:
	@if [ -z "$(REVIEW)" ]; then echo "Set REVIEW=/path/to/filled_manual_review.csv-or.xlsx"; exit 2; fi
	PYTHONPATH=$(PYTHONPATH_VALUE) python3 tools/run_review_learning.py \
	  --run-dir "$(RUN_DIR)" \
	  --review "$(REVIEW)" \
	  --labels "$(LABELS)" \
	  --write-xlsx \
	  --update-config

check-suggestions:
	PYTHONPATH=$(PYTHONPATH_VALUE) python3 tools/run_agent_b_recommendations.py \
	  --run-dir "$(RUN_DIR)"

agent-b-suggestions: check-suggestions

agent-c: check-suggestions

apply-operation-optimizations:
	PYTHONPATH=$(PYTHONPATH_VALUE) python3 tools/apply_agent_optimizations.py \
	  --run-dir "$(RUN_DIR)" \
	  --apply

apply-agent-optimizations: apply-operation-optimizations

verify:
	python3 tools/verify_run_outputs.py \
	  --run-dir "$(RUN_DIR)" \
	  --final official_sites.csv \
	  --unresolved unresolved.csv \
	  --quality quality.json \
	  --xlsx "$(RUN_DIR)/official_sites.xlsx"
