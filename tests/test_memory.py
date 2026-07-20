import os
import shutil
import tempfile
import unittest
from unittest.mock import patch, AsyncMock

import pandas as pd

from src.memory.history_store import append_to_history, CONTRACT_COLUMNS, COUNT_COLUMNS
from src.ingestion.ingestion import ingestion_pipeline
from src.signal.detectors import detect_new_zone
from src.orchestrator import run_scan

SEED = "data/history.csv"
NEW_ZONE = "data/incoming/incoming_new_zone.json"
DID = 52586  # active outbreak (DRC Ebola 2026)


def _rowcount(path):
    return len(pd.read_csv(path))


def _non_null_incoming_count(records):
    return sum(
        1 for r in records
        if all(r.get(c) is not None and not (isinstance(r.get(c), float) and pd.isna(r.get(c)))
               for c in COUNT_COLUMNS)
    )


def _new_zone_flag(zone="Komanda"):
    return {
        "detector": "new_zone", "health_zone": zone, "province": "Ituri",
        "confirmed_cases": 9, "source_url": "http://example.org/nz", "report_date": "2026-06-21",
    }


class TestMemory(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.hist = os.path.join(self.tmp, "history.csv")
        shutil.copy(SEED, self.hist)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_append_skips_null_count_rows(self):
        records = [
            {"disaster_id": DID, "date": "2026-06-25", "province": "Ituri", "health_zone": "Ztest",
             "suspected_cases": 5, "confirmed_cases": 3, "deaths": 1,
             "source_url": "http://x", "report_date": "2026-06-26"},
            {"disaster_id": DID, "date": "2026-06-25", "province": "Ituri", "health_zone": "Znull",
             "suspected_cases": 5, "confirmed_cases": None, "deaths": 1,
             "source_url": "http://x", "report_date": "2026-06-26"},
        ]
        before = _rowcount(self.hist)
        added = append_to_history(records, self.hist)
        after = _rowcount(self.hist)
        self.assertEqual(added, 1)
        self.assertEqual(after - before, 1)  # only the non-null row persisted
        hist = pd.read_csv(self.hist)
        self.assertFalse(hist[COUNT_COLUMNS].isnull().any().any())  # history never has nulls
        self.assertEqual(list(hist.columns), CONTRACT_COLUMNS)      # contract order preserved

    def test_dedup_upsert(self):
        """Addition #2: re-appending the same identity key with a higher count updates the
        existing row rather than creating a duplicate."""
        path = os.path.join(self.tmp, "fresh.csv")
        key = {"disaster_id": DID, "date": "2026-06-25", "province": "Ituri", "health_zone": "Upsert",
               "source_url": "http://u"}
        r1 = {**key, "suspected_cases": 10, "confirmed_cases": 80, "deaths": 5, "report_date": "2026-06-26"}
        r2 = {**key, "suspected_cases": 12, "confirmed_cases": 120, "deaths": 7, "report_date": "2026-06-27"}

        append_to_history([r1], path)
        append_to_history([r2], path)

        hist = pd.read_csv(path)
        match = hist[(hist.date == "2026-06-25") & (hist.province == "Ituri")
                     & (hist.health_zone == "Upsert") & (hist.source_url == "http://u")]
        self.assertEqual(len(match), 1)                                  # one row, not two
        self.assertEqual(int(match.iloc[0]["confirmed_cases"]), 120)     # higher count wins

    def test_two_scan_acceptance(self):
        """Acceptance: the second scan sees zones added by the first."""
        ing1 = ingestion_pipeline(self.hist, NEW_ZONE)
        new1 = {f["health_zone"] for f in
                detect_new_zone(pd.DataFrame(ing1["incoming"]), pd.DataFrame(ing1["prior_snapshot"]))}
        self.assertIn("Komanda", new1)  # Komanda is new in scan 1

        non_null = _non_null_incoming_count(ing1["incoming"])
        before = _rowcount(self.hist)
        added = append_to_history(ing1["incoming"], self.hist)
        after = _rowcount(self.hist)
        # Addition #1: history grows by the non-null incoming count, not the full incoming count.
        self.assertEqual(after - before, non_null)
        self.assertEqual(added, non_null)

        ing2 = ingestion_pipeline(self.hist, NEW_ZONE)
        new2 = {f["health_zone"] for f in
                detect_new_zone(pd.DataFrame(ing2["incoming"]), pd.DataFrame(ing2["prior_snapshot"]))}
        self.assertNotIn("Komanda", new2)  # second scan no longer flags it as new

    @patch("src.orchestrator.run_signal_pipeline_async", new_callable=AsyncMock)
    @patch("src.orchestrator._load_reports_via_mcptoolset", new_callable=AsyncMock)
    def test_run_scan_persist_true_appends(self, mock_mcp, mock_signal):
        mock_mcp.side_effect = RuntimeError("force fallback")  # use real ingestion_pipeline
        mock_signal.return_value = {"status": "success", "flags": [_new_zone_flag()], "reasoning": ""}

        non_null = _non_null_incoming_count(ingestion_pipeline(self.hist, NEW_ZONE)["incoming"])
        before = _rowcount(self.hist)
        result = run_scan(NEW_ZONE, history_path=self.hist, persist=True)
        after = _rowcount(self.hist)
        self.assertEqual(result["persisted"], non_null)
        self.assertEqual(after - before, non_null)

    @patch("src.orchestrator.run_signal_pipeline_async", new_callable=AsyncMock)
    @patch("src.orchestrator._load_reports_via_mcptoolset", new_callable=AsyncMock)
    def test_run_scan_persist_defaults_false(self, mock_mcp, mock_signal):
        mock_mcp.side_effect = RuntimeError("force fallback")
        mock_signal.return_value = {"status": "success", "flags": [_new_zone_flag()], "reasoning": ""}

        before = _rowcount(self.hist)
        result = run_scan(NEW_ZONE, history_path=self.hist)  # persist defaults False
        after = _rowcount(self.hist)
        self.assertEqual(after, before)            # history not mutated
        self.assertEqual(result.get("persisted", 0), 0)


if __name__ == "__main__":
    unittest.main()
