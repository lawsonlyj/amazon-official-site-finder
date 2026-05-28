SOURCE_CSV := /Users/luojianyin/Documents/Codex/2026-05-22/https-sellercentral-europe-amazon-com-gspn/gspn_output_v3_live_20260522_192420/final_csv_with_descriptions/provider_details_final_with_field_descriptions.csv
NORMALIZED := outputs/providers_normalized.csv
LABELS := tests/fixtures/golden_expected_websites.csv
RUN_DIR := outputs/production_run
CURRENT_RUN_DIR := outputs/production_run_brave_twostage_v3
NO_PROD_KEYS := SERPAPI_API_KEY= BRAVE_API_KEY= TAVILY_API_KEY= SERPER_API_KEY= FIRECRAWL_API_KEY= EXA_API_KEY=

.PHONY: help test install-optional prepare doctor preflight sample ddgs-sample eval-sample eval-ddgs finalize-demo batch-demo tool-eval summarize-tool-eval rescore-ddgs-10 quality-demo quality-ddgs-10 review-demo apply-review-demo pipeline-demo pipeline build-xlsx second-pass rebuild-current-from-evidence verify-current verify-current-second-pass plan-unresolved

help:
	@echo "Targets:"
	@echo "  test                 Run unit tests"
	@echo "  install-optional     Install optional tools into .vendor_eval"
	@echo "  prepare              Normalize source CSV"
	@echo "  doctor               Check search-source configuration"
	@echo "  preflight            Write production readiness handoff report"
	@echo "  sample               Run 5-row no-search-key smoke test"
	@echo "  ddgs-sample          Run 2-row DDGS exploratory sample"
	@echo "  eval-sample          Evaluate sample_results.csv against golden labels"
	@echo "  eval-ddgs            Evaluate sample_ddgs_results.csv against golden labels"
	@echo "  finalize-demo        Merge sample results with manual review demo decisions"
	@echo "  batch-demo           Demonstrate resume/append batch behavior"
	@echo "  tool-eval            Run ddgs+trafilatura tool evaluation"
	@echo "  summarize-tool-eval  Summarize tool evaluation output"
	@echo "  rescore-ddgs-10      Rescore saved 10-row DDGS evidence after config changes"
	@echo "  quality-demo         Run quality gate on finalized demo output"
	@echo "  quality-ddgs-10      Run quality gate on rescored DDGS 10-row sample"
	@echo "  review-demo          Build enhanced review sheet from pipeline demo evidence"
	@echo "  apply-review-demo    Apply manual review decisions to pipeline demo"
	@echo "  pipeline-demo        Run end-to-end reusable pipeline demo"
	@echo "  pipeline             Run reusable production pipeline; override SOURCE_CSV/RUN_DIR"
	@echo "  ./run_workflow.sh    Simplest portable command: input CSV + output run dir"
	@echo "  build-xlsx           Build clickable XLSX from RUN_DIR outputs"
	@echo "  second-pass          Run unresolved second-pass discovery for RUN_DIR"
	@echo "  rebuild-current-from-evidence  Rebuild current run from saved evidence"
	@echo "  verify-current       Verify current production run handoff artifacts"
	@echo "  verify-current-second-pass  Verify current second-pass handoff artifacts"
	@echo "  plan-unresolved      Build second-pass plan for unresolved rows"

test:
	python3 -m unittest discover -s tests
	python3 -m compileall finder tests tools

install-optional:
	python3 -m pip install --target .vendor_eval -r requirements-optional.txt

prepare:
	python3 -m finder.cli prepare --input "$(SOURCE_CSV)" --output "$(NORMALIZED)"

doctor:
	python3 -m finder.cli doctor --input "$(NORMALIZED)"

preflight:
	PYTHONPATH=.vendor_eval:. python3 tools/preflight_report.py --source "$(SOURCE_CSV)" --run-dir outputs/production_run --labels "$(LABELS)" --soft-fail

sample:
	$(NO_PROD_KEYS) DDGS_ENABLED=0 python3 -m finder.cli run --input "$(NORMALIZED)" --output outputs/sample_results.csv --evidence evidence/sample_evidence.jsonl --limit 5 --per-query 5
	python3 -m finder.cli audit-results --input outputs/sample_results.csv --review-output outputs/sample_review_queue.csv

ddgs-sample:
	$(NO_PROD_KEYS) DDGS_ENABLED=1 FINDER_HTTP_TIMEOUT=8 PYTHONPATH=.vendor_eval:. python3 -m finder.cli run --input "$(NORMALIZED)" --output outputs/sample_ddgs_results.csv --evidence evidence/sample_ddgs_evidence.jsonl --limit 2 --per-query 3 --max-candidates 20
	python3 -m finder.cli audit-results --input outputs/sample_ddgs_results.csv --review-output outputs/sample_ddgs_review_queue.csv

eval-sample:
	python3 tools/evaluate_labeled_results.py --labels "$(LABELS)" --results outputs/sample_results.csv --output-md outputs/labeled_eval_sample_results.md --output-json outputs/labeled_eval_sample_results.json

eval-ddgs:
	python3 tools/evaluate_labeled_results.py --labels "$(LABELS)" --results outputs/sample_ddgs_results.csv --output-md outputs/labeled_eval_sample_ddgs_results.md --output-json outputs/labeled_eval_sample_ddgs_results.json

finalize-demo:
	python3 -m finder.cli finalize-results --input outputs/sample_results.csv --review tests/fixtures/manual_review_demo.csv --output outputs/final_demo_official_websites.csv --unresolved-output outputs/final_demo_unresolved.csv
	python3 tools/evaluate_labeled_results.py --labels "$(LABELS)" --results outputs/final_demo_official_websites.csv --output-md outputs/labeled_eval_final_demo.md --output-json outputs/labeled_eval_final_demo.json

batch-demo:
	$(NO_PROD_KEYS) DDGS_ENABLED=0 python3 -m finder.cli run --input "$(NORMALIZED)" --output outputs/batch_demo_results.csv --evidence evidence/batch_demo_evidence.jsonl --offset 0 --limit 2 --per-query 2 --max-candidates 20
	$(NO_PROD_KEYS) DDGS_ENABLED=0 python3 -m finder.cli run --input "$(NORMALIZED)" --output outputs/batch_demo_results.csv --evidence evidence/batch_demo_evidence.jsonl --offset 0 --limit 3 --per-query 2 --resume --max-candidates 20
	python3 -m finder.cli audit-results --input outputs/batch_demo_results.csv --review-output outputs/batch_demo_review_queue.csv

tool-eval:
	PYTHONPATH=.vendor_eval:. python3 tools/evaluate_tools.py --input "$(NORMALIZED)" --output outputs/tool_eval_ddgs_trafilatura.csv --limit 5 --max-results 5

summarize-tool-eval:
	python3 tools/summarize_tool_eval.py --input outputs/tool_eval_ddgs_trafilatura.csv --output-md outputs/tool_eval_summary.md --output-json outputs/tool_eval_summary.json

rescore-ddgs-10:
	PYTHONPATH=.vendor_eval:. python3 tools/rescore_evidence.py --providers "$(NORMALIZED)" --evidence evidence/ddgs_10_capped_evidence.jsonl --output outputs/ddgs_10_capped_rescored.csv
	python3 -m finder.cli audit-results --input outputs/ddgs_10_capped_rescored.csv --review-output outputs/ddgs_10_capped_rescored_review_queue.csv

quality-demo:
	python3 tools/quality_gate.py --results outputs/final_demo_official_websites.csv --labels "$(LABELS)" --expected-rows 5 --min-domain-accuracy 1.0 --min-auto-precision 1.0 --min-official-url-rate 1.0 --max-unresolved-rate 0.0 --output-md outputs/quality_gate_final_demo.md --output-json outputs/quality_gate_final_demo.json

quality-ddgs-10:
	python3 tools/quality_gate.py --results outputs/ddgs_10_capped_rescored.csv --labels "$(LABELS)" --expected-rows 10 --min-domain-accuracy 1.0 --min-auto-precision 1.0 --min-official-url-rate 0.7 --max-unresolved-rate 0.5 --output-md outputs/quality_gate_ddgs_10_capped_rescored.md --output-json outputs/quality_gate_ddgs_10_capped_rescored.json

review-demo:
	python3 tools/build_review_sheet.py --results outputs/pipeline_demo/provider_official_websites.csv --evidence outputs/pipeline_demo/provider_official_websites_evidence.jsonl --output outputs/pipeline_demo/provider_review_sheet_enhanced.csv --top-candidates 5

apply-review-demo:
	python3 tools/apply_review.py --run-dir outputs/pipeline_demo --review tests/fixtures/manual_review_demo.csv --labels "$(LABELS)" --min-domain-accuracy 1.0 --min-auto-precision 1.0 --min-official-url-rate 1.0 --max-unresolved-rate 0.0

pipeline-demo:
	$(NO_PROD_KEYS) DDGS_ENABLED=0 PYTHONPATH=.vendor_eval:. python3 tools/run_pipeline.py --source "$(SOURCE_CSV)" --run-dir outputs/pipeline_demo --labels "$(LABELS)" --review-decisions tests/fixtures/manual_review_demo.csv --limit 5 --batch-size 2 --per-query 2 --max-queries 6 --max-candidates 10 --allow-exploratory --min-domain-accuracy 1.0 --min-auto-precision 1.0 --min-official-url-rate 1.0 --max-unresolved-rate 0.0

pipeline:
	PYTHONPATH=.vendor_eval:. python3 tools/run_pipeline.py --source "$(SOURCE_CSV)" --run-dir "$(RUN_DIR)" --labels "$(LABELS)" --batch-size 50 --per-query 3 --max-queries 6 --max-candidates 10 --resume --run-second-pass --second-pass-per-query 3 --second-pass-max-search-queries 6 --second-pass-accept-threshold 70 --second-pass-write-xlsx --min-domain-accuracy 0.8 --min-auto-precision 0.95 --min-official-url-rate 0.5 --max-unresolved-rate 0.6

build-xlsx:
	python3 tools/build_linked_workbook.py --sheet Final="$(RUN_DIR)/provider_final_official_websites.csv" --sheet Auto_Results="$(RUN_DIR)/provider_official_websites_enriched.csv" --sheet Review_Queue="$(RUN_DIR)/provider_review_sheet_enhanced.csv" --output "$(RUN_DIR)/provider_official_websites_final_with_clickable_links.xlsx"

second-pass:
	PYTHONPATH=.vendor_eval:. python3 tools/run_unresolved_second_pass.py --run-dir "$(RUN_DIR)" --labels "$(LABELS)" --per-query 3 --max-search-queries 6 --accept-threshold 70 --write-xlsx

rebuild-current-from-evidence:
	PYTHONPATH=.vendor_eval:. python3 tools/rebuild_from_evidence.py --run-dir "$(CURRENT_RUN_DIR)" --labels "$(LABELS)" --expected-rows 1184 --build-xlsx

verify-current:
	python3 tools/verify_run_outputs.py --run-dir "$(CURRENT_RUN_DIR)" --expected-rows 1184 --expected-unresolved 453 --xlsx "$(CURRENT_RUN_DIR)/provider_official_websites_final_with_clickable_links.xlsx"

verify-current-second-pass:
	python3 tools/verify_run_outputs.py --run-dir "$(CURRENT_RUN_DIR)" --final provider_final_official_websites_second_pass.csv --unresolved provider_unresolved_second_pass.csv --quality quality_gate_provider_second_pass_final.json --expected-rows 1184 --expected-unresolved 165 --xlsx "$(CURRENT_RUN_DIR)/provider_official_websites_second_pass_with_clickable_links.xlsx"

plan-unresolved:
	python3 tools/plan_unresolved_second_pass.py --run-dir "$(CURRENT_RUN_DIR)" --output "$(CURRENT_RUN_DIR)/unresolved_second_pass_plan.csv"
