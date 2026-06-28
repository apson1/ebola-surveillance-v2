import unittest

from src.alert.alert_agent import draft_alert
from src.guardrails.guardrails import enforce_guardrails


def _flags():
    """A realistic clean flag set: cfr percentages, a new zone, source URLs that contain
    both digits and a banned substring ('treatment', '608')."""
    return [
        {
            "detector": "cfr_shift", "health_zone": "Beni", "province": "North Kivu",
            "confirmed_prior": 78, "deaths_prior": 16, "cfr_prior": 0.2051,
            "confirmed_incoming": 90, "deaths_incoming": 41, "cfr_incoming": 0.4556,
            "cfr_diff": 0.2505,
            "source_url": "https://reliefweb.int/u?search=ebola+2026-DON608",
            "report_date": "2026-06-21",
        },
        {
            "detector": "new_zone", "health_zone": "Komanda", "province": "Ituri",
            "confirmed_cases": 10,
            "source_url": "https://who.int/2026/treatment-guide",
            "report_date": "2026-06-21",
        },
    ]


def _types(result):
    return {v["type"] for v in result.violations}


class TestGuardrails(unittest.TestCase):
    # ---- clean ----
    def test_clean_alert_passes_unchanged(self):
        flags = _flags()
        alert = draft_alert(flags)  # already contains the escalation line
        result = enforce_guardrails(alert, flags)
        self.assertTrue(result.passed)
        self.assertFalse(result.blocked)
        self.assertEqual(result.violations, [])
        self.assertEqual(result.alert, alert)  # escalation already present -> unchanged

    def test_no_false_positives_on_percentages_dates_or_urls(self):
        # The cfr percentages, ISO dates, and a URL containing '608'/'treatment' must NOT trip.
        flags = _flags()
        result = enforce_guardrails(draft_alert(flags), flags)
        self.assertTrue(result.passed)
        self.assertNotIn("unsourced_number", _types(result))
        self.assertNotIn("banned_language", _types(result))

    # ---- three crafted bad outputs are caught ----
    def test_unsourced_number_blocked(self):
        flags = _flags()
        alert = draft_alert(flags) + "\nExtra: an estimated 99999 additional cases."
        result = enforce_guardrails(alert, flags)
        self.assertTrue(result.blocked)
        self.assertIn("unsourced_number", _types(result))
        self.assertTrue(any(v["detail"] == "99999" for v in result.violations))

    def test_diagnosis_sentence_blocked(self):
        flags = _flags()
        alert = draft_alert(flags) + "\nThe patient was diagnosed with the disease."
        result = enforce_guardrails(alert, flags)  # default mode = block
        self.assertTrue(result.blocked)
        self.assertIn("banned_language", _types(result))

    def test_fabricated_zone_blocked(self):
        flags = _flags()
        # A signal line for a zone not in the flags; its numbers are otherwise sourced.
        alert = draft_alert(flags) + (
            "\n- [new_zone] Atlantis (Ituri): confirmed_cases=10 "
            "(source: https://x/y, report_date: 2026-06-21)"
        )
        result = enforce_guardrails(alert, flags)
        self.assertTrue(result.blocked)
        self.assertIn("fabricated_zone", _types(result))
        self.assertTrue(any(v["detail"] == "Atlantis" for v in result.violations))

    # ---- whitelist / URL false-positive guards ----
    def test_treatment_center_name_passes(self):
        flags = _flags()
        alert = draft_alert(flags) + "\nAn Ebola Treatment Center is operating in Beni."
        result = enforce_guardrails(alert, flags)
        self.assertNotIn("banned_language", _types(result))
        self.assertFalse(result.blocked)
        self.assertTrue(result.passed)

    # ---- fail-closed placeholder still surfaces the sourced signals ----
    def test_blocked_placeholder_keeps_escalation_and_sources(self):
        flags = _flags()
        alert = draft_alert(flags) + "\nEstimated 99999 cases."
        result = enforce_guardrails(alert, flags)
        self.assertTrue(result.blocked)
        self.assertIn("ESCALATION:", result.alert)
        self.assertIn("WITHHELD", result.alert)
        for f in flags:
            self.assertIn(f["source_url"], result.alert)  # signal not discarded

    # ---- strip mode ----
    def test_strip_mode_removes_banned_line_without_blocking(self):
        flags = _flags()
        alert = draft_alert(flags) + "\nNote: treatment should be prescribed to patients."
        result = enforce_guardrails(alert, flags, banned_mode="strip")
        self.assertFalse(result.blocked)
        self.assertIn("banned_language", _types(result))
        self.assertNotIn("prescribed", result.alert)
        self.assertIn("ESCALATION:", result.alert)

    # ---- live integration: the real pipeline output passes its own guardrail ----
    def test_run_scan_output_passes_guardrail(self):
        from src.orchestrator import run_scan
        result = run_scan("data/incoming/incoming_multi_signal.json")
        self.assertTrue(result["guardrail"]["passed"])
        self.assertFalse(result["guardrail"]["blocked"])
        self.assertEqual(result["guardrail"]["violations"], [])


if __name__ == "__main__":
    unittest.main()
