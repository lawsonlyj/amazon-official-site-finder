import csv
import io
import json
import os
import tempfile
import time
import unittest
import urllib.error
import zipfile
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from finder import http
from finder.audit import audit_results
from finder.doctor import doctor
from finder.finalize import finalize_results, finalize_rows
from finder.input_normalizer import normalize_provider_rows
from finder.query_builder import build_queries
from finder.html_extract import extract_html
from finder.scoring import choose_best, is_excluded_domain, load_config, _extract_page, _text_similarity, _urls_to_fetch
from finder import search_sources
from finder.search_sources import SearchCandidate
from finder.text import url_like_candidates
from finder.logo import extract_logo_urls, hash_similarity
from finder.cli import limit_candidates_for_scoring, read_done_provider_ids, run_workflow
from tools.evaluate_labeled_results import evaluate as evaluate_labeled
from tools.build_review_sheet import build_review_sheet
from tools.build_manual_review_task import build_manual_review_task
from tools.quality_gate import evaluate_quality_gate
from tools.apply_review import apply_review
from tools.run_review_learning import run_review_learning
from tools.enrich_result_links import enrich_result_links
from tools.run_pipeline import PipelineError, run_pipeline
from tools.preflight_report import build_preflight_report, render_markdown
from tools.build_linked_workbook import build_workbook
from tools.plan_unresolved_second_pass import build_second_pass_plan
from tools.verify_run_outputs import verify_run_outputs
from tools.run_unresolved_second_pass import run_unresolved_second_pass, _accepted
from tools.configure_env_from_key_files import extract_key_from_file, main as configure_env_main
from tools.deduplicate_input import deduplicate_input
from tools.run_agent_b_verification import run_agent_b_verification
from tools.run_agent_b_recommendations import run_agent_b_recommendations
from tools.apply_agent_optimizations import apply_agent_optimizations
from tools.run_check_agent import run_check_agent
from tools.run_optimization_agent import run_optimization_agent
from tools.build_development_cycle_report import build_development_cycle_report
from tools.evaluate_workflow_balance import evaluate_balance, evaluate_balance_from_details
from tools.build_balance_report import build_balance_report
from tools.build_calibration_label_gap_task import build_calibration_label_gap_task
from tools.build_protected_lane_review_task import build_protected_lane_review_task
from tools.build_protected_lane_priority_task import build_protected_lane_priority_task
from tools.build_convergence_audit import build_convergence_audit
from tools.build_calibration_regression_cases import build_calibration_regression_cases
from tools.build_calibration_review_sample import build_calibration_review_sample
from tools.build_calibration_status_report import build_calibration_status_report
from tools.check_calibration_application_gate import check_calibration_application_gate
from tools.evaluate_calibration_review_sample import evaluate_calibration_review_sample
from tools.mine_evidence_patterns import features_for_review_agent_row, mine_evidence_patterns
from tools.run_calibration_cycle import run_calibration_cycle
from tools.run_calibration_followup import run_calibration_followup
from tools.run_calibration_regression_gate import run_calibration_regression_gate
from tools.simulate_pattern_release import simulate_pattern_release
from tools.apply_pattern_release_experiment import apply_pattern_release_experiment
from tools.apply_pattern_release_to_run import apply_pattern_release_to_run
from tools.simulate_review_lane_output_policy import simulate_review_lane_output_policy
from tools.build_release_policy_report import build_release_policy_report
from tools.build_threshold_boundary_report import build_threshold_boundary_report
from tools.verify_protected_lane_review_task import verify_protected_lane_review_task
from tools.reuse_historical_labels_for_task import reuse_historical_labels_for_task
from tools.apply_calibration_regression_cases import apply_calibration_regression_cases
from tools.build_policy_validation_task import build_policy_validation_task
from tools.evaluate_policy_validation_task import evaluate_policy_validation_task
from tools.build_visual_verification_task import build_visual_verification_task
from tools.apply_visual_verification import apply_visual_verification
from tools.output_layout import (
    DEFAULT_MATCHED_REVIEW_CONFIDENCE_CUTOFF,
    DEFAULT_SECOND_PASS_ACCEPT_THRESHOLD,
    DEFAULT_SECOND_PASS_REVIEW_CONFIDENCE_CUTOFF,
    WORKFLOW_VERSION,
)

def _write_test_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

class _FakeJsonClient:
    def __init__(self, responses: list[dict]):
        self.responses = list(responses)
        self.payloads: list[dict] = []

    def complete_json(self, *, system_prompt: str, user_payload: dict, max_tokens: int = 1400):
        self.payloads.append({"system_prompt": system_prompt, "user_payload": user_payload, "max_tokens": max_tokens})
        return self.responses.pop(0)

__all__ = [name for name in globals() if not name.startswith("__")]
