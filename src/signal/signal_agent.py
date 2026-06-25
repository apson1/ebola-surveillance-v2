"""
Signal Agent for the Ebola surveillance system.
Integrates rule-based detectors, sends them to Gemini for ranking,
and applies an LLM Guard to ensure flag integrity.
"""

import json
import logging
from typing import Dict, List
import pandas as pd
from google import genai

from src.config import GEMINI_API_KEY, GEMINI_MODEL
from src.signal.detectors import run_all_detectors
from src.signal.ranking import apply_rank_guard, fallback_rank_flags, FALLBACK_REASONING

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def clean_and_parse_json(text: str) -> Dict:
    """Clean and parse JSON from the model's response text."""
    text = text.strip()
    if text.startswith("```json"):
        text = text[7:]
    elif text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    text = text.strip()
    return json.loads(text)


def run_signal_agent(prior_snapshot: List[Dict], incoming: List[Dict]) -> Dict:
    """
    Signal Agent entry point:
    1. Converts snapshots to DataFrames.
    2. Runs all detectors.
    3. Passes flags labeled with IDs to LLM for ranking.
    4. Applies LLM Guard verifying that the returned order is a clean permutation.
    """
    # Convert incoming dicts back to pandas DataFrames for detectors
    prior_df = pd.DataFrame(prior_snapshot)
    incoming_df = pd.DataFrame(incoming)
    
    # Run detectors
    flags = run_all_detectors(incoming_df, prior_df)
    
    if not flags:
        return {
            "status": "success",
            "flags": [],
            "reasoning": "No anomaly signals or data quality issues were flagged in this scan."
        }
        
    # Assign each flag a stable integer ID. Send only non-numeric routing fields to the
    # model (id, detector type, zone name) so the LLM cannot see, return, or alter numbers.
    flags_for_llm = [
        {"id": i, "detector": flag["detector"], "health_zone": flag.get("health_zone")}
        for i, flag in enumerate(flags)
    ]
        
    # Configure and call the Gemini API using the new google-genai SDK
    client = genai.Client(api_key=GEMINI_API_KEY)
    
    prompt = f"""
You are an expert epidemiological risk assessor.
Below is a list of surveillance flags (signals) detected from the current Ebola situation reports in the Democratic Republic of the Congo.
Each flag is assigned a stable integer "id".

Input Flags:
{json.dumps(flags_for_llm, indent=2)}

Your task is to rank these flags in order of urgency and severity.
Guidelines:
- A sharp rise in Case Fatality Ratio (CFR) or a high surge in confirmed cases is extremely urgent (active danger).
- A new health zone with few cases is urgent but acts as an early warning.
- A missing report or a data quality gap is less urgent, but still important.

You MUST return a JSON object with exactly two keys:
1. "order": A list of the flag "id" integers, ordered from highest urgency to lowest urgency. This list MUST be a clean permutation of the input flag IDs (each ID must appear exactly once).
2. "reasoning": A brief, professional explanation of the ranking, referring to zones by name only. Do not state or invent any numeric values.

Response format:
Return ONLY a valid JSON object. Do not wrap it in markdown formatting or anything else. Do not echo back the full flag objects.
"""
    
    try:
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
        )
        result = clean_and_parse_json(response.text)
        order = result.get("order", [])
        reasoning = result.get("reasoning", "")

        # The id-based guard is the single source of truth (src.signal.ranking).
        ranked_flags, used_fallback = apply_rank_guard(flags, order)
        if used_fallback:
            return {
                "status": "success",
                "flags": ranked_flags,
                "reasoning": FALLBACK_REASONING,
            }
        logger.info("LLM Guard passed successfully.")
        return {
            "status": "success",
            "flags": ranked_flags,
            "reasoning": reasoning,
        }

    except Exception as e:
        logger.error(f"Error calling LLM or parsing response: {e}. Falling back.")
        fallback_flags = fallback_rank_flags(flags)
        return {
            "status": "success",
            "flags": fallback_flags,
            "reasoning": (
                f"[LLM Error Fallback] Failed to call or parse LLM response ({e}). "
                "Reverted to deterministic priority ordering: cfr_shift > surge > new_zone > stale_or_missing."
            )
        }
