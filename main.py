import time, json, os, urllib.request
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
YAHOO = {s: s + "=X" for s in FOREX}

PAPER_INTERVAL_SECONDS = 60
MIN_SCORE_THRESHOLD = 90.0         # ขันเกณฑ์บอทขึ้นเป็น 90.0 เพื่อกรองสัญญาณหลอก
MAX_NEW_ALERTS_PER_SCAN = 1
MAX_SCAN_PAIRS = 18
SYMBOL_COOLDOWN_SECONDS = 300

PAIR_MIN_HISTORY = 10
SETUP_MIN_HISTORY = 10
PAIR_BLOCK_WR = 50.0               # บล็อกคู่เงินที่มี Win Rate ต่ำกว่า 50%
PAIR_WEAK_WR = 55.0
PAIR_FAVOR_WR = 65.0
PAIR_STRONG_WR = 70.0

# ---------------- TIMEZONE ----------------
THAI_TZ = timezone(timedelta(hours=7))

def thai_now():
    return datetime.now(THAI_TZ)

# ---------------- CONFIGURATION ----------------
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()
AI_CONFIDENCE_THRESHOLD = float(os.getenv("AI_CONFIDENCE_THRESHOLD", "80"))  # ดันเกณฑ์ AI เป็น 80%
FINAL_SCORE_THRESHOLD = float(os.getenv("FINAL_SCORE_THRESHOLD", "85"))      # ดัน Final Score เป็น 85%

STRICT_AI_CONSENSUS = True  # บังคับ Strict Consensus เสมอเมื่อมี AI

# ใช้ Relative Path เสมอ เพื่อป้องกันปัญหา FileNotFoundError บน Railway
BASE = Path(__file__).resolve().parent
MEMORY_CANDIDATES = [
    BASE / "bot_memory_sigzy_brain_v6.json",
    BASE / "bot_memory_sigzy_brain_v5.json",
    BASE / "bot_memory_sigzy_brain_v4_crypto_forex.json",
    BASE / "bot_memory_sigzy_discord.json",
]
MEMORY_FILE = str(next((p for p in MEMORY_CANDIDATES if p.exists()), MEMORY_CANDIDATES[0]))

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "").strip()

TUNING = {"DEFAULT": {"sr": .008, "zone": .015, "vol": 1.40}}
for s in CRYPTO:
    TUNING[s] = {
        "sr": {"BTCUSDT": .006, "ETHUSDT": .007, "SOLUSDT": .010, "XRPUSDT": .010, "BNBUSDT": .008}[s],
        "zone": {"BTCUSDT": .012, "ETHUSDT": .013, "SOLUSDT": .018, "XRPUSDT": .018, "BNBUSDT": .015}[s],
        "vol": {"BTCUSDT": 1.35, "ETHUSDT": 1.35, "SOLUSDT": 1.45, "XRPUSDT": 1.45, "BNBUSDT": 1.40}[s]}
for s in FOREX:
    TUNING[s] = {
        "sr": .005 if s in ("GBPUSD", "USDJPY", "EURUSD", "AUDUSD", "NZDUSD", "USDCAD", "USDCHF", "EURGBP") else .006,
        "zone": .012 if s in ("GBPUSD", "USDJPY", "EURUSD", "AUDUSD", "NZDUSD", "USDCAD", "USDCHF", "EURGBP") else .013,
        "vol": 1.20}

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
