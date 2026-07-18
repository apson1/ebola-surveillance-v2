"""
MCP Server for Ebola surveillance ingestion.
Exposes the ingestion pipeline as an MCP tool.
"""

from mcp.server.fastmcp import FastMCP
from src.ingestion.ingestion import ingestion_pipeline
from src.ingestion.live_sources import fetch_recent_drc_ebola_reports

mcp = FastMCP("Ebola Surveillance Ingestion Server")

@mcp.tool()
def load_reports(history_path: str, report_path: str) -> dict:
    """
    Ingest the historical seed CSV and an incoming report JSON.
    Validates both against the data contract, then returns the
    prior per-zone snapshot and incoming records separately.
    """
    return ingestion_pipeline(history_path, report_path)


@mcp.tool()
def list_recent_sources(limit: int = 10) -> list:
    """
    List the latest official DRC Ebola situation reports from ReliefWeb (metadata only:
    id, title, source, date, url). Returns an empty list if live sources are disabled or
    unavailable. Extracts no numbers and does not touch the detection pipeline.
    """
    return fetch_recent_drc_ebola_reports(limit).reports

if __name__ == "__main__":
    mcp.run()
