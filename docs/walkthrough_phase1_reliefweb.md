# Walkthrough — Phase 1: Live ReliefWeb source discovery

Branch: `feature/reliefweb-phase-1`. Metadata retrieval only — no numbers extracted, nothing
fed into the detectors, alert, guardrail, memory, or eval set. The five hard rules are
untouched.

## Key finding (verified live, not from memory)

ReliefWeb **API v1 is decommissioned** — a v1 call returns `HTTP 410: "The API version 'v1'
has been decommissioned. Please use version 'v2' instead."` All work targets **v2**
(`https://api.reliefweb.int/v2/reports`). The DRC Bundibugyo outbreak is a **real** ReliefWeb
disaster: id **52586**, GLIDE **EP-2026-000071-COD**, status ongoing — matching the ReliefWeb
link already in `context.md`.

## What changed

| File | Change |
|---|---|
| `src/config.py` | Added `RELIEFWEB_APPNAME`, `RELIEFWEB_API_BASE` (v2), `RELIEFWEB_DISASTER_ID` (default 52586, overridable). |
| `src/ingestion/live_sources.py` (new) | `fetch_recent_drc_ebola_reports(limit=10) -> LiveSourceResult`. POSTs the verified v2 query; maps to `{id, title, source, date, url}`. Never raises. |
| `src/ingestion/mcp_server.py` | Added a second MCP tool `list_recent_sources(limit=10)` alongside the untouched `load_reports`. |
| `app_streamlit.py` | New "📡 Live official reports (ReliefWeb)" section **above** the scenario runner; existing UI unchanged. |
| `scripts/fetch_live_sources.py` (new) | Terminal demo. |
| `tests/test_live_sources.py` (new) | Live integration test that skips when appname/network unavailable. |
| `requirements.txt` | Declared `requests` (ReliefWeb HTTP client). |
| `.env.example` (new) | Self-documents `RELIEFWEB_APPNAME=` (and the other env vars). |
| `.gitignore` | Ignores `.cache/` (runtime response cache). |

## The three approved refinements

1. **Pinned → fallback.** Query pins `disaster.id=52586`. If it returns **zero** results (not an
   error), it falls back to an `"ebola"` + DRC text query and records that the fallback fired.
   The Streamlit panel shows the fallback as a visible ⚠️ warning, so drift off the pinned
   outbreak is obvious without a redeploy.
2. **Negative caching.** Successful fetches cache for 15 min; disabled/error/empty states cache
   for 30 s, so Streamlit reruns don't hammer the network.
3. **`.env.example`** added with `RELIEFWEB_APPNAME=` so the required variable is
   self-documenting for anyone cloning the repo. `requests` declared in `requirements.txt`.

## The verified query (POST + JSON body, `HTTP 200`)

```json
POST https://api.reliefweb.int/v2/reports?appname=${RELIEFWEB_APPNAME}
{
  "filter": {"operator": "AND", "conditions": [
    {"field": "disaster.id", "value": 52586},
    {"field": "format.name", "value": "Situation Report"}]},
  "fields": {"include": ["title", "source", "date", "url_alias", "url"]},
  "sort": ["date.created:desc"], "limit": 10
}
```
Field mapping (real v2 names): `id`→`data[].id` (string), `title`→`fields.title`,
`source`→`fields.source[0].shortname`, `date`→`fields.date.original`, `url`→`fields.url_alias`.

## Verification results

- **Demo** (`python -m scripts.fetch_live_sources`): `[pinned]` mode, **10 real DRC Ebola
  situation reports** dated July 2026 (WHO Bundibugyo external sitreps, UNICEF, IFRC, ETC,
  Health Cluster), each a clickable ReliefWeb link. ✅ (acceptance: ≥3 recent reports)
- **Full test suite**: **40 tests, OK** (39 existing unchanged + 1 new live test, which ran and
  passed). ✅
- **Disabled degradation**: with `RELIEFWEB_APPNAME` empty, the script/app print
  `[disabled] Live sources disabled: set RELIEFWEB_APPNAME …` and run without error. ✅
- **Freeze compliance**: changed files are `.gitignore`, `app_streamlit.py`, `requirements.txt`,
  `src/config.py`, `src/ingestion/mcp_server.py` + new files only. **Nothing under
  `src/signal/`, `src/alert/`, `src/guardrails/`, or `evals/`.** `load_reports` unchanged. ✅

## Not verified here (needs a manual check)

- The Streamlit panel is confirmed to **compile** and the fetch it calls is confirmed working,
  but rendering + clicking a link in the browser was not exercised in this environment. Run
  `streamlit run app_streamlit.py` to confirm the panel renders above the scanner and links
  open the real ReliefWeb pages.

## Not done (correctly out of scope for Phase 1)

No number extraction, no feed into detectors/alert/guardrail/eval, no data-contract change.
Those are Phases 2 and 3.
