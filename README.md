# Ebola Surveillance Signal-Detector Agent

A multi-agent decision-support tool that helps outbreak-response coordinators in the
Democratic Republic of the Congo (DRC) detect new or accelerating Ebola clusters early. It
ingests published situation reports, tracks case and death counts per health zone over time,
flags signals (new zones, surges, rising fatality, data gaps) with **rule-based** detectors,
ranks them with an LLM, and drafts a short, **source-cited** alert for a human to act on.

> Capstone for the Kaggle *AI Agents: Intensive — Vibe Coding* course, "Agents for Good"
> (healthcare) track. It is a working prototype demonstrated on curated data — **not** a
> real-time deployment, and **not** a clinical or diagnostic tool. See
> [docs/context.md](docs/context.md) for the durable rules and [docs/writeup.md](docs/writeup.md)
> for the full submission writeup.

## What it does

![Architecture](docs/images/architecture.png)

**Core design (non-negotiable):** detection is rule-based Python; the LLM only *ranks and
explains* the flagged set and never invents numbers or zones. Every number in any output
traces to a source record (`report_date` + `source_url`), and a guardrail layer validates that
before anything leaves.

## The four course concepts

| Concept | Where |
|---|---|
| **Multi-agent system (ADK)** | `src/signal/signal_pipeline.py` — `SequentialAgent`: `DetectionAgent → RankingAgent → GuardAgent`, driven by `src/orchestrator.py` |
| **Tools / MCP server** | `src/ingestion/mcp_server.py` exposes `load_reports`; the orchestrator consumes it via the ADK `MCPToolset` with a plain-function fallback |
| **Memory / context engineering** | `src/memory/history_store.py` — opt-in append so each scan compares against the accumulated past |
| **Quality & security** | `src/guardrails/guardrails.py` (traceability / fabricated-zone / banned-language) + `evals/` (detection eval set + runner) |

## Quickstart

**Prerequisites:** Python 3.11–3.13, and a Gemini API key.

```bash
# 1. Environment
python -m venv venv
venv\Scripts\activate            # Windows  (use: source venv/bin/activate on macOS/Linux)
pip install -r requirements.txt

# 2. API key — create a .env file in the repo root:
#    GEMINI_API_KEY=your_key_here
#    (optional) GEMINI_MODEL=gemini-2.5-flash
```

**Run a single scan** (prints the drafted alert):

```bash
python -c "from src.orchestrator import run_scan; print(run_scan('data/incoming/incoming_multi_signal.json')['alert'])"
```

**Run all five demo scenarios** end to end:

```bash
python -m scripts.verify_scenarios
```

**Run the Streamlit Web UI**:

```bash
streamlit run app_streamlit.py
```
This opens the interactive browser UI at `http://localhost:8501`.


**Run the detection eval set** (hermetic, no LLM) and write the results artifact:

```bash
python -m evals.run_evals          # -> 5/5 passed, writes evals/eval_results.json
```

**Run the test suite:**

```bash
python -m unittest discover -s tests -v
```

> The scan path makes one live Gemini call (the ranking step); if the model is unavailable
> (quota/network) the scan degrades to deterministic priority ordering rather than failing.
> The eval runner and almost all tests are hermetic; two integration tests exercise the live
> model when available and otherwise pass via the fallback. The free tier is limited to 20
> requests/day.

## Memory (opt-in persistence)

By default scans do not mutate history (so demos and tests stay reproducible). To accumulate:

```python
from src.orchestrator import run_scan
run_scan("data/incoming/incoming_new_zone.json", persist=True)   # appends clean rows to history
```

## Data & ethics

Public, aggregate data only — never patient-level records. Every number traces to a source
record; outputs carry uncertainty and data-quality caveats; a human coordinator always decides
and acts. No clinical diagnosis, no treatment advice, no epidemic forecasting. Full policy in
[docs/context.md §6](docs/context.md) and the writeup.

## Repository layout

```
src/
  ingestion/      plain loader + MCP server (load_reports)
  signal/         detectors.py (4 rule-based detectors), ranking.py (id guard),
                  signal_pipeline.py (ADK agents)
  alert/          alert_agent.py (deterministic template) + formatting helpers (src/formatting.py)
  guardrails/     guardrails.py (validation before output leaves)
  memory/         history_store.py (opt-in append)
  orchestrator.py run_scan / run_scan_async entry points
data/             history.csv (seed) + incoming/incoming_*.json (5 scenarios)
evals/            eval_set.json, run_evals.py, eval_results.json
scripts/          smoke_adk, verify_scenarios, verify_step4, capture_baselines (python -m scripts.<name>)
tests/            39 tests
docs/             context.md, implementation.md, writeup.md, images/
walkthrough.md    verification evidence (5 scenarios + MCP parity)
```

## Limitations

Rule-based, explainable detection (not predictive modelling); a working prototype on curated
data, not a real-time system; the LLM is confined to ranking and phrasing. See the writeup's
*Limitations* section.
