"""
Orchestrator for the Ebola surveillance signal-detector agent.
Wires together ingestion, signal, and alert layers.
"""

import asyncio
import logging
import os
import sys
from typing import Dict

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from src.signal.signal_agent import run_signal_agent
from src.alert.alert_agent import draft_alert

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def _call_mcp_load_reports(history_path: str, incoming_path: str) -> Dict:
    """Helper to run the MCP server and call load_reports tool."""
    # Use current running python interpreter to run the module
    python_exe = sys.executable
    if not python_exe or not os.path.exists(python_exe):
        python_exe = os.path.join(os.getcwd(), "venv", "Scripts", "python.exe")
        
    server_params = StdioServerParameters(
        command=python_exe,
        args=["-m", "src.ingestion.mcp_server"],
        env=os.environ.copy()
    )
    
    async with stdio_client(server_params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            tool_result = await session.call_tool(
                name="load_reports",
                arguments={
                    "history_path": history_path,
                    "report_path": incoming_path
                }
            )
            import json
            return json.loads(tool_result.content[0].text)


def run_scan(incoming_path: str, history_path: str = "data/history.csv") -> Dict:
    """
    Main orchestrator entry point:
    1. Loads prior and incoming data (MCP with plain fallback).
    2. Runs signal agent to detect and rank anomalies.
    3. Drafts human brief using alert agent.
    4. Logs model reasoning and returns the finished brief and flags.
    """
    ingested_data = None
    
    # 1. Prefer Ingestion MCP Server tool
    try:
        logger.info("Attempting to load reports using Ingestion MCP server...")
        ingested_data = asyncio.run(_call_mcp_load_reports(history_path, incoming_path))
        logger.info("Successfully loaded reports via Ingestion MCP server.")
    except Exception as e:
        logger.warning(
            f"MCP server loading was unavailable or failed: {e}. "
            "Falling back to plain ingestion_pipeline function..."
        )
        
    # Fallback to plain ingestion function
    if ingested_data is None:
        from src.ingestion.ingestion import ingestion_pipeline
        ingested_data = ingestion_pipeline(history_path, incoming_path)
        logger.info("Successfully loaded reports via plain ingestion_pipeline fallback.")
        
    prior_snapshot = ingested_data.get("prior_snapshot", [])
    incoming = ingested_data.get("incoming", [])
    
    # 2. Run signal agent
    logger.info("Invoking signal agent...")
    signal_result = run_signal_agent(prior_snapshot, incoming)
    
    ranked_flags = signal_result.get("flags", [])
    reasoning = signal_result.get("reasoning", "")
    
    # 3. Log model reasoning separately at INFO level
    logger.info(f"Signal Agent Reasoning (debugging context):\n{reasoning}")
    
    # 4. Draft alert brief (reasoning is logged but never passed to human brief)
    logger.info("Drafting alert brief...")
    alert_text = draft_alert(ranked_flags)
    
    return {
        "status": "success",
        "alert": alert_text,
        "flags": ranked_flags
    }
