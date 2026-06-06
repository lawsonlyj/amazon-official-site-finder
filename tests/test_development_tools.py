from support import *

class OperationalCommandTests(unittest.TestCase):
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
            with (run_dir / "check_suggestion/check.csv").open(newline="", encoding="utf-8") as f:
                rows = {row["provider_id"]: row for row in csv.DictReader(f)}
            xlsx_exists = (run_dir / "check_suggestion/check.xlsx").exists()
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
        self.assertFalse(legacy_xlsx_exists)

    def test_agent_b_uses_structured_dom_evidence_without_researching_strong_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            rows = [
                {
                    "provider_id": "p-1",
                    "provider_name": "Deep Evidence Agency LLC",
                    "provider_detail_url": "https://amazon.example/p-1",
                    "official_url": "https://deep.example",
                    "provider_locations": json.dumps(["United Kingdom"]),
                    "confidence": "81",
                    "status": "manual_accepted",
                }
            ]
            _write_test_csv(run_dir / "review_task.csv", rows)
            _write_test_csv(run_dir / "official_sites.csv", rows)

            def fake_fetch(url):
                html = """
                <html><head><title>Deep Evidence Agency LLC</title>
                <meta name="description" content="Amazon marketplace account management">
                <script type="application/ld+json">
                {"@type":"Organization","name":"Deep Evidence Agency LLC",
                "legalName":"Deep Evidence Agency Limited",
                "address":{"addressCountry":"United Kingdom"},
                "contactPoint":{"email":"hello@deep.example","telephone":"+44 20 0000 0000"}}
                </script></head>
                <body><nav>Home Services Contact</nav><h1>Deep Evidence Agency LLC</h1>
                <h2>Amazon Seller Central and ecommerce advertising services</h2>
                <a href="mailto:hello@deep.example">Email</a>
                <footer>Registered office in the United Kingdom</footer>
                About us. Contact us. Privacy policy. Terms and conditions.</body></html>
                """
                return {"ok": True, "status": 200, "final_url": url, "text": html}

            with patch("tools.run_agent_b_verification.fetch_text", side_effect=fake_fetch), patch(
                "tools.run_agent_b_verification.collect_candidates_for_queries"
            ) as collect:
                summary = run_agent_b_verification(run_dir=run_dir, write_xlsx=False)
            with (run_dir / "check_suggestion/check.csv").open(newline="", encoding="utf-8") as f:
                out = list(csv.DictReader(f))[0]
            details = [json.loads(line) for line in (run_dir / "check_suggestion/check.jsonl").read_text(encoding="utf-8").splitlines()]

        self.assertEqual(summary["decision_counts"], {"accept": 1})
        self.assertEqual(out["agent_b_decision"], "accept")
        self.assertIn("schema_org_name_matches_provider", out["supporting_facts"])
        self.assertIn("schema_org_contact_point_seen", out["supporting_facts"])
        self.assertIn("page_role:contact", out["supporting_facts"])
        self.assertEqual(details[0]["independent_search_ran"], False)
        collect.assert_not_called()

    def test_agent_b_rejects_structured_platform_or_parked_page_risks(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            rows = [
                {
                    "provider_id": "p-1",
                    "provider_name": "Profile Risk Agency",
                    "provider_detail_url": "https://amazon.example/p-1",
                    "official_url": "https://vendorhub.example/profile/profile-risk-agency",
                    "provider_locations": json.dumps(["United States"]),
                    "confidence": "80",
                    "status": "manual_accepted",
                },
                {
                    "provider_id": "p-2",
                    "provider_name": "Parked Risk Agency",
                    "provider_detail_url": "https://amazon.example/p-2",
                    "official_url": "https://parked.example",
                    "provider_locations": json.dumps(["United States"]),
                    "confidence": "80",
                    "status": "manual_accepted",
                },
            ]
            _write_test_csv(run_dir / "review_task.csv", rows)
            _write_test_csv(run_dir / "official_sites.csv", rows)

            def fake_fetch(url):
                if "parked.example" in url:
                    html = "<html><body>Parked Risk Agency. This domain may be for sale. Buy this domain.</body></html>"
                else:
                    html = "<html><body>Profile Risk Agency company profile. Claim this profile. View profile.</body></html>"
                return {"ok": True, "status": 200, "final_url": url, "text": html}

            with patch("tools.run_agent_b_verification.fetch_text", side_effect=fake_fetch), patch(
                "tools.run_agent_b_verification.collect_candidates_for_queries", return_value=[]
            ):
                summary = run_agent_b_verification(run_dir=run_dir, write_xlsx=False)
            with (run_dir / "check_suggestion/check.csv").open(newline="", encoding="utf-8") as f:
                out = {row["provider_id"]: row for row in csv.DictReader(f)}

        self.assertEqual(summary["decision_counts"], {"reject": 2})
        self.assertIn("candidate_looks_platform_profile", out["p-1"]["counter_evidence"])
        self.assertIn("candidate_looks_directory_page", out["p-1"]["counter_evidence"])
        self.assertIn("candidate_looks_parked_or_domain_sale", out["p-2"]["counter_evidence"])

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
            with (run_dir / "check_suggestion/check.csv").open(newline="", encoding="utf-8") as f:
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
            with (run_dir / "check_suggestion/check.csv").open(newline="", encoding="utf-8") as f:
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
            with (run_dir / "check_suggestion/check.csv").open(newline="", encoding="utf-8") as f:
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
            with (run_dir / "check_suggestion/check.csv").open(newline="", encoding="utf-8") as f:
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
            _write_test_csv(run_dir / "check_suggestion/check.csv", existing_agent_b)
            (run_dir / "check_suggestion/check.jsonl").write_text(
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
            with (run_dir / "check_suggestion/check.csv").open(newline="", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
            jsonl_lines = (run_dir / "check_suggestion/check.jsonl").read_text(encoding="utf-8").strip().splitlines()

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
            with (run_dir / "check_suggestion/check.csv").open(newline="", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))

        self.assertEqual(summary["output_rows"], 1)
        self.assertEqual(summary["timeout_rows"], 1)
        self.assertEqual(rows[0]["agent_b_decision"], "unsure")
        self.assertEqual(rows[0]["reason_for_unsure"], "agent_b_row_timeout")
        self.assertIn("agent_b_row_timeout", rows[0]["counter_evidence"])

    def test_check_agent_reviews_only_high_risk_rows_and_preserves_core_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            core_rows = [
                {
                    "provider_id": "safe",
                    "provider_name": "Safe Brand",
                    "provider_detail_url": "https://amazon.example/safe",
                    "official_url": "https://safe.example",
                    "status": "matched",
                    "confidence": "96",
                }
            ]
            check_rows = [
                {
                    "provider_id": "safe",
                    "provider_name": "Safe Brand",
                    "provider_detail_url": "https://amazon.example/safe",
                    "candidate_url": "https://safe.example",
                    "candidate_domain": "safe.example",
                    "agent_b_decision": "accept",
                    "confidence": "96",
                    "evidence_score": "92",
                    "supporting_facts": "strong_identity",
                    "counter_evidence": "",
                    "reason_for_unsure": "",
                    "review_reason": "",
                    "source_status": "matched",
                    "source_confidence": "96",
                },
                {
                    "provider_id": "risk",
                    "provider_name": "Risk Brand",
                    "provider_detail_url": "https://amazon.example/risk",
                    "candidate_url": "https://risk.example",
                    "candidate_domain": "risk.example",
                    "agent_b_decision": "unsure",
                    "confidence": "61",
                    "evidence_score": "61",
                    "supporting_facts": "page_contains_provider_name",
                    "counter_evidence": "identity_gap_location_or_service_context_missing",
                    "reason_for_unsure": "insufficient_or_conflicting_evidence",
                    "review_reason": "precision_low_confidence_auto_match",
                    "source_status": "matched",
                    "source_confidence": "61",
                },
            ]
            _write_test_csv(run_dir / "official_sites.csv", core_rows)
            _write_test_csv(run_dir / "check_suggestion/check.csv", check_rows)
            original_core = (run_dir / "official_sites.csv").read_text(encoding="utf-8")
            client = _FakeJsonClient(
                [
                    {
                        "decision": "unsure",
                        "confidence": 66,
                        "supporting_facts": ["provider name appears"],
                        "counter_evidence": ["location evidence is weak"],
                        "reason_for_unsure": "needs_human_confirmation",
                        "suggestions": ["collect more country evidence before changing rules"],
                    }
                ]
            )

            summary = run_check_agent(run_dir=run_dir, client=client)
            with (run_dir / "development/check_agent/check.csv").open(newline="", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
            jsonl_lines = (run_dir / "development/check_agent/check.jsonl").read_text(encoding="utf-8").splitlines()
            core_after = (run_dir / "official_sites.csv").read_text(encoding="utf-8")

        self.assertEqual(summary["status"], "completed")
        self.assertEqual(summary["input_rows"], 1)
        self.assertEqual(rows[0]["provider_id"], "risk")
        self.assertEqual(rows[0]["check_agent_decision"], "unsure")
        self.assertEqual(rows[0]["manual_decision"], "unsure")
        self.assertEqual(rows[0]["provider_detail_url"], "https://amazon.example/risk")
        self.assertEqual(len(jsonl_lines), 1)
        self.assertEqual(core_after, original_core)
        self.assertIn("provider_detail_url", client.payloads[0]["user_payload"]["provider"])

    def test_check_agent_missing_openai_key_fails_closed_without_core_output_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            _write_test_csv(
                run_dir / "check_suggestion/check.csv",
                [
                    {
                        "provider_id": "p-1",
                        "provider_name": "Risk Brand",
                        "provider_detail_url": "https://amazon.example/p-1",
                        "candidate_url": "https://risk.example",
                        "candidate_domain": "risk.example",
                        "review_reason": "precision_low_confidence_auto_match",
                        "confidence": "70",
                        "evidence_score": "65",
                    }
                ],
            )
            _write_test_csv(run_dir / "official_sites.csv", [{"provider_id": "p-1", "official_url": "https://risk.example"}])
            original_core = (run_dir / "official_sites.csv").read_text(encoding="utf-8")
            with patch.dict(os.environ, {}, clear=True):
                summary = run_check_agent(run_dir=run_dir)
            check_csv_exists = (run_dir / "development/check_agent/check.csv").exists()
            summary_exists = (run_dir / "development/check_agent/summary.json").exists()
            core_after = (run_dir / "official_sites.csv").read_text(encoding="utf-8")

        self.assertEqual(summary["status"], "blocked")
        self.assertEqual(summary["reason"], "missing_openai_api_key")
        self.assertFalse(check_csv_exists)
        self.assertTrue(summary_exists)
        self.assertEqual(core_after, original_core)

    def test_optimization_agent_schema_and_gate_prevent_direct_application(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            _write_test_csv(
                run_dir / "development/check_agent/check.csv",
                [
                    {
                        "provider_id": "p-1",
                        "provider_name": "Risk Brand",
                        "provider_detail_url": "https://amazon.example/p-1",
                        "candidate_url": "https://risk.example",
                        "check_agent_decision": "reject",
                        "confidence": "90",
                        "supporting_facts": "none",
                        "counter_evidence": "platform page",
                    }
                ],
            )
            gates = {
                "summary": {"allowed_gate_count": 0, "not_allowed_gate_count": 1},
                "checks": [{"gate": "review_lane_change", "allowed": False, "can_apply_now": False}],
            }
            gates_path = run_dir / "calibration_application_gates.json"
            gates_path.write_text(json.dumps(gates), encoding="utf-8")
            client = _FakeJsonClient(
                [
                    {
                        "overall_decision": "apply_candidate",
                        "should_apply_now": True,
                        "recommendations": [{"action": "tighten_identity_rule", "evidence": "one row"}],
                        "blocked_reasons": [],
                        "needed_labels": [],
                        "needed_tests": ["identity regression"],
                        "risk_assessment": "Gate must pass before applying.",
                    }
                ]
            )

            summary = run_optimization_agent(run_dir=run_dir, application_gates_json=gates_path, client=client)
            decision_json_exists = (run_dir / "development/optimization_agent/decision.json").exists()
            decision_md_exists = (run_dir / "development/optimization_agent/decision.md").exists()

        self.assertEqual(summary["overall"]["status"], "completed")
        self.assertFalse(summary["overall"]["effective_apply_allowed"])
        self.assertEqual(summary["decision"]["overall_decision"], "needs_regression_test")
        self.assertIn("deterministic_gate_not_passed", summary["decision"]["blocked_reasons"])
        self.assertTrue(decision_json_exists)
        self.assertTrue(decision_md_exists)

    def test_development_cycle_report_combines_metrics_agents_and_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            eval_path = run_dir / "balance_eval_labeled100_latest.json"
            eval_path.write_text(
                json.dumps(
                    {
                        "overall": {
                            "labeled_rows": 100,
                            "auto_precision": 0.95,
                            "official_recall": 0.92,
                            "overall_accuracy": 0.9,
                            "false_official_rows": 4,
                            "over_rejected_rows": 6,
                            "manual_review_rows": 115,
                        }
                    }
                ),
                encoding="utf-8",
            )
            check_summary = run_dir / "development/check_agent/summary.json"
            check_summary.parent.mkdir(parents=True)
            check_summary.write_text(
                json.dumps({"status": "completed", "output_rows": 3, "decision_counts": {"unsure": 2, "reject": 1}}),
                encoding="utf-8",
            )
            opt_summary = run_dir / "development/optimization_agent/decision.json"
            opt_summary.parent.mkdir(parents=True)
            opt_summary.write_text(
                json.dumps({"overall": {"status": "completed", "overall_decision": "needs_more_labels", "effective_apply_allowed": False}}),
                encoding="utf-8",
            )
            gate_path = run_dir / "calibration_application_gates.json"
            gate_path.write_text(json.dumps({"summary": {"allowed_gate_count": 0, "not_allowed_gate_count": 2}}), encoding="utf-8")

            report = build_development_cycle_report(run_dir=run_dir, cycle=1, application_gates_json=gate_path)
            metrics_exists = (run_dir / "development/cycle_1/metrics.json").exists()

        self.assertEqual(report["summary"]["auto_precision"], 0.95)
        self.assertEqual(report["summary"]["check_agent_rows"], 3)
        self.assertEqual(report["summary"]["optimization_decision"], "needs_more_labels")
        self.assertEqual(report["summary"]["gate_blocked_count"], 2)
        self.assertTrue(metrics_exists)

    def test_agent_b_recommends_and_agent_a_applies_only_safe_rules(self):
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

            recommendations = run_agent_b_recommendations(run_dir=run_dir)
            dry_run = apply_agent_optimizations(run_dir=run_dir, config_path=config_path, apply=False)
            applied = apply_agent_optimizations(run_dir=run_dir, config_path=config_path, apply=True)
            config = json.loads(config_path.read_text(encoding="utf-8"))
            suggestions_exists = (run_dir / "check_suggestion/suggestions.json").exists()
            legacy_suggestions_exists = (run_dir / "agent_c_optimization_recommendations.json").exists()

        self.assertEqual(recommendations["overall"]["safe_config_action_count"], 1)
        self.assertEqual(dry_run["pending_excluded_domains"], ["bad-directory.example"])
        self.assertTrue(applied["updated"])
        self.assertIn("bad-directory.example", config["excluded_domains"])
        self.assertNotIn("single-case.example", config["excluded_domains"])
        self.assertTrue(suggestions_exists)
        self.assertFalse(legacy_suggestions_exists)
        self.assertNotIn("agent_c_recommendations", recommendations)

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

            recommendations = run_agent_b_recommendations(run_dir=run_dir)
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

            recommendations = run_agent_b_recommendations(run_dir=run_dir, human_review=human_review)
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

            recommendations = run_agent_b_recommendations(run_dir=run_dir, human_review=human_review)
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
            agent_b = run_dir / "check_suggestion/check.csv"
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
            agent_b = run_dir / "check_suggestion/check.csv"
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

    def test_evidence_pattern_features_include_missing_identity_and_generic_name_markers(self):
        features = features_for_review_agent_row(
            {
                "provider_id": "p-1",
                "provider_name": "Aseller",
                "review_reason": "precision_generic_identity_term_risk",
            },
            {
                "provider_id": "p-1",
                "provider_name": "Aseller",
                "candidate_url": "https://aseller.example",
                "candidate_domain": "aseller.example",
                "agent_b_decision": "unsure",
                "evidence_score": "81",
                "supporting_facts": "candidate_pages_fetch_ok; page_contains_exact_provider_name",
                "counter_evidence": "",
                "reason_for_unsure": "high_risk_identity_needs_human_confirmation",
            },
        )

        self.assertIn("provider_name_contains:seller", features)
        self.assertIn("provider_name_shape:single_token", features)
        self.assertIn("missing:legal_entity_marker_found", features)
        self.assertIn("missing:listing_logo_visual_match", features)
        self.assertIn("review_reason:precision_generic_identity_term_risk", features)

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
            summary_exists = (run_dir / "operation_optimization/pattern_release_applied.json").exists()

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
        self.assertEqual(legacy_rows[0]["official_domain"], "")

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
        self.assertIn("Check and Suggestion Recall Release Simulation", md_text)
        self.assertIn(str(pattern_release), md_text)
        self.assertIn("Prefer narrow pattern release over global threshold relaxation", md_text)
        self.assertIn("Pattern Release", md_text)
        self.assertTrue(json_exists)

    def test_build_balance_report_prefers_current_threshold_when_tied(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            labeled = root / "balance.json"
            labeled.write_text(
                json.dumps(
                    {
                        "overall": {
                            "manual_review_false_official_capture_rate": 1.0,
                            "agent_b_false_official_accept_rate": 0.0,
                        },
                        "threshold_simulations": [
                            {"threshold": 70, "overall_accuracy": 0.85, "official_recall": 0.908, "false_official_rows": 8},
                            {"threshold": 75, "overall_accuracy": 0.85, "official_recall": 0.908, "false_official_rows": 8},
                            {"threshold": 80, "overall_accuracy": 0.84, "official_recall": 0.885, "false_official_rows": 7},
                        ],
                        "agent_b_recall_release_simulations": [],
                        "manual_review_lanes": [],
                        "manual_review_lane_drop_simulations": [],
                    }
                ),
                encoding="utf-8",
            )

            report = build_balance_report(labeled_eval_json=labeled)

        self.assertEqual(report["summary"]["recommended_threshold"], DEFAULT_SECOND_PASS_ACCEPT_THRESHOLD)
        self.assertEqual(
            report["thresholds"]["recommendation"]["current_threshold"],
            DEFAULT_SECOND_PASS_ACCEPT_THRESHOLD,
        )

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

    def test_threshold_boundary_uses_current_threshold_when_simulations_are_unsorted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            labeled = root / "labeled.json"
            labeled.write_text(
                json.dumps(
                    {
                        "threshold_simulations": [
                            {
                                "threshold": 70,
                                "overall_accuracy": 0.85,
                                "auto_precision": 0.908,
                                "official_recall": 0.908,
                                "false_official_rows": 8,
                                "over_rejected_rows": 7,
                            },
                            {
                                "threshold": 75,
                                "overall_accuracy": 0.85,
                                "auto_precision": 0.908,
                                "official_recall": 0.908,
                                "false_official_rows": 8,
                                "over_rejected_rows": 7,
                            },
                            {
                                "threshold": 80,
                                "overall_accuracy": 0.84,
                                "auto_precision": 0.9167,
                                "official_recall": 0.8851,
                                "false_official_rows": 7,
                                "over_rejected_rows": 9,
                            },
                        ],
                        "details": [],
                    }
                ),
                encoding="utf-8",
            )

            report = build_threshold_boundary_report(labeled_eval_json=labeled)

        self.assertEqual(report["summary"]["recommended_global_accept_threshold"], DEFAULT_SECOND_PASS_ACCEPT_THRESHOLD)
        self.assertEqual(report["summary"]["global_threshold_change"], "keep_current")
        self.assertEqual(report["thresholds"]["current"]["threshold"], DEFAULT_SECOND_PASS_ACCEPT_THRESHOLD)

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
            labels_dir = root / "labels"
            labels_dir.mkdir()
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
            _write_test_csv(
                labels_dir / "manual_review_combined_decisions.csv",
                [
                    {
                        "provider_id": "batch-precision",
                        "manual_decision": "accept",
                        "manual_url": "",
                        "notes": "historically confirmed",
                    }
                ],
            )
            _write_test_csv(
                labels_dir / "agent_b_verification_results.csv",
                [
                    {
                        "provider_id": "batch-recall",
                        "manual_decision": "accept",
                        "manual_url": "",
                        "notes": "automatic source ignored",
                    }
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
                reuse_label_paths=[labels_dir],
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
                "application_gates_json": (output_dir / "calibration_application_gates.json").exists(),
                "application_gates_md": (output_dir / "calibration_application_gates.md").exists(),
                "label_gap_csv": (output_dir / "label_gap_task.csv").exists(),
                "label_gap_xlsx": (output_dir / "label_gap_task.xlsx").exists(),
                "label_gap_high_csv": (output_dir / "label_gap_high_priority_task.csv").exists(),
                "label_gap_high_xlsx": (output_dir / "label_gap_high_priority_task.xlsx").exists(),
                "protected_lane_csv": (output_dir / "protected_lanes_next_review_task.csv").exists(),
                "protected_lane_xlsx": (output_dir / "protected_lanes_next_review_task.xlsx").exists(),
                "protected_lane_json": (output_dir / "protected_lanes_next_review_task_summary.json").exists(),
                "protected_lane_verify_json": (output_dir / "protected_lanes_next_review_task_verification.json").exists(),
                "protected_lane_verify_md": (output_dir / "protected_lanes_next_review_task_verification.md").exists(),
                "protected_lane_prefilled_csv": (output_dir / "protected_lanes_next_review_task_prefilled.csv").exists(),
                "protected_lane_prefilled_xlsx": (output_dir / "protected_lanes_next_review_task_prefilled.xlsx").exists(),
                "protected_lane_reuse_json": (
                    output_dir / "protected_lanes_next_review_task_historical_label_reuse.json"
                ).exists(),
                "protected_lane_prefilled_verify_json": (
                    output_dir / "protected_lanes_next_review_task_prefilled_verification.json"
                ).exists(),
                "protected_lane_priority_csv": (output_dir / "protected_lanes_priority_task.csv").exists(),
                "protected_lane_priority_xlsx": (output_dir / "protected_lanes_priority_task.xlsx").exists(),
                "protected_lane_priority_json": (output_dir / "protected_lanes_priority_task_summary.json").exists(),
                "protected_lane_priority_md": (output_dir / "protected_lanes_priority_task_handoff.md").exists(),
                "protected_lane_priority_verify_json": (
                    output_dir / "protected_lanes_priority_task_verification.json"
                ).exists(),
                "protected_lane_priority_verify_md": (
                    output_dir / "protected_lanes_priority_task_verification.md"
                ).exists(),
                "protected_lane_priority_prefilled_csv": (
                    output_dir / "protected_lanes_priority_task_prefilled.csv"
                ).exists(),
                "protected_lane_priority_prefilled_xlsx": (
                    output_dir / "protected_lanes_priority_task_prefilled.xlsx"
                ).exists(),
                "protected_lane_priority_reuse_json": (
                    output_dir / "protected_lanes_priority_task_historical_label_reuse.json"
                ).exists(),
                "protected_lane_priority_prefilled_verify_json": (
                    output_dir / "protected_lanes_priority_task_prefilled_verification.json"
                ).exists(),
                "convergence_audit_json": (output_dir / "convergence_audit.json").exists(),
                "convergence_audit_md": (output_dir / "convergence_audit.md").exists(),
            }
            with (output_dir / "label_gap_high_priority_task.csv").open(newline="", encoding="utf-8") as f:
                high_gap_rows = list(csv.DictReader(f))
            with (output_dir / "protected_lanes_next_review_task.csv").open(newline="", encoding="utf-8") as f:
                protected_lane_rows = list(csv.DictReader(f))
            with (output_dir / "protected_lanes_priority_task_prefilled.csv").open(newline="", encoding="utf-8") as f:
                protected_priority_prefilled_rows = list(csv.DictReader(f))
            status_data = json.loads((output_dir / "calibration_status.json").read_text(encoding="utf-8"))
            gate_data = json.loads((output_dir / "calibration_application_gates.json").read_text(encoding="utf-8"))
            convergence_data = json.loads((output_dir / "convergence_audit.json").read_text(encoding="utf-8"))
            protected_verify = json.loads(
                (output_dir / "protected_lanes_next_review_task_verification.json").read_text(encoding="utf-8")
            )
            protected_priority_verify = json.loads(
                (output_dir / "protected_lanes_priority_task_verification.json").read_text(encoding="utf-8")
            )
            protected_priority_prefilled_verify = json.loads(
                (output_dir / "protected_lanes_priority_task_prefilled_verification.json").read_text(encoding="utf-8")
            )

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
        self.assertTrue(output_exists["application_gates_json"])
        self.assertTrue(output_exists["application_gates_md"])
        self.assertTrue(output_exists["label_gap_csv"])
        self.assertTrue(output_exists["label_gap_xlsx"])
        self.assertTrue(output_exists["label_gap_high_csv"])
        self.assertTrue(output_exists["label_gap_high_xlsx"])
        self.assertTrue(output_exists["protected_lane_csv"])
        self.assertTrue(output_exists["protected_lane_xlsx"])
        self.assertTrue(output_exists["protected_lane_json"])
        self.assertTrue(output_exists["protected_lane_verify_json"])
        self.assertTrue(output_exists["protected_lane_verify_md"])
        self.assertTrue(output_exists["protected_lane_prefilled_csv"])
        self.assertTrue(output_exists["protected_lane_prefilled_xlsx"])
        self.assertTrue(output_exists["protected_lane_reuse_json"])
        self.assertTrue(output_exists["protected_lane_prefilled_verify_json"])
        self.assertTrue(output_exists["protected_lane_priority_csv"])
        self.assertTrue(output_exists["protected_lane_priority_xlsx"])
        self.assertTrue(output_exists["protected_lane_priority_json"])
        self.assertTrue(output_exists["protected_lane_priority_md"])
        self.assertTrue(output_exists["protected_lane_priority_verify_json"])
        self.assertTrue(output_exists["protected_lane_priority_verify_md"])
        self.assertTrue(output_exists["protected_lane_priority_prefilled_csv"])
        self.assertTrue(output_exists["protected_lane_priority_prefilled_xlsx"])
        self.assertTrue(output_exists["protected_lane_priority_reuse_json"])
        self.assertTrue(output_exists["protected_lane_priority_prefilled_verify_json"])
        self.assertTrue(output_exists["convergence_audit_json"])
        self.assertTrue(output_exists["convergence_audit_md"])
        self.assertEqual(report["summary"]["empty_eval_labeled_rows"], 0)
        self.assertIn("label_gap_task_rows", report["summary"])
        self.assertIn("label_gap_high_priority_task_rows", report["summary"])
        self.assertIn("protected_lanes_next_review_task_rows", report["summary"])
        self.assertIn("protected_lanes_priority_task_rows", report["summary"])
        self.assertIn("convergence_state", report["summary"])
        self.assertIn("threshold_decision", report["summary"])
        self.assertTrue(report["summary"]["protected_lanes_next_review_task_verification_passed"])
        self.assertTrue(report["summary"]["protected_lanes_priority_task_verification_passed"])
        self.assertIn("label_gap_task", report)
        self.assertIn("label_gap_high_priority_task", report)
        self.assertIn("protected_lanes_next_review_task", report)
        self.assertIn("protected_lanes_next_review_task_verification", report)
        self.assertIn("protected_lanes_priority_task", report)
        self.assertIn("protected_lanes_priority_task_verification", report)
        self.assertIn("convergence_audit", report)
        self.assertEqual(report["summary"]["label_gap_high_priority_task_rows"], 0)
        self.assertEqual(report["summary"]["protected_lanes_next_review_task_rows"], 2)
        self.assertEqual(report["summary"]["protected_lanes_priority_task_rows"], 2)
        self.assertEqual({row["provider_id"] for row in protected_lane_rows}, {"batch-recall", "batch-precision"})
        self.assertIn("provider_detail_url", protected_lane_rows[0])
        self.assertTrue(protected_verify["summary"]["passed"])
        self.assertEqual(protected_verify["summary"]["row_count"], 2)
        self.assertTrue(protected_priority_verify["summary"]["passed"])
        self.assertEqual(protected_priority_verify["summary"]["row_count"], 2)
        self.assertTrue(protected_priority_prefilled_verify["summary"]["passed"])
        self.assertEqual(protected_priority_prefilled_verify["summary"]["filled_manual_decision_rows"], 1)
        self.assertEqual(
            {row["provider_id"]: row["historical_label_status"] for row in protected_priority_prefilled_rows},
            {"batch-recall": "unlabeled", "batch-precision": "reused"},
        )
        self.assertEqual(status_data["summary"]["label_gap_task_rows"], report["summary"]["label_gap_task_rows"])
        self.assertEqual(status_data["summary"]["label_gap_high_priority_task_rows"], 0)
        self.assertEqual(status_data["summary"]["protected_lanes_priority_task_rows"], 2)
        self.assertIn("protected_lanes_next_review_task_xlsx", status_data["artifacts"])
        self.assertIn("protected_lanes_priority_task_xlsx", status_data["artifacts"])
        self.assertIn("protected_lanes_priority_task_handoff_md", status_data["artifacts"])
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
        self.assertIn("application_gate_checks", report)
        self.assertEqual(gate_data["summary"]["gate_count"], 3)
        self.assertEqual(report["summary"]["application_gate_not_allowed_count"], 3)
        self.assertEqual(report["summary"]["application_gate_allowed_count"], 0)
        self.assertTrue(report["summary"]["historical_label_reuse_enabled"])
        self.assertEqual(report["summary"]["protected_lanes_priority_task_prefilled_reused_rows"], 1)
        self.assertEqual(report["summary"]["protected_lanes_priority_task_prefilled_unlabeled_rows"], 1)
        self.assertTrue(report["summary"]["protected_lanes_priority_task_prefilled_verification_passed"])
        self.assertEqual(report["inputs"]["reuse_label_paths"], [str(labels_dir)])
        self.assertIn("protected_lanes_priority_task_prefilled_xlsx", report["outputs"])
        self.assertIn("review_lane_change", gate_data["summary"]["not_allowed_gates"])
        self.assertEqual(report["calibration_status"]["summary"]["workflow_status"], "not_converged_needs_human_labels")
        self.assertEqual(report["summary"]["convergence_state"], convergence_data["summary"]["convergence_state"])
        self.assertEqual(report["summary"]["threshold_decision"], convergence_data["summary"]["threshold_decision"])
        self.assertEqual(report["inputs"]["pattern_release_jsons"], [str(pattern_release)])
        self.assertEqual(report["inputs"]["preferred_pattern_release_json"], str(pattern_release))
        self.assertEqual(report["inputs"]["policy_report_json"], str(policy_report))
        self.assertEqual(report["inputs"]["batch_total_rows"], "2")

    def test_run_calibration_followup_reuses_cycle_inputs_with_filled_sample(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            balance_json = root / "balance.json"
            labeled_agent_b = root / "labeled_agent_b.csv"
            review = root / "review.csv"
            batch_agent_b = root / "batch_agent_b.csv"
            output_dir = root / "calibration"
            filled_sample = root / "filled_label_gap.csv"
            filled_policy_validation = root / "filled_policy_validation.csv"
            followup_dir = root / "followup"
            balance_json.write_text(
                json.dumps(
                    {
                        "overall": {
                            "labeled_rows": 1,
                            "auto_precision": 1.0,
                            "official_recall": 1.0,
                            "false_official_rows": 0,
                            "over_rejected_rows": 0,
                        },
                        "threshold_simulations": [
                            {
                                "threshold": 75,
                                "overall_accuracy": 1.0,
                                "auto_precision": 1.0,
                                "official_recall": 1.0,
                                "false_official_rows": 0,
                                "over_rejected_rows": 0,
                            }
                        ],
                        "details": [
                            {
                                "provider_id": "p-precision",
                                "provider_name": "Precision Lane",
                                "expected_kind": "official",
                                "expected_domain": "precision.example",
                                "outcome": "correct_official",
                                "manual_review_reason": "precision_second_pass_accepted_lt70",
                            }
                        ],
                        "manual_review_lanes": [
                            {
                                "review_reason": "precision_second_pass_accepted_lt70",
                                "review_task_rows": 1,
                                "labeled_rows": 1,
                                "false_official_rows": 0,
                                "over_rejected_rows": 0,
                                "correct_official_rows": 1,
                                "correct_no_official_rows": 0,
                                "risk_rows": 0,
                            }
                        ],
                        "manual_review_lane_drop_simulations": [
                            {
                                "drop_review_reason": "precision_second_pass_accepted_lt70",
                                "manual_review_rows_removed": 1,
                                "known_false_official_missed_if_dropped": 0,
                                "known_over_rejected_missed_if_dropped": 0,
                                "known_correct_reviews_removed_if_dropped": 1,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            _write_test_csv(
                labeled_agent_b,
                [
                    {
                        "provider_id": "p-precision",
                        "provider_name": "Precision Lane",
                        "candidate_domain": "precision.example",
                        "candidate_url": "https://precision.example",
                        "agent_b_decision": "accept",
                        "confidence": "69",
                        "evidence_score": "69",
                        "supporting_facts": "candidate_pages_fetch_ok; page_contains_exact_provider_name",
                        "counter_evidence": "",
                        "reason_for_unsure": "",
                    }
                ],
            )
            _write_test_csv(
                review,
                [
                    {
                        "provider_id": "p-precision",
                        "provider_name": "Precision Lane",
                        "provider_detail_url": "https://amazon.example/p-precision",
                        "official_url": "https://precision.example",
                        "official_domain": "precision.example",
                        "top_candidate_url": "",
                        "top_candidate_domain": "",
                        "review_reason": "precision_second_pass_accepted_lt70",
                    }
                ],
            )
            _write_test_csv(
                batch_agent_b,
                [
                    {
                        "provider_id": "p-precision",
                        "provider_name": "Precision Lane",
                        "candidate_domain": "precision.example",
                        "candidate_url": "https://precision.example",
                        "agent_b_decision": "accept",
                        "confidence": "69",
                        "evidence_score": "69",
                        "supporting_facts": "candidate_pages_fetch_ok; page_contains_exact_provider_name",
                        "counter_evidence": "",
                        "reason_for_unsure": "",
                    }
                ],
            )

            run_calibration_cycle(
                labeled_eval_json=balance_json,
                labeled_agent_b_csv=labeled_agent_b,
                review_csv=review,
                batch_agent_b_csv=batch_agent_b,
                batch_total_rows=1,
                output_dir=output_dir,
                max_rows=1,
            )
            blank_priority_sample = output_dir / "protected_lanes_priority_task.csv"
            with self.assertRaises(ValueError):
                run_calibration_followup(
                    previous_summary_json=output_dir / "calibration_cycle_summary.json",
                    filled_sample=blank_priority_sample,
                )
            failed_verification = json.loads(
                (output_dir / "filled_protected_sample_verification.json").read_text(encoding="utf-8")
            )
            self.assertFalse(failed_verification["summary"]["passed"])
            self.assertGreater(failed_verification["summary"]["failure_count"], 0)

            with blank_priority_sample.open(newline="", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
            for row in rows:
                row["manual_decision"] = "accept"
                row["notes"] = "confirmed official site"
            _write_test_csv(filled_sample, rows)
            _write_test_csv(
                filled_policy_validation,
                [
                    {
                        "provider_id": "p-policy",
                        "provider_name": "Policy Candidate",
                        "provider_detail_url": "https://amazon.example/p-policy",
                        "candidate_policy_action": "release",
                        "candidate_policy_pattern": "review_reason:recall_unresolved_top_candidate AND has:page_contains_exact_provider_name",
                        "candidate_policy_source": "release_pattern",
                        "candidate_url": "https://policy.example",
                        "review_reason": "recall_unresolved_top_candidate",
                        "agent_b_decision": "unsure",
                        "known_label_status": "unlabeled",
                        "manual_decision": "accept",
                        "manual_url": "",
                        "notes": "confirmed official site",
                    }
                ],
            )
            summary_with_blank_previous = root / "calibration_summary_with_blank_previous.json"
            summary_payload = json.loads((output_dir / "calibration_cycle_summary.json").read_text(encoding="utf-8"))
            summary_payload["inputs"]["filled_samples"] = [str(blank_priority_sample)]
            summary_with_blank_previous.write_text(json.dumps(summary_payload), encoding="utf-8")
            policy_only_followup_dir = root / "policy_only_followup"
            policy_only_decision = run_calibration_followup(
                previous_summary_json=summary_with_blank_previous,
                filled_policy_validation=filled_policy_validation,
                output_dir=policy_only_followup_dir,
            )

            decision = run_calibration_followup(
                previous_summary_json=output_dir / "calibration_cycle_summary.json",
                filled_sample=filled_sample,
                filled_policy_validation=filled_policy_validation,
                output_dir=followup_dir,
            )
            decision_json_exists = (followup_dir / "calibration_followup_decision.json").exists()
            decision_md_exists = (followup_dir / "calibration_followup_decision.md").exists()
            decision_md = (followup_dir / "calibration_followup_decision.md").read_text(encoding="utf-8")
            verification_json_exists = (followup_dir / "filled_protected_sample_verification.json").exists()
            verification_md_exists = (followup_dir / "filled_protected_sample_verification.md").exists()
            verification = json.loads((followup_dir / "filled_protected_sample_verification.json").read_text(encoding="utf-8"))
            policy_eval_exists = (followup_dir / "filled_policy_validation_evaluation.json").exists()
            policy_eval_md_exists = (followup_dir / "filled_policy_validation_evaluation.md").exists()
            policy_eval = json.loads((followup_dir / "filled_policy_validation_evaluation.json").read_text(encoding="utf-8"))

        self.assertTrue(decision_json_exists)
        self.assertTrue(decision_md_exists)
        self.assertTrue(verification_json_exists)
        self.assertTrue(verification_md_exists)
        self.assertTrue(policy_eval_exists)
        self.assertTrue(policy_eval_md_exists)
        self.assertEqual(policy_only_decision["inputs"]["filled_samples"], [])
        self.assertEqual(policy_only_decision["summary"]["filled_protected_sample_verification_count"], 0)
        self.assertEqual(policy_only_decision["summary"]["filled_policy_validation_support_rows"], 1)
        self.assertTrue(verification["summary"]["passed"])
        self.assertEqual(decision["inputs"]["filled_samples"], [str(filled_sample)])
        self.assertEqual(decision["inputs"]["filled_policy_validations"], [str(filled_policy_validation)])
        self.assertEqual(decision["summary"]["filled_labeled_rows"], len(rows))
        self.assertEqual(decision["summary"]["filled_protected_sample_verification_count"], 1)
        self.assertTrue(decision["summary"]["filled_protected_sample_verification_passed"])
        self.assertEqual(decision["summary"]["filled_policy_validation_file_count"], 1)
        self.assertEqual(decision["summary"]["filled_policy_validation_support_rows"], 1)
        self.assertEqual(decision["summary"]["filled_policy_validation_blocking_rows"], 0)
        self.assertEqual(decision["summary"]["filled_policy_needs_more_labels_count"], 1)
        self.assertEqual(decision["summary"]["policy_validation_decision"], "needs_more_labels")
        self.assertEqual(decision["summary"]["policy_validation_gate_status"], "blocked")
        self.assertIn("targeted policy-validation", decision["summary"]["policy_validation_required_action"])
        self.assertEqual(policy_eval["summary"]["support_rows"], 1)
        self.assertIn("filled_lane_candidate_for_change_count", decision["summary"])
        self.assertIn("filled_lane_keep_review_count", decision["summary"])
        self.assertIn("filled_rule_candidate_count", decision["summary"])
        self.assertIn("filled_rejected_pattern_count", decision["summary"])
        self.assertIn("workflow_status", decision["summary"])
        self.assertIn("convergence_state", decision["summary"])
        self.assertIn("threshold_decision", decision["summary"])
        self.assertIn("review_lane_decision", decision["summary"])
        self.assertIn("pattern_release_decision", decision["summary"])
        self.assertIn("current_threshold_ties_best_accuracy", decision["summary"])
        self.assertIn("protected_lanes_next_review_task_rows", decision["summary"])
        self.assertIn("protected_lanes_priority_task_rows", decision["summary"])
        self.assertIn("blocked_gates", decision["summary"])
        self.assertIn("blocked_gate_count", decision["summary"])
        self.assertIn("convergence_audit", decision)
        self.assertIn("application_gate_checks", decision)
        self.assertIn("convergence_audit_json", decision["outputs"])
        self.assertIn("protected_lanes_next_review_task_xlsx", decision["outputs"])
        self.assertIn("protected_lanes_priority_task_xlsx", decision["outputs"])
        self.assertIn("protected_lanes_priority_task_handoff_md", decision["outputs"])
        self.assertIn("protected_lanes_next_review_task_verification_json", decision["outputs"])
        self.assertIn("protected_lanes_priority_task_verification_json", decision["outputs"])
        self.assertIn("protected_lanes_next_review_task_verification_md", decision["outputs"])
        self.assertIn("protected_lanes_priority_task_verification_md", decision["outputs"])
        self.assertIn("filled_protected_sample_verification_json", decision["outputs"])
        self.assertIn("filled_protected_sample_verification_md", decision["outputs"])
        self.assertIn("policy_validation_eval_json", decision["outputs"])
        self.assertIn("policy_validation_eval_md", decision["outputs"])
        self.assertIn("filled_eval_json", decision["outputs"])
        self.assertIn("pattern_rule_candidates_json", decision["outputs"])
        self.assertEqual(len(decision["filled_protected_sample_verifications"]), 1)
        self.assertIn("filled_lane_recommendations", decision)
        self.assertIn("filled_pattern_rule_candidates", decision)
        self.assertIn("filled_policy_validation_evaluations", decision)
        self.assertIn("filled_policy_rule_candidates", decision)
        self.assertEqual(len(decision["filled_policy_validation_evaluations"]), 1)
        self.assertGreaterEqual(len(decision["filled_lane_recommendations"]), 1)
        if decision["summary"].get("protected_lanes_priority_task_rows"):
            self.assertIn("protected_lanes_priority_task.xlsx", decision["summary"]["next_action"])
        self.assertIn("Filled Lane Recommendations", decision_md)
        self.assertIn("Filled Pattern Rule Candidates", decision_md)
        self.assertIn("Filled Policy Validation", decision_md)
        self.assertIn("Filled Policy Rule Candidates", decision_md)
        self.assertIn("Policy validation decision", decision_md)
        self.assertIn("Threshold decision", decision_md)
        self.assertIn("Next Actions", decision_md)

    def test_run_calibration_cycle_can_evaluate_filled_pattern_sample(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            balance_json = root / "balance.json"
            labeled_agent_b = root / "labeled_agent_b.csv"
            review = root / "review.csv"
            batch_agent_b = root / "batch_agent_b.csv"
            filled_sample = root / "filled_sample.csv"
            candidate_final = root / "official_sites.csv"
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
            _write_test_csv(
                candidate_final,
                [
                    {
                        "provider_id": "filled-good",
                        "provider_name": "Filled Good",
                        "official_url": "https://filledgood.example/",
                        "status": "matched",
                    },
                    {
                        "provider_id": "filled-bad",
                        "provider_name": "Filled Bad",
                        "official_url": "",
                        "status": "unresolved",
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
                candidate_final_csv=candidate_final,
            )
            filled_eval_exists = (output_dir / "pattern_validation_sample_50_eval_filled.json").exists()
            rule_candidates_json_exists = (output_dir / "pattern_rule_candidates.json").exists()
            regression_cases_json_exists = (output_dir / "calibration_regression_cases.json").exists()
            regression_cases_csv_exists = (output_dir / "calibration_regression_cases.csv").exists()
            regression_gate_json_exists = (output_dir / "calibration_regression_gate.json").exists()
            regression_overlay_csv_exists = (output_dir / "calibration_regression_overlay_official_sites.csv").exists()
            regression_overlay_xlsx_exists = (output_dir / "calibration_regression_overlay_official_sites.xlsx").exists()
            regression_overlay_json_exists = (output_dir / "calibration_regression_overlay_application.json").exists()
            regression_overlay_gate_json_exists = (output_dir / "calibration_regression_overlay_gate.json").exists()
            regression_overlay_balance_json_exists = (output_dir / "calibration_regression_overlay_balance.json").exists()
            regression_overlay_balance_csv_exists = (
                output_dir / "calibration_regression_overlay_balance_details.csv"
            ).exists()
            rule_candidates_md = output_dir / "pattern_rule_candidates.md"
            rule_candidates_md_text = rule_candidates_md.read_text(encoding="utf-8")
            summary_text = (output_dir / "calibration_cycle_summary.md").read_text(encoding="utf-8")

        self.assertTrue(filled_eval_exists)
        self.assertTrue(rule_candidates_json_exists)
        self.assertTrue(regression_cases_json_exists)
        self.assertTrue(regression_cases_csv_exists)
        self.assertTrue(regression_gate_json_exists)
        self.assertTrue(regression_overlay_csv_exists)
        self.assertTrue(regression_overlay_xlsx_exists)
        self.assertTrue(regression_overlay_json_exists)
        self.assertTrue(regression_overlay_gate_json_exists)
        self.assertTrue(regression_overlay_balance_json_exists)
        self.assertTrue(regression_overlay_balance_csv_exists)
        self.assertEqual(report["summary"]["filled_eval_labeled_rows"], 2)
        self.assertEqual(report["summary"]["filled_pattern_recommendation_counts"]["reject_pattern"], 1)
        self.assertEqual(report["summary"]["filled_lane_recommendation_counts"]["keep_review_lane"], 1)
        self.assertEqual(report["summary"]["filled_lane_keep_review_count"], 1)
        self.assertEqual(report["summary"]["filled_rejected_pattern_count"], 1)
        self.assertEqual(report["summary"]["filled_regression_case_rows"], 2)
        self.assertEqual(report["summary"]["filled_recall_blocking_fixture_rows"], 1)
        self.assertEqual(report["summary"]["filled_positive_fixture_rows"], 1)
        self.assertEqual(report["summary"]["regression_gate_status"], "pass")
        self.assertEqual(report["summary"]["regression_gate_fail_rows"], 0)
        self.assertEqual(report["summary"]["regression_gate_unverified_rows"], 0)
        self.assertEqual(report["summary"]["regression_overlay_changed_rows"], 0)
        self.assertEqual(report["summary"]["regression_overlay_gate_status"], "pass")
        self.assertEqual(report["summary"]["regression_overlay_gate_fail_rows"], 0)
        self.assertIn("regression_overlay_balance_accuracy", report["summary"])
        self.assertIn("regression_overlay_balance_json", report["outputs"])
        self.assertIn("regression_overlay_xlsx", report["outputs"])
        self.assertIn("regression_overlay", report)
        self.assertIn("regression_overlay_balance", report)
        self.assertIn("delivery_recommendation", report)
        self.assertEqual(report["summary"]["delivery_decision"], "use_candidate_final")
        self.assertFalse(report["summary"]["delivery_is_rule_release"])
        self.assertIn("run_calibration_regression_gate.py", report["summary"]["regression_gate_next_step"])
        self.assertIn("Rejected Pattern", rule_candidates_md_text)
        self.assertIn("Filled Lane Recommendations", summary_text)
        self.assertIn("Filled Pattern Recommendations", summary_text)
        self.assertIn("Filled Candidate Rule Export", summary_text)
        self.assertIn("Filled Regression Cases", summary_text)
        self.assertIn("Regression gate next step", summary_text)
        self.assertIn("Regression Gate", summary_text)
        self.assertIn("Regression Overlay", summary_text)
        self.assertIn("Regression Overlay Balance", summary_text)
        self.assertIn("Delivery Recommendation", summary_text)
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
        self.assertIn("Keep Check and Suggestion risky accepts in manual review", md_text)
        self.assertIn("Review Lane Guidance", md_text)
        self.assertIn("Pattern Validation", md_text)
        self.assertIn("Candidate Rule Export", md_text)
        self.assertTrue(out_json_exists)
        self.assertEqual(detail_rows[1]["normalized_manual_url"], "https://real-recall.example")

    def test_build_calibration_regression_cases_exports_decisive_fixtures(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sample = root / "calibration.csv"
            eval_json = root / "calibration.json"
            cases_csv = root / "cases.csv"
            cases_json = root / "cases.json"
            cases_md = root / "cases.md"
            _write_test_csv(
                sample,
                [
                    {
                        "provider_id": "precision-good",
                        "provider_name": "Precision Good",
                        "sample_reason": "low_confidence_label",
                        "pattern_scope": "precision",
                        "pattern_match": "has:page_contains_exact_provider_name",
                        "review_reason": "precision_low_confidence_auto_match",
                        "agent_b_decision": "accept",
                        "official_url": "https://good.example",
                        "candidate_url": "https://good.example",
                        "manual_decision": "accept",
                        "manual_url": "",
                    },
                    {
                        "provider_id": "precision-bad",
                        "provider_name": "Precision Bad",
                        "sample_reason": "low_confidence_label",
                        "pattern_scope": "precision",
                        "pattern_match": "has:page_contains_exact_provider_name",
                        "review_reason": "precision_low_confidence_auto_match",
                        "agent_b_decision": "accept",
                        "official_url": "https://bad.example",
                        "candidate_url": "https://bad.example",
                        "manual_decision": "replace",
                        "manual_url": "https://real.example",
                    },
                    {
                        "provider_id": "recall-bad",
                        "provider_name": "Recall Bad",
                        "sample_reason": "pattern_candidate_validation",
                        "pattern_scope": "recall",
                        "pattern_match": "agent_b_score<45",
                        "review_reason": "recall_unresolved_top_candidate",
                        "agent_b_decision": "unsure",
                        "official_url": "",
                        "candidate_url": "https://recallbad.example",
                        "manual_decision": "reject",
                        "manual_url": "",
                    },
                    {
                        "provider_id": "skip-unsure",
                        "provider_name": "Skip Unsure",
                        "sample_reason": "pattern_candidate_validation",
                        "pattern_scope": "recall",
                        "pattern_match": "agent_b_score<45",
                        "review_reason": "recall_unresolved_top_candidate",
                        "agent_b_decision": "unsure",
                        "official_url": "",
                        "candidate_url": "https://skip.example",
                        "manual_decision": "unsure",
                        "manual_url": "",
                    },
                ],
            )
            evaluate_calibration_review_sample(sample=sample, output_json=eval_json)

            report = build_calibration_regression_cases(
                sample_eval_json=eval_json,
                output_csv=cases_csv,
                output_json=cases_json,
                output_md=cases_md,
            )
            with cases_csv.open(newline="", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
            md_text = cases_md.read_text(encoding="utf-8")

        self.assertEqual(report["summary"]["case_rows"], 3)
        self.assertEqual(report["summary"]["precision_blocking_fixture_rows"], 1)
        self.assertEqual(report["summary"]["recall_blocking_fixture_rows"], 1)
        self.assertEqual(report["summary"]["positive_fixture_rows"], 1)
        self.assertEqual({row["case_type"] for row in rows}, {"precision_positive_fixture", "precision_blocking_fixture", "recall_blocking_fixture"})
        by_id = {row["provider_id"]: row for row in rows}
        self.assertEqual(by_id["precision-bad"]["expected_url"], "https://real.example")
        self.assertEqual(by_id["precision-bad"]["assertion"], "candidate_url_or_official_url_must_not_auto_accept")
        self.assertIn("Precision blocking fixtures: 1", md_text)

    def test_run_calibration_regression_gate_flags_blocked_and_over_rejected_cases(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cases = root / "cases.csv"
            candidate = root / "official_sites.csv"
            out_csv = root / "gate.csv"
            out_json = root / "gate.json"
            out_md = root / "gate.md"
            _write_test_csv(
                cases,
                [
                    {
                        "case_type": "precision_blocking_fixture",
                        "provider_id": "bad-accepted",
                        "provider_name": "Bad Accepted",
                        "review_reason": "precision_low_confidence_auto_match",
                        "candidate_url": "https://wrong.example",
                        "official_url": "https://wrong.example",
                        "expected_url": "https://right.example",
                        "assertion": "candidate_url_or_official_url_must_not_auto_accept",
                    },
                    {
                        "case_type": "precision_blocking_fixture",
                        "provider_id": "bad-corrected",
                        "provider_name": "Bad Corrected",
                        "review_reason": "precision_low_confidence_auto_match",
                        "candidate_url": "https://wrong2.example",
                        "official_url": "https://wrong2.example",
                        "expected_url": "https://right2.example",
                        "assertion": "candidate_url_or_official_url_must_not_auto_accept",
                    },
                    {
                        "case_type": "precision_positive_fixture",
                        "provider_id": "good-overrejected",
                        "provider_name": "Good Overrejected",
                        "review_reason": "precision_second_pass_accepted_lt70",
                        "candidate_url": "https://good.example",
                        "official_url": "https://good.example",
                        "expected_url": "https://good.example",
                        "assertion": "candidate_should_remain_accepted_for_same_evidence_lane",
                    },
                    {
                        "case_type": "recall_positive_fixture",
                        "provider_id": "good-www",
                        "provider_name": "Good WWW",
                        "review_reason": "recall_unresolved_top_candidate",
                        "candidate_url": "https://example.com",
                        "official_url": "",
                        "expected_url": "https://example.com",
                        "assertion": "candidate_can_seed_recall_pattern_only_with_same_evidence",
                    },
                ],
            )
            _write_test_csv(
                candidate,
                [
                    {
                        "provider_id": "bad-accepted",
                        "provider_name": "Bad Accepted",
                        "official_url": "https://wrong.example/",
                        "status": "matched",
                    },
                    {
                        "provider_id": "bad-corrected",
                        "provider_name": "Bad Corrected",
                        "official_url": "https://right2.example/",
                        "status": "matched",
                    },
                    {
                        "provider_id": "good-overrejected",
                        "provider_name": "Good Overrejected",
                        "official_url": "",
                        "status": "unresolved",
                    },
                    {
                        "provider_id": "good-www",
                        "provider_name": "Good WWW",
                        "official_url": "https://www.example.com/",
                        "status": "matched",
                    },
                ],
            )

            report = run_calibration_regression_gate(
                cases_csv=cases,
                candidate_final_csv=candidate,
                output_csv=out_csv,
                output_json=out_json,
                output_md=out_md,
            )
            with out_csv.open(newline="", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
            md_text = out_md.read_text(encoding="utf-8")

        self.assertEqual(report["summary"]["gate_status"], "fail")
        self.assertEqual(report["summary"]["case_rows"], 4)
        self.assertEqual(report["summary"]["pass_rows"], 2)
        self.assertEqual(report["summary"]["fail_rows"], 2)
        by_id = {row["provider_id"]: row for row in rows}
        self.assertEqual(by_id["bad-accepted"]["failure_reason"], "blocked_candidate_was_auto_accepted")
        self.assertEqual(by_id["bad-corrected"]["gate_result"], "pass")
        self.assertEqual(by_id["good-overrejected"]["failure_reason"], "positive_fixture_over_rejected")
        self.assertEqual(by_id["good-www"]["gate_result"], "pass")
        self.assertIn("Gate status: fail", md_text)

    def test_apply_calibration_regression_cases_fixes_exact_human_fixtures(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cases = root / "cases.csv"
            candidate = root / "official_sites.csv"
            output_csv = root / "official_sites_overlay.csv"
            output_xlsx = root / "official_sites_overlay.xlsx"
            output_json = root / "overlay.json"
            output_md = root / "overlay.md"
            gate_json = root / "gate.json"
            gate_md = root / "gate.md"
            gate_csv = root / "gate.csv"
            _write_test_csv(
                cases,
                [
                    {
                        "case_type": "precision_blocking_fixture",
                        "provider_id": "bad-reject",
                        "provider_name": "Bad Reject",
                        "review_reason": "precision_generic_identity_term_risk",
                        "candidate_url": "https://wrong.example",
                        "official_url": "https://wrong.example",
                        "expected_url": "",
                        "assertion": "candidate_url_or_official_url_must_not_auto_accept",
                    },
                    {
                        "case_type": "precision_blocking_fixture",
                        "provider_id": "bad-replace",
                        "provider_name": "Bad Replace",
                        "review_reason": "precision_low_confidence_auto_match",
                        "candidate_url": "https://wrong2.example",
                        "official_url": "https://wrong2.example",
                        "expected_url": "https://right2.example",
                        "assertion": "candidate_url_or_official_url_must_not_auto_accept",
                    },
                    {
                        "case_type": "recall_positive_fixture",
                        "provider_id": "good-recall",
                        "provider_name": "Good Recall",
                        "review_reason": "recall_unresolved_top_candidate",
                        "candidate_url": "https://good.example",
                        "official_url": "https://good.example",
                        "expected_url": "https://good.example",
                        "assertion": "candidate_can_seed_recall_pattern_only_with_same_evidence",
                    },
                ],
            )
            _write_test_csv(
                candidate,
                [
                    {
                        "provider_id": "bad-reject",
                        "provider_name": "Bad Reject",
                        "provider_detail_url": "https://amazon.example/bad-reject",
                        "listing_logo_url": "",
                        "official_url": "https://wrong.example/",
                        "official_domain": "wrong.example",
                        "status": "matched",
                        "decision_source": "auto_matched",
                        "confidence": "100",
                        "source_status": "matched",
                        "evidence_summary": "",
                        "candidate_count": "1",
                        "scored_candidate_count": "1",
                        "service_apis": "",
                        "provider_locations": "",
                        "notes": "",
                    },
                    {
                        "provider_id": "bad-replace",
                        "provider_name": "Bad Replace",
                        "provider_detail_url": "https://amazon.example/bad-replace",
                        "listing_logo_url": "",
                        "official_url": "https://wrong2.example/",
                        "official_domain": "wrong2.example",
                        "status": "matched",
                        "decision_source": "auto_matched",
                        "confidence": "82",
                        "source_status": "matched",
                        "evidence_summary": "",
                        "candidate_count": "1",
                        "scored_candidate_count": "1",
                        "service_apis": "",
                        "provider_locations": "",
                        "notes": "",
                    },
                    {
                        "provider_id": "good-recall",
                        "provider_name": "Good Recall",
                        "provider_detail_url": "https://amazon.example/good-recall",
                        "listing_logo_url": "",
                        "official_url": "",
                        "official_domain": "",
                        "status": "unresolved",
                        "decision_source": "pending_review",
                        "confidence": "68",
                        "source_status": "unresolved",
                        "evidence_summary": "",
                        "candidate_count": "1",
                        "scored_candidate_count": "1",
                        "service_apis": "",
                        "provider_locations": "",
                        "notes": "",
                    },
                ],
            )

            report = apply_calibration_regression_cases(
                cases_csv=cases,
                candidate_final_csv=candidate,
                output_csv=output_csv,
                output_xlsx=output_xlsx,
                output_json=output_json,
                output_md=output_md,
                gate_json=gate_json,
                gate_md=gate_md,
                gate_csv=gate_csv,
            )
            with output_csv.open(newline="", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
            md_text = output_md.read_text(encoding="utf-8")
            saved = json.loads(output_json.read_text(encoding="utf-8"))
            output_xlsx_exists = output_xlsx.exists()

        by_id = {row["provider_id"]: row for row in rows}
        self.assertEqual(report["summary"]["changed_rows"], 3)
        self.assertEqual(report["summary"]["regression_gate_status"], "pass")
        self.assertEqual(saved["summary"]["regression_gate_fail_rows"], 0)
        self.assertEqual(by_id["bad-reject"]["official_url"], "")
        self.assertEqual(by_id["bad-reject"]["decision_source"], "calibration_regression_block")
        self.assertEqual(by_id["bad-replace"]["official_domain"], "right2.example")
        self.assertEqual(by_id["bad-replace"]["decision_source"], "calibration_regression_replace")
        self.assertEqual(by_id["good-recall"]["official_domain"], "good.example")
        self.assertEqual(by_id["good-recall"]["decision_source"], "calibration_regression_positive")
        self.assertTrue(output_xlsx_exists)
        self.assertIn("Regression gate: pass", md_text)

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

    def test_evaluate_calibration_review_sample_reports_fill_quality_issues(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sample = root / "bad_fill.csv"
            out_csv = root / "details.csv"
            _write_test_csv(
                sample,
                [
                    {
                        "provider_id": "bad-decision",
                        "provider_name": "Bad Decision",
                        "review_reason": "precision_low_confidence_auto_match",
                        "official_url": "https://bad.example",
                        "candidate_url": "https://bad.example",
                        "manual_decision": "maybe",
                        "manual_url": "",
                    },
                    {
                        "provider_id": "missing-url",
                        "provider_name": "Missing Url",
                        "review_reason": "recall_unresolved_top_candidate",
                        "official_url": "",
                        "candidate_url": "https://candidate.example",
                        "manual_decision": "replace",
                        "manual_url": "",
                    },
                ],
            )

            report = evaluate_calibration_review_sample(sample=sample, output_csv=out_csv)
            with out_csv.open(newline="", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))

        self.assertEqual(report["summary"]["invalid_manual_decision_rows"], 1)
        self.assertEqual(report["summary"]["replace_missing_manual_url_rows"], 1)
        self.assertEqual(report["summary"]["decision_quality_issue_rows"], 2)
        self.assertEqual(report["summary"]["labeled_rows"], 0)
        self.assertEqual(report["summary"]["decisive_rows"], 0)
        self.assertEqual(report["summary"]["recall_useful_rows"], 0)
        self.assertIn("Fix calibration fill-quality issues", report["recommendations"][0])
        self.assertEqual(rows[0]["decision_quality_issue"], "invalid_manual_decision")
        self.assertEqual(rows[0]["calibration_outcome"], "fill_quality_issue")
        self.assertEqual(rows[1]["decision_quality_issue"], "replace_missing_manual_url")
        self.assertEqual(rows[1]["calibration_outcome"], "fill_quality_issue")

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
        self.assertEqual(candidates[0]["evidence_strength"], "minimum_support")
        self.assertEqual(candidates[0]["support_rate_wilson_lower_80"], 0.7527)
        self.assertEqual(candidates[0]["blocking_rate_wilson_upper_80"], 0.2473)
        self.assertIn("narrow recall recovery rule", candidates[0]["required_action"])
        self.assertIn("minimum support", candidates[0]["required_action"])

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
        self.assertEqual(report["lane_recommendations"][0]["support_rows"], 5)
        self.assertEqual(report["lane_recommendations"][0]["blocking_rows"], 0)
        self.assertEqual(report["lane_recommendations"][0]["support_rate"], 1.0)
        self.assertEqual(report["lane_recommendations"][0]["support_rate_wilson_lower_80"], 0.7527)
        self.assertEqual(report["lane_recommendations"][0]["blocking_rate_wilson_upper_80"], 0.2473)
        self.assertEqual(report["lane_recommendations"][0]["evidence_strength"], "minimum_support")
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
                            "label_gap_task_rows": 7,
                            "label_gap_high_priority_task_rows": 5,
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
        self.assertEqual(report["summary"]["label_gap_task_rows"], 7)
        self.assertEqual(report["summary"]["label_gap_high_priority_task_rows"], 5)
        self.assertEqual(report["summary"]["regression_gate_status"], "not_needed")
        self.assertEqual(report["application_gates"]["global_threshold_change"]["status"], "not_recommended")
        self.assertFalse(report["application_gates"]["global_threshold_change"]["can_apply_now"])
        self.assertEqual(report["application_gates"]["review_lane_change"]["status"], "blocked")
        self.assertIn("fill_calibration_sample", report["application_gates"]["review_lane_change"]["blockers"])
        by_reason = {item["review_reason"]: item for item in report["label_targets"]}
        self.assertEqual(by_reason["precision_calibrated_pattern_release"]["target_decisive_rows"], 3)
        self.assertEqual(by_reason["precision_low_confidence_auto_match"]["target_decisive_rows"], 3)
        self.assertIn(str(label_gap_high_xlsx), report["next_actions"][0])
        self.assertIn(str(sample_xlsx), md_text)
        self.assertIn(str(label_gap_high_xlsx), md_text)
        self.assertIn("precision_second_pass_accepted_lt70", md_text)
        self.assertIn("prior/pattern_release.json", md_text)
        self.assertIn("supplied_prior", md_text)
        self.assertIn("Label-gap task rows: 7 total, 5 high priority", md_text)
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

    def test_historical_pattern_release_becomes_guarded_after_current_spot_checks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cycle = root / "cycle.json"
            balance = root / "balance.json"
            sample_eval = root / "sample_eval.json"
            sample_csv = root / "sample.csv"
            cycle.write_text(
                json.dumps(
                    {
                        "summary": {
                            "recommended_global_accept_threshold": 75,
                            "recommended_second_pass_threshold": 75,
                            "filled_eval_labeled_rows": 4,
                            "filled_eval_decisive_rows": 4,
                            "recommended_pattern_release": "narrow_pattern_release_candidate",
                            "recommended_pattern_release_source_kind": "supplied_prior",
                            "recommended_pattern_release_source_path": "prior/pattern_release.json",
                            "pattern_release_correct_rows": 4,
                            "pattern_release_wrong_rows": 0,
                        },
                        "outputs": {"sample_csv": str(sample_csv)},
                    }
                ),
                encoding="utf-8",
            )
            balance.write_text(
                json.dumps(
                    {
                        "summary": {
                            "recommended_pattern_release": "narrow_pattern_release_candidate",
                            "pattern_release_source_kind": "supplied_prior",
                            "pattern_release_source_path": "prior/pattern_release.json",
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
                        "summary": {"labeled_rows": 4, "decisive_rows": 4},
                        "by_review_reason": {
                            "precision_calibrated_pattern_release": {
                                "rows": 4,
                                "labeled_rows": 4,
                                "decisive_rows": 4,
                            }
                        },
                        "lane_recommendations": [
                            {
                                "review_reason": "precision_calibrated_pattern_release",
                                "recommendation": "needs_more_labels",
                                "support_rows": 4,
                                "blocking_rows": 0,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            _write_test_csv(
                sample_csv,
                [
                    {"provider_id": "p-1", "review_reason": "precision_calibrated_pattern_release"},
                    {"provider_id": "p-2", "review_reason": "precision_calibrated_pattern_release"},
                    {"provider_id": "p-3", "review_reason": "precision_calibrated_pattern_release"},
                    {"provider_id": "p-4", "review_reason": "precision_calibrated_pattern_release"},
                ],
            )

            report = build_calibration_status_report(
                calibration_cycle_json=cycle,
                balance_report_json=balance,
                sample_eval_json=sample_eval,
            )

        self.assertEqual(report["summary"]["pattern_release_status"], "current_guarded_candidate")
        self.assertNotIn("validate_historical_pattern_release", {item["id"] for item in report["open_requirements"]})
        self.assertIn("guarded_pattern_release", {item["id"] for item in report["open_requirements"]})

    def test_build_calibration_status_report_blocks_on_regression_gate_failures(self):
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
                            "filled_regression_case_rows": 3,
                            "regression_gate_status": "fail",
                            "regression_gate_fail_rows": 1,
                            "regression_gate_unverified_rows": 1,
                            "regression_overlay_changed_rows": 2,
                            "regression_overlay_gate_status": "pass",
                            "regression_overlay_balance_accuracy": 0.9,
                            "regression_overlay_balance_auto_precision": 0.95,
                            "regression_overlay_balance_official_recall": 0.92,
                            "regression_overlay_balance_accuracy_delta": 0.05,
                            "regression_overlay_balance_precision_delta": 0.03,
                            "regression_overlay_balance_recall_delta": 0.01,
                        },
                        "outputs": {
                            "regression_cases_csv": "calibration_regression_cases.csv",
                            "regression_gate_md": "calibration_regression_gate.md",
                            "regression_overlay_csv": "calibration_regression_overlay_official_sites.csv",
                            "regression_overlay_xlsx": "calibration_regression_overlay_official_sites.xlsx",
                            "regression_overlay_balance_json": "calibration_regression_overlay_balance.json",
                        },
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

            report = build_calibration_status_report(calibration_cycle_json=cycle, sample_eval_json=sample_eval)

        self.assertEqual(report["summary"]["workflow_status"], "not_converged_regression_gate_failed")
        self.assertEqual(report["summary"]["regression_gate_status"], "failed")
        self.assertEqual(report["summary"]["regression_gate_fail_rows"], 1)
        self.assertEqual(report["summary"]["regression_gate_unverified_rows"], 1)
        self.assertEqual(report["artifacts"]["regression_gate_md"], "calibration_regression_gate.md")
        self.assertEqual(report["summary"]["delivery_decision"], "use_regression_overlay_final")
        self.assertEqual(report["summary"]["delivery_output_xlsx"], "calibration_regression_overlay_official_sites.xlsx")
        self.assertFalse(report["summary"]["delivery_is_rule_release"])
        self.assertEqual(report["application_gates"]["review_lane_change"]["status"], "blocked")
        self.assertIn("fix_regression_gate_failures", report["application_gates"]["review_lane_change"]["blockers"])
        self.assertIn("fix_regression_gate_failures", {item["id"] for item in report["open_requirements"]})
        self.assertIn("Use calibration_regression_overlay_official_sites.xlsx", report["next_actions"][0])
        self.assertIn("Fix candidate workflow changes", report["next_actions"][1])

    def test_build_calibration_status_report_marks_candidate_changes_after_gate_pass(self):
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
                            "filled_regression_case_rows": 5,
                            "regression_gate_status": "pass",
                            "regression_gate_fail_rows": 0,
                            "regression_gate_unverified_rows": 0,
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

            report = build_calibration_status_report(calibration_cycle_json=cycle, sample_eval_json=sample_eval)

        self.assertEqual(report["summary"]["workflow_status"], "candidate_changes_regression_passed")
        self.assertEqual(report["summary"]["regression_gate_status"], "pass")
        self.assertEqual(report["application_gates"]["global_threshold_change"]["status"], "not_recommended")
        self.assertEqual(report["application_gates"]["review_lane_change"]["status"], "candidate")
        self.assertFalse(report["application_gates"]["review_lane_change"]["can_apply_now"])
        self.assertIn("Regression gate passed", report["next_actions"][0])

    def test_build_calibration_status_report_allows_current_guarded_pattern_candidate_with_protected_lanes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cycle = root / "cycle.json"
            sample_eval = root / "sample_eval.json"
            status_json = root / "status.json"
            cycle.write_text(
                json.dumps(
                    {
                        "summary": {
                            "recommended_global_accept_threshold": 75,
                            "recommended_second_pass_threshold": 75,
                            "recommended_pattern_release": "narrow_pattern_release_candidate",
                            "recommended_pattern_release_source_kind": "supplied_prior",
                            "recommended_pattern_release_source_path": "prior/pattern_release.json",
                            "pattern_release_correct_rows": 4,
                            "pattern_release_wrong_rows": 0,
                            "filled_eval_labeled_rows": 8,
                            "filled_eval_decisive_rows": 8,
                            "filled_regression_case_rows": 8,
                            "regression_gate_status": "pass",
                            "regression_gate_fail_rows": 0,
                            "regression_gate_unverified_rows": 0,
                            "protected_review_lane_count": 2,
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
                            "lane_keep_review_rows": 1,
                            "lane_candidate_for_change_rows": 0,
                            "lane_needs_more_label_rows": 0,
                        },
                        "by_review_reason": {
                            "precision_calibrated_pattern_release": {
                                "rows": 4,
                                "labeled_rows": 4,
                                "decisive_rows": 4,
                            },
                            "precision_low_confidence_auto_match": {
                                "rows": 4,
                                "labeled_rows": 4,
                                "decisive_rows": 4,
                            },
                        },
                        "lane_recommendations": [
                            {
                                "review_reason": "precision_calibrated_pattern_release",
                                "recommendation": "candidate_for_review_downgrade",
                                "support_rows": 4,
                                "blocking_rows": 0,
                            },
                            {
                                "review_reason": "precision_low_confidence_auto_match",
                                "recommendation": "keep_review_lane",
                                "support_rows": 3,
                                "blocking_rows": 1,
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )

            report = build_calibration_status_report(
                calibration_cycle_json=cycle,
                sample_eval_json=sample_eval,
                output_json=status_json,
            )
            default_gate = check_calibration_application_gate(status_json=status_json, gate="pattern_release_change")
            allowed_gate = check_calibration_application_gate(
                status_json=status_json,
                gate="pattern_release_change",
                allow_candidate=True,
            )

        self.assertEqual(report["summary"]["workflow_status"], "partially_converged_keep_review_lanes")
        self.assertEqual(report["summary"]["pattern_release_status"], "current_guarded_candidate")
        self.assertEqual(report["summary"]["review_lane_status"], "protected_by_filled_labels")
        self.assertEqual(report["application_gates"]["pattern_release_change"]["status"], "candidate")
        self.assertEqual(report["application_gates"]["pattern_release_change"]["blockers"], [])
        self.assertFalse(default_gate["summary"]["allowed"])
        self.assertEqual(default_gate["summary"]["decision_reason"], "candidate_requires_explicit_allow_candidate")
        self.assertTrue(allowed_gate["summary"]["allowed"])
        self.assertEqual(allowed_gate["summary"]["decision_reason"], "candidate_allowed_for_controlled_rollout")

    def test_build_calibration_status_report_blocks_historical_pattern_candidate_until_current_spot_check(self):
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
                            "recommended_pattern_release": "narrow_pattern_release_candidate",
                            "recommended_pattern_release_source_kind": "supplied_prior",
                            "recommended_pattern_release_source_path": "prior/pattern_release.json",
                            "pattern_release_correct_rows": 4,
                            "pattern_release_wrong_rows": 0,
                            "filled_eval_labeled_rows": 4,
                            "filled_eval_decisive_rows": 4,
                            "filled_regression_case_rows": 4,
                            "regression_gate_status": "pass",
                            "regression_gate_fail_rows": 0,
                            "regression_gate_unverified_rows": 0,
                            "protected_review_lane_count": 0,
                        }
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
                            "lane_keep_review_rows": 0,
                            "lane_candidate_for_change_rows": 0,
                            "lane_needs_more_label_rows": 0,
                        },
                        "by_review_reason": {},
                        "lane_recommendations": [],
                    }
                ),
                encoding="utf-8",
            )

            report = build_calibration_status_report(calibration_cycle_json=cycle, sample_eval_json=sample_eval)

        self.assertEqual(report["summary"]["pattern_release_status"], "historical_guarded_candidate")
        self.assertEqual(report["application_gates"]["pattern_release_change"]["status"], "blocked")
        self.assertIn(
            "validate_historical_pattern_release",
            report["application_gates"]["pattern_release_change"]["blockers"],
        )

    def test_check_calibration_application_gate_blocks_unsafe_actions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            status = root / "calibration_status.json"
            status.write_text(
                json.dumps(
                    {
                        "application_gates": {
                            "review_lane_change": {
                                "status": "blocked",
                                "can_apply_now": False,
                                "blockers": ["fill_calibration_sample", "collect_lane_labels"],
                                "reason": "More labels are needed.",
                                "required_action": "Resolve blockers before applying review_lane_change.",
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )

            report = check_calibration_application_gate(status_json=status, gate="review_lane_change")

        self.assertFalse(report["summary"]["allowed"])
        self.assertEqual(report["summary"]["gate_status"], "blocked")
        self.assertEqual(report["summary"]["decision_reason"], "gate_has_blockers")
        self.assertIn("fill_calibration_sample", report["summary"]["blockers"])

    def test_check_calibration_application_gate_allows_candidate_only_when_requested(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            status = root / "calibration_status.json"
            status.write_text(
                json.dumps(
                    {
                        "application_gates": {
                            "review_lane_change": {
                                "status": "candidate",
                                "can_apply_now": False,
                                "blockers": [],
                                "reason": "Regression gate passed and lane evidence is clean.",
                                "required_action": "Apply a narrow lane downgrade.",
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )

            default_report = check_calibration_application_gate(status_json=status, gate="review_lane_change")
            allowed_report = check_calibration_application_gate(
                status_json=status,
                gate="review_lane_change",
                allow_candidate=True,
            )

        self.assertFalse(default_report["summary"]["allowed"])
        self.assertEqual(default_report["summary"]["decision_reason"], "candidate_requires_explicit_allow_candidate")
        self.assertTrue(allowed_report["summary"]["allowed"])
        self.assertEqual(allowed_report["summary"]["decision_reason"], "candidate_allowed_for_controlled_rollout")

    def test_check_calibration_application_gate_keeps_not_recommended_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            status = root / "calibration_status.json"
            status.write_text(
                json.dumps(
                    {
                        "application_gates": {
                            "global_threshold_change": {
                                "status": "not_recommended",
                                "can_apply_now": False,
                                "blockers": [],
                                "reason": "Current evidence recommends keeping thresholds unchanged.",
                                "required_action": "Keep thresholds unchanged.",
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )

            report = check_calibration_application_gate(
                status_json=status,
                gate="global_threshold_change",
                allow_candidate=True,
            )

        self.assertFalse(report["summary"]["allowed"])
        self.assertEqual(report["summary"]["decision_reason"], "gate_not_recommended")

    def test_build_calibration_status_report_prioritizes_unrun_regression_gate(self):
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
                            "filled_regression_case_rows": 5,
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

            report = build_calibration_status_report(calibration_cycle_json=cycle, sample_eval_json=sample_eval)

        self.assertEqual(report["summary"]["workflow_status"], "candidate_changes_require_regression")
        self.assertEqual(report["summary"]["regression_gate_status"], "not_run")
        self.assertEqual(report["application_gates"]["review_lane_change"]["status"], "blocked")
        self.assertIn("run_regression_gate", report["application_gates"]["review_lane_change"]["blockers"])
        self.assertIn("run_regression_gate", {item["id"] for item in report["open_requirements"]})
        self.assertIn("run_calibration_regression_gate.py", report["next_actions"][0])

    def test_build_calibration_status_report_does_not_converge_with_unrun_regression_gate(self):
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
                            "filled_regression_case_rows": 4,
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
                            "lane_candidate_for_change_rows": 0,
                            "lane_keep_review_rows": 0,
                            "lane_needs_more_label_rows": 0,
                        }
                    }
                ),
                encoding="utf-8",
            )

            report = build_calibration_status_report(calibration_cycle_json=cycle, sample_eval_json=sample_eval)

        self.assertEqual(report["summary"]["workflow_status"], "not_converged_regression_gate_not_run")
        self.assertEqual(report["summary"]["regression_gate_status"], "not_run")
        self.assertEqual(report["application_gates"]["pattern_release_change"]["status"], "blocked")
        self.assertIn("run_regression_gate", report["application_gates"]["pattern_release_change"]["blockers"])
        self.assertIn("run_regression_gate", {item["id"] for item in report["open_requirements"]})
        self.assertIn("run_calibration_regression_gate.py", report["next_actions"][0])

    def test_build_calibration_status_report_blocks_on_fill_quality_issues(self):
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
                            "filled_eval_labeled_rows": 3,
                            "filled_eval_decisive_rows": 2,
                        }
                    }
                ),
                encoding="utf-8",
            )
            sample_eval.write_text(
                json.dumps(
                    {
                        "summary": {
                            "labeled_rows": 2,
                            "decisive_rows": 2,
                            "decision_quality_issue_rows": 2,
                            "invalid_manual_decision_rows": 1,
                            "replace_missing_manual_url_rows": 1,
                        }
                    }
                ),
                encoding="utf-8",
            )

            report = build_calibration_status_report(calibration_cycle_json=cycle, sample_eval_json=sample_eval)

        self.assertEqual(report["summary"]["workflow_status"], "not_converged_fix_fill_quality")
        self.assertEqual(report["summary"]["decision_quality_issue_rows"], 2)
        self.assertEqual(report["summary"]["invalid_manual_decision_rows"], 1)
        self.assertEqual(report["summary"]["replace_missing_manual_url_rows"], 1)
        self.assertIn("fix_calibration_fill_quality", {item["id"] for item in report["open_requirements"]})
        self.assertIn("Fix invalid manual_decision", report["next_actions"][0])

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
                                "support_rows": 5,
                                "blocking_rows": 0,
                                "support_rate": 1.0,
                                "support_rate_wilson_lower_80": 0.7527,
                                "blocking_rate_wilson_upper_80": 0.2473,
                                "evidence_strength": "minimum_support",
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
        self.assertEqual(report["summary"]["lane_change_candidate_count"], 1)
        self.assertEqual(report["summary"]["deferred_lane_change_candidate_count"], 1)
        self.assertEqual(report["summary"]["ready_lane_change_candidate_count"], 0)
        self.assertEqual(report["review_lanes"]["decisive_rows_needed"], 3)
        self.assertEqual(by_reason["precision_second_pass_accepted_lt70"]["decisive_rows_needed"], 0)
        self.assertIn("sub-70 second-pass", by_reason["precision_second_pass_accepted_lt70"]["if_clean_action"])
        self.assertIn("manual-only", by_reason["precision_second_pass_accepted_lt70"]["if_blocked_action"])
        self.assertIn("decisive", by_reason["precision_second_pass_accepted_lt70"]["if_unsure_action"])
        self.assertEqual(by_reason["precision_low_confidence_auto_match"]["decisive_rows_needed"], 3)
        self.assertIn("protected lane", by_reason["precision_low_confidence_auto_match"]["if_clean_action"])
        self.assertIn("regression fixtures", by_reason["precision_low_confidence_auto_match"]["if_blocked_action"])
        self.assertEqual(report["lane_change_candidates"][0]["review_reason"], "precision_second_pass_accepted_lt70")
        self.assertEqual(report["lane_change_candidates"][0]["status"], "deferred_until_remaining_label_gaps_close")
        self.assertEqual(report["lane_change_candidates"][0]["blocking_decisive_rows_needed"], 3)
        self.assertEqual(report["lane_change_candidates"][0]["evidence_strength"], "minimum_support")
        self.assertEqual(report["lane_change_candidates"][0]["support_rate_wilson_lower_80"], 0.7527)
        self.assertIn("defer_lane_downgrade_candidate", {item["id"] for item in report["open_requirements"]})
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
        self.assertIn("5 remaining decisive labels", out_rows[0]["label_decision_impact"])
        self.assertIn("zero reject/replace blockers", out_rows[0]["label_decision_impact"])
        self.assertIn("sub-70 second-pass", out_rows[0]["label_if_clean_action"])
        self.assertIn("manual-only", out_rows[0]["label_if_blocked_action"])
        self.assertIn("decisive", out_rows[0]["label_if_unsure_action"])
        self.assertEqual(out_rows[0]["manual_decision"], "")
        self.assertIn("provider_detail_url", out_rows[0])
        self.assertNotIn("high-1", {row["provider_id"] for row in out_rows})
        spot_rows = [row for row in out_rows if row["review_reason"] == "precision_calibrated_pattern_release"]
        self.assertEqual(spot_rows[0]["label_evidence_source_kind"], "supplied_prior")
        self.assertEqual(spot_rows[0]["label_evidence_source_path"], "prior/pattern_release.json")
        self.assertIn("blocks wider automatic release", spot_rows[0]["label_decision_hint"])
        self.assertIn("A reject/replace blocks wider automatic release", spot_rows[0]["label_decision_impact"])
        self.assertIn("guarded release candidate", spot_rows[0]["label_if_clean_action"])
        self.assertIn("Block wider pattern release", spot_rows[0]["label_if_blocked_action"])
        self.assertEqual(high_only_summary["task_rows"], 4)
        self.assertEqual({row["label_priority"] for row in high_only_rows}, {"high"})

    def test_build_protected_lane_review_task_selects_unfilled_protected_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            status = root / "status.json"
            sample = root / "sample.csv"
            filled = root / "filled.csv"
            output_csv = root / "protected.csv"
            output_xlsx = root / "protected.xlsx"
            output_json = root / "protected_summary.json"
            rows = [
                {
                    "sample_priority": "58",
                    "review_reason": "precision_low_confidence_auto_match",
                    "agent_b_decision": "accept",
                    "provider_id": "low-0",
                    "provider_name": "Low Zero",
                    "provider_detail_url": "https://amazon.example/low-0",
                    "official_url": "https://low0.example",
                    "candidate_url": "https://low0.example",
                    "manual_decision": "",
                    "manual_url": "",
                    "notes": "",
                },
                {
                    "sample_priority": "58",
                    "review_reason": "precision_low_confidence_auto_match",
                    "agent_b_decision": "accept",
                    "provider_id": "low-1",
                    "provider_name": "Low One",
                    "provider_detail_url": "https://amazon.example/low-1",
                    "official_url": "https://low1.example",
                    "candidate_url": "https://low1.example",
                    "manual_decision": "",
                    "manual_url": "",
                    "notes": "",
                },
                {
                    "sample_priority": "58",
                    "review_reason": "precision_low_confidence_auto_match",
                    "agent_b_decision": "accept",
                    "provider_id": "low-2",
                    "provider_name": "Low Two",
                    "provider_detail_url": "https://amazon.example/low-2",
                    "official_url": "https://low2.example",
                    "candidate_url": "https://low2.example",
                    "manual_decision": "accept",
                    "manual_url": "",
                    "notes": "",
                },
                {
                    "sample_priority": "66",
                    "review_reason": "precision_generic_identity_term_risk",
                    "agent_b_decision": "unsure",
                    "provider_id": "generic-0",
                    "provider_name": "Generic Zero",
                    "provider_detail_url": "https://amazon.example/generic-0",
                    "official_url": "https://generic0.example",
                    "candidate_url": "https://generic0.example",
                    "manual_decision": "",
                    "manual_url": "",
                    "notes": "",
                },
                {
                    "sample_priority": "98",
                    "review_reason": "precision_calibrated_pattern_release",
                    "agent_b_decision": "accept",
                    "provider_id": "release-0",
                    "provider_name": "Release Zero",
                    "provider_detail_url": "https://amazon.example/release-0",
                    "official_url": "https://release0.example",
                    "candidate_url": "https://release0.example",
                    "manual_decision": "",
                    "manual_url": "",
                    "notes": "",
                },
                {
                    "sample_priority": "80",
                    "review_reason": "recall_unresolved_top_candidate",
                    "agent_b_decision": "unsure",
                    "provider_id": "recall-0",
                    "provider_name": "Recall Zero",
                    "provider_detail_url": "https://amazon.example/recall-0",
                    "official_url": "",
                    "candidate_url": "https://recall0.example",
                    "manual_decision": "",
                    "manual_url": "",
                    "notes": "",
                },
            ]
            _write_test_csv(sample, rows)
            _write_test_csv(
                filled,
                [
                    {
                        "provider_id": "low-1",
                        "review_reason": "precision_low_confidence_auto_match",
                        "manual_decision": "reject",
                    }
                ],
            )
            status.write_text(
                json.dumps(
                    {
                        "summary": {"pattern_release_status": "current_guarded_candidate"},
                        "artifacts": {"sample_csv": str(sample)},
                        "label_targets": [
                            {
                                "review_reason": "precision_low_confidence_auto_match",
                                "priority": "medium",
                                "target_decisive_rows": 3,
                                "decisive_rows": 3,
                                "decisive_rows_needed": 0,
                                "recommendation": "keep_review_lane",
                                "label_goal": "Keep checking low-confidence accepted rows.",
                                "if_clean_action": "Consider a narrow routing downgrade only after regression tests.",
                                "if_blocked_action": "Keep this lane protected.",
                                "if_unsure_action": "Keep this lane protected.",
                            },
                            {
                                "review_reason": "precision_generic_identity_term_risk",
                                "priority": "high",
                                "target_decisive_rows": 5,
                                "decisive_rows": 5,
                                "decisive_rows_needed": 0,
                                "recommendation": "needs_more_labels",
                                "label_goal": "Collect more generic-name identity labels.",
                            },
                            {
                                "review_reason": "precision_calibrated_pattern_release",
                                "priority": "medium",
                                "target_decisive_rows": 3,
                                "decisive_rows": 3,
                                "decisive_rows_needed": 0,
                                "blocking_rows": 0,
                                "recommendation": "spot_check_candidate",
                                "label_goal": "Already validated current guarded release.",
                            },
                            {
                                "review_reason": "recall_unresolved_top_candidate",
                                "priority": "medium",
                                "target_decisive_rows": 3,
                                "decisive_rows": 3,
                                "decisive_rows_needed": 0,
                                "recommendation": "keep_review_lane",
                                "label_goal": "Keep recall rows manual-only.",
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )

            summary = build_protected_lane_review_task(
                status_json=status,
                filled_sample=[filled],
                output_csv=output_csv,
                output_xlsx=output_xlsx,
                output_json=output_json,
                max_rows=4,
                max_per_reason=2,
            )
            with output_csv.open(newline="", encoding="utf-8") as f:
                out_rows = list(csv.DictReader(f))
            with zipfile.ZipFile(output_xlsx) as z:
                sheet_xml = z.read("xl/worksheets/sheet1.xml").decode("utf-8")
            summary_json = json.loads(output_json.read_text(encoding="utf-8"))

        provider_ids = {row["provider_id"] for row in out_rows}
        self.assertEqual(summary["task_rows"], 3)
        self.assertEqual(summary_json["task_rows"], 3)
        self.assertEqual(provider_ids, {"generic-0", "low-0", "recall-0"})
        self.assertNotIn("low-1", provider_ids)
        self.assertNotIn("low-2", provider_ids)
        self.assertNotIn("release-0", provider_ids)
        self.assertEqual(out_rows[0]["protected_lane_priority"], "high")
        self.assertEqual(out_rows[0]["protected_lane_label_gap_closed"], "yes")
        self.assertIn("manual_decision", out_rows[0]["review_instruction"])
        low_row = next(row for row in out_rows if row["provider_id"] == "low-0")
        self.assertIn("regression", low_row["optimization_use"])
        self.assertEqual(low_row["manual_decision"], "")
        self.assertIn("provider_detail_url", out_rows[0])
        self.assertIn("HYPERLINK", sheet_xml)
        self.assertEqual(summary["excluded_filled_rows"], 2)

    def test_build_protected_lane_priority_task_balances_high_value_boundaries(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "protected.csv"
            output_csv = root / "priority.csv"
            output_xlsx = root / "priority.xlsx"
            output_json = root / "priority.json"
            output_md = root / "priority.md"
            rows = [
                {
                    "sample_priority": "74",
                    "review_reason": "precision_generic_identity_term_risk",
                    "agent_b_decision": "unsure",
                    "provider_id": "generic-1",
                    "provider_name": "AA Consulting",
                    "provider_detail_url": "https://amazon.example/generic-1",
                    "official_url": "https://aa.example",
                    "candidate_url": "https://aa.example",
                    "source_confidence": "100",
                    "supporting_facts": "page_contains_exact_provider_name",
                    "counter_evidence": "",
                    "evidence_summary": "domain_exact_provider_slug",
                    "manual_decision": "",
                    "manual_url": "",
                    "notes": "",
                },
                {
                    "sample_priority": "96",
                    "review_reason": "precision_low_confidence_auto_match",
                    "agent_b_decision": "unsure",
                    "provider_id": "low-1",
                    "provider_name": "E TECH HUB",
                    "provider_detail_url": "https://amazon.example/low-1",
                    "official_url": "https://etech.example",
                    "candidate_url": "https://etech.example",
                    "source_confidence": "82",
                    "supporting_facts": "",
                    "counter_evidence": "candidate_pages_not_fetchable; provider_name_not_found_on_candidate_pages",
                    "evidence_summary": "domain_exact_provider_slug",
                    "manual_decision": "",
                    "manual_url": "",
                    "notes": "",
                },
                {
                    "sample_priority": "74",
                    "review_reason": "precision_low_confidence_auto_match",
                    "agent_b_decision": "accept",
                    "provider_id": "low-filled",
                    "provider_name": "Filled Low",
                    "provider_detail_url": "https://amazon.example/low-filled",
                    "official_url": "https://filled.example",
                    "candidate_url": "https://filled.example",
                    "source_confidence": "82",
                    "supporting_facts": "",
                    "counter_evidence": "",
                    "evidence_summary": "",
                    "manual_decision": "accept",
                    "manual_url": "",
                    "notes": "",
                },
                {
                    "sample_priority": "96",
                    "review_reason": "recall_unresolved_top_candidate",
                    "agent_b_decision": "unsure",
                    "provider_id": "recall-1",
                    "provider_name": "Recall One",
                    "provider_detail_url": "https://amazon.example/recall-1",
                    "official_url": "",
                    "candidate_url": "https://recall.example",
                    "source_confidence": "69",
                    "supporting_facts": "",
                    "counter_evidence": "provider_name_not_found_on_candidate_pages",
                    "evidence_summary": "identity_cap_ambiguous_name_requires_page_and_service",
                    "manual_decision": "",
                    "manual_url": "",
                    "notes": "",
                },
                {
                    "sample_priority": "96",
                    "review_reason": "precision_slug_extension_identity_risk",
                    "agent_b_decision": "accept",
                    "provider_id": "slug-1",
                    "provider_name": "Slug One",
                    "provider_detail_url": "https://amazon.example/slug-1",
                    "official_url": "https://slug.example",
                    "candidate_url": "https://slug.example",
                    "source_confidence": "100",
                    "supporting_facts": "service_content_matches_amazon_provider",
                    "counter_evidence": "",
                    "evidence_summary": "domain_contains_provider_slug",
                    "manual_decision": "",
                    "manual_url": "",
                    "notes": "",
                },
            ]
            _write_test_csv(source, rows)

            summary = build_protected_lane_priority_task(
                source_csv=source,
                output_csv=output_csv,
                output_xlsx=output_xlsx,
                output_json=output_json,
                output_md=output_md,
                max_rows=4,
                max_per_reason=1,
            )
            with output_csv.open(newline="", encoding="utf-8") as f:
                out_rows = list(csv.DictReader(f))
            with zipfile.ZipFile(output_xlsx) as z:
                sheet_xml = z.read("xl/worksheets/sheet1.xml").decode("utf-8")
            summary_json = json.loads(output_json.read_text(encoding="utf-8"))
            md_text = output_md.read_text(encoding="utf-8")

        self.assertEqual(summary["task_rows"], 4)
        self.assertEqual(summary_json["task_rows"], 4)
        self.assertEqual(set(summary["reason_counts"].values()), {1})
        self.assertNotIn("low-filled", {row["provider_id"] for row in out_rows})
        self.assertEqual(out_rows[0]["priority_rank"], "1")
        self.assertIn("priority_reason", out_rows[0])
        self.assertIn("decision_impact", out_rows[0])
        self.assertTrue(any(row["priority_reason"] == "unfetchable_candidate_boundary" for row in out_rows))
        self.assertTrue(any(row["priority_reason"] == "slug_extension_agentb_accept" for row in out_rows))
        self.assertTrue(all(row["manual_decision"] == "" for row in out_rows))
        self.assertIn("provider_detail_url", out_rows[0])
        self.assertIn("HYPERLINK", sheet_xml)
        self.assertIn("Protected-Lane Priority Review Handoff", md_text)
        self.assertIn("unfetchable_candidate_boundary", md_text)
        self.assertIn("manual_decision", md_text)

    def test_reuse_historical_labels_for_task_reuses_only_trusted_manual_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task = root / "priority.csv"
            labels_dir = root / "labels"
            labels_dir.mkdir()
            output_csv = root / "prefilled.csv"
            output_xlsx = root / "prefilled.xlsx"
            output_json = root / "reuse.json"
            output_md = root / "reuse.md"
            _write_test_csv(
                task,
                [
                    {
                        "provider_id": "p1",
                        "provider_name": "Provider One",
                        "review_reason": "precision_generic_identity_term_risk",
                        "provider_detail_url": "https://amazon.example/p1",
                        "candidate_url": "https://one.example",
                        "official_url": "https://one.example",
                        "manual_decision": "",
                        "manual_url": "",
                        "notes": "",
                    },
                    {
                        "provider_id": "p2",
                        "provider_name": "Provider Two",
                        "review_reason": "precision_low_confidence_auto_match",
                        "provider_detail_url": "https://amazon.example/p2",
                        "candidate_url": "https://two.example",
                        "official_url": "https://two.example",
                        "manual_decision": "",
                        "manual_url": "",
                        "notes": "",
                    },
                    {
                        "provider_id": "p3",
                        "provider_name": "Provider Three",
                        "review_reason": "recall_unresolved_top_candidate",
                        "provider_detail_url": "https://amazon.example/p3",
                        "candidate_url": "https://three.example",
                        "official_url": "",
                        "manual_decision": "",
                        "manual_url": "",
                        "notes": "",
                    },
                ],
            )
            _write_test_csv(
                labels_dir / "manual_review_combined_decisions.csv",
                [
                    {"provider_id": "p1", "manual_decision": "reject", "manual_url": "", "notes": "wrong company"},
                    {"provider_id": "p2", "manual_decision": "accept", "manual_url": "", "notes": "correct"},
                ],
            )
            _write_test_csv(
                labels_dir / "agent_b_verification_results.csv",
                [{"provider_id": "p3", "manual_decision": "accept", "manual_url": "", "notes": "not trusted"}],
            )
            _write_test_csv(
                labels_dir / "agent_human_review_regression_cases.csv",
                [{"provider_id": "p2", "manual_decision": "reject", "manual_url": "", "notes": "conflict"}],
            )

            report = reuse_historical_labels_for_task(
                task_csv=task,
                label_paths=[labels_dir],
                output_csv=output_csv,
                output_xlsx=output_xlsx,
                output_json=output_json,
                output_md=output_md,
            )
            with output_csv.open(newline="", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
            saved = json.loads(output_json.read_text(encoding="utf-8"))
            md_text = output_md.read_text(encoding="utf-8")
            output_xlsx_exists = output_xlsx.exists()

        by_provider = {row["provider_id"]: row for row in rows}
        self.assertEqual(report["summary"]["reused_rows"], 1)
        self.assertEqual(report["summary"]["conflict_rows"], 1)
        self.assertEqual(report["summary"]["unlabeled_rows"], 1)
        self.assertEqual(saved["summary"]["reused_rows"], 1)
        self.assertEqual(by_provider["p1"]["manual_decision"], "reject")
        self.assertEqual(by_provider["p1"]["historical_label_status"], "reused")
        self.assertEqual(by_provider["p2"]["manual_decision"], "")
        self.assertEqual(by_provider["p2"]["historical_label_status"], "conflict")
        self.assertEqual(by_provider["p3"]["manual_decision"], "")
        self.assertEqual(by_provider["p3"]["historical_label_status"], "unlabeled")
        self.assertIn("Provider One", md_text)
        self.assertTrue(output_xlsx_exists)

    def test_build_convergence_audit_keeps_75_75_and_routes_next_protected_labels(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            status = root / "status.json"
            balance = root / "balance.json"
            protected_summary = root / "protected_summary.json"
            priority_summary = root / "priority_summary.json"
            output_json = root / "convergence.json"
            output_md = root / "convergence.md"
            status.write_text(
                json.dumps(
                    {
                        "summary": {
                            "workflow_status": "partially_converged_keep_review_lanes",
                            "threshold_status": "stable_keep_current",
                            "pattern_release_status": "current_guarded_candidate",
                            "review_lane_status": "protected_by_filled_labels",
                            "recommended_global_accept_threshold": 75,
                            "recommended_second_pass_threshold": 75,
                            "filled_decisive_rows": 22,
                            "protected_review_lane_count": 5,
                            "pattern_release_correct_rows": 4,
                            "pattern_release_wrong_rows": 0,
                            "pattern_release_source_path": "prior/pattern_release.json",
                            "pattern_release_source_kind": "supplied_prior",
                            "regression_gate_status": "pass",
                        },
                        "application_gates": {
                            "global_threshold_change": {
                                "status": "not_recommended",
                                "can_apply_now": False,
                                "blockers": [],
                                "reason": "Current evidence recommends keeping thresholds unchanged.",
                            },
                            "review_lane_change": {
                                "status": "blocked",
                                "can_apply_now": False,
                                "blockers": [],
                                "reason": "Filled labels still show a protected lane.",
                            },
                            "pattern_release_change": {
                                "status": "candidate",
                                "can_apply_now": False,
                                "blockers": [],
                                "reason": "Current spot checks are clean.",
                                "required_action": "Keep guarded.",
                            },
                        },
                        "next_actions": ["Keep protected review lanes active."],
                    }
                ),
                encoding="utf-8",
            )
            balance.write_text(
                json.dumps(
                    {
                        "threshold_simulations": [
                            {
                                "threshold": 70,
                                "overall_accuracy": 0.85,
                                "auto_precision": 0.908,
                                "official_recall": 0.908,
                                "false_official_rows": 8,
                                "over_rejected_rows": 7,
                                "official_output_rows": 87,
                            },
                            {
                                "threshold": 75,
                                "overall_accuracy": 0.85,
                                "auto_precision": 0.908,
                                "official_recall": 0.908,
                                "false_official_rows": 8,
                                "over_rejected_rows": 7,
                                "official_output_rows": 87,
                            },
                            {
                                "threshold": 80,
                                "overall_accuracy": 0.84,
                                "auto_precision": 0.9167,
                                "official_recall": 0.8851,
                                "false_official_rows": 7,
                                "over_rejected_rows": 9,
                                "official_output_rows": 84,
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            protected_summary.write_text(
                json.dumps(
                    {
                        "task_rows": 32,
                        "output_csv": "protected.csv",
                        "output_xlsx": "protected.xlsx",
                        "reason_counts": {
                            "precision_low_confidence_auto_match": 10,
                            "recall_unresolved_top_candidate": 10,
                        },
                        "agent_b_decision_counts": {"unsure": 29, "accept": 3},
                    }
                ),
                encoding="utf-8",
            )
            priority_summary.write_text(
                json.dumps(
                    {
                        "task_rows": 16,
                        "output_csv": "priority.csv",
                        "output_xlsx": "priority.xlsx",
                        "reason_counts": {
                            "precision_low_confidence_auto_match": 4,
                            "recall_unresolved_top_candidate": 4,
                        },
                        "priority_reason_counts": {
                            "unfetchable_candidate_boundary": 3,
                            "country_conflict_boundary": 1,
                        },
                        "agent_b_decision_counts": {"unsure": 15, "accept": 1},
                    }
                ),
                encoding="utf-8",
            )

            report = build_convergence_audit(
                status_json=status,
                labeled_balance_json=balance,
                protected_task_summary_json=protected_summary,
                protected_priority_task_summary_json=priority_summary,
                output_json=output_json,
                output_md=output_md,
            )
            saved = json.loads(output_json.read_text(encoding="utf-8"))
            md_text = output_md.read_text(encoding="utf-8")

        self.assertEqual(report["summary"]["convergence_state"], "partially_converged_keep_protected_lanes")
        self.assertEqual(report["summary"]["threshold_decision"], "keep_current_75_75")
        self.assertTrue(report["summary"]["current_threshold_ties_best_accuracy"])
        self.assertEqual(report["threshold"]["best_accuracy_thresholds"], [70, 75])
        self.assertEqual(report["review_lanes"]["decision"], "keep_protected_lanes")
        self.assertEqual(report["review_lanes"]["next_task_rows"], 32)
        self.assertEqual(report["review_lanes"]["priority_task_rows"], 16)
        self.assertEqual(report["summary"]["protected_lanes_priority_task_rows"], 16)
        self.assertEqual(report["pattern_release"]["decision"], "guarded_candidate_requires_explicit_allow")
        self.assertIn("priority.xlsx", report["next_actions"][1])
        self.assertIn("protected.xlsx", report["next_actions"][2])
        self.assertEqual(saved["summary"]["threshold_decision"], "keep_current_75_75")
        self.assertEqual(saved["summary"]["protected_lanes_priority_task_rows"], 16)
        self.assertIn("Convergence Audit", md_text)
        self.assertIn("75/75", md_text)
        self.assertIn("priority.xlsx", md_text)

    def test_build_convergence_audit_separates_overlay_delivery_from_rule_release(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            status = root / "status.json"
            balance = root / "balance.json"
            output_md = root / "convergence.md"
            status.write_text(
                json.dumps(
                    {
                        "summary": {
                            "workflow_status": "not_converged_regression_gate_failed",
                            "threshold_status": "stable_keep_current",
                            "pattern_release_status": "current_guarded_candidate",
                            "review_lane_status": "protected_by_filled_labels",
                            "recommended_global_accept_threshold": 75,
                            "recommended_second_pass_threshold": 75,
                            "filled_decisive_rows": 30,
                            "protected_review_lane_count": 5,
                            "pattern_release_correct_rows": 4,
                            "pattern_release_wrong_rows": 0,
                            "regression_gate_status": "failed",
                        },
                        "delivery_recommendation": {
                            "decision": "use_regression_overlay_final",
                            "output_csv": "overlay.csv",
                            "output_xlsx": "overlay.xlsx",
                            "is_rule_release": False,
                            "reason": "Exact human-label overlay only.",
                        },
                        "next_actions": ["Fix candidate workflow changes until calibration_regression_gate passes."],
                    }
                ),
                encoding="utf-8",
            )
            balance.write_text(
                json.dumps(
                    {
                        "threshold_simulations": [
                            {
                                "threshold": 75,
                                "overall_accuracy": 0.9,
                                "auto_precision": 0.95,
                                "official_recall": 0.92,
                                "false_official_rows": 4,
                                "over_rejected_rows": 6,
                                "official_output_rows": 243,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            report = build_convergence_audit(
                status_json=status,
                labeled_balance_json=balance,
                output_md=output_md,
            )
            md_text = output_md.read_text(encoding="utf-8")

        self.assertEqual(report["summary"]["delivery_decision"], "use_regression_overlay_final")
        self.assertEqual(report["summary"]["delivery_output_xlsx"], "overlay.xlsx")
        self.assertFalse(report["summary"]["delivery_is_rule_release"])
        self.assertIn("Use overlay.xlsx for current delivery", report["next_actions"][1])
        self.assertIn("Fix candidate workflow changes", report["next_actions"][-1])
        self.assertIn("Delivery Recommendation", md_text)
        self.assertIn("overlay.xlsx", md_text)

    def test_simulate_review_lane_output_policy_holds_selected_precision_lane(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            final_csv = root / "official_sites.csv"
            review_csv = root / "review_task.csv"
            agent_b_csv = root / "agent_b.csv"
            details_json = root / "details.json"
            cases_csv = root / "cases.csv"
            output_csv = root / "policy.csv"
            output_xlsx = root / "policy.xlsx"
            pattern_csv = root / "pattern_policy.csv"
            pattern_summary_json = root / "pattern_policy.json"
            summary_json = root / "policy.json"
            summary_md = root / "policy.md"
            _write_test_csv(
                final_csv,
                [
                    {
                        "provider_id": "p-risk",
                        "provider_name": "Risk Agency",
                        "provider_detail_url": "https://amazon.example/risk",
                        "official_url": "https://wrong.example/",
                        "official_domain": "wrong.example",
                        "status": "matched",
                        "decision_source": "auto",
                        "confidence": "82",
                        "source_status": "",
                        "evidence_summary": "",
                        "candidate_count": "1",
                        "scored_candidate_count": "1",
                        "service_apis": "",
                        "provider_locations": "",
                        "notes": "",
                    },
                    {
                        "provider_id": "p-safe",
                        "provider_name": "Safe Agency",
                        "provider_detail_url": "https://amazon.example/safe",
                        "official_url": "https://safe.example/",
                        "official_domain": "safe.example",
                        "status": "matched",
                        "decision_source": "auto",
                        "confidence": "90",
                        "source_status": "",
                        "evidence_summary": "",
                        "candidate_count": "1",
                        "scored_candidate_count": "1",
                        "service_apis": "",
                        "provider_locations": "",
                        "notes": "",
                    },
                ],
            )
            _write_test_csv(
                review_csv,
                [
                    {
                        "provider_id": "p-risk",
                        "provider_name": "Risk Agency",
                        "review_reason": "precision_low_confidence_auto_match",
                    },
                    {
                        "provider_id": "p-safe",
                        "provider_name": "Safe Agency",
                        "review_reason": "precision_calibrated_pattern_release",
                    },
                ],
            )
            _write_test_csv(
                agent_b_csv,
                [
                    {
                        "provider_id": "p-risk",
                        "provider_name": "Risk Agency",
                        "review_reason": "precision_low_confidence_auto_match",
                        "candidate_url": "https://wrong.example/",
                        "candidate_domain": "wrong.example",
                        "agent_b_decision": "unsure",
                        "evidence_score": "65",
                        "supporting_facts": "candidate_pages_fetch_ok; page_contains_exact_provider_name",
                        "counter_evidence": "",
                        "reason_for_unsure": "insufficient_or_conflicting_evidence",
                    },
                    {
                        "provider_id": "p-safe",
                        "provider_name": "Safe Agency",
                        "review_reason": "precision_calibrated_pattern_release",
                        "candidate_url": "https://safe.example/",
                        "candidate_domain": "safe.example",
                        "agent_b_decision": "accept",
                        "evidence_score": "90",
                        "supporting_facts": "candidate_pages_fetch_ok; page_contains_exact_provider_name",
                        "counter_evidence": "",
                        "reason_for_unsure": "",
                    },
                ],
            )
            details_json.write_text(
                json.dumps(
                    {
                        "details": [
                            {
                                "provider_id": "p-risk",
                                "provider_name": "Risk Agency",
                                "expected_kind": "no_official",
                                "expected_url": "",
                                "expected_domain": "",
                            },
                            {
                                "provider_id": "p-safe",
                                "provider_name": "Safe Agency",
                                "expected_kind": "official",
                                "expected_url": "https://safe.example/",
                                "expected_domain": "safe.example",
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            _write_test_csv(
                cases_csv,
                [
                    {
                        "case_type": "precision_blocking_fixture",
                        "provider_id": "p-risk",
                        "provider_name": "Risk Agency",
                        "review_reason": "precision_low_confidence_auto_match",
                        "candidate_url": "https://wrong.example/",
                        "official_url": "https://wrong.example/",
                        "expected_url": "",
                        "assertion": "candidate_url_or_official_url_must_not_auto_accept",
                        "notes": "",
                    }
                ],
            )

            report = simulate_review_lane_output_policy(
                final_csv=final_csv,
                review_task_csv=review_csv,
                hold_review_reasons=["precision_low_confidence_auto_match"],
                output_csv=output_csv,
                output_xlsx=output_xlsx,
                labeled_details=details_json,
                cases_csv=cases_csv,
                summary_json=summary_json,
                summary_md=summary_md,
            )
            with output_csv.open(newline="", encoding="utf-8-sig") as f:
                rows = {row["provider_id"]: row for row in csv.DictReader(f)}
            saved = json.loads(summary_json.read_text(encoding="utf-8"))
            md_text = summary_md.read_text(encoding="utf-8")
            output_xlsx_exists = output_xlsx.exists()
            pattern_report = simulate_review_lane_output_policy(
                final_csv=final_csv,
                review_task_csv=review_csv,
                hold_review_reasons=[],
                hold_patterns=["review_reason:precision_low_confidence_auto_match AND has:candidate_pages_fetch_ok"],
                agent_b_csv=agent_b_csv,
                output_csv=pattern_csv,
                labeled_details=details_json,
                cases_csv=cases_csv,
                summary_json=pattern_summary_json,
            )
            with pattern_csv.open(newline="", encoding="utf-8-sig") as f:
                pattern_rows = {row["provider_id"]: row for row in csv.DictReader(f)}

        self.assertEqual(rows["p-risk"]["official_url"], "")
        self.assertEqual(rows["p-risk"]["status"], "needs_review")
        self.assertEqual(rows["p-risk"]["source_status"], "precision_low_confidence_auto_match")
        self.assertEqual(rows["p-safe"]["official_url"], "https://safe.example/")
        self.assertEqual(report["summary"]["held_rows"], 1)
        self.assertEqual(report["summary"]["balance_overall"]["false_official_rows"], 0)
        self.assertEqual(report["summary"]["balance_overall"]["overall_accuracy"], 1.0)
        self.assertEqual(report["summary"]["regression_gate_summary"]["gate_status"], "pass")
        self.assertEqual(saved["summary"]["held_provider_ids"], ["p-risk"])
        self.assertTrue(output_xlsx_exists)
        self.assertIn("Review Lane Output Policy Simulation", md_text)
        self.assertEqual(pattern_rows["p-risk"]["official_url"], "")
        self.assertEqual(pattern_rows["p-safe"]["official_url"], "https://safe.example/")
        self.assertEqual(pattern_report["summary"]["held_rows"], 1)
        self.assertEqual(
            pattern_report["summary"]["held_pattern_counts"],
            {"has:candidate_pages_fetch_ok AND review_reason:precision_low_confidence_auto_match": 1},
        )
        self.assertEqual(pattern_report["summary"]["regression_gate_summary"]["gate_status"], "pass")

    def test_build_policy_validation_task_outputs_unlabeled_rule_impact_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            final_csv = root / "official_sites.csv"
            review_csv = root / "review_task.csv"
            agent_b_csv = root / "agent_b.csv"
            details_json = root / "details.json"
            release_json = root / "release_pattern.json"
            output_csv = root / "policy_validation.csv"
            output_xlsx = root / "policy_validation.xlsx"
            summary_json = root / "policy_validation.json"
            summary_md = root / "policy_validation.md"
            base_final_row = {
                "provider_id": "",
                "provider_name": "",
                "provider_detail_url": "",
                "official_url": "",
                "official_domain": "",
                "status": "",
                "decision_source": "",
                "confidence": "",
                "source_status": "",
                "evidence_summary": "",
                "candidate_count": "1",
                "scored_candidate_count": "1",
                "service_apis": "",
                "provider_locations": "",
                "notes": "",
            }
            final_rows = []
            for provider_id, name, url, domain, status in [
                ("p-hold-labeled", "Hold Labeled", "https://wrong.example/", "wrong.example", "matched"),
                ("p-hold-unlabeled", "Hold Unlabeled", "https://maybe.example/", "maybe.example", "matched"),
                ("p-release-labeled", "Release Labeled", "", "", "unresolved"),
                ("p-release-unlabeled", "Release Unlabeled", "", "", "unresolved"),
            ]:
                row = dict(base_final_row)
                row.update(
                    {
                        "provider_id": provider_id,
                        "provider_name": name,
                        "provider_detail_url": f"https://amazon.example/{provider_id}",
                        "official_url": url,
                        "official_domain": domain,
                        "status": status,
                    }
                )
                final_rows.append(row)
            _write_test_csv(final_csv, final_rows)
            _write_test_csv(
                review_csv,
                [
                    {
                        "provider_id": "p-hold-labeled",
                        "provider_name": "Hold Labeled",
                        "provider_detail_url": "https://amazon.example/p-hold-labeled",
                        "review_reason": "precision_low_confidence_auto_match",
                        "service_apis": "Listings",
                        "provider_locations": "DE",
                    },
                    {
                        "provider_id": "p-hold-unlabeled",
                        "provider_name": "Hold Unlabeled",
                        "provider_detail_url": "https://amazon.example/p-hold-unlabeled",
                        "review_reason": "precision_low_confidence_auto_match",
                        "service_apis": "Listings",
                        "provider_locations": "DE",
                    },
                    {
                        "provider_id": "p-release-labeled",
                        "provider_name": "Release Labeled",
                        "provider_detail_url": "https://amazon.example/p-release-labeled",
                        "review_reason": "recall_unresolved_top_candidate",
                        "service_apis": "Listings",
                        "provider_locations": "US",
                    },
                    {
                        "provider_id": "p-release-unlabeled",
                        "provider_name": "Release Unlabeled",
                        "provider_detail_url": "https://amazon.example/p-release-unlabeled",
                        "review_reason": "recall_unresolved_top_candidate",
                        "service_apis": "Listings",
                        "provider_locations": "US",
                    },
                ],
            )
            _write_test_csv(
                agent_b_csv,
                [
                    {
                        "provider_id": "p-hold-labeled",
                        "provider_name": "Hold Labeled",
                        "provider_detail_url": "https://amazon.example/p-hold-labeled",
                        "review_reason": "precision_low_confidence_auto_match",
                        "candidate_url": "https://wrong.example/",
                        "candidate_domain": "wrong.example",
                        "agent_b_decision": "unsure",
                        "confidence": "68",
                        "evidence_score": "68",
                        "supporting_facts": "candidate_pages_fetch_ok; page_contains_exact_provider_name",
                        "counter_evidence": "",
                        "reason_for_unsure": "insufficient_or_conflicting_evidence",
                    },
                    {
                        "provider_id": "p-hold-unlabeled",
                        "provider_name": "Hold Unlabeled",
                        "provider_detail_url": "https://amazon.example/p-hold-unlabeled",
                        "review_reason": "precision_low_confidence_auto_match",
                        "candidate_url": "https://maybe.example/",
                        "candidate_domain": "maybe.example",
                        "agent_b_decision": "unsure",
                        "confidence": "68",
                        "evidence_score": "68",
                        "supporting_facts": "candidate_pages_fetch_ok; page_contains_exact_provider_name",
                        "counter_evidence": "",
                        "reason_for_unsure": "insufficient_or_conflicting_evidence",
                    },
                    {
                        "provider_id": "p-release-labeled",
                        "provider_name": "Release Labeled",
                        "provider_detail_url": "https://amazon.example/p-release-labeled",
                        "review_reason": "recall_unresolved_top_candidate",
                        "candidate_url": "https://releaselabeled.example/",
                        "candidate_domain": "releaselabeled.example",
                        "agent_b_decision": "unsure",
                        "confidence": "48",
                        "evidence_score": "48",
                        "supporting_facts": "page_contains_exact_provider_name",
                        "counter_evidence": "",
                        "reason_for_unsure": "",
                    },
                    {
                        "provider_id": "p-release-unlabeled",
                        "provider_name": "Release Unlabeled",
                        "provider_detail_url": "https://amazon.example/p-release-unlabeled",
                        "review_reason": "recall_unresolved_top_candidate",
                        "candidate_url": "https://releaseunlabeled.example/",
                        "candidate_domain": "releaseunlabeled.example",
                        "agent_b_decision": "unsure",
                        "confidence": "48",
                        "evidence_score": "48",
                        "supporting_facts": "page_contains_exact_provider_name",
                        "counter_evidence": "",
                        "reason_for_unsure": "",
                    },
                ],
            )
            details_json.write_text(
                json.dumps(
                    {
                        "details": [
                            {
                                "provider_id": "p-hold-labeled",
                                "provider_name": "Hold Labeled",
                                "expected_kind": "no_official",
                                "expected_url": "",
                                "expected_domain": "",
                            },
                            {
                                "provider_id": "p-release-labeled",
                                "provider_name": "Release Labeled",
                                "expected_kind": "official",
                                "expected_url": "https://releaselabeled.example/",
                                "expected_domain": "releaselabeled.example",
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            release_json.write_text(
                json.dumps(
                    {
                        "candidate_for_rule": [
                            {
                                "pattern": "review_reason:recall_unresolved_top_candidate AND has:page_contains_exact_provider_name AND agent_b_score>=45 AND agent_b_score<50",
                                "features": [
                                    "review_reason:recall_unresolved_top_candidate",
                                    "has:page_contains_exact_provider_name",
                                    "agent_b_score>=45",
                                    "agent_b_score<50",
                                ],
                                "supporting_rows": 1,
                                "blocking_rows": 0,
                                "actionable": True,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            report = build_policy_validation_task(
                final_csv=final_csv,
                review_task_csv=review_csv,
                agent_b_csv=agent_b_csv,
                hold_patterns=["review_reason:precision_low_confidence_auto_match AND has:candidate_pages_fetch_ok"],
                release_pattern_jsons=[release_json],
                labeled_details=[details_json],
                output_csv=output_csv,
                output_xlsx=output_xlsx,
                summary_json=summary_json,
                summary_md=summary_md,
            )
            with output_csv.open(newline="", encoding="utf-8-sig") as f:
                rows = {row["provider_id"]: row for row in csv.DictReader(f)}
            saved = json.loads(summary_json.read_text(encoding="utf-8"))
            md_text = summary_md.read_text(encoding="utf-8")
            with zipfile.ZipFile(output_xlsx) as z:
                sheet_text = z.read("xl/worksheets/sheet1.xml").decode("utf-8")

        self.assertEqual(report["summary"]["matched_candidate_rows"], 4)
        self.assertEqual(report["summary"]["output_rows"], 2)
        self.assertEqual(report["summary"]["skipped_labeled_rows"], 2)
        self.assertEqual(report["summary"]["action_counts"], {"holdout": 1, "release": 1})
        self.assertEqual(set(rows), {"p-hold-unlabeled", "p-release-unlabeled"})
        self.assertEqual(rows["p-hold-unlabeled"]["manual_decision"], "")
        self.assertEqual(rows["p-release-unlabeled"]["candidate_url"], "https://releaseunlabeled.example/")
        self.assertEqual(rows["p-release-unlabeled"]["provider_detail_url"], "https://amazon.example/p-release-unlabeled")
        self.assertEqual(saved["summary"]["known_label_counts"], {"unlabeled": 2})
        self.assertIn("Policy Validation Task", md_text)
        self.assertIn("HYPERLINK", sheet_text)

    def test_evaluate_policy_validation_task_summarizes_support_and_blockers(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_csv = root / "policy_validation.csv"
            task_xlsx = root / "policy_validation.xlsx"
            output_json = root / "policy_eval.json"
            output_md = root / "policy_eval.md"
            output_csv = root / "policy_eval_details.csv"
            _write_test_csv(
                task_csv,
                [
                    {
                        "provider_id": "p-release-good",
                        "provider_name": "Release Good",
                        "provider_detail_url": "https://amazon.example/good",
                        "candidate_policy_action": "release",
                        "candidate_policy_pattern": "review_reason:recall_unresolved_top_candidate AND has:page_contains_exact_provider_name",
                        "candidate_policy_source": "release_pattern",
                        "candidate_url": "https://releasegood.example/",
                        "review_reason": "recall_unresolved_top_candidate",
                        "agent_b_decision": "unsure",
                        "known_label_status": "unlabeled",
                        "manual_decision": "accept",
                        "manual_url": "",
                        "notes": "Correct official site.",
                    },
                    {
                        "provider_id": "p-hold-good",
                        "provider_name": "Hold Good",
                        "provider_detail_url": "https://amazon.example/hold-good",
                        "candidate_policy_action": "holdout",
                        "candidate_policy_pattern": "review_reason:precision_low_confidence_auto_match AND has:candidate_pages_fetch_ok",
                        "candidate_policy_source": "hold_pattern",
                        "candidate_url": "https://holdgood.example/",
                        "review_reason": "precision_low_confidence_auto_match",
                        "agent_b_decision": "unsure",
                        "known_label_status": "unlabeled",
                        "manual_decision": "reject",
                        "manual_url": "",
                        "notes": "Wrong current official site.",
                    },
                    {
                        "provider_id": "p-release-bad",
                        "provider_name": "Release Bad",
                        "provider_detail_url": "https://amazon.example/bad",
                        "candidate_policy_action": "release",
                        "candidate_policy_pattern": "review_reason:recall_unresolved_top_candidate AND has:schema_org_organization_seen",
                        "candidate_policy_source": "release_pattern",
                        "candidate_url": "https://wrong.example/",
                        "review_reason": "recall_unresolved_top_candidate",
                        "agent_b_decision": "unsure",
                        "known_label_status": "unlabeled",
                        "manual_decision": "replace",
                        "manual_url": "https://right.example/",
                        "notes": "Different true official site.",
                    },
                    {
                        "provider_id": "p-invalid",
                        "provider_name": "Invalid",
                        "provider_detail_url": "https://amazon.example/invalid",
                        "candidate_policy_action": "release",
                        "candidate_policy_pattern": "review_reason:recall_unresolved_top_candidate AND has:contact_email_found",
                        "candidate_policy_source": "release_pattern",
                        "candidate_url": "https://invalid.example/",
                        "review_reason": "recall_unresolved_top_candidate",
                        "agent_b_decision": "unsure",
                        "known_label_status": "unlabeled",
                        "manual_decision": "maybe",
                        "manual_url": "",
                        "notes": "",
                    },
                ],
            )
            build_workbook([("Policy", task_csv)], task_xlsx)

            report = evaluate_policy_validation_task(
                task=task_xlsx,
                output_json=output_json,
                output_md=output_md,
                output_csv=output_csv,
            )
            saved = json.loads(output_json.read_text(encoding="utf-8"))
            md_text = output_md.read_text(encoding="utf-8")
            with output_csv.open(newline="", encoding="utf-8-sig") as f:
                rows = {row["provider_id"]: row for row in csv.DictReader(f)}

        self.assertEqual(report["summary"]["task_rows"], 4)
        self.assertEqual(report["summary"]["labeled_rows"], 3)
        self.assertEqual(report["summary"]["decisive_rows"], 3)
        self.assertEqual(report["summary"]["support_rows"], 2)
        self.assertEqual(report["summary"]["blocking_rows"], 1)
        self.assertEqual(report["summary"]["invalid_manual_decision_rows"], 1)
        self.assertEqual(saved["summary"]["reject_pattern_rows"], 1)
        self.assertEqual(rows["p-release-good"]["policy_outcome"], "release_supported")
        self.assertEqual(rows["p-hold-good"]["policy_outcome"], "holdout_supported")
        self.assertEqual(rows["p-release-bad"]["policy_outcome"], "release_blocked")
        self.assertEqual(rows["p-invalid"]["decision_quality_issue"], "invalid_manual_decision")
        self.assertIn("Policy Validation Evaluation", md_text)

    def test_verify_protected_lane_review_task_checks_links_summary_and_manual_blanks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_csv = root / "protected.csv"
            task_xlsx = root / "protected.xlsx"
            summary_json = root / "summary.json"
            output_json = root / "verification.json"
            rows = [
                {
                    "provider_id": "p1",
                    "provider_name": "Provider One",
                    "provider_detail_url": "https://amazon.example/p1",
                    "review_reason": "precision_low_confidence_auto_match",
                    "candidate_url": "https://providerone.example",
                    "official_url": "https://providerone.example",
                    "manual_decision": "",
                    "manual_url": "",
                    "notes": "",
                    "review_instruction": "Fill manual_decision.",
                    "optimization_use": "Use as precision fixture.",
                },
                {
                    "provider_id": "p2",
                    "provider_name": "Provider Two",
                    "provider_detail_url": "https://amazon.example/p2",
                    "review_reason": "recall_unresolved_top_candidate",
                    "candidate_url": "https://providertwo.example",
                    "official_url": "",
                    "manual_decision": "",
                    "manual_url": "",
                    "notes": "",
                    "review_instruction": "Fill manual_decision.",
                    "optimization_use": "Use as recall evidence.",
                },
            ]
            _write_test_csv(task_csv, rows)
            build_workbook([("Protected", task_csv)], task_xlsx)
            summary_json.write_text(
                json.dumps(
                    {
                        "task_rows": 2,
                        "reason_counts": {
                            "precision_low_confidence_auto_match": 1,
                            "recall_unresolved_top_candidate": 1,
                        },
                    }
                ),
                encoding="utf-8",
            )

            report = verify_protected_lane_review_task(
                csv_path=task_csv,
                summary_json=summary_json,
                xlsx_path=task_xlsx,
                output_json=output_json,
            )
            rows[1]["manual_decision"] = "accept"
            rows.append(dict(rows[0]))
            bad_csv = root / "bad_protected.csv"
            _write_test_csv(bad_csv, rows)
            bad_report = verify_protected_lane_review_task(
                csv_path=bad_csv,
                summary_json=summary_json,
                xlsx_path=task_xlsx,
            )
            allow_filled_report = verify_protected_lane_review_task(
                csv_path=bad_csv,
                summary_json=summary_json,
                xlsx_path=task_xlsx,
                allow_filled=True,
            )
            filled_rows = [dict(row) for row in rows[:2]]
            filled_rows[0]["manual_decision"] = "replace"
            filled_rows[0]["manual_url"] = ""
            filled_rows[1]["manual_decision"] = "maybe"
            filled_bad_csv = root / "filled_bad.csv"
            _write_test_csv(filled_bad_csv, filled_rows)
            filled_bad_report = verify_protected_lane_review_task(
                csv_path=filled_bad_csv,
                summary_json=summary_json,
                xlsx_path=task_xlsx,
                allow_filled=True,
                require_filled=True,
            )
            filled_rows[0]["manual_url"] = "https://replacement.example"
            filled_rows[1]["manual_decision"] = "accept"
            filled_good_csv = root / "filled_good.csv"
            filled_good_xlsx = root / "filled_good.xlsx"
            _write_test_csv(filled_good_csv, filled_rows)
            build_workbook([("Filled", filled_good_csv)], filled_good_xlsx)
            filled_good_report = verify_protected_lane_review_task(
                csv_path=filled_good_csv,
                summary_json=summary_json,
                xlsx_path=task_xlsx,
                allow_filled=True,
                require_filled=True,
            )
            filled_good_xlsx_report = verify_protected_lane_review_task(
                csv_path=filled_good_xlsx,
                summary_json=summary_json,
                xlsx_path=filled_good_xlsx,
                allow_filled=True,
                require_filled=True,
            )
            saved = json.loads(output_json.read_text(encoding="utf-8"))

        self.assertTrue(report["summary"]["passed"])
        self.assertEqual(report["summary"]["row_count"], 2)
        self.assertGreater(report["summary"]["xlsx_hyperlink_formula_count"], 0)
        self.assertEqual(report["summary"]["blank_manual_decision_rows"], 2)
        self.assertEqual(report["summary"]["invalid_manual_decision_rows"], 0)
        self.assertEqual(report["summary"]["replace_missing_manual_url_rows"], 0)
        self.assertEqual(saved["summary"]["failure_count"], 0)
        self.assertFalse(bad_report["summary"]["passed"])
        self.assertGreaterEqual(bad_report["summary"]["duplicate_key_count"], 1)
        self.assertGreaterEqual(bad_report["summary"]["filled_manual_decision_rows"], 1)
        self.assertFalse(allow_filled_report["summary"]["passed"])
        self.assertTrue(any(item["check"] == "duplicate_provider_reason" for item in allow_filled_report["failures"]))
        self.assertFalse(filled_bad_report["summary"]["passed"])
        self.assertEqual(filled_bad_report["summary"]["invalid_manual_decision_rows"], 1)
        self.assertEqual(filled_bad_report["summary"]["replace_missing_manual_url_rows"], 1)
        self.assertTrue(any(item["check"] == "invalid_manual_decision" for item in filled_bad_report["failures"]))
        self.assertTrue(any(item["check"] == "replace_missing_manual_url" for item in filled_bad_report["failures"]))
        self.assertTrue(filled_good_report["summary"]["passed"])
        self.assertEqual(filled_good_report["summary"]["blank_manual_decision_rows"], 0)
        self.assertTrue(filled_good_xlsx_report["summary"]["passed"])
        self.assertEqual(filled_good_xlsx_report["summary"]["blank_manual_decision_rows"], 0)
        self.assertGreater(filled_good_xlsx_report["summary"]["xlsx_hyperlink_formula_count"], 0)

OperationalCommandTests.__module__ = "test_workflow"
