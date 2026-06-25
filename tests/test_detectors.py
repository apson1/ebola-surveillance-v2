import unittest
from unittest.mock import patch, MagicMock
import pandas as pd

from src.signal.detectors import (
    detect_new_zone,
    detect_surge,
    detect_cfr_shift,
    detect_stale_or_missing,
)
from src.signal.signal_agent import run_signal_agent

class TestDetectors(unittest.TestCase):
    def setUp(self):
        # Set up standard dataframes for testing
        self.prior_df = pd.DataFrame([
            {
                "date": pd.to_datetime("2026-06-17"),
                "province": "Ituri",
                "health_zone": "Bunia",
                "suspected_cases": 100,
                "confirmed_cases": 80,
                "deaths": 20,
                "source_url": "http://source1",
                "report_date": pd.to_datetime("2026-06-19"),
            },
            {
                "date": pd.to_datetime("2026-06-17"),
                "province": "Ituri",
                "health_zone": "Mongbwalu",
                "suspected_cases": 150,
                "confirmed_cases": 120,
                "deaths": 30,
                "source_url": "http://source1",
                "report_date": pd.to_datetime("2026-06-19"),
            },
            {
                "date": pd.to_datetime("2026-06-17"),
                "province": "North Kivu",
                "health_zone": "Beni",
                "suspected_cases": 50,
                "confirmed_cases": 40,
                "deaths": 10,
                "source_url": "http://source1",
                "report_date": pd.to_datetime("2026-06-19"),
            }
        ])

    def test_new_zone(self):
        incoming_df = pd.DataFrame([
            {
                "date": pd.to_datetime("2026-06-20"),
                "province": "Ituri",
                "health_zone": "Bunia",
                "suspected_cases": 110,
                "confirmed_cases": 90,
                "deaths": 22,
                "source_url": "http://source2",
                "report_date": pd.to_datetime("2026-06-21"),
            },
            {
                "date": pd.to_datetime("2026-06-20"),
                "province": "Ituri",
                "health_zone": "Komanda", # New zone
                "suspected_cases": 30,
                "confirmed_cases": 10,
                "deaths": 2,
                "source_url": "http://source2",
                "report_date": pd.to_datetime("2026-06-21"),
            }
        ])
        flags = detect_new_zone(incoming_df, self.prior_df)
        self.assertEqual(len(flags), 1)
        self.assertEqual(flags[0]["health_zone"], "Komanda")
        self.assertEqual(flags[0]["detector"], "new_zone")

    def test_surge(self):
        incoming_df = pd.DataFrame([
            {
                "date": pd.to_datetime("2026-06-20"), # 3 days gap
                "province": "Ituri",
                "health_zone": "Mongbwalu",
                "suspected_cases": 300,
                "confirmed_cases": 200, # 200 - 120 = 80 increase. 80/3 = 26.6 daily. 80/120 = 66.6% growth.
                "deaths": 40,
                "source_url": "http://source2",
                "report_date": pd.to_datetime("2026-06-21"),
            }
        ])
        flags = detect_surge(incoming_df, self.prior_df)
        self.assertEqual(len(flags), 1)
        self.assertEqual(flags[0]["health_zone"], "Mongbwalu")
        self.assertEqual(flags[0]["detector"], "surge")
        self.assertGreaterEqual(flags[0]["daily_new"], 10)
        self.assertGreaterEqual(flags[0]["pct_growth"], 0.5)

    def test_cfr_shift(self):
        incoming_df = pd.DataFrame([
            {
                "date": pd.to_datetime("2026-06-20"),
                "province": "North Kivu",
                "health_zone": "Beni",
                "suspected_cases": 60,
                "confirmed_cases": 50, # confirmed >= 20
                "deaths": 20, # CFR = 20/50 = 40% (high threshold is 30%, prior CFR was 10/40 = 25%, shift is 15%)
                "source_url": "http://source2",
                "report_date": pd.to_datetime("2026-06-21"),
            }
        ])
        flags = detect_cfr_shift(incoming_df, self.prior_df)
        self.assertEqual(len(flags), 1)
        self.assertEqual(flags[0]["health_zone"], "Beni")
        self.assertEqual(flags[0]["detector"], "cfr_shift")
        self.assertGreaterEqual(flags[0]["cfr_incoming"], 0.30)
        
    def test_stale_or_missing(self):
        # 1. Missing zone: Bunia and Beni are missing from incoming
        # 2. Null field: Mongbwalu confirmed_cases is null
        incoming_df = pd.DataFrame([
            {
                "date": pd.to_datetime("2026-06-20"),
                "province": "Ituri",
                "health_zone": "Mongbwalu",
                "suspected_cases": 150,
                "confirmed_cases": None, # null field
                "deaths": 30,
                "source_url": "http://source2",
                "report_date": pd.to_datetime("2026-06-21"),
            }
        ])
        flags = detect_stale_or_missing(incoming_df, self.prior_df)
        self.assertEqual(len(flags), 3) # missing Bunia, missing Beni, and null field for Mongbwalu
        
        detectors = [f["detector"] for f in flags]
        types = [f.get("type") for f in flags]
        
        self.assertTrue(all(d == "stale_or_missing" for d in detectors))
        self.assertIn("missing_zone", types)
        self.assertIn("null_field", types)

    def test_new_zone_no_surge_raise(self):
        """Verify that a new zone in incoming does not raise an exception inside detect_surge."""
        incoming_df = pd.DataFrame([
            {
                "date": pd.to_datetime("2026-06-20"),
                "province": "Ituri",
                "health_zone": "Komanda", # New zone
                "suspected_cases": 30,
                "confirmed_cases": 10,
                "deaths": 2,
                "source_url": "http://source2",
                "report_date": pd.to_datetime("2026-06-21"),
            }
        ])
        try:
            flags = detect_surge(incoming_df, self.prior_df)
            self.assertEqual(len(flags), 0)
        except Exception as e:
            self.fail(f"detect_surge raised an exception on a new zone: {e}")

    def test_null_confirmed_skipped(self):
        """Verify that a null confirmed count in incoming is skipped by surge and CFR detectors."""
        incoming_df = pd.DataFrame([
            {
                "date": pd.to_datetime("2026-06-20"),
                "province": "Ituri",
                "health_zone": "Bunia",
                "suspected_cases": 110,
                "confirmed_cases": None, # Null confirmed
                "deaths": 22,
                "source_url": "http://source2",
                "report_date": pd.to_datetime("2026-06-21"),
            }
        ])
        # Neither should raise or flag since we skip rows with nulls
        surge_flags = detect_surge(incoming_df, self.prior_df)
        cfr_flags = detect_cfr_shift(incoming_df, self.prior_df)
        self.assertEqual(len(surge_flags), 0)
        self.assertEqual(len(cfr_flags), 0)

    @patch("src.signal.signal_agent.genai.Client")
    def test_llm_guard_tampered_ids(self, mock_client_class):
        """Verify that the LLM Guard rejects a tampered ID list and falls back to deterministic ordering."""
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        mock_response = MagicMock()
        # Mock returns order [0, 99] which is not a valid permutation of [0, 1]
        mock_response.text = '{"order": [0, 99], "reasoning": "tampered response"}'
        mock_client.models.generate_content.return_value = mock_response

        # Mock prior snapshot and incoming data that will trigger two flags
        # e.g., Bunia (surge) and Beni (cfr_shift)
        prior = [
            {
                "date": "2026-06-17",
                "province": "Ituri",
                "health_zone": "Bunia",
                "suspected_cases": 100,
                "confirmed_cases": 80,
                "deaths": 20,
                "source_url": "http://source1",
                "report_date": "2026-06-19",
            },
            {
                "date": "2026-06-17",
                "province": "North Kivu",
                "health_zone": "Beni",
                "suspected_cases": 50,
                "confirmed_cases": 40,
                "deaths": 10,
                "source_url": "http://source1",
                "report_date": "2026-06-19",
            }
        ]
        incoming = [
            {
                "date": "2026-06-20",
                "province": "Ituri",
                "health_zone": "Bunia",
                "suspected_cases": 300,
                "confirmed_cases": 200, # surge (daily_new = 40)
                "deaths": 40,
                "source_url": "http://source2",
                "report_date": "2026-06-21",
            },
            {
                "date": "2026-06-20",
                "province": "North Kivu",
                "health_zone": "Beni",
                "suspected_cases": 60,
                "confirmed_cases": 50, # confirmed >= 20
                "deaths": 25, # CFR = 50% (cfr_shift)
                "source_url": "http://source2",
                "report_date": "2026-06-21",
            }
        ]

        result = run_signal_agent(prior, incoming)
        self.assertEqual(result["status"], "success")
        # Due to fallback: priority order is cfr_shift first, then surge
        self.assertEqual(len(result["flags"]), 2)
        self.assertEqual(result["flags"][0]["detector"], "cfr_shift")
        self.assertEqual(result["flags"][1]["detector"], "surge")
        self.assertIn("[LLM Guard Fallback]", result["reasoning"])


if __name__ == "__main__":
    unittest.main()
