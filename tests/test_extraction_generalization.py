"""Phase B2 tests: the extraction deny-list and prompts are now driven by the outbreak profile.

Deny-list tests use two fixture profiles (DRC-shaped, Uganda-shaped) and assert that each denies
only its own country/province names — neither leaks into the other.
"""
import unittest
from unittest.mock import patch

from src.outbreaks import OutbreakProfile
from src.live.extract_report import (
    _denied_zones, extract_report, _ExtractedRow, _ExtractionPayload,
)

DRC_ID, UGA_ID = 52586, 90000

DRC = OutbreakProfile(
    disaster_id=DRC_ID, display_name="DRC Ebola 2026",
    disease="Bundibugyo virus disease (Ebola)",
    country_name="the Democratic Republic of the Congo",
    denied_zone_aliases=["drc", "congo", "ituri", "north kivu"],
)
UGA = OutbreakProfile(
    disaster_id=UGA_ID, display_name="Uganda SVD 2026",
    disease="Sudan virus disease (Ebola)", country_name="Uganda",
    denied_zone_aliases=["uganda", "kampala", "mubende"],
)
FIXTURE_REGISTRY = {DRC_ID: DRC, UGA_ID: UGA}


class TestConfigDrivenDenyList(unittest.TestCase):
    def setUp(self):
        _denied_zones.cache_clear()

    def tearDown(self):
        _denied_zones.cache_clear()   # never leak a fixture profile into other test modules

    @patch("src.outbreaks.REGISTRY", FIXTURE_REGISTRY)
    def test_deny_lists_are_per_outbreak_and_do_not_leak(self):
        drc, uga = _denied_zones(DRC_ID), _denied_zones(UGA_ID)
        self.assertIn("ituri", drc)
        self.assertIn("drc", drc)
        self.assertNotIn("kampala", drc)          # Uganda name absent from DRC deny-list
        self.assertIn("kampala", uga)
        self.assertIn("uganda", uga)
        self.assertNotIn("ituri", uga)            # DRC name absent from Uganda deny-list

    @patch("src.outbreaks.REGISTRY", FIXTURE_REGISTRY)
    @patch("src.live.extract_report._call_extraction_llm")
    def test_denied_zone_dropped_follows_active_profile(self, mock_llm):
        body = "Ituri reported 100 confirmed cases. Kampala reported 50 confirmed cases."
        mock_llm.return_value = _ExtractionPayload(records=[
            _ExtractedRow(date="2026-07-15", health_zone="Ituri", confirmed_cases=100,
                          snippet="Ituri reported 100 confirmed cases."),
            _ExtractedRow(date="2026-07-15", health_zone="Kampala", confirmed_cases=50,
                          snippet="Kampala reported 50 confirmed cases."),
        ])
        # DRC profile: Ituri is a province name -> denied; Kampala is kept
        drc = extract_report(body, "http://x", "2026-07-16", DRC_ID)
        self.assertEqual({r["health_zone"] for r in drc.records}, {"Kampala"})
        self.assertTrue(any(d["reason"] == "denied_zone" and d["record"]["health_zone"] == "Ituri"
                            for d in drc.dropped))
        # Uganda profile: Kampala denied; Ituri kept (no cross-outbreak leak)
        uga = extract_report(body, "http://x", "2026-07-16", UGA_ID)
        self.assertEqual({r["health_zone"] for r in uga.records}, {"Ituri"})

    def test_unknown_outbreak_has_empty_deny_list(self):
        # placeholder profile -> nothing denied, no crash
        self.assertEqual(_denied_zones(123456), frozenset())


if __name__ == "__main__":
    unittest.main()
