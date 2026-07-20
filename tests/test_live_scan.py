"""Phase 3 tests: the review model (review.py) and the option-B live scan (live_scan.py).

The e2e test is fully hermetic — it mocks both LLM boundaries and uses a temp history — and
asserts the whole flow: extract -> validate -> write_candidates -> promote_candidates ->
detect against the source_url-excluded prior, with BOTH new_zone AND surge firing. That is the
whole point of choosing option B over "promote then scan literally" (C): once the report's rows
are in history, scanning against a source_url-excluded prior keeps the emerging/accelerating
clusters visible.
"""
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

from src.live.extract_report import _ExtractedRow, _ExtractionPayload, extract_report
from src.live.validate_extraction import _Verdict, _ValidationPayload, validate_extraction
from src.live.candidate_store import write_candidates, promote_candidates, candidate_id
from src.live.live_scan import detect_new_data, prior_excluding_source
from src.live.review import build_review, human_reason, ReviewModel

# A report body whose sentences are copied verbatim into the snippets (so the guards pass).
NEW_BODY = (
    "Ebola situation update, data as of 15 July 2026.\n"
    "Beni health zone: 300 confirmed cases and 60 deaths as of 15 July 2026.\n"
    "Mongbwalu health zone: 80 confirmed cases and 10 deaths as of 15 July 2026.\n"
)
NEW_URL = "http://reliefweb/new-sitrep"
OLD_URL = "http://reliefweb/old-sitrep"
DID = 52586  # active outbreak (DRC Ebola 2026)


def _prior_history_csv(path: str) -> None:
    """One prior row: Beni at 100 confirmed on 2026-07-08, from a DIFFERENT report."""
    pd.DataFrame([{
        "disaster_id": DID,
        "date": "2026-07-08", "province": "Nord-Kivu", "health_zone": "Beni",
        "suspected_cases": 20, "confirmed_cases": 100, "deaths": 25,
        "source_url": OLD_URL, "report_date": "2026-07-08",
    }]).to_csv(path, index=False)


class TestReviewModel(unittest.TestCase):
    def test_human_reason_maps_the_invented_quote_slug(self):
        msg = human_reason("snippet_not_verbatim")
        self.assertIn("verbatim", msg)
        self.assertIn("invented", msg)
        # unknown slugs pass through rather than blow up
        self.assertEqual(human_reason("weird"), "weird")

    @patch("src.live.validate_extraction._call_validation_llm")
    @patch("src.live.extract_report._call_extraction_llm")
    def test_build_review_splits_approvable_from_rejected(self, mock_e, mock_v):
        body = (
            "Beni health zone reported 120 confirmed cases and 30 deaths.\n"
            "DR Congo reported 1926 confirmed cases and 702 deaths nationally.\n"
        )
        mock_e.return_value = _ExtractionPayload(records=[
            _ExtractedRow(date="2026-07-15", province="Nord-Kivu", health_zone="Beni",
                          confirmed_cases=120, deaths=30,
                          snippet="Beni health zone reported 120 confirmed cases and 30 deaths."),
            # national total, no health zone -> dropped by the extraction guard (rejected card)
            _ExtractedRow(date="2026-07-15", province="", health_zone="",
                          confirmed_cases=1926, deaths=702,
                          snippet="DR Congo reported 1926 confirmed cases and 702 deaths nationally."),
        ])
        extraction = extract_report(body, NEW_URL, "2026-07-16", DID)
        self.assertEqual(len(extraction.records), 1)          # only Beni survives the guards
        self.assertTrue(extraction.dropped)                    # national line dropped

        mock_v.return_value = _ValidationPayload(verdicts=[_Verdict(index=0, verdict="PASS")])
        validation = validate_extraction(extraction.records, body)

        model = build_review(extraction, validation)
        self.assertIsInstance(model, ReviewModel)
        self.assertEqual(model.approvable_count, 1)
        self.assertEqual(model.approvable[0].record["health_zone"], "Beni")
        self.assertTrue(model.approvable[0].candidate_id)      # has an id for promotion
        self.assertGreaterEqual(model.rejected_count, 1)       # the national line is rejected
        self.assertFalse(model.is_empty)

    def test_build_review_empty_when_nothing_extracted(self):
        class _Empty:
            records = []
            dropped = []
        model = build_review(_Empty(), None)
        self.assertTrue(model.is_empty)

    @patch("src.live.extract_report._call_extraction_llm")
    def test_non_verbatim_snippet_renders_as_rejected_reason(self, mock_e):
        # Req 3: the deterministic guard routes a non-verbatim snippet into the rejected bucket
        # with a human-readable reason that the card can show.
        body = "Beni health zone reported 120 confirmed cases."
        mock_e.return_value = _ExtractionPayload(records=[
            _ExtractedRow(date="2026-07-15", province="Nord-Kivu", health_zone="Beni",
                          confirmed_cases=120, deaths=30,
                          snippet="Beni reported one hundred and twenty confirmed cases."),
        ])
        extraction = extract_report(body, NEW_URL, "2026-07-16", DID)
        self.assertEqual(len(extraction.records), 0)
        model = build_review(extraction, None)
        self.assertEqual(model.approvable_count, 0)
        self.assertEqual(model.rejected_count, 1)
        self.assertIn("verbatim", model.rejected[0].reason)


class TestOptionBScan(unittest.TestCase):
    def test_prior_excludes_rows_by_source_url(self):
        tmp = tempfile.mkdtemp()
        hist = os.path.join(tmp, "history.csv")
        try:
            pd.DataFrame([
                {"disaster_id": DID, "date": "2026-07-08", "province": "Nord-Kivu", "health_zone": "Beni",
                 "suspected_cases": 20, "confirmed_cases": 100, "deaths": 25,
                 "source_url": OLD_URL, "report_date": "2026-07-08"},
                {"disaster_id": DID, "date": "2026-07-15", "province": "Nord-Kivu", "health_zone": "Beni",
                 "suspected_cases": 40, "confirmed_cases": 300, "deaths": 60,
                 "source_url": NEW_URL, "report_date": "2026-07-16"},
            ]).to_csv(hist, index=False)
            prior = prior_excluding_source(hist, NEW_URL, DID)
            # the NEW_URL row is excluded; only the OLD_URL Beni@100 remains
            self.assertEqual(len(prior), 1)
            self.assertEqual(prior[0]["confirmed_cases"], 100)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    @patch("src.live.validate_extraction._call_validation_llm")
    @patch("src.live.extract_report._call_extraction_llm")
    def test_e2e_new_zone_and_surge_both_fire_after_promotion(self, mock_e, mock_v):
        tmp = tempfile.mkdtemp()
        hist = os.path.join(tmp, "history.csv")
        cand = os.path.join(tmp, "candidate_history.csv")
        try:
            _prior_history_csv(hist)

            # 1. extract two per-zone records from the new report
            mock_e.return_value = _ExtractionPayload(records=[
                _ExtractedRow(date="2026-07-15", province="Nord-Kivu", health_zone="Beni",
                              confirmed_cases=300, deaths=60,
                              snippet="Beni health zone: 300 confirmed cases and 60 deaths as of 15 July 2026."),
                _ExtractedRow(date="2026-07-15", province="Ituri", health_zone="Mongbwalu",
                              confirmed_cases=80, deaths=10,
                              snippet="Mongbwalu health zone: 80 confirmed cases and 10 deaths as of 15 July 2026."),
            ])
            extraction = extract_report(NEW_BODY, NEW_URL, "2026-07-16", DID)
            self.assertEqual(len(extraction.records), 2)

            # 2. independent validation passes both
            mock_v.return_value = _ValidationPayload(verdicts=[
                _Verdict(index=0, verdict="PASS"), _Verdict(index=1, verdict="PASS"),
            ])
            validation = validate_extraction(extraction.records, NEW_BODY)
            self.assertEqual(len(validation.validated), 2)

            # 3. stage + human promotion into the temp history
            write_candidates(validation.validated, cand)
            cids = [candidate_id(r) for r in validation.validated]
            result = promote_candidates(cids, cand, hist)
            self.assertEqual(len(result.promoted), 2)
            self.assertEqual(result.rejected, [])

            # history now holds the old Beni@100 AND both promoted NEW_URL rows
            self.assertEqual(len(pd.read_csv(hist)), 3)

            # 4. option B: scan the report's records against the source_url-excluded prior
            flags = detect_new_data(validation.validated, NEW_URL, hist, DID)
            by_detector = {(f["detector"], f["health_zone"]) for f in flags}

            # new_zone fires for Mongbwalu (absent from the source-excluded prior)
            self.assertIn(("new_zone", "Mongbwalu"), by_detector)
            # surge fires for Beni (100 -> 300 over 7 days), still visible post-promotion
            self.assertIn(("surge", "Beni"), by_detector)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
