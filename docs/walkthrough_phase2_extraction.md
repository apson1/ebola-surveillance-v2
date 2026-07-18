# Walkthrough — Phase 2: Extraction with a two-model safety layer

Branch: `feature/reliefweb-phase-2`. This phase reads case counts out of ReliefWeb report
prose — the one thing the submission agent never did — so the whole design is about making
that safe. Nothing enters `data/history.csv` in this phase except through an explicit,
human-invoked promotion.

## The safety story in plain language

The submission agent is defensible because the LLM never writes a number into the human-facing
brief. Live reports force the model to *read* numbers out of prose. Phase 2 keeps that safe
with **two independent model passes plus a human gate**, and two cheap deterministic guards
that don't trust the model at all:

1. **Extract** (`extract_report`): one model reads the report body and returns candidate
   records — but only figures explicitly tied to a named **health zone** with a date, each with
   the **exact sentence** it came from.
2. **Two deterministic guards** run in Python, after the model:
   - **Verbatim-snippet guard:** the cited sentence must be a character-for-character substring
     of the report body, or the record is dropped. This catches a model inventing a
     plausible-sounding quote.
   - **No-national-totals guard:** a record with no health zone (e.g. a country-wide total) is
     dropped.
3. **Validate** (`validate_extraction`): a *second, differently-prompted* model acts as a strict
   fact-checker, judging each record independently (it must quote the supporting phrase per
   record). Only a strict PASS survives; anything hedged or contradicted is rejected.
4. **Human promotion:** validated records land in a separate `data/candidate_history.csv` with
   status `pending`. They enter real history **only** when a human calls
   `promote_candidates([ids])` with an explicit list. Nothing is ever promoted automatically.

## What changed

| File | Change |
|---|---|
| `src/ingestion/live_sources.py` | Added `fetch_report_body(report_id)` (verified list-endpoint + id filter, `fields=["body"]`). |
| `src/live/extract_report.py` (new) | `extract_report(...)` — google-genai + Pydantic schema, then the verbatim-snippet and no-zone guards. Never raises. |
| `src/live/validate_extraction.py` (new) | `validate_extraction(...)` — one batched checker call returning a per-record verdict with a quoted supporting phrase; fail-closed on error. |
| `src/live/candidate_store.py` (new) | `write_candidates` / `promote_candidates` / `reset_candidates`; `data/candidate_history.csv` with a `status` column (`pending\|approved\|promoted\|rejected`). Promotion reuses `append_to_history` (Phase 7). |
| `scripts/extract_from_url.py` (new) | Demo: fetch body → extract → validate → print records + snippets + rejects. |
| `tests/test_extraction.py` (new) | Hermetic extraction/validation/candidate-store tests + one live D2 check. |
| `.gitignore` | Ignore `data/candidate_history.csv` (runtime staging store). |

## Verification results

- **Real report demo** (`python -m scripts.extract_from_url 4221419`): from the WHO Bundibugyo
  external sitrep, extraction returned **10 per-zone candidate records** (Bunia 503/156,
  Rwampara 384/89, Mongbwalu 329/171, … Beni 30/20), each with a **verbatim snippet** and the
  correct province, and **all 10 validated**. The national "DR Congo 1,926 confirmed / 702
  deaths" total was correctly **not** extracted. ✅ (acceptance: ≥1 real report → non-empty
  validated candidates with snippets)
- **Deliberately misleading record** (D2): a batch of three where one number (999) contradicts
  its snippet (200) → the checker rejects **only** that one; the two supported records pass. ✅
- **No-national-totals (R1):** the verified "DR Congo 1,926…" line with no health zone →
  **zero** records (hermetic). ✅
- **Verbatim-snippet (R2):** an invented, non-substring snippet → dropped (hermetic). ✅
- **history.csv untouched** except via `promote_candidates` (tested against a temp history). ✅
- **Full suite: 49 tests OK** (40 unchanged + 9 new). ✅
- **Freeze respected:** nothing under `src/signal`, `src/alert`, `src/guardrails`, `evals`;
  `load_reports` untouched; no data-contract change. ✅

## Note on the D2 (batching) decision

You asked: if the batched checker can't isolate a single contradicted record, drop to
per-record calls. It **can** — the batched validator returned distinct, correct per-record
reasons (it caught "999 does not match 200"). The initial test failure was a **fixture** bug:
the test body omitted the record date, so the strict checker (correctly) rejected everything
for missing date support. Fixed the fixture to state the date; batching stays.

## Failure visibility (D4)

Every failure log carries `report_id` and the failing stage — e.g.
`dropped record [report_id=… stage=snippet_guard]`, `record rejected [report_id=… stage=
validation index=1]`, `validation failed [report_id=… stage=validate] … failing closed`. Phase
3's UI/demo can surface these so failures are seen being handled, not swallowed.

## Post-review hardening (bug found + fixed)

A double-check after the build surfaced a real bug the tests missed (they used a *complete*
record). Fixed on this branch:

- **Promotion honesty.** `promote_candidates` used to mark a record `promoted` even when
  `append_to_history` silently dropped it — a safety-story problem, not just UX. It now returns
  a `PromotionResult{added_to_history, promoted, rejected}`: only records that actually entered
  history are `promoted`; the rest are marked `rejected` with a reason and reported.
- **Scoped null-suspected exception (Option B).** Real per-zone sitrep lines give confirmed +
  deaths but not suspected, so under the old strict rule they could never be promoted. Now a
  null `suspected_cases` is allowed **only** on the candidate-promotion path
  (`append_to_history(..., allow_null_suspected=True)`); `confirmed_cases` and `deaths` must
  still be present, and every other write path stays strict. `load_history` tolerates a null
  `suspected_cases` but still rejects a null confirmed/deaths. Documented in context.md sec 8
  and SKILL.md.
- **Zone deny list.** The extraction guard now also drops a `health_zone` that is a country or
  province ("DRC", "Democratic Republic of the Congo", "Congo", or any province in
  `history.csv`), closing the mislabeled-national-total path.

New tests: suspected-null promotes (history keeps null suspected, correct confirmed/deaths and
`load_history` accepts it); confirmed-null is rejected cleanly and reported; a `health_zone` of
"DRC" is dropped by the guard. **Full suite: 52 OK.**

## Not done (correctly out of scope for Phase 2)

No UI for extraction or promotion (that's Phase 3). No automatic promotion, ever. No changes to
detectors, alert, guardrail, or the eval set.
