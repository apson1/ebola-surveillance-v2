"""Pure ranking helpers shared by the legacy signal_agent and the ADK signal pipeline.

The id-based permutation guard lives here as the single source of truth. An LLM (whether
called directly or via an ADK LlmAgent) only ever proposes an `order` of flag ids; this
module decides whether that order is trustworthy and, if not, falls back to a deterministic
priority ordering. Numbers never pass through here.
"""
import logging
from typing import Dict, List, Tuple

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# Fallback priority: rising deaths (cfr_shift) and rapid acceleration (surge) are active
# danger; a new zone is early warning; a data gap is lowest. (context.md §7)
PRIORITY = {"cfr_shift": 1, "surge": 2, "new_zone": 3, "stale_or_missing": 4}

FALLBACK_REASONING = (
    "[LLM Guard Fallback] The model output did not return a valid permutation of the flag IDs. "
    "Reverted to deterministic priority ordering: cfr_shift > surge > new_zone > stale_or_missing."
)


class RankingDecision(BaseModel):
    """Structured output schema for the ranking LLM. Shape only — the guard still validates
    that `order` is a clean permutation (output_schema does NOT guarantee that)."""
    order: List[int] = Field(default_factory=list)
    reasoning: str = ""


def fallback_rank_flags(flags: List[Dict]) -> List[Dict]:
    """Deterministic priority ordering. Stable, so flags keep their relative order within
    a detector group."""
    return sorted(flags, key=lambda f: PRIORITY.get(f.get("detector"), 99))


def apply_rank_guard(flags: List[Dict], order) -> Tuple[List[Dict], bool]:
    """The id-based guard. Returns (ranked_flags, used_fallback).

    `order` is trusted only if it is a clean permutation of range(len(flags)) — exactly the
    ids, each once. Anything else (non-ints, duplicates, out-of-range, missing, wrong length)
    routes to the deterministic fallback. This is load-bearing: a structured `output_schema`
    guarantees `order` is a list of ints, never that it is a valid permutation.
    """
    try:
        order_ints = [int(x) for x in order]
    except (ValueError, TypeError):
        order_ints = []

    if sorted(order_ints) == list(range(len(flags))):
        return [flags[idx] for idx in order_ints], False

    logger.warning(
        "LLM Guard failed. Invalid order list: %s. Falling back to deterministic sorting.", order
    )
    return fallback_rank_flags(flags), True
