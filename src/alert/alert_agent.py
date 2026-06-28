"""
Alert Agent for the Ebola surveillance system.
Produces a structured alert brief for a human coordinator using a fixed template.
"""

from typing import Dict, List

from src.formatting import fmt_float1, fmt_pct1

# Shared with the guardrail layer, which both ensures this line is present and reuses it in
# its fail-closed placeholder. Keep it as the single source of the escalation wording.
ESCALATION_LINE = (
    "ESCALATION: Human coordinator must verify the flagged signal(s) and cited numbers against the source reports "
    "and decide. This tool is for decision support only and does not make autonomous clinical or operational decisions."
)


def draft_alert(ranked_flags: List[Dict]) -> str:
    """
    Drafts an alert brief for a human coordinator.
    Uses a fixed template and fills it ONLY from the ranked flags.
    No numbers are invented or recomputed. The brief is fully deterministic:
    headline / signals / note / escalation.
    """
    if not ranked_flags:
        headline = "ALERT: No active surveillance signals flagged."
        signals_section = "No active signals."
        note_text = "CONFIDENCE AND DATA-QUALITY NOTE: No signals flagged. All counts are as-reported and unverified."
        return f"{headline}\n\n{signals_section}\n\n{note_text}\n\n{ESCALATION_LINE}"
    
    # 1. Headline: one line naming the single highest-ranked signal and its zone.
    highest = ranked_flags[0]
    detector = highest.get("detector")
    zone = highest.get("health_zone", "Unknown Zone")
    province = highest.get("province", "Unknown Province")
    
    if detector == "new_zone":
        headline = f"ALERT: New transmission zone detected in {zone} ({province})"
    elif detector == "surge":
        headline = f"ALERT: Rapid confirmed case surge detected in {zone} ({province})"
    elif detector == "cfr_shift":
        headline = f"ALERT: Significant rise in Case Fatality Ratio detected in {zone} ({province})"
    elif detector == "stale_or_missing":
        if highest.get("type") == "missing_zone":
            headline = f"ALERT: Expected reporting from health zone {zone} ({province}) is missing"
        else:
            headline = f"ALERT: Data quality gap (null value) flagged in health zone {zone} ({province})"
    else:
        headline = f"ALERT: Surveillance signal flagged in {zone} ({province})"
        
    # 2. Ranked signals: each flag as a short line stating the detector type, health_zone,
    # key numbers, source_url and report_date.
    signal_lines = []
    for f in ranked_flags:
        det = f.get("detector")
        hz = f.get("health_zone")
        prov = f.get("province")
        source = f.get("source_url")
        rep_date = f.get("report_date")
        
        if det == "new_zone":
            line = (
                f"- [new_zone] {hz} ({prov}): confirmed_cases={f.get('confirmed_cases')} "
                f"(source: {source}, report_date: {rep_date})"
            )
        elif det == "surge":
            line = (
                f"- [surge] {hz} ({prov}): confirmed cases {f.get('confirmed_prior')} -> {f.get('confirmed_incoming')}, "
                f"daily_new={fmt_float1(f.get('daily_new'))}, pct_growth={fmt_pct1(f.get('pct_growth'))}% "
                f"(source: {source}, report_date: {rep_date})"
            )
        elif det == "cfr_shift":
            line = (
                f"- [cfr_shift] {hz} ({prov}): CFR {fmt_pct1(f.get('cfr_prior'))}% -> {fmt_pct1(f.get('cfr_incoming'))}% "
                f"(shift: {fmt_pct1(f.get('cfr_diff'))}%) "
                f"(source: {source}, report_date: {rep_date})"
            )
        elif det == "stale_or_missing":
            if f.get("type") == "missing_zone":
                line = (
                    f"- [stale_or_missing] {hz} ({prov}) is missing from the incoming report "
                    f"(prior: confirmed_cases={f.get('confirmed_cases_prior')}, deaths={f.get('deaths_prior')}, "
                    f"source: {f.get('source_url_prior')}, report_date_prior: {f.get('report_date_prior')})"
                )
            else:
                line = (
                    f"- [stale_or_missing] {hz} ({prov}) has null value(s) in required field(s): {f.get('null_fields')} "
                    f"(source: {source}, report_date: {rep_date})"
                )
        else:
            line = f"- [{det}] {hz} ({prov}) (source: {source})"
            
        signal_lines.append(line)
        
    signals_section = "\n".join(signal_lines)
    
    # 3. Confidence and data-quality note: state if any stale_or_missing flags are present
    # and that counts are as-reported and unverified.
    stale_flags = [f for f in ranked_flags if f.get("detector") == "stale_or_missing"]
    if stale_flags:
        note_text = (
            "CONFIDENCE AND DATA-QUALITY NOTE: Reporting gaps or null data fields were flagged in this scan. "
            "All data counts are as-reported by the source agencies and are currently unverified. "
            "Surveillance system integrity may be degraded by these missing reports."
        )
    else:
        note_text = (
            "CONFIDENCE AND DATA-QUALITY NOTE: No reporting gaps or null fields were flagged in this scan. "
            "However, all data counts are as-reported by the source agencies and remain unverified."
        )
        
    # LLM reasoning is intentionally NOT rendered here so no model-authored number can
    # enter the brief. The signal agent already returns `reasoning` as a separate field
    # for the orchestrator to log.

    # 4. Escalation line: a fixed sentence directing the human to verify and decide.
    return f"{headline}\n\n{signals_section}\n\n{note_text}\n\n{ESCALATION_LINE}"
