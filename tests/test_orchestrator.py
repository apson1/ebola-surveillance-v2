import asyncio
import unittest
from unittest.mock import patch, AsyncMock

from src.orchestrator import run_scan, run_scan_async

# Indicators that the live LLM was unavailable (quota/rate/transient) rather than a real bug.
_LLM_UNAVAILABLE = ("RESOURCE_EXHAUSTED", "429", "503", "UNAVAILABLE", "quota",
                    "ConnectError", "ConnectionError", "Timeout", "Deadline")


def _skip_if_llm_unavailable(test, exc):
    msg = str(exc)
    if any(k in msg for k in _LLM_UNAVAILABLE):
        test.skipTest(f"Gemini unavailable/quota-limited: {msg[:140]}")


def _new_zone_flag(zone="Komanda"):
    """A deterministic new_zone flag for stubbing run_signal_pipeline_async."""
    return {
        "detector": "new_zone",
        "health_zone": zone,
        "province": "Ituri",
        "confirmed_cases": 9,
        "source_url": "http://example.org/nz",
        "report_date": "2026-06-21",
    }


def _cfr_flag(zone="Beni"):
    """A fully-formed cfr_shift flag (draft_alert renders its cfr_* numbers)."""
    return {
        "detector": "cfr_shift",
        "health_zone": zone,
        "province": "North Kivu",
        "confirmed_prior": 40,
        "deaths_prior": 10,
        "cfr_prior": 0.25,
        "confirmed_incoming": 50,
        "deaths_incoming": 25,
        "cfr_incoming": 0.50,
        "cfr_diff": 0.25,
        "source_url": "http://example.org/cfr",
        "report_date": "2026-06-21",
    }


class TestOrchestrator(unittest.TestCase):
    def test_run_scan_new_zone(self):
        """Phase 5 acceptance: run_scan on incoming_new_zone.json names the new zone.

        The one intentionally end-to-end (live) test; the rest are hermetic. It skips (does
        not fail) when Gemini is rate-limited or unavailable.
        """
        try:
            result = run_scan(
                incoming_path="data/incoming/incoming_new_zone.json",
                history_path="data/history.csv",
            )
        except Exception as e:  # noqa: BLE001
            _skip_if_llm_unavailable(self, e)
            raise
        self.assertEqual(result["status"], "success")
        self.assertIn("Komanda", result["alert"])
        self.assertTrue(len(result["flags"]) > 0)

    # ---- T3: model reasoning must never leak into the alert -----------------
    @patch("src.orchestrator.run_signal_pipeline_async", new_callable=AsyncMock)
    @patch("src.orchestrator._load_reports_via_mcptoolset", new_callable=AsyncMock)
    def test_reasoning_excluded_from_alert(self, mock_mcp, mock_signal):
        mock_mcp.side_effect = RuntimeError("force fallback")  # hermetic: plain ingestion
        token = "REASONING_SENTINEL_ZZZ"
        digits = "8675309"
        mock_signal.return_value = {
            "status": "success",
            "flags": [_new_zone_flag("Komanda")],
            "reasoning": f"{token}: the model cites {digits} contacts traced.",
        }
        result = run_scan("data/incoming/incoming_new_zone.json")

        self.assertEqual(result["status"], "success")
        self.assertNotIn(token, result["alert"])
        self.assertNotIn(digits, result["alert"])
        self.assertNotIn(token, str(result))
        self.assertNotIn(digits, str(result))

    # ---- T4: MCP path tried, with a clean and logged fallback ---------------
    @patch("src.orchestrator.run_signal_pipeline_async", new_callable=AsyncMock)
    @patch("src.orchestrator._load_reports_via_mcptoolset", new_callable=AsyncMock)
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
        self.assertNotIn("Successfully loaded reports via Ingestion MCP server", log)

    @patch("src.orchestrator.run_signal_pipeline_async", new_callable=AsyncMock)
    @patch("src.orchestrator._load_reports_via_mcptoolset", new_callable=AsyncMock)
    def test_mcp_success_uses_mcp_path(self, mock_mcp, mock_signal):
        mock_mcp.return_value = {
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
        self.assertIn("Successfully loaded reports via Ingestion MCP server", log)
        self.assertNotIn("plain ingestion_pipeline fallback", log)
        mock_mcp.assert_awaited_once()

    # ---- T2 (adapted): headline matches the top ranked flag, no live LLM ----
    @patch("src.orchestrator.run_signal_pipeline_async", new_callable=AsyncMock)
    @patch("src.orchestrator._load_reports_via_mcptoolset", new_callable=AsyncMock)
    def test_headline_matches_top_ranked_flag(self, mock_mcp, mock_signal):
        mock_mcp.side_effect = RuntimeError("force fallback")
        mock_signal.return_value = {
            "status": "success",
            "flags": [_cfr_flag("Beni"), _new_zone_flag("Komanda")],
            "reasoning": "",
        }
        result = run_scan("data/incoming/incoming_multi_signal.json")

        top = result["flags"][0]
        headline = result["alert"].split("\n")[0]
        self.assertIn(top["health_zone"], headline)       # headline reflects flags[0]
        self.assertIn("Case Fatality Ratio", headline)    # cfr_shift headline

    # ---- A1: sync run_scan works from inside a running event loop -----------
    @patch("src.orchestrator.run_signal_pipeline_async", new_callable=AsyncMock)
    @patch("src.orchestrator._load_reports_via_mcptoolset", new_callable=AsyncMock)
    def test_run_scan_inside_running_loop(self, mock_mcp, mock_signal):
        mock_mcp.side_effect = RuntimeError("force fallback")
        mock_signal.return_value = {
            "status": "success",
            "flags": [_new_zone_flag("Komanda")],
            "reasoning": "",
        }

        async def driver():
            # We are now inside a running event loop (Kaggle-like). Sync run_scan must NOT
            # raise (it thread-offloads); run_scan_async must also work via await.
            sync_result = run_scan("data/incoming/incoming_new_zone.json")
            async_result = await run_scan_async("data/incoming/incoming_new_zone.json")
            return sync_result, async_result

        sync_result, async_result = asyncio.run(driver())
        self.assertIn("Komanda", sync_result["alert"])
        self.assertIn("Komanda", async_result["alert"])


if __name__ == "__main__":
    unittest.main()
