"""Live source discovery via the ReliefWeb API v2 (Phase 1).

Retrieval and demo capability only — NO numbers are extracted here and nothing is fed into
the detectors, alert, guardrail, memory, or eval set. This module returns metadata about the
latest official DRC Ebola situation reports.

Safety / robustness (per the live-data plan):
- If RELIEFWEB_APPNAME is unset, live sources are disabled (empty result, logged) and the
  existing pipeline is unaffected.
- Any network error, rate limit, or non-200 returns an empty result and logs the reason.
  It never raises into the caller.
- Results are cached on disk: successful fetches for 15 minutes (repeatable demos, quota
  safety); failure/disabled states for 30 seconds (so Streamlit reruns don't hammer the net).
- The query is pinned to the DRC Bundibugyo disaster (id 52586, GLIDE EP-2026-000071-COD).
  If that pinned query returns zero results, it falls back to an "ebola" + DRC text query and
  records that the fallback fired, so the drift is visible in the UI.

ReliefWeb API v2 verified live: POST https://api.reliefweb.int/v2/reports?appname=...
"""
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Dict, List

import requests

from src.config import RELIEFWEB_API_BASE, RELIEFWEB_APPNAME, RELIEFWEB_DISASTER_ID

logger = logging.getLogger(__name__)

_CACHE_DIR = ".cache"
_CACHE_FILE = os.path.join(_CACHE_DIR, "reliefweb_sources.json")
_SUCCESS_TTL = 15 * 60   # 900s — successful fetch
_FAILURE_TTL = 30        # 30s — disabled / error / empty (negative caching)
_TIMEOUT = 10            # seconds per HTTP request

# Fields to request from ReliefWeb (real v2 field names, verified against a live response).
_INCLUDE_FIELDS = ["title", "source", "date", "url_alias", "url"]


@dataclass
class LiveSourceResult:
    """Reports plus the retrieval mode, so the UI can show which path was used."""
    reports: List[Dict] = field(default_factory=list)
    mode: str = "disabled"   # "pinned" | "fallback" | "disabled" | "error"
    note: str = ""
    fetched_at: float = 0.0  # epoch seconds this data was fetched (or cached); UI shows "fetched N ago"


def _record(item: Dict) -> Dict:
    """Flatten a ReliefWeb report item to the plan's {id, title, source, date, url} shape.

    Verified mapping: source is an array (use shortname), date is an object (use .original),
    the clickable public page is url_alias (fall back to the node url), id is a string.
    """
    f = item.get("fields", {})
    sources = f.get("source") or [{}]
    return {
        "id": item.get("id"),
        "title": f.get("title"),
        "source": sources[0].get("shortname") or sources[0].get("name"),
        "date": (f.get("date") or {}).get("original"),
        "url": f.get("url_alias") or f.get("url"),
    }


def _post(body: Dict) -> List[Dict]:
    """POST a query to the ReliefWeb reports endpoint. Raises on network/HTTP error."""
    resp = requests.post(
        f"{RELIEFWEB_API_BASE}/reports",
        params={"appname": RELIEFWEB_APPNAME},
        json=body,
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    return [_record(it) for it in data.get("data", [])]


def _pinned_query(limit: int) -> List[Dict]:
    return _post({
        "filter": {"operator": "AND", "conditions": [
            {"field": "disaster.id", "value": RELIEFWEB_DISASTER_ID},
            {"field": "format.name", "value": "Situation Report"},
        ]},
        "fields": {"include": _INCLUDE_FIELDS},
        "sort": ["date.created:desc"],
        "limit": limit,
    })


def _fallback_query(limit: int) -> List[Dict]:
    return _post({
        "query": {"value": "ebola"},
        "filter": {"operator": "AND", "conditions": [
            {"field": "primary_country.iso3", "value": "cod"},
            {"field": "format.name", "value": "Situation Report"},
        ]},
        "fields": {"include": _INCLUDE_FIELDS},
        "sort": ["date.created:desc"],
        "limit": limit,
    })


# ---- caching -------------------------------------------------------------------------

def _read_cache(limit: int):
    try:
        with open(_CACHE_FILE, encoding="utf-8") as fh:
            entry = json.load(fh).get(str(limit))
        if entry and (time.time() - entry["fetched_at"]) < entry["ttl"]:
            return LiveSourceResult(entry["reports"], entry["mode"], entry["note"], entry["fetched_at"])
    except (FileNotFoundError, json.JSONDecodeError, KeyError, TypeError):
        pass
    return None


def _write_cache(limit: int, result: LiveSourceResult, ttl: int) -> None:
    try:
        os.makedirs(_CACHE_DIR, exist_ok=True)
        store = {}
        if os.path.exists(_CACHE_FILE):
            with open(_CACHE_FILE, encoding="utf-8") as fh:
                store = json.load(fh)
        store[str(limit)] = {
            "fetched_at": result.fetched_at or time.time(), "ttl": ttl,
            "mode": result.mode, "note": result.note, "reports": result.reports,
        }
        with open(_CACHE_FILE, "w", encoding="utf-8") as fh:
            json.dump(store, fh)
    except OSError as e:  # caching is best-effort; never let it break a fetch
        logger.warning("Could not write ReliefWeb cache: %s", e)


# ---- public entry point --------------------------------------------------------------

def fetch_recent_drc_ebola_reports(limit: int = 10, force: bool = False) -> LiveSourceResult:
    """Return the latest official DRC Ebola situation reports (metadata only).

    Never raises. See the module docstring for the caching and fallback behavior. Pass
    force=True to bypass the cache and re-fetch (the UI's "Refresh latest report" control).
    """
    if not force:
        cached = _read_cache(limit)
        if cached is not None:
            return cached

    if not RELIEFWEB_APPNAME:
        result = LiveSourceResult(
            [], "disabled",
            "Live sources disabled: set RELIEFWEB_APPNAME in your .env to enable them.",
        )
        result.fetched_at = time.time()
        _write_cache(limit, result, _FAILURE_TTL)
        return result

    try:
        reports = _pinned_query(limit)
        if reports:
            result = LiveSourceResult(
                reports, "pinned",
                f"Pinned to DRC Ebola disaster {RELIEFWEB_DISASTER_ID} (GLIDE EP-2026-000071-COD).",
            )
        else:
            logger.warning(
                "Pinned disaster %s returned 0 reports; falling back to 'ebola' + DRC text query.",
                RELIEFWEB_DISASTER_ID,
            )
            reports = _fallback_query(limit)
            result = LiveSourceResult(
                reports, "fallback",
                f"Fallback active: pinned disaster {RELIEFWEB_DISASTER_ID} returned no reports; "
                "showing an 'ebola' + DRC text query instead (we may have drifted off the pinned outbreak).",
            )
    except requests.RequestException as e:
        logger.warning("ReliefWeb fetch failed: %s", e)
        result = LiveSourceResult([], "error", f"Live sources unavailable right now: {e}")

    # Cache successes (records returned) for 15 min; disabled/error/empty for 30s.
    result.fetched_at = time.time()
    _write_cache(limit, result, _SUCCESS_TTL if result.reports else _FAILURE_TTL)
    return result


def fetch_report_meta(report_id) -> Dict:
    """Fetch a single report's metadata by id: {id, title, source, date, url, disaster_ids,
    disaster_names}. Returns {} on any failure. `disaster_ids` are the ReliefWeb disasters this
    report is linked to — the 'load by ID' path uses them to warn when a report is not associated
    with the active outbreak (a report id can numerically collide with a disaster id)."""
    if not RELIEFWEB_APPNAME:
        logger.warning("fetch_report_meta skipped: RELIEFWEB_APPNAME unset [report_id=%s]", report_id)
        return {}
    try:
        resp = requests.post(
            f"{RELIEFWEB_API_BASE}/reports",
            params={"appname": RELIEFWEB_APPNAME},
            json={"filter": {"field": "id", "value": report_id},
                  "fields": {"include": _INCLUDE_FIELDS + ["disaster.id", "disaster.name"]},
                  "limit": 1},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json().get("data") or []
        if not data:
            return {}
        meta = _record(data[0])
        disasters = data[0].get("fields", {}).get("disaster") or []
        meta["disaster_ids"] = [d.get("id") for d in disasters if d.get("id") is not None]
        meta["disaster_names"] = [d.get("name") for d in disasters if d.get("name")]
        return meta
    except requests.RequestException as e:
        logger.warning("fetch_report_meta failed [report_id=%s stage=fetch_meta]: %s", report_id, e)
        return {}


def fetch_report_body(report_id) -> str:
    """Fetch a single ReliefWeb report's markdown body by its id (Phase 2 input).

    Returns '' on any failure (appname unset, network/HTTP error, or no body). Never raises.
    Uses the verified list endpoint with an id filter + fields.include=["body"].
    """
    if not RELIEFWEB_APPNAME:
        logger.warning("fetch_report_body skipped: RELIEFWEB_APPNAME unset [report_id=%s]", report_id)
        return ""
    try:
        resp = requests.post(
            f"{RELIEFWEB_API_BASE}/reports",
            params={"appname": RELIEFWEB_APPNAME},
            json={"filter": {"field": "id", "value": report_id},
                  "fields": {"include": ["body"]}, "limit": 1},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json().get("data") or []
        if not data:
            logger.warning("fetch_report_body: no report found [report_id=%s]", report_id)
            return ""
        return data[0].get("fields", {}).get("body", "") or ""
    except requests.RequestException as e:
        logger.warning("fetch_report_body failed [report_id=%s stage=fetch_body]: %s", report_id, e)
        return ""
