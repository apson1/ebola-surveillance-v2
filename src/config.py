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
