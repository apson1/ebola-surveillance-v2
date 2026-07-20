"""Phase 2 (step 1 of the two-model check): extract per-health-zone case counts from a
ReliefWeb report body.

Uses google-genai with a strict Pydantic schema, then applies DETERMINISTIC guards that do NOT
trust the model:
- Snippet guard (R2): every record's `snippet` must be a VERBATIM substring of the report
  body, else the record is dropped and logged — a cheap guard against invented quotes.
- Zone guard (R1): every record must name a non-empty health_zone; national/provincial totals
  with no zone are dropped and logged.
- `source_url` and `report_date` are filled from the caller's arguments, never the model.

Never raises. On any LLM/parse failure it returns an empty result with ok=False (and the error
text) and logs report_id + stage, so the caller can distinguish a service failure from a genuine
"no per-zone data" result.
"""
import functools
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from google import genai
from google.genai import types
from pydantic import BaseModel, Field

from src.config import GEMINI_API_KEY, GEMINI_MODEL
from src.outbreaks import profile_for

logger = logging.getLogger(__name__)


@functools.lru_cache(maxsize=None)
def _denied_zones(disaster_id: int) -> frozenset:
    """Names that must NOT appear as a health_zone (country/province aliases) for this outbreak.

    Config-driven, from the outbreak profile's `denied_zone_aliases`, so it is per-outbreak and
    no other outbreak's names can leak in. Cached per disaster_id (profiles are immutable)."""
    return frozenset(a.strip().lower() for a in profile_for(disaster_id).denied_zone_aliases)


class _ExtractedRow(BaseModel):
    date: str = ""
    province: str = ""
    health_zone: str = ""
    suspected_cases: Optional[int] = None
    confirmed_cases: Optional[int] = None
    deaths: Optional[int] = None
    snippet: str = ""


class _ExtractionPayload(BaseModel):
    records: List[_ExtractedRow] = Field(default_factory=list)


@dataclass
class ExtractionResult:
    records: List[Dict] = field(default_factory=list)   # each: 8 contract columns + snippet
    dropped: List[Dict] = field(default_factory=list)   # {record, reason}
    note: str = ""
    ok: bool = True       # False only when the LLM call itself failed (rate limit, network, parse)
    error: str = ""       # the failure text when ok is False; lets the UI distinguish an
    #                       extraction-service failure from a genuine "no per-zone data" result.


# The intro is templated per outbreak; `_EXTRACTION_RULES` is BYTE-IDENTICAL to the pre-B2 prompt
# and is what drives extraction behavior. render_extraction_prompt composes intro + rules with
# nothing injected between them (asserted by the prompt-parity test), so no outbreak's framing can
# reweight the rules in the model's attention.
_EXTRACTION_INTRO = (
    "You are a careful data extractor for a {disease} outbreak surveillance system in "
    "{country_name}.\n"
    "From the REPORT BODY below, extract cumulative case counts that are EXPLICITLY attributed to a\n"
    "specific named health zone (health_zone) with a date.\n"
)

_EXTRACTION_RULES = """
Strict rules:
- Extract ONLY figures tied to a specific health_zone. Do NOT extract national totals (e.g.
  country-wide figures) or provincial totals that are not tied to a specific health_zone.
- Do NOT infer, average, estimate, or sum figures across zones. Report only numbers stated
  as-is in the text.
- For each record, copy the EXACT sentence or phrase from the body that supports the numbers
  into `snippet`. The snippet MUST be a verbatim, character-for-character substring of the
  body. Do not paraphrase, reword, translate, or fix typos.
- If a count (suspected_cases, confirmed_cases, deaths) is not stated for a zone, leave it null.
- If no figure is explicitly attributed to a specific health_zone, return an empty list.

Return JSON matching the schema: a list of records, each with date, province, health_zone,
suspected_cases, confirmed_cases, deaths, and snippet."""


def render_extraction_prompt(profile) -> str:
    """Per-outbreak intro + the invariant rules block, with nothing between them."""
    return _EXTRACTION_INTRO.format(disease=profile.disease, country_name=profile.country_name) + _EXTRACTION_RULES


def _call_extraction_llm(prompt: str, report_body: str) -> _ExtractionPayload:
    """The single extraction LLM call. Patched in hermetic tests."""
    client = genai.Client(api_key=GEMINI_API_KEY)
    resp = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=f"{prompt}\n\nREPORT BODY:\n{report_body}",
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=_ExtractionPayload,
        ),
    )
    if getattr(resp, "parsed", None) is not None:
        return resp.parsed
    return _ExtractionPayload.model_validate_json(resp.text)


def extract_report(report_body: str, report_url: str, report_date: str,
                   disaster_id: int) -> ExtractionResult:
    """Extract per-zone records from a report body. `report_url` is the provenance (source_url)
    and the report_id used in logs; `report_date` is the publication date; `disaster_id` tags the
    outbreak. source_url, report_date and disaster_id are filled from args, never the model.
    Guards and prompt are unchanged (their generalization is B2). Never raises."""
    try:
        prompt = render_extraction_prompt(profile_for(disaster_id))
        payload = _call_extraction_llm(prompt, report_body)
    except Exception as e:  # noqa: BLE001 — any LLM/parse failure degrades to empty
        logger.warning("extraction failed [report_id=%s stage=extract]: %s", report_url, e)
        # ok=False so the UI can say "service unavailable, retry" instead of "no data found".
        return ExtractionResult([], [], f"extraction failed: {e}", ok=False, error=str(e))

    records, dropped = [], []
    for row in payload.records:
        raw = row.model_dump()
        # R2: the snippet must be a verbatim substring of the body.
        if not row.snippet or row.snippet not in report_body:
            logger.warning(
                "dropped record [report_id=%s stage=snippet_guard]: snippet not a verbatim substring",
                report_url,
            )
            dropped.append({"record": raw, "reason": "snippet_not_verbatim"})
            continue
        # R1: no national/provincial totals — a record must name a health_zone.
        if not (row.health_zone or "").strip():
            logger.warning(
                "dropped record [report_id=%s stage=zone_guard]: no health_zone (national/provincial total)",
                report_url,
            )
            dropped.append({"record": raw, "reason": "no_health_zone"})
            continue
        # R1 hardening: a health_zone must not be a country or province (mislabeled total).
        if row.health_zone.strip().lower() in _denied_zones(disaster_id):
            logger.warning(
                "dropped record [report_id=%s stage=zone_guard]: denied zone '%s' (country/province label)",
                report_url, row.health_zone,
            )
            dropped.append({"record": raw, "reason": "denied_zone"})
            continue
        records.append({
            "disaster_id": disaster_id,   # from args, never the model
            "date": row.date,
            "province": row.province,
            "health_zone": row.health_zone,
            "suspected_cases": row.suspected_cases,
            "confirmed_cases": row.confirmed_cases,
            "deaths": row.deaths,
            "source_url": report_url,     # from args, never the model
            "report_date": report_date,   # from args, never the model
            "snippet": row.snippet,
        })

    return ExtractionResult(records, dropped, f"{len(records)} extracted, {len(dropped)} dropped")
