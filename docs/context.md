# context.md — Ebola surveillance signal-detector agent

This file is the durable project knowledge. Read it before any task. It explains
what we are building, why, the rules that never change, and the decisions already made.
Do not re-litigate decisions recorded here without being asked.

---

## 1. What this is

A multi-agent decision-support tool that helps outbreak-response coordinators in the
Democratic Republic of the Congo (DRC) detect new or accelerating Ebola clusters early.
It ingests published situation reports, tracks case and death counts per health zone over
time, flags signals (new zones, surges, rising fatality, data gaps), and drafts a short,
source-cited alert for a human to act on.

It is a capstone project for the Kaggle "AI Agents: Intensive Vibe Coding" course,
"Agents for Good" track. Submission deadline: July 6, 2026.

## 2. Problem statement

Coordinators receive a flood of unstructured situation reports from WHO, CDC, ECDC,
ReliefWeb, and MSF, across dozens of affected health zones. Spotting a newly affected
zone or an accelerating cluster early is what lets responders isolate cases fast.
Isolation speed is the single biggest lever on outbreak size. The agent compresses that
detection step from hours of manual reading into a ranked, source-cited brief.

## 3. Real-world grounding (use in the writeup; cite sources)

As of mid-June 2026:
- 17th Ebola outbreak in DRC, declared 15 May 2026, caused by the Bundibugyo virus.
- No licensed vaccine or specific treatment for Bundibugyo. Early supportive care is lifesaving.
- ~896 confirmed cases and 232 deaths across 31 health zones (17 June 2026).
- Ituri is the epicenter; spread to North Kivu, South Kivu, and Uganda.
- WHO declared a Public Health Emergency of International Concern.
- The International Rescue Committee estimated only ~20% of contacts were being located.
- Community mistrust is severe: burial teams attacked, patients fleeing treatment centers.
- CDC modeling: if 70% of cases isolate within two days, there is a 94% probability of
  keeping the outbreak under 10,000 cases. Speed of detection and isolation is decisive.

Sources to cite:
- WHO: https://www.who.int/emergencies/situations/ebola-outbreak---drc-2026
- CDC: https://www.cdc.gov/ebola/situation-summary/index.html
- ECDC: https://www.ecdc.europa.eu/en/ebola-outbreak-democratic-republic-congo-and-uganda
- ReliefWeb: https://reliefweb.int/disaster/ep-2026-000071-cod
- MSF: https://www.doctorswithoutborders.org/latest/ebola-disease-outbreak-2026-how-msf-responding

## 4. Competition fit

Track: Agents for Good (healthcare).
The build must demonstrate at least three course concepts. This project targets four:
1. Multi-agent system (Agent Development Kit): orchestrator plus specialist sub-agents.
2. Tools / MCP server: ingestion exposed as an MCP server returning structured records.
3. Memory / context engineering: history persisted so each scan compares against the past.
4. Quality and security: a guardrail layer plus an eval set proving detectors fire correctly.

## 5. Scope and non-goals

In scope: ingesting public aggregate reports, detecting signals, drafting alerts for humans.

Non-goals (do not build these):
- No clinical diagnosis of any individual.
- No treatment recommendations.
- No patient-level data of any kind.
- No predictive epidemic modeling. Detection is rule-based and explainable, not a forecast.
- No real-time deployment claims. This is a working prototype demonstrated on curated data.

## 6. Data policy and ethics (non-negotiable)

- Use only public, aggregate data. Never patient-level records.
- Every number the agent outputs must trace to a source record (report_date + source_url).
- The agent supports decisions. A human coordinator always decides and acts.
- Outputs must carry uncertainty and data-quality caveats, never false confidence.
- State all of this explicitly in the submission documentation. Judges will look for it.

## 7. Architecture decisions

- Pipeline: data sources -> orchestrator -> ingestion agent -> signal agent (reads/writes
  memory) -> alert agent -> guardrail layer -> human coordinator.
- Detection is rule-based Python. The LLM does not compute flags. It ranks the flagged set
  and writes the human-readable brief. This division of labor is the core design and must
  be preserved.
- The fallback priority ordering is cfr_shift, then surge, then new_zone, then stale_or_missing. Rising deaths and rapid acceleration are active danger, a new zone with few cases is early warning, a data gap is lowest.
- Keep an MCP version and a plain-function fallback of the ingestion tool, so an MCP setup
  problem cannot block the whole build.

## 8. Data contract (single source of truth for schema)

All data files use these columns, in this order:

| column            | type    | notes                                                                                  |
|-------------------|---------|----------------------------------------------------------------------------------------|
| date              | date    |  as-of date, when the situation was true. ISO 8601, e.g. 2026-06-17                    |
| province          | string  | e.g. Ituri, North Kivu, South Kivu                                                     |
| health_zone       | string  | e.g. Mongbwalu, Bunia, Rwampara                                                        |
| suspected_cases   | integer | cumulative                                                                             |
| confirmed_cases   | integer | cumulative                                                                             |
| deaths            | integer | cumulative                                                                             |
| source_url        | string  | the report this row came from                                                          |
|report_date        | date    | publication date, when the report was issued. ISO 8601                                 |

`date` is the as-of date the detectors order by. `report_date` is provenance, used with
`source_url` by the guardrail layer. The two differ by the real reporting lag, usually a
few days.

Counts are cumulative integers. A null count is allowed only in an incoming report, where
it is treated as a data-quality signal. History must never contain a null `confirmed_cases`
or `deaths`.

**Scoped exception (live promotion).** The no-null rule was written for the curated seed and
is preserved for the seed loader. Records promoted from `candidate_history.csv` (live ReliefWeb
extraction) may carry a null in `suspected_cases` only, because that field is often absent in
real situation reports and is not consumed by any detector (surge and CFR use `confirmed_cases`
and `deaths`; `stale_or_missing` flags the gap). `confirmed_cases` and `deaths` must still be
present; a null in either continues to route through `stale_or_missing` and never enters
history. All non-promotion write paths remain strict.

`data/history.csv` holds the historical seed. Incoming reports live in
`data/incoming/*.json` and the agent scans them against history. An incoming report is JSON
with a top-level `report_date` and a `data` array of records. Each record carries the
columns above except `report_date`, which is filled from the top-level value.

## 9. Glossary

- Health zone: the DRC administrative unit used for outbreak reporting.
- CFR (case fatality ratio): deaths divided by confirmed cases.
- Suspect case: a case meeting the symptom and exposure definition, not yet confirmed.
- Signal: a rule-flagged change worth a human's attention.
