import unittest
import re
from src.alert.alert_agent import draft_alert

DATE_KEYS = {"date", "report_date", "report_date_prior"}


def collect_allowed_numbers(flags):
    """Exact string renderings the template may emit for each flag field.

    Traceability is strict: a number found in the alert must be one of these exact
    representations. There is deliberately no float-proximity tolerance and no blanket
    allowance for small integers, so a wrong-but-close number fails the test.
    """
    allowed = set()
    for f in flags:
        for key, val in f.items():
            if key in DATE_KEYS and isinstance(val, str):
                allowed.update(re.findall(r"\d+", val))
            elif isinstance(val, bool):
                continue
            elif isinstance(val, int):
                allowed.add(str(val))
            elif isinstance(val, float):
                allowed.add(f"{val:.1f}")          # e.g. daily_new
                allowed.add(f"{val * 100:.1f}")    # e.g. cfr / pct_growth as percent
    return allowed


def assert_numbers_traceable(test, alert, flags):
    allowed = collect_allowed_numbers(flags)
    for num in re.findall(r"\d+\.?\d*", alert):
        test.assertIn(
            num, allowed,
            f"Number {num} in alert cannot be traced to any input flag field.",
        )


def signal_line_for(alert, zone):
    """Return the signal line (starts with '-') naming `zone`, or None."""
    for line in alert.split("\n"):
        if line.strip().startswith("-") and zone in line:
            return line
    return None


class TestAlertAgent(unittest.TestCase):
    def setUp(self):
        # One flag of every detector type, each with a DISTINCT source_url so the
        # per-line source check is meaningful. cfr_shift is first => headline.
        self.flags = [
            {
                "detector": "cfr_shift",
                "health_zone": "Beni",
                "province": "North Kivu",
                "confirmed_prior": 78,
                "deaths_prior": 16,
                "cfr_prior": 0.20,
                "confirmed_incoming": 90,
                "deaths_incoming": 40,
                "cfr_incoming": 0.44,
                "cfr_diff": 0.24,
                "source_url": "https://example.org/cfr",
                "report_date": "2026-06-21",
            },
            {
                "detector": "new_zone",
                "health_zone": "Komanda",
                "province": "Ituri",
                "confirmed_cases": 9,
                "source_url": "https://example.org/newzone",
                "report_date": "2026-06-21",
            },
            {
                "detector": "surge",
                "health_zone": "Mongbwalu",
                "province": "Ituri",
                "confirmed_prior": 120,
                "confirmed_incoming": 200,
                "daily_new": 26.7,
                "pct_growth": 0.67,
                "source_url": "https://example.org/surge",
                "report_date": "2026-06-21",
            },
            {
                "detector": "stale_or_missing",
                "type": "missing_zone",
                "health_zone": "Bunia",
                "province": "Ituri",
                "confirmed_cases_prior": 55,
                "deaths_prior": 12,
                "source_url_prior": "https://example.org/missing",
                "report_date_prior": "2026-06-19",
            },
            {
                "detector": "stale_or_missing",
                "type": "null_field",
                "health_zone": "Rwampara",
                "province": "Ituri",
                "null_fields": ["confirmed_cases"],
                "source_url": "https://example.org/null",
                "report_date": "2026-06-21",
            },
        ]

    def test_normal_alert_structure_and_escalation(self):
        alert = draft_alert(self.flags)
        # One signal line per input flag.
        lines = [l for l in alert.split("\n") if l.strip().startswith("-")]
        self.assertEqual(len(lines), len(self.flags))
        # Every detector type is named.
        for det in ["cfr_shift", "new_zone", "surge", "stale_or_missing"]:
            self.assertIn(det, alert)
        # Escalation line is present on the normal path.
        self.assertIn("ESCALATION:", alert)

    def test_each_signal_line_has_its_own_source_url(self):
        alert = draft_alert(self.flags)
        cases = [
            ("Beni", "https://example.org/cfr"),
            ("Komanda", "https://example.org/newzone"),
            ("Mongbwalu", "https://example.org/surge"),
            ("Bunia", "https://example.org/missing"),       # missing_zone -> source_url_prior
            ("Rwampara", "https://example.org/null"),
        ]
        for zone, url in cases:
            line = signal_line_for(alert, zone)
            self.assertIsNotNone(line, f"No signal line found for zone {zone}")
            self.assertIn(url, line, f"source_url for {zone} is missing from its signal line")

    def test_numbers_traceable_tight(self):
        alert = draft_alert(self.flags)
        assert_numbers_traceable(self, alert, self.flags)

    def test_no_reasoning_parameter(self):
        # draft_alert must not accept a reasoning argument so the LLM-leak path
        # cannot be re-introduced at the call site.
        import inspect
        params = inspect.signature(draft_alert).parameters
        self.assertNotIn("reasoning", params)

    def test_empty_flags_path(self):
        alert = draft_alert([])
        self.assertIn("No active", alert)        # no-signals template
        self.assertIn("ESCALATION:", alert)      # escalation still present
        self.assertNotIn("999", alert)           # reasoning still not rendered
        self.assertNotIn("Rationale", alert)


if __name__ == "__main__":
    unittest.main()
