---
name: ebola-surveillance
description: Build, run, and extend the Ebola outbreak surveillance signal-detector agent in this repository. Use this skill whenever the task involves the surveillance agent, the ingestion or signal or alert agents, the orchestrator, the detectors, the guardrails, the data files, the eval set, or anything in the src/ tree of this project, even if the request does not name the skill directly. Consult it before writing or changing any agent code so the data contract, the rule-based detection design, and the safety rules stay intact.
---

# Ebola surveillance agent

This skill governs how to work in this repository. It is a healthcare decision-support
project. Correctness and safety outrank speed and cleverness.

Before doing anything, read `docs/context.md` (the durable why and the rules) and
`docs/implementation.md` (the ordered build plan). This skill is the how-to-work-here layer
that sits on top of both.

## What the system does

A multi-agent pipeline ingests public aggregate Ebola situation reports, detects new or
accelerating clusters per health zone, and drafts a short source-cited alert for a human.
Pipeline: data sources -> orchestrator -> ingestion agent -> signal agent (reads/writes
memory) -> alert agent -> guardrail layer -> human coordinator.

## Hard rules (never break these)

1. The agent supports decisions. A human always decides and acts. Every alert ends with an
   escalation line directing the human to verify and act.
2. Use only public aggregate data. Never patient-level data.
3. Every number in any output must trace to a source record (report_date + source_url).
   Unsourced numbers are blocked by the guardrail layer.
4. No clinical diagnosis of individuals. No treatment advice. No epidemic forecasting.
5. Detection is rule-based Python. The LLM ranks and explains flags. The LLM never invents
   numbers or zones. Preserve this division of labor in every change.

## Data contract

All data files use exactly these nine columns, in order (single source of truth: `src/contract.py`):
`disaster_id, date, province, health_zone, suspected_cases, confirmed_cases, deaths, source_url, report_date`.
`disaster_id` (Phase B) is the outbreak partition key so one history file can hold multiple
outbreaks; it is part of the identity/dedup key. `date` is the as-of date the detectors order by;
`report_date` is the publication date used for provenance. Both are ISO 8601. Case and death counts
are cumulative integers. `confirmed_cases` and `deaths` are never null in history; `suspected_cases`
may be null only for records promoted from live extraction (scoped exception, see context.md sec 8).
For an incoming report, `disaster_id` precedence is per-record > file-level > active outbreak. Do
not add, rename, or reorder columns without updating `src/contract.py`, `docs/context.md`, and every reader.

## Repository map

- `src/ingestion/` plain loader function plus an MCP server exposing it. Keep both working.
- `src/signal/` the four detectors and the ranking agent.
- `src/alert/` the alert agent and its fixed output template.
- `src/guardrails/` validation that runs before any output leaves. Numbers are traced to the
  ranked flags; unsourced numbers and fabricated zones fail closed (prose withheld, sourced
  signals still surfaced). Clinical, treatment, or forecasting language is blocked and flagged
  by default (configurable to strip), scanning prose only and whitelisting operational terms.
- `src/live/` live ReliefWeb extraction: the two-model check (`extract_report` → deterministic
  guards → `validate_extraction`), candidate store, and option-B live scan. The extraction
  **deny-list and both prompts are outbreak-configured** — they read the active profile's
  `denied_zone_aliases`, `disease`, and `country_name` (Phase B2). The guards, the two-model
  sequence, and the human promotion gate are outbreak-agnostic and unchanged.
- `src/contract.py` the single 9-column data contract + identity key. `src/outbreaks.py` the
  outbreak profile registry (active outbreak from `RELIEFWEB_DISASTER_ID`).
- `src/memory/` the history store.
- `src/config.py` thresholds and banned output patterns. All tunables live here.
- `src/orchestrator.py` the ADK wiring and the `run_scan(incoming_path)` entry point.
- `data/`, `evals/`, `tests/` as described in implementation.md.

## The detectors

Four pure functions, each returning structured flags, thresholds read from `src/config.py`:
- `new_zone`: health_zone present in incoming but absent from history.
- `surge`: confirmed_cases growth in a zone above threshold over a window.
- `cfr_shift`: deaths/confirmed rising past threshold.
- `stale_or_missing`: expected zone missing, or a null required field.

When adding a detector: write it as a pure function, add its threshold to `config.py`,
add a unit test with a crafted input, then register it with the signal agent. Never let a
detector call the LLM, and never let the LLM compute a flag.

## Output template (alert agent)

One-line headline, then ranked signals, then the numbers each with their source_url, then a
confidence and data-quality note. Fill it only from the ranked flags.

## How to run

- Single scan: `run_scan('data/incoming/<file>.json')`.
- The MCP ingestion server is the default path. If it fails to start, the plain loader is the
  fallback and the pipeline must still run.

## Verification expectations

After any change, before declaring done:
- Run the unit tests for the detectors.
- Run the three demo scenarios (new_zone, spike, data_gap) and confirm the right flag ranks
  highest in each.
- Run the eval set in `evals/` and confirm all cases pass or document why one does not.
- Produce a short walkthrough artifact summarizing what changed and the verification result.

## When unsure

If a request conflicts with the hard rules above, stop and surface the conflict to the human
rather than working around it. The rules exist because this is a public-health context.
