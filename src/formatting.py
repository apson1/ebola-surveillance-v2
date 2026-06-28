"""Shared number/date formatting, imported by BOTH the alert template (to render numbers)
and the guardrail (to validate them). Single-sourcing these renderings is what stops the
traceability check from drifting away from what the template actually emits.
"""
import re
from typing import Dict, List, Set

# Flag fields that hold dates; their digit groups are the only "numbers" a string field
# legitimately contributes to the rendered alert.
DATE_FIELDS = {"date", "report_date", "report_date_prior"}


def fmt_float1(x) -> str:
    """One-decimal float, e.g. daily_new -> '61.0'."""
    return f"{float(x):.1f}"


def fmt_pct1(ratio) -> str:
    """Ratio -> one-decimal percent string WITHOUT the % sign (the template adds it)."""
    return f"{float(ratio) * 100:.1f}"


def flag_number_strings(flag: Dict) -> Set[str]:
    """Every numeric string the template could legitimately render for this flag:
    integers, one-decimal floats, one-decimal percents, and the digit groups of any date
    field. Intentionally a superset — the guardrail must never reject a value the template
    is entitled to print; it only catches numbers with no basis in the flags at all.
    """
    allowed: Set[str] = set()
    for key, val in flag.items():
        if val is None or isinstance(val, bool):
            continue
        if key in DATE_FIELDS:
            allowed.update(re.findall(r"\d+", str(val)))
        elif isinstance(val, int):
            allowed.add(str(val))
        elif isinstance(val, float):
            if val.is_integer():
                allowed.add(str(int(val)))
            allowed.add(fmt_float1(val))   # e.g. daily_new
            allowed.add(fmt_pct1(val))     # e.g. cfr / pct_growth as percent
    return allowed


def allowed_number_strings(flags: List[Dict]) -> Set[str]:
    """Union of every legitimate numeric rendering across all ranked flags."""
    out: Set[str] = set()
    for f in flags:
        out |= flag_number_strings(f)
    return out
