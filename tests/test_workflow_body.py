from support import *

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
        self.assertFalse(legacy_task_exists)

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
        self.assertFalse(legacy_xlsx_exists)

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
        self.assertFalse(legacy_final_exists)
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

    def test_second_pass_sibling_candidate_cap_does_not_block_clean_winner(self):
        # Regression: a clean high-confidence selected candidate must not be rejected
        # just because an unselected sibling candidate for the same provider carries an
        # identity cap. (Previously _has_blocking_identity_cap scanned every candidate.)
        config = load_config()
        result = {
            "official_url": "https://www.embarcconsulting.com/",
            "status": "matched",
            "confidence": "100",
            "evidence_summary": "page_contains_exact_provider_name; page_contains_amazon_service_keywords; domain_exact_provider_slug",
            "candidates": [
                {
                    "url": "https://www.embarcconsulting.com/",
                    "reject": False,
                    "source": "brave",
                    "reasons": [
                        "page_contains_exact_provider_name",
                        "page_contains_amazon_service_keywords",
                        "domain_exact_provider_slug",
                    ],
                },
                {
                    "url": "https://some-other-samename.com/",
                    "reject": False,
                    "source": "brave",
                    "reasons": ["identity_cap_ambiguous_name_requires_page_and_service"],
                },
            ],
        }
        self.assertTrue(_accepted(result, config, DEFAULT_SECOND_PASS_ACCEPT_THRESHOLD))

    def test_second_pass_selected_candidate_cap_still_blocks(self):
        # The selected official candidate's own identity cap must still block acceptance.
        config = load_config()
        result = {
            "official_url": "https://www.ambiguousname.com/",
            "status": "matched",
            "confidence": "100",
            "evidence_summary": "identity_cap_ambiguous_name_requires_page_and_service; page_contains_exact_provider_name",
            "candidates": [
                {
                    "url": "https://www.ambiguousname.com/",
                    "reject": False,
                    "source": "brave",
                    "reasons": [
                        "identity_cap_ambiguous_name_requires_page_and_service",
                        "page_contains_exact_provider_name",
                    ],
                }
            ],
        }
        self.assertFalse(_accepted(result, config, DEFAULT_SECOND_PASS_ACCEPT_THRESHOLD))

    def test_build_visual_verification_task_selects_uncertain_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            final_rows = [
                {"provider_id": "p-clean", "provider_name": "Bright Ledger Labs", "official_url": "https://brightledgerlabs.com/", "official_domain": "brightledgerlabs.com", "status": "matched", "confidence": "100", "evidence_summary": "page_contains_exact_provider_name; domain_exact_provider_slug", "provider_detail_url": "https://amazon/x", "listing_logo_url": "https://logo/clean.png"},
                {"provider_id": "p-samename", "provider_name": "Amazon Seller Agency", "official_url": "https://amazonselleragency.com/", "official_domain": "amazonselleragency.com", "status": "matched", "confidence": "100", "evidence_summary": "domain_exact_provider_slug", "provider_detail_url": "https://amazon/y", "listing_logo_url": "https://logo/sn.png"},
                {"provider_id": "p-lowconf", "provider_name": "Brightpeak Mercantile Atlas", "official_url": "https://brightpeak.com/", "official_domain": "brightpeak.com", "status": "matched", "confidence": "80", "evidence_summary": "domain_contains_provider_slug", "provider_detail_url": "https://amazon/z", "listing_logo_url": "https://logo/lc.png"},
                {"provider_id": "p-unresolved-cand", "provider_name": "Northwind Trading House", "official_url": "", "official_domain": "", "status": "unresolved", "confidence": "", "evidence_summary": "", "provider_detail_url": "https://amazon/u", "listing_logo_url": "https://logo/u.png"},
                {"provider_id": "p-unresolved-nocand", "provider_name": "Silverbrook Holdings", "official_url": "", "official_domain": "", "status": "not_found", "confidence": "", "evidence_summary": "", "provider_detail_url": "https://amazon/n", "listing_logo_url": ""},
            ]
            _write_test_csv(run_dir / "official_sites.csv", final_rows)
            sp_rows = [
                {"provider_id": "p-unresolved-cand", "provider_name": "Northwind Trading House", "previous_top_candidate_url": "https://northwindtrading.com/", "official_url": "", "evidence_summary": ""},
                {"provider_id": "p-unresolved-nocand", "provider_name": "Silverbrook Holdings", "previous_top_candidate_url": "", "official_url": "", "evidence_summary": ""},
            ]
            _write_test_csv(run_dir / "details/second_pass/results.csv", sp_rows)

            summary = build_visual_verification_task(run_dir=run_dir, render=False, write_xlsx=False)
            with (run_dir / "visual_verification/visual_verification_task.csv").open(encoding="utf-8-sig") as f:
                rows = list(csv.DictReader(f))
            by_id = {row["provider_id"]: row for row in rows}

            self.assertIn("p-samename", by_id)
            self.assertIn("p-lowconf", by_id)
            self.assertIn("p-unresolved-cand", by_id)
            self.assertNotIn("p-clean", by_id)
            self.assertNotIn("p-unresolved-nocand", by_id)
            self.assertEqual(by_id["p-samename"]["review_reason"], "precision_same_name_risk")
            self.assertEqual(by_id["p-lowconf"]["review_reason"], "precision_low_confidence_accept")
            self.assertEqual(by_id["p-unresolved-cand"]["review_reason"], "recall_unresolved_candidate")
            self.assertEqual(by_id["p-unresolved-cand"]["official_url"], "https://northwindtrading.com/")
            self.assertFalse(summary["renderer_available"])
            self.assertEqual(summary["rendered_screenshots"], 0)

    def test_apply_visual_verification_overwrites_canonical_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            enriched = [
                {"provider_id": "p-clean", "provider_name": "Bright Ledger Labs", "status": "matched", "official_url": "https://brightledgerlabs.com/", "official_domain": "brightledgerlabs.com", "confidence": "100", "evidence_summary": "page_contains_exact_provider_name", "provider_detail_url": "https://amazon/x", "listing_logo_url": "", "candidate_count": "3", "scored_candidate_count": "3", "service_apis": "[]", "provider_locations": "[]"},
                {"provider_id": "p-samename", "provider_name": "Amazon Seller Agency", "status": "needs_review", "official_url": "", "official_domain": "", "confidence": "100", "evidence_summary": "domain_exact_provider_slug", "provider_detail_url": "https://amazon/y", "listing_logo_url": "", "candidate_count": "4", "scored_candidate_count": "4", "service_apis": "[]", "provider_locations": "[]"},
                {"provider_id": "p-unresolved-cand", "provider_name": "Northwind Trading House", "status": "not_found", "official_url": "", "official_domain": "", "confidence": "", "evidence_summary": "", "provider_detail_url": "https://amazon/u", "listing_logo_url": "", "candidate_count": "2", "scored_candidate_count": "2", "service_apis": "[]", "provider_locations": "[]"},
            ]
            _write_test_csv(run_dir / "details/first_pass/enriched.csv", enriched)
            _write_test_csv(run_dir / "details/second_pass/decisions.csv", [
                {"provider_id": "p-samename", "provider_name": "Amazon Seller Agency", "manual_decision": "replace", "manual_url": "https://amazonselleragency.com/", "notes": "second_pass_auto_accept", "source_status": "matched", "evidence_summary": "domain_exact_provider_slug"},
            ])
            _write_test_csv(run_dir / "details/second_pass/results.csv", [
                {"provider_id": "p-unresolved-cand", "provider_name": "Northwind Trading House", "previous_top_candidate_url": "https://northwindtrading.com/", "official_url": "", "evidence_summary": ""},
            ])
            _write_test_csv(run_dir / "official_sites.csv", [
                {"provider_id": "p-clean", "provider_name": "Bright Ledger Labs", "provider_detail_url": "https://amazon/x", "listing_logo_url": "", "official_url": "https://brightledgerlabs.com/", "official_domain": "brightledgerlabs.com", "status": "matched", "decision_source": "auto_matched", "confidence": "100", "source_status": "matched", "evidence_summary": "page_contains_exact_provider_name", "candidate_count": "3", "scored_candidate_count": "3", "service_apis": "[]", "provider_locations": "[]", "notes": ""},
                {"provider_id": "p-samename", "provider_name": "Amazon Seller Agency", "provider_detail_url": "https://amazon/y", "listing_logo_url": "", "official_url": "https://amazonselleragency.com/", "official_domain": "amazonselleragency.com", "status": "manual_accepted", "decision_source": "manual_replace", "confidence": "100", "source_status": "needs_review", "evidence_summary": "domain_exact_provider_slug", "candidate_count": "4", "scored_candidate_count": "4", "service_apis": "[]", "provider_locations": "[]", "notes": ""},
                {"provider_id": "p-unresolved-cand", "provider_name": "Northwind Trading House", "provider_detail_url": "https://amazon/u", "listing_logo_url": "", "official_url": "", "official_domain": "", "status": "unresolved", "decision_source": "pending_review", "confidence": "", "source_status": "not_found", "evidence_summary": "", "candidate_count": "2", "scored_candidate_count": "2", "service_apis": "[]", "provider_locations": "[]", "notes": ""},
            ])
            (run_dir / "manifest.json").write_text(json.dumps({"parameters": {"total_to_run": 3}}), encoding="utf-8")

            verdict_csv = run_dir / "verdicts.csv"
            _write_test_csv(verdict_csv, [
                {"provider_id": "p-samename", "provider_name": "Amazon Seller Agency", "manual_decision": "reject", "manual_url": "", "official_url": "https://amazonselleragency.com/", "candidate_1_url": "https://amazonselleragency.com/", "notes": "wrong entity"},
                {"provider_id": "p-unresolved-cand", "provider_name": "Northwind Trading House", "manual_decision": "replace", "manual_url": "https://northwindtrading.com/", "official_url": "", "candidate_1_url": "https://northwindtrading.com/", "notes": "confirmed official site"},
            ])

            summary = apply_visual_verification(run_dir=run_dir, verdicts_path=verdict_csv, write_xlsx=False)

            with (run_dir / "official_sites.csv").open(encoding="utf-8-sig") as f:
                final = {row["provider_id"]: row for row in csv.DictReader(f)}
            self.assertEqual(final["p-samename"]["official_url"], "")
            self.assertEqual(final["p-samename"]["status"], "rejected")
            self.assertEqual(final["p-unresolved-cand"]["official_url"], "https://northwindtrading.com/")
            self.assertEqual(final["p-clean"]["official_url"], "https://brightledgerlabs.com/")
            self.assertEqual(summary["decision_counts"].get("reject"), 1)
            self.assertEqual(summary["decision_counts"].get("replace"), 1)
            self.assertTrue(Path(summary["outputs"]["combined_decisions"]).exists())

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

    def test_second_pass_rejects_subthreshold_token_only_name_match(self):
        config = load_config()
        token_only_name_match = {
            "official_url": "https://www.studiodelfiume.it/",
            "status": "needs_review",
            "confidence": "66",
            "evidence_summary": "domain_token_match:del,fiume; page_contains_provider_name_tokens; page_contains_some_service_keywords; search_result_contains_name_tokens; top_search_result",
            "candidates": [
                {
                    "url": "https://www.studiodelfiume.it/",
                    "reject": False,
                    "source": "exa",
                    "reasons": [
                        "domain_token_match:del,fiume",
                        "http_ok_home",
                        "page_contains_provider_name_tokens",
                        "page_contains_some_service_keywords",
                        "search_result_contains_name_tokens",
                        "top_search_result",
                    ],
                }
            ],
        }
        fuzzy_with_service = {
            "official_url": "https://www.expertsecretsacademy.com/",
            "status": "needs_review",
            "confidence": "66",
            "evidence_summary": "domain_token_match:experts,secrets,academy; page_fuzzy_provider_name_match; page_contains_amazon_service_keywords; search_result_contains_name_tokens; top_search_result",
            "candidates": [
                {
                    "url": "https://www.expertsecretsacademy.com/",
                    "reject": False,
                    "source": "second_pass_top_candidate",
                    "reasons": [
                        "domain_token_match:experts,secrets,academy",
                        "http_ok_home",
                        "page_fuzzy_provider_name_match",
                        "page_contains_amazon_service_keywords",
                        "search_result_contains_name_tokens",
                        "top_search_result",
                    ],
                }
            ],
        }

        self.assertFalse(_accepted(token_only_name_match, config, 75))
        self.assertTrue(_accepted(fuzzy_with_service, config, 75))

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
        self.assertEqual(report["scale_estimate"]["estimated_search_requests"], 5)

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

OperationalCommandTests.__module__ = "test_workflow"
