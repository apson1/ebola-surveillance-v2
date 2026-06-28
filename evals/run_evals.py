"""Phase 9 eval runner: score the detectors against evals/eval_set.json.

Hermetic and fully reproducible — ingestion + run_all_detectors only, NO live LLM and no ADK
Runner. It scores the SET of (detector, health_zone, type) tuples per scenario, so ordering
(ranking) is structurally excluded; this proves detection correctness, which is exactly what
Phase 9 requires. Writes a structured artifact to evals/eval_results.json for the writeup.

Run:  python -m evals.run_evals
"""
import json
import sys

import pandas as pd

from src.ingestion.ingestion import ingestion_pipeline
from src.signal.detectors import run_all_detectors

DEFAULT_EVAL_SET = "evals/eval_set.json"
DEFAULT_HISTORY = "data/history.csv"
DEFAULT_RESULTS = "evals/eval_results.json"


def _flag_tuple(flag):
    return (flag.get("detector"), flag.get("health_zone"), flag.get("type"))


def _expected_tuple(entry):
    return (entry["detector"], entry["health_zone"], entry.get("type"))


def _sort_key(t):
    return (t[0] or "", t[1] or "", t[2] or "")


def _as_dicts(tuples):
    return [
        {"detector": t[0], "health_zone": t[1], "type": t[2]}
        for t in sorted(tuples, key=_sort_key)
    ]


def run_evals(eval_set_path=DEFAULT_EVAL_SET, history_path=DEFAULT_HISTORY,
              results_path=DEFAULT_RESULTS):
    """Run all eval cases. Returns the report dict and (if results_path) writes it as JSON."""
    with open(eval_set_path, encoding="utf-8") as fh:
        eval_set = json.load(fh)

    results = []
    passed = 0
    for entry in eval_set:
        report_path = entry["report_path"]
        ing = ingestion_pipeline(entry.get("history_path", history_path), report_path)
        flags = run_all_detectors(
            pd.DataFrame(ing["incoming"]), pd.DataFrame(ing["prior_snapshot"])
        )

        actual = {_flag_tuple(f) for f in flags}
        expected = {_expected_tuple(e) for e in entry["expected_flags"]}
        ok = actual == expected
        passed += int(ok)

        result = {
            "scenario": entry["scenario"],
            "report_path": report_path,
            "passed": ok,
            "expected": _as_dicts(expected),
            "actual": _as_dicts(actual),
        }
        if not ok:
            result["missing"] = _as_dicts(expected - actual)       # expected but not produced
            result["unexpected"] = _as_dicts(actual - expected)    # produced but not expected
        results.append(result)

    report = {
        "summary": {
            "passed": passed,
            "total": len(eval_set),
            "all_passed": passed == len(eval_set),
        },
        "results": results,
    }
    if results_path:
        with open(results_path, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2)
    return report


def main():
    report = run_evals()
    for r in report["results"]:
        print(f"[{'PASS' if r['passed'] else 'FAIL'}] {r['scenario']}")
        if not r["passed"]:
            print(f"        missing:    {r['missing']}")
            print(f"        unexpected: {r['unexpected']}")
    s = report["summary"]
    print(f"\n{s['passed']}/{s['total']} scenarios passed. Artifact: {DEFAULT_RESULTS}")
    sys.exit(0 if s["all_passed"] else 1)


if __name__ == "__main__":
    main()
