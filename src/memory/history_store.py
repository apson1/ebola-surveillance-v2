"""Memory / persistence: append validated incoming reports to the durable history store so
each scan can compare against the accumulated past.

Invariants:
- History never contains a null confirmed_cases or deaths. Incoming rows missing either are
  skipped (they were already surfaced by the stale_or_missing detector). suspected_cases may be
  null only on the candidate-promotion path (allow_null_suspected=True); see context.md sec 8.
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

from src.contract import CONTRACT_COLUMNS, COUNT_COLUMNS, IDENTITY_COLUMNS

# Upsert key = the contract identity (now includes disaster_id), so two outbreaks' rows for the
# same zone/date/source stay distinct in the shared file. Re-exported name kept for readers.
DEDUP_KEY = IDENTITY_COLUMNS


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


def _to_int_cols(df: pd.DataFrame) -> pd.DataFrame:
    """Cast count columns to pandas nullable Int64 (tolerates a null suspected_cases)."""
    for c in COUNT_COLUMNS:
        df[c] = pd.to_numeric(df[c], errors="coerce").astype("Int64")
    return df


def append_to_history(incoming_records: List[Dict], history_path: str = "data/history.csv",
                      allow_null_suspected: bool = False) -> int:
    """Append clean incoming records to the history store.

    Returns the NET number of rows added (an upsert that updates an existing key counts as 0
    net rows added).

    By default a row with ANY null count is skipped — history must stay complete. When
    `allow_null_suspected=True` (only the candidate-promotion path passes this), a null
    `suspected_cases` is permitted as long as `confirmed_cases` and `deaths` are present; a null
    in confirmed_cases or deaths is still skipped (it routes through stale_or_missing, never
    into history). All other write paths keep the strict rule. See context.md section 8.
    """
    required = ["confirmed_cases", "deaths"] if allow_null_suspected else COUNT_COLUMNS

    # 1. Drop rows missing a required count.
    clean = []
    for r in incoming_records:
        if any(_is_null(r.get(c)) for c in required):
            continue
        clean.append({c: r.get(c) for c in CONTRACT_COLUMNS})
    if not clean:
        return 0

    new_df = _to_int_cols(pd.DataFrame(clean, columns=CONTRACT_COLUMNS))
    new_df["date"] = new_df["date"].map(_norm_date)
    new_df["report_date"] = new_df["report_date"].map(_norm_date)

    # 2. Merge with existing history.
    if os.path.exists(history_path):
        hist = pd.read_csv(history_path)
        hist["date"] = hist["date"].map(_norm_date)
        hist["report_date"] = hist["report_date"].map(_norm_date)
        hist = _to_int_cols(hist)
        before = len(hist)
        combined = pd.concat([hist, new_df], ignore_index=True)
    else:
        before = 0
        combined = new_df

    # 3. Upsert: keep-last so a later row for the same identity key wins.
    combined = combined.drop_duplicates(subset=DEDUP_KEY, keep="last").reset_index(drop=True)
    combined = _to_int_cols(combined[CONTRACT_COLUMNS])

    # 4. Atomic write.
    _atomic_write_csv(combined, history_path)
    return len(combined) - before
