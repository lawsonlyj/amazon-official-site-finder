SOURCE_CSV ?=
RUN_DIR ?= outputs/my_run
LABELS ?= tests/fixtures/golden_expected_websites.csv
PYTHONPATH_VALUE ?= .vendor_eval:.

.PHONY: help test install-optional pipeline second-pass verify

help:
	@echo "Targets:"
	@echo "  test              Run unit tests and compile checks"
	@echo "  install-optional  Install optional dependencies into .vendor_eval"
	@echo "  pipeline          Run full workflow; requires SOURCE_CSV=/path/to/input.csv"
	@echo "  second-pass       Re-run unresolved second-pass for RUN_DIR"
	@echo "  verify            Verify final handoff outputs for RUN_DIR"
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
	  --second-pass-accept-threshold 70 \
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
	  --accept-threshold 70 \
	  --write-xlsx

verify:
	python3 tools/verify_run_outputs.py \
	  --run-dir "$(RUN_DIR)" \
	  --final provider_final_official_websites_second_pass.csv \
	  --unresolved provider_unresolved_second_pass.csv \
	  --quality quality_gate_provider_second_pass_final.json \
	  --xlsx "$(RUN_DIR)/provider_official_websites_second_pass_with_clickable_links.xlsx"
