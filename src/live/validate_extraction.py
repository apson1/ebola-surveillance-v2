"""Phase 2 (step 2 of the two-model check): independent validation of extracted records.

A DIFFERENT prompt from extraction, framed as a strict fact-checker. It runs as ONE batched
call that returns a per-record verdict, and — crucially — requires the model to quote the exact
supporting phrase for EACH record, so it reasons through them independently rather than rubber-
stamping the batch. A record is validated only on a strict PASS; anything else (FAIL, a hedge,
or a missing verdict) is rejected and logged.

Fail-closed: on any LLM/parse failure, ALL records are rejected (nothing is trusted). Never
raises. Failure logs carry report_id + stage.
"""
import json
import logging
from dataclasses import dataclass, field
from typing import Dict, List

from google import genai
from google.genai import types
from pydantic import BaseModel, Field

from src.config import GEMINI_API_KEY, GEMINI_MODEL

logger = logging.getLogger(__name__)


class _Verdict(BaseModel):
    index: int
    verdict: str = "FAIL"          # "PASS" or "FAIL"
    supporting_phrase: str = ""    # exact phrase from the body supporting this number
    reason: str = ""


class _ValidationPayload(BaseModel):
    verdicts: List[_Verdict] = Field(default_factory=list)


@dataclass
class ValidationResult:
    validated: List[Dict] = field(default_factory=list)
    rejected: List[Dict] = field(default_factory=list)   # {record, reason}
    note: str = ""


_VALIDATION_PROMPT = """You are a strict fact-checker for an Ebola surveillance system. You are
given the REPORT BODY and a numbered list of CANDIDATE RECORDS extracted from it. Each record
claims cumulative counts for a specific health_zone on a date, together with a snippet.

For EACH record independently, decide whether the REPORT BODY EXPLICITLY and UNAMBIGUOUSLY
supports the exact numbers for that exact health_zone and date.

Rules:
- Judge each record on its own merits. For each record, quote the exact supporting phrase from
  the body into `supporting_phrase`.
- Answer PASS only if the body explicitly states these numbers for this zone. If the number
  requires inference or aggregation, or the supporting text does not clearly state it for this
  zone, or the snippet does not actually support the number, answer FAIL.
- Return exactly one verdict per record, keyed by the record's `index`.

Return JSON with a `verdicts` array; each item has index, verdict ("PASS" or "FAIL"),
supporting_phrase, and reason."""


def _records_block(records: List[Dict]) -> str:
    lines = []
    for i, r in enumerate(records):
        lines.append(json.dumps({
            "index": i,
            "health_zone": r.get("health_zone"),
            "date": r.get("date"),
            "suspected_cases": r.get("suspected_cases"),
            "confirmed_cases": r.get("confirmed_cases"),
            "deaths": r.get("deaths"),
            "snippet": r.get("snippet"),
        }))
    return "\n".join(lines)


def _call_validation_llm(records: List[Dict], report_body: str) -> _ValidationPayload:
    """The single batched validation LLM call. Patched in hermetic tests."""
    client = genai.Client(api_key=GEMINI_API_KEY)
    contents = (
        f"{_VALIDATION_PROMPT}\n\nREPORT BODY:\n{report_body}\n\n"
        f"CANDIDATE RECORDS (one JSON per line):\n{_records_block(records)}"
    )
    resp = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=contents,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=_ValidationPayload,
        ),
    )
    if getattr(resp, "parsed", None) is not None:
        return resp.parsed
    return _ValidationPayload.model_validate_json(resp.text)


def validate_extraction(records: List[Dict], report_body: str) -> ValidationResult:
    """Validate extracted records against the body. Only strict PASS survives. Never raises."""
    if not records:
        return ValidationResult([], [], "no records to validate")

    report_id = records[0].get("source_url")
    try:
        payload = _call_validation_llm(records, report_body)
    except Exception as e:  # noqa: BLE001 — fail closed: reject everything
        logger.warning(
            "validation failed [report_id=%s stage=validate]: %s — failing closed (all rejected)",
            report_id, e,
        )
        return ValidationResult(
            [], [{"record": r, "reason": f"validation_error: {e}"} for r in records],
            "validation failed; all records rejected",
        )

    by_index = {v.index: v for v in payload.verdicts}
    validated, rejected = [], []
    for i, rec in enumerate(records):
        v = by_index.get(i)
        if v is not None and v.verdict.strip().upper() == "PASS":
            validated.append(rec)
        else:
            reason = v.reason if v is not None else "no verdict returned"
            logger.warning(
                "record rejected [report_id=%s stage=validation index=%d]: %s", report_id, i, reason
            )
            rejected.append({"record": rec, "reason": reason})

    return ValidationResult(validated, rejected, f"{len(validated)} validated, {len(rejected)} rejected")
