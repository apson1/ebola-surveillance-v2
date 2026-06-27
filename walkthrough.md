# walkthrough.md — ADK refactor verification (step 4)

Full path exercised: **MCPToolset ingestion -> ADK signal_pipeline (detection -> ranking -> guard) -> deterministic draft_alert**.

## What changed (Option B: real ADK)

- **Signal stage is now an ADK `SequentialAgent`** (`src/signal/signal_pipeline.py`):
  `DetectionAgent` (non-LLM BaseAgent; runs the four rule-based detectors, writes an
  ids-only projection to state) -> `RankingAgent` (`LlmAgent`, `output_schema=RankingDecision`,
  ranks ids only, never sees numbers) -> `GuardAgent` (applies the id-permutation guard).
- **The id guard is extracted** to `src/signal/ranking.py` (`apply_rank_guard`) as the single
  source of truth. It remains load-bearing: `output_schema` guarantees shape only, not a
  valid permutation.
- **Ingestion** is a deterministic seam (`src/orchestrator.py::_load_reports`) that calls the
  existing MCP server through the ADK **`MCPToolset`**, with a logged fallback to the plain
  `ingestion_pipeline`. The MCP server and detectors are unchanged.
- **`draft_alert` is unchanged** and runs as a deterministic post-step outside the agent flow.
- **Entry points:** `run_scan_async` is the real entry; `run_scan` is a sync wrapper that
  thread-offloads when a live event loop is already running (Kaggle-safe).
- **Removed:** the legacy `src/signal/signal_agent.py` (direct-genai ranking) — superseded by
  the ADK pipeline, no longer imported anywhere.
- **Exactly one LLM call per scan** (the RankingAgent); detection and alert are LLM-free.

## Test suite

`python -m unittest discover -s tests` -> **22 pass**. Adapted (not rewritten): the guard test
now targets `apply_rank_guard`; the orchestrator tests target the new ingestion/signal seams
(the `genai.Client` patch is gone, since ADK owns the LLM call). New: ids-only projection
tests (A2) and a running-event-loop test (A1).

## Five scenarios (detector set vs Option-A baseline)

| scenario | match | top signal (LLM-ranked) | detector set |
|---|---|---|---|
| new_zone | PASS | new_zone | ['new_zone'] |
| spike | PASS | surge | ['surge'] |
| data_gap | PASS | stale_or_missing | ['stale_or_missing'] |
| cfr_shift | PASS | cfr_shift | ['cfr_shift'] |
| multi_signal | PASS | cfr_shift | ['cfr_shift', 'new_zone', 'stale_or_missing', 'surge'] |

Detector sets are deterministic and match the baselines exactly; the top signal is the live LLM ranking (guard-validated permutation) and may vary.

## A3 — MCPToolset ingestion equals ingestion_pipeline (multi_signal)

- prior_snapshot rows: MCP=6, plain=6
- incoming rows: MCP=6, plain=6
- prior zone set (both): ['Beni', 'Bunia', 'Mongbwalu', 'Nyankunde', 'Oicha', 'Rwampara']
- prior records identical (dates canonicalized): True
- incoming records identical (dates canonicalized): True

**MCP parity: PASS** — the ADK MCPToolset path returns the same records as the plain function, so the MCP path is proven (not merely the fallback).
