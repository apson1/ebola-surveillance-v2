import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

from src.config import GEMINI_API_KEY
from src.live.extract_report import (
    ExtractionResult, _ExtractedRow, _ExtractionPayload, extract_report,
)
from src.live.validate_extraction import _Verdict, _ValidationPayload, validate_extraction
from src.live.candidate_store import (
    write_candidates, promote_candidates, reset_candidates, candidate_id,
)

# A body whose sentences are copied verbatim into the crafted snippets below.
BODY = (
    "Situation update as of 15 July 2026.\n"
    "In Beni health zone, 120 confirmed cases and 30 deaths were reported.\n"
    "Bunia health zone recorded 200 confirmed cases and 45 deaths.\n"
    "Mongbwalu reported 80 confirmed cases.\n"
)


def _row(zone, confirmed, deaths, snippet, province="Ituri", date="2026-07-15"):
    return _ExtractedRow(date=date, province=province, health_zone=zone,
                         confirmed_cases=confirmed, deaths=deaths, snippet=snippet)


def _three_rows():
    return [
        _row("Beni", 120, 30, "In Beni health zone, 120 confirmed cases and 30 deaths were reported."),
        _row("Bunia", 200, 45, "Bunia health zone recorded 200 confirmed cases and 45 deaths."),
        _row("Mongbwalu", 80, None, "Mongbwalu reported 80 confirmed cases."),
    ]


class TestExtraction(unittest.TestCase):
    @patch("src.live.extract_report._call_extraction_llm")
    def test_extract_three_attributed_records_with_snippets(self, mock_llm):
        mock_llm.return_value = _ExtractionPayload(records=_three_rows())
        res = extract_report(BODY, "http://x/1", "2026-07-16")
        self.assertEqual(len(res.records), 3)
        for r in res.records:
            self.assertIn(r["snippet"], BODY)               # verbatim
            self.assertEqual(r["source_url"], "http://x/1")  # filled from args
            self.assertEqual(r["report_date"], "2026-07-16")

    @patch("src.live.extract_report._call_extraction_llm")
    def test_vague_body_returns_zero_records(self, mock_llm):
        mock_llm.return_value = _ExtractionPayload(records=[])
        res = extract_report("Cases are rising in the region; officials are concerned.", "http://x/2", "2026-07-16")
        self.assertEqual(len(res.records), 0)

    @patch("src.live.extract_report._call_extraction_llm")
    def test_national_total_is_dropped(self, mock_llm):
        # R1: the verified WHO national line, no health zone -> zero records.
        body = "DR Congo has now reported 1,926 confirmed cases and 702 deaths (CFR 36.5%)."
        mock_llm.return_value = _ExtractionPayload(records=[
            _ExtractedRow(date="2026-07-14", province="", health_zone="",
                          confirmed_cases=1926, deaths=702, snippet=body),
        ])
        res = extract_report(body, "http://x/3", "2026-07-14")
        self.assertEqual(len(res.records), 0)
        self.assertTrue(any(d["reason"] == "no_health_zone" for d in res.dropped))

    @patch("src.live.extract_report._call_extraction_llm")
    def test_non_verbatim_snippet_is_dropped(self, mock_llm):
        # R2: a plausible but invented (non-substring) snippet -> dropped.
        body = "Beni health zone reported 120 confirmed cases."
        mock_llm.return_value = _ExtractionPayload(records=[
            _row("Beni", 120, None, "Beni reported one hundred and twenty confirmed cases."),
        ])
        res = extract_report(body, "http://x/4", "2026-07-16")
        self.assertEqual(len(res.records), 0)
        self.assertTrue(any(d["reason"] == "snippet_not_verbatim" for d in res.dropped))

    # ---- validation gating (hermetic) ----
    def _records_from_rows(self):
        payload = _ExtractionPayload(records=_three_rows())
        with patch("src.live.extract_report._call_extraction_llm", return_value=payload):
            return extract_report(BODY, "http://x/1", "2026-07-16").records

    @patch("src.live.validate_extraction._call_validation_llm")
    def test_validate_all_pass(self, mock_v):
        records = self._records_from_rows()
        mock_v.return_value = _ValidationPayload(verdicts=[
            _Verdict(index=0, verdict="PASS"), _Verdict(index=1, verdict="PASS"), _Verdict(index=2, verdict="PASS"),
        ])
        res = validate_extraction(records, BODY)
        self.assertEqual(len(res.validated), 3)
        self.assertEqual(len(res.rejected), 0)

    @patch("src.live.validate_extraction._call_validation_llm")
    def test_validate_rejects_only_the_contradicted_one(self, mock_v):
        records = self._records_from_rows()
        mock_v.return_value = _ValidationPayload(verdicts=[
            _Verdict(index=0, verdict="PASS"),
            _Verdict(index=1, verdict="FAIL", reason="number not supported by snippet"),
            _Verdict(index=2, verdict="PASS"),
        ])
        res = validate_extraction(records, BODY)
        self.assertEqual(len(res.validated), 2)
        self.assertEqual(len(res.rejected), 1)
        self.assertEqual(res.rejected[0]["record"]["health_zone"], "Bunia")

    def test_validation_fails_closed_when_llm_errors(self):
        records = self._records_from_rows()
        with patch("src.live.validate_extraction._call_validation_llm", side_effect=RuntimeError("boom")):
            res = validate_extraction(records, BODY)
        self.assertEqual(len(res.validated), 0)
        self.assertEqual(len(res.rejected), 3)

    # ---- candidate store ----
    def test_candidate_store_write_dedup_promote_reset(self):
        tmp = tempfile.mkdtemp()
        cand = os.path.join(tmp, "candidate_history.csv")
        hist = os.path.join(tmp, "history.csv")
        rec = {"date": "2026-07-15", "province": "Ituri", "health_zone": "Beni",
               "suspected_cases": 150, "confirmed_cases": 120, "deaths": 30,
               "source_url": "http://x/1", "report_date": "2026-07-16", "snippet": "..."}
        try:
            self.assertEqual(write_candidates([rec], cand), 1)
            # dedup/upsert on identity key: same key, higher count -> one row, keep-last
            write_candidates([dict(rec, confirmed_cases=130)], cand)
            df = pd.read_csv(cand)
            self.assertEqual(len(df), 1)
            self.assertEqual(int(df.iloc[0]["confirmed_cases"]), 130)
            self.assertEqual(df.iloc[0]["status"], "pending")

            cid = df.iloc[0]["candidate_id"]
            self.assertEqual(cid, candidate_id(rec))
            # promote into a temp history, leaving the real history.csv untouched
            self.assertEqual(promote_candidates([cid], cand, hist), 1)
            hdf = pd.read_csv(hist)
            self.assertIn("Beni", set(hdf["health_zone"]))
            self.assertEqual(pd.read_csv(cand).iloc[0]["status"], "promoted")

            # reset clears the candidate store but not history
            reset_candidates(cand)
            self.assertFalse(os.path.exists(cand))
            self.assertTrue(os.path.exists(hist))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestValidationLive(unittest.TestCase):
    """Live check (D2): the batched checker must isolate the one contradicted record. Skips when
    the LLM is unavailable. If this fails, drop validate_extraction to per-record calls."""

    def test_batched_validation_isolates_contradiction(self):
        if not GEMINI_API_KEY:
            self.skipTest("GEMINI_API_KEY not set")
        # Body states zone, numbers AND date for each record, so the strict checker can fully
        # support the good ones; only Bunia's number is deliberately contradicted.
        body = (
            "Ebola situation, data as of 15 July 2026.\n"
            "Beni health zone: 120 confirmed cases and 30 deaths as of 15 July 2026.\n"
            "Bunia health zone: 200 confirmed cases and 45 deaths as of 15 July 2026.\n"
            "Mongbwalu health zone: 80 confirmed cases and 10 deaths as of 15 July 2026.\n"
        )
        base = {"date": "2026-07-15", "province": "Ituri", "suspected_cases": None,
                "source_url": "http://x", "report_date": "2026-07-16"}
        records = [
            {**base, "health_zone": "Beni", "confirmed_cases": 120, "deaths": 30,
             "snippet": "Beni health zone: 120 confirmed cases and 30 deaths as of 15 July 2026."},
            {**base, "health_zone": "Bunia", "confirmed_cases": 999, "deaths": 45,  # 999 contradicts snippet (200)
             "snippet": "Bunia health zone: 200 confirmed cases and 45 deaths as of 15 July 2026."},
            {**base, "health_zone": "Mongbwalu", "confirmed_cases": 80, "deaths": 10,
             "snippet": "Mongbwalu health zone: 80 confirmed cases and 10 deaths as of 15 July 2026."},
        ]
        res = validate_extraction(records, body)

        # If the model was unavailable, validate_extraction fails closed (all rejected w/ error).
        if len(res.rejected) == 3 and all("validation_error" in r["reason"] for r in res.rejected):
            self.skipTest("LLM unavailable; validation failed closed")

        rejected_zones = {r["record"]["health_zone"] for r in res.rejected}
        validated_zones = {r["health_zone"] for r in res.validated}
        self.assertIn("Bunia", rejected_zones)          # the contradicted one is rejected
        self.assertIn("Beni", validated_zones)          # the supported ones survive
        self.assertIn("Mongbwalu", validated_zones)


if __name__ == "__main__":
    unittest.main()
