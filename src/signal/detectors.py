"""
Rule-based detectors for the Ebola surveillance agent.
All functions are pure Python functions that return structured flags.
"""

from typing import List, Dict
import pandas as pd

from src.config import (
    SURGE_MIN_DAILY_NEW,
    SURGE_MIN_PCT_GROWTH,
    SURGE_MAX_GAP_DAYS,
    CFR_SHIFT_THRESHOLD,
    CFR_HIGH_THRESHOLD,
    CFR_MIN_CONFIRMED,
)

def detect_new_zone(incoming_df: pd.DataFrame, prior_df: pd.DataFrame) -> List[Dict]:
    """
    new_zone: health_zone present in incoming but absent from history (prior_df).
    New-only zones go to this detector.
    """
    prior_zones = set(prior_df["health_zone"].unique())
    flags = []
    
    for _, row in incoming_df.iterrows():
        zone = row["health_zone"]
        if zone not in prior_zones:
            flags.append({
                "detector": "new_zone",
                "health_zone": zone,
                "province": row["province"],
                "date": row["date"].strftime("%Y-%m-%d") if hasattr(row["date"], "strftime") else str(row["date"]),
                "suspected_cases": int(row["suspected_cases"]) if not pd.isna(row["suspected_cases"]) else None,
                "confirmed_cases": int(row["confirmed_cases"]) if not pd.isna(row["confirmed_cases"]) else None,
                "deaths": int(row["deaths"]) if not pd.isna(row["deaths"]) else None,
                "source_url": row["source_url"],
                "report_date": row["report_date"].strftime("%Y-%m-%d") if hasattr(row["report_date"], "strftime") else str(row["report_date"]),
            })
            
    return flags


def detect_surge(incoming_df: pd.DataFrame, prior_df: pd.DataFrame) -> List[Dict]:
    """
    surge: confirmed_cases growth in a zone above a rate + relative threshold over a window.
    Evaluate only zones present in BOTH prior and incoming.
    Skip any row with a null count.
    """
    incoming_by_zone = {r["health_zone"]: r for _, r in incoming_df.iterrows()}
    prior_by_zone = {r["health_zone"]: r for _, r in prior_df.iterrows()}
    
    common_zones = set(incoming_by_zone.keys()) & set(prior_by_zone.keys())
    flags = []
    
    for zone in sorted(common_zones):
        inc = incoming_by_zone[zone]
        pri = prior_by_zone[zone]
        
        # Surge uses only confirmed_cases. Skip if it is null/NaN in either side.
        if pd.isna(inc["confirmed_cases"]) or pd.isna(pri["confirmed_cases"]):
            continue

        inc_date = pd.to_datetime(inc["date"])
        pri_date = pd.to_datetime(pri["date"])
        days = (inc_date - pri_date).days
        
        # Skip if days <= 0 or days > SURGE_MAX_GAP_DAYS
        if days <= 0 or days > SURGE_MAX_GAP_DAYS:
            continue
            
        confirmed_inc = int(inc["confirmed_cases"])
        confirmed_pri = int(pri["confirmed_cases"])
        
        daily_new = (confirmed_inc - confirmed_pri) / days
        pct_growth = (confirmed_inc - confirmed_pri) / max(confirmed_pri, 1)
        
        if daily_new >= SURGE_MIN_DAILY_NEW or pct_growth >= SURGE_MIN_PCT_GROWTH:
            flags.append({
                "detector": "surge",
                "health_zone": zone,
                "province": inc["province"],
                "days": int(days),
                "confirmed_prior": confirmed_pri,
                "confirmed_incoming": confirmed_inc,
                "daily_new": float(daily_new),
                "pct_growth": float(pct_growth),
                "source_url": inc["source_url"],
                "report_date": inc["report_date"].strftime("%Y-%m-%d") if hasattr(inc["report_date"], "strftime") else str(inc["report_date"]),
            })
            
    return flags


def detect_cfr_shift(incoming_df: pd.DataFrame, prior_df: pd.DataFrame) -> List[Dict]:
    """
    cfr_shift: deaths/confirmed rising past threshold.
    Evaluate only zones present in BOTH prior and incoming.
    Evaluate a zone only when confirmed_incoming >= CFR_MIN_CONFIRMED.
    Skip any row with a null count.
    """
    incoming_by_zone = {r["health_zone"]: r for _, r in incoming_df.iterrows()}
    prior_by_zone = {r["health_zone"]: r for _, r in prior_df.iterrows()}
    
    common_zones = set(incoming_by_zone.keys()) & set(prior_by_zone.keys())
    flags = []
    
    for zone in sorted(common_zones):
        inc = incoming_by_zone[zone]
        pri = prior_by_zone[zone]
        
        # CFR uses only confirmed_cases and deaths. Skip if either is null/NaN in either side.
        if any(pd.isna(inc[c]) or pd.isna(pri[c]) for c in ("confirmed_cases", "deaths")):
            continue

        confirmed_inc = int(inc["confirmed_cases"])
        confirmed_pri = int(pri["confirmed_cases"])

        # Evaluate only when confirmed_incoming >= CFR_MIN_CONFIRMED
        if confirmed_inc < CFR_MIN_CONFIRMED:
            continue
            
        deaths_inc = int(inc["deaths"])
        deaths_pri = int(pri["deaths"])
        
        cfr_inc = deaths_inc / confirmed_inc
        cfr_pri = deaths_pri / confirmed_pri if confirmed_pri > 0 else 0.0
        
        cfr_diff = cfr_inc - cfr_pri
        
        if cfr_inc >= CFR_HIGH_THRESHOLD or cfr_diff >= CFR_SHIFT_THRESHOLD:
            flags.append({
                "detector": "cfr_shift",
                "health_zone": zone,
                "province": inc["province"],
                "confirmed_prior": confirmed_pri,
                "deaths_prior": deaths_pri,
                "cfr_prior": float(cfr_pri),
                "confirmed_incoming": confirmed_inc,
                "deaths_incoming": deaths_inc,
                "cfr_incoming": float(cfr_inc),
                "cfr_diff": float(cfr_diff),
                "source_url": inc["source_url"],
                "report_date": inc["report_date"].strftime("%Y-%m-%d") if hasattr(inc["report_date"], "strftime") else str(inc["report_date"]),
            })
            
    return flags


def detect_stale_or_missing(incoming_df: pd.DataFrame, prior_df: pd.DataFrame) -> List[Dict]:
    """
    stale_or_missing: expected zone missing, or a null required field.
    Prior-only zones go to this detector.
    """
    incoming_zones = set(incoming_df["health_zone"].unique())
    prior_zones = set(prior_df["health_zone"].unique())
    flags = []
    
    # 1. Prior-only zones (expected zone missing)
    missing_zones = prior_zones - incoming_zones
    for zone in sorted(missing_zones):
        prior_rows = prior_df[prior_df["health_zone"] == zone]
        if not prior_rows.empty:
            prior_row = prior_rows.iloc[-1]
            flags.append({
                "detector": "stale_or_missing",
                "health_zone": zone,
                "province": prior_row["province"],
                "type": "missing_zone",
                "details": f"Expected health zone '{zone}' is present in history but missing from the incoming report.",
                "confirmed_cases_prior": int(prior_row["confirmed_cases"]),
                "deaths_prior": int(prior_row["deaths"]),
                "report_date_prior": prior_row["report_date"].strftime("%Y-%m-%d") if hasattr(prior_row["report_date"], "strftime") else str(prior_row["report_date"]),
                "source_url_prior": prior_row["source_url"]
            })
            
    # 2. Null required fields in incoming reports
    for _, row in incoming_df.iterrows():
        zone = row["health_zone"]
        null_fields = []
        for col in ["suspected_cases", "confirmed_cases", "deaths"]:
            if pd.isna(row[col]) or row[col] is None:
                null_fields.append(col)
        if null_fields:
            flags.append({
                "detector": "stale_or_missing",
                "health_zone": zone,
                "province": row["province"],
                "type": "null_field",
                "details": f"Incoming report record for health zone '{zone}' has null value(s) in required field(s): {', '.join(null_fields)}.",
                "null_fields": null_fields,
                "source_url": row["source_url"],
                "report_date": row["report_date"].strftime("%Y-%m-%d") if hasattr(row["report_date"], "strftime") else str(row["report_date"]),
            })
            
    return flags


def run_all_detectors(incoming_df: pd.DataFrame, prior_df: pd.DataFrame) -> List[Dict]:
    """Run all four detectors and return a aggregated list of flags."""
    flags = []
    flags.extend(detect_new_zone(incoming_df, prior_df))
    flags.extend(detect_surge(incoming_df, prior_df))
    flags.extend(detect_cfr_shift(incoming_df, prior_df))
    flags.extend(detect_stale_or_missing(incoming_df, prior_df))
    return flags
