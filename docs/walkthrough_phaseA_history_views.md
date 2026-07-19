# Walkthrough — Phase A: History tab (trends + since-the-previous-report diff)

Branch: `feature/history-views`. Two read-only views over `data/history.csv`, added as a third
Streamlit tab. No numbers are read from prose and nothing is scanned here — this is pure
visualization of the accumulated history. The freeze holds: no changes under `src/signal`,
`src/alert`, `src/guardrails`, or `evals`.

## What the tab does

A new **📊 History** tab (the Live scan and Scenario runner tabs are byte-for-byte unchanged)
with two sections:

1. **Per-zone trends.** A searchable `st.multiselect` of zones, **defaulting to the 5
   fastest-changing by recent Δ confirmed** (`top_zones_by_recent_change`) — what should catch a
   coordinator's eye first, not raw size. Two stacked Altair charts (Confirmed, then Deaths), one
   line per zone, with **`mark_line` + `mark_point`** so sparse series stay visible. The two
   charts share one date axis (`resolve_scale(x="shared")`) and both carry a dashed **vertical
   rule at the global most-recent reporting round** — computed across *all* history
   (`latest_reporting_round`), the same date the diff uses for staleness — with the caption
   *"Marker shows the most recent reporting round in history."* Any selected zone with < 3 points
   gets an honest *"Sparse series shown honestly"* caption naming it.
2. **Since the previous report.** A sortable table from `compute_history_diff`, largest movers
   first: `zone · province · status · prior/current/Δ confirmed · prior/current/Δ deaths · days`.
   Status badges: **🔴 SURGE** (the change would trip the surge detector), **🟠 changed**,
   **🟢 NEW** (first appearance, deltas shown as "—"), **⚪ STALE**. The legend spells the stale
   meaning neutrally: *"this zone did not appear in the most recent report"* — it never implies the
   outbreak improved. A stale zone is **never** flagged as a current surge even if its own last
   two rows jumped.

## Diff semantics (per your approval)

Per zone, its **two most-recent rows**. `days_between` is that zone's own gap. First appearance
(one row) → `new`. A zone whose latest row predates the global latest round → `stale`. Otherwise
`changed`. Negative deltas (data revisions) are preserved, not clamped.

## Threshold badge — option (b), and why not (a)

Option (a) ("lift the surge check into a function **in `src/signal`**") would require modifying
frozen, evaluated code — and to actually prevent drift it would have to refactor `detect_surge`
to call the shared helper, a real freeze violation. So I took **(b)**: `is_surge_like` lives in
the unfrozen `src/insights/history_views.py`, and **`test_surge_badge_parity`** cross-checks it
against the **real `detect_surge`** across a grid of inputs — including the exact boundaries you
called out: `days = SURGE_MAX_GAP_DAYS`, `daily_new = SURGE_MIN_DAILY_NEW`,
`pct_growth = SURGE_MIN_PCT_GROWTH`, and `days = 0`. If either side's threshold logic changes, the
test fails. No comment is relied on to prevent drift.

## What changed

| File | Change |
|---|---|
| `src/insights/history_views.py` (new) | `zone_trend_series`, `compute_history_diff`, `top_zones_by_recent_change`, `is_surge_like`, `latest_reporting_round` — pure, no Streamlit. |
| `src/insights/__init__.py` (new) | Package marker/docstring. |
| `app_streamlit.py` | Adds the third **📊 History** tab (two sections) plus two render helpers; imports `load_history` + the insights functions + Altair. Existing two tab bodies untouched. |
| `tests/test_history_views.py` (new) | 16 hermetic tests incl. the surge-badge parity/boundary test. |

## Verification results

- **Full suite: 77 tests OK** (61 unchanged + 16 new; 1 live D2 test skipped without a key). ✅
- **Surge-badge parity**: `is_surge_like` matches the real `detect_surge` on every grid case,
  including all four boundary cases. ✅
- **Diff logic** (hermetic): correct Δ/days for a changed zone; `new` labeled with no deltas;
  `stale` flagged and **never** surge; negative delta preserved; sort = (surge, magnitude) desc;
  empty history → `[]`. ✅
- **Default selection** ranks by recent Δ confirmed: on the seed it picks
  Bunia/Rwampara/Mongbwalu/Beni/Nyankunde; Oicha (2 points, smaller Δ) is outside the default but
  selectable. ✅
- **Headless AppTest**: the History tab renders with **zero exceptions**; selecting the 2-point
  Oicha shows the sparse caption; the latest-round caption is present; clearing the selection shows
  the pick-a-zone prompt without crashing. ✅
- **No deprecations**: the new `st.dataframe` uses `width="stretch"` (not the retired
  `use_container_width`). ✅

## Manual walkthrough checklist (visual — run `streamlit run app_streamlit.py`)

- [ ] 📊 History tab: two charts stacked, one color per zone, shared date axis, dashed latest-round
      rule visible on both.
- [ ] Select **Oicha** (2 points): renders as a short line **with visible point markers**; sparse
      caption names it. A hypothetical 1-point zone shows a single visible dot.
- [ ] Diff table sorts largest movers first; a surging zone shows the **red SURGE** tint; NEW is
      green, STALE is grey, and the legend reads "did not appear in the most recent report."
- [ ] Live scan tab, Scenario runner tab, and the promote/scan flow behave exactly as before.

## Notes flagged for review (deliberate choices)

- **Stale zones can appear in the default chart set.** The default ranks by each zone's most-recent
  Δ confirmed regardless of staleness, so a zone that jumped hard and then went silent can surface
  — arguably *more* worth seeing. Easy to switch to "exclude stale from the default" if you'd
  rather. (In the current seed no zone is stale, so this doesn't affect today's demo.)
- **Badges are emoji + a cell tint**, not custom HTML — robust inside `st.dataframe`, which keeps
  the table sortable and exportable.

## Not done (out of scope)

No detector/alert/guardrail/eval changes; no data-contract change; no new dependency (Altair ships
with Streamlit). No writes to history from this tab — it is strictly read-only.
