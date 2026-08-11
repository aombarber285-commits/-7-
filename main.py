import time, json, os, urllib.request, math
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ============================================================
# SIGZY BRAIN V6.0 - 3 BRAIN PAPER TRADER (STRICT 80%+ WINRATE TUNED)
# BOT + AI #1 PATTERN + AI #2 RISK/CONTRARIAN
# REAL MEMORY + BEST PAIR SELECTOR + DISCORD
# THAI TIMEZONE FIXED (UTC+7)
# ============================================================

CRYPTO = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT"]
FOREX = ["GBPUSD", "GBPJPY", "USDJPY", "EURUSD", "EURJPY", "AUDJPY",
         "AUDUSD", "NZDUSD", "USDCAD", "USDCHF", "EURGBP", "NZDJPY", "CADJPY"]
SYMBOLS = CRYPTO + FOREX

PAPER_INTERVAL_SECONDS = 60
MIN_SCORE_THRESHOLD = 85.0
SYMBOL_COOLDOWN_SECONDS = 300

THAI_TZ = timezone(timedelta(hours=7))

def thai_now():
    return datetime.now(THAI_TZ)

# ---------------- CONFIGURATION ----------------
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "").strip()

BASE = Path(__file__).resolve().parent
MEMORY_FILE = BASE / "bot_memory_sigzy_brain_v6.json"

# ---------------- DISCORD NOTIFICATION ----------------
def discord(msg):
    if not DISCORD_WEBHOOK_URL:
        print("[WARN] DISCORD_WEBHOOK_URL is not set.", flush=True)
        return False
    try:
        data = json.dumps({"content": msg}, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            DISCORD_WEBHOOK_URL,
            data=data,
            headers={"Content-Type": "application/json", "User-Agent": "SIGZY-V6"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=15):
            pass
        return True
    except Exception as e:
        print("[DISCORD ERROR]", e, flush=True)
        return False

# ---------------- AI FUNCTIONS ----------------
def parse_ai_json(text):
    text = str(text).strip()
    if text.startswith("```"):
        lines = text.splitlines()[1:]
        if lines and lines[-1].strip() == "
