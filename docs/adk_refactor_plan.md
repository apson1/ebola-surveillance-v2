# adk_refactor_plan.md — Option B: refactor to real ADK

Status: **PLAN ONLY. No code changed.** This document scopes the migration of the
orchestrator and signal stage onto Google's Agent Development Kit (ADK) so that the
submission's "Multi-agent system (Agent Development Kit)" claim (context.md §4, concept #1)
is satisfied by the code, not just the prose.

Guiding constraint: correctness and the five hard rules outrank ADK-idiom purity. Where an
ADK API forces a design choice, the choice that best preserves rule-based detection, the
ids-only LLM exposure, and the deterministic alert wins.

---

## 0. Two divergences from the brief (decide before we start)

Both are flagged because the literal brief conflicts with how ADK actually works.

**D1 — "detectors registered as tools the agent calls" vs. the ids-only guard.**
ADK's `LlmAgent` has a hard constraint: **if `output_schema` is set, the agent cannot use
tools or transfer** (structured-output mode disables tool-calling). So one agent cannot both
*call the four detectors as tools* and *emit a structured ranking*. Worse, if detectors are
LLM-callable tools, their full numeric output (confirmed counts, CFR, deaths) is returned
into the LLM's context — which undoes the hardening we just landed in `signal_agent.py`,
where the model is given **only** `{id, detector, health_zone}` and never the numbers.

  - **Recommended:** detectors run **deterministically** (not LLM-triggered) inside a
    non-LLM step; the LLM agent receives only the id+detector+zone projection and ranks.
    This keeps hard rule 5 (LLM never computes a flag, never sees the numbers) intact and
    guarantees all four detectors always run.
  - **Literal-brief alternative:** wrap detectors as `FunctionTool`s on a tool-using
    `LlmAgent` with no `output_schema`, parse a free-text ranking afterwards. Costs:
    re-exposes numbers to the LLM, ranking is non-deterministic about whether every detector
    fired, and we lose schema validation. Not recommended.

**D2 — alert as an ADK agent vs. orchestrator-invoked.** See §2c. Recommended: keep
`draft_alert` outside the agent flow.

If you accept D1 (deterministic detection) and D2 (alert outside the flow), the rest of this
plan is internally consistent. If you want the literal detectors-as-tools design, stop here
and we re-scope §2b.

---

## 1. Packages, versions, Python constraints

Add to `requirements.txt`:

```
google-adk==2.3.0     # latest as of 2026-06-24; verify imports in the Phase-0 smoke, then lock via pip freeze
```

Notes:
- `google-adk` depends on `google-genai` (already used by `signal_agent.py`) and `pydantic`
  (v2). The existing `from google import genai` keeps working; ADK uses the same SDK under
  the hood. No separate genai pin needed, but keep `mcp` (already present) for the server.
- **Exact version:** the current major is **2.x** (latest `2.3.0`, confirmed via pip on
  2026-06-24); the earlier `1.x` framing is superseded. In Phase 0, install `2.3.0`, run the
  smoke gate (§5), verify every import path, then lock via `pip freeze > requirements.lock`.
  Treat §2's ADK import paths as **unverified** until the smoke gate confirms them on 2.x.
- **Python:** ADK 2.x supports CPython 3.10–3.13. Repo runs **3.13.14** (venv is cpython-313),
  implementation.md Phase 0 already requires 3.11+. Constraint: **3.11 ≤ Python ≤ 3.13**.
- **Auth/env mapping:** ADK on Google AI Studio reads `GOOGLE_API_KEY` and
  `GOOGLE_GENAI_USE_VERTEXAI=FALSE`. Today we set `GEMINI_API_KEY`. Plan: in `config.py`,
  alias it — `os.environ.setdefault("GOOGLE_API_KEY", GEMINI_API_KEY or "")` and
  `os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "FALSE")` — so existing `.env` files
  keep working. `GEMINI_MODEL` (default `gemini-2.5-flash`) is passed straight to
  `LlmAgent(model=...)`.

---

## 2. Component-by-component design

ADK classes referenced (import paths to verify against the pinned version in Phase 0):
`google.adk.agents.{LlmAgent, SequentialAgent, BaseAgent}`,
`google.adk.runners.Runner` / `InMemoryRunner`,
`google.adk.sessions.InMemorySessionService`,
`google.adk.tools.FunctionTool`,
`google.adk.tools.mcp_tool.MCPToolset` (+ `StdioConnectionParams` wrapping
`mcp.StdioServerParameters`),
`google.adk.events.Event`, `google.genai.types`.

### 2a. Ingestion — `mcp_server.py` unchanged, consumed via `MCPToolset`

- **Untouched:** `src/ingestion/mcp_server.py` and `src/ingestion/ingestion.py`.
- The orchestrator obtains the `load_reports` tool through ADK's MCP integration:
  **`MCPToolset`** with `StdioConnectionParams(server_params=StdioServerParameters(command=<python>, args=["-m","src.ingestion.mcp_server"]))`.
- Because ingestion must be **deterministic with a logged fallback** (hard requirement +
  T4 tests), we do **not** hand the MCPToolset to an LLM and hope it calls the tool. Instead
  a small non-LLM `IngestionAgent(BaseAgent)` (or a plain helper invoked before the Runner)
  does: try `MCPToolset` → call `load_reports(history_path, report_path)` → on any exception,
  fall back to `ingestion_pipeline(...)`. It writes `prior_snapshot` and `incoming` to
  session state and logs which path ran, **reusing the exact current log strings** so T4's
  log assertions need only a patch-target rename (see §3).
- This keeps "orchestrator consumes ingestion via the ADK MCPToolset" true, while preserving
  the fallback and its logging.

### 2b. Signal — `LlmAgent` that ranks ids; detectors run deterministically

Per D1, the signal stage is a **`SequentialAgent("signal_pipeline")`** of three sub-agents:

1. **`DetectionAgent(BaseAgent)`** — pure Python, no LLM. Reads `prior_snapshot` /
   `incoming` from state, builds DataFrames, calls the **untouched**
   `run_all_detectors(...)`, assigns stable integer ids, and writes:
   - `state["flags"]` — full flags (with numbers), never shown to the LLM.
   - `state["flags_for_llm_json"]` — JSON string of `[{id, detector, health_zone}]` only.

2. **`RankingAgent(LlmAgent)`** — the only LLM in the system.
   - `name`: `"ranking_agent"`
   - `model`: `GEMINI_MODEL`
   - `tools`: **none** (required, because `output_schema` is set).
   - `instruction` (outline): "You are an epidemiological risk assessor. You are given a list
     of flags, each with an `id`, a `detector` type, and a `health_zone` — and **no counts**.
     Rank the ids by urgency (active danger > early warning > data gap). Return the ids as a
     permutation; never invent ids; explain referring to zones by name only, never numbers."
     The flag list is injected via state templating: `{flags_for_llm_json}`.
   - `output_schema`: `RankingDecision` (Pydantic) →
     `class RankingDecision(BaseModel): order: list[int]; reasoning: str`
   - `output_key`: `"ranking"` (ADK writes the validated object to `state["ranking"]`).

3. **`GuardAgent(BaseAgent)`** — pure Python, **the id-based guard, logic preserved verbatim
   from `signal_agent.py`**. Reads `state["ranking"]` and `state["flags"]`, calls the
   extracted pure function `apply_rank_guard(flags, order)` (see §3), writes
   `state["ranked_flags"]` and `state["reasoning"]`. On a non-permutation it falls back to the
   deterministic priority order `cfr_shift > surge > new_zone > stale_or_missing`.

The LLM still only ranks ids; numbers never enter its context; the guard is unchanged.

### 2c. Alert — keep `draft_alert` outside the agent flow (D2)

- **Untouched:** `src/alert/alert_agent.py` (template + the no-`reasoning`-param signature).
- **Recommendation:** invoke `draft_alert(ranked_flags)` from `run_scan` **after** the
  Runner finishes, *not* as an ADK agent.
- **Justification:** `draft_alert` is pure, deterministic, and fully covered by 5 alert
  tests. Wrapping it as an agent adds an ADK shim with zero functional benefit. Keeping it
  outside the LLM-bearing flow makes the safety boundary crisp — no agent/LLM context ever
  touches the human-facing brief — which matters most in a healthcare setting and directly
  serves hard rules 3 and 5. It also keeps `test_alert.py` untouched and the alert step in
  `run_scan` byte-identical to today.
- Trade-off: the top-level composition is then "ingestion → **signal_pipeline** → alert(post-
  step)" rather than a single three-stage `SequentialAgent`. If you prefer the literal
  three-stage Sequential, the alternative is a trivial `AlertAgent(BaseAgent)` that reads
  `state["ranked_flags"]` and calls `draft_alert`; it's ~8 lines but pulls the deterministic
  output into the agent runtime. Recommended: post-step.

### 2d. Orchestrator — thin `Runner` invocation; state contract

- **Composition:** `root = SequentialAgent("scan", sub_agents=[DetectionAgent, RankingAgent,
  GuardAgent])` for the signal stage. Ingestion (2a) seeds state before the run; alert (2c)
  consumes state after.
- **`run_scan(incoming_path, history_path="data/history.csv")` keeps its signature and its
  return shape `{status, alert, flags}`.** New body, same contract:
  1. Ingestion seam (2a) → `prior_snapshot`, `incoming` (+ logged MCP/fallback).
  2. `session = InMemorySessionService().create_session(state={"prior_snapshot":...,
     "incoming":...})`.
  3. `runner = Runner(agent=root, session_service=...)`; drive it to completion over a
     trivial trigger `types.Content(role="user", parts=[types.Part(text="scan")])`.
  4. Read `state["ranked_flags"]` and `state["reasoning"]`; **log reasoning, never pass it to
     `draft_alert`** (unchanged invariant).
  5. `alert = draft_alert(ranked_flags)`; return `{status, alert, flags: ranked_flags}`.
- **State contract between agents:** plain JSON-serializable values in `session.state`:
  `prior_snapshot: list[dict]`, `incoming: list[dict]` (seeded) → `flags: list[dict]`,
  `flags_for_llm_json: str` (DetectionAgent) → `ranking: {order, reasoning}` (RankingAgent
  via `output_key`) → `ranked_flags: list[dict]`, `reasoning: str` (GuardAgent). DataFrames
  live only inside DetectionAgent; only records cross agent boundaries (same as today's
  `prior_snapshot`/`incoming` dicts), so nothing un-serializable enters state.
- `asyncio` note: `Runner.run_async` is a coroutine. The **real** entry point is
  `run_scan_async`; sync `run_scan` delegates to it (see Addendum A1 for the live-event-loop /
  Kaggle handling). Do **not** assume a bare `asyncio.run` is safe — it raises inside a running
  loop.

---

## 3. What stays untouched / what adapts

**Untouched (no edits):**
- `src/signal/detectors.py` — all four detectors and `run_all_detectors`.
- The data contract (8 columns) and every reader.
- `src/alert/alert_agent.py` — template and `draft_alert(ranked_flags)` signature.
- `src/ingestion/ingestion.py` and `src/ingestion/mcp_server.py`.
- `src/config.py` thresholds (only the env-alias lines from §1 are added).

**The id-based guard moves but its logic does not change.** Extract the current permutation
check + `fallback_rank_flags` from `signal_agent.py` into a pure function
`apply_rank_guard(flags, order) -> (ranked_flags, reasoning)`. `GuardAgent` calls it; the old
test calls it. No behavioral change.

**Tests — 12 unchanged, 5 adapted, 0 rewritten:**

| Test | File | Verdict |
|------|------|---------|
| `test_new_zone`, `test_surge`, `test_cfr_shift`, `test_stale_or_missing`, `test_new_zone_no_surge_raise`, `test_null_confirmed_skipped` | test_detectors | **unchanged** (detectors untouched) |
| all 5 alert tests | test_alert | **unchanged** |
| `test_run_scan_new_zone` | test_orchestrator | **unchanged** — `run_scan` keeps signature/return; still a live end-to-end smoke |
| `test_llm_guard_tampered_ids` | test_detectors | **adapt (minimal):** retarget from `run_signal_agent(...)` to `apply_rank_guard(flags, order=[0,99])`; assert fallback ordering. No LLM mock needed. |
| `test_mcp_failure_falls_back_to_plain` (T4a) | test_orchestrator | **adapt:** patch the new ingestion seam name instead of `_call_mcp_load_reports`; **log strings preserved**, assertions unchanged. |
| `test_mcp_success_uses_mcp_path` (T4b) | test_orchestrator | **adapt:** same patch-target rename; assertions unchanged. |
| `test_reasoning_excluded_from_alert` (T3) | test_orchestrator | **adapt:** inject sentinel `reasoning` via the new signal seam / a stubbed `GuardAgent` state instead of mocking `run_signal_agent`; assertions unchanged. |
| `test_multi_signal_fallback_top_signal` (T2) | test_orchestrator | **adapt:** ADK owns the LLM call, so `genai.Client` patching no longer intercepts it. Replace with either (a) a stub `model` on `RankingAgent` returning a non-permutation, or (b) unit-test `apply_rank_guard` on the real multi_signal flags. Assertions (cfr_shift top, headline match) unchanged. |

**Key cross-cutting test fact:** patching `src.signal.signal_agent.genai.Client` will **not**
control an ADK `LlmAgent` — ADK invokes the model through its own model layer. Any test that
needs to control LLM output adapts to ADK's mechanism (substitute the agent's `model` with a
stub `BaseLlm`) rather than patching the SDK client. This affects exactly T2 and (indirectly)
the guard test, both already listed above.

To keep churn minimal, two design requirements are binding: **(i)** preserve the ingestion
log strings verbatim, and **(ii)** keep a single patchable ingestion seam function in
`orchestrator.py`. With those, the orchestrator tests adapt by patch-target rename, not logic.

---

## 4. Verification plan

Done = all of the following, in order:

1. **Smoke gate (Phase 0):** a one-LlmAgent ADK "hello" runs against `gemini-2.5-flash` via
   the env mapping in §1. Pin the exact `google-adk` version here.
2. **Ingestion parity:** the `MCPToolset` path returns records **identical** to
   `ingestion_pipeline(...)` for the same inputs (Phase 2 acceptance, re-asserted).
3. **Unit suite:** run `python -m unittest discover -s tests -v`. Target: **17 pass**,
   12 unchanged + 5 adapted as in §3. No test rewritten; no assertion weakened.
4. **Five scenarios end-to-end** via `run_scan`: `incoming_new_zone`, `incoming_spike`,
   `incoming_data_gap`, `incoming_cfr_shift`, `incoming_multi_signal`. For each, confirm the
   correct flag(s) are produced and the **alert content matches the current Option-A output**.
   Acceptance nuance (stated in the brief): the alert body is deterministic from the ranked
   flags, so only the **ranking order** may differ when the live LLM is involved; the
   headline must still name the top-ranked flag's zone, and multi_signal must still surface
   all four detectors. Compare against the Option-A outputs captured before the refactor
   (capture them on the current branch first, as the baseline).
5. **No new LLM call count regression:** still exactly one LLM invocation per scan (the
   `RankingAgent`); detectors and alert remain LLM-free.

---

## 5. Risks and rollback

**Risks (most likely first):**
- **R1 — `output_schema` ⊕ tools constraint** (see D1). Mitigated by the deterministic-
  detection design; if a pinned ADK version relaxes/changes this, no harm.
- **R2 — MCPToolset stdio lifecycle on Windows.** ADK spawning the stdio MCP subprocess and
  cleaning it up across `asyncio.run` boundaries can hang or leak on Windows. Mitigation: the
  fallback to `ingestion_pipeline` already covers a dead server; if MCPToolset itself is
  flaky, ingestion can call the plain pipeline by default and treat MCP as best-effort.
- **R3 — event-loop nesting.** `Runner.run_async` inside `asyncio.run` inside a sync
  `run_scan`; nested loops can error if a caller is already async. Mitigation: keep `run_scan`
  sync-only (as today) and document it.
- **R4 — ADK API drift.** Import paths / `MCPToolset` connection-param classes differ across
  1.x minors. Mitigation: pin exactly in Phase 0; verify imports against the installed
  version, not this doc.
- **R5 — test-control of the LLM.** Substituting a stub model in ADK is more involved than
  patching `genai.Client`. Mitigation: push LLM-independent assertions down to
  `apply_rank_guard` (pure), keep only one live LLM integration test.

**Rollback / go-no-go (protects the July 6, 2026 deadline; today is 2026-06-24):**
- Do the refactor on a separate branch/worktree. Option A (docs-only reword) is independent
  and can ship at any time regardless of this work.
- **Cutoff: end of 2026-06-29.** By then, two gates must be green:
  (1) the Phase-0 smoke gate, and (2) ingestion parity + the `RankingAgent`→guard path
  returning a valid permutation on `incoming_multi_signal`.
- If either gate is red at the cutoff, **revert to Option A**: discard the branch, and instead
  edit the four doc locations (implementation.md §92/§18, context.md §52, SKILL.md §48) to
  describe the as-built Python+google-genai+MCP wiring. That leaves a full week of buffer for
  the writeup. The ≥3 demonstrated-concepts bar is still met by MCP tool + memory + guardrails/
  evals even without ADK.

---

## Decisions (resolved 2026-06-24)
- **D1 accepted:** deterministic detection; the LLM ranks ids only and never sees counts.
- **D2 accepted:** `draft_alert` runs as a post-step in the orchestrator, outside the agent flow.
- **Cutoff confirmed:** 2026-06-29 go/no-go. The ADK build lives on an isolated branch
  (`feature/adk-refactor`); `main` holds the Option-A reference + captured baselines for rollback.

---

## Addendum — review round 2 (binding refinements)

### A1. Async-first entry point (Kaggle / live event loop)
`asyncio.run()` raises inside a notebook's already-running loop, so a sync-only `run_scan`
would break the demo. Design:
- `async def run_scan_async(incoming_path, history_path=...)` is the **real** entry point and
  holds the Runner-drive logic.
- `def run_scan(...)` is a thin sync wrapper:
  - `try: asyncio.get_running_loop()`
    - **no loop** (`RuntimeError` raised) → `return asyncio.run(run_scan_async(...))`.
    - **loop already running** (notebook) → do **not** call `asyncio.run`; offload to a worker
      thread with its own loop (`ThreadPoolExecutor` running `asyncio.run(run_scan_async(...))`)
      and return its result.
- Rationale: the brief's "only call `asyncio.run` when no loop is running" defines the no-loop
  path but leaves the loop-running path undefined — and in Kaggle a loop *is* running, so the
  sync wrapper must still complete. Thread-offload returns a real result without `nest_asyncio`
  hacks; notebook users may also `await run_scan_async(...)` directly.
- **Test (T-async):** from inside a running event loop, call sync `run_scan(...)` (hermetic:
  mocked ingestion + stubbed ranking) and assert it returns a valid alert rather than raising;
  also assert `await run_scan_async(...)` works in the same loop.

### A2. Ids-only projection test (T-projection)
Two hermetic assertions:
- Parse `state["flags_for_llm_json"]` and assert **every item has exactly keys
  `{id, detector, health_zone}`** and **no count keys** (`suspected_cases`, `confirmed_cases`,
  `deaths`, `confirmed_prior`, `confirmed_incoming`, `daily_new`, `pct_growth`, `cfr_prior`,
  `cfr_incoming`, `cfr_diff`, …).
- Assert `RankingAgent.instruction` contains the token `flags_for_llm_json` and does **not**
  contain the bare `{flags}` state-injection placeholder. Precision: check for the `{flags}`
  template token, **not** the substring `"flags"` (which legitimately occurs inside
  `flags_for_llm_json`).

### A3. MCPToolset success must be captured (not just fallback)
Verification must include **at least one run where the MCPToolset path succeeds** and returns
records **identical** to `ingestion_pipeline(...)` for the same inputs, recorded in
`walkthrough.md` (matching zone sets + row counts + empty diff). Fallback-only runs are not
proof; earlier scenario runs mostly hit fallback, so this is called out explicitly.

### A4. Guard remains load-bearing (affirmed)
`output_schema=RankingDecision{order: list[int]}` guarantees only the **shape** (a list of
ints) — not that `order` is a permutation of the input ids (it can be `[0,0]`, out-of-range,
short, or long). `apply_rank_guard`'s permutation check is therefore **still required**, is
preserved verbatim, and is unit-tested (`test_llm_guard_tampered_ids` → `apply_rank_guard`).

### A5. Execution order (with environment corrections)
1. **Capture Option-A baselines** on `main` (5 scenarios via the current `run_scan`); commit as
   the regression reference.
2. **Phase-0 smoke gate** on `feature/adk-refactor`: install `google-adk==2.3.0`, verify
   imports, one-LlmAgent hello against `gemini-2.5-flash`, pin the exact version.
   → **STOP for review.**
3. Build DetectionAgent → RankingAgent → GuardAgent; confirm `signal_pipeline` returns a valid
   permutation on `incoming_multi_signal`. → **STOP for review.**
4. Wire `run_scan_async`/`run_scan` + the alert post-step; full verification (§4 + A1–A3).

### Environment corrections found during recon (2026-06-24)
- **`google-adk` latest is `2.3.0`, not 1.x.** §1's old `>=1.0.0,<2.0.0` is superseded; target
  `2.3.0` and verify all import paths in the smoke gate (the major bump may move
  `MCPToolset`/agent classes).
- **The directory is not a git repository.** Branch isolation requires `git init` first, with a
  `.gitignore` excluding `venv/`, `__pycache__/`, `*.pyc`, and **`.env` (contains the API
  key)**. Reference state committed to `main`; the build proceeds on `feature/adk-refactor`.
