"""Pure, read-only analytics over the history store for the UI's 📊 History tab (Phase A).

Two views, both computed here so the UI layer stays thin and everything is hermetically
testable:
- `zone_trend_series` — tidy per-zone time series for the trend charts.
- `compute_history_diff` — the "since the previous report" per-zone diff.

Both key on `confirmed_cases` and `deaths` only (always present in history); `suspected_cases`
may be null and is not used here. Dates use `date` (the as-of date), matching detector
semantics.

`is_surge_like` re-derives the surge threshold condition for the diff's badge. It deliberately
does NOT import from src/signal (frozen), and it is NOT trusted to stay in sync by inspection:
`tests/test_history_views.py::test_surge_badge_parity` cross-checks it against the real
`detect_surge` across a grid of inputs (including the exact threshold boundaries), so any drift
fails the suite.
"""
from typing import Dict, List, Optional

import pandas as pd

from src.config import SURGE_MIN_DAILY_NEW, SURGE_MIN_PCT_GROWTH, SURGE_MAX_GAP_DAYS

_TREND_COLUMNS = ["date", "health_zone", "confirmed_cases", "deaths"]


def _prepared(history_df: pd.DataFrame) -> pd.DataFrame:
    """Copy with `date` parsed to datetime, so callers can pass a raw or loaded frame."""
    df = history_df.copy()
    df["date"] = pd.to_datetime(df["date"])
    return df


def _prepared_scoped(history_df: pd.DataFrame, disaster_id) -> pd.DataFrame:
    """_prepared, then scoped to one outbreak when disaster_id is given (None = all outbreaks).
    All views scope BEFORE computing, so staleness and 'latest round' are per-outbreak."""
    df = _prepared(history_df)
    return df if disaster_id is None else df[df["disaster_id"] == disaster_id]


def latest_reporting_round(history_df: pd.DataFrame, disaster_id=None) -> Optional[pd.Timestamp]:
    """The most-recent as-of date for the (optionally outbreak-scoped) history. The trend charts
    mark this date and the diff uses it for staleness — one shared, per-outbreak definition."""
    if history_df is None or len(history_df) == 0:
        return None
    df = _prepared_scoped(history_df, disaster_id)
    return None if len(df) == 0 else df["date"].max()


def zone_trend_series(history_df: pd.DataFrame, zones=None, disaster_id=None) -> pd.DataFrame:
    """Tidy long frame [date, health_zone, confirmed_cases, deaths], sorted by zone then date.

    Optionally scoped to one outbreak (`disaster_id`) and filtered to `zones`. Sparse zones (1-2
    rows) are kept as-is — the UI renders them honestly with point markers rather than hiding."""
    if history_df is None or len(history_df) == 0:
        return pd.DataFrame(columns=_TREND_COLUMNS)
    df = _prepared_scoped(history_df, disaster_id)[_TREND_COLUMNS]
    if zones is not None:
        df = df[df["health_zone"].isin(list(zones))]
    return df.sort_values(["health_zone", "date"]).reset_index(drop=True)


def is_surge_like(prior_confirmed, current_confirmed, days_between) -> bool:
    """True iff the confirmed-case change would trip the surge detector's threshold condition.

    Mirrors src/signal/detectors.detect_surge: valid only for 0 < days <= SURGE_MAX_GAP_DAYS,
    then fires when daily_new >= SURGE_MIN_DAILY_NEW OR pct_growth >= SURGE_MIN_PCT_GROWTH.
    Kept parity-tested against the real detector (see module docstring)."""
    days = int(days_between)
    if days <= 0 or days > SURGE_MAX_GAP_DAYS:
        return False
    delta = int(current_confirmed) - int(prior_confirmed)
    daily_new = delta / days
    pct_growth = delta / max(int(prior_confirmed), 1)
    return daily_new >= SURGE_MIN_DAILY_NEW or pct_growth >= SURGE_MIN_PCT_GROWTH


def _change_magnitude(delta_confirmed, current_confirmed) -> int:
    """Ranking magnitude: |Δconfirmed| for a zone with a prior, else the current count (a new
    zone's emergence from zero is its full jump). Used to order the diff table AND to pick the
    default chart zones, so both agree on 'what changed fastest'."""
    return abs(delta_confirmed) if delta_confirmed is not None else int(current_confirmed)


def compute_history_diff(history_df: pd.DataFrame, disaster_id=None) -> List[Dict]:
    """Per-zone diff between each zone's two most-recent rows, optionally scoped to one outbreak.

    Each entry: health_zone, province, prior/current/delta for confirmed and deaths,
    days_between, status, surge_like. Status precedence:
    - `new`   : the zone has a single row (no prior to diff); deltas are None.
    - `stale` : the zone's latest row predates the latest reporting round (it did not appear in
                the most recent report for this outbreak).
    - `changed`: otherwise.
    `surge_like` is set only for `changed` rows (a stale zone is never flagged as a current
    surge; a new zone has no prior). Sorted by (surge_like, change magnitude) descending.
    Negative deltas (data revisions) are preserved, not clamped."""
    if history_df is None or len(history_df) == 0:
        return []
    df = _prepared_scoped(history_df, disaster_id)
    if len(df) == 0:
        return []
    global_max = df["date"].max()

    rows: List[Dict] = []
    for zone, g in df.groupby("health_zone"):
        g = g.sort_values("date")
        current = g.iloc[-1]
        cur_conf, cur_deaths = int(current["confirmed_cases"]), int(current["deaths"])
        base = {
            "health_zone": zone,
            "province": current["province"],
            "current_confirmed": cur_conf,
            "current_deaths": cur_deaths,
        }
        if len(g) == 1:
            rows.append({**base, "status": "new",
                         "prior_confirmed": None, "delta_confirmed": None,
                         "prior_deaths": None, "delta_deaths": None,
                         "days_between": None, "surge_like": False})
            continue

        prior = g.iloc[-2]
        pri_conf, pri_deaths = int(prior["confirmed_cases"]), int(prior["deaths"])
        days = int((current["date"] - prior["date"]).days)
        stale = current["date"] < global_max
        status = "stale" if stale else "changed"
        surge_like = status == "changed" and is_surge_like(pri_conf, cur_conf, days)
        rows.append({**base, "status": status,
                     "prior_confirmed": pri_conf, "delta_confirmed": cur_conf - pri_conf,
                     "prior_deaths": pri_deaths, "delta_deaths": cur_deaths - pri_deaths,
                     "days_between": days, "surge_like": surge_like})

    rows.sort(key=lambda r: (r["surge_like"],
                             _change_magnitude(r["delta_confirmed"], r["current_confirmed"])),
              reverse=True)
    return rows


def top_zones_by_recent_change(history_df: pd.DataFrame, n: int = 5, disaster_id=None) -> List[str]:
    """The n zones changing fastest by recent Δconfirmed (largest movers first), for the chart's
    default selection, optionally scoped to one outbreak. A new zone ranks by its current count
    (emergence from zero)."""
    diff = compute_history_diff(history_df, disaster_id)  # sorted by (surge_like, magnitude) desc
    ordered = sorted(
        diff,
        key=lambda r: _change_magnitude(r["delta_confirmed"], r["current_confirmed"]),
        reverse=True,
    )
    return [r["health_zone"] for r in ordered[:n]]
