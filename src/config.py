"""
Configuration module for the Ebola surveillance agent.
All tunables, thresholds, and environment-driven configurations live here.
"""

import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Gemini Model Config
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# ADK / google-genai read GOOGLE_API_KEY and GOOGLE_GENAI_USE_VERTEXAI. Alias from the
# existing GEMINI_API_KEY so .env files keep working under the ADK refactor.
os.environ.setdefault("GOOGLE_API_KEY", GEMINI_API_KEY or "")
os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "FALSE")

# ReliefWeb live-source config (Phase 1). The API v1 is decommissioned; v2 is required.
# RELIEFWEB_APPNAME is a public request identifier ReliefWeb asks each client to send; if it
# is unset, all live-source features degrade to disabled and the existing pipeline is
# unaffected. RELIEFWEB_DISASTER_ID pins the DRC Bundibugyo outbreak (GLIDE EP-2026-000071-COD,
# ReliefWeb disaster id 52586). It is overridable so the tool ages across future outbreaks.
RELIEFWEB_APPNAME = os.getenv("RELIEFWEB_APPNAME")
RELIEFWEB_API_BASE = os.getenv("RELIEFWEB_API_BASE", "https://api.reliefweb.int/v2")
RELIEFWEB_DISASTER_ID = int(os.getenv("RELIEFWEB_DISASTER_ID", "52586"))

# Surge Detector Thresholds
SURGE_MIN_DAILY_NEW = 10.0
SURGE_MIN_PCT_GROWTH = 0.5
SURGE_MAX_GAP_DAYS = 14

# CFR (Case Fatality Ratio) Detector Thresholds
CFR_SHIFT_THRESHOLD = 0.05
CFR_HIGH_THRESHOLD = 0.30
CFR_MIN_CONFIRMED = 20

# Guardrail layer (Phase 6).
# Clinical / treatment / forecasting language. Scanned ONLY on human-readable prose (URLs
# stripped) with operational terms whitelisted first. Default action: block + flag.
GUARDRAIL_BANNED_MODE = "block"  # "block" (default) or "strip"

BANNED_PATTERNS = [
    r"\bdiagnos\w*",      # diagnose, diagnosed, diagnosis, diagnosing
    r"\btreatments?\b",   # treatment(s) — whitelisted operational terms removed first
    r"\bprescrib\w*",     # prescribe, prescribed, prescription
    r"\bcure[ds]?\b",     # cure, cured, cures
    r"\bforecast\w*",     # epidemic forecasting
    r"\bpredict\w*",      # prediction language
]

# Operational terms that legitimately contain a banned word; removed before the scan so a
# named "Ebola Treatment Center" does not trip the treatment ban.
GUARDRAIL_WHITELIST = [
    "ebola treatment center",
    "ebola treatment centre",
    "ebola treatment unit",
    "treatment center",
    "treatment centre",
    "treatment unit",
]
