import os
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from src.config import RELIEFWEB_APPNAME
from src.ingestion.live_sources import fetch_recent_drc_ebola_reports, fetch_report_meta

_EXPECTED_KEYS = {"id", "title", "source", "date", "url"}


def _fake_resp(payload):
    m = MagicMock()
    m.raise_for_status.return_value = None
    m.json.return_value = payload
    return m


def _report_item(rid, title, disaster):
    return {"id": str(rid), "fields": {
        "title": title, "source": [{"shortname": "SRC"}],
        "date": {"original": "2026-07-14T00:00:00+00:00"},
        "url_alias": f"https://reliefweb.int/report/{rid}", "disaster": disaster,
    }}


class TestFetchReportMeta(unittest.TestCase):
    """Hermetic — mocks the ReliefWeb HTTP call to check disaster-association parsing."""

    @patch("src.ingestion.live_sources.RELIEFWEB_APPNAME", "test-app")
    @patch("src.ingestion.live_sources.requests.post")
    def test_parses_linked_disaster_ids(self, mock_post):
        mock_post.return_value = _fake_resp({"data": [_report_item(
            4221419, "WHO Bundibugyo sitrep",
            [{"id": 52586, "name": "Central/Eastern Africa: Ebola Outbreak - May 2026"}])]})
        meta = fetch_report_meta(4221419)
        self.assertEqual(meta["disaster_ids"], [52586])
        self.assertEqual(meta["disaster_names"], ["Central/Eastern Africa: Ebola Outbreak - May 2026"])
        self.assertEqual(meta["title"], "WHO Bundibugyo sitrep")

    @patch("src.ingestion.live_sources.RELIEFWEB_APPNAME", "test-app")
    @patch("src.ingestion.live_sources.requests.post")
    def test_unlinked_report_has_empty_disaster_ids(self, mock_post):
        # the East Timor CWS doc whose report id (52586) collides with the DRC disaster id
        mock_post.return_value = _fake_resp({"data": [_report_item(
            52586, "CWS: aid for East Timor/Indonesia", [])]})
        meta = fetch_report_meta(52586)
        self.assertEqual(meta["disaster_ids"], [])

    @patch("src.ingestion.live_sources.RELIEFWEB_APPNAME", "test-app")
    @patch("src.ingestion.live_sources.requests.post")
    def test_no_data_returns_empty_dict(self, mock_post):
        mock_post.return_value = _fake_resp({"data": []})
        self.assertEqual(fetch_report_meta(999), {})

    def test_disabled_when_appname_unset(self):
        with patch("src.ingestion.live_sources.RELIEFWEB_APPNAME", None):
            self.assertEqual(fetch_report_meta(4221419), {})


def _pinned_did(body):
    return next((c["value"] for c in body.get("filter", {}).get("conditions", [])
                 if c["field"] == "disaster.id"), None)


class TestFetchCacheAndFallback(unittest.TestCase):
    """Hermetic — mocks the HTTP call and a temp cache file to check the disaster_id-keyed cache
    and the profile-driven fallback query (Phase B3)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.cache = os.path.join(self.tmp, "cache.json")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    @patch("src.ingestion.live_sources.RELIEFWEB_APPNAME", "test-app")
    def test_cache_is_keyed_by_disaster_id(self):
        def side_effect(url, params=None, json=None, timeout=None):
            did = _pinned_did(json)
            m = MagicMock()
            m.raise_for_status.return_value = None
            m.json.return_value = {"data": [_report_item(did, f"report for disaster {did}", [])]}
            return m

        with patch("src.ingestion.live_sources._CACHE_FILE", self.cache), \
             patch("src.ingestion.live_sources.requests.post", side_effect=side_effect) as mp:
            a1 = fetch_recent_drc_ebola_reports(limit=5, disaster_id=111)
            b1 = fetch_recent_drc_ebola_reports(limit=5, disaster_id=222)
            self.assertIn("111", a1.reports[0]["title"])
            self.assertIn("222", b1.reports[0]["title"])   # B did NOT serve A's cache
            after_two = mp.call_count
            a2 = fetch_recent_drc_ebola_reports(limit=5, disaster_id=111)   # within TTL
            self.assertEqual(a2.reports, a1.reports)        # served from A's cache slot
            self.assertEqual(mp.call_count, after_two)      # no new HTTP call

    @patch("src.ingestion.live_sources.RELIEFWEB_APPNAME", "test-app")
    def test_fallback_uses_profile_country_and_query(self):
        def side_effect(url, params=None, json=None, timeout=None):
            m = MagicMock()
            m.raise_for_status.return_value = None
            # pinned query returns nothing -> forces the fallback; fallback returns a report
            m.json.return_value = ({"data": [_report_item(1, "fallback report", [])]}
                                   if "query" in json else {"data": []})
            return m

        with patch("src.ingestion.live_sources._CACHE_FILE", self.cache), \
             patch("src.ingestion.live_sources.requests.post", side_effect=side_effect) as mp:
            res = fetch_recent_drc_ebola_reports(limit=5, disaster_id=52586, force=True)
            self.assertEqual(res.mode, "fallback")
            fallback_bodies = [c.kwargs["json"] for c in mp.call_args_list if "query" in c.kwargs["json"]]
            self.assertTrue(fallback_bodies, "fallback query should have been issued")
            body = fallback_bodies[0]
            self.assertEqual(body["query"]["value"], "ebola")                 # DRC profile.fallback_query
            iso3 = next(c["value"] for c in body["filter"]["conditions"]
                        if c["field"] == "primary_country.iso3")
            self.assertEqual(iso3, "cod")                                     # DRC profile.country_iso3


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
