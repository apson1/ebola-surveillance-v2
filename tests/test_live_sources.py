import unittest

from src.config import RELIEFWEB_APPNAME
from src.ingestion.live_sources import fetch_recent_drc_ebola_reports

_EXPECTED_KEYS = {"id", "title", "source", "date", "url"}


class TestLiveSources(unittest.TestCase):
    def test_fetch_returns_expected_shape(self):
        """Live integration test. Skips (does not fail) when RELIEFWEB_APPNAME is unset or the
        network/ReliefWeb is unavailable — matching the live-Gemini skip pattern. When it runs,
        it asserts a non-empty list of the {id, title, source, date, url} shape."""
        if not RELIEFWEB_APPNAME:
            self.skipTest("RELIEFWEB_APPNAME not set; live sources disabled")

        result = fetch_recent_drc_ebola_reports(limit=3)

        if result.mode in ("disabled", "error"):
            self.skipTest(f"ReliefWeb unavailable: {result.note}")

        self.assertIn(result.mode, ("pinned", "fallback"))
        self.assertTrue(len(result.reports) > 0, "expected at least one report")
        for r in result.reports:
            self.assertEqual(set(r.keys()), _EXPECTED_KEYS)
        self.assertTrue(str(result.reports[0]["url"]).startswith("http"))


if __name__ == "__main__":
    unittest.main()
