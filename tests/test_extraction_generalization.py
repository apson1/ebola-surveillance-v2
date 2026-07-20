"""Phase B2 tests: the extraction deny-list and prompts are now driven by the outbreak profile.

Deny-list tests use two fixture profiles (DRC-shaped, Uganda-shaped) and assert that each denies
only its own country/province names — neither leaks into the other.
"""
import unittest
from unittest.mock import patch

from src.outbreaks import OutbreakProfile
from src.live.extract_report import (
    _denied_zones, extract_report, _ExtractedRow, _ExtractionPayload,
    render_extraction_prompt, _EXTRACTION_INTRO, _EXTRACTION_RULES,
)
from src.live.validate_extraction import (
    render_validation_prompt, _VALIDATION_INTRO, _VALIDATION_RULES,
)

# Pinned copies of the pre-B2 rules blocks. Extraction/validation behavior must not drift from
# these, so the templated prompts must keep them byte-identical.
PRE_B2_EXTRACTION_RULES = """
Strict rules:
- Extract ONLY figures tied to a specific health_zone. Do NOT extract national totals (e.g.
  country-wide figures) or provincial totals that are not tied to a specific health_zone.
- Do NOT infer, average, estimate, or sum figures across zones. Report only numbers stated
  as-is in the text.
- For each record, copy the EXACT sentence or phrase from the body that supports the numbers
  into `snippet`. The snippet MUST be a verbatim, character-for-character substring of the
  body. Do not paraphrase, reword, translate, or fix typos.
- If a count (suspected_cases, confirmed_cases, deaths) is not stated for a zone, leave it null.
- If no figure is explicitly attributed to a specific health_zone, return an empty list.

Return JSON matching the schema: a list of records, each with date, province, health_zone,
suspected_cases, confirmed_cases, deaths, and snippet."""

PRE_B2_VALIDATION_RULES = """ You are
given the REPORT BODY and a numbered list of CANDIDATE RECORDS extracted from it. Each record
claims cumulative counts for a specific health_zone on a date, together with a snippet.

For EACH record independently, decide whether the REPORT BODY EXPLICITLY and UNAMBIGUOUSLY
supports the exact numbers for that exact health_zone and date.

Rules:
- Judge each record on its own merits. For each record, quote the exact supporting phrase from
  the body into `supporting_phrase`.
- Answer PASS only if the body explicitly states these numbers for this zone. If the number
  requires inference or aggregation, or the supporting text does not clearly state it for this
  zone, or the snippet does not actually support the number, answer FAIL.
- Return exactly one verdict per record, keyed by the record's `index`.

Return JSON with a `verdicts` array; each item has index, verdict ("PASS" or "FAIL"),
supporting_phrase, and reason."""

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


class TestPromptParity(unittest.TestCase):
    def test_rules_blocks_are_byte_identical_to_pre_b2(self):
        self.assertEqual(_EXTRACTION_RULES, PRE_B2_EXTRACTION_RULES)
        self.assertEqual(_VALIDATION_RULES, PRE_B2_VALIDATION_RULES)

    def test_rendered_prompts_are_intro_then_rules_with_nothing_injected(self):
        for profile in (DRC, UGA):
            # extraction
            rendered_e = render_extraction_prompt(profile)
            intro_e = _EXTRACTION_INTRO.format(disease=profile.disease, country_name=profile.country_name)
            self.assertEqual(rendered_e, intro_e + _EXTRACTION_RULES)          # exact composition
            self.assertEqual(rendered_e.index(_EXTRACTION_RULES), len(intro_e))  # rules right after intro
            self.assertIn(profile.disease, rendered_e)
            self.assertIn(profile.country_name, rendered_e)
            # validation
            rendered_v = render_validation_prompt(profile)
            intro_v = _VALIDATION_INTRO.format(disease=profile.disease, country_name=profile.country_name)
            self.assertEqual(rendered_v, intro_v + _VALIDATION_RULES)
            self.assertEqual(rendered_v.index(_VALIDATION_RULES), len(intro_v))
            self.assertIn(profile.disease, rendered_v)
            self.assertIn(profile.country_name, rendered_v)


if __name__ == "__main__":
    unittest.main()
