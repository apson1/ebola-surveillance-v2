"""
Ingestion for the Ebola surveillance agent.

Responsibilities:
- Load the historical seed (CSV) and an incoming report (JSON).
- Validate both against the data contract. A structurally broken row raises an error.
  A present-but-null count is allowed and surfaced, because that is a data-quality signal.
- Return the prior per-zone snapshot (from history only) and the incoming records
  separately, so the signal layer can diff incoming against the prior state.

Data contract (8 columns):
date, province, health_zone, suspected_cases, confirmed_cases, deaths, source_url, report_date
- date: as-of date (when the situation was true), ISO 8601
- report_date: publication date (when the report was issued), ISO 8601
- counts are cumulative integers; null allowed only in incoming reports as a data-quality signal
"""

import json
from typing import Dict, List, Optional

import pandas as pd

CONTRACT_COLUMNS = [
    "date", "province", "health_zone",
    "suspected_cases", "confirmed_cases", "deaths",
    "source_url", "report_date",
]
COUNT_COLUMNS = ["suspected_cases", "confirmed_cases", "deaths"]
IDENTITY_COLUMNS = ["date", "province", "health_zone", "source_url"]


def load_history(history_path: str = "data/history.csv") -> pd.DataFrame:
    """Load and validate the historical store. confirmed_cases and deaths must be complete
    (no nulls); suspected_cases may be null for live-promoted records (see context.md sec 8)."""
    df = pd.read_csv(history_path)

    missing = [c for c in CONTRACT_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"History is missing required columns: {missing}")

    df["date"] = pd.to_datetime(df["date"])
    df["report_date"] = pd.to_datetime(df["report_date"])

    for col in COUNT_COLUMNS:
        # errors='raise' rejects any non-numeric value in history
        df[col] = pd.to_numeric(df[col], errors="raise")

    # confirmed_cases and deaths must be complete; suspected_cases may be null (live promotion).
    for col in ("confirmed_cases", "deaths"):
        if df[col].isnull().any():
            raise ValueError(f"History has a null in '{col}'. Confirmed cases and deaths must be complete.")

    return df


def load_incoming_report(report_path: str = "data/incoming/incoming_new_zone.json") -> pd.DataFrame:
    """
    Load and validate an incoming report.
    Raises on a structurally broken record (missing identity field, non-numeric count).
    Allows a null count and keeps it as a data-quality signal.
    """
    with open(report_path, "r") as f:
        payload = json.load(f)

    top_report_date = payload.get("report_date")
    records = payload.get("data", [])
    if not records:
        raise ValueError(f"Incoming report '{report_path}' has no records under 'data'.")

    clean: List[Dict] = []
    for i, raw in enumerate(records):
        rec = dict(raw)
        rec.setdefault("report_date", top_report_date)

        for col in IDENTITY_COLUMNS:
            if col not in rec or rec[col] in (None, ""):
                raise ValueError(f"Incoming record {i} is missing required field '{col}'.")
        if not rec.get("report_date"):
            raise ValueError(f"Incoming record {i} has no report_date and no top-level report_date.")

        for col in COUNT_COLUMNS:
            val = rec.get(col, None)
            if val is None:
                rec[col] = None  # data-quality signal, allowed
            else:
                try:
                    rec[col] = int(val)
                except (TypeError, ValueError):
                    raise ValueError(
                        f"Incoming record {i} field '{col}' is not an integer: {val!r}"
                    )

        clean.append({c: rec.get(c) for c in CONTRACT_COLUMNS})

    df = pd.DataFrame(clean, columns=CONTRACT_COLUMNS)
    df["date"] = pd.to_datetime(df["date"])
    df["report_date"] = pd.to_datetime(df["report_date"])
    return df


def get_prior_snapshot(history_df: pd.DataFrame) -> pd.DataFrame:
    """Latest row per health zone from history only. This is the state to diff against."""
    return (
        history_df.sort_values("date")
        .drop_duplicates(subset=["health_zone"], keep="last")
        .reset_index(drop=True)
    )


def ingestion_pipeline(
    history_path: str = "data/history.csv",
    report_path: str = "data/incoming/incoming_new_zone.json",
) -> Dict:
    """
    Full ingestion: load and validate both inputs, then return the prior snapshot and
    the incoming records separately for the signal layer.
    """
    history = load_history(history_path)
    incoming = load_incoming_report(report_path)
    prior = get_prior_snapshot(history)

    return {
        "status": "success",
        "history_rows": len(history),
        "incoming_rows": len(incoming),
        "prior_snapshot": prior.to_dict(orient="records"),
        "incoming": incoming.to_dict(orient="records"),
    }


if __name__ == "__main__":
    result = ingestion_pipeline("data/history.csv", "data/incoming/incoming_new_zone.json")
    print("Ingestion successful.")
    print(f"History rows: {result['history_rows']}, incoming rows: {result['incoming_rows']}")
    prior_zones = {r["health_zone"] for r in result["prior_snapshot"]}
    incoming_zones = {r["health_zone"] for r in result["incoming"]}
    print("Prior zones:", sorted(prior_zones))
    print("Incoming zones:", sorted(incoming_zones))
    print("New zones in incoming:", sorted(incoming_zones - prior_zones))
