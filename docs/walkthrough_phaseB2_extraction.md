# Walkthrough — Phase B2: outbreak-configured extraction (deny-list + prompts)

Branch: `feature/outbreak-generalization`. B2 is the safety-adjacent slice deferred from B1: the
extraction **deny-list** and the two extraction-path **prompts** now consume the outbreak profile
from `src/outbreaks.py`, so pointing the agent at a non-DRC outbreak is a configuration change.
It changes *what* is denied and *what the prompt intro says* — never *how* the safety layer works.
`src/signal`, `src/alert`, `src/guardrails`, `evals` stay frozen.

## The DRC-specific surface B2 removed

Four hardcoded spots, all in the extraction path:

| Location | Was | Now |
|---|---|---|
| `extract_report._STATIC_DENIED_ZONES` | static `drc/congo/...` set | **removed** |
| `extract_report._denied_zones()` | static set + **every province in `history.csv`** | `_denied_zones(disaster_id)` = the profile's `denied_zone_aliases`, cached per id |
| `extract_report._EXTRACTION_PROMPT` | `"...for an Ebola surveillance system."` | templated intro (`{disease}`, `{country_name}`) + byte-identical rules block |
| `validate_extraction._VALIDATION_PROMPT` | `"...fact-checker for an Ebola surveillance system..."` | same treatment |

## Deny-list: profile-only

`_denied_zones(disaster_id)` returns `frozenset(profile_for(disaster_id).denied_zone_aliases)`,
`lru_cache`d per `disaster_id`. It no longer reads `data/history.csv`, so the guard is
per-outbreak, deterministic, and independent of a mutable data file. **DRC's effective denied set
is unchanged** — the profile already lists the country aliases plus `ituri`/`north kivu` (the only
provinces in history) — so existing extraction behavior and tests are preserved. Note: the
deny-list is built from country/province *aliases*, never from `health_zone` values (those are the
legitimate zones we keep).

## Prompt templating with a hard positional invariant

Each prompt is split into a per-outbreak **intro** (`{disease}` + `{country_name}`) and a **rules
block that is byte-identical to pre-B2**. `render_extraction_prompt(profile)` /
`render_validation_prompt(profile)` compose `intro + rules` with **nothing between them**. The
`_call_*_llm` seams take the rendered prompt; `extract_report` renders from
`profile_for(disaster_id)`, `validate_extraction` from the records' `disaster_id`. Mock boundaries
are unchanged (patched by name).

DRC renders, e.g.:
> "You are a careful data extractor for a **Bundibugyo virus disease (Ebola)** outbreak
> surveillance system in **the Democratic Republic of the Congo**. From the REPORT BODY below … "
> *(then the identical rules block)*

## Safety properties unchanged

The two-model check (extract → deterministic guards → independent validate), the verbatim-snippet
guard, the deny-list guard applied *after* extraction (same position, same `denied_zone` drop), the
"return an empty list on national/unattributed numbers" rule, the fail-closed/never-raises
contract, and the human promotion gate are all identical. B2 changes the denied *set* and the
prompt *intro*, not the machinery.

## Backward-compat with the DRC eval

Two independent reasons the DRC baseline stays green:
1. **The frozen eval set never calls extraction.** `evals/run_evals.py` runs `ingestion_pipeline`
   + `run_all_detectors` on canned JSON — no `extract_report`, no LLM, no prompt, no deny-list. So
   B2 cannot move the eval numbers.
2. **Live DRC extraction is functionally equivalent** — identical denied set, byte-identical rules
   block; only the intro sentence expands. Anchored by the prompt-parity test.

## Verification results

- **Full suite: 94 tests OK** (89 after commit 1 → 92 after commit 2 → 94 after commit 3; 1 live D2 test skipped without a key). ✅
- **Frozen evals: 5/5** — unchanged, extraction is never exercised by the eval runner. ✅
- **Deny-list**: two fixture profiles (DRC/Uganda) deny only their own names; neither leaks; the post-extraction `denied_zone` drop follows the active profile; an unconfigured id → empty deny-list. ✅
- **Prompt parity**: both rules blocks equal the pinned pre-B2 copies; every rendered prompt equals `intro + rules` with the rules block beginning exactly where the intro ends (**no text can be injected between intro and rules to reweight them**); `disease` + `country_name` appear in each. ✅
- **Freeze intact**: changes only in `src/outbreaks.py`, `src/live/extract_report.py`, `src/live/validate_extraction.py`, tests, docs. ✅

## Commit sequence (bisectable, four commits)

1. `country_name` + `profile_for` on the profile (no consumer yet).
2. Config-driven deny-list (`_denied_zones(disaster_id)`; drop the `history.csv` read).
3. Templated prompts for both models + prompt-parity tests.
4. Docs (this file + `SKILL.md`/`context.md`).

## What stays untouched

`src/signal`, `src/alert`, `src/guardrails`, `evals` frozen. Detector logic, the alert template,
the guardrail layer, the id-based ranking guard, and the human promotion gate are identical.
Reserved profile fields (`country_iso3`, `glide`, `fallback_query`, `report_format`) remain
hardcoded in `live_sources` — moving those is a later tidy, not B2.
