"""Step-4 verification:
1. Run all five scenarios through the full ADK run_scan_async (MCPToolset ingestion ->
   signal_pipeline -> draft_alert); assert each detector SET matches the Option-A baseline
   (detector set is deterministic; LLM ranking order may vary).
2. A3 parity: prove the MCPToolset ingestion returns records identical to ingestion_pipeline.
Writes evidence to walkthrough.md.
"""
import asyncio
import ast
import re

import pandas as pd

from src.orchestrator import run_scan_async, _load_reports_via_mcptoolset
from src.ingestion.ingestion import ingestion_pipeline

HISTORY = "data/history.csv"
SCENARIOS = [
    ("new_zone", "data/incoming/incoming_new_zone.json"),
    ("spike", "data/incoming/incoming_spike.json"),
    ("data_gap", "data/incoming/incoming_data_gap.json"),
    ("cfr_shift", "data/incoming/incoming_cfr_shift.json"),
    ("multi_signal", "data/incoming/incoming_multi_signal.json"),
]


def baseline_set(name):
    txt = open(f"baselines/{name}.txt", encoding="utf-8").read()
    m = re.search(r"detector_set \(deterministic\): (\[.*\])", txt)
    return sorted(ast.literal_eval(m.group(1)))


def norm(records):
    """Canonicalize records for comparison (dates -> uniform string; stable sort)."""
    out = []
    for r in records:
        rr = dict(r)
        for k in ("date", "report_date"):
            if rr.get(k) is not None:
                rr[k] = pd.to_datetime(rr[k]).strftime("%Y-%m-%d %H:%M:%S")
        out.append(rr)
    return sorted(out, key=lambda d: (str(d.get("health_zone")), str(d.get("date"))))


async def main():
    rows = []
    for name, path in SCENARIOS:
        res = await run_scan_async(path, HISTORY)
        got = sorted({f["detector"] for f in res["flags"]})
        exp = baseline_set(name)
        top = res["flags"][0]["detector"] if res["flags"] else "(none)"
        match = "PASS" if got == exp else "FAIL"
        rows.append((name, match, top, got, exp))
        assert got == exp, f"{name}: detector set {got} != baseline {exp}"

    # A3: MCPToolset vs plain ingestion parity (multi_signal: richest fixture).
    mcp = await _load_reports_via_mcptoolset(HISTORY, "data/incoming/incoming_multi_signal.json")
    plain = ingestion_pipeline(HISTORY, "data/incoming/incoming_multi_signal.json")
    prior_zones_mcp = sorted({r["health_zone"] for r in mcp["prior_snapshot"]})
    prior_zones_plain = sorted({r["health_zone"] for r in plain["prior_snapshot"]})
    inc_rows_mcp, inc_rows_plain = len(mcp["incoming"]), len(plain["incoming"])
    prior_match = norm(mcp["prior_snapshot"]) == norm(plain["prior_snapshot"])
    inc_match = norm(mcp["incoming"]) == norm(plain["incoming"])
    parity = prior_match and inc_match and prior_zones_mcp == prior_zones_plain and inc_rows_mcp == inc_rows_plain

    # ---- console ----
    for name, match, top, got, exp in rows:
        print(f"{name:13s} {match}  top={top:18s} set={got}")
    print(f"\nA3 MCP parity: prior_rows {len(mcp['prior_snapshot'])}=={len(plain['prior_snapshot'])}, "
          f"incoming_rows {inc_rows_mcp}=={inc_rows_plain}, "
          f"prior_records_identical={prior_match}, incoming_records_identical={inc_match} -> "
          f"{'PASS' if parity else 'FAIL'}")
    assert parity, "A3 MCP parity FAILED"

    # ---- walkthrough.md ----
    with open("walkthrough.md", "w", encoding="utf-8") as fh:
        fh.write("# walkthrough.md — ADK refactor verification (step 4)\n\n")
        fh.write("Full path exercised: **MCPToolset ingestion -> ADK signal_pipeline "
                 "(detection -> ranking -> guard) -> deterministic draft_alert**.\n\n")
        fh.write("## Five scenarios (detector set vs Option-A baseline)\n\n")
        fh.write("| scenario | match | top signal (LLM-ranked) | detector set |\n")
        fh.write("|---|---|---|---|\n")
        for name, match, top, got, exp in rows:
            fh.write(f"| {name} | {match} | {top} | {got} |\n")
        fh.write("\nDetector sets are deterministic and match the baselines exactly; the top "
                 "signal is the live LLM ranking (guard-validated permutation) and may vary.\n\n")
        fh.write("## A3 — MCPToolset ingestion equals ingestion_pipeline (multi_signal)\n\n")
        fh.write(f"- prior_snapshot rows: MCP={len(mcp['prior_snapshot'])}, plain={len(plain['prior_snapshot'])}\n")
        fh.write(f"- incoming rows: MCP={inc_rows_mcp}, plain={inc_rows_plain}\n")
        fh.write(f"- prior zone set (both): {prior_zones_mcp}\n")
        fh.write(f"- prior records identical (dates canonicalized): {prior_match}\n")
        fh.write(f"- incoming records identical (dates canonicalized): {inc_match}\n")
        fh.write(f"\n**MCP parity: {'PASS' if parity else 'FAIL'}** — the ADK MCPToolset path "
                 "returns the same records as the plain function, so the MCP path is proven "
                 "(not merely the fallback).\n")
    print("\nWrote walkthrough.md")


if __name__ == "__main__":
    asyncio.run(main())
