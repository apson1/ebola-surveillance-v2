"""
MCP Server for Ebola surveillance ingestion.
Exposes the ingestion pipeline as an MCP tool.
"""

from mcp.server.fastmcp import FastMCP
from src.ingestion.ingestion import ingestion_pipeline

mcp = FastMCP("Ebola Surveillance Ingestion Server")

@mcp.tool()
def load_reports(history_path: str, report_path: str) -> dict:
    """
    Ingest the historical seed CSV and an incoming report JSON.
    Validates both against the data contract, then returns the
    prior per-zone snapshot and incoming records separately.
    """
    return ingestion_pipeline(history_path, report_path)

if __name__ == "__main__":
    mcp.run()
