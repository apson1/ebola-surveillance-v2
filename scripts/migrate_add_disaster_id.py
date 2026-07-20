"""One-time migration: add the `disaster_id` column to an existing history.csv (Phase B).

Every current row is DRC Ebola 2026, so all get disaster_id = 52586 (the ReliefWeb disaster id,
matching RELIEFWEB_DISASTER_ID). Backs up to `<history>.bak` first, inserts disaster_id as the
first column, writes atomically. IDEMPOTENT: a no-op if the column already exists. This is a
one-time script — it is NEVER called from a load path.

    python -m scripts.migrate_add_disaster_id
"""
import os
import sys
import shutil
import tempfile

import pandas as pd

DEFAULT_HISTORY = "data/history.csv"
DRC_EBOLA_2026 = 52586


def _atomic_write(df: pd.DataFrame, path: str) -> None:
    directory = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
    os.close(fd)
    try:
        df.to_csv(tmp, index=False)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def migrate(history_path: str = DEFAULT_HISTORY, disaster_id: int = DRC_EBOLA_2026,
            backup: bool = True) -> str:
    """Add disaster_id as column 1 (value `disaster_id`) to every row. Idempotent."""
    df = pd.read_csv(history_path)
    if "disaster_id" in df.columns:
        return f"already migrated ({len(df)} rows); no change"
    if backup:
        shutil.copy2(history_path, history_path + ".bak")
    df.insert(0, "disaster_id", disaster_id)
    _atomic_write(df, history_path)
    return f"migrated {len(df)} rows -> disaster_id={disaster_id} (backup: {history_path}.bak)"


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_HISTORY
    print(migrate(path))
