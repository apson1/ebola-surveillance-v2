# live_data_plan.md — ReliefWeb live-data build

This is the authored, ordered plan. Work top to bottom. Each phase has a goal, files, and
acceptance criteria. Do not skip ahead. After each phase, produce a short walkthrough
artifact and stop for review. Do not merge to main without approval.

Read `docs/context.md` and `.agents/skills/ebola-surveillance/SKILL.md` first. The five hard
rules from the capstone still apply: rule-based detection stays in Python, the LLM never
computes a flag, every number in any output traces to a source, the human always decides,
and public aggregate data only. This plan extends the system with live data. It does not
relax the safety rules; it strengthens them, because the numbers are now coming from prose.

The three phases are independent enough that phase one alone is a shippable improvement.
Phases two and three build on it. Do not start phase two until phase one is merged and
working.

---

## Phase 1 — Source discovery (live ReliefWeb tool)

Goal: a working live API call using the approved RELIEFWEB_APPNAME, returning the latest
official DRC Ebola situation reports as structured metadata, wired into both the MCP
server and the Streamlit UI. No numbers are extracted in this phase. This phase is a
retrieval and demo capability.

### Tasks

1. Add `RELIEFWEB_APPNAME` to `src/config.py`, loaded from the environment. If unset, all
   live-source features degrade to disabled with a logged warning; the existing pipeline
   is unaffected.

2. Create `src/ingestion/live_sources.py` with one function:
   `fetch_recent_drc_ebola_reports(limit: int = 10) -> list[dict]`.
   - Calls the ReliefWeb `reports` endpoint with the appname, filtered to the DRC Ebola
     disaster (verify the exact filter against a live response; do not assume field
     names from memory).
   - Returns a list of `{title, source, date, url, id}` records, newest first.
   - Handles rate limits, network failures, and missing appname gracefully: on any of
     these, return an empty list and log the reason. Never raise into the caller.
   - Caches responses locally for at least 15 minutes to keep the free-tier daily quota
     safe and to make demos repeatable within a session.

3. Expose the function as a second MCP tool in `src/ingestion/mcp_server.py`, alongside
   the existing `load_reports`. Name it `list_recent_sources`. The tool returns the same
   list the function does.

4. Extend `app_streamlit.py` with a new section titled "Live official reports (ReliefWeb)".
   It calls `fetch_recent_drc_ebola_reports` and shows the returned reports as a list of
   clickable links with title, source, and date. If the appname is unset, show a small
   note saying live sources are disabled and how to enable them, rather than an empty
   panel. This section sits ABOVE the existing scenario runner and does not alter it.

5. Add `scripts/fetch_live_sources.py` that calls the tool and prints the latest reports,
   for a terminal demo and the walkthrough.

6. Add one live integration test in `tests/test_live_sources.py` that skips when
   RELIEFWEB_APPNAME is unset or the network is unavailable, matching the existing
   live-Gemini skip pattern. When it runs, it asserts the response is a non-empty list of
   the expected shape.

### Acceptance criteria

- `scripts/fetch_live_sources.py` prints at least three real DRC Ebola situation reports
  with today's or this week's date.
- The Streamlit UI shows the live source list next to the scenario runner, and clicking a
  link opens the real ReliefWeb page for that report.
- The full existing test suite still passes unchanged (39 tests plus the new skip-when-
  unavailable test).
- If `RELIEFWEB_APPNAME` is removed from `.env`, the app still runs end to end; only the
  live-sources panel is disabled with a message.
- Nothing about the detectors, alert, guardrail, memory, evals, or existing MCP tool has
  been modified.

### Non-goals for phase 1

- Do not extract numbers from report bodies. This phase returns metadata only.
- Do not feed ReliefWeb data into the detectors, the alert, the guardrail, or the eval
  set.
- Do not change the data contract.

---

## Phase 2 — Extraction with a safety layer (candidate history)

Goal: given a fetched ReliefWeb report body, extract case counts into the eight-column
data contract using an LLM, then validate the extraction with a second, independent LLM
pass before any record is allowed to become a candidate for history. History itself is
not modified in this phase. Everything lands in a separate `candidate_history` table that
requires human confirmation to promote.

This phase deliberately violates no hard rule, because numbers extracted from prose are
gated behind a two-model check and a human approval step. State this in the writeup.

### Tasks

1. Add `src/live/extract_report.py` with `extract_report(report_body: str, report_url: str,
   report_date: str) -> ExtractionResult`.
   - Uses Gemini with a strict Pydantic output schema matching the eight-column contract
     (date, province, health_zone, suspected_cases, confirmed_cases, deaths, source_url,
     report_date), returning zero or more records per report.
   - The prompt is deterministic in intent: extract only figures explicitly attributed to
     a health zone with a date. Do not infer, do not average, do not sum across zones.
   - Returns an `ExtractionResult` carrying the records plus a per-record trace: the
     exact sentence or snippet each number came from. If no snippet can be produced for
     a number, the number is dropped and the reason logged.

2. Add `src/live/validate_extraction.py` with `validate_extraction(records: list,
   report_body: str) -> ValidationResult`.
   - Uses Gemini with a DIFFERENT prompt phrased as a checker, not an extractor: given a
     record and a source snippet, does the snippet actually support this number?
   - Runs per-record. A record is validated only if the checker returns a strict pass.
     Any partial or hedged response is a fail.
   - Returns per-record validated/not-validated with reasons. Records that fail
     validation are dropped.

3. Add a candidate store: `src/live/candidate_store.py` with functions to write
   validated records to `data/candidate_history.csv`, deduplicated on the same identity
   key as history (date, province, health_zone, source_url). This file is separate from
   `data/history.csv` and is never read by the detectors directly.

4. Add a promotion step (function only, no UI yet in this phase):
   `promote_candidates(record_ids: list) -> int` in `candidate_store.py`, which copies
   approved candidate rows into `data/history.csv` using the existing `append_to_history`
   contract. This step is what makes the two-model check meaningful: nothing enters
   history until a human calls this with an explicit list of record IDs.

5. Add `tests/test_extraction.py`:
   - Given a crafted report body containing three numeric claims about known zones, the
     extractor returns three records, each with a source snippet.
   - Given the same records and body, the validator passes all three.
   - Given a record whose number does not match the snippet, the validator rejects it.
   - Given a report body with vague or unattributed numbers, the extractor returns zero
     records rather than guessing.

6. Add `scripts/extract_from_url.py` that takes a ReliefWeb report URL, fetches the body,
   runs extraction and validation, and prints the resulting candidate records with their
   snippets. This is the primary demo tool for phase 2.

### Acceptance criteria

- On at least one real ReliefWeb DRC Ebola report, the extraction script produces a
  non-empty list of validated candidate records, each with a visible source snippet.
- On a deliberately misleading crafted report (numbers that contradict their snippet),
  the validator rejects the false records.
- `data/history.csv` is never modified by any code path in this phase except through
  `promote_candidates` called explicitly.
- All existing tests still pass unchanged.
- The writeup addition explains the two-model check and the human-in-the-loop promotion
  step in plain language.

### Non-goals for phase 2

- No UI for extraction or promotion in this phase. That is phase 3.
- No automatic promotion. Ever. History updates are always a deliberate action.

---

## Phase 3 — Live scan (end to end)

Goal: a "scan latest report" button in the Streamlit UI that fetches the newest ReliefWeb
report, runs extraction and validation, shows the candidate records to a human, lets the
human approve promotion, and only then runs the full detection pipeline on the updated
history. The point of this phase is to make the whole flow visible in one demo.

### Tasks

1. Extend `app_streamlit.py` with a "Live scan" section. It shows:
   - The most recent ReliefWeb report (from phase 1's `list_recent_sources`).
   - A "Extract candidate records" button that runs phase 2's extraction and validation
     and displays the candidate records in a table, each with its source snippet.
   - A checkbox per candidate record letting the human approve or skip it.
   - A "Promote approved records and run scan" button that calls `promote_candidates`
     with the approved IDs, then runs `run_scan_async` on the updated history and shows
     the alert.

2. Add a clear visual affordance that separates candidate records from confirmed history.
   Unpromoted candidates must never be styled the same way as promoted history.

3. Add a "Reset candidate store" button that clears `data/candidate_history.csv` without
   touching `data/history.csv`. Useful during demos.

4. Extend the walkthrough with a screenshot sequence: fetch, extract, validate, approve,
   promote, run scan, see alert.

5. Add one end-to-end test in `tests/test_live_scan.py` that uses a fixture report body
   (no network), runs extraction and validation, promotes the results into a temp
   history file, runs the pipeline, and asserts the expected flags fire.

### Acceptance criteria

- The full flow works end to end in the browser on a real ReliefWeb report.
- Nothing enters history without a human clicking approve.
- If extraction returns zero records, the UI says so clearly and does not attempt a scan.
- If validation rejects every extracted record, the UI shows which records were rejected
  and why.
- All existing tests still pass. The new end-to-end test passes hermetically.

### Non-goals for phase 3

- No scheduled or automated live scans. This is a demo tool the human triggers.
- No writes to the submission repo. This work stays in the v2 repo.

---

## Cross-phase rules

- Work on a feature branch per phase (`feature/reliefweb-phase-1`, etc). Do not commit to
  main directly.
- Every phase ends with the full test suite green (existing tests unchanged plus the new
  tests for that phase), a walkthrough artifact, and stop for review.
- Do not modify anything under `src/signal/`, `src/alert/`, `src/guardrails/`, or
  `evals/` in any phase. Those are the parts of the submission architecture that must
  keep working exactly as they did.
- The data contract does not change. Extracted records use the same eight columns.
- If a live API call, an extraction, or a validation fails, the failure is logged and
  surfaced in the UI. Never invent, retry silently in a way that can loop, or fall back
  to unverified data.
- Do not commit `.env` or the RELIEFWEB_APPNAME value. Confirm `.gitignore` excludes
  them before any push in the new repo.

---

## The one design principle to preserve above all else

The submission agent is defensible because the LLM never writes a number into the human-
facing brief. Live data changes the picture: the LLM has to read numbers out of prose for
the pipeline to work at all. That is why phase 2 uses a two-model check and phase 3
requires human promotion before anything enters history. This is not overhead. This is
the whole safety story of the live version. Preserve it in every design decision.
