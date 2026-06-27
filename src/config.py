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

# Banned Output Patterns (Phase 6 placeholder to keep structure aligned)
BANNED_PATTERNS = [
    r"\bdiagnose\b",
    r"\bdiagnosis\b",
    r"\btreatment\b",
    r"\bprescribe\b",
    r"\bcure\b",
]
