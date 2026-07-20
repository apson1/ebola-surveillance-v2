"""Registry of configured outbreaks (Phase B).

Adding a new outbreak is a configuration change only: add one `OutbreakProfile` to `REGISTRY`
and point `RELIEFWEB_DISASTER_ID` at its id. `active_outbreak()` resolves the currently active
profile from that env var; `profile_for(disaster_id)` resolves a specific outbreak's profile.

Field consumption:
  Consumed by B1 (plumbing):
    - disaster_id   : the history partition key + ReliefWeb pin + registry key
    - display_name  : shown in the UI's display-only outbreak header
  Consumed by B2 (extraction generalization):
    - disease           : templated into the extraction + validation prompts
    - country_name      : friendly country, templated into the prompts
    - denied_zone_aliases : the config-driven extraction deny-list (per outbreak)
  Reserved (still hardcoded in live_sources; move to profile later):
    - country_iso3, glide, fallback_query, report_format
"""
import logging
from dataclasses import dataclass, field
from typing import Dict, List

from src.config import RELIEFWEB_DISASTER_ID

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OutbreakProfile:
    # --- consumed by B1 ---
    disaster_id: int
    display_name: str
    # --- consumed by B2 (extraction). Neutral defaults keep a placeholder prompt grammatical. ---
    disease: str = "the disease under surveillance"
    country_name: str = "the affected area"
    denied_zone_aliases: List[str] = field(default_factory=list)
    # --- reserved (live_sources, still hardcoded) ---
    country_iso3: str = ""
    glide: str = ""
    fallback_query: str = "ebola"
    report_format: str = "Situation Report"


# One configured outbreak today. A second one is purely additive here.
REGISTRY: Dict[int, OutbreakProfile] = {
    52586: OutbreakProfile(
        disaster_id=52586,
        display_name="DRC Ebola 2026",
        disease="Bundibugyo virus disease (Ebola)",
        country_name="the Democratic Republic of the Congo",
        denied_zone_aliases=[
            "drc", "dr congo", "rdc", "congo",
            "democratic republic of the congo", "democratic republic of congo",
            "ituri", "north kivu",
        ],
        country_iso3="cod",
        glide="EP-2026-000071-COD",
        fallback_query="ebola",
    ),
}


def profile_for(disaster_id: int) -> OutbreakProfile:
    """The profile for a specific outbreak (the report's disaster_id), or a neutral placeholder
    (empty deny-list, grammatical prompt fallbacks) if the id is not configured — never crashes."""
    profile = REGISTRY.get(disaster_id)
    if profile is None:
        logger.warning("no outbreak profile for disaster_id=%s; using a placeholder", disaster_id)
        return OutbreakProfile(disaster_id=disaster_id or 0, display_name=f"Outbreak {disaster_id}")
    return profile


def active_outbreak() -> OutbreakProfile:
    """The currently active outbreak profile, resolved from RELIEFWEB_DISASTER_ID."""
    return profile_for(RELIEFWEB_DISASTER_ID)
