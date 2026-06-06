from support import *

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

SearchSourceTests.__module__ = "test_workflow"
