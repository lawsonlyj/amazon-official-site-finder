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
from finder.scoring import choose_best, is_excluded_domain, load_config, _extract_page, _text_similarity
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
from tools.run_agent_b_verification import run_agent_b_verification
from tools.run_agent_c_recommendations import run_agent_c_recommendations
from tools.apply_agent_optimizations import apply_agent_optimizations
from tools.evaluate_workflow_balance import evaluate_balance, evaluate_balance_from_details
from tools.build_balance_report import build_balance_report
from tools.build_calibration_label_gap_task import build_calibration_label_gap_task
from tools.build_calibration_review_sample import build_calibration_review_sample
from tools.build_calibration_status_report import build_calibration_status_report
from tools.evaluate_calibration_review_sample import evaluate_calibration_review_sample
from tools.mine_evidence_patterns import mine_evidence_patterns
from tools.run_calibration_cycle import run_calibration_cycle
from tools.simulate_pattern_release import simulate_pattern_release
from tools.apply_pattern_release_experiment import apply_pattern_release_experiment
from tools.apply_pattern_release_to_run import apply_pattern_release_to_run
from tools.build_release_policy_report import build_release_policy_report
from tools.build_threshold_boundary_report import build_threshold_boundary_report
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


class WorkflowTests(unittest.TestCase):
    def test_configure_env_from_key_files_reads_plain_and_env_style_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            brave_file = root / "brave.txt"
            exa_file = root / "exa.env"
            env_file = root / ".env"
            example_file = root / ".env.example"
            brave_file.write_text("brave-secret-key\n", encoding="utf-8")
            exa_file.write_text("EXA_API_KEY='exa-secret-key'\n", encoding="utf-8")
            example_file.write_text("BRAVE_API_KEY=\nEXA_API_KEY=\nDDGS_ENABLED=1\nFINDER_HTTP_TIMEOUT=12\n", encoding="utf-8")

            out = io.StringIO()
            with redirect_stdout(out):
                configure_env_main(
                    [
                        "--brave-key-file",
                        str(brave_file),
                        "--exa-key-file",
                        str(exa_file),
                        "--env",
                        str(env_file),
                        "--example",
                        str(example_file),
                    ]
                )

            text = env_file.read_text(encoding="utf-8")
            self.assertIn("BRAVE_API_KEY=brave-secret-key", text)
            self.assertIn("EXA_API_KEY=exa-secret-key", text)
            self.assertIn("FINDER_HTTP_TIMEOUT=12", text)
            self.assertNotIn("brave-secret-key", out.getvalue())
            self.assertNotIn("exa-secret-key", out.getvalue())

    def test_extract_key_from_json_key_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            key_file = Path(tmp) / "brave.json"
            key_file.write_text(json.dumps({"brave": {"api_key": "json-secret"}}), encoding="utf-8")

            self.assertEqual(extract_key_from_file(key_file, "BRAVE_API_KEY"), "json-secret")

    def test_extract_key_from_rtf_file_without_printing_secret(self):
        with tempfile.TemporaryDirectory() as tmp:
            key_file = Path(tmp) / "brave.rtf"
            key_file.write_text(r"{\rtf1\ansi BRAVE_API_KEY=rtf-secret-key\par}", encoding="utf-8")

            self.assertEqual(extract_key_from_file(key_file, "BRAVE_API_KEY"), "rtf-secret-key")

    def test_normalizer_merges_duplicate_provider_rows_and_skips_description_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "providers.csv"
            rows = [
                {
                    "provider_id": "Amazon SPN 服务商唯一 ID；主表关联键之一",
                    "provider_name": "服务商名称",
                    "service_api": "一级服务名称；主表关联键之一",
                    "detail_url": "详情页来源 URL",
                    "listing_logo_url": "列表页 logo URL",
                    "about_listing_text": "详情页 About this listing 区域文本",
                    "service_description": "详情页 Service Description 文本",
                    "service_types_json": "[]",
                    "provider_locations_json": "[]",
                    "provider_languages_json": "[]",
                },
                {
                    "provider_id": "p-1",
                    "provider_name": "Example Agency LLC",
                    "service_api": "Account Management",
                    "detail_url": "https://sellercentral-europe.amazon.com/gspn/provider-details/a/p-1",
                    "listing_logo_url": "https://m.media-amazon.com/logo.png",
                    "about_listing_text": "Amazon account management.",
                    "service_description": "Account management services.",
                    "service_types_json": json.dumps(["Complete Account Management"]),
                    "provider_locations_json": json.dumps(["United Kingdom"]),
                    "provider_languages_json": json.dumps(["English"]),
                },
                {
                    "provider_id": "p-1",
                    "provider_name": "Example Agency LLC",
                    "service_api": "Advertising Optimization",
                    "detail_url": "https://sellercentral-europe.amazon.com/gspn/provider-details/b/p-1",
                    "listing_logo_url": "https://m.media-amazon.com/logo.png",
                    "about_listing_text": "Amazon advertising.",
                    "service_description": "Advertising services.",
                    "service_types_json": json.dumps(["PPC"]),
                    "provider_locations_json": json.dumps(["United Kingdom"]),
                    "provider_languages_json": json.dumps(["English", "German"]),
                },
            ]
            with source.open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=rows[0].keys())
                writer.writeheader()
                writer.writerows(rows)

            providers = normalize_provider_rows(source)

        self.assertEqual(len(providers), 1)
        self.assertEqual(providers[0]["provider_id"], "p-1")
        self.assertEqual(providers[0]["source_rows"], 2)
        self.assertEqual(providers[0]["service_apis"], ["Account Management", "Advertising Optimization"])
        self.assertEqual(providers[0]["provider_languages"], ["English", "German"])

    def test_query_builder_includes_web_and_github_queries(self):
        provider = {
            "provider_name": "Example Agency LLC",
            "service_apis": ["Account Management"],
            "provider_locations": ["United Kingdom"],
        }

        queries = build_queries(provider)

        self.assertIn('"Example Agency LLC" official website', queries)
        self.assertIn('"Example Agency LLC" "United Kingdom" website', queries)
        self.assertIn('site:github.com "Example Agency LLC"', queries)

    def test_query_builder_adds_country_language_terms(self):
        provider = {
            "provider_name": "Akkountweb",
            "service_apis": ["Account Management"],
            "provider_locations": ["Italy"],
        }

        queries = build_queries(provider)

        self.assertIn('"Akkountweb" "sito ufficiale"', queries)
        self.assertIn('"Akkountweb" "contatti"', queries)
        self.assertIn('"Akkountweb" "agenzia amazon"', queries)

    def test_scoring_rejects_excluded_domains_and_selects_official_site(self):
        provider = {
            "provider_id": "p-1",
            "provider_name": "Example Agency LLC",
            "service_apis": ["Account Management"],
            "provider_locations": ["United Kingdom"],
        }
        candidates = [
            SearchCandidate(
                url="https://www.linkedin.com/company/example-agency",
                title="Example Agency LinkedIn",
                source="serpapi",
                query='"Example Agency LLC" official website',
                rank=1,
            ),
            SearchCandidate(
                url="https://www.scribd.com/document/example-agency",
                title="Example Agency LLC document",
                source="serpapi",
                query='"Example Agency LLC" official website',
                rank=2,
            ),
            SearchCandidate(
                url="https://www.exampleagency.com",
                title="Example Agency LLC - Amazon Seller Central services",
                snippet="Official Amazon marketplace account management agency in United Kingdom.",
                source="serpapi",
                query='"Example Agency LLC" official website',
                rank=3,
            ),
        ]

        def fake_fetch(url):
            html = """
            <html>
              <head><title>Example Agency LLC</title></head>
              <body>
                <h1>Example Agency LLC</h1>
                <p>Amazon Seller Central account management, PPC, marketplace compliance,
                FBA catalog services and ecommerce support in the United Kingdom.</p>
                <a href="/about">About us</a><a href="/contact">Contact us</a>
              </body>
            </html>
            """
            return {"ok": True, "status": 200, "final_url": url, "text": html}

        with patch("finder.scoring.fetch_text", side_effect=fake_fetch):
            result = choose_best(provider, candidates, load_config("config/scoring.json"))

        self.assertEqual(result["status"], "matched")
        self.assertEqual(result["official_domain"], "exampleagency.com")
        self.assertGreaterEqual(result["confidence"], 75)
        self.assertTrue(is_excluded_domain("www.linkedin.com", load_config("config/scoring.json")))
        self.assertTrue(
            is_excluded_domain(
                "https://www.linkedin.com/in/example-agency-owner",
                load_config("config/scoring.json"),
            )
        )
        self.assertTrue(is_excluded_domain("https://x.com/example", load_config("config/scoring.json")))
        self.assertFalse(is_excluded_domain("https://gozenix.com", load_config("config/scoring.json")))

    def test_domain_guess_without_service_relevance_needs_review_not_auto_match(self):
        provider = {
            "provider_id": "p-2",
            "provider_name": "Plain Name",
            "service_apis": ["Account Management"],
            "provider_locations": ["United States of America"],
        }
        candidates = [
            SearchCandidate(
                url="https://plainname.com",
                title="domain guess",
                source="domain_guess",
                query="Plain Name",
                rank=1,
            )
        ]

        def fake_fetch(url):
            html = """
            <html><head><title>Plain Name</title></head>
            <body><h1>Plain Name</h1><p>Contact us. About us. Privacy policy.</p></body></html>
            """
            return {"ok": True, "status": 200, "final_url": url, "text": html}

        with patch("finder.scoring.fetch_text", side_effect=fake_fetch):
            result = choose_best(provider, candidates, load_config("config/scoring.json"))

        self.assertEqual(result["status"], "needs_review")
        self.assertEqual(result["official_domain"], "plainname.com")
        self.assertLess(result["confidence"], 75)

    def test_identity_caps_block_country_and_industry_same_name_false_positive(self):
        provider = {
            "provider_id": "p-bfarm",
            "provider_name": "BFarm",
            "service_apis": ["Account Management"],
            "provider_locations": ["Ukraine"],
        }
        candidate = SearchCandidate(
            url="https://www.bfarm.de/DE/Home/_node.html",
            title="BFarm - Federal Institute",
            snippet="BFarm Federal Institute for drugs and medical devices",
            source="brave",
            query='"BFarm" official website',
            rank=1,
        )

        def fake_fetch(url):
            html = """
            <html><head><title>BFarm Federal Institute</title></head>
            <body>BFarm is a government agency for medicines, medical devices,
            pharmaceutical safety and health authority services. Contact us. About us.</body></html>
            """
            return {"ok": True, "status": 200, "final_url": url, "text": html}

        with patch("finder.scoring.fetch_text", side_effect=fake_fetch):
            result = choose_best(provider, [candidate], load_config("config/scoring.json"))

        self.assertEqual(result["status"], "needs_review")
        self.assertEqual(result["official_url"], "https://www.bfarm.de/")
        self.assertLess(result["confidence"], 75)
        self.assertIn("identity_cap_industry_mismatch_without_service", result["evidence_summary"])
        self.assertIn("identity_cap_country_conflict_without_service", result["evidence_summary"])

    def test_country_conflict_requires_provider_country_corroboration(self):
        provider = {
            "provider_id": "p-armr",
            "provider_name": "ARMR",
            "service_apis": ["Account Management"],
            "provider_locations": ["United States of America"],
        }
        candidate = SearchCandidate(
            url="https://www.armr.in/",
            title="ARMR official website",
            snippet="ARMR Amazon marketplace account management services",
            source="brave",
            query='"ARMR" official website',
            rank=1,
        )

        def fake_fetch(url):
            html = """
            <html><head><title>ARMR</title></head>
            <body>ARMR provides Amazon marketplace account management and seller
            advertising services. Contact us. About us.</body></html>
            """
            return {"ok": True, "status": 200, "final_url": url, "text": html}

        with patch("finder.scoring.fetch_text", side_effect=fake_fetch):
            result = choose_best(provider, [candidate], load_config("config/scoring.json"))

        self.assertEqual(result["status"], "needs_review")
        self.assertLess(result["confidence"], 75)
        self.assertIn("identity_cap_country_conflict_needs_review", result["evidence_summary"])

    def test_ambiguous_name_with_page_name_and_weak_service_can_auto_match(self):
        provider = {
            "provider_id": "p-bluepace",
            "provider_name": "Bluepace",
            "service_apis": ["Account Management"],
            "provider_locations": ["Germany"],
        }
        candidate = SearchCandidate(
            url="https://bluepace.de/",
            title="Bluepace official website",
            snippet="Bluepace marketplace support Germany",
            source="brave",
            query='"Bluepace" official website',
            rank=1,
        )

        def fake_fetch(url):
            html = """
            <html><head><title>Bluepace</title></head>
            <body>Bluepace supports marketplace sellers in Germany.
            Contact us. About us.</body></html>
            """
            return {"ok": True, "status": 200, "final_url": url, "text": html}

        with patch("finder.scoring.fetch_text", side_effect=fake_fetch):
            result = choose_best(provider, [candidate], load_config("config/scoring.json"))

        self.assertEqual(result["status"], "matched")
        self.assertNotIn("identity_cap_ambiguous_name_requires_page_and_service", result["evidence_summary"])

    def test_two_stage_scoring_fetches_only_best_preliminary_candidates(self):
        provider = {
            "provider_id": "p-2",
            "provider_name": "Best Agency",
            "service_apis": ["Account Management"],
            "provider_locations": ["United Kingdom"],
        }
        candidates = [
            SearchCandidate(
                url="https://irrelevant.example",
                title="Unrelated result",
                source="brave",
                query='"Best Agency" official website',
                rank=1,
            ),
            SearchCandidate(
                url="https://bestagency.example",
                title="Best Agency official website",
                snippet="Amazon Seller Central marketplace support.",
                source="brave",
                query='"Best Agency" official website',
                rank=2,
            ),
            SearchCandidate(
                url="https://directory.example/best-agency",
                title="Best Agency directory profile",
                source="brave",
                query='"Best Agency" official website',
                rank=3,
            ),
        ]
        config = load_config("config/scoring.json")
        config["max_fetch_candidates"] = 1

        def fake_fetch(url):
            html = """
            <html><head><title>Best Agency</title></head>
            <body>Best Agency provides Amazon Seller Central marketplace account management,
            PPC, FBA compliance and catalog services in the United Kingdom. Contact us.</body></html>
            """
            return {"ok": True, "status": 200, "final_url": url, "text": html}

        with patch("finder.scoring.fetch_text", side_effect=fake_fetch) as fetch:
            result = choose_best(provider, candidates, config)

        fetched_urls = [call.args[0] for call in fetch.call_args_list]
        self.assertTrue(all("bestagency.example" in url for url in fetched_urls))
        self.assertEqual(result["status"], "matched")
        self.assertTrue(any("not_fetched_preliminary_score" in c["reasons"] for c in result["candidates"]))

    def test_optional_trafilatura_text_is_used_when_available(self):
        html = "<html><head><title>Thin</title></head><body><nav>Menu</nav></body></html>"
        with patch(
            "finder.scoring._extract_with_trafilatura",
            return_value="Example Agency LLC Amazon Seller Central marketplace account management services.",
        ):
            extracted = _extract_page(html, "https://example.com")

        self.assertIn("Amazon Seller Central", extracted["text"])

    def test_dynamic_rendering_can_rescue_javascript_only_candidate(self):
        provider = {
            "provider_id": "p-js",
            "provider_name": "JS Agency LLC",
            "service_apis": ["Account Management"],
            "provider_locations": ["United Kingdom"],
        }
        candidate = SearchCandidate(
            url="https://jsagency.example",
            title="JS Agency LLC",
            snippet="Official website",
            source="ddgs",
            query='"JS Agency LLC" official website',
            rank=1,
        )
        config = load_config("config/scoring.json")
        config["dynamic_rendering"]["enabled"] = True

        def fake_fetch(url):
            return {
                "ok": True,
                "status": 200,
                "final_url": "https://jsagency.example/",
                "text": "<html><body>JavaScript is required. Enable JavaScript.</body></html>",
            }

        def fake_render(url, timeout_ms=8000):
            return {
                "ok": True,
                "status": 200,
                "final_url": "https://jsagency.example/",
                "text": """
                <html><head><title>JS Agency LLC</title></head>
                <body>JS Agency LLC helps brands with Amazon Seller Central marketplace
                account management, PPC, FBA catalog compliance in the United Kingdom.
                Contact us. About us.</body></html>
                """,
            }

        with patch("finder.scoring.fetch_text", side_effect=fake_fetch), patch(
            "finder.scoring.render_dynamic_page", side_effect=fake_render
        ):
            result = choose_best(provider, [candidate], config)

        self.assertEqual(result["status"], "matched")
        self.assertIn("dynamic_rendered_page", result["evidence_summary"])
        self.assertIn("page_contains_amazon_service_keywords", result["evidence_summary"])

    def test_dynamic_rendering_missing_playwright_keeps_review_signal(self):
        provider = {
            "provider_id": "p-js",
            "provider_name": "JS Agency LLC",
            "service_apis": ["Account Management"],
            "provider_locations": ["United Kingdom"],
        }
        candidate = SearchCandidate(
            url="https://jsagency.example",
            title="JS Agency LLC",
            snippet="Official website",
            source="ddgs",
            query='"JS Agency LLC" official website',
            rank=1,
        )
        config = load_config("config/scoring.json")
        config["dynamic_rendering"]["enabled"] = True

        def fake_fetch(url):
            return {
                "ok": True,
                "status": 200,
                "final_url": "https://jsagency.example/",
                "text": "<html><body>JavaScript is required. Enable JavaScript.</body></html>",
            }

        def fake_render(url, timeout_ms=8000):
            return {"ok": False, "status": None, "final_url": url, "text": "", "error": "playwright_not_installed"}

        with patch("finder.scoring.fetch_text", side_effect=fake_fetch), patch(
            "finder.scoring.render_dynamic_page", side_effect=fake_render
        ):
            result = choose_best(provider, [candidate], config)

        self.assertEqual(result["status"], "low_confidence")
        self.assertIn("dynamic_render_unavailable", result["evidence_summary"])

    def test_text_similarity_handles_normalized_legal_suffixes(self):
        self.assertGreaterEqual(_text_similarity("9THSIGHT PRIVATE LIMITED", "9thsight"), 90)
        self.assertGreaterEqual(_text_similarity("A2Z-ECOM", "a2z-ecom"), 90)


class SearchSourceTests(unittest.TestCase):
    def test_serpapi_response_is_mapped_to_candidates(self):
        response = {
            "organic_results": [
                {"link": "https://example.com", "title": "Example", "snippet": "Official site"}
            ]
        }
        with patch.dict(os.environ, {"SERPAPI_API_KEY": "serp-test"}), patch(
            "finder.search_sources.request_json", return_value=response
        ) as request:
            candidates = search_sources._search_serpapi('"Example" official website', per_query=5)

        self.assertEqual(candidates[0].url, "https://example.com")
        self.assertEqual(candidates[0].source, "serpapi")
        self.assertIn("api_key=serp-test", request.call_args.args[0])

    def test_brave_response_uses_subscription_header(self):
        response = {"web": {"results": [{"url": "https://example.com", "title": "Example", "description": "Official"}]}}
        with patch.dict(os.environ, {"BRAVE_API_KEY": "brave-test"}), patch(
            "finder.search_sources.request_json", return_value=response
        ) as request:
            candidates = search_sources._search_brave('"Example" official website', per_query=5)

        self.assertEqual(candidates[0].url, "https://example.com")
        self.assertEqual(candidates[0].source, "brave")
        self.assertEqual(request.call_args.kwargs["headers"]["X-Subscription-Token"], "brave-test")

    def test_tavily_response_uses_bearer_authentication(self):
        response = {"results": [{"url": "https://example.com", "title": "Example", "raw_content": "Official raw"}]}
        with patch.dict(os.environ, {"TAVILY_API_KEY": "tvly-test"}), patch(
            "finder.search_sources.request_json", return_value=response
        ) as request:
            candidates = search_sources._search_tavily('"Example" official website', per_query=5)

        self.assertEqual(candidates[0].url, "https://example.com")
        self.assertEqual(candidates[0].snippet, "Official raw")
        self.assertEqual(candidates[0].source, "tavily")
        self.assertEqual(request.call_args.kwargs["headers"]["Authorization"], "Bearer tvly-test")
        self.assertEqual(request.call_args.kwargs["payload"]["include_raw_content"], "markdown")

    def test_serper_response_uses_api_key_header(self):
        response = {"organic": [{"link": "https://example.com", "title": "Example", "snippet": "Official"}]}
        with patch.dict(os.environ, {"SERPER_API_KEY": "serper-test"}), patch(
            "finder.search_sources.request_json", return_value=response
        ) as request:
            candidates = search_sources._search_serper('"Example" official website', per_query=5)

        self.assertEqual(candidates[0].url, "https://example.com")
        self.assertEqual(candidates[0].source, "serper")
        self.assertEqual(request.call_args.kwargs["headers"]["X-API-KEY"], "serper-test")

    def test_firecrawl_response_uses_bearer_authentication(self):
        response = {
            "data": {
                "web": [
                    {
                        "url": "https://example.com",
                        "title": "Example",
                        "description": "Official",
                        "markdown": "# Example",
                    }
                ]
            }
        }
        with patch.dict(os.environ, {"FIRECRAWL_API_KEY": "fc-test"}), patch(
            "finder.search_sources.request_json", return_value=response
        ) as request:
            candidates = search_sources._search_firecrawl('"Example" official website', per_query=5)

        self.assertEqual(candidates[0].url, "https://example.com")
        self.assertEqual(candidates[0].source, "firecrawl")
        self.assertEqual(request.call_args.kwargs["headers"]["Authorization"], "Bearer fc-test")
        self.assertEqual(request.call_args.kwargs["payload"]["scrapeOptions"]["formats"], [{"type": "markdown"}])

    def test_exa_response_uses_api_key_and_content_fields(self):
        response = {
            "results": [
                {
                    "url": "https://example.com",
                    "title": "Example",
                    "text": "Official website text",
                    "highlights": ["Amazon service provider"],
                }
            ]
        }
        with patch.dict(os.environ, {"EXA_API_KEY": "exa-test"}, clear=True), patch(
            "finder.search_sources.request_json", return_value=response
        ) as request:
            candidates = search_sources._search_exa('"Example" official website', per_query=5)

        self.assertEqual(candidates[0].url, "https://example.com")
        self.assertEqual(candidates[0].source, "exa")
        self.assertIn("Amazon service provider", candidates[0].snippet)
        self.assertEqual(request.call_args.kwargs["headers"]["x-api-key"], "exa-test")
        self.assertIn("contents", request.call_args.kwargs["payload"])

    def test_ddgs_response_is_mapped_to_candidates(self):
        response = [{"href": "https://example.com", "title": "Example", "body": "Official site"}]
        with patch("finder.search_sources._ddgs_text", return_value=response):
            candidates = search_sources._search_ddgs('"Example" official website', per_query=5)

        self.assertEqual(candidates[0].url, "https://example.com")
        self.assertEqual(candidates[0].source, "ddgs")

    def test_search_failures_return_empty_candidates(self):
        with patch("sys.stderr"), patch(
            "finder.search_sources._search_serpapi", side_effect=RuntimeError("quota exceeded")
        ):
            candidates = search_sources._safe_search(
                "serpapi",
                '"Example" official website',
                lambda: search_sources._search_serpapi('"Example" official website', per_query=5),
            )

        self.assertEqual(candidates, [])

    def test_safe_search_retries_transient_timeouts(self):
        calls = {"count": 0}

        def flaky():
            calls["count"] += 1
            if calls["count"] == 1:
                raise TimeoutError("timed out")
            return [SearchCandidate(url="https://example.com", source="ddgs")]

        with patch.dict(
            os.environ,
            {"FINDER_SEARCH_RETRIES": "1", "FINDER_SEARCH_RETRY_DELAY": "0"},
            clear=True,
        ):
            candidates = search_sources._safe_search("ddgs", '"Example" official website', flaky)

        self.assertEqual(calls["count"], 2)
        self.assertEqual(candidates[0].url, "https://example.com")

    def test_search_source_smoke_test_sanitizes_key_in_errors(self):
        with patch.dict(os.environ, {"SERPAPI_API_KEY": "secret-test-key"}, clear=True), patch(
            "finder.search_sources.request_json", side_effect=RuntimeError("bad secret-test-key")
        ):
            result = search_sources.smoke_test_source("serpapi")

        self.assertFalse(result["ok"])
        self.assertEqual(result["source"], "serpapi")
        self.assertNotIn("secret-test-key", result["error"])
        self.assertIn("[redacted]", result["error"])

    def test_request_json_retries_transient_url_errors(self):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return b'{"ok": true}'

        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ,
            {
                "FINDER_CACHE_DIR": tmp,
                "FINDER_SEARCH_RETRIES": "1",
                "FINDER_SEARCH_RETRY_DELAY": "0",
            },
            clear=True,
        ), patch(
            "urllib.request.urlopen",
            side_effect=[urllib.error.URLError("temporary dns failure"), Response()],
        ) as urlopen:
            result = http.request_json("https://api.example.test/search", use_cache=False)

        self.assertEqual(result, {"ok": True})
        self.assertEqual(urlopen.call_count, 2)

    def test_collect_candidates_can_limit_generated_queries(self):
        provider = {
            "provider_name": "Example Agency LLC",
            "service_apis": ["Account Management"],
            "provider_locations": ["United Kingdom"],
        }
        with patch.dict(os.environ, {"DDGS_ENABLED": "1"}, clear=True), patch(
            "finder.search_sources._ddgs_text",
            return_value=[{"href": "https://example.com", "title": "Example", "body": "Official"}],
        ) as ddgs:
            search_sources.collect_candidates(provider, per_query=1, max_queries=2)

        self.assertEqual(ddgs.call_count, 2)

    def test_collect_candidates_for_queries_runs_exa_source_specific_queries(self):
        with patch.dict(os.environ, {"EXA_API_KEY": "exa-test"}, clear=True), patch(
            "finder.search_sources._search_exa",
            return_value=[SearchCandidate(url="https://example.com", source="exa")],
        ) as exa:
            candidates = search_sources.collect_candidates_for_queries(
                ['"Example" official website'],
                per_query=1,
                source_queries={"exa": ["official company website for Example"]},
            )

        self.assertEqual(exa.call_count, 2)
        self.assertEqual(candidates[0].source, "exa")

    def test_collect_candidates_for_queries_can_skip_exa_for_base_queries(self):
        with patch.dict(os.environ, {"EXA_API_KEY": "exa-test"}, clear=True), patch(
            "finder.search_sources._search_exa",
            return_value=[SearchCandidate(url="https://example.com", source="exa")],
        ) as exa:
            candidates = search_sources.collect_candidates_for_queries(
                ['"Example" official website'],
                per_query=1,
                source_queries={"exa": ["official company website for Example"]},
                skip_sources={"exa"},
            )

        self.assertEqual(exa.call_count, 1)
        self.assertEqual(candidates[0].source, "exa")

    def test_collect_candidates_stops_when_all_production_search_calls_fail(self):
        provider = {
            "provider_name": "Example Agency LLC",
            "service_apis": ["Account Management"],
            "provider_locations": ["United Kingdom"],
        }
        with patch.dict(os.environ, {"BRAVE_API_KEY": "brave-test"}, clear=True), patch(
            "finder.search_sources.request_json", side_effect=RuntimeError("dns failure")
        ), patch("sys.stderr"):
            with self.assertRaises(RuntimeError) as ctx:
                search_sources.collect_candidates(provider, per_query=1, max_queries=2)

        self.assertIn("domain-guess-only degradation", str(ctx.exception))

    def test_url_like_candidates_accepts_hyphenated_multilevel_domains(self):
        self.assertIn("grow-business.co.uk", url_like_candidates("Official: grow-business.co.uk"))

    def test_dedupe_candidates_skips_invalid_urls(self):
        candidates = search_sources.dedupe_candidates(
            [
                SearchCandidate(url="https://example.com", source="test"),
                SearchCandidate(url="https://[invalid", source="test"),
            ]
        )

        self.assertEqual([candidate.url for candidate in candidates], ["https://example.com"])


class OperationalCommandTests(unittest.TestCase):
    def test_doctor_reports_missing_input_and_unconfigured_sources(self):
        with patch.dict(os.environ, {}, clear=True):
            result = doctor("/does/not/exist.csv")

        self.assertFalse(result["input_exists"])
        self.assertFalse(result["production_ready"])
        self.assertEqual(result["configured_sources"], [])
        self.assertTrue(any("No search sources" in note for note in result["notes"]))

    def test_doctor_marks_serper_as_production_ready_and_ddgs_as_exploratory(self):
        with patch.dict(os.environ, {"SERPER_API_KEY": "serper-test", "DDGS_ENABLED": "1"}, clear=True):
            result = doctor()

        self.assertTrue(result["production_ready"])
        self.assertEqual(result["configured_sources"], ["serper", "ddgs"])

    def test_audit_results_summarizes_statuses_and_writes_review_queue(self):
        with tempfile.TemporaryDirectory() as tmp:
            results = Path(tmp) / "results.csv"
            review = Path(tmp) / "review.csv"
            rows = [
                {
                    "provider_id": "p-1",
                    "provider_name": "Matched Provider",
                    "official_url": "https://matched.example",
                    "official_domain": "matched.example",
                    "confidence": "88",
                    "status": "matched",
                    "evidence_summary": "strong",
                },
                {
                    "provider_id": "p-2",
                    "provider_name": "Review Provider",
                    "official_url": "https://review.example",
                    "official_domain": "review.example",
                    "confidence": "61",
                    "status": "needs_review",
                    "evidence_summary": "partial",
                },
                {
                    "provider_id": "p-3",
                    "provider_name": "Weak Provider",
                    "official_url": "",
                    "official_domain": "",
                    "confidence": "20",
                    "status": "low_confidence",
                    "evidence_summary": "weak",
                },
            ]
            with results.open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=rows[0].keys())
                writer.writeheader()
                writer.writerows(rows)

            summary = audit_results(results, review)
            with review.open(newline="", encoding="utf-8") as f:
                review_rows = list(csv.DictReader(f))

        self.assertEqual(summary["total_rows"], 3)
        self.assertEqual(summary["status_counts"]["matched"], 1)
        self.assertEqual(summary["needs_review_rows"], 1)
        self.assertEqual(summary["unresolved_rows"], 1)
        self.assertEqual(len(review_rows), 2)
        self.assertIn("manual_decision", review_rows[0])

    def test_enrich_result_links_adds_provider_detail_url(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            providers = tmp_path / "providers.csv"
            results = tmp_path / "results.csv"
            enriched = tmp_path / "enriched.csv"
            with providers.open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(
                    f,
                    fieldnames=[
                        "provider_id",
                        "provider_name",
                        "service_apis",
                        "provider_locations",
                        "provider_languages",
                        "service_types",
                        "listing_logo_url",
                        "detail_url",
                        "about_listing_text",
                        "source_rows",
                    ],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "provider_id": "p-1",
                        "provider_name": "One",
                        "service_apis": "[]",
                        "provider_locations": "[]",
                        "provider_languages": "[]",
                        "service_types": "[]",
                        "listing_logo_url": "https://images.example/logo.png",
                        "detail_url": "https://sellercentral.amazon.example/provider/p-1",
                        "about_listing_text": "",
                        "source_rows": "1",
                    }
                )
            with results.open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(
                    f,
                    fieldnames=["provider_id", "provider_name", "official_url", "official_domain", "status"],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "provider_id": "p-1",
                        "provider_name": "One",
                        "official_url": "https://one.example",
                        "official_domain": "one.example",
                        "status": "matched",
                    }
                )

            summary = enrich_result_links(providers, results, enriched)
            with enriched.open(newline="", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))

        self.assertEqual(summary["provider_detail_url_rows"], 1)
        self.assertEqual(rows[0]["provider_detail_url"], "https://sellercentral.amazon.example/provider/p-1")
        self.assertEqual(rows[0]["listing_logo_url"], "https://images.example/logo.png")

    def test_run_workflow_append_and_done_ids_support_batch_resume(self):
        providers = [
            {"provider_id": "p-1", "provider_name": "One", "service_apis": [], "provider_locations": []},
            {"provider_id": "p-2", "provider_name": "Two", "service_apis": [], "provider_locations": []},
        ]

        def fake_choose(provider, candidates, config):
            return {
                "official_url": f"https://{provider['provider_id']}.example",
                "official_domain": f"{provider['provider_id']}.example",
                "confidence": 90,
                "status": "matched",
                "evidence_summary": "test",
                "candidates": [],
            }

        with tempfile.TemporaryDirectory() as tmp, patch("sys.stderr"), patch(
            "finder.cli.collect_candidates", return_value=[]
        ), patch("finder.cli.choose_best", side_effect=fake_choose):
            output = Path(tmp) / "results.csv"
            evidence = Path(tmp) / "evidence.jsonl"
            run_workflow([providers[0]], output, evidence, {}, append=False)
            run_workflow([providers[1]], output, evidence, {}, append=True)
            with output.open(newline="", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
            evidence_lines = evidence.read_text(encoding="utf-8").splitlines()
            done_ids = read_done_provider_ids(output)

        self.assertEqual([row["provider_id"] for row in rows], ["p-1", "p-2"])
        self.assertEqual(done_ids, {"p-1", "p-2"})
        self.assertEqual(len(evidence_lines), 2)

    def test_candidate_limit_preserves_domain_guesses_and_trims_search_results(self):
        candidates = [
            SearchCandidate(url="https://one.example", source="ddgs"),
            SearchCandidate(url="https://two.example", source="ddgs"),
            SearchCandidate(url="https://three.example", source="ddgs"),
            SearchCandidate(url="https://provider.example", source="domain_guess"),
        ]

        limited = limit_candidates_for_scoring(candidates, 2)

        self.assertEqual([candidate.url for candidate in limited], ["https://one.example", "https://provider.example"])

    def test_labeled_result_evaluation_reports_accuracy_and_misses(self):
        labels = [
            {"provider_id": "p-1", "provider_name": "One", "expected_domain": "one.example", "expected_url": ""},
            {"provider_id": "p-2", "provider_name": "Two", "expected_domain": "two.example", "expected_url": ""},
        ]
        results = [
            {
                "provider_id": "p-1",
                "provider_name": "One",
                "official_domain": "one.example",
                "official_url": "https://one.example",
                "status": "matched",
                "confidence": "90",
                "evidence_summary": "test",
            },
            {
                "provider_id": "p-2",
                "provider_name": "Two",
                "official_domain": "",
                "official_url": "",
                "status": "low_confidence",
                "confidence": "30",
                "evidence_summary": "weak",
            },
        ]

        summary = evaluate_labeled(labels, results)

        self.assertEqual(summary["overall"]["evaluated_rows"], 2)
        self.assertEqual(summary["overall"]["domain_matches"], 1)
        self.assertEqual(summary["overall"]["domain_accuracy"], 0.5)
        self.assertEqual(summary["overall"]["auto_match_precision"], 1.0)
        self.assertEqual(len(summary["mismatches"]), 1)

    def test_finalize_rows_applies_auto_matches_and_manual_decisions(self):
        results = [
            {
                "provider_id": "p-1",
                "provider_name": "Auto",
                "official_url": "https://auto.example",
                "official_domain": "auto.example",
                "confidence": "88",
                "status": "matched",
                "evidence_summary": "strong",
                "candidate_count": "4",
                "service_apis": "[]",
                "provider_locations": "[]",
            },
            {
                "provider_id": "p-2",
                "provider_name": "Review",
                "official_url": "https://review.example",
                "official_domain": "review.example",
                "confidence": "63",
                "status": "needs_review",
                "evidence_summary": "partial",
                "candidate_count": "3",
                "service_apis": "[]",
                "provider_locations": "[]",
            },
            {
                "provider_id": "p-3",
                "provider_name": "Replace",
                "official_url": "",
                "official_domain": "",
                "confidence": "30",
                "status": "low_confidence",
                "evidence_summary": "weak",
                "candidate_count": "1",
                "service_apis": "[]",
                "provider_locations": "[]",
            },
            {
                "provider_id": "p-4",
                "provider_name": "Reject",
                "official_url": "https://wrong.example",
                "official_domain": "wrong.example",
                "confidence": "55",
                "status": "needs_review",
                "evidence_summary": "wrong entity risk",
                "candidate_count": "2",
                "service_apis": "[]",
                "provider_locations": "[]",
            },
            {
                "provider_id": "p-5",
                "provider_name": "Review Sheet Accept",
                "official_url": "",
                "official_domain": "",
                "confidence": "39",
                "status": "low_confidence",
                "evidence_summary": "needs review",
                "candidate_count": "2",
                "service_apis": "[]",
                "provider_locations": "[]",
            },
            {
                "provider_id": "p-6",
                "provider_name": "Candidate Accept",
                "official_url": "",
                "official_domain": "",
                "confidence": "41",
                "status": "low_confidence",
                "evidence_summary": "candidate only",
                "candidate_count": "2",
                "service_apis": "[]",
                "provider_locations": "[]",
            },
        ]
        review = [
            {"provider_id": "p-2", "manual_decision": "accept", "manual_url": "", "notes": "checked"},
            {"provider_id": "p-3", "manual_decision": "replace", "manual_url": "replace.example", "notes": ""},
            {"provider_id": "p-4", "manual_decision": "reject", "manual_url": "", "notes": "same name only"},
            {
                "provider_id": "p-5",
                "manual_decision": "accept",
                "official_url": "https://review-sheet.example",
                "manual_url": "",
                "notes": "candidate confirmed",
            },
            {
                "provider_id": "p-6",
                "manual_decision": "accept",
                "candidate_1_url": "https://candidate.example",
                "manual_url": "",
                "notes": "first candidate confirmed",
            },
        ]

        final_rows, unresolved_rows, summary = finalize_rows(results, review)
        by_id = {row["provider_id"]: row for row in final_rows}

        self.assertEqual(by_id["p-1"]["status"], "matched")
        self.assertEqual(by_id["p-1"]["decision_source"], "auto_matched")
        self.assertEqual(by_id["p-2"]["official_domain"], "review.example")
        self.assertEqual(by_id["p-2"]["status"], "manual_accepted")
        self.assertEqual(by_id["p-3"]["official_url"], "https://replace.example")
        self.assertEqual(by_id["p-3"]["official_domain"], "replace.example")
        self.assertEqual(by_id["p-4"]["status"], "rejected")
        self.assertEqual(by_id["p-4"]["official_url"], "")
        self.assertEqual(by_id["p-5"]["official_domain"], "review-sheet.example")
        self.assertEqual(by_id["p-5"]["decision_source"], "manual_accept")
        self.assertEqual(by_id["p-6"]["official_domain"], "candidate.example")
        self.assertEqual(summary["official_url_rows"], 5)
        self.assertEqual([row["provider_id"] for row in unresolved_rows], ["p-4"])

    def test_finalize_results_writes_final_and_unresolved_csvs(self):
        with tempfile.TemporaryDirectory() as tmp:
            results_path = Path(tmp) / "results.csv"
            review_path = Path(tmp) / "review.csv"
            final_path = Path(tmp) / "final.csv"
            unresolved_path = Path(tmp) / "unresolved.csv"
            result_rows = [
                {
                    "provider_id": "p-1",
                    "provider_name": "One",
                    "official_url": "https://one.example",
                    "official_domain": "one.example",
                    "confidence": "90",
                    "status": "matched",
                    "evidence_summary": "strong",
                    "candidate_count": "2",
                    "service_apis": "[]",
                    "provider_locations": "[]",
                },
                {
                    "provider_id": "p-2",
                    "provider_name": "Two",
                    "official_url": "",
                    "official_domain": "",
                    "confidence": "25",
                    "status": "not_found",
                    "evidence_summary": "",
                    "candidate_count": "0",
                    "service_apis": "[]",
                    "provider_locations": "[]",
                },
            ]
            review_rows = [
                {
                    "provider_id": "p-2",
                    "provider_name": "Two",
                    "official_url": "",
                    "official_domain": "",
                    "confidence": "25",
                    "status": "not_found",
                    "evidence_summary": "",
                    "manual_decision": "",
                    "manual_url": "https://two.example",
                    "notes": "manual URL implies replace",
                }
            ]
            with results_path.open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=result_rows[0].keys())
                writer.writeheader()
                writer.writerows(result_rows)
            with review_path.open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=review_rows[0].keys())
                writer.writeheader()
                writer.writerows(review_rows)

            summary = finalize_results(
                results_path,
                final_path,
                review_csv=review_path,
                unresolved_csv=unresolved_path,
            )
            with final_path.open(newline="", encoding="utf-8") as f:
                final_rows = list(csv.DictReader(f))
            with unresolved_path.open(newline="", encoding="utf-8") as f:
                unresolved_rows = list(csv.DictReader(f))

        self.assertEqual(summary["official_url_rows"], 2)
        self.assertEqual(len(final_rows), 2)
        self.assertEqual(final_rows[1]["official_domain"], "two.example")
        self.assertEqual(unresolved_rows, [])

    def test_build_review_sheet_expands_top_candidates_from_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            results_path = Path(tmp) / "results.csv"
            evidence_path = Path(tmp) / "evidence.jsonl"
            result_rows = [
                {
                    "provider_id": "p-1",
                    "provider_name": "Needs JS",
                    "official_url": "https://needsjs.example",
                    "official_domain": "needsjs.example",
                    "confidence": "68",
                    "status": "needs_review",
                    "evidence_summary": "page_requires_javascript; javascript_page_requires_dynamic_review",
                    "candidate_count": "4",
                    "scored_candidate_count": "4",
                },
                {
                    "provider_id": "p-2",
                    "provider_name": "Matched",
                    "official_url": "https://matched.example",
                    "official_domain": "matched.example",
                    "confidence": "90",
                    "status": "matched",
                    "evidence_summary": "strong",
                    "candidate_count": "1",
                    "scored_candidate_count": "1",
                },
            ]
            with results_path.open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=result_rows[0].keys())
                writer.writeheader()
                writer.writerows(result_rows)
            evidence = {
                "provider_id": "p-1",
                "provider_name": "Needs JS",
                "candidate_count": 4,
                "scored_candidate_count": 4,
                "candidates": [
                    {"url": "https://bad.example", "domain": "bad.example", "score": 80, "reject": True, "source": "ddgs"},
                    {
                        "url": "https://www.linkedin.com/in/needs-js-owner",
                        "domain": "linkedin.com",
                        "score": 99,
                        "reject": False,
                        "source": "ddgs",
                    },
                    {
                        "url": "https://needsjs.example",
                        "domain": "needsjs.example",
                        "score": 68,
                        "reject": False,
                        "source": "ddgs",
                        "rank": 1,
                        "query": '"Needs JS" official website',
                        "reasons": ["page_requires_javascript"],
                    },
                    {
                        "url": "https://alternate.example",
                        "domain": "alternate.example",
                        "score": 45,
                        "reject": False,
                        "source": "ddgs",
                    },
                ],
            }
            evidence_path.write_text(json.dumps(evidence) + "\n", encoding="utf-8")

            rows = build_review_sheet(results_csv=results_path, evidence_jsonl=evidence_path, top_candidates=2)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["review_priority"], "high")
        self.assertEqual(rows[0]["suggested_action"], "open_candidate_in_browser")
        self.assertEqual(rows[0]["candidate_1_domain"], "needsjs.example")
        self.assertEqual(rows[0]["candidate_2_domain"], "alternate.example")

    def test_build_linked_workbook_writes_clickable_formula_links(self):
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = Path(tmp) / "links.csv"
            xlsx_path = Path(tmp) / "links.xlsx"
            rows = [
                {
                    "provider_id": "p-1",
                    "provider_detail_url": "https://sellercentral.amazon.example/detail",
                    "official_url": "https://example.com",
                }
            ]
            with csv_path.open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=rows[0].keys())
                writer.writeheader()
                writer.writerows(rows)

            summary = build_workbook([("Final", csv_path)], xlsx_path)
            with zipfile.ZipFile(xlsx_path) as z:
                sheet_xml = z.read("xl/worksheets/sheet1.xml").decode("utf-8")

        self.assertEqual(summary["sheets"], 1)
        self.assertIn("HYPERLINK(", sheet_xml)
        self.assertIn("https://example.com", sheet_xml)

    def test_build_manual_review_task_creates_simplified_clickable_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            final_rows = [
                {
                    "provider_id": "p-1",
                    "provider_name": "Low Accepted",
                    "provider_detail_url": "https://amazon.example/p-1",
                    "official_url": "https://low.example",
                    "official_domain": "low.example",
                    "status": "manual_accepted",
                    "decision_source": "manual_replace",
                    "confidence": "64",
                    "source_status": "needs_review",
                    "evidence_summary": "verified",
                    "service_apis": "[]",
                    "provider_locations": "[]",
                },
                {
                    "provider_id": "p-2",
                    "provider_name": "Strong Match",
                    "provider_detail_url": "https://amazon.example/p-2",
                    "official_url": "https://strong.example",
                    "official_domain": "strong.example",
                    "status": "matched",
                    "decision_source": "auto_matched",
                    "confidence": "91",
                    "source_status": "matched",
                    "evidence_summary": "strong",
                    "service_apis": "[]",
                    "provider_locations": "[]",
                },
                {
                    "provider_id": "p-3",
                    "provider_name": "Still Missing",
                    "provider_detail_url": "https://amazon.example/p-3",
                    "official_url": "",
                    "official_domain": "",
                    "status": "unresolved",
                    "decision_source": "pending_review",
                    "confidence": "38",
                    "source_status": "low_confidence",
                    "evidence_summary": "weak",
                    "service_apis": "[]",
                    "provider_locations": "[]",
                },
            ]
            second_pass_rows = [
                {
                    "provider_id": "p-3",
                    "provider_name": "Still Missing",
                    "official_url": "https://candidate.example",
                    "official_domain": "candidate.example",
                    "confidence": "58",
                    "accepted_for_final": "false",
                    "previous_top_candidate_url": "https://candidate.example",
                }
            ]
            _write_test_csv(run_dir / "provider_final_official_websites_second_pass.csv", final_rows)
            _write_test_csv(run_dir / "unresolved_second_pass_results.csv", second_pass_rows)

            summary = build_manual_review_task(run_dir=run_dir, write_xlsx=True)
            with (run_dir / "review_task.csv").open(newline="", encoding="utf-8") as f:
                task_rows = list(csv.DictReader(f))
            task_xlsx_exists = (run_dir / "review_task.xlsx").exists()
            legacy_task_exists = (run_dir / "manual_official_site_review_task.xlsx").exists()

        self.assertEqual(summary["review_rows"], 2)
        self.assertEqual([row["provider_id"] for row in task_rows], ["p-1", "p-3"])
        self.assertEqual(task_rows[0]["review_reason"], "precision_second_pass_accepted_lt70")
        self.assertEqual(task_rows[1]["top_candidate_url"], "https://candidate.example")
        self.assertTrue(task_xlsx_exists)
        self.assertTrue(legacy_task_exists)

    def test_build_manual_review_task_flags_high_confidence_ambiguous_identity_risks(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            final_rows = [
                {
                    "provider_id": "generic",
                    "provider_name": "AA Consulting",
                    "provider_detail_url": "https://amazon.example/generic",
                    "official_url": "https://aaconsulting.example",
                    "official_domain": "aaconsulting.example",
                    "status": "matched",
                    "decision_source": "auto_matched",
                    "confidence": "100",
                    "source_status": "matched",
                    "evidence_summary": "page_contains_exact_provider_name; page_contains_amazon_service_keywords; domain_exact_provider_slug",
                    "service_apis": "[]",
                    "provider_locations": "[]",
                },
                {
                    "provider_id": "slug",
                    "provider_name": "Behemoth",
                    "provider_detail_url": "https://amazon.example/slug",
                    "official_url": "https://behemothimports.example",
                    "official_domain": "behemothimports.example",
                    "status": "matched",
                    "decision_source": "auto_matched",
                    "confidence": "100",
                    "source_status": "matched",
                    "evidence_summary": "page_contains_exact_provider_name; page_contains_amazon_service_keywords; domain_contains_provider_slug",
                    "service_apis": "[]",
                    "provider_locations": "[]",
                },
                {
                    "provider_id": "logo",
                    "provider_name": "Best Seller",
                    "provider_detail_url": "https://amazon.example/logo",
                    "official_url": "https://bestseller.example",
                    "official_domain": "bestseller.example",
                    "status": "matched",
                    "decision_source": "auto_matched",
                    "confidence": "100",
                    "source_status": "matched",
                    "evidence_summary": "page_contains_exact_provider_name; page_contains_amazon_service_keywords; domain_exact_provider_slug; listing_logo_visual_match",
                    "service_apis": "[]",
                    "provider_locations": "[]",
                },
                {
                    "provider_id": "safe",
                    "provider_name": "Distinct Systems Corporation",
                    "provider_detail_url": "https://amazon.example/safe",
                    "official_url": "https://distinct.example",
                    "official_domain": "distinct.example",
                    "status": "matched",
                    "decision_source": "auto_matched",
                    "confidence": "100",
                    "source_status": "matched",
                    "evidence_summary": "page_contains_exact_provider_name; page_contains_amazon_service_keywords; domain_exact_provider_slug",
                    "service_apis": "[]",
                    "provider_locations": "[]",
                },
                {
                    "provider_id": "broad",
                    "provider_name": "Global Commerce",
                    "provider_detail_url": "https://amazon.example/broad",
                    "official_url": "https://globalcommerce.example",
                    "official_domain": "globalcommerce.example",
                    "status": "matched",
                    "decision_source": "auto_matched",
                    "confidence": "100",
                    "source_status": "matched",
                    "evidence_summary": "page_contains_exact_provider_name; domain_exact_provider_slug",
                    "service_apis": "[]",
                    "provider_locations": "[]",
                },
            ]
            _write_test_csv(run_dir / "official_sites.csv", final_rows)

            summary = build_manual_review_task(run_dir=run_dir, write_xlsx=False)
            with (run_dir / "review_task.csv").open(newline="", encoding="utf-8") as f:
                task_rows = {row["provider_id"]: row for row in csv.DictReader(f)}

        self.assertEqual(summary["review_rows"], 2)
        self.assertEqual(task_rows["generic"]["review_reason"], "precision_generic_identity_term_risk")
        self.assertEqual(task_rows["slug"]["review_reason"], "precision_slug_extension_identity_risk")
        self.assertNotIn("logo", task_rows)
        self.assertNotIn("safe", task_rows)
        self.assertNotIn("broad", task_rows)

    def test_build_manual_review_task_skips_high_confidence_second_pass_accepts(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            final_rows = [
                {
                    "provider_id": "high",
                    "provider_name": "High Second Pass",
                    "provider_detail_url": "https://amazon.example/high",
                    "official_url": "https://high.example",
                    "official_domain": "high.example",
                    "status": "manual_accepted",
                    "decision_source": "manual_replace",
                    "confidence": "90",
                    "source_status": "matched",
                    "evidence_summary": "second pass high confidence",
                    "service_apis": "[]",
                    "provider_locations": "[]",
                },
                {
                    "provider_id": "low",
                    "provider_name": "Low Second Pass",
                    "provider_detail_url": "https://amazon.example/low",
                    "official_url": "https://low.example",
                    "official_domain": "low.example",
                    "status": "manual_accepted",
                    "decision_source": "manual_replace",
                    "confidence": "84",
                    "source_status": "matched",
                    "evidence_summary": "second pass low confidence",
                    "service_apis": "[]",
                    "provider_locations": "[]",
                },
            ]
            _write_test_csv(run_dir / "official_sites.csv", final_rows)

            summary = build_manual_review_task(run_dir=run_dir, write_xlsx=False)
            with (run_dir / "review_task.csv").open(newline="", encoding="utf-8") as f:
                task_rows = {row["provider_id"]: row for row in csv.DictReader(f)}

        self.assertEqual(summary["review_rows"], 1)
        self.assertNotIn("high", task_rows)
        self.assertEqual(task_rows["low"]["review_reason"], "precision_second_pass_accepted_70_84")

    def test_build_manual_review_task_uses_calibrated_matched_review_cutoff(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            final_rows = [
                {
                    "provider_id": "watch",
                    "provider_name": "Watch Band",
                    "provider_detail_url": "https://amazon.example/watch",
                    "official_url": "https://watch.example",
                    "official_domain": "watch.example",
                    "status": "matched",
                    "decision_source": "auto_matched",
                    "confidence": str(DEFAULT_MATCHED_REVIEW_CONFIDENCE_CUTOFF - 1),
                    "source_status": "matched",
                    "evidence_summary": "page_contains_exact_provider_name",
                    "service_apis": "[]",
                    "provider_locations": "[]",
                },
                {
                    "provider_id": "above",
                    "provider_name": "Above Band",
                    "provider_detail_url": "https://amazon.example/above",
                    "official_url": "https://above.example",
                    "official_domain": "above.example",
                    "status": "matched",
                    "decision_source": "auto_matched",
                    "confidence": str(DEFAULT_MATCHED_REVIEW_CONFIDENCE_CUTOFF),
                    "source_status": "matched",
                    "evidence_summary": "page_contains_exact_provider_name",
                    "service_apis": "[]",
                    "provider_locations": "[]",
                },
            ]
            _write_test_csv(run_dir / "official_sites.csv", final_rows)

            summary = build_manual_review_task(run_dir=run_dir, write_xlsx=False)
            with (run_dir / "review_task.csv").open(newline="", encoding="utf-8") as f:
                task_rows = {row["provider_id"]: row for row in csv.DictReader(f)}

        self.assertEqual(summary["review_rows"], 1)
        self.assertEqual(summary["matched_review_confidence_below"], DEFAULT_MATCHED_REVIEW_CONFIDENCE_CUTOFF)
        self.assertEqual(
            summary["second_pass_review_confidence_below"],
            DEFAULT_SECOND_PASS_REVIEW_CONFIDENCE_CUTOFF,
        )
        self.assertIn("watch", task_rows)
        self.assertEqual(task_rows["watch"]["review_reason"], "precision_low_confidence_auto_match")
        self.assertNotIn("above", task_rows)

    def test_agent_b_verification_outputs_decisions_and_clickable_workbook(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            manual_rows = [
                {
                    "provider_id": "p-1",
                    "provider_name": "Accept Agency LLC",
                    "provider_detail_url": "https://amazon.example/p-1",
                    "official_url": "https://acceptagency.example",
                    "provider_locations": json.dumps(["United Kingdom"]),
                    "confidence": "82",
                    "status": "manual_accepted",
                },
                {
                    "provider_id": "p-2",
                    "provider_name": "Replace Agency",
                    "provider_detail_url": "https://amazon.example/p-2",
                    "official_url": "https://wrong.example",
                    "provider_locations": json.dumps(["Germany"]),
                    "confidence": "58",
                    "status": "needs_review",
                },
                {
                    "provider_id": "p-3",
                    "provider_name": "Rejected Directory",
                    "provider_detail_url": "https://amazon.example/p-3",
                    "official_url": "https://linkedin.com/company/rejected-directory",
                    "provider_locations": json.dumps(["United States"]),
                    "confidence": "55",
                    "status": "needs_review",
                },
                {
                    "provider_id": "p-4",
                    "provider_name": "Missing Candidate",
                    "provider_detail_url": "https://amazon.example/p-4",
                    "official_url": "",
                    "provider_locations": json.dumps(["France"]),
                    "confidence": "0",
                    "status": "unresolved",
                },
            ]
            _write_test_csv(run_dir / "manual_official_site_review_task.csv", manual_rows)
            _write_test_csv(run_dir / "provider_final_official_websites_second_pass.csv", manual_rows)

            def fake_fetch(url):
                if "acceptagency.example" in url:
                    html = """
                    <html><head><title>Accept Agency LLC</title>
                    <script type="application/ld+json">{"@type":"Organization"}</script></head>
                    <body>Accept Agency LLC provides Amazon Seller Central marketplace
                    account management and ecommerce advertising in the United Kingdom.
                    Contact us privacy policy terms and conditions info@acceptagency.example.</body></html>
                    """
                    return {"ok": True, "status": 200, "final_url": url, "text": html}
                if "replaceagency.com" in url:
                    html = """
                    <html><head><title>Replace Agency</title></head>
                    <body>Replace Agency offers Amazon marketplace account management,
                    seller central PPC and ecommerce services in Germany. About us. Contact us.</body></html>
                    """
                    return {"ok": True, "status": 200, "final_url": url, "text": html}
                if "wrong.example" in url:
                    return {"ok": True, "status": 200, "final_url": url, "text": "<html><body>Unrelated site</body></html>"}
                return {"ok": False, "status": 404, "final_url": url, "text": ""}

            replacement_candidates = [
                SearchCandidate(
                    url="https://replaceagency.com",
                    title="Replace Agency official website",
                    snippet="Replace Agency Amazon marketplace services contact Germany",
                    source="brave",
                    query='"Replace Agency" official website',
                    rank=1,
                )
            ]

            with patch("tools.run_agent_b_verification.fetch_text", side_effect=fake_fetch), patch(
                "finder.scoring.fetch_text", side_effect=fake_fetch
            ), patch(
                "tools.run_agent_b_verification.collect_candidates_for_queries",
                side_effect=lambda queries, per_query=2: replacement_candidates
                if any("Replace Agency" in query for query in queries)
                else [],
            ):
                summary = run_agent_b_verification(run_dir=run_dir, write_xlsx=True)
            with (run_dir / "agent_b/check.csv").open(newline="", encoding="utf-8") as f:
                rows = {row["provider_id"]: row for row in csv.DictReader(f)}
            xlsx_exists = (run_dir / "agent_b/check.xlsx").exists()
            legacy_xlsx_exists = (run_dir / "agent_b_verification_results.xlsx").exists()

        self.assertEqual(summary["decision_counts"]["accept"], 1)
        self.assertEqual(rows["p-1"]["manual_decision"], "accept")
        self.assertEqual(rows["p-2"]["agent_b_decision"], "replace")
        self.assertEqual(rows["p-2"]["manual_url"], "https://replaceagency.com/")
        self.assertEqual(rows["p-3"]["agent_b_decision"], "reject")
        self.assertEqual(rows["p-4"]["agent_b_decision"], "unsure")
        self.assertTrue(xlsx_exists)
        self.assertIn("https://amazon.example/p-1", rows["p-1"]["provider_detail_url"])
        self.assertEqual(summary["workflow_version"], WORKFLOW_VERSION)
        self.assertTrue(legacy_xlsx_exists)

    def test_agent_b_defaults_to_high_risk_rows_without_manual_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            final_rows = [
                {
                    "provider_id": "safe",
                    "provider_name": "Trusted Distinct Corporation",
                    "provider_detail_url": "https://amazon.example/safe",
                    "official_url": "https://trusted.example",
                    "status": "matched",
                    "confidence": "96",
                    "evidence_summary": "page_contains_exact_provider_name; page_contains_amazon_service_keywords",
                },
                {
                    "provider_id": "second",
                    "provider_name": "Second Pass Agency",
                    "provider_detail_url": "https://amazon.example/second",
                    "official_url": "https://second.example",
                    "status": "manual_accepted",
                    "confidence": "88",
                    "evidence_summary": "page_contains_exact_provider_name",
                },
                {
                    "provider_id": "low",
                    "provider_name": "Low Confidence Agency",
                    "provider_detail_url": "https://amazon.example/low",
                    "official_url": "https://low.example",
                    "status": "matched",
                    "confidence": str(DEFAULT_MATCHED_REVIEW_CONFIDENCE_CUTOFF - 1),
                    "evidence_summary": "page_contains_exact_provider_name",
                },
                {
                    "provider_id": "above",
                    "provider_name": "Above Watch Band",
                    "provider_detail_url": "https://amazon.example/above",
                    "official_url": "https://above.example",
                    "status": "matched",
                    "confidence": str(DEFAULT_MATCHED_REVIEW_CONFIDENCE_CUTOFF),
                    "evidence_summary": "page_contains_exact_provider_name",
                },
                {
                    "provider_id": "logo",
                    "provider_name": "Logo Only Brand",
                    "provider_detail_url": "https://amazon.example/logo",
                    "official_url": "https://logo.example",
                    "status": "matched",
                    "confidence": "94",
                    "evidence_summary": "listing_logo_visual_match",
                },
                {
                    "provider_id": "ambiguous",
                    "provider_name": "AA Consulting",
                    "provider_detail_url": "https://amazon.example/ambiguous",
                    "official_url": "https://aaconsulting.example",
                    "status": "matched",
                    "confidence": "100",
                    "evidence_summary": "page_contains_exact_provider_name; page_contains_amazon_service_keywords; domain_exact_provider_slug",
                },
            ]
            _write_test_csv(run_dir / "official_sites.csv", final_rows)
            with patch("tools.run_agent_b_verification.fetch_text", return_value={"ok": False, "text": ""}), patch(
                "tools.run_agent_b_verification.collect_candidates_for_queries", return_value=[]
            ):
                summary = run_agent_b_verification(run_dir=run_dir, write_xlsx=False)
            with (run_dir / "agent_b/check.csv").open(newline="", encoding="utf-8") as f:
                checked_ids = [row["provider_id"] for row in csv.DictReader(f)]

        self.assertEqual(summary["input_rows"], 4)
        self.assertEqual(checked_ids, ["second", "low", "logo", "ambiguous"])
        self.assertNotIn("above", checked_ids)

    def test_agent_b_keeps_high_risk_ambiguous_identity_as_unsure_without_exact_logo(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            final_rows = [
                {
                    "provider_id": "p-1",
                    "provider_name": "AA Consulting",
                    "provider_detail_url": "https://amazon.example/p-1",
                    "official_url": "https://aaconsulting.example",
                    "status": "matched",
                    "confidence": "100",
                    "evidence_summary": "page_contains_exact_provider_name; page_contains_amazon_service_keywords; domain_exact_provider_slug",
                },
            ]
            manual_rows = [
                {
                    "provider_id": "p-1",
                    "provider_name": "AA Consulting",
                    "provider_detail_url": "https://amazon.example/p-1",
                    "official_url": "https://aaconsulting.example",
                    "status": "matched",
                    "confidence": "100",
                    "review_reason": "precision_generic_identity_term_risk",
                },
            ]
            _write_test_csv(run_dir / "official_sites.csv", final_rows)
            _write_test_csv(run_dir / "review_task.csv", manual_rows)

            def fake_fetch(url):
                html = """
                <html><head><title>AA Consulting</title>
                <script type="application/ld+json">{"@type":"Organization"}</script></head>
                <body>AA Consulting LLC. About us. Contact us. Privacy policy.
                Amazon marketplace account management ecommerce services.</body></html>
                """
                return {"ok": True, "status": 200, "final_url": url, "text": html}

            with patch("tools.run_agent_b_verification.fetch_text", side_effect=fake_fetch), patch(
                "tools.run_agent_b_verification.collect_candidates_for_queries", return_value=[]
            ):
                summary = run_agent_b_verification(run_dir=run_dir, write_xlsx=False)
            with (run_dir / "agent_b/check.csv").open(newline="", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))

        self.assertEqual(summary["decision_counts"], {"unsure": 1})
        self.assertEqual(rows[0]["agent_b_decision"], "unsure")
        self.assertEqual(rows[0]["review_reason"], "precision_generic_identity_term_risk")
        self.assertEqual(rows[0]["reason_for_unsure"], "high_risk_identity_needs_human_confirmation")

    def test_agent_b_keeps_recall_unresolved_rows_as_unsure(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            final_rows = [
                {
                    "provider_id": "p-1",
                    "provider_name": "Recovered Brand",
                    "provider_detail_url": "https://amazon.example/p-1",
                    "official_url": "",
                    "status": "unresolved",
                    "confidence": "69",
                    "evidence_summary": "recall candidate",
                },
            ]
            manual_rows = [
                {
                    "provider_id": "p-1",
                    "provider_name": "Recovered Brand",
                    "provider_detail_url": "https://amazon.example/p-1",
                    "official_url": "https://recovered.example",
                    "status": "unresolved",
                    "confidence": "69",
                    "review_reason": "recall_unresolved_top_candidate",
                },
            ]
            _write_test_csv(run_dir / "official_sites.csv", final_rows)
            _write_test_csv(run_dir / "review_task.csv", manual_rows)

            def fake_fetch(url):
                html = """
                <html><head><title>Recovered Brand</title>
                <script type="application/ld+json">{"@type":"Organization"}</script></head>
                <body>Recovered Brand LLC. About us. Contact us. Privacy policy.
                Amazon marketplace account management ecommerce services.</body></html>
                """
                return {"ok": True, "status": 200, "final_url": url, "text": html}

            with patch("tools.run_agent_b_verification.fetch_text", side_effect=fake_fetch), patch(
                "tools.run_agent_b_verification.collect_candidates_for_queries", return_value=[]
            ):
                summary = run_agent_b_verification(run_dir=run_dir, write_xlsx=False)
            with (run_dir / "agent_b/check.csv").open(newline="", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))

        self.assertEqual(summary["decision_counts"], {"unsure": 1})
        self.assertEqual(rows[0]["agent_b_decision"], "unsure")
        self.assertEqual(rows[0]["reason_for_unsure"], "recall_candidate_needs_human_confirmation")
        self.assertEqual(rows[0]["review_reason"], "recall_unresolved_top_candidate")

    def test_agent_b_keeps_review_task_replacements_as_unsure(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            final_rows = [
                {
                    "provider_id": "p-1",
                    "provider_name": "Replace Candidate",
                    "provider_detail_url": "https://amazon.example/p-1",
                    "official_url": "https://wrong.example",
                    "status": "matched",
                    "confidence": "77",
                    "evidence_summary": "weak candidate",
                },
            ]
            manual_rows = [
                {
                    "provider_id": "p-1",
                    "provider_name": "Replace Candidate",
                    "provider_detail_url": "https://amazon.example/p-1",
                    "official_url": "https://wrong.example",
                    "status": "matched",
                    "confidence": "77",
                    "review_reason": "precision_low_confidence_auto_match",
                },
            ]
            _write_test_csv(run_dir / "official_sites.csv", final_rows)
            _write_test_csv(run_dir / "review_task.csv", manual_rows)

            def fake_fetch(url):
                if "replacement.example" in url:
                    html = """
                    <html><head><title>Replace Candidate</title></head>
                    <body>Replace Candidate LLC. About us. Contact us.
                    Amazon marketplace account management ecommerce services.</body></html>
                    """
                    return {"ok": True, "status": 200, "final_url": url, "text": html}
                return {"ok": True, "status": 200, "final_url": url, "text": "<html><body>Wrong page</body></html>"}

            replacement_candidates = [
                SearchCandidate(
                    url="https://replacement.example",
                    title="Replace Candidate official website",
                    snippet="Replace Candidate Amazon marketplace services contact",
                    source="brave",
                    query='"Replace Candidate" official website',
                    rank=1,
                )
            ]

            with patch("tools.run_agent_b_verification.fetch_text", side_effect=fake_fetch), patch(
                "finder.scoring.fetch_text", side_effect=fake_fetch
            ), patch("tools.run_agent_b_verification.collect_candidates_for_queries", return_value=replacement_candidates):
                summary = run_agent_b_verification(run_dir=run_dir, write_xlsx=False)
            with (run_dir / "agent_b/check.csv").open(newline="", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))

        self.assertEqual(summary["decision_counts"], {"unsure": 1})
        self.assertEqual(rows[0]["agent_b_decision"], "unsure")
        self.assertEqual(rows[0]["replacement_domain"], "replacement.example")
        self.assertEqual(rows[0]["manual_url"], "")
        self.assertEqual(rows[0]["reason_for_unsure"], "replacement_candidate_needs_human_confirmation")

    def test_agent_b_resume_reuses_existing_rows_and_writes_incremental_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            final_rows = [
                {
                    "provider_id": "p-1",
                    "provider_name": "Existing Brand",
                    "provider_detail_url": "https://amazon.example/p-1",
                    "official_url": "https://existing.example",
                    "status": "matched",
                    "confidence": "90",
                    "evidence_summary": "existing",
                },
                {
                    "provider_id": "p-2",
                    "provider_name": "New Brand",
                    "provider_detail_url": "https://amazon.example/p-2",
                    "official_url": "https://new.example",
                    "status": "matched",
                    "confidence": "90",
                    "evidence_summary": "new",
                },
            ]
            manual_rows = [
                {
                    "provider_id": "p-1",
                    "provider_name": "Existing Brand",
                    "official_url": "https://existing.example",
                    "review_reason": "precision_low_confidence_auto_match",
                },
                {
                    "provider_id": "p-2",
                    "provider_name": "New Brand",
                    "official_url": "https://new.example",
                    "review_reason": "precision_low_confidence_auto_match",
                },
            ]
            existing_agent_b = [
                {
                    "provider_id": "p-1",
                    "provider_name": "Existing Brand",
                    "candidate_url": "https://existing.example",
                    "candidate_domain": "existing.example",
                    "agent_b_decision": "accept",
                    "manual_decision": "accept",
                    "manual_url": "",
                    "confidence": "90",
                    "evidence_score": "90",
                    "evidence_urls": "https://existing.example",
                    "supporting_facts": "cached",
                    "counter_evidence": "",
                    "reason_for_unsure": "",
                    "notes": "cached row",
                    "independent_search_queries": "",
                    "replacement_url": "",
                    "replacement_domain": "",
                    "source_status": "matched",
                    "source_confidence": "90",
                    "review_reason": "precision_low_confidence_auto_match",
                }
            ]
            _write_test_csv(run_dir / "official_sites.csv", final_rows)
            _write_test_csv(run_dir / "review_task.csv", manual_rows)
            _write_test_csv(run_dir / "agent_b/check.csv", existing_agent_b)
            (run_dir / "agent_b/check.jsonl").write_text(
                json.dumps({"provider_id": "p-1", "provider_name": "Existing Brand", "decision": "accept"}) + "\n",
                encoding="utf-8",
            )

            def fake_fetch(url):
                html = """
                <html><head><title>New Brand</title></head>
                <body>New Brand LLC. About us. Contact us.
                Amazon marketplace account management ecommerce services.</body></html>
                """
                return {"ok": True, "status": 200, "final_url": url, "text": html}

            with patch("tools.run_agent_b_verification.fetch_text", side_effect=fake_fetch), patch(
                "tools.run_agent_b_verification.collect_candidates_for_queries", return_value=[]
            ):
                summary = run_agent_b_verification(run_dir=run_dir, write_xlsx=False, resume=True)
            with (run_dir / "agent_b/check.csv").open(newline="", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
            jsonl_lines = (run_dir / "agent_b/check.jsonl").read_text(encoding="utf-8").strip().splitlines()

        self.assertEqual(summary["output_rows"], 2)
        self.assertEqual(summary["resumed_rows"], 1)
        self.assertEqual(summary["processed_rows"], 1)
        self.assertEqual([row["provider_id"] for row in rows], ["p-1", "p-2"])
        self.assertEqual(rows[0]["notes"], "cached row")
        self.assertEqual(len(jsonl_lines), 2)

    def test_agent_b_row_timeout_records_unsure_and_keeps_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            final_rows = [
                {
                    "provider_id": "p-1",
                    "provider_name": "Slow Brand",
                    "provider_detail_url": "https://amazon.example/p-1",
                    "official_url": "https://slow.example",
                    "status": "matched",
                    "confidence": "80",
                    "evidence_summary": "slow candidate",
                },
            ]
            manual_rows = [
                {
                    "provider_id": "p-1",
                    "provider_name": "Slow Brand",
                    "official_url": "https://slow.example",
                    "review_reason": "precision_low_confidence_auto_match",
                },
            ]
            _write_test_csv(run_dir / "official_sites.csv", final_rows)
            _write_test_csv(run_dir / "review_task.csv", manual_rows)

            def slow_fetch(url):
                time.sleep(2)
                return {"ok": True, "status": 200, "final_url": url, "text": "<html><body>Slow Brand</body></html>"}

            with patch("tools.run_agent_b_verification.fetch_text", side_effect=slow_fetch), patch(
                "tools.run_agent_b_verification.collect_candidates_for_queries", return_value=[]
            ):
                summary = run_agent_b_verification(run_dir=run_dir, write_xlsx=False, row_timeout=1)
            with (run_dir / "agent_b/check.csv").open(newline="", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))

        self.assertEqual(summary["output_rows"], 1)
        self.assertEqual(summary["timeout_rows"], 1)
        self.assertEqual(rows[0]["agent_b_decision"], "unsure")
        self.assertEqual(rows[0]["reason_for_unsure"], "agent_b_row_timeout")
        self.assertIn("agent_b_row_timeout", rows[0]["counter_evidence"])

    def test_agent_c_recommends_and_agent_a_applies_only_safe_rules(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            agent_b_rows = [
                {
                    "provider_id": "p-1",
                    "provider_name": "One",
                    "candidate_url": "https://bad-directory.example/profile/one",
                    "candidate_domain": "bad-directory.example",
                    "agent_b_decision": "reject",
                    "manual_decision": "reject",
                    "evidence_score": "0",
                    "independent_search_queries": "",
                    "counter_evidence": "candidate_not_independent_official_site",
                    "reason_for_unsure": "",
                },
                {
                    "provider_id": "p-2",
                    "provider_name": "Two",
                    "candidate_url": "https://bad-directory.example/profile/two",
                    "candidate_domain": "bad-directory.example",
                    "agent_b_decision": "reject",
                    "manual_decision": "reject",
                    "evidence_score": "0",
                    "independent_search_queries": "",
                    "counter_evidence": "candidate_not_independent_official_site",
                    "reason_for_unsure": "",
                },
                {
                    "provider_id": "p-3",
                    "provider_name": "Three",
                    "candidate_url": "https://single-case.example",
                    "candidate_domain": "single-case.example",
                    "agent_b_decision": "reject",
                    "manual_decision": "reject",
                    "evidence_score": "0",
                    "independent_search_queries": "",
                    "counter_evidence": "candidate_not_independent_official_site",
                    "reason_for_unsure": "",
                },
            ]
            _write_test_csv(run_dir / "agent_b_verification_results.csv", agent_b_rows)
            config_path = run_dir / "scoring.json"
            config_path.write_text(json.dumps(load_config("config/scoring.json")), encoding="utf-8")

            recommendations = run_agent_c_recommendations(run_dir=run_dir)
            dry_run = apply_agent_optimizations(run_dir=run_dir, config_path=config_path, apply=False)
            applied = apply_agent_optimizations(run_dir=run_dir, config_path=config_path, apply=True)
            config = json.loads(config_path.read_text(encoding="utf-8"))
            suggestions_exists = (run_dir / "agent_b/suggestions.json").exists()
            legacy_suggestions_exists = (run_dir / "agent_c_optimization_recommendations.json").exists()

        self.assertEqual(recommendations["overall"]["safe_config_action_count"], 1)
        self.assertEqual(dry_run["pending_excluded_domains"], ["bad-directory.example"])
        self.assertTrue(applied["updated"])
        self.assertIn("bad-directory.example", config["excluded_domains"])
        self.assertNotIn("single-case.example", config["excluded_domains"])
        self.assertTrue(suggestions_exists)
        self.assertTrue(legacy_suggestions_exists)

    def test_agent_a_writes_identity_regression_fixtures_without_config_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            rows = []
            for idx in range(3):
                rows.append(
                    {
                        "provider_id": f"p-{idx}",
                        "provider_name": f"Generic Provider {idx}",
                        "candidate_url": f"https://generic{idx}.example",
                        "candidate_domain": f"generic{idx}.example",
                        "agent_b_decision": "unsure",
                        "manual_decision": "unsure",
                        "evidence_score": "31",
                        "independent_search_queries": "",
                        "counter_evidence": "identity_gap_location_or_service_context_missing",
                        "reason_for_unsure": "insufficient_or_conflicting_evidence",
                    }
                )
            _write_test_csv(run_dir / "agent_b_verification_results.csv", rows)
            config_path = run_dir / "scoring.json"
            config_path.write_text(json.dumps(load_config("config/scoring.json")), encoding="utf-8")

            recommendations = run_agent_c_recommendations(run_dir=run_dir)
            applied = apply_agent_optimizations(run_dir=run_dir, config_path=config_path, apply=True)
            fixture_path = Path(applied["identity_regression_fixture"])
            fixture_exists = fixture_path.exists()

        self.assertEqual(recommendations["recommendations"][0]["action"], "write_identity_regression_fixtures")
        self.assertFalse(applied["updated"])
        self.assertTrue(applied["artifacts_updated"])
        self.assertEqual(applied["identity_regression_fixture_rows"], 3)
        self.assertTrue(fixture_exists)
        self.assertEqual(fixture_path.name, "identity_cases.csv")

    def test_human_review_recommendations_write_fixtures_and_platform_rule(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            human_review = run_dir / "human_review.csv"
            human_rows = [
                {
                    "review_batch": "A",
                    "provider_name": "PT GARUDA SPINTEX PRIVATE LIMITED",
                    "current_or_candidate_url": "https://www.kaam24.com/Detail-PT-Garuda",
                    "amazon_detail_url": "https://amazon.example/garuda",
                    "confidence": "39",
                    "your_decision": "replace",
                    "your_true_official_url": "https://www.indiamart.com/company/252021821/",
                    "your_notes": "人工找到的不确定是官网，像是公司在 IndiaMART 上创建的供应商展示页/店铺页",
                    "provider_id": "p-1",
                },
                {
                    "review_batch": "F",
                    "provider_name": "Bitesu India",
                    "current_or_candidate_url": "https://bitesuindia.com/",
                    "amazon_detail_url": "https://amazon.example/bitesu",
                    "confidence": "61",
                    "your_decision": "replace",
                    "your_true_official_url": "https://www.bitesuindia.com/",
                    "your_notes": "AI提供的网址无法打开，但是域名几乎正确，应该是格式问题",
                    "provider_id": "p-2",
                },
                {
                    "review_batch": "A",
                    "provider_name": "Digital Tech Force",
                    "current_or_candidate_url": "https://www.digitalforcetech.com/",
                    "amazon_detail_url": "https://amazon.example/dtf",
                    "confidence": "58",
                    "your_decision": "replace",
                    "your_true_official_url": "https://digitaltechforce.com/",
                    "your_notes": "AI提供的网址名字类似，但是服务内容完全不一致",
                    "provider_id": "p-3",
                },
            ]
            _write_test_csv(human_review, human_rows)
            config = load_config("config/scoring.json")
            config["excluded_domains"] = [domain for domain in config.get("excluded_domains", []) if domain != "indiamart.com"]
            config_path = run_dir / "scoring.json"
            config_path.write_text(json.dumps(config), encoding="utf-8")

            recommendations = run_agent_c_recommendations(run_dir=run_dir, human_review=human_review)
            applied = apply_agent_optimizations(run_dir=run_dir, config_path=config_path, apply=True)
            updated_config = json.loads(config_path.read_text(encoding="utf-8"))

        actions = {item["action"] for item in recommendations["recommendations"]}
        self.assertIn("write_human_review_regression_fixtures", actions)
        self.assertIn("verify_url_variants_before_accept", actions)
        self.assertIn("write_identity_regression_fixtures", actions)
        self.assertIn("indiamart.com", updated_config["excluded_domains"])
        self.assertEqual(applied["human_review_regression_fixture_rows"], 3)
        self.assertEqual(applied["url_reachability_regression_fixture_rows"], 1)
        self.assertGreaterEqual(applied["identity_regression_fixture_rows"], 1)

    def test_human_review_xlsx_formula_urls_feed_no_official_fixtures(self):
        try:
            from openpyxl import Workbook
        except ImportError:
            self.skipTest("openpyxl not installed")
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            human_review = run_dir / "human_review.xlsx"
            workbook = Workbook()
            sheet = workbook.active
            sheet.append(
                [
                    "provider_id",
                    "provider_name",
                    "current_or_candidate_url",
                    "your_decision",
                    "your_true_official_url",
                    "error_type",
                    "your_notes",
                ]
            )
            sheet.append(
                [
                    "p-1",
                    "AA Consulting",
                    '=HYPERLINK("https://aaconsulting.nl/","https://aaconsulting.nl/")',
                    "reject",
                    "",
                    "实际无官网",
                    "同名高分，但人工确认不是该 Amazon provider 的官网",
                ]
            )
            workbook.save(human_review)
            config_path = run_dir / "scoring.json"
            config_path.write_text(json.dumps(load_config("config/scoring.json")), encoding="utf-8")

            recommendations = run_agent_c_recommendations(run_dir=run_dir, human_review=human_review)
            applied = apply_agent_optimizations(run_dir=run_dir, config_path=config_path, apply=True)
            with open(applied["no_official_regression_fixture"], newline="", encoding="utf-8") as f:
                fixture_rows = list(csv.DictReader(f))

        actions = {item["action"] for item in recommendations["recommendations"]}
        self.assertIn("write_no_official_regression_fixtures", actions)
        self.assertEqual(applied["no_official_regression_fixture_rows"], 1)
        self.assertEqual(fixture_rows[0]["candidate_url"], "https://aaconsulting.nl/")
        self.assertIn("confirmed_no_official", fixture_rows[0]["note_tags"])

    def test_evaluate_workflow_balance_counts_false_and_over_rejected_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            baseline = root / "baseline.csv"
            candidate = root / "candidate.csv"
            review = root / "review.csv"
            _write_test_csv(
                baseline,
                [
                    {"provider_id": "p-1", "provider_name": "One", "official_url": "https://one.example", "official_domain": "one.example"},
                    {"provider_id": "p-2", "provider_name": "Two", "official_url": "https://wrong.example", "official_domain": "wrong.example"},
                    {"provider_id": "p-3", "provider_name": "Three", "official_url": "https://three.example", "official_domain": "three.example"},
                    {"provider_id": "p-4", "provider_name": "Four", "official_url": "", "official_domain": ""},
                ],
            )
            _write_test_csv(
                candidate,
                [
                    {"provider_id": "p-1", "provider_name": "One", "official_url": "https://one.example", "official_domain": "one.example", "status": "matched", "confidence": "90"},
                    {"provider_id": "p-2", "provider_name": "Two", "official_url": "https://two.example", "official_domain": "two.example", "status": "matched", "confidence": "90"},
                    {"provider_id": "p-3", "provider_name": "Three", "official_url": "", "official_domain": "", "status": "unresolved", "confidence": "0"},
                    {"provider_id": "p-4", "provider_name": "Four", "official_url": "https://four.example", "official_domain": "four.example", "status": "matched", "confidence": "90"},
                ],
            )
            _write_test_csv(
                review,
                [
                    {"provider_id": "p-2", "provider_name": "Two", "manual_decision": "replace", "manual_url": "https://two.example"},
                    {"provider_id": "p-4", "provider_name": "Four", "manual_decision": "reject", "manual_url": ""},
                ],
            )

            summary = evaluate_balance(
                baseline_final=baseline,
                candidate_final=candidate,
                human_review=review,
                simulate_thresholds=[85],
            )

        self.assertEqual(summary["overall"]["correct_official_rows"], 2)
        self.assertEqual(summary["overall"]["false_official_rows"], 1)
        self.assertEqual(summary["overall"]["over_rejected_rows"], 1)
        self.assertEqual(summary["overall"]["auto_precision"], 0.6667)
        self.assertEqual(summary["threshold_simulations"][0]["threshold"], 85)
        self.assertEqual(summary["threshold_simulations"][0]["official_output_rows"], 3)

    def test_evaluate_workflow_balance_counts_manual_review_capture(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "run"
            baseline = root / "baseline.csv"
            candidate = root / "candidate.csv"
            review = root / "review.csv"
            review_task = run_dir / "review_task.csv"
            agent_b = run_dir / "agent_b/check.csv"
            unresolved = run_dir / "unresolved.csv"
            _write_test_csv(
                baseline,
                [
                    {"provider_id": "p-1", "provider_name": "One", "official_url": "https://one.example", "official_domain": "one.example"},
                    {"provider_id": "p-2", "provider_name": "Two", "official_url": "https://two.example", "official_domain": "two.example"},
                    {"provider_id": "p-3", "provider_name": "Three", "official_url": "https://three.example", "official_domain": "three.example"},
                    {"provider_id": "p-4", "provider_name": "Four", "official_url": "", "official_domain": ""},
                ],
            )
            _write_test_csv(
                candidate,
                [
                    {"provider_id": "p-1", "provider_name": "One", "official_url": "https://one.example", "official_domain": "one.example", "status": "matched", "confidence": "96"},
                    {"provider_id": "p-2", "provider_name": "Two", "official_url": "https://two.example", "official_domain": "two.example", "status": "matched", "confidence": "91"},
                    {"provider_id": "p-3", "provider_name": "Three", "official_url": "", "official_domain": "", "status": "unresolved", "confidence": "68"},
                    {"provider_id": "p-4", "provider_name": "Four", "official_url": "https://four.example", "official_domain": "four.example", "status": "matched", "confidence": "86"},
                ],
            )
            _write_test_csv(
                review,
                [
                    {"provider_id": "p-4", "provider_name": "Four", "manual_decision": "reject", "manual_url": ""},
                ],
            )
            _write_test_csv(
                review_task,
                [
                    {"provider_id": "p-1", "provider_name": "One", "review_reason": "precision_second_pass_accepted_85_plus"},
                    {"provider_id": "p-3", "provider_name": "Three", "review_reason": "recall_unresolved_near_threshold"},
                    {"provider_id": "p-4", "provider_name": "Four", "review_reason": "identity_weak_or_conflicting"},
                ],
            )
            _write_test_csv(
                unresolved,
                [
                    {"provider_id": "p-3", "provider_name": "Three"},
                ],
            )
            _write_test_csv(
                agent_b,
                [
                    {
                        "provider_id": "p-1",
                        "provider_name": "One",
                        "candidate_domain": "one.example",
                        "agent_b_decision": "accept",
                        "manual_url": "",
                        "confidence": "90",
                    },
                    {
                        "provider_id": "p-3",
                        "provider_name": "Three",
                        "candidate_domain": "",
                        "agent_b_decision": "replace",
                        "manual_url": "https://three.example",
                        "confidence": "88",
                    },
                    {
                        "provider_id": "p-4",
                        "provider_name": "Four",
                        "candidate_domain": "four.example",
                        "agent_b_decision": "accept",
                        "manual_url": "",
                        "confidence": "86",
                    },
                ],
            )

            summary = evaluate_balance(
                baseline_final=baseline,
                candidate_final=candidate,
                human_review=review,
                run_dir=run_dir,
            )

        overall = summary["overall"]
        self.assertEqual(overall["manual_review_rows"], 3)
        self.assertEqual(overall["manual_review_labeled_rows"], 3)
        self.assertEqual(overall["manual_review_false_official_rows"], 1)
        self.assertEqual(overall["manual_review_missed_false_official_rows"], 0)
        self.assertEqual(overall["manual_review_over_rejected_rows"], 1)
        self.assertEqual(overall["manual_review_correct_official_rows"], 1)
        self.assertEqual(overall["manual_review_false_official_capture_rate"], 1.0)
        self.assertEqual(overall["manual_review_false_official_share"], 0.3333)
        self.assertEqual(overall["unresolved_rows"], 1)
        self.assertEqual(overall["agent_b_rows"], 3)
        self.assertEqual(overall["agent_b_accept_rows"], 2)
        self.assertEqual(overall["agent_b_replace_rows"], 1)
        self.assertEqual(overall["agent_b_false_official_rows"], 1)
        self.assertEqual(overall["agent_b_false_official_accept_rows"], 1)
        self.assertEqual(overall["agent_b_false_official_catch_rate"], 0.0)
        self.assertEqual(overall["agent_b_correct_official_accept_rate"], 1.0)
        self.assertEqual(overall["agent_b_over_rejected_correct_recovery_rows"], 1)
        self.assertEqual(overall["agent_b_over_rejected_recovery_rate"], 1.0)
        self.assertEqual(overall["agent_b_expected_no_official_accept_or_replace_rows"], 1)
        detail_by_id = {row["provider_id"]: row for row in summary["details"]}
        self.assertEqual(detail_by_id["p-4"]["manual_review_reason"], "identity_weak_or_conflicting")
        self.assertEqual(detail_by_id["p-3"]["agent_b_suggested_domain"], "three.example")
        lanes = {row["review_reason"]: row for row in summary["manual_review_lanes"]}
        self.assertEqual(lanes["identity_weak_or_conflicting"]["false_official_rows"], 1)
        self.assertEqual(lanes["recall_unresolved_near_threshold"]["over_rejected_rows"], 1)
        self.assertEqual(lanes["precision_second_pass_accepted_85_plus"]["correct_official_rows"], 1)
        drop = {row["drop_review_reason"]: row for row in summary["manual_review_lane_drop_simulations"]}
        self.assertEqual(drop["identity_weak_or_conflicting"]["known_false_official_missed_if_dropped"], 1)
        self.assertEqual(drop["recall_unresolved_near_threshold"]["known_over_rejected_missed_if_dropped"], 1)
        self.assertEqual(drop["precision_second_pass_accepted_85_plus"]["known_correct_reviews_removed_if_dropped"], 1)

    def test_evaluate_workflow_balance_can_reuse_labeled_details(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "run"
            details = root / "details.csv"
            candidate = root / "candidate.csv"
            review_task = run_dir / "review_task.csv"
            _write_test_csv(
                details,
                [
                    {
                        "provider_id": "p-1",
                        "provider_name": "One",
                        "label_source": "baseline_unmarked_correct",
                        "expected_kind": "official",
                        "expected_domain": "one.example",
                        "expected_url": "https://one.example",
                    },
                    {
                        "provider_id": "p-2",
                        "provider_name": "Two",
                        "label_source": "human_reject",
                        "expected_kind": "no_official",
                        "expected_domain": "",
                        "expected_url": "",
                    },
                    {
                        "provider_id": "p-3",
                        "provider_name": "Three",
                        "label_source": "baseline_unmarked_correct",
                        "expected_kind": "official",
                        "expected_domain": "three.example",
                        "expected_url": "https://three.example",
                    },
                ],
            )
            _write_test_csv(
                candidate,
                [
                    {"provider_id": "p-1", "provider_name": "One", "official_url": "https://one.example", "official_domain": "one.example", "status": "matched", "confidence": "92"},
                    {"provider_id": "p-2", "provider_name": "Two", "official_url": "https://wrong.example", "official_domain": "wrong.example", "status": "matched", "confidence": "82"},
                    {"provider_id": "p-3", "provider_name": "Three", "official_url": "", "official_domain": "", "status": "unresolved", "confidence": "68"},
                ],
            )
            _write_test_csv(
                review_task,
                [
                    {"provider_id": "p-2", "provider_name": "Two", "review_reason": "precision_low_confidence_auto_match"},
                    {"provider_id": "p-3", "provider_name": "Three", "review_reason": "recall_unresolved_top_candidate"},
                ],
            )

            summary = evaluate_balance_from_details(
                labeled_details=details,
                candidate_final=candidate,
                run_dir=run_dir,
                simulate_thresholds=[83],
            )

        overall = summary["overall"]
        self.assertEqual(overall["labeled_rows"], 3)
        self.assertEqual(overall["correct_official_rows"], 1)
        self.assertEqual(overall["false_official_rows"], 1)
        self.assertEqual(overall["over_rejected_rows"], 1)
        self.assertEqual(overall["manual_review_rows"], 2)
        self.assertEqual(overall["manual_review_false_official_rows"], 1)
        self.assertEqual(overall["manual_review_over_rejected_rows"], 1)
        self.assertEqual(overall["manual_review_false_official_capture_rate"], 1.0)
        self.assertEqual(summary["threshold_simulations"][0]["threshold"], 83)
        self.assertEqual(summary["threshold_simulations"][0]["false_official_rows"], 0)
        self.assertEqual(summary["threshold_simulations"][0]["over_rejected_rows"], 1)
        self.assertEqual(summary["threshold_simulations"][0]["correct_no_official_rows"], 1)
        self.assertEqual(summary["inputs"]["labeled_details"], str(details))

    def test_evaluate_workflow_balance_simulates_agent_b_recall_release_risk(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "run"
            run_dir.mkdir()
            baseline = root / "baseline.csv"
            candidate = root / "candidate.csv"
            human = root / "human.csv"
            review = run_dir / "review_task.csv"
            agent_b = run_dir / "agent_b/check.csv"
            unresolved = run_dir / "unresolved.csv"
            human.write_text("provider_id,manual_decision,manual_url\n", encoding="utf-8")
            _write_test_csv(
                baseline,
                [
                    {"provider_id": "p-1", "provider_name": "One", "official_url": "https://one.example", "official_domain": "one.example"},
                    {"provider_id": "p-2", "provider_name": "Two", "official_url": "https://two.example", "official_domain": "two.example"},
                    {"provider_id": "p-3", "provider_name": "None", "official_url": "", "official_domain": ""},
                ],
            )
            _write_test_csv(
                candidate,
                [
                    {"provider_id": "p-1", "provider_name": "One", "official_url": "", "official_domain": "", "status": "unresolved", "confidence": "69"},
                    {"provider_id": "p-2", "provider_name": "Two", "official_url": "", "official_domain": "", "status": "unresolved", "confidence": "69"},
                    {"provider_id": "p-3", "provider_name": "None", "official_url": "", "official_domain": "", "status": "unresolved", "confidence": "69"},
                ],
            )
            _write_test_csv(
                review,
                [
                    {"provider_id": "p-1", "provider_name": "One", "review_reason": "recall_unresolved_top_candidate"},
                    {"provider_id": "p-2", "provider_name": "Two", "review_reason": "recall_unresolved_top_candidate"},
                    {"provider_id": "p-3", "provider_name": "None", "review_reason": "recall_unresolved_top_candidate"},
                ],
            )
            _write_test_csv(unresolved, [{"provider_id": "p-1"}])
            _write_test_csv(
                agent_b,
                [
                    {
                        "provider_id": "p-1",
                        "provider_name": "One",
                        "candidate_url": "https://one.example",
                        "candidate_domain": "one.example",
                        "agent_b_decision": "unsure",
                        "evidence_score": "80",
                        "confidence": "69",
                        "reason_for_unsure": "recall_candidate_needs_human_confirmation",
                    },
                    {
                        "provider_id": "p-2",
                        "provider_name": "Two",
                        "candidate_url": "https://wrong.example",
                        "candidate_domain": "wrong.example",
                        "agent_b_decision": "unsure",
                        "evidence_score": "80",
                        "confidence": "69",
                        "reason_for_unsure": "recall_candidate_needs_human_confirmation",
                    },
                    {
                        "provider_id": "p-3",
                        "provider_name": "None",
                        "candidate_url": "https://ghost.example",
                        "candidate_domain": "ghost.example",
                        "agent_b_decision": "unsure",
                        "evidence_score": "80",
                        "confidence": "69",
                        "reason_for_unsure": "recall_candidate_needs_human_confirmation",
                    },
                ],
            )

            summary = evaluate_balance(
                baseline_final=baseline,
                candidate_final=candidate,
                human_review=human,
                run_dir=run_dir,
            )
            sim_by_threshold = {
                row["agent_b_evidence_threshold"]: row for row in summary["agent_b_recall_release_simulations"]
            }
            details_by_id = {row["provider_id"]: row for row in summary["details"]}

        self.assertEqual(sim_by_threshold[75]["release_rows"], 3)
        self.assertEqual(sim_by_threshold[75]["correct_recovery_rows"], 1)
        self.assertEqual(sim_by_threshold[75]["wrong_release_rows"], 2)
        self.assertEqual(sim_by_threshold[75]["release_precision"], 0.3333)
        self.assertEqual(details_by_id["p-1"]["agent_b_candidate_domain"], "one.example")

    def test_mine_evidence_patterns_finds_safe_and_risky_recall_patterns(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            balance_json = root / "balance.json"
            agent_b = root / "agent_b.csv"
            output_json = root / "patterns.json"
            output_md = root / "patterns.md"
            balance_json.write_text(
                json.dumps(
                    {
                        "details": [
                            {
                                "provider_id": "p-1",
                                "provider_name": "One Agency",
                                "expected_kind": "official",
                                "expected_domain": "one.example",
                                "manual_review_reason": "recall_unresolved_top_candidate",
                            },
                            {
                                "provider_id": "p-2",
                                "provider_name": "Two Agency",
                                "expected_kind": "official",
                                "expected_domain": "two.example",
                                "manual_review_reason": "recall_unresolved_top_candidate",
                            },
                            {
                                "provider_id": "p-3",
                                "provider_name": "Three Agency",
                                "expected_kind": "official",
                                "expected_domain": "three.example",
                                "manual_review_reason": "recall_unresolved_top_candidate",
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            _write_test_csv(
                agent_b,
                [
                    {
                        "provider_id": "p-1",
                        "provider_name": "One Agency",
                        "candidate_url": "https://one.example",
                        "candidate_domain": "one.example",
                        "agent_b_decision": "unsure",
                        "evidence_score": "80",
                        "supporting_facts": "candidate_pages_fetch_ok; safe_fact; shared_fact",
                        "counter_evidence": "",
                        "reason_for_unsure": "recall_candidate_needs_human_confirmation",
                        "review_reason": "recall_unresolved_top_candidate",
                    },
                    {
                        "provider_id": "p-2",
                        "provider_name": "Two Agency",
                        "candidate_url": "https://two.example",
                        "candidate_domain": "two.example",
                        "agent_b_decision": "unsure",
                        "evidence_score": "80",
                        "supporting_facts": "candidate_pages_fetch_ok; safe_fact; shared_fact",
                        "counter_evidence": "",
                        "reason_for_unsure": "recall_candidate_needs_human_confirmation",
                        "review_reason": "recall_unresolved_top_candidate",
                    },
                    {
                        "provider_id": "p-3",
                        "provider_name": "Three Agency",
                        "candidate_url": "https://wrong.example",
                        "candidate_domain": "wrong.example",
                        "agent_b_decision": "unsure",
                        "evidence_score": "80",
                        "supporting_facts": "candidate_pages_fetch_ok; shared_fact",
                        "counter_evidence": "",
                        "reason_for_unsure": "recall_candidate_needs_human_confirmation",
                        "review_reason": "recall_unresolved_top_candidate",
                    },
                ],
            )

            report = mine_evidence_patterns(
                balance_json=balance_json,
                agent_b_csv=agent_b,
                min_support=2,
                output_json=output_json,
                output_md=output_md,
            )
            safe_patterns = {row["pattern"] for row in report["durable_safe_patterns"]}
            all_patterns = {row["pattern"] for row in report["all_patterns"]}
            md_text = output_md.read_text(encoding="utf-8")
            output_json_exists = output_json.exists()

        self.assertEqual(report["summary"]["rows"], 3)
        self.assertGreaterEqual(report["summary"]["durable_safe_patterns"], 1)
        self.assertIn("has:safe_fact", safe_patterns)
        self.assertTrue(any("has:shared_fact" in pattern for pattern in all_patterns))
        self.assertTrue(output_json_exists)
        self.assertIn("Best candidate pattern", md_text)

    def test_simulate_pattern_release_scores_safe_recall_rules(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            balance_json = root / "balance.json"
            agent_b = root / "agent_b.csv"
            patterns = root / "patterns.json"
            output_json = root / "simulation.json"
            output_md = root / "simulation.md"
            balance_json.write_text(
                json.dumps(
                    {
                        "overall": {
                            "labeled_rows": 5,
                            "expected_official_rows": 3,
                            "expected_no_official_rows": 2,
                            "official_output_rows": 1,
                            "correct_official_rows": 1,
                            "correct_no_official_rows": 2,
                            "false_official_rows": 0,
                            "over_rejected_rows": 2,
                            "auto_precision": 1.0,
                            "official_recall": 0.3333,
                            "overall_accuracy": 0.6,
                        },
                        "details": [
                            {
                                "provider_id": "p-good-1",
                                "provider_name": "Good One",
                                "expected_kind": "official",
                                "expected_domain": "goodone.example",
                                "outcome": "over_rejected",
                                "manual_review_reason": "recall_unresolved_top_candidate",
                            },
                            {
                                "provider_id": "p-good-2",
                                "provider_name": "Good Two",
                                "expected_kind": "official",
                                "expected_domain": "goodtwo.example",
                                "outcome": "over_rejected",
                                "manual_review_reason": "recall_unresolved_top_candidate",
                            },
                            {
                                "provider_id": "p-bad",
                                "provider_name": "Bad One",
                                "expected_kind": "no_official",
                                "expected_domain": "",
                                "outcome": "correct_no_official",
                                "manual_review_reason": "recall_unresolved_top_candidate",
                            },
                            {
                                "provider_id": "p-docs",
                                "provider_name": "Good Docs",
                                "expected_kind": "no_official",
                                "expected_domain": "",
                                "outcome": "correct_no_official",
                                "manual_review_reason": "recall_unresolved_top_candidate",
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            _write_test_csv(
                agent_b,
                [
                    {
                        "provider_id": "p-good-1",
                        "provider_name": "Good One",
                        "candidate_url": "https://goodone.example",
                        "candidate_domain": "goodone.example",
                        "agent_b_decision": "unsure",
                        "evidence_score": "31",
                        "supporting_facts": "candidate_pages_fetch_ok; schema_org_organization_seen",
                        "counter_evidence": "",
                        "reason_for_unsure": "recall_candidate_needs_human_confirmation",
                        "review_reason": "recall_unresolved_top_candidate",
                    },
                    {
                        "provider_id": "p-good-2",
                        "provider_name": "Good Two",
                        "candidate_url": "https://goodtwo.example",
                        "candidate_domain": "goodtwo.example",
                        "agent_b_decision": "unsure",
                        "evidence_score": "31",
                        "supporting_facts": "candidate_pages_fetch_ok; schema_org_organization_seen",
                        "counter_evidence": "",
                        "reason_for_unsure": "recall_candidate_needs_human_confirmation",
                        "review_reason": "recall_unresolved_top_candidate",
                    },
                    {
                        "provider_id": "p-bad",
                        "provider_name": "Bad One",
                        "candidate_url": "https://bad.example",
                        "candidate_domain": "bad.example",
                        "agent_b_decision": "unsure",
                        "evidence_score": "31",
                        "supporting_facts": "candidate_pages_fetch_ok; shared_fact",
                        "counter_evidence": "",
                        "reason_for_unsure": "recall_candidate_needs_human_confirmation",
                        "review_reason": "recall_unresolved_top_candidate",
                    },
                    {
                        "provider_id": "p-docs",
                        "provider_name": "Good Docs",
                        "candidate_url": "https://docs.gooddocs.example",
                        "candidate_domain": "docs.gooddocs.example",
                        "agent_b_decision": "unsure",
                        "evidence_score": "31",
                        "supporting_facts": "candidate_pages_fetch_ok; schema_org_organization_seen",
                        "counter_evidence": "",
                        "reason_for_unsure": "recall_candidate_needs_human_confirmation",
                        "review_reason": "recall_unresolved_top_candidate",
                    },
                ],
            )
            patterns.write_text(
                json.dumps(
                    {
                        "summary": {"scope": "recall"},
                        "durable_safe_patterns": [
                            {
                                "pattern": "domain_relation:exact_provider_slug AND has:schema_org_organization_seen",
                                "features": ["domain_relation:exact_provider_slug", "has:schema_org_organization_seen"],
                                "correct_recovery_rows": 2,
                                "wrong_release_rows": 0,
                            },
                            {
                                "pattern": "has:candidate_pages_fetch_ok",
                                "features": ["has:candidate_pages_fetch_ok"],
                                "correct_recovery_rows": 2,
                                "wrong_release_rows": 1,
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )

            report = simulate_pattern_release(
                balance_json=balance_json,
                agent_b_csv=agent_b,
                pattern_jsons=[patterns],
                min_support=2,
                output_json=output_json,
                output_md=output_md,
            )
            safe = report["safe_patterns"][0]
            md_text = output_md.read_text(encoding="utf-8")
            output_json_exists = output_json.exists()

        self.assertEqual(report["summary"]["safe_pattern_count"], 1)
        self.assertEqual(report["summary"]["actionable_safe_pattern_count"], 1)
        self.assertEqual(report["summary"]["selected_actionable_pattern_count"], 1)
        self.assertEqual(report["summary"]["selected_actionable_correct_recovery_rows"], 2)
        self.assertEqual(safe["pattern"], "domain_relation:exact_provider_slug AND has:schema_org_organization_seen")
        self.assertTrue(safe["actionable"])
        self.assertEqual(safe["correct_recovery_rows"], 2)
        self.assertEqual(safe["wrong_release_rows"], 0)
        self.assertEqual(safe["simulated_overall"]["overall_accuracy"], 1.0)
        self.assertTrue(output_json_exists)
        self.assertIn("Pattern Release Simulation", md_text)

    def test_simulate_pattern_release_selects_actionable_set_from_all_patterns(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            balance_json = root / "balance.json"
            agent_b = root / "agent_b.csv"
            patterns = root / "patterns.json"
            balance_json.write_text(
                json.dumps(
                    {
                        "overall": {
                            "labeled_rows": 6,
                            "expected_official_rows": 5,
                            "expected_no_official_rows": 1,
                            "official_output_rows": 1,
                            "correct_official_rows": 1,
                            "correct_no_official_rows": 1,
                            "false_official_rows": 0,
                            "over_rejected_rows": 4,
                            "auto_precision": 1.0,
                            "official_recall": 0.2,
                            "overall_accuracy": 0.3333,
                        },
                        "details": [
                            {
                                "provider_id": "exact-1",
                                "provider_name": "Exact One",
                                "expected_kind": "official",
                                "expected_domain": "exactone.example",
                                "outcome": "over_rejected",
                                "manual_review_reason": "recall_unresolved_top_candidate",
                            },
                            {
                                "provider_id": "exact-2",
                                "provider_name": "Exact Two",
                                "expected_kind": "official",
                                "expected_domain": "exacttwo.example",
                                "outcome": "over_rejected",
                                "manual_review_reason": "recall_unresolved_top_candidate",
                            },
                            {
                                "provider_id": "service-1",
                                "provider_name": "Service One Consulting",
                                "expected_kind": "official",
                                "expected_domain": "serviceone.example",
                                "outcome": "over_rejected",
                                "manual_review_reason": "recall_unresolved_top_candidate",
                            },
                            {
                                "provider_id": "service-2",
                                "provider_name": "Service Two Consulting",
                                "expected_kind": "official",
                                "expected_domain": "servicetwo.example",
                                "outcome": "over_rejected",
                                "manual_review_reason": "recall_unresolved_top_candidate",
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            _write_test_csv(
                agent_b,
                [
                    {
                        "provider_id": "exact-1",
                        "provider_name": "Exact One",
                        "candidate_url": "https://exactone.example",
                        "candidate_domain": "exactone.example",
                        "agent_b_decision": "unsure",
                        "evidence_score": "31",
                        "supporting_facts": "candidate_pages_fetch_ok; schema_org_organization_seen",
                        "counter_evidence": "",
                        "reason_for_unsure": "recall_candidate_needs_human_confirmation",
                        "review_reason": "recall_unresolved_top_candidate",
                    },
                    {
                        "provider_id": "exact-2",
                        "provider_name": "Exact Two",
                        "candidate_url": "https://exacttwo.example",
                        "candidate_domain": "exacttwo.example",
                        "agent_b_decision": "unsure",
                        "evidence_score": "31",
                        "supporting_facts": "candidate_pages_fetch_ok; schema_org_organization_seen",
                        "counter_evidence": "",
                        "reason_for_unsure": "recall_candidate_needs_human_confirmation",
                        "review_reason": "recall_unresolved_top_candidate",
                    },
                    {
                        "provider_id": "service-1",
                        "provider_name": "Service One Consulting",
                        "candidate_url": "https://serviceone.example",
                        "candidate_domain": "serviceone.example",
                        "agent_b_decision": "unsure",
                        "evidence_score": "31",
                        "supporting_facts": "candidate_pages_fetch_ok; some_service_content_matches",
                        "counter_evidence": "",
                        "reason_for_unsure": "recall_candidate_needs_human_confirmation",
                        "review_reason": "recall_unresolved_top_candidate",
                    },
                    {
                        "provider_id": "service-2",
                        "provider_name": "Service Two Consulting",
                        "candidate_url": "https://servicetwo.example",
                        "candidate_domain": "servicetwo.example",
                        "agent_b_decision": "unsure",
                        "evidence_score": "31",
                        "supporting_facts": "candidate_pages_fetch_ok; some_service_content_matches",
                        "counter_evidence": "",
                        "reason_for_unsure": "recall_candidate_needs_human_confirmation",
                        "review_reason": "recall_unresolved_top_candidate",
                    },
                ],
            )
            patterns.write_text(
                json.dumps(
                    {
                        "summary": {"scope": "recall"},
                        "durable_safe_patterns": [],
                        "all_patterns": [
                            {
                                "pattern": "domain_relation:exact_provider_slug AND has:schema_org_organization_seen",
                                "features": [
                                    "domain_relation:exact_provider_slug",
                                    "has:schema_org_organization_seen",
                                ],
                                "correct_recovery_rows": 2,
                                "wrong_release_rows": 0,
                            },
                            {
                                "pattern": "domain_relation:provider_slug_contains_domain AND has:some_service_content_matches",
                                "features": [
                                    "domain_relation:provider_slug_contains_domain",
                                    "has:some_service_content_matches",
                                ],
                                "correct_recovery_rows": 2,
                                "wrong_release_rows": 0,
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )

            report = simulate_pattern_release(
                balance_json=balance_json,
                agent_b_csv=agent_b,
                pattern_jsons=[patterns],
                min_support=2,
            )

        self.assertEqual(report["summary"]["patterns_loaded"], 2)
        self.assertEqual(report["summary"]["actionable_safe_pattern_count"], 2)
        self.assertEqual(report["summary"]["selected_actionable_pattern_count"], 2)
        self.assertEqual(report["summary"]["selected_actionable_correct_recovery_rows"], 4)
        self.assertEqual(report["summary"]["selected_actionable_wrong_release_rows"], 0)
        self.assertEqual(report["summary"]["selected_actionable_accuracy"], 1.0)

    def test_apply_pattern_release_experiment_outputs_candidate_final(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            final_csv = root / "official_sites.csv"
            agent_b = root / "agent_b.csv"
            patterns = root / "pattern_release_simulation.json"
            output_csv = root / "official_sites_experiment.csv"
            output_xlsx = root / "official_sites_experiment.xlsx"
            summary_json = root / "summary.json"
            _write_test_csv(
                final_csv,
                [
                    {
                        "provider_id": "release",
                        "provider_name": "Release Brand",
                        "provider_detail_url": "https://amazon.example/release",
                        "listing_logo_url": "",
                        "official_url": "",
                        "official_domain": "",
                        "status": "unresolved",
                        "decision_source": "pending_review",
                        "confidence": "69",
                        "source_status": "needs_review",
                        "evidence_summary": "candidate",
                        "candidate_count": "1",
                        "scored_candidate_count": "1",
                        "service_apis": "[]",
                        "provider_locations": "[]",
                        "notes": "",
                    },
                    {
                        "provider_id": "docs",
                        "provider_name": "Docs Brand",
                        "provider_detail_url": "https://amazon.example/docs",
                        "listing_logo_url": "",
                        "official_url": "",
                        "official_domain": "",
                        "status": "unresolved",
                        "decision_source": "pending_review",
                        "confidence": "69",
                        "source_status": "needs_review",
                        "evidence_summary": "candidate",
                        "candidate_count": "1",
                        "scored_candidate_count": "1",
                        "service_apis": "[]",
                        "provider_locations": "[]",
                        "notes": "",
                    },
                    {
                        "provider_id": "kept",
                        "provider_name": "Kept Brand",
                        "provider_detail_url": "https://amazon.example/kept",
                        "listing_logo_url": "",
                        "official_url": "https://kept.example/",
                        "official_domain": "kept.example",
                        "status": "matched",
                        "decision_source": "auto_matched",
                        "confidence": "90",
                        "source_status": "matched",
                        "evidence_summary": "already accepted",
                        "candidate_count": "1",
                        "scored_candidate_count": "1",
                        "service_apis": "[]",
                        "provider_locations": "[]",
                        "notes": "",
                    },
                ],
            )
            _write_test_csv(
                agent_b,
                [
                    {
                        "provider_id": "release",
                        "provider_name": "Release Brand",
                        "candidate_url": "https://releasebrand.example/",
                        "candidate_domain": "releasebrand.example",
                        "agent_b_decision": "unsure",
                        "confidence": "69",
                        "evidence_score": "31",
                        "supporting_facts": "candidate_pages_fetch_ok; schema_org_organization_seen",
                        "counter_evidence": "",
                        "reason_for_unsure": "recall_candidate_needs_human_confirmation",
                        "review_reason": "recall_unresolved_top_candidate",
                    },
                    {
                        "provider_id": "kept",
                        "provider_name": "Kept Brand",
                        "candidate_url": "https://wrong.example/",
                        "candidate_domain": "wrong.example",
                        "agent_b_decision": "unsure",
                        "confidence": "69",
                        "evidence_score": "31",
                        "supporting_facts": "candidate_pages_fetch_ok; schema_org_organization_seen",
                        "counter_evidence": "",
                        "reason_for_unsure": "recall_candidate_needs_human_confirmation",
                        "review_reason": "recall_unresolved_top_candidate",
                    },
                    {
                        "provider_id": "docs",
                        "provider_name": "Docs Brand",
                        "candidate_url": "https://docs.docsbrand.example/",
                        "candidate_domain": "docs.docsbrand.example",
                        "agent_b_decision": "unsure",
                        "confidence": "69",
                        "evidence_score": "31",
                        "supporting_facts": "candidate_pages_fetch_ok; schema_org_organization_seen",
                        "counter_evidence": "",
                        "reason_for_unsure": "recall_candidate_needs_human_confirmation",
                        "review_reason": "recall_unresolved_top_candidate",
                    },
                ],
            )
            patterns.write_text(
                json.dumps(
                    {
                        "summary": {"scope": "recall"},
                        "selected_actionable_pattern_set": [
                            {
                                "pattern": "agent_b_score<60 AND domain_relation:exact_provider_slug AND has:schema_org_organization_seen",
                                "features": [
                                    "agent_b_score<60",
                                    "domain_relation:exact_provider_slug",
                                    "has:schema_org_organization_seen",
                                ],
                                "correct_recovery_rows": 1,
                                "wrong_release_rows": 0,
                            }
                        ],
                        "actionable_safe_patterns": [
                            {
                                "pattern": "domain_relation:no_such_pattern AND has:schema_org_organization_seen",
                                "features": [
                                    "domain_relation:no_such_pattern",
                                    "has:schema_org_organization_seen",
                                ],
                                "correct_recovery_rows": 99,
                                "wrong_release_rows": 0,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            summary = apply_pattern_release_experiment(
                final_csv=final_csv,
                agent_b_csv=agent_b,
                pattern_jsons=[patterns],
                output_csv=output_csv,
                output_xlsx=output_xlsx,
                summary_json=summary_json,
            )
            with output_csv.open(newline="", encoding="utf-8") as f:
                rows = {row["provider_id"]: row for row in csv.DictReader(f)}
            output_xlsx_exists = output_xlsx.exists()
            summary_json_exists = summary_json.exists()

        self.assertEqual(summary["released_rows"], 1)
        self.assertEqual(rows["release"]["status"], "experimental_released")
        self.assertEqual(rows["docs"]["status"], "unresolved")
        self.assertEqual(rows["docs"]["official_domain"], "")
        self.assertEqual(rows["release"]["official_domain"], "releasebrand.example")
        self.assertEqual(rows["kept"]["official_domain"], "kept.example")
        self.assertTrue(output_xlsx_exists)
        self.assertTrue(summary_json_exists)

    def test_apply_pattern_release_to_run_updates_canonical_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "run"
            agent_b_dir = run_dir / "agent_b"
            final_csv = run_dir / "official_sites.csv"
            unresolved_csv = run_dir / "unresolved.csv"
            agent_b_csv = agent_b_dir / "check.csv"
            patterns = root / "pattern_release_simulation.json"
            manifest = run_dir / "manifest.json"
            run_dir.mkdir(parents=True)
            agent_b_dir.mkdir(parents=True)
            final_rows = [
                {
                    "provider_id": "release",
                    "provider_name": "Release Brand",
                    "provider_detail_url": "https://amazon.example/release",
                    "listing_logo_url": "",
                    "official_url": "",
                    "official_domain": "",
                    "status": "unresolved",
                    "decision_source": "pending_review",
                    "confidence": "69",
                    "source_status": "needs_review",
                    "evidence_summary": "candidate",
                    "candidate_count": "1",
                    "scored_candidate_count": "1",
                    "service_apis": "[]",
                    "provider_locations": "[]",
                    "notes": "",
                },
                {
                    "provider_id": "kept",
                    "provider_name": "Kept Brand",
                    "provider_detail_url": "https://amazon.example/kept",
                    "listing_logo_url": "",
                    "official_url": "https://kept.example/",
                    "official_domain": "kept.example",
                    "status": "matched",
                    "decision_source": "auto_matched",
                    "confidence": "100",
                    "source_status": "matched",
                    "evidence_summary": "domain_exact_provider_slug",
                    "candidate_count": "1",
                    "scored_candidate_count": "1",
                    "service_apis": "[]",
                    "provider_locations": "[]",
                    "notes": "",
                },
            ]
            _write_test_csv(final_csv, final_rows)
            _write_test_csv(unresolved_csv, [final_rows[0]])
            _write_test_csv(
                agent_b_csv,
                [
                    {
                        "provider_id": "release",
                        "provider_name": "Release Brand",
                        "candidate_url": "https://releasebrand.example/",
                        "candidate_domain": "releasebrand.example",
                        "agent_b_decision": "unsure",
                        "confidence": "69",
                        "evidence_score": "31",
                        "supporting_facts": "candidate_pages_fetch_ok; schema_org_organization_seen",
                        "counter_evidence": "",
                        "reason_for_unsure": "recall_candidate_needs_human_confirmation",
                        "review_reason": "recall_unresolved_top_candidate",
                    }
                ],
            )
            patterns.write_text(
                json.dumps(
                    {
                        "summary": {"scope": "recall"},
                        "selected_actionable_pattern_set": [
                            {
                                "pattern": "agent_b_score<60 AND domain_relation:exact_provider_slug AND has:schema_org_organization_seen",
                                "features": [
                                    "agent_b_score<60",
                                    "domain_relation:exact_provider_slug",
                                    "has:schema_org_organization_seen",
                                ],
                                "correct_recovery_rows": 1,
                                "wrong_release_rows": 0,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            manifest.write_text(json.dumps({"summary": {}, "outputs": {}}), encoding="utf-8")

            summary = apply_pattern_release_to_run(
                run_dir=run_dir,
                pattern_jsons=[patterns],
                write_xlsx=True,
            )
            with final_csv.open(newline="", encoding="utf-8") as f:
                rows = {row["provider_id"]: row for row in csv.DictReader(f)}
            with unresolved_csv.open(newline="", encoding="utf-8") as f:
                unresolved_rows = list(csv.DictReader(f))
            review_task = run_dir / "review_task.csv"
            with review_task.open(newline="", encoding="utf-8") as f:
                review_rows = list(csv.DictReader(f))
            manifest_data = json.loads(manifest.read_text(encoding="utf-8"))
            xlsx_exists = (run_dir / "official_sites.xlsx").exists()
            summary_exists = (run_dir / "agent_a/pattern_release_applied.json").exists()

        self.assertEqual(summary["released_rows"], 1)
        self.assertEqual(summary["unresolved_rows"], 0)
        self.assertEqual(rows["release"]["status"], "calibrated_released")
        self.assertEqual(rows["release"]["decision_source"], "calibrated_pattern_release")
        self.assertEqual(rows["release"]["official_domain"], "releasebrand.example")
        self.assertEqual(rows["release"]["provider_detail_url"], "https://amazon.example/release")
        self.assertEqual(unresolved_rows, [])
        self.assertEqual(review_rows[0]["review_reason"], "precision_calibrated_pattern_release")
        self.assertTrue(xlsx_exists)
        self.assertTrue(summary_exists)
        self.assertEqual(manifest_data["summary"]["pattern_release_applied_rows"], 1)
        self.assertTrue(manifest_data["summary"]["quality_passed"])

    def test_apply_pattern_release_to_run_reads_legacy_second_pass_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "run"
            agent_b_dir = run_dir / "agent_b"
            legacy_final = run_dir / "provider_final_official_websites_second_pass.csv"
            legacy_unresolved = run_dir / "provider_unresolved_second_pass.csv"
            agent_b_csv = agent_b_dir / "check.csv"
            patterns = root / "pattern_release_simulation.json"
            run_dir.mkdir(parents=True)
            agent_b_dir.mkdir(parents=True)
            source_row = {
                "provider_id": "legacy",
                "provider_name": "Legacy Brand",
                "provider_detail_url": "https://amazon.example/legacy",
                "listing_logo_url": "",
                "official_url": "",
                "official_domain": "",
                "status": "unresolved",
                "decision_source": "pending_review",
                "confidence": "69",
                "source_status": "needs_review",
                "evidence_summary": "candidate",
                "candidate_count": "1",
                "scored_candidate_count": "1",
                "service_apis": "[]",
                "provider_locations": "[]",
                "notes": "",
            }
            _write_test_csv(legacy_final, [source_row])
            _write_test_csv(legacy_unresolved, [source_row])
            _write_test_csv(
                agent_b_csv,
                [
                    {
                        "provider_id": "legacy",
                        "provider_name": "Legacy Brand",
                        "candidate_url": "https://legacybrand.example/",
                        "candidate_domain": "legacybrand.example",
                        "agent_b_decision": "unsure",
                        "confidence": "69",
                        "evidence_score": "31",
                        "supporting_facts": "candidate_pages_fetch_ok; schema_org_organization_seen",
                        "counter_evidence": "",
                        "reason_for_unsure": "recall_candidate_needs_human_confirmation",
                        "review_reason": "recall_unresolved_top_candidate",
                    }
                ],
            )
            patterns.write_text(
                json.dumps(
                    {
                        "summary": {"scope": "recall"},
                        "selected_actionable_pattern_set": [
                            {
                                "pattern": "agent_b_score<60 AND domain_relation:exact_provider_slug AND has:schema_org_organization_seen",
                                "features": [
                                    "agent_b_score<60",
                                    "domain_relation:exact_provider_slug",
                                    "has:schema_org_organization_seen",
                                ],
                                "correct_recovery_rows": 1,
                                "wrong_release_rows": 0,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            summary = apply_pattern_release_to_run(
                run_dir=run_dir,
                pattern_jsons=[patterns],
                write_xlsx=False,
            )
            with (run_dir / "official_sites.csv").open(newline="", encoding="utf-8") as f:
                canonical_rows = list(csv.DictReader(f))
            with legacy_final.open(newline="", encoding="utf-8") as f:
                legacy_rows = list(csv.DictReader(f))

        self.assertEqual(summary["source_final_csv"], str(legacy_final))
        self.assertEqual(summary["released_rows"], 1)
        self.assertEqual(canonical_rows[0]["official_domain"], "legacybrand.example")
        self.assertEqual(legacy_rows[0]["official_domain"], "legacybrand.example")

    def test_build_balance_report_recommends_current_threshold_and_summarizes_batch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            labeled = root / "balance.json"
            review = root / "review.csv"
            agent_b = root / "agent_b.csv"
            out_json = root / "report.json"
            out_md = root / "report.md"
            labeled.write_text(
                json.dumps(
                    {
                        "overall": {
                            "labeled_rows": 4,
                            "auto_precision": 0.9,
                            "official_recall": 0.86,
                            "false_official_rows": 1,
                            "over_rejected_rows": 1,
                            "manual_review_rows": 2,
                            "manual_review_false_official_capture_rate": 1.0,
                            "agent_b_false_official_accept_rate": 0.0,
                        },
                        "threshold_simulations": [
                            {"threshold": 75, "overall_accuracy": 0.81, "official_recall": 0.86, "false_official_rows": 8},
                            {"threshold": 82, "overall_accuracy": 0.81, "official_recall": 0.83, "false_official_rows": 6},
                        ],
                        "details": [
                            {
                                "provider_id": "false-82",
                                "provider_name": "False At 82",
                                "outcome": "false_official",
                                "output_confidence": "82",
                                "output_domain": "wrong.example",
                                "expected_domain": "",
                                "manual_review_reason": "precision_low_confidence_auto_match",
                            }
                        ],
                        "agent_b_recall_release_simulations": [
                            {
                                "agent_b_evidence_threshold": 75,
                                "release_rows": 4,
                                "correct_recovery_rows": 1,
                                "wrong_release_rows": 3,
                                "release_precision": 0.25,
                            }
                        ],
                        "manual_review_lanes": [
                            {
                                "review_reason": "precision_low_confidence_auto_match",
                                "review_task_rows": 10,
                                "labeled_rows": 3,
                                "false_official_rows": 1,
                                "over_rejected_rows": 0,
                                "correct_official_rows": 2,
                                "correct_no_official_rows": 0,
                                "risk_rows": 1,
                                "risk_share_of_labeled_lane": 0.3333,
                                "correct_share_of_labeled_lane": 0.6667,
                            },
                            {
                                "review_reason": "recall_unresolved_top_candidate",
                                "review_task_rows": 8,
                                "labeled_rows": 2,
                                "false_official_rows": 0,
                                "over_rejected_rows": 1,
                                "correct_official_rows": 0,
                                "correct_no_official_rows": 1,
                                "risk_rows": 1,
                                "risk_share_of_labeled_lane": 0.5,
                                "correct_share_of_labeled_lane": 0.5,
                            },
                            {
                                "review_reason": "precision_calibrated_pattern_release",
                                "review_task_rows": 4,
                                "labeled_rows": 4,
                                "false_official_rows": 0,
                                "over_rejected_rows": 0,
                                "correct_official_rows": 4,
                                "correct_no_official_rows": 0,
                                "risk_rows": 0,
                                "risk_share_of_labeled_lane": 0.0,
                                "correct_share_of_labeled_lane": 1.0,
                            },
                            {
                                "review_reason": "precision_second_pass_accepted_lt70",
                                "review_task_rows": 3,
                                "labeled_rows": 1,
                                "false_official_rows": 0,
                                "over_rejected_rows": 0,
                                "correct_official_rows": 1,
                                "correct_no_official_rows": 0,
                                "risk_rows": 0,
                                "risk_share_of_labeled_lane": 0.0,
                                "correct_share_of_labeled_lane": 1.0,
                            },
                        ],
                        "manual_review_lane_drop_simulations": [
                            {
                                "drop_review_reason": "precision_low_confidence_auto_match",
                                "manual_review_rows_removed": 10,
                                "known_false_official_missed_if_dropped": 1,
                                "known_over_rejected_missed_if_dropped": 0,
                                "known_correct_reviews_removed_if_dropped": 2,
                            },
                            {
                                "drop_review_reason": "recall_unresolved_top_candidate",
                                "manual_review_rows_removed": 8,
                                "known_false_official_missed_if_dropped": 0,
                                "known_over_rejected_missed_if_dropped": 1,
                                "known_correct_reviews_removed_if_dropped": 1,
                            },
                            {
                                "drop_review_reason": "precision_calibrated_pattern_release",
                                "manual_review_rows_removed": 4,
                                "known_false_official_missed_if_dropped": 0,
                                "known_over_rejected_missed_if_dropped": 0,
                                "known_correct_reviews_removed_if_dropped": 4,
                            },
                            {
                                "drop_review_reason": "precision_second_pass_accepted_lt70",
                                "manual_review_rows_removed": 3,
                                "known_false_official_missed_if_dropped": 0,
                                "known_over_rejected_missed_if_dropped": 0,
                                "known_correct_reviews_removed_if_dropped": 1,
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            pattern_release = root / "pattern_release.json"
            pattern_release.write_text(
                json.dumps(
                    {
                        "summary": {
                            "scope": "recall",
                            "baseline_overall_accuracy": 0.81,
                            "selected_actionable_pattern_count": 2,
                            "selected_actionable_correct_recovery_rows": 4,
                            "selected_actionable_wrong_release_rows": 0,
                            "selected_actionable_accuracy": 0.85,
                            "selected_actionable_auto_precision": 0.907,
                            "selected_actionable_official_recall": 0.907,
                        },
                        "selected_actionable_pattern_set": [
                            {
                                "pattern": "domain_relation:exact_provider_slug AND has:schema_org_organization_seen",
                                "features": [
                                    "domain_relation:exact_provider_slug",
                                    "has:schema_org_organization_seen",
                                ],
                                "correct_recovery_rows": 2,
                                "wrong_release_rows": 0,
                            }
                        ],
                        "selected_actionable_release_summary": {
                            "pattern_count": 2,
                            "correct_recovery_rows": 4,
                            "wrong_release_rows": 0,
                            "simulated_overall": {
                                "overall_accuracy": 0.85,
                                "auto_precision": 0.907,
                                "official_recall": 0.907,
                            },
                        },
                    }
                ),
                encoding="utf-8",
            )
            _write_test_csv(
                review,
                [
                    {"provider_id": "p-1", "review_reason": "precision_low_confidence_auto_match"},
                    {"provider_id": "p-2", "review_reason": "recall_unresolved_top_candidate"},
                ],
            )
            _write_test_csv(
                agent_b,
                [
                    {"provider_id": "p-1", "review_reason": "precision_low_confidence_auto_match", "agent_b_decision": "accept", "reason_for_unsure": ""},
                    {"provider_id": "p-2", "review_reason": "recall_unresolved_top_candidate", "agent_b_decision": "unsure", "reason_for_unsure": "agent_b_row_timeout"},
                ],
            )

            report = build_balance_report(
                labeled_eval_json=labeled,
                batch_review_csv=review,
                batch_agent_b_csv=agent_b,
                batch_total_rows=4,
                pattern_release_jsons=[pattern_release],
                output_json=out_json,
                output_md=out_md,
            )
            md_text = out_md.read_text(encoding="utf-8")
            json_exists = out_json.exists()

        self.assertEqual(report["summary"]["recommended_threshold"], 75)
        self.assertEqual(report["summary"]["recommended_agent_b_recall_release"], "manual_only")
        self.assertEqual(report["summary"]["recommended_pattern_release"], "narrow_pattern_release_candidate")
        self.assertEqual(report["summary"]["pattern_release_source_path"], str(pattern_release))
        self.assertEqual(report["summary"]["pattern_release_correct_rows"], 4)
        self.assertEqual(report["summary"]["pattern_release_wrong_rows"], 0)
        self.assertEqual(report["summary"]["protected_review_lane_count"], 2)
        self.assertEqual(
            report["summary"]["protected_review_lanes"],
            ["precision_low_confidence_auto_match", "recall_unresolved_top_candidate"],
        )
        self.assertEqual(report["summary"]["spot_check_candidate_lanes"], ["precision_calibrated_pattern_release"])
        self.assertEqual(report["summary"]["more_label_review_lanes"], ["precision_second_pass_accepted_lt70"])
        self.assertEqual(report["summary"]["batch_review_rows"], 2)
        self.assertEqual(report["summary"]["batch_review_rate"], 0.5)
        self.assertEqual(report["summary"]["batch_agent_b_timeout_rows"], 1)
        self.assertIn("Keep auto-accept threshold at 75", md_text)
        self.assertIn("Do not remove protected review lanes yet", md_text)
        self.assertIn("Protected Lanes", md_text)
        self.assertIn("Spot-Check Candidates", md_text)
        self.assertIn("Needs More Labels", md_text)
        self.assertIn("AgentB Recall Release Simulation", md_text)
        self.assertIn(str(pattern_release), md_text)
        self.assertIn("Prefer narrow pattern release over global threshold relaxation", md_text)
        self.assertIn("Pattern Release", md_text)
        self.assertTrue(json_exists)

    def test_build_release_policy_report_recommends_guarded_pattern_release(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            baseline = root / "baseline.json"
            calibrated = root / "calibrated.json"
            pattern = root / "pattern.json"
            balance = root / "balance.json"
            batch = root / "batch.json"
            output_json = root / "policy.json"
            output_md = root / "policy.md"
            baseline.write_text(
                json.dumps(
                    {
                        "overall": {
                            "overall_accuracy": 0.81,
                            "auto_precision": 0.9024,
                            "official_recall": 0.8605,
                            "false_official_rows": 8,
                            "over_rejected_rows": 11,
                            "official_output_rows": 82,
                            "correct_official_rows": 74,
                        }
                    }
                ),
                encoding="utf-8",
            )
            calibrated.write_text(
                json.dumps(
                    {
                        "overall": {
                            "overall_accuracy": 0.85,
                            "auto_precision": 0.907,
                            "official_recall": 0.907,
                            "false_official_rows": 8,
                            "over_rejected_rows": 7,
                            "official_output_rows": 86,
                            "correct_official_rows": 78,
                        }
                    }
                ),
                encoding="utf-8",
            )
            pattern.write_text(
                json.dumps(
                    {
                        "summary": {
                            "selected_actionable_pattern_count": 2,
                            "selected_actionable_correct_recovery_rows": 4,
                            "selected_actionable_wrong_release_rows": 0,
                        }
                    }
                ),
                encoding="utf-8",
            )
            balance.write_text(
                json.dumps(
                    {
                        "summary": {
                            "recommended_threshold": 75,
                            "agent_b_recall_release_correct_rows": 6,
                            "agent_b_recall_release_wrong_rows": 9,
                        }
                    }
                ),
                encoding="utf-8",
            )
            batch.write_text(
                json.dumps(
                    {
                        "run_dir": "outputs/current_workflow_300_cycle3_20260529",
                        "released_rows": 0,
                        "official_url_rows": 276,
                        "unresolved_rows": 24,
                        "quality_passed": True,
                    }
                ),
                encoding="utf-8",
            )

            report = build_release_policy_report(
                baseline_eval_json=baseline,
                calibrated_eval_json=calibrated,
                pattern_release_json=pattern,
                balance_report_json=balance,
                batch_application_jsons=[batch],
                output_json=output_json,
                output_md=output_md,
            )
            md_text = output_md.read_text(encoding="utf-8")
            output_json_exists = output_json.exists()

        self.assertEqual(report["summary"]["recommended_first_pass_threshold"], 75)
        self.assertEqual(report["summary"]["recommended_second_pass_threshold"], 75)
        self.assertEqual(report["summary"]["raw_agent_b_recall_release"], "manual_only")
        self.assertEqual(report["summary"]["calibrated_pattern_release"], "enabled_with_guard_no_batch_release")
        self.assertEqual(report["summary"]["accuracy_delta"], 0.04)
        self.assertEqual(report["summary"]["false_official_delta"], 0)
        self.assertIn("do not globally lower thresholds", md_text)
        self.assertTrue(output_json_exists)

    def test_build_threshold_boundary_report_keeps_threshold_and_review_band(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            labeled = root / "labeled.json"
            pattern = root / "pattern.json"
            policy = root / "policy.json"
            output_json = root / "boundary.json"
            output_md = root / "boundary.md"
            labeled.write_text(
                json.dumps(
                    {
                        "threshold_simulations": [
                            {
                                "threshold": 75,
                                "overall_accuracy": 0.81,
                                "auto_precision": 0.9024,
                                "official_recall": 0.8605,
                                "false_official_rows": 8,
                                "over_rejected_rows": 11,
                                "official_output_rows": 82,
                                "correct_official_rows": 74,
                            },
                            {
                                "threshold": 82,
                                "overall_accuracy": 0.81,
                                "auto_precision": 0.9231,
                                "official_recall": 0.8372,
                                "false_official_rows": 6,
                                "over_rejected_rows": 13,
                                "official_output_rows": 78,
                                "correct_official_rows": 72,
                            },
                            {
                                "threshold": 85,
                                "overall_accuracy": 0.78,
                                "auto_precision": 0.9315,
                                "official_recall": 0.7907,
                                "false_official_rows": 5,
                                "over_rejected_rows": 17,
                                "official_output_rows": 73,
                                "correct_official_rows": 68,
                            },
                        ],
                        "details": [
                            {
                                "provider_id": "false-82",
                                "provider_name": "False At 82",
                                "outcome": "false_official",
                                "output_confidence": "82",
                                "output_domain": "wrong.example",
                                "expected_domain": "",
                                "manual_review_reason": "precision_low_confidence_auto_match",
                            }
                        ],
                        "agent_b_recall_release_simulations": [
                            {
                                "agent_b_evidence_threshold": 0,
                                "correct_recovery_rows": 6,
                                "wrong_release_rows": 9,
                                "release_precision": 0.4,
                            },
                            {
                                "agent_b_evidence_threshold": 75,
                                "correct_recovery_rows": 1,
                                "wrong_release_rows": 3,
                                "release_precision": 0.25,
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            pattern.write_text(
                json.dumps(
                    {
                        "summary": {
                            "selected_actionable_pattern_count": 2,
                            "selected_actionable_correct_recovery_rows": 4,
                            "selected_actionable_wrong_release_rows": 0,
                        }
                    }
                ),
                encoding="utf-8",
            )
            policy.write_text(
                json.dumps(
                    {
                        "summary": {
                            "recommended_first_pass_threshold": 75,
                            "recommended_second_pass_threshold": 75,
                            "calibrated_pattern_release": "enabled_with_guard_no_batch_release",
                        }
                    }
                ),
                encoding="utf-8",
            )

            report = build_threshold_boundary_report(
                labeled_eval_json=labeled,
                pattern_release_json=pattern,
                policy_report_json=policy,
                output_json=output_json,
                output_md=output_md,
            )
            md_text = output_md.read_text(encoding="utf-8")
            output_json_exists = output_json.exists()

        self.assertEqual(report["summary"]["recommended_global_accept_threshold"], 75)
        self.assertEqual(report["summary"]["best_labeled_accuracy_threshold"], 75)
        self.assertEqual(report["summary"]["precision_watch_min"], 75)
        self.assertEqual(report["summary"]["precision_watch_max"], 82)
        self.assertEqual(
            report["summary"]["recommended_matched_review_confidence_below"],
            DEFAULT_MATCHED_REVIEW_CONFIDENCE_CUTOFF,
        )
        self.assertEqual(report["summary"]["observed_low_confidence_false_official_max"], 82)
        self.assertEqual(report["thresholds"]["precision_boundary"]["threshold"], 82)
        self.assertEqual(report["thresholds"]["precision_boundary"]["recommended_use"], "review_lane_only")
        self.assertEqual(report["summary"]["raw_agent_b_recall_release"], "manual_only")
        self.assertEqual(report["summary"]["calibrated_pattern_release"], "enabled_with_guard_no_batch_release")
        self.assertIn("high-value precision review band", md_text)
        self.assertTrue(output_json_exists)

    def test_build_calibration_review_sample_prioritizes_high_value_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            review = root / "review.csv"
            agent_b = root / "agent_b.csv"
            output_csv = root / "sample.csv"
            output_xlsx = root / "sample.xlsx"
            _write_test_csv(
                review,
                [
                    {
                        "provider_id": "timeout",
                        "provider_name": "Timeout Row",
                        "provider_detail_url": "https://amazon.example/timeout",
                        "official_url": "https://timeout.example",
                        "official_domain": "timeout.example",
                        "top_candidate_url": "",
                        "top_candidate_domain": "",
                        "review_reason": "precision_low_confidence_auto_match",
                    },
                    {
                        "provider_id": "accept",
                        "provider_name": "Risky Accept",
                        "provider_detail_url": "https://amazon.example/accept",
                        "official_url": "https://accept.example",
                        "official_domain": "accept.example",
                        "top_candidate_url": "",
                        "top_candidate_domain": "",
                        "review_reason": "precision_generic_identity_term_risk",
                    },
                    {
                        "provider_id": "recall",
                        "provider_name": "Recall Row",
                        "provider_detail_url": "https://amazon.example/recall",
                        "official_url": "",
                        "official_domain": "",
                        "top_candidate_url": "https://recall.example",
                        "top_candidate_domain": "recall.example",
                        "review_reason": "recall_unresolved_top_candidate",
                    },
                    {
                        "provider_id": "low",
                        "provider_name": "Low Priority",
                        "provider_detail_url": "https://amazon.example/low",
                        "official_url": "https://low.example",
                        "official_domain": "low.example",
                        "top_candidate_url": "",
                        "top_candidate_domain": "",
                        "review_reason": "precision_low_confidence_auto_match",
                    },
                ],
            )
            _write_test_csv(
                agent_b,
                [
                    {
                        "provider_id": "timeout",
                        "provider_name": "Timeout Row",
                        "candidate_url": "https://timeout.example",
                        "candidate_domain": "timeout.example",
                        "agent_b_decision": "unsure",
                        "confidence": "0",
                        "evidence_score": "0",
                        "reason_for_unsure": "agent_b_row_timeout",
                    },
                    {
                        "provider_id": "accept",
                        "provider_name": "Risky Accept",
                        "candidate_url": "https://accept.example",
                        "candidate_domain": "accept.example",
                        "agent_b_decision": "accept",
                        "confidence": "88",
                        "evidence_score": "88",
                        "reason_for_unsure": "",
                    },
                    {
                        "provider_id": "recall",
                        "provider_name": "Recall Row",
                        "candidate_url": "https://recall.example",
                        "candidate_domain": "recall.example",
                        "agent_b_decision": "unsure",
                        "confidence": "69",
                        "evidence_score": "45",
                        "reason_for_unsure": "recall_candidate_needs_human_confirmation",
                    },
                    {
                        "provider_id": "low",
                        "provider_name": "Low Priority",
                        "candidate_url": "https://low.example",
                        "candidate_domain": "low.example",
                        "agent_b_decision": "unsure",
                        "confidence": "69",
                        "evidence_score": "65",
                        "reason_for_unsure": "",
                    },
                ],
            )

            summary = build_calibration_review_sample(
                review_csv=review,
                agent_b_csv=agent_b,
                output_csv=output_csv,
                output_xlsx=output_xlsx,
                max_rows=3,
                max_per_reason=2,
            )
            with output_csv.open(newline="", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
            xlsx_exists = output_xlsx.exists()

        self.assertEqual(summary["sample_rows"], 3)
        self.assertEqual(rows[0]["provider_id"], "timeout")
        self.assertIn("agent_b_accept_risky_lane", {row["sample_reason"] for row in rows})
        self.assertIn("recall_candidate_label", {row["sample_reason"] for row in rows})
        self.assertTrue(xlsx_exists)

    def test_build_calibration_review_sample_prioritizes_pattern_validation_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            review = root / "review.csv"
            agent_b = root / "agent_b.csv"
            patterns = root / "patterns.json"
            output_csv = root / "sample.csv"
            _write_test_csv(
                review,
                [
                    {
                        "provider_id": "pattern",
                        "provider_name": "Pattern Agency",
                        "provider_detail_url": "https://amazon.example/pattern",
                        "official_url": "",
                        "official_domain": "",
                        "top_candidate_url": "https://pattern.example",
                        "top_candidate_domain": "pattern.example",
                        "review_reason": "recall_unresolved_top_candidate",
                    },
                    {
                        "provider_id": "ordinary",
                        "provider_name": "Ordinary Agency",
                        "provider_detail_url": "https://amazon.example/ordinary",
                        "official_url": "",
                        "official_domain": "",
                        "top_candidate_url": "https://ordinary.example",
                        "top_candidate_domain": "ordinary.example",
                        "review_reason": "recall_unresolved_top_candidate",
                    },
                ],
            )
            _write_test_csv(
                agent_b,
                [
                    {
                        "provider_id": "pattern",
                        "provider_name": "Pattern Agency",
                        "candidate_url": "https://pattern.example",
                        "candidate_domain": "pattern.example",
                        "agent_b_decision": "unsure",
                        "confidence": "69",
                        "evidence_score": "31",
                        "supporting_facts": "candidate_pages_fetch_ok; schema_org_organization_seen",
                        "counter_evidence": "",
                        "reason_for_unsure": "recall_candidate_needs_human_confirmation",
                    },
                    {
                        "provider_id": "ordinary",
                        "provider_name": "Ordinary Agency",
                        "candidate_url": "https://ordinary.example",
                        "candidate_domain": "ordinary.example",
                        "agent_b_decision": "unsure",
                        "confidence": "69",
                        "evidence_score": "69",
                        "supporting_facts": "candidate_pages_fetch_ok",
                        "counter_evidence": "",
                        "reason_for_unsure": "recall_candidate_needs_human_confirmation",
                    },
                ],
            )
            patterns.write_text(
                json.dumps(
                    {
                        "summary": {"scope": "recall"},
                        "durable_safe_patterns": [
                            {
                                "pattern": "agent_b_score<45 AND has:schema_org_organization_seen",
                                "features": ["agent_b_score<45", "has:schema_org_organization_seen"],
                                "correct_recovery_rows": 3,
                                "wrong_release_rows": 0,
                            }
                        ],
                        "risky_patterns": [],
                    }
                ),
                encoding="utf-8",
            )

            summary = build_calibration_review_sample(
                review_csv=review,
                agent_b_csv=agent_b,
                output_csv=output_csv,
                max_rows=2,
                max_per_pattern=1,
                pattern_jsons=[patterns],
            )
            with output_csv.open(newline="", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))

        self.assertEqual(rows[0]["provider_id"], "pattern")
        self.assertEqual(rows[0]["sample_reason"], "pattern_candidate_validation")
        self.assertEqual(rows[0]["pattern_scope"], "recall")
        self.assertIn("schema_org_organization_seen", rows[0]["pattern_match"])
        self.assertEqual(summary["max_per_pattern"], 1)
        self.assertEqual(summary["pattern_match_counts"][rows[0]["pattern_match"]], 1)

    def test_build_calibration_review_sample_prioritizes_actionable_release_patterns(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            review = root / "review.csv"
            agent_b = root / "agent_b.csv"
            release_patterns = root / "release_patterns.json"
            output_csv = root / "sample.csv"
            _write_test_csv(
                review,
                [
                    {
                        "provider_id": "actionable",
                        "provider_name": "Actionable Brand",
                        "provider_detail_url": "https://amazon.example/actionable",
                        "official_url": "",
                        "official_domain": "",
                        "top_candidate_url": "https://actionablebrand.example",
                        "top_candidate_domain": "actionablebrand.example",
                        "review_reason": "recall_unresolved_top_candidate",
                    },
                    {
                        "provider_id": "ordinary",
                        "provider_name": "Ordinary Brand",
                        "provider_detail_url": "https://amazon.example/ordinary",
                        "official_url": "",
                        "official_domain": "",
                        "top_candidate_url": "https://ordinary.example",
                        "top_candidate_domain": "ordinary.example",
                        "review_reason": "recall_unresolved_top_candidate",
                    },
                    {
                        "provider_id": "docs",
                        "provider_name": "Docs Brand",
                        "provider_detail_url": "https://amazon.example/docs",
                        "official_url": "",
                        "official_domain": "",
                        "top_candidate_url": "https://docs.docsbrand.example",
                        "top_candidate_domain": "docs.docsbrand.example",
                        "review_reason": "recall_unresolved_top_candidate",
                    },
                ],
            )
            _write_test_csv(
                agent_b,
                [
                    {
                        "provider_id": "actionable",
                        "provider_name": "Actionable Brand",
                        "candidate_url": "https://actionablebrand.example",
                        "candidate_domain": "actionablebrand.example",
                        "agent_b_decision": "unsure",
                        "confidence": "69",
                        "evidence_score": "31",
                        "supporting_facts": "candidate_pages_fetch_ok; schema_org_organization_seen",
                        "counter_evidence": "",
                        "reason_for_unsure": "recall_candidate_needs_human_confirmation",
                    },
                    {
                        "provider_id": "ordinary",
                        "provider_name": "Ordinary Brand",
                        "candidate_url": "https://ordinary.example",
                        "candidate_domain": "ordinary.example",
                        "agent_b_decision": "unsure",
                        "confidence": "69",
                        "evidence_score": "31",
                        "supporting_facts": "candidate_pages_fetch_ok",
                        "counter_evidence": "",
                        "reason_for_unsure": "recall_candidate_needs_human_confirmation",
                    },
                    {
                        "provider_id": "docs",
                        "provider_name": "Docs Brand",
                        "candidate_url": "https://docs.docsbrand.example",
                        "candidate_domain": "docs.docsbrand.example",
                        "agent_b_decision": "unsure",
                        "confidence": "69",
                        "evidence_score": "31",
                        "supporting_facts": "candidate_pages_fetch_ok; schema_org_organization_seen",
                        "counter_evidence": "",
                        "reason_for_unsure": "recall_candidate_needs_human_confirmation",
                    },
                ],
            )
            release_patterns.write_text(
                json.dumps(
                    {
                        "summary": {"scope": "recall"},
                        "selected_actionable_pattern_set": [
                            {
                                "pattern": "agent_b_score<60 AND domain_relation:exact_provider_slug AND has:schema_org_organization_seen",
                                "features": [
                                    "agent_b_score<60",
                                    "domain_relation:exact_provider_slug",
                                    "has:schema_org_organization_seen",
                                ],
                                "correct_recovery_rows": 2,
                                "wrong_release_rows": 0,
                            }
                        ],
                        "actionable_safe_patterns": [
                            {
                                "pattern": "domain_relation:no_such_pattern AND has:schema_org_organization_seen",
                                "features": [
                                    "domain_relation:no_such_pattern",
                                    "has:schema_org_organization_seen",
                                ],
                                "correct_recovery_rows": 99,
                                "wrong_release_rows": 0,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            summary = build_calibration_review_sample(
                review_csv=review,
                agent_b_csv=agent_b,
                output_csv=output_csv,
                max_rows=3,
                max_per_pattern=1,
                pattern_jsons=[release_patterns],
            )
            with output_csv.open(newline="", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))

        self.assertEqual(rows[0]["provider_id"], "actionable")
        self.assertEqual(rows[0]["sample_reason"], "actionable_release_validation")
        self.assertIn("domain_relation:exact_provider_slug", rows[0]["pattern_match"])
        self.assertEqual({row["provider_id"]: row["sample_reason"] for row in rows}["docs"], "recall_candidate_label")
        self.assertEqual(summary["sample_reason_counts"]["actionable_release_validation"], 1)

    def test_build_calibration_review_sample_balances_repeated_pattern_matches(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            review = root / "review.csv"
            agent_b = root / "agent_b.csv"
            patterns = root / "patterns.json"
            output_csv = root / "sample.csv"
            review_rows = []
            agent_rows = []
            for idx in range(1, 5):
                provider_id = f"p-{idx}"
                review_rows.append(
                    {
                        "provider_id": provider_id,
                        "provider_name": f"Repeat {idx}",
                        "provider_detail_url": f"https://amazon.example/{provider_id}",
                        "official_url": "",
                        "official_domain": "",
                        "top_candidate_url": f"https://repeat{idx}.example",
                        "top_candidate_domain": f"repeat{idx}.example",
                        "review_reason": "recall_unresolved_top_candidate",
                    }
                )
                agent_rows.append(
                    {
                        "provider_id": provider_id,
                        "provider_name": f"Repeat {idx}",
                        "candidate_url": f"https://repeat{idx}.example",
                        "candidate_domain": f"repeat{idx}.example",
                        "agent_b_decision": "unsure",
                        "confidence": "69",
                        "evidence_score": "31",
                        "supporting_facts": "candidate_pages_fetch_ok; schema_org_organization_seen"
                        if idx < 4
                        else "candidate_pages_fetch_ok",
                        "counter_evidence": "",
                        "reason_for_unsure": "recall_candidate_needs_human_confirmation",
                    }
                )
            _write_test_csv(review, review_rows)
            _write_test_csv(agent_b, agent_rows)
            patterns.write_text(
                json.dumps(
                    {
                        "summary": {"scope": "recall"},
                        "durable_safe_patterns": [
                            {
                                "pattern": "agent_b_score<45 AND has:schema_org_organization_seen",
                                "features": ["agent_b_score<45", "has:schema_org_organization_seen"],
                                "correct_recovery_rows": 3,
                                "wrong_release_rows": 0,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            summary = build_calibration_review_sample(
                review_csv=review,
                agent_b_csv=agent_b,
                output_csv=output_csv,
                max_rows=2,
                max_per_pattern=1,
                pattern_jsons=[patterns],
            )

        self.assertEqual(summary["sample_rows"], 2)
        self.assertEqual(
            summary["pattern_match_counts"]["agent_b_score<45 AND has:schema_org_organization_seen"], 1
        )

    def test_build_calibration_review_sample_respects_reason_cap_when_refilling(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            review = root / "review.csv"
            agent_b = root / "agent_b.csv"
            output_csv = root / "sample.csv"
            review_rows = []
            agent_rows = []
            for idx in range(5):
                provider_id = f"low-{idx}"
                review_rows.append(
                    {
                        "provider_id": provider_id,
                        "provider_name": f"Low {idx}",
                        "provider_detail_url": f"https://amazon.example/{provider_id}",
                        "official_url": f"https://low{idx}.example",
                        "official_domain": f"low{idx}.example",
                        "top_candidate_url": "",
                        "top_candidate_domain": "",
                        "review_reason": "precision_low_confidence_auto_match",
                    }
                )
                agent_rows.append(
                    {
                        "provider_id": provider_id,
                        "provider_name": f"Low {idx}",
                        "candidate_url": f"https://low{idx}.example",
                        "candidate_domain": f"low{idx}.example",
                        "agent_b_decision": "unsure",
                        "confidence": "70",
                        "evidence_score": "70",
                        "reason_for_unsure": "",
                    }
                )
            review_rows.append(
                {
                    "provider_id": "recall",
                    "provider_name": "Recall",
                    "provider_detail_url": "https://amazon.example/recall",
                    "official_url": "",
                    "official_domain": "",
                    "top_candidate_url": "https://recall.example",
                    "top_candidate_domain": "recall.example",
                    "review_reason": "recall_unresolved_top_candidate",
                }
            )
            agent_rows.append(
                {
                    "provider_id": "recall",
                    "provider_name": "Recall",
                    "candidate_url": "https://recall.example",
                    "candidate_domain": "recall.example",
                    "agent_b_decision": "unsure",
                    "confidence": "69",
                    "evidence_score": "50",
                    "reason_for_unsure": "recall_candidate_needs_human_confirmation",
                }
            )
            _write_test_csv(review, review_rows)
            _write_test_csv(agent_b, agent_rows)

            summary = build_calibration_review_sample(
                review_csv=review,
                agent_b_csv=agent_b,
                output_csv=output_csv,
                max_rows=6,
                max_per_reason=2,
            )

        self.assertLessEqual(summary["reason_counts"]["precision_low_confidence_auto_match"], 2)
        self.assertLessEqual(summary["reason_counts"]["recall_unresolved_top_candidate"], 2)
        self.assertEqual(summary["sample_rows"], 3)

    def test_run_calibration_cycle_writes_patterns_sample_and_empty_eval(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            balance_json = root / "balance.json"
            labeled_agent_b = root / "labeled_agent_b.csv"
            review = root / "review.csv"
            batch_agent_b = root / "batch_agent_b.csv"
            policy_report = root / "policy_report.json"
            pattern_release = root / "pattern_release.json"
            output_dir = root / "calibration"
            balance_json.write_text(
                json.dumps(
                    {
                        "details": [
                            {
                                "provider_id": "p-good-1",
                                "provider_name": "Good One",
                                "expected_kind": "official",
                                "expected_domain": "goodone.example",
                                "manual_review_reason": "recall_unresolved_top_candidate",
                            },
                            {
                                "provider_id": "p-good-2",
                                "provider_name": "Good Two",
                                "expected_kind": "official",
                                "expected_domain": "goodtwo.example",
                                "manual_review_reason": "recall_unresolved_top_candidate",
                            },
                            {
                                "provider_id": "p-bad",
                                "provider_name": "Bad One",
                                "expected_kind": "official",
                                "expected_domain": "badone.example",
                                "manual_review_reason": "recall_unresolved_top_candidate",
                            },
                            {
                                "provider_id": "p-precision-good",
                                "provider_name": "Precision Good",
                                "expected_kind": "official",
                                "expected_domain": "precisiongood.example",
                                "outcome": "correct_official",
                                "manual_review_reason": "precision_low_confidence_auto_match",
                            },
                        ],
                        "manual_review_lanes": [
                            {
                                "review_reason": "recall_unresolved_top_candidate",
                                "review_task_rows": 1,
                                "labeled_rows": 1,
                                "false_official_rows": 0,
                                "over_rejected_rows": 1,
                                "correct_official_rows": 0,
                                "correct_no_official_rows": 0,
                                "risk_rows": 1,
                            },
                            {
                                "review_reason": "precision_low_confidence_auto_match",
                                "review_task_rows": 1,
                                "labeled_rows": 1,
                                "false_official_rows": 1,
                                "over_rejected_rows": 0,
                                "correct_official_rows": 0,
                                "correct_no_official_rows": 0,
                                "risk_rows": 1,
                            },
                            {
                                "review_reason": "precision_calibrated_pattern_release",
                                "review_task_rows": 1,
                                "labeled_rows": 3,
                                "false_official_rows": 0,
                                "over_rejected_rows": 0,
                                "correct_official_rows": 3,
                                "correct_no_official_rows": 0,
                                "risk_rows": 0,
                            },
                        ],
                        "manual_review_lane_drop_simulations": [
                            {
                                "drop_review_reason": "recall_unresolved_top_candidate",
                                "manual_review_rows_removed": 1,
                                "known_false_official_missed_if_dropped": 0,
                                "known_over_rejected_missed_if_dropped": 1,
                                "known_correct_reviews_removed_if_dropped": 0,
                            },
                            {
                                "drop_review_reason": "precision_low_confidence_auto_match",
                                "manual_review_rows_removed": 1,
                                "known_false_official_missed_if_dropped": 1,
                                "known_over_rejected_missed_if_dropped": 0,
                                "known_correct_reviews_removed_if_dropped": 0,
                            },
                            {
                                "drop_review_reason": "precision_calibrated_pattern_release",
                                "manual_review_rows_removed": 1,
                                "known_false_official_missed_if_dropped": 0,
                                "known_over_rejected_missed_if_dropped": 0,
                                "known_correct_reviews_removed_if_dropped": 3,
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            _write_test_csv(
                labeled_agent_b,
                [
                    {
                        "provider_id": "p-good-1",
                        "provider_name": "Good One",
                        "candidate_domain": "goodone.example",
                        "candidate_url": "https://goodone.example",
                        "agent_b_decision": "unsure",
                        "evidence_score": "31",
                        "supporting_facts": "candidate_pages_fetch_ok; schema_org_organization_seen",
                        "counter_evidence": "",
                        "reason_for_unsure": "recall_candidate_needs_human_confirmation",
                    },
                    {
                        "provider_id": "p-good-2",
                        "provider_name": "Good Two",
                        "candidate_domain": "goodtwo.example",
                        "candidate_url": "https://goodtwo.example",
                        "agent_b_decision": "unsure",
                        "evidence_score": "31",
                        "supporting_facts": "candidate_pages_fetch_ok; schema_org_organization_seen",
                        "counter_evidence": "",
                        "reason_for_unsure": "recall_candidate_needs_human_confirmation",
                    },
                    {
                        "provider_id": "p-bad",
                        "provider_name": "Bad One",
                        "candidate_domain": "wrong.example",
                        "candidate_url": "https://wrong.example",
                        "agent_b_decision": "unsure",
                        "evidence_score": "80",
                        "supporting_facts": "candidate_pages_fetch_ok",
                        "counter_evidence": "",
                        "reason_for_unsure": "recall_candidate_needs_human_confirmation",
                    },
                    {
                        "provider_id": "p-precision-good",
                        "provider_name": "Precision Good",
                        "candidate_domain": "precisiongood.example",
                        "candidate_url": "https://precisiongood.example",
                        "agent_b_decision": "accept",
                        "evidence_score": "90",
                        "supporting_facts": "candidate_pages_fetch_ok; page_contains_exact_provider_name",
                        "counter_evidence": "",
                        "reason_for_unsure": "",
                    },
                ],
            )
            _write_test_csv(
                review,
                [
                    {
                        "provider_id": "batch-recall",
                        "provider_name": "Batch Recall",
                        "provider_detail_url": "https://amazon.example/batch-recall",
                        "official_url": "",
                        "official_domain": "",
                        "top_candidate_url": "https://batchrecall.example",
                        "top_candidate_domain": "batchrecall.example",
                        "review_reason": "recall_unresolved_top_candidate",
                    },
                    {
                        "provider_id": "batch-precision",
                        "provider_name": "Batch Precision",
                        "provider_detail_url": "https://amazon.example/batch-precision",
                        "official_url": "https://batchprecision.example",
                        "official_domain": "batchprecision.example",
                        "top_candidate_url": "",
                        "top_candidate_domain": "",
                        "review_reason": "precision_low_confidence_auto_match",
                    },
                ],
            )
            _write_test_csv(
                batch_agent_b,
                [
                    {
                        "provider_id": "batch-recall",
                        "provider_name": "Batch Recall",
                        "candidate_domain": "batchrecall.example",
                        "candidate_url": "https://batchrecall.example",
                        "agent_b_decision": "unsure",
                        "confidence": "69",
                        "evidence_score": "31",
                        "supporting_facts": "candidate_pages_fetch_ok; schema_org_organization_seen",
                        "counter_evidence": "",
                        "reason_for_unsure": "recall_candidate_needs_human_confirmation",
                    },
                    {
                        "provider_id": "batch-precision",
                        "provider_name": "Batch Precision",
                        "candidate_domain": "batchprecision.example",
                        "candidate_url": "https://batchprecision.example",
                        "agent_b_decision": "accept",
                        "confidence": "90",
                        "evidence_score": "90",
                        "supporting_facts": "candidate_pages_fetch_ok; page_contains_exact_provider_name",
                        "counter_evidence": "",
                        "reason_for_unsure": "",
                    },
                ],
            )
            policy_report.write_text(
                json.dumps(
                    {
                        "summary": {
                            "recommended_first_pass_threshold": 75,
                            "recommended_second_pass_threshold": 75,
                            "raw_agent_b_recall_release": "manual_only",
                            "calibrated_pattern_release": "enabled_with_guard_no_batch_release",
                        }
                    }
                ),
                encoding="utf-8",
            )
            pattern_release.write_text(
                json.dumps(
                    {
                        "summary": {
                            "scope": "recall",
                            "baseline_overall_accuracy": 0.81,
                            "selected_actionable_pattern_count": 1,
                            "selected_actionable_correct_recovery_rows": 2,
                            "selected_actionable_wrong_release_rows": 0,
                            "selected_actionable_accuracy": 0.84,
                            "selected_actionable_auto_precision": 0.95,
                            "selected_actionable_official_recall": 0.9,
                        },
                        "selected_actionable_pattern_set": [
                            {
                                "pattern": "agent_b_score<45 AND domain_relation:exact_provider_slug AND has:schema_org_organization_seen",
                                "features": [
                                    "agent_b_score<45",
                                    "domain_relation:exact_provider_slug",
                                    "has:schema_org_organization_seen",
                                ],
                                "correct_recovery_rows": 2,
                                "wrong_release_rows": 0,
                            }
                        ],
                        "selected_actionable_release_summary": {
                            "pattern_count": 1,
                            "correct_recovery_rows": 2,
                            "wrong_release_rows": 0,
                            "simulated_overall": {
                                "overall_accuracy": 0.84,
                                "auto_precision": 0.95,
                                "official_recall": 0.9,
                            },
                        },
                    }
                ),
                encoding="utf-8",
            )

            report = run_calibration_cycle(
                labeled_eval_json=balance_json,
                labeled_agent_b_csv=labeled_agent_b,
                review_csv=review,
                batch_agent_b_csv=batch_agent_b,
                batch_total_rows=2,
                output_dir=output_dir,
                max_rows=2,
                max_per_pattern=1,
                pattern_release_jsons=[pattern_release],
                policy_report_json=policy_report,
            )
            output_exists = {
                "recall_json": (output_dir / "evidence_patterns_recall.json").exists(),
                "precision_md": (output_dir / "evidence_patterns_precision.md").exists(),
                "release_simulation_md": (output_dir / "pattern_release_simulation.md").exists(),
                "balance_report_json": (output_dir / "balance_report.json").exists(),
                "balance_report_md": (output_dir / "balance_report.md").exists(),
                "threshold_boundary_json": (output_dir / "threshold_boundary_report.json").exists(),
                "threshold_boundary_md": (output_dir / "threshold_boundary_report.md").exists(),
                "sample_xlsx": (output_dir / "pattern_validation_sample_50.xlsx").exists(),
                "eval_json": (output_dir / "pattern_validation_sample_50_eval_empty.json").exists(),
                "summary_md": (output_dir / "calibration_cycle_summary.md").exists(),
                "status_json": (output_dir / "calibration_status.json").exists(),
                "status_md": (output_dir / "calibration_status.md").exists(),
                "label_gap_csv": (output_dir / "label_gap_task.csv").exists(),
                "label_gap_xlsx": (output_dir / "label_gap_task.xlsx").exists(),
                "label_gap_high_csv": (output_dir / "label_gap_high_priority_task.csv").exists(),
                "label_gap_high_xlsx": (output_dir / "label_gap_high_priority_task.xlsx").exists(),
            }
            with (output_dir / "label_gap_high_priority_task.csv").open(newline="", encoding="utf-8") as f:
                high_gap_rows = list(csv.DictReader(f))

        self.assertEqual(report["summary"]["sample_rows"], 2)
        self.assertTrue(output_exists["recall_json"])
        self.assertTrue(output_exists["precision_md"])
        self.assertTrue(output_exists["release_simulation_md"])
        self.assertTrue(output_exists["balance_report_json"])
        self.assertTrue(output_exists["balance_report_md"])
        self.assertTrue(output_exists["threshold_boundary_json"])
        self.assertTrue(output_exists["threshold_boundary_md"])
        self.assertTrue(output_exists["sample_xlsx"])
        self.assertTrue(output_exists["eval_json"])
        self.assertTrue(output_exists["summary_md"])
        self.assertTrue(output_exists["status_json"])
        self.assertTrue(output_exists["status_md"])
        self.assertTrue(output_exists["label_gap_csv"])
        self.assertTrue(output_exists["label_gap_xlsx"])
        self.assertTrue(output_exists["label_gap_high_csv"])
        self.assertTrue(output_exists["label_gap_high_xlsx"])
        self.assertEqual(report["summary"]["empty_eval_labeled_rows"], 0)
        self.assertIn("label_gap_task_rows", report["summary"])
        self.assertIn("label_gap_high_priority_task_rows", report["summary"])
        self.assertIn("label_gap_task", report)
        self.assertIn("label_gap_high_priority_task", report)
        self.assertEqual(report["summary"]["label_gap_high_priority_task_rows"], 0)
        self.assertEqual(high_gap_rows, [])
        self.assertIn("release_actionable_safe_patterns", report["summary"])
        self.assertEqual(report["summary"]["actionable_release_validation_rows"], 1)
        self.assertEqual(report["summary"]["recommended_global_accept_threshold"], DEFAULT_SECOND_PASS_ACCEPT_THRESHOLD)
        self.assertEqual(
            report["summary"]["recommended_matched_review_confidence_below"],
            DEFAULT_MATCHED_REVIEW_CONFIDENCE_CUTOFF,
        )
        self.assertEqual(report["summary"]["calibrated_pattern_release"], "enabled_with_guard_no_batch_release")
        self.assertEqual(report["summary"]["recommended_pattern_release"], "narrow_pattern_release_candidate")
        self.assertEqual(report["summary"]["recommended_pattern_release_source_path"], str(pattern_release))
        self.assertEqual(report["summary"]["recommended_pattern_release_source_kind"], "supplied_prior")
        self.assertEqual(report["summary"]["pattern_release_correct_rows"], 2)
        self.assertEqual(report["summary"]["pattern_release_wrong_rows"], 0)
        self.assertEqual(report["summary"]["protected_review_lane_count"], 2)
        self.assertEqual(
            report["summary"]["spot_check_candidate_lanes"],
            ["precision_calibrated_pattern_release"],
        )
        self.assertIn("balance_report", report)
        self.assertIn("threshold_boundary", report)
        self.assertEqual(report["calibration_status"]["summary"]["workflow_status"], "not_converged_needs_human_labels")
        self.assertEqual(report["inputs"]["pattern_release_jsons"], [str(pattern_release)])
        self.assertEqual(report["inputs"]["preferred_pattern_release_json"], str(pattern_release))
        self.assertEqual(report["inputs"]["policy_report_json"], str(policy_report))
        self.assertEqual(report["inputs"]["batch_total_rows"], "2")

    def test_run_calibration_cycle_can_evaluate_filled_pattern_sample(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            balance_json = root / "balance.json"
            labeled_agent_b = root / "labeled_agent_b.csv"
            review = root / "review.csv"
            batch_agent_b = root / "batch_agent_b.csv"
            filled_sample = root / "filled_sample.csv"
            output_dir = root / "calibration"
            balance_json.write_text(
                json.dumps(
                    {
                        "details": [
                            {
                                "provider_id": "p-good-1",
                                "provider_name": "Good One",
                                "expected_kind": "official",
                                "expected_domain": "goodone.example",
                                "manual_review_reason": "recall_unresolved_top_candidate",
                            },
                            {
                                "provider_id": "p-good-2",
                                "provider_name": "Good Two",
                                "expected_kind": "official",
                                "expected_domain": "goodtwo.example",
                                "manual_review_reason": "recall_unresolved_top_candidate",
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            _write_test_csv(
                labeled_agent_b,
                [
                    {
                        "provider_id": "p-good-1",
                        "provider_name": "Good One",
                        "candidate_domain": "goodone.example",
                        "candidate_url": "https://goodone.example",
                        "agent_b_decision": "unsure",
                        "evidence_score": "31",
                        "supporting_facts": "candidate_pages_fetch_ok; schema_org_organization_seen",
                        "counter_evidence": "",
                        "reason_for_unsure": "recall_candidate_needs_human_confirmation",
                    },
                    {
                        "provider_id": "p-good-2",
                        "provider_name": "Good Two",
                        "candidate_domain": "goodtwo.example",
                        "candidate_url": "https://goodtwo.example",
                        "agent_b_decision": "unsure",
                        "evidence_score": "31",
                        "supporting_facts": "candidate_pages_fetch_ok; schema_org_organization_seen",
                        "counter_evidence": "",
                        "reason_for_unsure": "recall_candidate_needs_human_confirmation",
                    },
                ],
            )
            _write_test_csv(
                review,
                [
                    {
                        "provider_id": "batch-recall",
                        "provider_name": "Batch Recall",
                        "provider_detail_url": "https://amazon.example/batch-recall",
                        "official_url": "",
                        "official_domain": "",
                        "top_candidate_url": "https://batchrecall.example",
                        "top_candidate_domain": "batchrecall.example",
                        "review_reason": "recall_unresolved_top_candidate",
                    }
                ],
            )
            _write_test_csv(
                batch_agent_b,
                [
                    {
                        "provider_id": "batch-recall",
                        "provider_name": "Batch Recall",
                        "candidate_domain": "batchrecall.example",
                        "candidate_url": "https://batchrecall.example",
                        "agent_b_decision": "unsure",
                        "confidence": "69",
                        "evidence_score": "31",
                        "supporting_facts": "candidate_pages_fetch_ok; schema_org_organization_seen",
                        "counter_evidence": "",
                        "reason_for_unsure": "recall_candidate_needs_human_confirmation",
                    }
                ],
            )
            _write_test_csv(
                filled_sample,
                [
                    {
                        "provider_id": "filled-good",
                        "provider_name": "Filled Good",
                        "sample_reason": "pattern_candidate_validation",
                        "pattern_scope": "recall",
                        "pattern_match": "agent_b_score<45 AND has:schema_org_organization_seen",
                        "review_reason": "recall_unresolved_top_candidate",
                        "agent_b_decision": "unsure",
                        "reason_for_unsure": "",
                        "official_url": "",
                        "candidate_url": "https://filledgood.example",
                        "manual_decision": "accept",
                        "manual_url": "",
                        "notes": "",
                    },
                    {
                        "provider_id": "filled-bad",
                        "provider_name": "Filled Bad",
                        "sample_reason": "pattern_candidate_validation",
                        "pattern_scope": "recall",
                        "pattern_match": "agent_b_score<45 AND has:schema_org_organization_seen",
                        "review_reason": "recall_unresolved_top_candidate",
                        "agent_b_decision": "unsure",
                        "reason_for_unsure": "",
                        "official_url": "",
                        "candidate_url": "https://filledbad.example",
                        "manual_decision": "reject",
                        "manual_url": "",
                        "notes": "wrong candidate",
                    },
                ],
            )

            report = run_calibration_cycle(
                labeled_eval_json=balance_json,
                labeled_agent_b_csv=labeled_agent_b,
                review_csv=review,
                batch_agent_b_csv=batch_agent_b,
                output_dir=output_dir,
                max_rows=1,
                filled_sample=filled_sample,
            )
            filled_eval_exists = (output_dir / "pattern_validation_sample_50_eval_filled.json").exists()
            rule_candidates_json_exists = (output_dir / "pattern_rule_candidates.json").exists()
            rule_candidates_md = output_dir / "pattern_rule_candidates.md"
            rule_candidates_md_text = rule_candidates_md.read_text(encoding="utf-8")
            summary_text = (output_dir / "calibration_cycle_summary.md").read_text(encoding="utf-8")

        self.assertTrue(filled_eval_exists)
        self.assertTrue(rule_candidates_json_exists)
        self.assertEqual(report["summary"]["filled_eval_labeled_rows"], 2)
        self.assertEqual(report["summary"]["filled_pattern_recommendation_counts"]["reject_pattern"], 1)
        self.assertEqual(report["summary"]["filled_lane_recommendation_counts"]["keep_review_lane"], 1)
        self.assertEqual(report["summary"]["filled_lane_keep_review_count"], 1)
        self.assertEqual(report["summary"]["filled_rejected_pattern_count"], 1)
        self.assertIn("Rejected Pattern", rule_candidates_md_text)
        self.assertIn("Filled Lane Recommendations", summary_text)
        self.assertIn("Filled Pattern Recommendations", summary_text)
        self.assertIn("Filled Candidate Rule Export", summary_text)
        self.assertEqual(report["calibration_status"]["summary"]["workflow_status"], "partially_converged_keep_review_lanes")

    def test_run_calibration_cycle_merges_multiple_filled_samples(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            balance_json = root / "balance.json"
            labeled_agent_b = root / "labeled_agent_b.csv"
            review = root / "review.csv"
            batch_agent_b = root / "batch_agent_b.csv"
            filled_one = root / "filled_one.csv"
            filled_two = root / "filled_two.csv"
            output_dir = root / "calibration"
            balance_json.write_text(
                json.dumps(
                    {
                        "details": [
                            {
                                "provider_id": "p-good",
                                "provider_name": "Good",
                                "expected_kind": "official",
                                "expected_domain": "good.example",
                                "manual_review_reason": "recall_unresolved_top_candidate",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            _write_test_csv(
                labeled_agent_b,
                [
                    {
                        "provider_id": "p-good",
                        "provider_name": "Good",
                        "candidate_domain": "good.example",
                        "candidate_url": "https://good.example",
                        "agent_b_decision": "unsure",
                        "evidence_score": "31",
                        "supporting_facts": "candidate_pages_fetch_ok; schema_org_organization_seen",
                        "counter_evidence": "",
                        "reason_for_unsure": "recall_candidate_needs_human_confirmation",
                    }
                ],
            )
            _write_test_csv(
                review,
                [
                    {
                        "provider_id": "batch-recall",
                        "provider_name": "Batch Recall",
                        "provider_detail_url": "https://amazon.example/batch-recall",
                        "official_url": "",
                        "official_domain": "",
                        "top_candidate_url": "https://batchrecall.example",
                        "top_candidate_domain": "batchrecall.example",
                        "review_reason": "recall_unresolved_top_candidate",
                    }
                ],
            )
            _write_test_csv(
                batch_agent_b,
                [
                    {
                        "provider_id": "batch-recall",
                        "provider_name": "Batch Recall",
                        "candidate_domain": "batchrecall.example",
                        "candidate_url": "https://batchrecall.example",
                        "agent_b_decision": "unsure",
                        "confidence": "69",
                        "evidence_score": "31",
                        "supporting_facts": "candidate_pages_fetch_ok; schema_org_organization_seen",
                        "counter_evidence": "",
                        "reason_for_unsure": "recall_candidate_needs_human_confirmation",
                    }
                ],
            )
            _write_test_csv(
                filled_one,
                [
                    {
                        "provider_id": "filled-good",
                        "provider_name": "Filled Good",
                        "sample_reason": "pattern_candidate_validation",
                        "pattern_scope": "recall",
                        "pattern_match": "agent_b_score<45 AND has:schema_org_organization_seen",
                        "review_reason": "recall_unresolved_top_candidate",
                        "agent_b_decision": "unsure",
                        "official_url": "",
                        "candidate_url": "https://filledgood.example",
                        "manual_decision": "accept",
                        "manual_url": "",
                        "notes": "",
                    },
                    {
                        "provider_id": "filled-dup",
                        "provider_name": "Filled Duplicate",
                        "sample_reason": "pattern_candidate_validation",
                        "pattern_scope": "recall",
                        "pattern_match": "agent_b_score<45 AND has:schema_org_organization_seen",
                        "review_reason": "recall_unresolved_top_candidate",
                        "agent_b_decision": "unsure",
                        "official_url": "",
                        "candidate_url": "https://duplicate.example",
                        "manual_decision": "accept",
                        "manual_url": "",
                        "notes": "old decision",
                    },
                    {
                        "provider_id": "filled-blank-overwrite",
                        "provider_name": "Filled Blank Overwrite",
                        "sample_reason": "pattern_candidate_validation",
                        "pattern_scope": "recall",
                        "pattern_match": "agent_b_score<45 AND has:schema_org_organization_seen",
                        "review_reason": "recall_unresolved_top_candidate",
                        "agent_b_decision": "unsure",
                        "official_url": "",
                        "candidate_url": "https://blank-overwrite.example",
                        "manual_decision": "accept",
                        "manual_url": "",
                        "notes": "filled old",
                    }
                ],
            )
            _write_test_csv(
                filled_two,
                [
                    {
                        "provider_id": "filled-bad",
                        "provider_name": "Filled Bad",
                        "sample_reason": "pattern_candidate_validation",
                        "pattern_scope": "recall",
                        "pattern_match": "agent_b_score<45 AND has:schema_org_organization_seen",
                        "review_reason": "recall_unresolved_top_candidate",
                        "agent_b_decision": "unsure",
                        "official_url": "",
                        "candidate_url": "https://filledbad.example",
                        "manual_decision": "reject",
                        "manual_url": "",
                        "notes": "wrong candidate",
                    },
                    {
                        "provider_id": "filled-dup",
                        "provider_name": "Filled Duplicate",
                        "sample_reason": "pattern_candidate_validation",
                        "pattern_scope": "recall",
                        "pattern_match": "agent_b_score<45 AND has:schema_org_organization_seen",
                        "review_reason": "recall_unresolved_top_candidate",
                        "agent_b_decision": "unsure",
                        "official_url": "",
                        "candidate_url": "https://duplicate.example",
                        "manual_decision": "reject",
                        "manual_url": "",
                        "notes": "new decision",
                    },
                    {
                        "provider_id": "filled-blank-overwrite",
                        "provider_name": "Filled Blank Overwrite",
                        "sample_reason": "pattern_candidate_validation",
                        "pattern_scope": "recall",
                        "pattern_match": "agent_b_score<45 AND has:schema_org_organization_seen",
                        "review_reason": "recall_unresolved_top_candidate",
                        "agent_b_decision": "unsure",
                        "official_url": "",
                        "candidate_url": "https://blank-overwrite.example",
                        "manual_decision": "",
                        "manual_url": "",
                        "notes": "blank later",
                    }
                ],
            )

            report = run_calibration_cycle(
                labeled_eval_json=balance_json,
                labeled_agent_b_csv=labeled_agent_b,
                review_csv=review,
                batch_agent_b_csv=batch_agent_b,
                output_dir=output_dir,
                max_rows=1,
                filled_sample=[filled_one, filled_two],
            )
            merged_exists = (output_dir / "pattern_validation_sample_50_filled_samples_merged.csv").exists()
            with (output_dir / "pattern_validation_sample_50_filled_samples_merged.csv").open(
                newline="", encoding="utf-8"
            ) as f:
                merged_rows = list(csv.DictReader(f))
            duplicate_rows = [row for row in merged_rows if row["provider_id"] == "filled-dup"]
            blank_overwrite_rows = [
                row for row in merged_rows if row["provider_id"] == "filled-blank-overwrite"
            ]

        self.assertTrue(merged_exists)
        self.assertEqual(report["summary"]["filled_eval_labeled_rows"], 4)
        self.assertEqual(report["summary"]["filled_eval_decisive_rows"], 4)
        self.assertEqual(report["inputs"]["filled_samples"], [str(filled_one), str(filled_two)])
        self.assertEqual(
            report["outputs"]["filled_samples_merged_csv"],
            str(output_dir / "pattern_validation_sample_50_filled_samples_merged.csv"),
        )
        self.assertEqual(len(duplicate_rows), 1)
        self.assertEqual(duplicate_rows[0]["manual_decision"], "reject")
        self.assertEqual(duplicate_rows[0]["notes"], "new decision")
        self.assertEqual(len(blank_overwrite_rows), 1)
        self.assertEqual(blank_overwrite_rows[0]["manual_decision"], "accept")
        self.assertEqual(blank_overwrite_rows[0]["notes"], "filled old")

    def test_evaluate_calibration_review_sample_turns_labels_into_rule_guidance(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sample_csv = root / "sample.csv"
            sample_xlsx = root / "sample.xlsx"
            out_json = root / "calibration.json"
            out_md = root / "calibration.md"
            out_csv = root / "calibration_details.csv"
            _write_test_csv(
                sample_csv,
                [
                    {
                        "provider_id": "p-risk",
                        "provider_name": "Risky Accept",
                        "sample_reason": "agent_b_accept_risky_lane",
                        "pattern_scope": "",
                        "pattern_match": "",
                        "review_reason": "precision_generic_identity_term_risk",
                        "agent_b_decision": "accept",
                        "reason_for_unsure": "",
                        "official_url": "https://wrong.example",
                        "candidate_url": "https://wrong.example",
                        "manual_decision": "reject",
                        "manual_url": "",
                        "notes": "same name, wrong company",
                    },
                    {
                        "provider_id": "p-recall",
                        "provider_name": "Recall Row",
                        "sample_reason": "recall_candidate_label",
                        "pattern_scope": "recall",
                        "pattern_match": "has:schema_org_organization_seen",
                        "review_reason": "recall_unresolved_top_candidate",
                        "agent_b_decision": "unsure",
                        "reason_for_unsure": "",
                        "official_url": "",
                        "candidate_url": "https://recall.example",
                        "manual_decision": "replace",
                        "manual_url": "https://real-recall.example",
                        "notes": "candidate useful but needs replacement",
                    },
                    {
                        "provider_id": "p-timeout",
                        "provider_name": "Timeout Row",
                        "sample_reason": "timeout_needs_manual",
                        "pattern_scope": "",
                        "pattern_match": "",
                        "review_reason": "precision_low_confidence_auto_match",
                        "agent_b_decision": "unsure",
                        "reason_for_unsure": "agent_b_row_timeout",
                        "official_url": "https://timeout.example",
                        "candidate_url": "https://timeout.example",
                        "manual_decision": "accept",
                        "manual_url": "",
                        "notes": "correct after manual check",
                    },
                    {
                        "provider_id": "p-blank",
                        "provider_name": "Blank Row",
                        "sample_reason": "agent_b_unsure_label",
                        "pattern_scope": "",
                        "pattern_match": "",
                        "review_reason": "precision_low_confidence_auto_match",
                        "agent_b_decision": "unsure",
                        "reason_for_unsure": "",
                        "official_url": "https://blank.example",
                        "candidate_url": "https://blank.example",
                        "manual_decision": "",
                        "manual_url": "",
                        "notes": "",
                    },
                ],
            )
            build_workbook([("Calibration_Review", sample_csv)], sample_xlsx)

            report = evaluate_calibration_review_sample(
                sample=sample_xlsx,
                output_json=out_json,
                output_md=out_md,
                output_csv=out_csv,
            )
            with out_csv.open(newline="", encoding="utf-8") as f:
                detail_rows = list(csv.DictReader(f))
            md_text = out_md.read_text(encoding="utf-8")
            out_json_exists = out_json.exists()

        self.assertEqual(report["summary"]["sample_rows"], 4)
        self.assertEqual(report["summary"]["labeled_rows"], 3)
        self.assertEqual(report["summary"]["candidate_incorrect_rows"], 1)
        self.assertEqual(report["summary"]["recall_useful_rows"], 1)
        self.assertEqual(report["by_sample_reason"]["agent_b_accept_risky_lane"]["outcome_counts"]["candidate_incorrect"], 1)
        self.assertEqual(report["lane_recommendations"][0]["recommendation"], "keep_review_lane")
        self.assertEqual(report["lane_recommendations"][0]["review_reason"], "precision_generic_identity_term_risk")
        self.assertEqual(report["pattern_recommendations"][0]["pattern"], "has:schema_org_organization_seen")
        self.assertEqual(report["pattern_recommendations"][0]["recommendation"], "needs_more_labels")
        self.assertEqual(report["pattern_rule_candidates"]["needs_more_labels"][0]["pattern"], "has:schema_org_organization_seen")
        self.assertIn("Keep this pattern in calibration samples", report["pattern_rule_candidates"]["needs_more_labels"][0]["required_action"])
        self.assertIn("Keep AgentB risky accepts in manual review", md_text)
        self.assertIn("Review Lane Guidance", md_text)
        self.assertIn("Pattern Validation", md_text)
        self.assertIn("Candidate Rule Export", md_text)
        self.assertTrue(out_json_exists)
        self.assertEqual(detail_rows[1]["normalized_manual_url"], "https://real-recall.example")

    def test_evaluate_calibration_review_sample_rejects_pattern_when_label_blocks_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sample = root / "pattern_sample.csv"
            _write_test_csv(
                sample,
                [
                    {
                        "provider_id": "p-good",
                        "provider_name": "Good",
                        "sample_reason": "pattern_candidate_validation",
                        "pattern_scope": "precision",
                        "pattern_match": "has:page_contains_exact_provider_name",
                        "review_reason": "precision_low_confidence_auto_match",
                        "agent_b_decision": "unsure",
                        "reason_for_unsure": "",
                        "official_url": "https://good.example",
                        "candidate_url": "https://good.example",
                        "manual_decision": "accept",
                        "manual_url": "",
                        "notes": "",
                    },
                    {
                        "provider_id": "p-bad",
                        "provider_name": "Bad",
                        "sample_reason": "pattern_candidate_validation",
                        "pattern_scope": "precision",
                        "pattern_match": "has:page_contains_exact_provider_name",
                        "review_reason": "precision_low_confidence_auto_match",
                        "agent_b_decision": "unsure",
                        "reason_for_unsure": "",
                        "official_url": "https://bad.example",
                        "candidate_url": "https://bad.example",
                        "manual_decision": "reject",
                        "manual_url": "",
                        "notes": "wrong company",
                    },
                ],
            )

            report = evaluate_calibration_review_sample(sample=sample)

        self.assertEqual(report["pattern_recommendations"][0]["recommendation"], "reject_pattern")
        self.assertEqual(report["pattern_recommendations"][0]["blocking_rows"], 1)
        self.assertEqual(report["pattern_rule_candidates"]["reject_pattern"][0]["pattern"], "has:page_contains_exact_provider_name")
        self.assertIn("Do not release", report["pattern_rule_candidates"]["reject_pattern"][0]["required_action"])

    def test_evaluate_calibration_review_sample_exports_candidate_rule_patterns(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sample = root / "pattern_sample.csv"
            rows = []
            for idx in range(5):
                rows.append(
                    {
                        "provider_id": f"p-{idx}",
                        "provider_name": f"Good {idx}",
                        "sample_reason": "pattern_candidate_validation",
                        "pattern_scope": "recall",
                        "pattern_match": "domain_relation:exact_provider_slug AND has:schema_org_organization_seen",
                        "review_reason": "recall_unresolved_top_candidate",
                        "agent_b_decision": "unsure",
                        "reason_for_unsure": "",
                        "official_url": "",
                        "candidate_url": f"https://good{idx}.example",
                        "manual_decision": "accept",
                        "manual_url": "",
                        "notes": "",
                    }
                )
            _write_test_csv(sample, rows)

            report = evaluate_calibration_review_sample(sample=sample)

        candidates = report["pattern_rule_candidates"]["candidate_for_rule"]
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["supporting_rows"], 5)
        self.assertEqual(candidates[0]["blocking_rows"], 0)
        self.assertIn("narrow recall recovery rule", candidates[0]["required_action"])

    def test_evaluate_calibration_review_sample_exports_lane_downgrade_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sample = root / "lane_sample.csv"
            rows = []
            for idx in range(5):
                rows.append(
                    {
                        "provider_id": f"p-{idx}",
                        "provider_name": f"Clean {idx}",
                        "sample_reason": "second_pass_threshold_label",
                        "pattern_scope": "",
                        "pattern_match": "",
                        "review_reason": "precision_second_pass_accepted_lt70",
                        "agent_b_decision": "accept",
                        "reason_for_unsure": "",
                        "official_url": f"https://clean{idx}.example",
                        "candidate_url": f"https://clean{idx}.example",
                        "manual_decision": "accept",
                        "manual_url": "",
                        "notes": "",
                    }
                )
            _write_test_csv(sample, rows)

            report = evaluate_calibration_review_sample(sample=sample)

        self.assertEqual(report["summary"]["lane_candidate_for_change_rows"], 1)
        self.assertEqual(report["summary"]["lane_keep_review_rows"], 0)
        self.assertEqual(report["lane_recommendations"][0]["recommendation"], "candidate_for_review_downgrade")
        self.assertEqual(report["lane_recommendations"][0]["candidate_correct_rows"], 5)
        self.assertIn("spot-check", report["lane_recommendations"][0]["required_action"])

    def test_build_calibration_status_report_requires_labels_before_converging(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cycle = root / "cycle.json"
            balance = root / "balance.json"
            threshold = root / "threshold.json"
            sample_eval = root / "sample_eval.json"
            sample_csv = root / "pattern_validation_sample_50.csv"
            sample_xlsx = root / "pattern_validation_sample_50.xlsx"
            label_gap_xlsx = root / "label_gap_task.xlsx"
            label_gap_high_xlsx = root / "label_gap_high_priority_task.xlsx"
            out_md = root / "status.md"
            cycle.write_text(
                json.dumps(
                    {
                        "summary": {
                            "recommended_global_accept_threshold": 75,
                            "recommended_second_pass_threshold": 75,
                            "recommended_pattern_release": "narrow_pattern_release_candidate",
                            "recommended_pattern_release_source_path": "prior/pattern_release.json",
                            "recommended_pattern_release_source_kind": "supplied_prior",
                            "pattern_release_correct_rows": 4,
                            "pattern_release_wrong_rows": 0,
                            "protected_review_lane_count": 5,
                            "more_label_review_lanes": ["precision_second_pass_accepted_lt70"],
                            "spot_check_candidate_lanes": ["precision_calibrated_pattern_release"],
                            "filled_eval_labeled_rows": None,
                        },
                        "outputs": {
                            "sample_csv": str(sample_csv),
                            "sample_xlsx": str(sample_xlsx),
                            "label_gap_xlsx": str(label_gap_xlsx),
                            "label_gap_high_priority_xlsx": str(label_gap_high_xlsx),
                        },
                    }
                ),
                encoding="utf-8",
            )
            balance.write_text(
                json.dumps(
                    {
                        "summary": {
                            "protected_review_lane_count": 5,
                            "recommended_pattern_release": "narrow_pattern_release_candidate",
                            "pattern_release_source_path": "prior/pattern_release.json",
                            "pattern_release_correct_rows": 4,
                            "pattern_release_wrong_rows": 0,
                            "protected_review_lanes": ["precision_low_confidence_auto_match"],
                        }
                    }
                ),
                encoding="utf-8",
            )
            threshold.write_text(
                json.dumps(
                    {
                        "summary": {
                            "recommended_global_accept_threshold": 75,
                            "recommended_second_pass_threshold": 75,
                            "global_threshold_change": "keep_current",
                            "selected_actionable_correct_rows": 4,
                            "selected_actionable_wrong_rows": 0,
                        }
                    }
                ),
                encoding="utf-8",
            )
            sample_eval.write_text(
                json.dumps(
                    {
                        "summary": {
                            "sample_rows": 48,
                            "labeled_rows": 0,
                            "decisive_rows": 0,
                            "lane_needs_more_label_rows": 7,
                        },
                        "by_review_reason": {
                            "precision_second_pass_accepted_lt70": {
                                "rows": 8,
                                "labeled_rows": 0,
                                "decisive_rows": 0,
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            _write_test_csv(
                sample_csv,
                [
                    {"provider_id": "p-low", "review_reason": "precision_second_pass_accepted_lt70"},
                    {"provider_id": "p-release", "review_reason": "precision_calibrated_pattern_release"},
                    {"provider_id": "p-release-2", "review_reason": "precision_calibrated_pattern_release"},
                    {"provider_id": "p-release-3", "review_reason": "precision_calibrated_pattern_release"},
                    {"provider_id": "p-protected", "review_reason": "precision_low_confidence_auto_match"},
                    {"provider_id": "p-protected-2", "review_reason": "precision_low_confidence_auto_match"},
                    {"provider_id": "p-protected-3", "review_reason": "precision_low_confidence_auto_match"},
                ],
            )

            report = build_calibration_status_report(
                calibration_cycle_json=cycle,
                balance_report_json=balance,
                threshold_boundary_json=threshold,
                sample_eval_json=sample_eval,
                output_md=out_md,
            )
            md_text = out_md.read_text(encoding="utf-8")

        self.assertEqual(report["summary"]["workflow_status"], "not_converged_needs_human_labels")
        self.assertEqual(report["summary"]["threshold_status"], "stable_keep_current")
        self.assertEqual(report["summary"]["pattern_release_status"], "historical_guarded_candidate")
        self.assertEqual(report["summary"]["pattern_release_source_path"], "prior/pattern_release.json")
        self.assertEqual(report["summary"]["pattern_release_source_kind"], "supplied_prior")
        self.assertEqual(report["summary"]["review_lane_status"], "needs_human_labels")
        self.assertIn("fill_calibration_sample", {item["id"] for item in report["open_requirements"]})
        self.assertIn("validate_historical_pattern_release", {item["id"] for item in report["open_requirements"]})
        self.assertEqual(report["artifacts"]["sample_xlsx"], str(sample_xlsx))
        self.assertEqual(report["artifacts"]["label_gap_xlsx"], str(label_gap_xlsx))
        self.assertEqual(report["artifacts"]["label_gap_high_priority_xlsx"], str(label_gap_high_xlsx))
        self.assertEqual(report["labeling_instructions"]["fields_to_fill"], ["manual_decision", "manual_url", "notes"])
        self.assertIn("precision_second_pass_accepted_lt70", {item["review_reason"] for item in report["label_targets"]})
        high_priority = [item for item in report["label_targets"] if item["priority"] == "high"]
        self.assertEqual(high_priority[0]["review_reason"], "precision_second_pass_accepted_lt70")
        self.assertEqual(high_priority[0]["target_decisive_rows"], 5)
        self.assertEqual(high_priority[0]["decisive_rows_needed"], 5)
        self.assertEqual(report["summary"]["high_priority_decisive_rows_needed"], 5)
        by_reason = {item["review_reason"]: item for item in report["label_targets"]}
        self.assertEqual(by_reason["precision_calibrated_pattern_release"]["target_decisive_rows"], 3)
        self.assertEqual(by_reason["precision_low_confidence_auto_match"]["target_decisive_rows"], 3)
        self.assertIn(str(label_gap_high_xlsx), report["next_actions"][0])
        self.assertIn(str(sample_xlsx), md_text)
        self.assertIn(str(label_gap_high_xlsx), md_text)
        self.assertIn("precision_second_pass_accepted_lt70", md_text)
        self.assertIn("prior/pattern_release.json", md_text)
        self.assertIn("supplied_prior", md_text)
        self.assertIn("decisive=0/5", md_text)

    def test_build_calibration_status_report_flags_candidate_changes_after_labels(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cycle = root / "cycle.json"
            sample_eval = root / "sample_eval.json"
            cycle.write_text(
                json.dumps(
                    {
                        "summary": {
                            "recommended_global_accept_threshold": 75,
                            "recommended_second_pass_threshold": 75,
                            "filled_eval_labeled_rows": 8,
                            "filled_eval_decisive_rows": 8,
                            "recommended_pattern_release": "narrow_pattern_release_candidate",
                            "pattern_release_correct_rows": 4,
                            "pattern_release_wrong_rows": 0,
                        }
                    }
                ),
                encoding="utf-8",
            )
            sample_eval.write_text(
                json.dumps(
                    {
                        "summary": {
                            "labeled_rows": 8,
                            "decisive_rows": 8,
                            "lane_candidate_for_change_rows": 1,
                            "lane_keep_review_rows": 0,
                            "lane_needs_more_label_rows": 0,
                        }
                    }
                ),
                encoding="utf-8",
            )

            report = build_calibration_status_report(
                calibration_cycle_json=cycle,
                sample_eval_json=sample_eval,
            )

        self.assertEqual(report["summary"]["workflow_status"], "candidate_changes_require_regression")
        self.assertEqual(report["summary"]["pattern_release_status"], "current_guarded_candidate")
        self.assertEqual(report["summary"]["review_lane_status"], "candidate_for_downgrade")
        self.assertIn("lane_downgrade_candidate", {item["id"] for item in report["open_requirements"]})

    def test_build_calibration_status_report_uses_full_sample_counts_after_partial_fill(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cycle = root / "cycle.json"
            sample_eval = root / "filled_eval.json"
            sample_csv = root / "sample.csv"
            sample_rows = [
                {
                    "provider_id": f"p-{idx}",
                    "review_reason": "precision_second_pass_accepted_lt70",
                    "manual_decision": "",
                }
                for idx in range(8)
            ]
            _write_test_csv(sample_csv, sample_rows)
            cycle.write_text(
                json.dumps(
                    {
                        "summary": {
                            "recommended_global_accept_threshold": 75,
                            "recommended_second_pass_threshold": 75,
                            "more_label_review_lanes": ["precision_second_pass_accepted_lt70"],
                            "filled_eval_labeled_rows": 4,
                            "filled_eval_decisive_rows": 4,
                        },
                        "outputs": {"sample_csv": str(sample_csv)},
                    }
                ),
                encoding="utf-8",
            )
            sample_eval.write_text(
                json.dumps(
                    {
                        "summary": {
                            "labeled_rows": 4,
                            "decisive_rows": 4,
                            "lane_needs_more_label_rows": 1,
                        },
                        "by_review_reason": {
                            "precision_second_pass_accepted_lt70": {
                                "rows": 4,
                                "labeled_rows": 4,
                                "decisive_rows": 4,
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            report = build_calibration_status_report(calibration_cycle_json=cycle, sample_eval_json=sample_eval)
            target = {
                item["review_reason"]: item for item in report["label_targets"]
            }["precision_second_pass_accepted_lt70"]

        self.assertEqual(target["rows"], 8)
        self.assertEqual(target["decisive_rows"], 4)
        self.assertEqual(target["target_decisive_rows"], 5)
        self.assertEqual(target["decisive_rows_needed"], 1)

    def test_build_calibration_status_report_keeps_partial_gap_from_candidate_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cycle = root / "cycle.json"
            balance = root / "balance.json"
            sample_eval = root / "filled_eval.json"
            sample_csv = root / "sample.csv"
            label_gap_xlsx = root / "label_gap_task.xlsx"
            label_gap_high_xlsx = root / "label_gap_high_priority_task.xlsx"
            rows = []
            for idx in range(5):
                rows.append(
                    {
                        "provider_id": f"high-{idx}",
                        "review_reason": "precision_second_pass_accepted_lt70",
                    }
                )
            for idx in range(3):
                rows.append(
                    {
                        "provider_id": f"medium-{idx}",
                        "review_reason": "precision_low_confidence_auto_match",
                    }
                )
            _write_test_csv(sample_csv, rows)
            cycle.write_text(
                json.dumps(
                    {
                        "summary": {
                            "recommended_global_accept_threshold": 75,
                            "recommended_second_pass_threshold": 75,
                            "more_label_review_lanes": ["precision_second_pass_accepted_lt70"],
                            "filled_eval_labeled_rows": 5,
                            "filled_eval_decisive_rows": 5,
                        },
                        "outputs": {
                            "sample_csv": str(sample_csv),
                            "label_gap_xlsx": str(label_gap_xlsx),
                            "label_gap_high_priority_xlsx": str(label_gap_high_xlsx),
                        },
                    }
                ),
                encoding="utf-8",
            )
            balance.write_text(
                json.dumps(
                    {
                        "summary": {
                            "protected_review_lanes": ["precision_low_confidence_auto_match"],
                            "protected_review_lane_count": 1,
                        }
                    }
                ),
                encoding="utf-8",
            )
            sample_eval.write_text(
                json.dumps(
                    {
                        "summary": {
                            "labeled_rows": 5,
                            "decisive_rows": 5,
                            "lane_candidate_for_change_rows": 1,
                            "lane_keep_review_rows": 0,
                            "lane_needs_more_label_rows": 0,
                        },
                        "by_review_reason": {
                            "precision_second_pass_accepted_lt70": {
                                "rows": 5,
                                "labeled_rows": 5,
                                "decisive_rows": 5,
                            }
                        },
                        "lane_recommendations": [
                            {
                                "review_reason": "precision_second_pass_accepted_lt70",
                                "recommendation": "candidate_for_review_downgrade",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            report = build_calibration_status_report(
                calibration_cycle_json=cycle,
                balance_report_json=balance,
                sample_eval_json=sample_eval,
            )
            by_reason = {item["review_reason"]: item for item in report["label_targets"]}

        self.assertEqual(report["summary"]["workflow_status"], "partially_converged_keep_review_lanes")
        self.assertEqual(report["summary"]["review_lane_status"], "needs_more_labels")
        self.assertEqual(report["review_lanes"]["decisive_rows_needed"], 3)
        self.assertEqual(by_reason["precision_second_pass_accepted_lt70"]["decisive_rows_needed"], 0)
        self.assertEqual(by_reason["precision_low_confidence_auto_match"]["decisive_rows_needed"], 3)
        self.assertIn(str(label_gap_xlsx), report["next_actions"][0])
        self.assertNotIn(str(label_gap_high_xlsx), report["next_actions"][0])

    def test_build_calibration_label_gap_task_selects_needed_unlabeled_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            status = root / "status.json"
            sample = root / "sample.csv"
            output_csv = root / "label_gap.csv"
            output_xlsx = root / "label_gap.xlsx"
            filled = root / "filled.csv"
            rows = []
            for idx in range(6):
                rows.append(
                    {
                        "provider_id": f"high-{idx}",
                        "provider_name": f"High {idx}",
                        "provider_detail_url": f"https://amazon.example/high-{idx}",
                        "review_reason": "precision_second_pass_accepted_lt70",
                        "official_url": f"https://high{idx}.example",
                        "manual_decision": "accept" if idx == 0 else "",
                        "manual_url": "",
                        "notes": "",
                    }
                )
            for idx in range(4):
                rows.append(
                    {
                        "provider_id": f"spot-{idx}",
                        "provider_name": f"Spot {idx}",
                        "provider_detail_url": f"https://amazon.example/spot-{idx}",
                        "review_reason": "precision_calibrated_pattern_release",
                        "official_url": f"https://spot{idx}.example",
                        "manual_decision": "",
                        "manual_url": "",
                        "notes": "",
                    }
                )
            _write_test_csv(sample, rows)
            _write_test_csv(
                filled,
                [
                    {
                        "provider_id": "high-1",
                        "review_reason": "precision_second_pass_accepted_lt70",
                        "manual_decision": "accept",
                    }
                ],
            )
            status.write_text(
                json.dumps(
                    {
                        "summary": {
                            "pattern_release_source_kind": "supplied_prior",
                            "pattern_release_source_path": "prior/pattern_release.json",
                        },
                        "artifacts": {"sample_csv": str(sample)},
                        "label_targets": [
                            {
                                "review_reason": "precision_second_pass_accepted_lt70",
                                "priority": "high",
                                "target_decisive_rows": 5,
                                "decisive_rows_needed": 5,
                                "label_goal": "Fill every sampled row.",
                            },
                            {
                                "review_reason": "precision_calibrated_pattern_release",
                                "priority": "medium",
                                "target_decisive_rows": 3,
                                "decisive_rows_needed": 3,
                                "label_goal": "Spot-check released rows.",
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )

            summary = build_calibration_label_gap_task(
                status_json=status,
                filled_sample=filled,
                output_csv=output_csv,
                output_xlsx=output_xlsx,
            )
            high_only_csv = root / "label_gap_high.csv"
            high_only_summary = build_calibration_label_gap_task(
                status_json=status,
                filled_sample=filled,
                output_csv=high_only_csv,
                priorities=["high"],
            )
            with output_csv.open(newline="", encoding="utf-8") as f:
                out_rows = list(csv.DictReader(f))
            with high_only_csv.open(newline="", encoding="utf-8") as f:
                high_only_rows = list(csv.DictReader(f))
            xlsx_exists = output_xlsx.exists()

        self.assertEqual(summary["task_rows"], 7)
        self.assertEqual(summary["priority_counts"]["high"], 4)
        self.assertEqual(summary["priority_counts"]["medium"], 3)
        self.assertTrue(xlsx_exists)
        self.assertEqual(out_rows[0]["label_priority"], "high")
        self.assertEqual(out_rows[0]["label_decisive_rows_needed"], "5")
        self.assertIn("official_url", out_rows[0]["label_question"])
        self.assertIn("sub-70 accepts", out_rows[0]["label_decision_hint"])
        self.assertEqual(out_rows[0]["manual_decision"], "")
        self.assertIn("provider_detail_url", out_rows[0])
        self.assertNotIn("high-1", {row["provider_id"] for row in out_rows})
        spot_rows = [row for row in out_rows if row["review_reason"] == "precision_calibrated_pattern_release"]
        self.assertEqual(spot_rows[0]["label_evidence_source_kind"], "supplied_prior")
        self.assertEqual(spot_rows[0]["label_evidence_source_path"], "prior/pattern_release.json")
        self.assertIn("blocks wider automatic release", spot_rows[0]["label_decision_hint"])
        self.assertEqual(high_only_summary["task_rows"], 4)
        self.assertEqual({row["label_priority"] for row in high_only_rows}, {"high"})

    def test_scoring_tries_www_variant_before_giving_up_on_candidate(self):
        config = load_config("config/scoring.json")
        provider = {
            "provider_name": "Bitesu India",
            "provider_locations": ["India"],
        }
        candidate = SearchCandidate(
            url="https://bitesuindia.com/",
            title="Bitesu India official website",
            snippet="Bitesu India Amazon ecommerce services contact India",
            source="brave",
            query='"Bitesu India" official website',
            rank=1,
        )

        def fake_fetch(url):
            if url.startswith("https://bitesuindia.com"):
                return {"ok": False, "status": 0, "final_url": url, "text": ""}
            html = """
            <html><head><title>Bitesu India</title></head>
            <body>Bitesu India provides Amazon marketplace and ecommerce services in India.
            Contact us. About us.</body></html>
            """
            return {"ok": True, "status": 200, "final_url": "https://www.bitesuindia.com/", "text": html}

        with patch("finder.scoring.fetch_text", side_effect=fake_fetch):
            result = choose_best(provider, [candidate], config)

        self.assertEqual(result["official_url"], "https://www.bitesuindia.com/")
        self.assertGreaterEqual(result["confidence"], 75)

    def test_indiamart_is_evidence_only_not_official_url(self):
        config = load_config("config/scoring.json")
        self.assertTrue(is_excluded_domain("https://www.indiamart.com/company/252021821/", config))
        platform_result = {
            "official_url": "https://www.indiamart.com/company/252021821/",
            "status": "matched",
            "confidence": "95",
            "evidence_summary": "page_contains_exact_provider_name",
        }
        self.assertFalse(_accepted(platform_result, config, 70))
        capped_result = {
            "official_url": "https://www.bfarm.de/DE/Home/_node.html",
            "status": "matched",
            "confidence": "95",
            "evidence_summary": "identity_cap_industry_mismatch_without_service",
        }
        self.assertFalse(_accepted(capped_result, config, 75))

    def test_logo_similarity_is_positive_identity_evidence(self):
        config = load_config("config/scoring.json")
        provider = {
            "provider_name": "Logo Agency",
            "provider_locations": ["United Kingdom"],
            "listing_logo_url": "https://amazon.example/logo.png",
        }
        candidate = SearchCandidate(
            url="https://logoagency.com/",
            title="Official website",
            snippet="Amazon marketplace services in the United Kingdom",
            source="brave",
            query='"Logo Agency" official website',
            rank=1,
        )

        def fake_fetch(url):
            html = """
            <html><head><title>Official website</title></head>
            <body><img class="site-logo" src="/logo.png">
            Amazon marketplace advertising services in the United Kingdom. Contact us.</body></html>
            """
            return {"ok": True, "status": 200, "final_url": url, "text": html}

        def fake_hash(url):
            return "1" * 64 if "logo" in url else ""

        with patch("finder.scoring.fetch_text", side_effect=fake_fetch), patch("finder.logo.image_average_hash", side_effect=fake_hash):
            result = choose_best(provider, [candidate], config)

        self.assertEqual(result["status"], "matched")
        self.assertIn("listing_logo_visual_match", result["evidence_summary"])

    def test_extract_logo_urls_from_common_logo_markup(self):
        html = """
        <html><head>
        <meta property="og:image" content="/og-logo.png">
        <link rel="icon" href="/favicon.ico">
        </head><body><img alt="Company logo" src="/brand.png"></body></html>
        """
        urls = extract_logo_urls(html, "https://example.com/about")

        self.assertIn("https://example.com/og-logo.png", urls)
        self.assertIn("https://example.com/favicon.ico", urls)
        self.assertIn("https://example.com/brand.png", urls)
        self.assertEqual(hash_similarity("11110000", "11100000"), 0.875)

    def test_run_review_learning_applies_manual_feedback_and_writes_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            source_rows = [
                {
                    "provider_id": "p-1",
                    "provider_name": "One",
                    "provider_detail_url": "https://amazon.example/p-1",
                    "official_url": "https://one.example",
                    "official_domain": "one.example",
                    "confidence": "90",
                    "status": "matched",
                    "evidence_summary": "strong",
                    "candidate_count": "1",
                    "scored_candidate_count": "1",
                    "service_apis": "[]",
                    "provider_locations": "[]",
                },
                {
                    "provider_id": "p-2",
                    "provider_name": "Two",
                    "provider_detail_url": "https://amazon.example/p-2",
                    "official_url": "",
                    "official_domain": "",
                    "confidence": "35",
                    "status": "low_confidence",
                    "evidence_summary": "weak",
                    "candidate_count": "1",
                    "scored_candidate_count": "1",
                    "service_apis": "[]",
                    "provider_locations": "[]",
                },
                {
                    "provider_id": "p-3",
                    "provider_name": "Three",
                    "provider_detail_url": "https://amazon.example/p-3",
                    "official_url": "https://wrong.example",
                    "official_domain": "wrong.example",
                    "confidence": "71",
                    "status": "needs_review",
                    "evidence_summary": "borderline",
                    "candidate_count": "1",
                    "scored_candidate_count": "1",
                    "service_apis": "[]",
                    "provider_locations": "[]",
                },
            ]
            second_pass_review = [
                {
                    "provider_id": "p-2",
                    "provider_name": "Two",
                    "manual_decision": "replace",
                    "manual_url": "https://two-auto.example",
                    "notes": "second_pass_auto_accept",
                }
            ]
            manual_review = [
                {
                    "provider_id": "p-2",
                    "provider_name": "Two",
                    "official_url": "https://two-auto.example",
                    "manual_decision": "replace",
                    "manual_url": "https://two-true.example",
                    "notes": "human corrected",
                },
                {
                    "provider_id": "p-3",
                    "provider_name": "Three",
                    "official_url": "https://wrong.example",
                    "manual_decision": "reject",
                    "manual_url": "",
                    "notes": "wrong company",
                },
            ]
            labels = [{"provider_id": "p-1", "provider_name": "One", "expected_domain": "one.example"}]
            _write_test_csv(run_dir / "provider_official_websites_enriched.csv", source_rows)
            _write_test_csv(run_dir / "unresolved_second_pass_review_decisions.csv", second_pass_review)
            _write_test_csv(run_dir / "manual_review.csv", manual_review)
            _write_test_csv(run_dir / "labels.csv", labels)
            manifest = {
                "parameters": {
                    "total_to_run": 3,
                    "min_domain_accuracy": 1.0,
                    "min_auto_precision": 1.0,
                    "min_official_url_rate": 0.0,
                    "max_unresolved_rate": 1.0,
                },
                "outputs": {
                    "results_enriched": str(run_dir / "provider_official_websites_enriched.csv"),
                },
                "summary": {"status": "complete"},
            }
            (run_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

            summary = run_review_learning(
                run_dir=run_dir,
                review_path=run_dir / "manual_review.csv",
                labels_csv=run_dir / "labels.csv",
                write_xlsx=True,
            )
            with (run_dir / "reviewed/official_sites.csv").open(newline="", encoding="utf-8") as f:
                reviewed = {row["provider_id"]: row for row in csv.DictReader(f)}
            report_exists = (run_dir / "reviewed/learning.md").exists()
            xlsx_exists = (run_dir / "reviewed/official_sites.xlsx").exists()
            legacy_xlsx_exists = (run_dir / "provider_official_websites_reviewed_with_clickable_links.xlsx").exists()

        self.assertEqual(reviewed["p-2"]["official_domain"], "two-true.example")
        self.assertEqual(reviewed["p-3"]["status"], "rejected")
        self.assertEqual(summary["overall"]["applied_manual_rows"], 2)
        self.assertTrue(report_exists)
        self.assertTrue(xlsx_exists)
        self.assertTrue(legacy_xlsx_exists)

    def test_review_learning_treats_typos_and_reject_with_manual_url_as_replace(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            source_rows = [
                {
                    "provider_id": "p-1",
                    "provider_name": "Typo Accept",
                    "provider_detail_url": "https://amazon.example/p-1",
                    "official_url": "https://typo.example",
                    "official_domain": "typo.example",
                    "confidence": "62",
                    "status": "matched",
                    "evidence_summary": "weak",
                    "candidate_count": "1",
                    "scored_candidate_count": "1",
                    "service_apis": "[]",
                    "provider_locations": "[]",
                },
                {
                    "provider_id": "p-2",
                    "provider_name": "Reject With Url",
                    "provider_detail_url": "https://amazon.example/p-2",
                    "official_url": "https://wrong.example",
                    "official_domain": "wrong.example",
                    "confidence": "62",
                    "status": "matched",
                    "evidence_summary": "weak",
                    "candidate_count": "1",
                    "scored_candidate_count": "1",
                    "service_apis": "[]",
                    "provider_locations": "[]",
                },
            ]
            review_rows = [
                {"provider_id": "p-1", "provider_name": "Typo Accept", "manual_decision": "accpet", "manual_url": "", "notes": ""},
                {
                    "provider_id": "p-2",
                    "provider_name": "Reject With Url",
                    "manual_decision": "reject",
                    "manual_url": "https://right.example",
                    "notes": "candidate wrong",
                },
            ]
            _write_test_csv(run_dir / "provider_official_websites_enriched.csv", source_rows)
            _write_test_csv(run_dir / "review.csv", review_rows)

            summary = run_review_learning(run_dir=run_dir, review_path=run_dir / "review.csv", write_xlsx=False)
            with Path(summary["outputs"]["combined_review"]).open(newline="", encoding="utf-8") as f:
                combined = {row["provider_id"]: row for row in csv.DictReader(f)}
            with Path(summary["outputs"]["final"]).open(newline="", encoding="utf-8") as f:
                final = {row["provider_id"]: row for row in csv.DictReader(f)}

        self.assertEqual(combined["p-1"]["manual_decision"], "accept")
        self.assertEqual(combined["p-2"]["manual_decision"], "replace")
        self.assertEqual(final["p-2"]["official_url"], "https://right.example")

    def test_plan_unresolved_second_pass_assigns_actionable_tiers(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            providers = [
                {
                    "provider_id": "p-1",
                    "provider_name": "Example GmbH",
                    "service_apis": json.dumps(["Account Management"]),
                    "provider_locations": json.dumps(["Germany"]),
                },
                {
                    "provider_id": "p-2",
                    "provider_name": "Guess Brand",
                    "service_apis": json.dumps(["Advertising Optimization"]),
                    "provider_locations": json.dumps(["United States of America"]),
                },
            ]
            review = [
                {
                    "provider_id": "p-1",
                    "provider_name": "Example GmbH",
                    "provider_detail_url": "https://amazon.example/p-1",
                    "status": "needs_review",
                    "confidence": "64",
                    "candidate_1_url": "https://example.de",
                    "candidate_1_domain": "example.de",
                    "candidate_1_score": "64",
                    "candidate_1_source": "brave",
                },
                {
                    "provider_id": "p-2",
                    "provider_name": "Guess Brand",
                    "provider_detail_url": "https://amazon.example/p-2",
                    "status": "low_confidence",
                    "confidence": "35",
                    "candidate_1_url": "https://guessbrand.com",
                    "candidate_1_domain": "guessbrand.com",
                    "candidate_1_score": "35",
                    "candidate_1_source": "domain_guess",
                },
            ]
            with (run_dir / "providers_normalized.csv").open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=providers[0].keys())
                writer.writeheader()
                writer.writerows(providers)
            with (run_dir / "provider_review_sheet_enhanced.csv").open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=review[0].keys())
                writer.writeheader()
                writer.writerows(review)

            rows = build_second_pass_plan(run_dir)

        self.assertEqual(rows[0]["strategy_tier"], "A_verify_top_candidate")
        self.assertTrue(any("site:example.de" in rows[0][f"query_{idx}"] for idx in range(1, 9)))
        self.assertEqual(rows[1]["strategy_tier"], "B_verify_or_expand_domain_guess")
        self.assertIn('"Guess Brand" official website', rows[1]["query_1"])

    def test_verify_run_outputs_checks_quality_and_detail_links(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            final_rows = [
                {
                    "provider_id": "p-1",
                    "provider_name": "One",
                    "provider_detail_url": "https://amazon.example/p-1",
                    "official_url": "https://one.example",
                    "official_domain": "one.example",
                    "status": "matched",
                },
                {
                    "provider_id": "p-2",
                    "provider_name": "Two",
                    "provider_detail_url": "https://amazon.example/p-2",
                    "official_url": "",
                    "official_domain": "",
                    "status": "unresolved",
                },
            ]
            with (run_dir / "official_sites.csv").open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=final_rows[0].keys())
                writer.writeheader()
                writer.writerows(final_rows)
            with (run_dir / "unresolved.csv").open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=final_rows[0].keys())
                writer.writeheader()
                writer.writerow(final_rows[1])
            quality = {
                "overall": {
                    "passed": True,
                    "total_rows": 2,
                    "excluded_official_url_rows": 0,
                    "duplicate_provider_ids": 0,
                    "malformed_official_url_rows": 0,
                }
            }
            (run_dir / "quality.json").write_text(json.dumps(quality), encoding="utf-8")

            summary = verify_run_outputs(run_dir, expected_rows=2, expected_unresolved=1)

        self.assertTrue(summary["passed"])
        self.assertEqual(summary["provider_detail_url_rows"], 2)

    def test_run_unresolved_second_pass_accepts_verified_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            provider = {
                "provider_id": "p-1",
                "provider_name": "Example Agency LLC",
                "service_apis": json.dumps(["Account Management"]),
                "provider_locations": json.dumps(["United Kingdom"]),
                "provider_languages": json.dumps(["English"]),
                "service_types": json.dumps(["Complete Account Management"]),
                "listing_logo_url": "https://m.media-amazon.com/logo.png",
                "detail_url": "https://sellercentral.amazon.example/p-1",
                "about_listing_text": "Amazon account management services.",
                "source_rows": "1",
            }
            result = {
                "provider_id": "p-1",
                "provider_name": "Example Agency LLC",
                "provider_detail_url": "https://sellercentral.amazon.example/p-1",
                "listing_logo_url": "https://m.media-amazon.com/logo.png",
                "official_url": "https://exampleagency.com",
                "official_domain": "exampleagency.com",
                "confidence": "68",
                "status": "needs_review",
                "evidence_summary": "needs verification",
                "candidate_count": "1",
                "scored_candidate_count": "1",
                "service_apis": json.dumps(["Account Management"]),
                "provider_locations": json.dumps(["United Kingdom"]),
            }
            review = {
                "provider_id": "p-1",
                "provider_name": "Example Agency LLC",
                "provider_detail_url": "https://sellercentral.amazon.example/p-1",
                "listing_logo_url": "https://m.media-amazon.com/logo.png",
                "review_priority": "medium",
                "suggested_action": "verify_candidate_or_replace",
                "status": "needs_review",
                "confidence": "68",
                "official_url": "https://exampleagency.com",
                "official_domain": "exampleagency.com",
                "evidence_summary": "needs verification",
                "candidate_count": "1",
                "scored_candidate_count": "1",
                "candidate_1_url": "https://exampleagency.com",
                "candidate_1_domain": "exampleagency.com",
                "candidate_1_score": "68",
                "candidate_1_reject": "False",
                "candidate_1_source": "brave",
                "candidate_1_rank": "1",
                "candidate_1_query": '"Example Agency LLC" official website',
                "candidate_1_reasons": "domain_exact_provider_slug",
            }
            extra_provider = dict(provider)
            extra_provider["provider_id"] = "p-2"
            extra_provider["provider_name"] = "Outside Limit LLC"
            _write_test_csv(run_dir / "providers_normalized.csv", [provider, extra_provider])
            _write_test_csv(run_dir / "provider_official_websites_enriched.csv", [result])
            _write_test_csv(run_dir / "provider_review_sheet_enhanced.csv", [review])
            (run_dir / "manifest.json").write_text(
                json.dumps({"parameters": {"total_to_run": 1}, "summary": {}, "outputs": {}}),
                encoding="utf-8",
            )

            def fake_fetch(url):
                html = """
                <html><head><title>Example Agency LLC</title></head>
                <body>Example Agency LLC provides Amazon Seller Central account management,
                marketplace advertising and ecommerce services in the United Kingdom.
                Contact us. About us.</body></html>
                """
                return {"ok": True, "status": 200, "final_url": url, "text": html}

            with patch("finder.scoring.fetch_text", side_effect=fake_fetch):
                summary = run_unresolved_second_pass(run_dir=run_dir, accept_threshold=75)
            with (run_dir / "official_sites.csv").open(newline="", encoding="utf-8") as f:
                final_rows = list(csv.DictReader(f))
            legacy_final_exists = (run_dir / "provider_final_official_websites_second_pass.csv").exists()
            manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))

        self.assertEqual(summary["accepted_rows"], 1)
        self.assertEqual(summary["finalize"]["official_url_rows"], 1)
        self.assertTrue(summary["quality_overall"]["passed"])
        self.assertNotIn("row_count_mismatch", ";".join(summary["quality_overall"]["failures"]))
        self.assertEqual(final_rows[0]["status"], "manual_accepted")
        self.assertTrue(legacy_final_exists)
        self.assertTrue(manifest["summary"]["quality_passed"])
        self.assertEqual(manifest["summary"]["official_url_rows"], 1)
        self.assertEqual(manifest["summary"]["unresolved_rows"], 0)

    def test_second_pass_acceptance_threshold_is_75(self):
        config = load_config()
        matched_75 = {
            "official_url": "https://example.com",
            "status": "matched",
            "confidence": "75",
            "evidence_summary": "page_contains_exact_provider_name",
        }
        matched_74 = {
            "official_url": "https://example.com",
            "status": "matched",
            "confidence": "74",
            "evidence_summary": "page_contains_exact_provider_name",
        }

        self.assertEqual(DEFAULT_SECOND_PASS_ACCEPT_THRESHOLD, 75)
        self.assertTrue(_accepted(matched_75, config, DEFAULT_SECOND_PASS_ACCEPT_THRESHOLD))
        self.assertFalse(_accepted(matched_74, config, DEFAULT_SECOND_PASS_ACCEPT_THRESHOLD))

    def test_second_pass_accepts_lower_score_only_with_verified_identity(self):
        config = load_config()
        verified_64 = {
            "official_url": "https://exampleagency.com",
            "status": "needs_review",
            "confidence": "64",
            "evidence_summary": "domain_exact_provider_slug; http_ok_home; page_contains_exact_provider_name",
            "candidates": [
                {
                    "url": "https://exampleagency.com",
                    "reject": False,
                    "source": "second_pass_domain_variant",
                    "reasons": [
                        "domain_exact_provider_slug",
                        "http_ok_home",
                        "page_contains_exact_provider_name",
                    ],
                }
            ],
        }
        guessed_58 = {
            "official_url": "https://exampleagency.com",
            "status": "needs_review",
            "confidence": "58",
            "evidence_summary": "domain_exact_provider_slug; search_result_contains_exact_name",
            "candidates": [
                {
                    "url": "https://exampleagency.com",
                    "reject": False,
                    "source": "second_pass_top_candidate",
                    "reasons": ["domain_exact_provider_slug", "search_result_contains_exact_name"],
                }
            ],
        }
        platform_58 = {
            "official_url": "https://vimeo.com",
            "status": "needs_review",
            "confidence": "58",
            "evidence_summary": "search_snippet_contains_amazon_service_keywords; search_result_contains_exact_name",
            "candidates": [
                {
                    "url": "https://vimeo.com",
                    "reject": False,
                    "source": "brave",
                    "reasons": [
                        "search_result_contains_exact_name",
                        "top_search_result",
                        "official_website_query_hit",
                        "http_ok_home",
                        "page_contains_provider_name_tokens",
                        "search_snippet_contains_amazon_service_keywords",
                    ],
                }
            ],
        }

        self.assertTrue(_accepted(verified_64, config, 70))
        self.assertFalse(_accepted(guessed_58, config, 70))
        self.assertFalse(_accepted(platform_58, config, 70))

    def test_second_pass_rejects_borderline_without_strong_evidence_and_parking_urls(self):
        config = load_config()
        weak_74 = {
            "official_url": "https://example.com",
            "status": "matched",
            "confidence": "74",
            "evidence_summary": "domain_exact_provider_slug; http_ok_home",
            "candidates": [],
        }
        parked_95 = {
            "official_url": "https://www.hugedomains.com/domain_profile.cfm?d=example.com",
            "status": "matched",
            "confidence": "95",
            "evidence_summary": "page_contains_exact_provider_name",
        }
        staging_95 = {
            "official_url": "https://staging.example.com/",
            "status": "matched",
            "confidence": "95",
            "evidence_summary": "page_contains_exact_provider_name",
        }

        self.assertFalse(_accepted(weak_74, config, 70))
        self.assertFalse(_accepted(parked_95, config, 70))
        self.assertFalse(_accepted(staging_95, config, 70))

    def test_quality_gate_passes_clean_labeled_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            results_path = Path(tmp) / "results.csv"
            rows = [
                {
                    "provider_id": "p-1",
                    "provider_name": "One",
                    "official_url": "https://one.example",
                    "official_domain": "one.example",
                    "confidence": "90",
                    "status": "matched",
                    "evidence_summary": "strong",
                },
                {
                    "provider_id": "p-2",
                    "provider_name": "Two",
                    "official_url": "https://two.example",
                    "official_domain": "two.example",
                    "confidence": "65",
                    "status": "needs_review",
                    "evidence_summary": "review",
                },
            ]
            with results_path.open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=rows[0].keys())
                writer.writeheader()
                writer.writerows(rows)
            labels = [
                {"provider_id": "p-1", "provider_name": "One", "expected_domain": "one.example"},
                {"provider_id": "p-2", "provider_name": "Two", "expected_domain": "two.example"},
            ]

            summary = evaluate_quality_gate(
                results_csv=results_path,
                config=load_config("config/scoring.json"),
                labels=labels,
                expected_rows=2,
                min_domain_accuracy=1.0,
                min_auto_precision=1.0,
                min_official_url_rate=1.0,
                max_unresolved_rate=0.5,
            )

        self.assertTrue(summary["overall"]["passed"])
        self.assertEqual(summary["overall"]["labeled_domain_accuracy"], 1.0)
        self.assertEqual(summary["overall"]["official_url_rate"], 1.0)

    def test_quality_gate_fails_excluded_official_domains_and_duplicates(self):
        with tempfile.TemporaryDirectory() as tmp:
            results_path = Path(tmp) / "results.csv"
            rows = [
                {
                    "provider_id": "p-1",
                    "provider_name": "One",
                    "official_url": "https://www.linkedin.com/company/one",
                    "official_domain": "linkedin.com",
                    "confidence": "90",
                    "status": "matched",
                    "evidence_summary": "bad",
                },
                {
                    "provider_id": "p-1",
                    "provider_name": "One Duplicate",
                    "official_url": "one.example",
                    "official_domain": "one.example",
                    "confidence": "90",
                    "status": "matched",
                    "evidence_summary": "bad",
                },
            ]
            with results_path.open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=rows[0].keys())
                writer.writeheader()
                writer.writerows(rows)

            summary = evaluate_quality_gate(
                results_csv=results_path,
                config=load_config("config/scoring.json"),
                expected_rows=2,
                min_official_url_rate=1.0,
                max_unresolved_rate=0.0,
            )

        self.assertFalse(summary["overall"]["passed"])
        self.assertIn("duplicate_provider_ids:1", summary["overall"]["failures"])
        self.assertIn("excluded_official_urls:1", summary["overall"]["failures"])
        self.assertIn("malformed_official_urls:1", summary["overall"]["failures"])

    def test_quality_gate_fails_low_coverage_and_high_unresolved_rate(self):
        with tempfile.TemporaryDirectory() as tmp:
            results_path = Path(tmp) / "results.csv"
            rows = [
                {
                    "provider_id": "p-1",
                    "provider_name": "One",
                    "official_url": "https://one.example",
                    "official_domain": "one.example",
                    "confidence": "90",
                    "status": "matched",
                    "evidence_summary": "strong",
                },
                {
                    "provider_id": "p-2",
                    "provider_name": "Two",
                    "official_url": "",
                    "official_domain": "",
                    "confidence": "35",
                    "status": "low_confidence",
                    "evidence_summary": "weak",
                },
            ]
            with results_path.open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=rows[0].keys())
                writer.writeheader()
                writer.writerows(rows)

            summary = evaluate_quality_gate(
                results_csv=results_path,
                config=load_config("config/scoring.json"),
                expected_rows=2,
                min_official_url_rate=0.75,
                max_unresolved_rate=0.25,
            )

        self.assertFalse(summary["overall"]["passed"])
        self.assertEqual(summary["overall"]["official_url_rate"], 0.5)
        self.assertEqual(summary["overall"]["unresolved_rate"], 0.5)
        self.assertIn("official_url_rate_below_threshold:0.5", summary["overall"]["failures"])
        self.assertIn("unresolved_rate_above_threshold:0.5", summary["overall"]["failures"])

    def test_run_pipeline_dry_run_writes_reproducible_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.csv"
            run_dir = Path(tmp) / "run"
            rows = [
                {
                    "provider_id": "p-1",
                    "provider_name": "Example Agency LLC",
                    "service_api": "Account Management",
                    "detail_url": "https://sellercentral-europe.amazon.com/gspn/provider-details/p-1",
                    "listing_logo_url": "",
                    "about_listing_text": "Amazon account management.",
                    "service_description": "",
                    "service_types_json": "[]",
                    "provider_locations_json": json.dumps(["United Kingdom"]),
                    "provider_languages_json": json.dumps(["English"]),
                }
            ]
            with source.open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=rows[0].keys())
                writer.writeheader()
                writer.writerows(rows)

            manifest = run_pipeline(
                source_csv=source,
                run_dir=run_dir,
                limit=1,
                batch_size=1,
                per_query=2,
                max_candidates=5,
                dry_run=True,
            )
            manifest_path = run_dir / "manifest.json"
            saved = json.loads(manifest_path.read_text(encoding="utf-8"))

        self.assertEqual(manifest["summary"]["status"], "dry_run")
        self.assertEqual(saved["parameters"]["total_to_run"], 1)
        self.assertIn("python3 -m finder.cli run", "\n".join(saved["commands"]))
        self.assertIn("review_sheet", saved["outputs"])
        self.assertFalse((run_dir / "providers_normalized.csv").exists())

    def test_run_pipeline_manifest_records_review_cutoffs(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.csv"
            run_dir = Path(tmp) / "run"
            rows = [
                {
                    "provider_id": "p-1",
                    "provider_name": "Example Agency LLC",
                    "service_api": "Account Management",
                    "detail_url": "https://sellercentral-europe.amazon.com/gspn/provider-details/p-1",
                    "listing_logo_url": "",
                    "about_listing_text": "Amazon account management.",
                    "service_description": "",
                    "service_types_json": "[]",
                    "provider_locations_json": json.dumps(["United Kingdom"]),
                    "provider_languages_json": json.dumps(["English"]),
                }
            ]
            with source.open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=rows[0].keys())
                writer.writeheader()
                writer.writerows(rows)

            def fake_run_workflow(providers, output_csv, evidence_jsonl, config, **kwargs):
                del config, kwargs
                output_path = Path(output_csv)
                evidence_path = Path(evidence_jsonl)
                output_path.parent.mkdir(parents=True, exist_ok=True)
                evidence_path.parent.mkdir(parents=True, exist_ok=True)
                result_rows = [
                    {
                        "provider_id": providers[0]["provider_id"],
                        "provider_name": providers[0]["provider_name"],
                        "official_url": "https://www.exampleagency.com",
                        "official_domain": "exampleagency.com",
                        "confidence": str(DEFAULT_MATCHED_REVIEW_CONFIDENCE_CUTOFF - 1),
                        "status": "matched",
                        "evidence_summary": "name_match; service_match; country_match",
                        "candidate_count": "1",
                        "scored_candidate_count": "1",
                        "service_apis": json.dumps(["Account Management"]),
                        "provider_locations": json.dumps(["United Kingdom"]),
                    }
                ]
                with output_path.open("w", newline="", encoding="utf-8") as f:
                    writer = csv.DictWriter(f, fieldnames=list(result_rows[0].keys()))
                    writer.writeheader()
                    writer.writerows(result_rows)
                evidence_path.write_text(
                    json.dumps(
                        {
                            "provider_id": "p-1",
                            "provider_name": "Example Agency LLC",
                            "candidate_count": 1,
                            "scored_candidate_count": 1,
                            "candidates": [],
                        }
                    )
                    + "\n",
                    encoding="utf-8",
                )

            with patch("tools.run_pipeline.doctor", return_value={"production_ready": True, "configured_sources": []}):
                with patch("tools.run_pipeline.run_workflow", side_effect=fake_run_workflow):
                    manifest = run_pipeline(
                        source_csv=source,
                        run_dir=run_dir,
                        limit=1,
                        batch_size=1,
                        per_query=2,
                        max_candidates=5,
                        min_auto_precision=0,
                    )
            saved = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))

        self.assertEqual(manifest["summary"]["manual_review_rows"], 1)
        self.assertEqual(
            manifest["summary"]["matched_review_confidence_below"],
            DEFAULT_MATCHED_REVIEW_CONFIDENCE_CUTOFF,
        )
        self.assertEqual(
            manifest["summary"]["second_pass_review_confidence_below"],
            DEFAULT_SECOND_PASS_REVIEW_CONFIDENCE_CUTOFF,
        )
        self.assertEqual(
            manifest["manual_review_task"]["matched_review_confidence_below"],
            DEFAULT_MATCHED_REVIEW_CONFIDENCE_CUTOFF,
        )
        self.assertEqual(
            manifest["manual_review_task"]["second_pass_review_confidence_below"],
            DEFAULT_SECOND_PASS_REVIEW_CONFIDENCE_CUTOFF,
        )
        self.assertEqual(saved["summary"], manifest["summary"])

    def test_run_pipeline_stops_without_production_source_unless_allowed(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {}, clear=True):
            source = Path(tmp) / "source.csv"
            run_dir = Path(tmp) / "run"
            rows = [
                {
                    "provider_id": "p-1",
                    "provider_name": "Example Agency LLC",
                    "service_api": "Account Management",
                    "detail_url": "",
                    "listing_logo_url": "",
                    "about_listing_text": "",
                    "service_description": "",
                    "service_types_json": "[]",
                    "provider_locations_json": "[]",
                    "provider_languages_json": "[]",
                }
            ]
            with source.open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=rows[0].keys())
                writer.writeheader()
                writer.writerows(rows)

            with self.assertRaises(PipelineError):
                run_pipeline(source_csv=source, run_dir=run_dir, limit=1, batch_size=1)
            manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))

        self.assertEqual(manifest["summary"]["status"], "stopped_no_production_source")

    def test_apply_review_updates_final_outputs_and_manifest_without_researching(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            run_dir.mkdir()
            results_path = run_dir / "provider_official_websites.csv"
            review_path = run_dir / "provider_review_sheet_enhanced.csv"
            labels_path = run_dir / "labels.csv"
            result_rows = [
                {
                    "provider_id": "p-1",
                    "provider_name": "One",
                    "official_url": "https://one.example",
                    "official_domain": "one.example",
                    "confidence": "90",
                    "status": "matched",
                    "evidence_summary": "strong",
                    "candidate_count": "1",
                    "scored_candidate_count": "1",
                    "service_apis": "[]",
                    "provider_locations": "[]",
                },
                {
                    "provider_id": "p-2",
                    "provider_name": "Two",
                    "official_url": "",
                    "official_domain": "",
                    "confidence": "35",
                    "status": "low_confidence",
                    "evidence_summary": "weak",
                    "candidate_count": "1",
                    "scored_candidate_count": "1",
                    "service_apis": "[]",
                    "provider_locations": "[]",
                },
            ]
            with results_path.open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=result_rows[0].keys())
                writer.writeheader()
                writer.writerows(result_rows)
            review_rows = [
                {
                    "provider_id": "p-2",
                    "provider_name": "Two",
                    "manual_decision": "replace",
                    "manual_url": "https://two.example",
                    "notes": "confirmed",
                }
            ]
            with review_path.open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=review_rows[0].keys())
                writer.writeheader()
                writer.writerows(review_rows)
            labels = [
                {"provider_id": "p-1", "provider_name": "One", "expected_domain": "one.example"},
                {"provider_id": "p-2", "provider_name": "Two", "expected_domain": "two.example"},
            ]
            with labels_path.open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=labels[0].keys())
                writer.writeheader()
                writer.writerows(labels)
            manifest = {
                "config_path": "config/scoring.json",
                "labels_csv": str(labels_path),
                "parameters": {
                    "total_to_run": 2,
                    "min_domain_accuracy": 1.0,
                    "min_auto_precision": 1.0,
                    "min_official_url_rate": 1.0,
                    "max_unresolved_rate": 0.0,
                },
                "outputs": {
                    "manifest": str(run_dir / "manifest.json"),
                    "results": str(results_path),
                    "review_sheet": str(review_path),
                    "final": str(run_dir / "provider_final_official_websites.csv"),
                    "unresolved": str(run_dir / "provider_unresolved.csv"),
                    "quality_md": str(run_dir / "quality_gate_provider_final.md"),
                    "quality_json": str(run_dir / "quality_gate_provider_final.json"),
                },
                "summary": {"status": "complete"},
            }
            (run_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

            updated = apply_review(run_dir=run_dir)
            with (run_dir / "provider_final_official_websites.csv").open(newline="", encoding="utf-8") as f:
                final_rows = list(csv.DictReader(f))

        self.assertEqual(updated["summary"]["status"], "review_applied")
        self.assertTrue(updated["summary"]["quality_passed"])
        self.assertEqual(final_rows[1]["official_domain"], "two.example")
        self.assertEqual(updated["post_review"]["quality_overall"]["official_url_rate"], 1.0)

    def test_preflight_report_blocks_without_production_search_source(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {}, clear=True):
            source = Path(tmp) / "source.csv"
            rows = [
                {
                    "provider_id": "p-1",
                    "provider_name": "Example Agency LLC",
                    "service_api": "Account Management",
                    "detail_url": "",
                    "listing_logo_url": "",
                    "about_listing_text": "",
                    "service_description": "",
                    "service_types_json": "[]",
                    "provider_locations_json": "[]",
                    "provider_languages_json": "[]",
                }
            ]
            with source.open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=rows[0].keys())
                writer.writeheader()
                writer.writerows(rows)

            report = build_preflight_report(source_csv=source, run_dir=Path(tmp) / "run")
            markdown = render_markdown(report)

        self.assertFalse(report["summary"]["ready_for_production_run"])
        self.assertIn("No production search API key is configured.", report["summary"]["readiness_failures"])
        self.assertIn("python3 tools/run_pipeline.py", report["recommended_commands"]["production_pipeline"])
        self.assertIn("NOT READY", markdown)

    def test_preflight_report_is_ready_with_production_key(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"BRAVE_API_KEY": "brave-test"}, clear=True):
            source = Path(tmp) / "source.csv"
            labels = Path(tmp) / "labels.csv"
            rows = [
                {
                    "provider_id": "p-1",
                    "provider_name": "Example Agency LLC",
                    "service_api": "Account Management",
                    "detail_url": "",
                    "listing_logo_url": "",
                    "about_listing_text": "",
                    "service_description": "",
                    "service_types_json": "[]",
                    "provider_locations_json": "[]",
                    "provider_languages_json": "[]",
                }
            ]
            with source.open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=rows[0].keys())
                writer.writeheader()
                writer.writerows(rows)
            with labels.open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=["provider_id", "provider_name", "expected_domain"])
                writer.writeheader()
                writer.writerow(
                    {
                        "provider_id": "p-1",
                        "provider_name": "Example Agency LLC",
                        "expected_domain": "exampleagency.com",
                    }
                )

            report = build_preflight_report(source_csv=source, run_dir=Path(tmp) / "run", labels_csv=labels)

        self.assertTrue(report["summary"]["ready_for_production_run"])
        self.assertEqual(report["summary"]["configured_sources"], ["brave"])
        self.assertEqual(report["summary"]["normalized_provider_count"], 1)
        self.assertIn("--labels", report["recommended_commands"]["production_pipeline"])
        self.assertEqual(report["scale_estimate"]["estimated_search_requests"], 6)

    def test_preflight_live_check_failure_blocks_production_readiness(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"BRAVE_API_KEY": "brave-test"}, clear=True), patch(
            "tools.preflight_report.smoke_test_configured_sources",
            return_value=[
                {
                    "source": "brave",
                    "ok": False,
                    "candidate_count": 0,
                    "error": "Unauthorized",
                    "error_type": "HTTPError",
                }
            ],
        ):
            source = Path(tmp) / "source.csv"
            rows = [
                {
                    "provider_id": "p-1",
                    "provider_name": "Example Agency LLC",
                    "service_api": "Account Management",
                    "detail_url": "",
                    "listing_logo_url": "",
                    "about_listing_text": "",
                    "service_description": "",
                    "service_types_json": "[]",
                    "provider_locations_json": "[]",
                    "provider_languages_json": "[]",
                }
            ]
            with source.open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=rows[0].keys())
                writer.writeheader()
                writer.writerows(rows)

            report = build_preflight_report(source_csv=source, run_dir=Path(tmp) / "run", live_check=True)

        self.assertFalse(report["summary"]["ready_for_production_run"])
        self.assertIn("Live search API check failed for: brave.", report["summary"]["readiness_failures"])


if __name__ == "__main__":
    unittest.main()
