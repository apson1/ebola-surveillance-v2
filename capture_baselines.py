"""Capture Option-A regression baselines: one file per scenario with the detector
set, the ranked detector order, and the full alert text. Ranking order is
LLM-influenced and may vary; the detector SET and per-flag rendered content are the
deterministic regression signal.
"""
import os
from src.orchestrator import run_scan

HISTORY = "data/history.csv"
SCENARIOS = [
    ("new_zone", "data/incoming/incoming_new_zone.json"),
    ("spike", "data/incoming/incoming_spike.json"),
    ("data_gap", "data/incoming/incoming_data_gap.json"),
    ("cfr_shift", "data/incoming/incoming_cfr_shift.json"),
    ("multi_signal", "data/incoming/incoming_multi_signal.json"),
]
OUT = "baselines"
os.makedirs(OUT, exist_ok=True)

summary = []
for name, path in SCENARIOS:
    result = run_scan(path, HISTORY)
    flags = result["flags"]
    detectors_ranked = [f.get("detector") for f in flags]
    detector_set = sorted(set(detectors_ranked))
    top = detectors_ranked[0] if detectors_ranked else "(none)"
    summary.append(f"{name:14s} top={top:18s} set={detector_set}")

    with open(os.path.join(OUT, f"{name}.txt"), "w", encoding="utf-8") as fh:
        fh.write(f"# Baseline (Option A) — scenario: {name}\n")
        fh.write(f"# source: {path}\n")
        fh.write(f"status: {result['status']}\n")
        fh.write(f"detector_set (deterministic): {detector_set}\n")
        fh.write(f"ranked_order (LLM-influenced): {detectors_ranked}\n")
        fh.write(f"top_signal: {top}\n")
        fh.write("\n--- ALERT TEXT ---\n")
        fh.write(result["alert"])
        fh.write("\n")

with open(os.path.join(OUT, "SUMMARY.txt"), "w", encoding="utf-8") as fh:
    fh.write("Option-A baselines (captured pre-ADK refactor)\n")
    fh.write("=" * 50 + "\n")
    fh.write("\n".join(summary) + "\n")

print("\n".join(summary))
print(f"\nWrote baselines to ./{OUT}/")
