import csv
import io
import json
import os
import tempfile
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
from tools.output_layout import DEFAULT_SECOND_PASS_ACCEPT_THRESHOLD, WORKFLOW_VERSION


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
                    "confidence": "74",
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
            ]
            _write_test_csv(run_dir / "official_sites.csv", final_rows)
            with patch("tools.run_agent_b_verification.fetch_text", return_value={"ok": False, "text": ""}), patch(
                "tools.run_agent_b_verification.collect_candidates_for_queries", return_value=[]
            ):
                summary = run_agent_b_verification(run_dir=run_dir, write_xlsx=False)
            with (run_dir / "agent_b/check.csv").open(newline="", encoding="utf-8") as f:
                checked_ids = [row["provider_id"] for row in csv.DictReader(f)]

        self.assertEqual(summary["input_rows"], 3)
        self.assertEqual(checked_ids, ["second", "low", "logo"])

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
            _write_test_csv(run_dir / "providers_normalized.csv", [provider])
            _write_test_csv(run_dir / "provider_official_websites_enriched.csv", [result])
            _write_test_csv(run_dir / "provider_review_sheet_enhanced.csv", [review])

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

        self.assertEqual(summary["accepted_rows"], 1)
        self.assertEqual(summary["finalize"]["official_url_rows"], 1)
        self.assertEqual(final_rows[0]["status"], "manual_accepted")
        self.assertTrue(legacy_final_exists)

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
