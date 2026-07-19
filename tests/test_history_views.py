"""Phase A: hermetic tests for the read-only history analytics (src/insights/history_views.py).

Includes `test_surge_badge_parity`, which cross-checks `is_surge_like` against the REAL
`detect_surge` across a grid of inputs — including the exact threshold boundaries — so the diff
view's surge badge cannot silently drift from the detector it mirrors.
"""
import unittest

import pandas as pd

from src.config import SURGE_MIN_DAILY_NEW, SURGE_MIN_PCT_GROWTH, SURGE_MAX_GAP_DAYS
from src.signal.detectors import detect_surge
from src.insights.history_views import (
    zone_trend_series, compute_history_diff, top_zones_by_recent_change,
    is_surge_like, latest_reporting_round,
)


def _hist(rows):
    cols = ["date", "province", "health_zone", "suspected_cases",
            "confirmed_cases", "deaths", "source_url", "report_date"]
    return pd.DataFrame(rows, columns=cols)


def _r(date, zone, confirmed, deaths=0, province="Ituri"):
    return {"date": date, "province": province, "health_zone": zone,
            "suspected_cases": None, "confirmed_cases": confirmed, "deaths": deaths,
            "source_url": "http://x", "report_date": date}


# A fixture with one of each shape. Global latest round = 2026-06-17.
FIXTURE = _hist([
    _r("2026-06-10", "Zchanged", 100, 20), _r("2026-06-17", "Zchanged", 300, 60),   # surging, current
    _r("2026-06-01", "Zstale", 10, 2),     _r("2026-06-08", "Zstale", 300, 40),      # big jump but stale
    _r("2026-06-10", "Zrev", 200, 30),     _r("2026-06-17", "Zrev", 150, 30),        # revision (neg delta)
    _r("2026-06-17", "Znew", 25, 5, province="North Kivu"),                          # first appearance
])


class TestTrendSeries(unittest.TestCase):
    def test_tidy_sorted_and_filtered(self):
        s = zone_trend_series(FIXTURE, zones=["Zchanged", "Znew"])
        self.assertEqual(list(s.columns), ["date", "health_zone", "confirmed_cases", "deaths"])
        self.assertEqual(set(s["health_zone"]), {"Zchanged", "Znew"})
        # Znew is a single-point series — kept, not dropped.
        self.assertEqual((s["health_zone"] == "Znew").sum(), 1)
        # sorted by zone then date
        zc = s[s["health_zone"] == "Zchanged"]["date"].tolist()
        self.assertEqual(zc, sorted(zc))

    def test_all_zones_when_unfiltered(self):
        s = zone_trend_series(FIXTURE)
        self.assertEqual(set(s["health_zone"]), {"Zchanged", "Zstale", "Zrev", "Znew"})

    def test_empty_history(self):
        self.assertTrue(zone_trend_series(_hist([])).empty)
        self.assertIsNone(latest_reporting_round(_hist([])))


class TestLatestRound(unittest.TestCase):
    def test_global_max_date(self):
        self.assertEqual(latest_reporting_round(FIXTURE), pd.Timestamp("2026-06-17"))


class TestHistoryDiff(unittest.TestCase):
    def setUp(self):
        self.diff = {d["health_zone"]: d for d in compute_history_diff(FIXTURE)}

    def test_changed_zone_deltas_and_surge(self):
        d = self.diff["Zchanged"]
        self.assertEqual(d["status"], "changed")
        self.assertEqual(d["delta_confirmed"], 200)
        self.assertEqual(d["delta_deaths"], 40)
        self.assertEqual(d["days_between"], 7)
        self.assertTrue(d["surge_like"])            # 200 / 7 ≈ 28.6/day

    def test_new_zone_labeled_new_no_deltas(self):
        d = self.diff["Znew"]
        self.assertEqual(d["status"], "new")
        self.assertIsNone(d["delta_confirmed"])
        self.assertIsNone(d["prior_confirmed"])
        self.assertIsNone(d["days_between"])
        self.assertFalse(d["surge_like"])

    def test_stale_zone_flagged_and_never_surge(self):
        d = self.diff["Zstale"]
        self.assertEqual(d["status"], "stale")
        self.assertEqual(d["delta_confirmed"], 290)  # its own last-two delta is preserved
        self.assertFalse(d["surge_like"])            # stale is NEVER flagged as a current surge

    def test_negative_delta_preserved(self):
        self.assertEqual(self.diff["Zrev"]["delta_confirmed"], -50)

    def test_sorted_surge_first_then_magnitude(self):
        order = [d["health_zone"] for d in compute_history_diff(FIXTURE)]
        self.assertEqual(order[0], "Zchanged")       # only surge_like row sorts first
        self.assertLess(order.index("Zstale"), order.index("Znew"))  # 290 before 25

    def test_empty_history(self):
        self.assertEqual(compute_history_diff(_hist([])), [])


class TestTopZones(unittest.TestCase):
    def test_ranks_by_change_magnitude(self):
        # magnitudes: Zstale 290, Zchanged 200, Znew 25 (emergence), Zrev 50
        self.assertEqual(top_zones_by_recent_change(FIXTURE, n=2), ["Zstale", "Zchanged"])

    def test_respects_n_and_short_history(self):
        self.assertEqual(len(top_zones_by_recent_change(FIXTURE, n=3)), 3)
        self.assertLessEqual(len(top_zones_by_recent_change(FIXTURE, n=99)), 4)


class TestIsSurgeLikeBoundaries(unittest.TestCase):
    def test_daily_new_boundary(self):
        days = 5
        delta_at = int(round(SURGE_MIN_DAILY_NEW * days))      # exactly the threshold
        # big prior so pct_growth stays well under its threshold; isolate the daily_new rule
        self.assertTrue(is_surge_like(10_000, 10_000 + delta_at, days))
        self.assertFalse(is_surge_like(10_000, 10_000 + delta_at - 1, days))

    def test_pct_growth_boundary(self):
        prior = 100
        delta_at = int(round(SURGE_MIN_PCT_GROWTH * prior))    # exactly the threshold
        self.assertTrue(is_surge_like(prior, prior + delta_at, SURGE_MAX_GAP_DAYS))
        self.assertFalse(is_surge_like(prior, prior + delta_at - 1, SURGE_MAX_GAP_DAYS))

    def test_gap_and_zero_days(self):
        self.assertTrue(is_surge_like(100, 400, SURGE_MAX_GAP_DAYS))       # at the gap: valid
        self.assertFalse(is_surge_like(100, 400, SURGE_MAX_GAP_DAYS + 1))  # over the gap: invalid
        self.assertFalse(is_surge_like(100, 400, 0))                       # zero days: invalid


class TestSurgeBadgeParity(unittest.TestCase):
    """is_surge_like must agree with the real detect_surge across inputs, especially at the
    exact threshold boundaries (the classic off-by-one drift)."""

    def _detector_fires(self, prior_conf, current_conf, days):
        base = pd.Timestamp("2026-06-01")
        prior_df = _hist([_r(base.strftime("%Y-%m-%d"), "Z", prior_conf)])
        inc_df = _hist([_r((base + pd.Timedelta(days=days)).strftime("%Y-%m-%d"), "Z", current_conf)])
        flags = detect_surge(inc_df, prior_df)
        return any(f["detector"] == "surge" for f in flags)

    def test_parity_across_grid_including_boundaries(self):
        g = SURGE_MAX_GAP_DAYS
        d_at = int(round(SURGE_MIN_DAILY_NEW * 5))          # daily_new boundary at days=5
        p_at = int(round(SURGE_MIN_PCT_GROWTH * 100))       # pct_growth boundary at prior=100
        cases = [
            # (prior, current, days) — boundary cases first
            (10_000, 10_000 + d_at, 5),        # daily_new exactly at threshold
            (10_000, 10_000 + d_at - 1, 5),    # just under
            (100, 100 + p_at, g),              # pct_growth exactly at threshold
            (100, 100 + p_at - 1, g),          # just under
            (100, 400, g),                     # days exactly SURGE_MAX_GAP_DAYS
            (100, 400, g + 1),                 # days one over the gap
            (100, 400, 0),                     # zero days
            # ordinary / other cases
            (100, 300, 7), (50, 55, 10), (200, 150, 5),    # incl. a negative delta (revision)
            (0, 5, 3), (1000, 1001, 1), (10, 400, 14),
        ]
        for prior, current, days in cases:
            with self.subTest(prior=prior, current=current, days=days):
                self.assertEqual(
                    is_surge_like(prior, current, days),
                    self._detector_fires(prior, current, days),
                    msg=f"badge/detector disagree for prior={prior} current={current} days={days}",
                )


if __name__ == "__main__":
    unittest.main()
