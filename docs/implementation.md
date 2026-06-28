# implementation.md — build plan

This is the authored, ordered plan. Work top to bottom. Do not skip ahead.
Before starting a phase, read its acceptance criteria. A phase is done only when its
criteria pass. After each phase, produce a short walkthrough artifact and stop for review.

Read `context.md` first. The data contract and the non-negotiable rules there govern everything.

---

## Phase 0 — Setup

Goal: a working environment and an empty, well-structured repo.

Tasks:
- Confirm Python 3.11+ and create a virtual environment.
- Get a Gemini API key from Google AI Studio and store it as an environment variable.
- Install the Agent Development Kit and project dependencies into `requirements.txt`.
- Create the repo structure (see "Repository structure" at the bottom).
- Get a single trivial agent to answer one prompt, to confirm the toolchain works.

Acceptance criteria:
- A one-line agent call returns a response.
- The folder tree matches the structure below.

## Phase 1 — Data layer

Goal: structured data the agent can reason over.

Tasks:
- Build `data/history.csv` using the data contract in `context.md`. Populate 20–40 rows
  across several weeks and several health zones, from the public sources listed in context.md.
- Build five test reports in `data/incoming/`, same schema:
  - `incoming_new_zone.json`: introduces a health zone absent from history.
  - `incoming_spike.json`: a sharp confirmed-case jump in an existing zone.
  - `incoming_data_gap.json`: a missing or null field to trigger the data-quality detector.
  - `incoming_cfr_shift.json`: a rising deaths/confirmed ratio in an existing zone, covering the CFR detector.
  - `incoming_multi_signal.json`: triggers all four detectors at once; the fixture that exercises ranking.
- Write a loader that validates rows against the contract and rejects malformed input.

Acceptance criteria:
- Loader parses `history.csv` and all five incoming files without error.
- Loader raises a clear error on a deliberately broken row.

## Phase 2 — Ingestion tool (and MCP server)

Goal: a tool that returns clean structured records.

Tasks:
- Implement ingestion as a plain Python function first (input: a JSON report path; output: validated records).
- Then wrap it as an MCP server exposing one tool, e.g. `load_reports(path)`.
- Keep the plain function as a fallback path.

Acceptance criteria:
- The MCP tool returns the same records as the plain function for the same input.
- If the MCP server fails to start, the fallback path still works.

## Phase 3 — Signal agent

Goal: rule-based detectors plus an LLM that ranks the results.

Tasks:
- Implement four detectors as pure Python functions, each returning structured flags:
  - new_zone: a health_zone present in the incoming report but absent from history.
  - surge: confirmed_cases growth in a zone above a configurable threshold over a window.
  - cfr_shift: deaths/confirmed rising past a configurable threshold.
  - stale_or_missing: an expected zone missing, or a null required field.
- Make thresholds configurable in one config file, not hard-coded across the codebase.
- Give the signal agent a prompt that takes the flag set and ranks flags by urgency.
  The agent must not invent numbers. It ranks and explains only what the detectors produced.

Acceptance criteria:
- Each detector has a unit test that passes on a crafted input.
- Running the signal agent on `incoming_spike.json` ranks the surge flag highest.

## Phase 4 — Alert agent

Goal: a short, source-cited brief for a human.

Tasks:
- Fixed output template: one-line headline, ranked signals, the numbers with source_url,
  and a confidence note.
- The agent fills the template only from the ranked flags. No free invention.

Acceptance criteria:
- Output follows the template exactly.
- Every number in the output appears with its source_url.

## Phase 5 — Orchestrator

Goal: the three agents wired into one flow.

Tasks:
- Build the ADK orchestrator: ingestion -> signal -> alert.
- One entry point, e.g. `run_scan(incoming_path)`, returns the drafted alert.

Acceptance criteria:
- `run_scan('data/incoming/incoming_new_zone.json')` returns an alert naming the new zone.

## Phase 6 — Guardrail layer

Goal: validation that runs before any output leaves.

Tasks:
- Validate every number in the alert against the ranked flags (each flag carries its
  report_date + source_url). Block (fail-closed) unsourced numbers and fabricated zones; the
  withheld brief still surfaces the underlying sourced signals so the signal is not discarded.
- Block and flag clinical, treatment, or forecasting language by default (configurable to
  strip). Scan only the human-readable prose — never source URLs — and whitelist operational
  terms (e.g. Ebola Treatment Center/Unit).
- Ensure the human-escalation line is present (idempotent — draft_alert already appends it).
- Keep the banned output patterns and the whitelist configurable in `src/config.py`.

Acceptance criteria:
- Three crafted bad outputs are caught: an unsourced number, a diagnosis sentence,
  and a fabricated zone.
- A clean alert passes unchanged except for the appended escalation line.

## Phase 7 — Memory

Goal: persistence so scans compare against accumulated history.

Tasks:
- After a scan, append the incoming report to the stored history.
- The next scan reads the updated history automatically.

Acceptance criteria:
- Running two scans in sequence: the second sees zones added by the first.

## Phase 8 — Demo scenarios

Goal: reproducible evidence for the writeup.

Tasks:
- Run all five scenarios end to end: new-zone, spike, data-gap, cfr-shift, and multi-signal.
  `incoming_multi_signal.json` triggers all four detectors at once and is the fixture that
  exercises the ranking step; `incoming_cfr_shift.json` covers the CFR detector in isolation.
- Capture each output (Antigravity artifacts: walkthrough plus screenshots).

Acceptance criteria:
- Five saved outputs, each showing the correct flag(s) ranked correctly, including the
  multi-signal scenario showing all four detectors ranked by urgency.

## Phase 9 — Evals

Goal: prove the detectors fire correctly.

Tasks:
- Build `evals/eval_set.json`: each entry pairs an input report with its expected flags.
- Write a runner that scores actual flags against expected and reports pass/fail.

Acceptance criteria:
- All eval cases pass, or failures are understood and documented.

## Phase 10 — Documentation and submission

Goal: a submission that scores on clarity, not just code.

Tasks:
- Writeup: problem, architecture diagram, the four concepts demonstrated, the data-ethics
  paragraph from context.md, limitations, and the scenario walkthrough.
- README with run instructions.

Acceptance criteria:
- A reader who has never seen the code understands what it does and why from the writeup.

---

## Repository structure

```
ebola-surveillance-agent/
├── .agents/
│   ├── skills/ebola-surveillance/SKILL.md
│   └── workflows/build-agent.md        # optional
├── docs/
│   ├── context.md
│   └── implementation.md
├── data/
│   ├── history.csv
│   └── incoming/incoming_{new_zone,spike,data_gap}.json
├── src/
│   ├── ingestion/      # plain function + MCP server
│   ├── signal/         # detectors + ranking agent
│   ├── alert/          # alert agent + template
│   ├── guardrails/     # validation layer
│   ├── memory/         # history store
│   ├── config.py       # thresholds, banned patterns
│   └── orchestrator.py
├── evals/eval_set.json
├── tests/
├── README.md
└── requirements.txt
```
