# Walkthrough — Phase B1: multi-country plumbing (`disaster_id`)

Branch: `feature/multi-country`. B1 is the **plumbing slice** of making the agent multi-country:
one shared history file gains a `disaster_id` partition column, the app shows the active outbreak,
and every history view + the live scan scope to it. **No safety-adjacent code is touched** — the
extraction guards and prompt stay DRC-specific and are deferred to B2. `src/signal`, `src/alert`,
`src/guardrails`, and `evals` stay frozen.

## The design in one paragraph

One history file, not per-outbreak files, so cross-outbreak comparison stays possible later.
`disaster_id` is the leading column and part of the identity/dedup key, so two outbreaks that share
a zone name never collide. The active outbreak is a **configuration** choice (`RELIEFWEB_DISASTER_ID`
→ a profile in `src/outbreaks.py`), surfaced as a **display-only header** — switching outbreaks is a
config change, not a per-session action. Detectors never see `disaster_id`; scoping happens in the
unfrozen ingestion/live-scan layer *before* detection, which is why the frozen detector core and
eval set are untouched.

## What changed

| File | Change |
|---|---|
| `src/contract.py` (new) | The single authoritative 9-column contract + identity key. **Leaf module — imports nothing**, so no circular import is possible; `ingestion`, `history_store`, `candidate_store` import from it. |
| `src/outbreaks.py` (new) | `OutbreakProfile` registry keyed by `disaster_id`; `active_outbreak()` resolves from `RELIEFWEB_DISASTER_ID`. Docstring flags **B1-consumed** (`disaster_id`, `display_name`) vs **B2-reserved** (`denied_zone_aliases`, `disease`, `country_iso3`, …) fields. |
| `scripts/migrate_add_disaster_id.py` (new) | One-time: back up to `.bak`, insert `disaster_id=52586` as column 1, atomic, **idempotent**. Never on a load path. |
| `scripts/rollback_disaster_id.py` (new) | Restores from `.bak`; **refuses** if any row has a `disaster_id ≠ 52586` (would lose a second outbreak's data). |
| `history_store.py`, `ingestion.py`, `candidate_store.py` | Import the contract from `src/contract.py` (kills the duplicated definitions); dedup/identity now include `disaster_id`. |
| `ingestion.load_incoming_report` | Tags each record: per-record > file-level > active outbreak (documented). |
| `ingestion.get_prior_snapshot` / `ingestion_pipeline` | Optional `disaster_id=None` filter (default = no filter → **frozen eval runner unchanged**). |
| `extract_report` | Gains a `disaster_id` arg, filled into the record "from args, never the model". **Guards + prompt unchanged.** |
| `live_scan` | `prior_excluding_source` / `detect_new_data` / `run_scan_on_new_data` scope the prior to the active `disaster_id` before detection. |
| `insights/history_views.py` | All four functions gain `disaster_id=None`; views scope per-outbreak (None = cross-outbreak). |
| `app_streamlit.py` | Display-only **active-outbreak header** (`🌍 DRC Ebola 2026 · 52586`); History tab scopes every view to the active outbreak. |
| `data/history.csv` | Migrated to 9 columns (all rows `disaster_id=52586`). `*.bak` gitignored. |
| `.env.example`, `docs/context.md`, `SKILL.md` | Document `disaster_id`, the incoming precedence rule, and the active-outbreak selector. |

## Verification results

- **Full suite: 88 tests OK** (77 updated for the schema + 11 new; 1 live D2 test skipped without a key). ✅
- **Frozen evals still green: 5/5 scenarios pass** via `python -m evals.run_evals`, `evals/` unchanged — the regression baseline holds. ✅
- **Migration**: adds `disaster_id` as column 1, all rows `52586`, backup written, idempotent on re-run. **Rollback**: restores from `.bak`, and **refuses** when a foreign `disaster_id` is present. ✅
- **No circular import**: `src/contract.py` imports nothing; the full import graph loads clean. ✅
- **Dedup by disaster**: same zone/date/source under two `disaster_id`s → both rows kept. ✅
- **Incoming precedence**: per-record > file-level > active outbreak, all three asserted. ✅
- **View/scan scoping**: `compute_history_diff` / `zone_trend_series` / `top_zones_by_recent_change` / `get_prior_snapshot` / `prior_excluding_source` all scope to one outbreak; unscoped (None) sees both. ✅
- **App renders clean** (headless AppTest): the outbreak header shows `DRC Ebola 2026 · 52586`; the History tab's multiselect + diff table render; zero exceptions. ✅

## Manual walkthrough checklist (`streamlit run app_streamlit.py`)

- [ ] Header shows **🌍 Active outbreak: DRC Ebola 2026 · disaster_id 52586**.
- [ ] History tab trends + diff look identical to Phase A (all seed rows are one outbreak, so scoping is a visible no-op today).
- [ ] Promote a report in Live scan → the new history row carries `disaster_id=52586`.
- [ ] `python -m evals.run_evals` → 5/5.
- [ ] Migration idempotency: re-run `python -m scripts.migrate_add_disaster_id` → "already migrated".

## What is NOT in B1 (deferred to B2)

The extraction **deny-list** and **prompt** are still DRC-specific; generalizing them (config-driven
`denied_zone_aliases` + templated `{disease}`/`{country}`) is B2, reviewed on its own so the
safety-adjacent extraction changes get scrutiny separately. Runtime outbreak switching (a selectbox +
`live_sources` `disaster_id` argument) is intentionally out — the header is display-only, driven by
config, per the approved design.
