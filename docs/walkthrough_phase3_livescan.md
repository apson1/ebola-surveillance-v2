# Walkthrough — Phase 3: End-to-end live-scan UI

Branch: `feature/reliefweb-phase-3`. This phase wires Phases 1 and 2 into the Streamlit app as a
single operator flow — **fetch a real report → extract candidate records → a human reviews and
approves → promote into history → run a scan → read the alert** — without ever letting a model's
number reach history or the brief unreviewed. No detector, alert, guardrail, or eval code was
touched.

## The flow, and why it is shaped this way

The app now has two tabs:

- **📡 Live scan (ReliefWeb)** — the new end-to-end flow.
- **🧪 Scenario runner** — the original canned-scenario demo, moved verbatim into its own tab
  (same selectbox, same "Simulate Guardrail Violation" path, same rendering). Behavior unchanged.

Inside the Live scan tab, the operator moves top to bottom:

0. **Load a specific report by ID** (additive, above the recent list). A small numeric input —
   **"Load specific report by ID"** + a **Load candidates** button — extracts from a known
   ReliefWeb report id directly (e.g. WHO **4221419**, *Bundibugyo External Sit Rep 09*, which has
   per-zone data), so a demo doesn't depend on which reports happen to be newest today. It fetches
   the body directly via `fetch_report_body(id)` and runs the **same** extract → validate → review
   flow as the picker (`source_url` is the stable `https://reliefweb.int/node/{id}`). A non-numeric
   entry is rejected with a warning; a report that doesn't resolve or has an empty body shows the
   same "Could not fetch this report's body" panel as the picker path. This does **not** replace
   the recent-reports picker.
1. **Pick a report.** The recent DRC Ebola situation reports (Phase 1) are listed with a
   **🔄 Refresh latest report** control and a visible **"fetched N ago"** line so the 15-minute
   cache is legible on screen (not hidden in a tooltip). Each report has an **Extract** button.
2. **Review candidates.** Extract → validate (the Phase 2 two-model check) runs once and is
   cached in session state, so Streamlit reruns don't re-call the LLM. Each surviving record is
   an **amber candidate card**: the **health zone and the numbers are the largest element at the
   top**, the verbatim snippet reads below in body text, and an amber `CANDIDATE` badge and an
   **Approve** checkbox sit to the side. Rejected records render as **read-only red cards with a
   human-readable reason** — including the invented-quote case ("Snippet is not a verbatim
   quote… possible invented quote").
3. **Two deliberate gates, not one.**
   - **✅ Promote approved records** (disabled until at least one is checked) is the irreversible
     step: it writes the approved rows into `data/history.csv` via `promote_candidates` and shows
     an honest green summary (how many actually entered history, and any that could not).
   - **🔍 Run scan on new data** appears only *after* promotion, as a separate click. Directly
     under it, in visible body text (per the requirement, not a tooltip):
     > *Compares this report's zones and numbers against the state of history before promotion,
     > so newly emerging and accelerating clusters are visible.*

The extra click is intentional — promotion and detection are different decisions and the operator
sees the state change between them.

## The scan-semantics problem, and the choice (option B)

Promoting the report's rows and then scanning history against itself is circular: once Beni@300
is in history, "is Beni new or surging?" answers *no*, because the report's own row is the thing
you would compare against. Four options were on the table; **option B** was chosen:

> The **prior** side of the comparison excludes any history row whose `source_url` matches the
> current report. So after promotion, the scan compares the report's records against history **as
> it was before this report** — regardless of *when* rows were promoted.

This is implemented in `prior_excluding_source(history_path, source_url)` (drop rows where
`source_url == this report`, then take the latest-per-zone snapshot). It is timestamp-independent,
so it stays correct even across multiple promotion batches from the same report in one session —
the reason to prefer B over a literal "promote then scan" (option C), which would suppress exactly
the signals the operator needs to see.

## What changed

| File | Change |
|---|---|
| `app_streamlit.py` | Restructured into two tabs. New Live scan flow (load-by-ID input, report picker, refresh + "fetched N ago", amber/green/red candidate cards, two-gate promote → scan, failure states, two-step candidate-store reset). Both the by-ID input and the picker route through a shared `_select_report` helper into the same extract → validate → review flow. Scenario runner moved verbatim into its tab. Alert rendering extracted into a shared `render_alert_brief` used by both tabs. |
| `src/ingestion/live_sources.py` | `LiveSourceResult` gained `fetched_at`; `fetch_recent_drc_ebola_reports(..., force=False)` bypasses the cache when the operator hits Refresh. |
| `src/live/live_scan.py` (new) | Option-B scan: `prior_excluding_source`, `detect_new_data` (deterministic, for tests), and `run_scan_on_new_data` (full detection → ranking → guard → alert → guardrail, returns `{alert, flags, guardrail}`). |
| `src/live/review.py` (new) | Pure, Streamlit-free review model: `build_review(extraction, validation)` → approvable cards (each with a `candidate_id`) + one merged rejected list with `human_reason(...)` text. Testable without a browser. |
| `tests/test_live_scan.py` (new) | Review-model tests + the hermetic e2e (extract → validate → promote → option-B scan) asserting **both** `new_zone` and `surge` fire. |

## Verification results

- **Hermetic e2e** (`test_e2e_new_zone_and_surge_both_fire_after_promotion`): prior history holds
  Beni@100 from an *old* report; the new report (a different `source_url`) yields Beni@300 and a
  brand-new Mongbwalu@80. After both are promoted into a temp history, the option-B scan (prior
  excluding the new report's `source_url`) fires **`new_zone` for Mongbwalu** and **`surge` for
  Beni** — proving B keeps both signals visible post-promotion. ✅ (this is the whole point of B
  over C)
- **`prior_excluding_source`** drops exactly the current report's rows and keeps the rest. ✅
- **Review model**: approvable vs. rejected split is correct; a national-total line lands in the
  rejected bucket; a **non-verbatim snippet renders as a rejected reason containing "verbatim"**
  (Req 3 confirmed — Phase 2's deterministic guard already routes it there). ✅
- **UI renders clean**: headless `AppTest` run of `app_streamlit.py` completes with **zero
  uncaught exceptions** (both tabs, all controls) in the default disabled-sources state. ✅
- **Full suite: 58 tests OK** (52 unchanged + 6 new; 1 live D2 test skipped without an API key). ✅
- **Freeze respected**: no changes under `src/signal`, `src/alert`, `src/guardrails`, or `evals`;
  no data-contract change. ✅

## Empty extraction does not dead-end the flow

When a picked (or by-ID) report yields **zero** per-zone records, the panel renders the honest
reason — *"No per-zone case figures were found in this report. National totals and unattributed
numbers are intentionally excluded."* — and stops there **without** blocking the operator. The
recent-reports picker is rendered at the tab level, above the selection block, so it stays
interactive: the operator can click **Extract** on a different report (or Load a different ID)
with **no refresh**. Each selection clears the cached extraction and re-runs, so a new report
extracts cleanly. Verified headlessly (`streamlit.testing` AppTest): after an empty extraction,
the honest message is present, the other report's Extract button is still available, and a second
pick runs without exceptions.

## Failure states (all handled visibly)

| Situation | What the operator sees |
|---|---|
| Sources disabled (no `RELIEFWEB_APPNAME`) / API error | An info/warning banner with the reason; the pipeline is otherwise unaffected. |
| 0 reports returned | "No recent DRC Ebola reports were returned." |
| Report body unavailable | "Could not fetch this report's body… Try another report." |
| Extraction found no per-zone figures | "No per-zone case figures were found… national totals are intentionally excluded." |
| Every record rejected | "Every extracted record was rejected — nothing to promote," with the reasons on each red card. |
| Nothing approved | The Promote button is disabled, with a caption saying so. |
| Scan flags nothing | The shared alert renderer shows the no-signal state. |
| Invented (non-verbatim) snippet | Red rejected card with the "possible invented quote" reason. |

## Requirements checklist (from the approval)

- Tabs; human picks from the recent list; per-record cards. ✅
- **Two buttons**, promote (irreversible) then a separate scan. ✅
- Amber (candidate) / green (promoted) / red (rejected/alert) palette. ✅
- Card layout: number + health_zone largest at top, snippet below, badge + Approve to the side. ✅
- "Fetched N ago" shown as visible text under Refresh. ✅
- `force` param on the fetch; two-step reset confirm; safe refresh. ✅
- Scan button labeled **"Run scan on new data"** with the exact one-line explanation visible
  (not a tooltip). ✅
- Prior excludes rows by **`source_url`**, not by promotion timestamp. ✅
- e2e asserts **`new_zone` and `surge` both fire**. ✅

## Not done (out of scope)

No changes to the detectors, alert, guardrail, or eval set. No automatic promotion, ever — the
only candidate→history path remains an explicit human click.
