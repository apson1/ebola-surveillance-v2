"""Pure UI-support logic for the Phase 3 candidate review (no Streamlit, so it is testable).

Merges the two "not approvable" buckets into one rejected list with human-readable reasons:
- records the deterministic extraction guards dropped (`snippet_not_verbatim`, `no_health_zone`,
  `denied_zone`) — from ExtractionResult.dropped, and
- records the independent second-model validator rejected — from ValidationResult.rejected.

Validated records become approvable cards, each with a stable candidate_id for promotion.
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from src.live.candidate_store import candidate_id

# Human-readable reasons for the UI. The invented-quote catch must be legible on the card.
_REASON_TEXT = {
    "snippet_not_verbatim": "Snippet is not a verbatim quote from the report body — possible "
                            "invented quote (dropped by the extraction guard).",
    "no_health_zone": "No health zone named — reads as a national or provincial total "
                      "(dropped by the extraction guard).",
    "denied_zone": "health_zone is a country or province name, not a health zone "
                   "(dropped by the extraction guard).",
}


def human_reason(reason: str) -> str:
    return _REASON_TEXT.get(reason, reason or "rejected")


@dataclass
class ReviewCard:
    record: Dict                 # extracted record (contract fields + snippet; may lack source_url if dropped)
    approvable: bool
    status: str                  # "validated" | "rejected"
    reason: str = ""             # human-readable, when not approvable
    candidate_id: str = ""       # set for approvable cards (to pass to promote_candidates)


@dataclass
class ReviewModel:
    approvable: List[ReviewCard] = field(default_factory=list)
    rejected: List[ReviewCard] = field(default_factory=list)

    @property
    def approvable_count(self) -> int:
        return len(self.approvable)

    @property
    def rejected_count(self) -> int:
        return len(self.rejected)

    @property
    def is_empty(self) -> bool:
        return not self.approvable and not self.rejected


def build_review(extraction, validation: Optional[object]) -> ReviewModel:
    """extraction: ExtractionResult; validation: ValidationResult or None (if extraction empty)."""
    model = ReviewModel()

    validated = validation.validated if validation is not None else []
    for rec in validated:
        model.approvable.append(ReviewCard(
            record=rec, approvable=True, status="validated", candidate_id=candidate_id(rec),
        ))

    if validation is not None:
        for rej in validation.rejected:
            model.rejected.append(ReviewCard(
                record=rej["record"], approvable=False, status="rejected",
                reason=human_reason(rej.get("reason", "")),
            ))

    for dropped in extraction.dropped:
        model.rejected.append(ReviewCard(
            record=dropped["record"], approvable=False, status="rejected",
            reason=human_reason(dropped.get("reason", "")),
        ))

    return model
