"""Central knobs for the app. Tweak here, not scattered through the code."""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# --- paths ---
ROOT = Path(__file__).resolve().parent.parent
ASSETS_DIR = ROOT / "assets"
DEMO_CLIPS_DIR = ASSETS_DIR / "demo_clips"
CACHED_RESULTS_DIR = ASSETS_DIR / "cached_results"
RULES_DIR = ROOT / "src" / "rules"
TMP_DIR = ROOT / "tmp"
TMP_DIR.mkdir(exist_ok=True)

# --- gemini ---
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
# 2.5-flash is cheap, fast, and on the free tier. Bump to -pro for harder calls.
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

# --- video preprocessing ---
# Accuracy-first defaults (less aggressive compression than the original demo).
MAX_CLIP_SECONDS = 12
TARGET_FPS = 24
TARGET_HEIGHT = 1080
X264_PRESET = "slow"
X264_CRF = 18
APPLY_UNSHARP = True

# Bump this to invalidate stale cached verdicts when prompt/logic changes.
CACHE_VERSION = "v3"

# --- rate limiting (keep us under Gemini free tier) ---
PER_IP_PER_HOUR = int(os.environ.get("PER_IP_PER_HOUR", "3"))
GLOBAL_PER_DAY = int(os.environ.get("GLOBAL_PER_DAY", "500"))
# Set to "1" in .env to disable limits during live demos.
DISABLE_RATE_LIMIT = os.environ.get("DISABLE_RATE_LIMIT", "0") == "1"

# --- sport ---
# Default sport when nothing else is specified. The actual sport for any given
# analysis is chosen in the UI (sidebar) or via sidecar JSON for warm_cache.
DEFAULT_SPORT = "tennis"
SPORT = DEFAULT_SPORT  # back-compat alias used by older code paths
