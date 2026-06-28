"""Hermetic tests for the ADK signal pipeline's ids-only invariant (plan A2).

No LLM is involved: DetectionAgent is a non-LLM BaseAgent, so we run it in isolation and
inspect the state it writes. The RankingAgent instruction is checked as a static string.
"""
import asyncio
import json
import unittest
from unittest.mock import patch

from google.adk.runners import InMemoryRunner
from google.genai import types

from src.ingestion.ingestion import ingestion_pipeline
from src.signal.signal_pipeline import (
    DetectionAgent,
    RANKING_INSTRUCTION,
    build_ranking_agent,
    run_signal_pipeline_async,
)

# Any numeric field a detector may attach to a full flag. None of these may appear in the
# ids-only projection handed to the LLM.
COUNT_KEYS = {
    "suspected_cases", "confirmed_cases", "deaths",
    "confirmed_prior", "confirmed_incoming", "daily_new", "pct_growth",
    "cfr_prior", "cfr_incoming", "cfr_diff", "deaths_prior", "deaths_incoming",
    "confirmed_cases_prior",
}


def _run_detection(prior, incoming):
    async def go():
        runner = InMemoryRunner(agent=DetectionAgent(name="detection_agent"), app_name="t")
        session = await runner.session_service.create_session(
            app_name="t", user_id="u", state={"prior_snapshot": prior, "incoming": incoming}
        )
        content = types.Content(role="user", parts=[types.Part(text="x")])
        async for _ in runner.run_async(user_id="u", session_id=session.id, new_message=content):
            pass
        final = await runner.session_service.get_session(app_name="t", user_id="u", session_id=session.id)
        return final.state

    return asyncio.run(go())


class TestSignalPipelineProjection(unittest.TestCase):
    def setUp(self):
        ing = ingestion_pipeline("data/history.csv", "data/incoming/incoming_multi_signal.json")
        self.state = _run_detection(ing["prior_snapshot"], ing["incoming"])

    def test_flags_for_llm_json_is_ids_only(self):
        items = json.loads(self.state["flags_for_llm_json"])
        self.assertEqual(len(items), 4)
        for item in items:
            self.assertEqual(set(item.keys()), {"id", "detector", "health_zone"})
            for k in item:
                self.assertNotIn(k, COUNT_KEYS)

    def test_full_flags_do_contain_numbers(self):
        # Sanity: the projection actually strips something — full flags carry count fields.
        full = self.state["flags"]
        self.assertTrue(
            any(any(k in COUNT_KEYS for k in f) for f in full),
            "expected at least one full flag to carry a count field",
        )

    def test_ranking_instruction_references_projection_not_full_flags(self):
        instr = RANKING_INSTRUCTION
        self.assertIn("{flags_for_llm_json}", instr)
        # The numbers-bearing state key must NOT be injected. Check the template token
        # '{flags}', not the substring 'flags' (which occurs inside flags_for_llm_json).
        self.assertNotIn("{flags}", instr)
        self.assertEqual(build_ranking_agent().instruction, instr)


class TestSignalPipelineResilience(unittest.TestCase):
    @patch("src.signal.signal_pipeline.InMemoryRunner", side_effect=RuntimeError("LLM down"))
    def test_ranking_failure_degrades_to_deterministic(self, _mock_runner):
        """When the ranking model/runner errors, the scan still completes via deterministic
        priority ordering instead of crashing (no live LLM in this test)."""
        ing = ingestion_pipeline("data/history.csv", "data/incoming/incoming_multi_signal.json")
        result = asyncio.run(run_signal_pipeline_async(ing["prior_snapshot"], ing["incoming"]))
        self.assertEqual(result["status"], "success")
        self.assertTrue(result["used_fallback"])
        self.assertEqual(len(result["flags"]), 4)
        self.assertEqual(result["flags"][0]["detector"], "cfr_shift")  # fallback priority top


if __name__ == "__main__":
    unittest.main()
