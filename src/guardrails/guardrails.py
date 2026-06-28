"""Guardrail layer: deterministic validation that runs after draft_alert, before any output
leaves. It is the safety net that backs the hard rules:

- Every number in the prose must trace to a sourced ranked flag (rule 3).
- No fabricated zones (rule 5: the LLM never invents zones).
- No clinical / treatment / forecasting language (rule 4) — blocked + flagged by default,
  configurable to strip.
- The escalation line is always present (rule 1).

Fail-closed: on an integrity failure (unsourced number or fabricated zone, or banned
language in block mode) the prose is WITHHELD, but the underlying sourced signals are still
surfaced so withholding the brief never discards the signal.

URLs are redacted up front so neither the number scan nor the banned-word scan ever inspects
a source_url (digits or banned substrings inside a URL must not cause false positives).
"""
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from src.config import BANNED_PATTERNS, GUARDRAIL_BANNED_MODE, GUARDRAIL_WHITELIST
from src.formatting import allowed_number_strings
from src.alert.alert_agent import ESCALATION_LINE

_URL_RE = re.compile(r"https?://\S+")
_NUMBER_RE = re.compile(r"\d+\.?\d*")
# Zones are cited in signal lines: "- [detector] Zone (Province) ...". The headline is
# template-derived from flags[0], so zone fabrication can only enter via a signal line.
_SIGNAL_ZONE_RE = re.compile(r"(?m)^\s*-\s*\[[a-z_]+\]\s+(.+?)\s+\(")


@dataclass
class GuardrailResult:
    passed: bool                 # True only if there were no violations at all
    blocked: bool                # True if the prose was withheld (fail-closed)
    alert: str                   # the text to emit (original, sanitized, or placeholder)
    violations: List[Dict] = field(default_factory=list)


def _strip_urls(text: str) -> str:
    return _URL_RE.sub("", text)


def _remove_whitelisted(text: str) -> str:
    for term in GUARDRAIL_WHITELIST:
        text = re.sub(re.escape(term), " ", text, flags=re.IGNORECASE)
    return text


def _ensure_escalation(text: str) -> str:
    return text if "ESCALATION:" in text else f"{text}\n\n{ESCALATION_LINE}"


def _banned_hits(prose: str) -> List[str]:
    scan_text = _remove_whitelisted(prose)
    hits = []
    for pat in BANNED_PATTERNS:
        m = re.search(pat, scan_text, flags=re.IGNORECASE)
        if m:
            hits.append(m.group(0))
    return hits


def _strip_banned_lines(text: str) -> str:
    kept = []
    for line in text.split("\n"):
        if _banned_hits(_strip_urls(line)):
            continue
        kept.append(line)
    return "\n".join(kept)


def _blocked_placeholder(flags: List[Dict], violations: List[Dict]) -> str:
    lines = [
        "ALERT WITHHELD: the drafted brief failed an automated safety/traceability check and "
        "was withheld. Verify these flagged signals manually against their sources:",
        "",
    ]
    for f in flags:
        src = f.get("source_url") or f.get("source_url_prior") or "(no source_url)"
        lines.append(
            f"- [{f.get('detector')}] {f.get('health_zone')} ({f.get('province')}) — source: {src}"
        )
    lines.append("")
    reasons = "; ".join(f"{v['type']} ({v['detail']})" for v in violations)
    lines.append(f"Reason(s): {reasons}")
    lines.append("")
    lines.append(ESCALATION_LINE)
    return "\n".join(lines)


def enforce_guardrails(
    alert_text: str,
    ranked_flags: List[Dict],
    *,
    banned_mode: Optional[str] = None,
) -> GuardrailResult:
    """Validate a drafted alert against the ranked flags. See module docstring."""
    banned_mode = banned_mode or GUARDRAIL_BANNED_MODE
    violations: List[Dict] = []

    prose = _strip_urls(alert_text)  # URLs never scanned for numbers or banned words

    # 1. Number traceability — every cited number must render from a sourced flag field.
    allowed = allowed_number_strings(ranked_flags)
    for num in _NUMBER_RE.findall(prose):
        if num not in allowed:
            violations.append({"type": "unsourced_number", "detail": num})

    # 2. Fabricated zones — every signal-line zone must be a known flag zone.
    legit_zones = {f.get("health_zone") for f in ranked_flags}
    for zone in (z.strip() for z in _SIGNAL_ZONE_RE.findall(alert_text)):
        if zone not in legit_zones:
            violations.append({"type": "fabricated_zone", "detail": zone})

    # 3. Clinical / treatment / forecasting language (prose only, whitelist-aware).
    banned = _banned_hits(prose)
    for term in banned:
        violations.append({"type": "banned_language", "detail": term})

    integrity_failed = any(
        v["type"] in ("unsourced_number", "fabricated_zone") for v in violations
    )

    # Fail-closed: withhold the prose but still surface the sourced signals.
    if integrity_failed or (banned and banned_mode == "block"):
        return GuardrailResult(
            passed=False,
            blocked=True,
            alert=_blocked_placeholder(ranked_flags, violations),
            violations=violations,
        )

    alert_out = _ensure_escalation(alert_text)
    if banned and banned_mode == "strip":
        alert_out = _ensure_escalation(_strip_banned_lines(alert_out))

    return GuardrailResult(
        passed=(len(violations) == 0),
        blocked=False,
        alert=alert_out,
        violations=violations,
    )
