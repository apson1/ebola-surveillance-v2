"""Memory / persistence: append validated incoming reports to the durable history store so
each scan can compare against the accumulated past.

Invariants:
- History never contains a null count (data contract). Incoming rows with any null count are
  skipped (they were already surfaced by the stale_or_missing detector).
- Upsert on the identity key (date, province, health_zone, source_url), keep-last, so a
  re-scanned or corrected row updates in place rather than duplicating.
- Atomic write (temp file + os.replace) so a crash mid-write cannot corrupt the store.

Persistence is opt-in at the orchestrator (`run_scan(..., persist=True)`); by default scans
do not mutate history, keeping demos and tests reproducible.
"""
import os
import tempfile
from typing import Dict, List

import pandas as pd

CONTRACT_COLUMNS = [
    "date", "province", "health_zone",
    "suspected_cases", "confirmed_cases", "deaths",
    "source_url", "report_date",
]
COUNT_COLUMNS = ["suspected_cases", "confirmed_cases", "deaths"]
DEDUP_KEY = ["date", "province", "health_zone", "source_url"]


def _norm_date(value) -> str:
    """Normalize any date representation to an ISO date string (YYYY-MM-DD)."""
    return pd.to_datetime(value).strftime("%Y-%m-%d")


def _is_null(v) -> bool:
    if v is None:
        return True
    try:
        return bool(pd.isna(v))
    except (TypeError, ValueError):
        return False


def _atomic_write_csv(df: pd.DataFrame, path: str) -> None:
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
    os.close(fd)
    try:
        df.to_csv(tmp, index=False)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def append_to_history(incoming_records: List[Dict], history_path: str = "data/history.csv") -> int:
    """Append clean incoming records to the history store.

    Returns the NET number of rows added (an upsert that updates an existing key counts as 0
    net rows added). Rows with any null count are skipped before counting.
    """
    # 1. Drop rows with any null count — history must stay complete.
    clean = []
    for r in incoming_records:
        if any(_is_null(r.get(c)) for c in COUNT_COLUMNS):
            continue
        clean.append({c: r.get(c) for c in CONTRACT_COLUMNS})
    if not clean:
        return 0

    new_df = pd.DataFrame(clean, columns=CONTRACT_COLUMNS)
    new_df["date"] = new_df["date"].map(_norm_date)
    new_df["report_date"] = new_df["report_date"].map(_norm_date)
    for c in COUNT_COLUMNS:
        new_df[c] = new_df[c].astype(int)

    # 2. Merge with existing history.
    if os.path.exists(history_path):
        hist = pd.read_csv(history_path)
        hist["date"] = hist["date"].map(_norm_date)
        hist["report_date"] = hist["report_date"].map(_norm_date)
        before = len(hist)
        combined = pd.concat([hist, new_df], ignore_index=True)
    else:
        before = 0
        combined = new_df

    # 3. Upsert: keep-last so a later row for the same identity key wins.
    combined = combined.drop_duplicates(subset=DEDUP_KEY, keep="last").reset_index(drop=True)
    combined = combined[CONTRACT_COLUMNS]
    for c in COUNT_COLUMNS:
        combined[c] = combined[c].astype(int)

    # 4. Atomic write.
    _atomic_write_csv(combined, history_path)
    return len(combined) - before
