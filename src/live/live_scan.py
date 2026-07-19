"""Phase 3 (option B): scan extracted records as a *new report* against the state of history
BEFORE this report.

The "prior" excludes any history row whose `source_url` matches the current report's url — a
stable, timestamp-independent definition of "before this report" (correct across multiple
promotion batches from the same report in one session). This is what keeps newly emerging
(`new_zone`) and accelerating (`surge`) clusters visible even after the report's rows have been
promoted into history.
"""
import pandas as pd

from src.ingestion.ingestion import get_prior_snapshot, load_history
from src.memory.history_store import CONTRACT_COLUMNS
from src.signal.detectors import run_all_detectors
from src.signal.signal_pipeline import run_signal_pipeline_async
from src.alert.alert_agent import draft_alert
from src.guardrails.guardrails import enforce_guardrails


def _contract_only(records):
    """Project to the eight contract columns (drop snippet / extras before detection)."""
    return [{c: r.get(c) for c in CONTRACT_COLUMNS} for r in records]


def prior_excluding_source(history_path: str, source_url: str):
    """Latest-per-zone snapshot of history with rows from `source_url` removed — i.e. history
    as it was before this report. Returns a list of prior records."""
    hist = load_history(history_path)
    pool = hist[hist["source_url"] != source_url]
    return get_prior_snapshot(pool).to_dict(orient="records")


def detect_new_data(records, source_url: str, history_path: str = "data/history.csv"):
    """Deterministic detection of `records` (as the incoming report) against the source-excluded
    prior. Returns the flag list. No LLM — used by the hermetic e2e test."""
    prior = prior_excluding_source(history_path, source_url)
    return run_all_detectors(pd.DataFrame(_contract_only(records)), pd.DataFrame(prior))


async def run_scan_on_new_data(records, source_url: str, history_path: str = "data/history.csv"):
    """Full scan of the new report against the source-excluded prior: detection -> ranking ->
    guard -> alert -> guardrail. Returns {alert, flags, guardrail}."""
    prior = prior_excluding_source(history_path, source_url)
    signal = await run_signal_pipeline_async(prior, _contract_only(records))
    ranked = signal.get("flags", [])
    alert = draft_alert(ranked)
    guard = enforce_guardrails(alert, ranked)
    return {
        "alert": guard.alert,
        "flags": ranked,
        "guardrail": {"passed": guard.passed, "blocked": guard.blocked, "violations": guard.violations},
    }
