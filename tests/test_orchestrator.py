import unittest
from unittest.mock import patch, MagicMock, AsyncMock

from src.orchestrator import run_scan


def _new_zone_flag(zone="Komanda"):
    """A deterministic new_zone flag for stubbing run_signal_agent."""
    return {
        "detector": "new_zone",
        "health_zone": zone,
        "province": "Ituri",
        "confirmed_cases": 9,
        "source_url": "http://example.org/nz",
        "report_date": "2026-06-21",
    }


class TestOrchestrator(unittest.TestCase):
    def test_run_scan_new_zone(self):
        """Phase 5 acceptance: run_scan on incoming_new_zone.json names the new zone.

        This is the one intentionally end-to-end (live) test; the rest are hermetic.
        """
        result = run_scan(
            incoming_path="data/incoming/incoming_new_zone.json",
            history_path="data/history.csv",
        )
        self.assertEqual(result["status"], "success")
        self.assertIn("Komanda", result["alert"])
        self.assertTrue(len(result["flags"]) > 0)

    # ---- T3: model reasoning must never leak into the alert -----------------
    @patch("src.orchestrator.run_signal_agent")
    @patch("src.orchestrator._call_mcp_load_reports", new_callable=AsyncMock)
    def test_reasoning_excluded_from_alert(self, mock_mcp, mock_signal):
        # Force the plain fallback so the test is hermetic (no MCP subprocess).
        mock_mcp.side_effect = RuntimeError("force fallback")
        token = "REASONING_SENTINEL_ZZZ"
        digits = "8675309"
        mock_signal.return_value = {
            "status": "success",
            "flags": [_new_zone_flag("Komanda")],
            "reasoning": f"{token}: the model cites {digits} contacts traced.",
        }
        result = run_scan("data/incoming/incoming_new_zone.json")

        self.assertEqual(result["status"], "success")
        # The injected reasoning token and digits must appear nowhere in the alert...
        self.assertNotIn(token, result["alert"])
        self.assertNotIn(digits, result["alert"])
        # ...nor anywhere in the returned structure.
        self.assertNotIn(token, str(result))
        self.assertNotIn(digits, str(result))

    # ---- T4: MCP path is tried, with a clean and logged fallback ------------
    @patch("src.orchestrator.run_signal_agent")
    @patch("src.orchestrator._call_mcp_load_reports", new_callable=AsyncMock)
    def test_mcp_failure_falls_back_to_plain(self, mock_mcp, mock_signal):
        mock_mcp.side_effect = RuntimeError("MCP unavailable")
        mock_signal.return_value = {
            "status": "success",
            "flags": [_new_zone_flag("Komanda")],
            "reasoning": "",
        }
        with self.assertLogs("src.orchestrator", level="INFO") as cm:
            result = run_scan("data/incoming/incoming_new_zone.json")

        self.assertEqual(result["status"], "success")
        self.assertIn("Komanda", result["alert"])
        log = "\n".join(cm.output)
        self.assertIn("plain ingestion_pipeline fallback", log)
        self.assertNotIn("Successfully loaded reports via Ingestion MCP server.", log)

    @patch("src.orchestrator.run_signal_agent")
    @patch("src.orchestrator._call_mcp_load_reports", new_callable=AsyncMock)
    def test_mcp_success_uses_mcp_path(self, mock_mcp, mock_signal):
        mock_mcp.return_value = {
            "status": "success",
            "prior_snapshot": [],
            "incoming": [{"health_zone": "Komanda"}],
        }
        mock_signal.return_value = {
            "status": "success",
            "flags": [_new_zone_flag("Komanda")],
            "reasoning": "",
        }
        with self.assertLogs("src.orchestrator", level="INFO") as cm:
            result = run_scan("data/incoming/incoming_new_zone.json")

        self.assertEqual(result["status"], "success")
        self.assertIn("Komanda", result["alert"])
        log = "\n".join(cm.output)
        self.assertIn("Successfully loaded reports via Ingestion MCP server.", log)
        self.assertNotIn("plain ingestion_pipeline fallback", log)
        mock_mcp.assert_awaited_once()

    # ---- T2: deterministic fallback ranking for the multi_signal scenario ---
    @patch("src.signal.signal_agent.genai.Client")
    @patch("src.orchestrator._call_mcp_load_reports", new_callable=AsyncMock)
    def test_multi_signal_fallback_top_signal(self, mock_mcp, mock_client_class):
        # Force plain ingestion so the real detectors run on the real scenario file.
        mock_mcp.side_effect = RuntimeError("force fallback")
        # Stub the LLM to return a non-permutation of the 4 flag ids, so the LLM guard
        # fails and the deterministic priority ordering activates. No live LLM is used.
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        mock_response = MagicMock()
        mock_response.text = '{"order": [0], "reasoning": "stubbed non-permutation"}'
        mock_client.models.generate_content.return_value = mock_response

        result = run_scan("data/incoming/incoming_multi_signal.json")

        self.assertEqual(result["status"], "success")
        self.assertEqual(len(result["flags"]), 4)
        # Fallback priority: cfr_shift > surge > new_zone > stale_or_missing.
        top = result["flags"][0]
        self.assertEqual(top["detector"], "cfr_shift")
        headline = result["alert"].split("\n")[0]
        self.assertIn(top["health_zone"], headline)   # headline matches the top flag
        self.assertIn("Case Fatality Ratio", headline)


if __name__ == "__main__":
    unittest.main()
