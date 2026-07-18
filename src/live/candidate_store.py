"""Phase 2: the candidate history store — a staging area for validated records that requires
explicit human promotion before anything enters the real history.

`data/candidate_history.csv` is SEPARATE from `data/history.csv` and is NEVER read by the
detectors. The only path from a candidate into history is `promote_candidates(record_ids)`,
which projects the chosen rows to the eight-column contract and calls the existing
`append_to_history` (the Phase 7 memory store). Nothing is ever promoted automatically.
"""
import hashlib
import logging
import os
import tempfile
from datetime import datetime, timezone
from typing import Dict, List

import pandas as pd

from src.memory.history_store import CONTRACT_COLUMNS, append_to_history

logger = logging.getLogger(__name__)

CANDIDATE_PATH = "data/candidate_history.csv"
_IDENTITY = ["date", "province", "health_zone", "source_url"]
COLUMNS = ["candidate_id", "status"] + CONTRACT_COLUMNS + ["snippet", "validated_at"]
# Valid status values: pending (just written), approved / rejected (human decision, Phase 3
# UI), promoted (copied into history). Written rows start as 'pending'.
STATUSES = ("pending", "approved", "promoted", "rejected")


def candidate_id(record: Dict) -> str:
    """Stable id from the identity key, so a human can reference candidates for promotion and
    dedup is by id."""
    key = "|".join(str(record.get(c, "")) for c in _IDENTITY)
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


def _atomic_write(df: pd.DataFrame, path: str) -> None:
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


def _load(path: str) -> pd.DataFrame:
    if os.path.exists(path):
        return pd.read_csv(path)
    return pd.DataFrame(columns=COLUMNS)


def write_candidates(validated_records: List[Dict], path: str = CANDIDATE_PATH) -> int:
    """Append validated records to the candidate store with status='pending', deduped on the
    identity key (keep-last). Returns the net number of new candidate rows added."""
    if not validated_records:
        return 0
    now = datetime.now(timezone.utc).isoformat()
    rows = []
    for r in validated_records:
        row = {c: r.get(c) for c in CONTRACT_COLUMNS}
        row["candidate_id"] = candidate_id(r)
        row["status"] = "pending"
        row["snippet"] = r.get("snippet", "")
        row["validated_at"] = now
        rows.append(row)
    new_df = pd.DataFrame(rows, columns=COLUMNS)

    existing = _load(path)
    before = len(existing)
    combined = pd.concat([existing, new_df], ignore_index=True)
    combined = combined.drop_duplicates(subset=["candidate_id"], keep="last").reset_index(drop=True)
    _atomic_write(combined[COLUMNS], path)
    return len(combined) - before


def promote_candidates(record_ids: List[str], path: str = CANDIDATE_PATH,
                       history_path: str = "data/history.csv") -> int:
    """Promote the given candidate_ids into history via append_to_history, and mark those rows
    status='promoted'. Returns the net rows added to history. The ONLY candidate -> history
    path; never called automatically."""
    df = _load(path)
    if df.empty or not record_ids:
        return 0
    selected = df[df["candidate_id"].isin(record_ids)]
    if selected.empty:
        return 0

    records = selected[CONTRACT_COLUMNS].to_dict(orient="records")
    added = append_to_history(records, history_path)

    df.loc[df["candidate_id"].isin(record_ids), "status"] = "promoted"
    _atomic_write(df[COLUMNS], path)
    logger.info("promoted %d candidate(s); %d net row(s) added to history", len(selected), added)
    return added


def reset_candidates(path: str = CANDIDATE_PATH) -> None:
    """Clear the candidate store. Does NOT touch history.csv."""
    if os.path.exists(path):
        os.remove(path)
