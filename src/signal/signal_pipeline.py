"""ADK signal pipeline: DetectionAgent -> RankingAgent -> GuardAgent.

Division of labor (hard rule 5, preserved):
- DetectionAgent runs the four rule-based detectors deterministically (no LLM) and writes
  the full flags plus an ids-only projection (`flags_for_llm_json`: id, detector, health_zone
  only — never any count) to session state.
- RankingAgent is the ONLY LLM. It sees only the ids-only projection and returns an `order`
  of ids via a structured schema. It never sees, returns, or alters numbers.
- GuardAgent applies the id-based permutation guard (src.signal.ranking) and writes the final
  ranked flags. output_schema guarantees shape only, so the guard remains load-bearing.
"""
import json
import logging
from typing import AsyncGenerator, Dict, List

import pandas as pd
from google.adk.agents import BaseAgent, LlmAgent, SequentialAgent
from google.adk.events import Event, EventActions
from google.adk.runners import InMemoryRunner
from google.genai import types
from pydantic import BaseModel

from src.config import GEMINI_MODEL
from src.signal.detectors import run_all_detectors
from src.signal.ranking import RankingDecision, apply_rank_guard, FALLBACK_REASONING

logger = logging.getLogger(__name__)

APP_NAME = "ebola_signal"

# Single brace placeholder ({flags_for_llm_json}); no bare {flags} token (numbers-bearing key).
RANKING_INSTRUCTION = (
    "You are an expert epidemiological risk assessor for Ebola surveillance in the DRC.\n"
    "You are given a list of detected surveillance flags. Each flag has a stable integer 'id', "
    "a 'detector' type, and a 'health_zone'. You are given NO case counts and NO numbers.\n\n"
    "Flags:\n{flags_for_llm_json}\n\n"
    "Rank the flags from highest to lowest urgency. Guidance: a rising case fatality ratio "
    "(cfr_shift) or a confirmed-case surge (surge) is active danger and most urgent; a newly "
    "affected zone (new_zone) is an early warning; a reporting gap or null field "
    "(stale_or_missing) is lowest priority but still matters.\n\n"
    "Return 'order': the ids from highest to lowest urgency, a permutation of the input ids "
    "with each id appearing exactly once. Return 'reasoning': a brief explanation that refers "
    "to zones by name only and states no numeric values."
)


class DetectionAgent(BaseAgent):
    """Deterministic detection. Reads prior_snapshot/incoming from state, runs the four
    detectors, writes `flags` (full) and `flags_for_llm_json` (ids-only) to state."""

    async def _run_async_impl(self, ctx) -> AsyncGenerator[Event, None]:
        state = ctx.session.state
        prior_df = pd.DataFrame(state.get("prior_snapshot", []))
        incoming_df = pd.DataFrame(state.get("incoming", []))

        flags = run_all_detectors(incoming_df, prior_df)
        for i, flag in enumerate(flags):
            flag["id"] = i

        flags_for_llm = [
            {"id": f["id"], "detector": f["detector"], "health_zone": f.get("health_zone")}
            for f in flags
        ]

        yield Event(
            author=self.name,
            actions=EventActions(state_delta={
                "flags": flags,
                "flags_for_llm_json": json.dumps(flags_for_llm, indent=2),
            }),
        )


class GuardAgent(BaseAgent):
    """Applies the id-based permutation guard to the LLM's proposed order and writes the
    final ranked flags + reasoning to state."""

    async def _run_async_impl(self, ctx) -> AsyncGenerator[Event, None]:
        state = ctx.session.state
        flags: List[Dict] = state.get("flags", [])

        ranking = state.get("ranking") or {}
        if isinstance(ranking, str):
            try:
                ranking = json.loads(ranking)
            except (ValueError, TypeError):
                ranking = {}
        if isinstance(ranking, BaseModel):
            order, llm_reasoning = ranking.order, ranking.reasoning
        elif isinstance(ranking, dict):
            order, llm_reasoning = ranking.get("order", []), ranking.get("reasoning", "")
        else:
            order, llm_reasoning = [], ""

        ranked_flags, used_fallback = apply_rank_guard(flags, order)
        reasoning = FALLBACK_REASONING if used_fallback else llm_reasoning

        yield Event(
            author=self.name,
            actions=EventActions(state_delta={
                "ranked_flags": ranked_flags,
                "reasoning": reasoning,
                "used_fallback": used_fallback,
            }),
        )


def build_ranking_agent() -> LlmAgent:
    """A fresh RankingAgent (LlmAgent). output_schema disables tools, as intended."""
    return LlmAgent(
        name="ranking_agent",
        model=GEMINI_MODEL,
        instruction=RANKING_INSTRUCTION,
        output_schema=RankingDecision,
        output_key="ranking",
    )


def build_signal_pipeline() -> SequentialAgent:
    """Fresh pipeline instance (ADK agents may only have one parent, so build per run)."""
    return SequentialAgent(
        name="signal_pipeline",
        sub_agents=[
            DetectionAgent(name="detection_agent"),
            build_ranking_agent(),
            GuardAgent(name="guard_agent"),
        ],
    )


async def run_signal_pipeline_async(prior_snapshot: List[Dict], incoming: List[Dict]) -> Dict:
    """Drive the signal pipeline to completion and return {status, flags, reasoning,
    used_fallback}. Mirrors the legacy run_signal_agent return shape (plus used_fallback)."""
    runner = InMemoryRunner(agent=build_signal_pipeline(), app_name=APP_NAME)
    session = await runner.session_service.create_session(
        app_name=APP_NAME,
        user_id="signal_user",
        state={"prior_snapshot": prior_snapshot, "incoming": incoming},
    )
    content = types.Content(role="user", parts=[types.Part(text="run scan")])
    async for _ in runner.run_async(
        user_id="signal_user", session_id=session.id, new_message=content
    ):
        pass

    final = await runner.session_service.get_session(
        app_name=APP_NAME, user_id="signal_user", session_id=session.id
    )
    st = final.state
    return {
        "status": "success",
        "flags": st.get("ranked_flags", []),
        "reasoning": st.get("reasoning", ""),
        "used_fallback": st.get("used_fallback"),
    }
