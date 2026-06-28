"""Orchestrator for the Ebola surveillance signal-detector agent (ADK).

Flow: ingestion (ADK MCPToolset, with a logged plain fallback) -> signal_pipeline
(ADK SequentialAgent: detection -> ranking -> guard) -> draft_alert (deterministic
post-step, outside the agent/LLM flow).

`run_scan_async` is the real entry point. `run_scan` is a sync wrapper that is safe to
call from a plain script OR from inside a live event loop (e.g. a Kaggle notebook), where
`asyncio.run` would otherwise raise.
"""
import asyncio
import concurrent.futures
import json
import logging
import os
import sys
from typing import Dict

from mcp import StdioServerParameters
from google.adk.tools.mcp_tool import MCPToolset, StdioConnectionParams
from google.adk.tools.tool_context import ToolContext
from google.adk.agents.invocation_context import InvocationContext
from google.adk.sessions import InMemorySessionService

import src.config  # noqa: F401  — sets GOOGLE_API_KEY / GOOGLE_GENAI_USE_VERTEXAI aliases
from src.ingestion.ingestion import ingestion_pipeline
from src.signal.signal_pipeline import run_signal_pipeline_async
from src.alert.alert_agent import draft_alert
from src.guardrails.guardrails import enforce_guardrails

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _coerce_mcp_result(raw) -> Dict:
    """Normalize an MCP tool result into the ingestion_pipeline dict."""
    if isinstance(raw, dict) and "prior_snapshot" in raw:
        data = raw
    elif isinstance(raw, dict) and raw.get("content"):
        if raw.get("isError"):
            raise RuntimeError(f"MCP load_reports returned an error result: {raw}")
        data = json.loads(raw["content"][0]["text"])
    elif isinstance(raw, str):
        data = json.loads(raw)
    else:
        raise RuntimeError(f"Unexpected MCP result shape: {type(raw)!r}")
    if "prior_snapshot" not in data or "incoming" not in data:
        raise RuntimeError("MCP result missing prior_snapshot/incoming")
    return data


async def _load_reports_via_mcptoolset(history_path: str, report_path: str) -> Dict:
    """Call the load_reports MCP tool through the ADK MCPToolset. Raises on any failure
    so the caller can fall back."""
    toolset = MCPToolset(
        connection_params=StdioConnectionParams(
            server_params=StdioServerParameters(
                command=sys.executable,
                args=["-m", "src.ingestion.mcp_server"],
                env=dict(os.environ),
            )
        )
    )
    try:
        tools = await toolset.get_tools()
        load_tool = next(t for t in tools if t.name == "load_reports")

        session_service = InMemorySessionService()
        session = await session_service.create_session(app_name="ingest", user_id="ingest")
        ictx = InvocationContext(
            session_service=session_service,
            invocation_id="ingest",
            session=session,
            agent=None,
        )
        raw = await load_tool.run_async(
            args={"history_path": history_path, "report_path": report_path},
            tool_context=ToolContext(ictx),
        )
        return _coerce_mcp_result(raw)
    finally:
        await toolset.close()


async def _load_reports(history_path: str, report_path: str) -> Dict:
    """Ingestion seam: ADK MCPToolset first, plain ingestion_pipeline on any failure.
    Returns {prior_snapshot, incoming, source}; logs which path ran."""
    try:
        data = await _load_reports_via_mcptoolset(history_path, report_path)
        logger.info("Successfully loaded reports via Ingestion MCP server (MCPToolset).")
        source = "mcp"
    except Exception as e:  # noqa: BLE001 — any MCP failure must degrade cleanly
        logger.warning(
            "MCP ingestion via MCPToolset unavailable or failed: %s. "
            "Falling back to plain ingestion_pipeline.",
            e,
        )
        data = ingestion_pipeline(history_path, report_path)
        logger.info("Successfully loaded reports via plain ingestion_pipeline fallback.")
        source = "plain"
    return {"prior_snapshot": data["prior_snapshot"], "incoming": data["incoming"], "source": source}


async def run_scan_async(
    incoming_path: str, history_path: str = "data/history.csv", persist: bool = False
) -> Dict:
    """Real entry point. ingestion -> signal_pipeline -> draft_alert -> guardrail.

    If persist=True, the scanned incoming report is appended to the history store after the
    scan (so this scan still compares against the prior state). Default off to keep demo/test
    runs reproducible.
    """
    logger.info("Loading reports (incoming=%s)...", incoming_path)
    loaded = await _load_reports(history_path, incoming_path)

    logger.info("Invoking signal pipeline (detection -> ranking -> guard)...")
    signal_result = await run_signal_pipeline_async(loaded["prior_snapshot"], loaded["incoming"])
    ranked_flags = signal_result.get("flags", [])
    reasoning = signal_result.get("reasoning", "")

    # Reasoning is logged for debugging only; it is NEVER passed into the human-facing brief.
    logger.info("Signal reasoning (logged, not rendered):\n%s", reasoning)

    logger.info("Drafting alert brief...")
    alert_text = draft_alert(ranked_flags)

    # Guardrail layer: last check before anything leaves. Fail-closed on integrity failures.
    guard = enforce_guardrails(alert_text, ranked_flags)
    if guard.blocked:
        logger.warning("Guardrail BLOCKED the brief: %s", guard.violations)
    elif guard.violations:
        logger.warning("Guardrail flagged (non-blocking): %s", guard.violations)

    # Memory: opt-in append of the scanned report to the durable history store.
    persisted = 0
    if persist:
        from src.memory.history_store import append_to_history
        persisted = append_to_history(loaded["incoming"], history_path)
        logger.info("Persisted %d new history row(s) to %s.", persisted, history_path)

    return {
        "status": "success",
        "alert": guard.alert,
        "flags": ranked_flags,
        "guardrail": {
            "passed": guard.passed,
            "blocked": guard.blocked,
            "violations": guard.violations,
        },
        "persisted": persisted,
    }


def run_scan(
    incoming_path: str, history_path: str = "data/history.csv", persist: bool = False
) -> Dict:
    """Sync wrapper around run_scan_async.

    - No running loop (plain script): use asyncio.run.
    - Running loop (e.g. Kaggle notebook): asyncio.run would raise, so offload to a worker
      thread with its own loop and block on the result. Notebook users may instead
      `await run_scan_async(...)` directly.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(run_scan_async(incoming_path, history_path, persist))

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            lambda: asyncio.run(run_scan_async(incoming_path, history_path, persist))
        )
        return future.result()
