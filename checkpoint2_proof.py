"""Checkpoint 2: prove the ADK signal_pipeline returns a valid permutation on the
multi_signal scenario. Uses the plain ingestion fallback (MCP wiring is checkpoint 4)."""
import asyncio

from src.ingestion.ingestion import ingestion_pipeline
from src.signal.signal_pipeline import run_signal_pipeline_async


async def main():
    ing = ingestion_pipeline("data/history.csv", "data/incoming/incoming_multi_signal.json")
    prior, incoming = ing["prior_snapshot"], ing["incoming"]

    result = await run_signal_pipeline_async(prior, incoming)
    flags = result["flags"]
    ids = [f.get("id") for f in flags]
    detectors_ranked = [f.get("detector") for f in flags]

    print("status        :", result["status"])
    print("used_fallback :", result["used_fallback"])
    print("n flags       :", len(flags))
    print("ranked ids    :", ids)
    print("ranked dets   :", detectors_ranked)
    print("reasoning     :", (result["reasoning"] or "")[:160])

    # Valid-permutation assertions (the checkpoint-2 gate):
    assert len(flags) == 4, f"expected 4 flags, got {len(flags)}"
    assert sorted(ids) == [0, 1, 2, 3], f"ids are not a clean permutation: {ids}"
    assert sorted(detectors_ranked) == ["cfr_shift", "new_zone", "stale_or_missing", "surge"], \
        f"detector set changed: {sorted(detectors_ranked)}"
    print("\nCHECKPOINT 2: PASS — signal_pipeline returned a valid permutation of 4 flags.")


if __name__ == "__main__":
    asyncio.run(main())
