from support import *

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

    def test_configure_env_can_add_openai_key_without_printing_secret(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            openai_file = root / "openaikey.rtf"
            env_file = root / ".env"
            example_file = root / ".env.example"
            openai_file.write_text(r"{\rtf1\ansi OPENAI_API_KEY=openai-secret-key-123\par}", encoding="utf-8")
            example_file.write_text("OPENAI_API_KEY=\nFINDER_DEV_AGENT_MODEL=gpt-4.1-mini\n", encoding="utf-8")

            out = io.StringIO()
            with redirect_stdout(out):
                configure_env_main(
                    [
                        "--openai-key-file",
                        str(openai_file),
                        "--env",
                        str(env_file),
                        "--example",
                        str(example_file),
                    ]
                )

            text = env_file.read_text(encoding="utf-8")
            self.assertIn("OPENAI_API_KEY=openai-secret-key-123", text)
            self.assertIn("FINDER_DEV_AGENT_MODEL=gpt-4.1-mini", text)
            self.assertNotIn("openai-secret-key-123", out.getvalue())

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

    def test_deduplicate_input_writes_explicit_workflow_body_input_and_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "raw.csv"
            output = root / "details/input/deduped_input.csv"
            xlsx = root / "details/input/deduped_input.xlsx"
            report_json = root / "details/input/dedupe_report.json"
            report_md = root / "details/input/dedupe_report.md"
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
            _write_test_csv(source, rows)

            summary = deduplicate_input(
                source_csv=source,
                output_csv=output,
                output_xlsx=xlsx,
                report_json=report_json,
                report_md=report_md,
            )

            with output.open(newline="", encoding="utf-8-sig") as f:
                deduped_rows = list(csv.DictReader(f))
            self.assertEqual(len(deduped_rows), 1)
            self.assertEqual(deduped_rows[0]["provider_id"], "p-1")
            self.assertEqual(json.loads(deduped_rows[0]["service_apis"]), ["Account Management", "Advertising Optimization"])
            self.assertEqual(summary["valid_provider_rows"], 2)
            self.assertEqual(summary["output_provider_rows"], 1)
            self.assertEqual(summary["duplicate_extra_rows"], 1)
            self.assertTrue(xlsx.exists())
            self.assertTrue(report_json.exists())
            self.assertIn("Duplicate extra rows removed", report_md.read_text(encoding="utf-8"))

    def test_query_builder_includes_web_queries_without_github_queries(self):
        provider = {
            "provider_name": "Example Agency LLC",
            "service_apis": ["Account Management"],
            "provider_locations": ["United Kingdom"],
        }

        queries = build_queries(provider)

        self.assertIn('"Example Agency LLC" official website', queries)
        self.assertIn('"Example Agency LLC" "United Kingdom" website', queries)
        self.assertNotIn('site:github.com "Example Agency LLC"', queries)
        self.assertFalse(any("site:github.com" in query for query in queries))

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

    def test_html_extractor_reads_structured_dom_and_json_ld_organization(self):
        html = """
        <html>
          <head>
            <title>Example Agency</title>
            <meta name="description" content="Amazon marketplace support">
            <script type="application/ld+json">
              {
                "@context": "https://schema.org",
                "@type": "Organization",
                "name": "Example Agency LLC",
                "legalName": "Example Agency Limited",
                "url": "https://exampleagency.com",
                "logo": "https://exampleagency.com/logo.png",
                "address": {"streetAddress": "1 High Street", "addressCountry": "United Kingdom"},
                "contactPoint": {"email": "hello@exampleagency.com", "telephone": "+44 20 0000 0000"}
              }
            </script>
          </head>
          <body>
            <nav>Home Services Contact</nav>
            <h1>Example Agency LLC</h1>
            <h2>Amazon Seller Central Services</h2>
            <a href="mailto:hello@exampleagency.com">Email</a>
            <a href="tel:+442000000000">Call</a>
            <footer>Registered in the United Kingdom</footer>
          </body>
        </html>
        """
        extracted = extract_html(html, "https://exampleagency.com/")

        self.assertEqual(extracted["title"], "Example Agency")
        self.assertIn("Example Agency LLC", extracted["h1"])
        self.assertIn("Amazon Seller Central Services", extracted["h2"])
        self.assertIn("Home Services Contact", extracted["nav"])
        self.assertIn("Registered in the United Kingdom", extracted["footer"])
        self.assertEqual(extracted["mailto_links"], ["mailto:hello@exampleagency.com"])
        self.assertEqual(extracted["tel_links"], ["tel:+442000000000"])
        self.assertEqual(extracted["organizations"][0]["name"], "Example Agency LLC")
        self.assertIn("United Kingdom", extracted["organizations"][0]["address"])

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
        self.assertGreaterEqual(_text_similarity("Arara ApS", "Arara"), 90)

WorkflowTests.__module__ = "test_workflow"

class OperationalCommandTests(unittest.TestCase):
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
        self.assertTrue(is_excluded_domain("https://new.myteamz.co.uk/", config))
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

    def test_generic_only_identity_requires_location_corroboration(self):
        config = load_config("config/scoring.json")
        provider = {
            "provider_name": "Global Sellers",
            "provider_locations": ["Poland"],
            "service_apis": ["Account Management", "Advertising Optimization"],
        }
        candidate = SearchCandidate(
            url="https://www.globalsellers.net/",
            title="Global Sellers Association",
            snippet="Global Sellers offers Amazon and ecommerce seller services.",
            source="brave",
            query='"Global Sellers" official website',
            rank=1,
        )

        def fake_fetch_without_location(url):
            return {
                "ok": True,
                "status": 200,
                "final_url": url,
                "text": "<html><title>Global Sellers Association</title><body>Global Sellers provides Amazon ecommerce seller support and account management.</body></html>",
            }

        def fake_fetch_with_location(url):
            return {
                "ok": True,
                "status": 200,
                "final_url": url,
                "text": "<html><title>Global Sellers</title><body>Global Sellers sp. z o.o. Polska provides Amazon account management and advertising services.</body></html>",
            }

        with patch("finder.scoring.fetch_text", side_effect=fake_fetch_without_location):
            capped = choose_best(provider, [candidate], config)
        with patch("finder.scoring.fetch_text", side_effect=fake_fetch_with_location):
            accepted = choose_best(provider, [candidate], config)

        self.assertEqual(capped["status"], "needs_review")
        self.assertIn("identity_cap_generic_name_requires_location", capped["evidence_summary"])
        self.assertEqual(accepted["status"], "matched")

    def test_fetch_selection_keeps_search_candidate_when_domain_guesses_lead(self):
        preliminary = [
            {
                "url": "https://www.araraaps.com/",
                "score": 35,
                "reject": False,
                "source": "second_pass_domain_variant",
            },
            {
                "url": "https://www.araraaps.net/",
                "score": 35,
                "reject": False,
                "source": "second_pass_domain_variant",
            },
            {
                "url": "https://arara-partners.com/en/about-us.html",
                "score": 34,
                "reject": False,
                "source": "exa",
            },
        ]

        urls = _urls_to_fetch(preliminary, 2)

        self.assertIn("https://arara-partners.com/en/about-us.html", urls)
        self.assertEqual(len(urls), 2)

    def test_legal_suffix_identity_can_accept_country_service_match(self):
        config = load_config("config/scoring.json")
        provider = {
            "provider_name": "Arara ApS",
            "provider_locations": ["Denmark"],
            "service_apis": ["Account Management", "Advertising Optimization"],
        }
        candidate = SearchCandidate(
            url="https://arara-partners.com/en/about-us.html",
            title="Arara Partners | Amazon agency in Denmark",
            snippet="Arara helps brands with Amazon account management, advertising, and marketplace services in Denmark.",
            source="exa",
            query='"Arara ApS" official website',
            rank=1,
        )

        def fake_fetch(url):
            return {
                "ok": True,
                "status": 200,
                "final_url": "https://arara-partners.com/",
                "text": """
                    <html><head><title>Arara Partners</title></head>
                    <body>
                    Arara is an Amazon agency based in Denmark. We support Amazon account management,
                    marketplace advertising and ecommerce services for brands.
                    Contact us. About us. Privacy policy.
                    </body></html>
                """,
            }

        with patch("finder.scoring.fetch_text", side_effect=fake_fetch):
            result = choose_best(provider, [candidate], config)

        self.assertEqual(result["official_url"], "https://arara-partners.com/")
        self.assertEqual(result["status"], "matched")
        self.assertIn("page_contains_exact_provider_name", result["evidence_summary"])

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

OperationalCommandTests.__module__ = "test_workflow"
