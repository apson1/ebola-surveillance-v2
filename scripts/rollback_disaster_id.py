"""Roll back the disaster_id migration by restoring history.csv from its `.bak` (Phase B).

Two-way migration insurance. REFUSES to run if the current history contains any disaster_id
other than the original DRC value (52586): once a second outbreak's rows exist in the shared
file, restoring the pre-migration backup would silently lose that outbreak's data.

    python -m scripts.rollback_disaster_id
"""
import os
import sys
import shutil

import pandas as pd

DEFAULT_HISTORY = "data/history.csv"
DRC_EBOLA_2026 = 52586


class RollbackRefused(RuntimeError):
    """Raised when rolling back would lose another outbreak's data."""


def rollback(history_path: str = DEFAULT_HISTORY, expected_id: int = DRC_EBOLA_2026) -> str:
    """Restore history_path from history_path + '.bak'. Refuses if a foreign disaster_id exists."""
    df = pd.read_csv(history_path)
    if "disaster_id" in df.columns:
        foreign = sorted(set(int(v) for v in df["disaster_id"].dropna().unique()) - {expected_id})
        if foreign:
            raise RollbackRefused(
                f"refusing to roll back: history contains disaster_id(s) {foreign} besides "
                f"{expected_id}; rolling back would lose that outbreak's data"
            )
    bak = history_path + ".bak"
    if not os.path.exists(bak):
        raise FileNotFoundError(f"no backup at {bak}; cannot roll back")
    shutil.copy2(bak, history_path)
    return f"rolled back {history_path} from {bak}"


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_HISTORY
    try:
        print(rollback(path))
    except (RollbackRefused, FileNotFoundError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
