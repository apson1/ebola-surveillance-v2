# Walkthrough — Phase B3: active-outbreak selector + name/ID separation

Branch: `feature/outbreak-selector`. A UX rework of the active-outbreak header, plus the
`live_sources` disaster_id plumbing that B1 deferred. `RELIEFWEB_DISASTER_ID` is now the
*default* active outbreak; the Live scan + History tabs let the operator select among configured
outbreaks per session. `src/signal`, `src/alert`, `src/guardrails`, `evals` stay frozen.

## Two changes

1. **Name / ID separation.** The old pill showed "Active outbreak: DRC Ebola 2026 · disaster_id
   52586" as if the readable name and the plumbing id were equally important. The header now shows
   only the outbreak **name** (in the selector); the `disaster_id` (plus GLIDE and country) live in
   a collapsed **"Outbreak details"** expander directly under it — one click away for debugging the
   report-id/disaster-id collision, out of the default view for everyone else.
2. **Selector.** A `st.selectbox` over the outbreak registry (option values are `disaster_id`s,
   labels via `format_func`), with `key="active_disaster_id"` as the single source of truth. Today
   it shows one option; a second profile is purely additive with zero further UI work.

## Session plumbing

- **Default + fallback.** Seeds `session_state.active_disaster_id` from the env `active_outbreak()`.
  If the session id is unset, stale, or the env id is unconfigured, it resets to a valid default
  and logs a warning (so a selectbox option that isn't in the registry can never be selected).
- **Reset-on-switch.** Changing the outbreak clears the in-progress live-scan flow (a loaded report
  belongs to the previous outbreak), so the operator starts clean.
- **Downstream.** Every Live scan + History call site reads the session `active_id` / profile and
  passes `disaster_id` into `fetch_recent_drc_ebola_reports`, `run_scan_on_new_data`, and
  `promote_candidates`.

## `live_sources`: disaster_id argument + per-outbreak cache

`fetch_recent_drc_ebola_reports(limit, disaster_id=RELIEFWEB_DISASTER_ID, force)` now takes the
outbreak to fetch (default = env, so existing callers are unchanged). The pinned query filters on
it; the fallback query uses the profile's `country_iso3` + `fallback_query` (generalizing the
DRC-hardcoded `"ebola"`/`"cod"`). The disk cache is keyed **per `(disaster_id, limit)`**, so
switching outbreaks never serves another outbreak's cached list — the exact stale-cache gap B1
flagged when deferring the selector. Old bare-`limit` cache entries simply miss and refetch.

## Scope boundary

The selector governs the **Live scan** and **History** tabs. The **Scenario runner** (canned DRC
data) and the frozen **eval** path stay on the env default — `active_outbreak()` remains the
env-resolved default and the backend functions keep their `active_outbreak()` defaults; only the
app's calls pass the session id. This keeps the eval path byte-stable.

## Verification results

- **Full suite: 110 tests OK** (106 after commit 1 → 110 after commit 2; 1 live D2 test skipped). ✅
- **Frozen evals: 5/5** — the selector doesn't touch the eval path. ✅
- **Cache keyed by disaster_id** (hermetic): outbreak B does not serve A's cached list; a same-id
  refetch within TTL serves cache with no new HTTP call. **Fallback** uses the profile's
  country/query. ✅
- **Selector (hermetic AppTest)**: default = env active; the `disaster_id` lives in the expander,
  not the header line; an invalid seeded session id falls back to the env default; switching (with
  a patched two-entry registry) updates `session_state.active_disaster_id`, re-scopes the History
  view, and clears the live-scan flow. ✅
- **Freeze intact**: changes only in `app_streamlit.py`, `src/ingestion/live_sources.py`, tests,
  docs. ✅

## Commit sequence (bisectable, three commits)

1. `live_sources` takes a `disaster_id` + per-outbreak cache key + profile-driven fallback.
2. App selector + session plumbing + call-site switch.
3. Docs (this file + `SKILL.md`/`context.md`).

## Manual walkthrough checklist (`streamlit run app_streamlit.py`)

- [ ] Header shows a **selector** with "DRC Ebola 2026" (name only); the `disaster_id` appears only
      inside the **"Outbreak details"** expander.
- [ ] Live scan and History behave exactly as before for the single configured outbreak.
- [ ] Scenario runner unchanged.
