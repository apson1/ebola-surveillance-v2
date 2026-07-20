"""Registry of configured outbreaks (Phase B).

Adding a new outbreak is a configuration change only: add one `OutbreakProfile` to `REGISTRY`
and point `RELIEFWEB_DISASTER_ID` at its id. `active_outbreak()` resolves the currently active
profile from that env var.

Field consumption (so the profile does not look over-specified for what B1 uses):
  Consumed by B1 (plumbing):
    - disaster_id   : the history partition key + ReliefWeb pin + registry key
    - display_name  : shown in the UI's display-only outbreak header
  Reserved for B2 (extraction generalization — NOT read yet):
    - disease           : templated into the extraction prompt
    - country_iso3      : ReliefWeb fallback query + geographic scope label
    - denied_zone_aliases : replaces the DRC-specific extraction deny-list
    - glide, fallback_query, report_format : currently hardcoded in live_sources; move to profile
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
    # --- reserved for B2 (populated now, consumed later) ---
    disease: str = ""
    country_iso3: str = ""
    denied_zone_aliases: List[str] = field(default_factory=list)
    glide: str = ""
    fallback_query: str = "ebola"
    report_format: str = "Situation Report"


# One configured outbreak today. A second one is purely additive here.
REGISTRY: Dict[int, OutbreakProfile] = {
    52586: OutbreakProfile(
        disaster_id=52586,
        display_name="DRC Ebola 2026",
        disease="Bundibugyo virus disease (Ebola)",
        country_iso3="cod",
        denied_zone_aliases=[
            "drc", "dr congo", "rdc", "congo",
            "democratic republic of the congo", "democratic republic of congo",
            "ituri", "north kivu",
        ],
        glide="EP-2026-000071-COD",
        fallback_query="ebola",
    ),
}

# Used when RELIEFWEB_DISASTER_ID points at an id not in REGISTRY (keeps the app running).
_UNKNOWN = OutbreakProfile(disaster_id=0, display_name="Unconfigured outbreak")


def active_outbreak() -> OutbreakProfile:
    """The currently active outbreak profile, resolved from RELIEFWEB_DISASTER_ID."""
    profile = REGISTRY.get(RELIEFWEB_DISASTER_ID)
    if profile is None:
        logger.warning("no outbreak profile for disaster_id=%s; using a placeholder", RELIEFWEB_DISASTER_ID)
        return OutbreakProfile(disaster_id=RELIEFWEB_DISASTER_ID or 0,
                               display_name=f"Outbreak {RELIEFWEB_DISASTER_ID}")
    return profile
