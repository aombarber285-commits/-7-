import time
import json
import os
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ============================================================
# SIGZY BRAIN V6.2 - SINGLE FILE FOR RAILWAY
# PRESERVES: Memory Structure / 85.0 Threshold / Existing Logic
# ============================================================

CRYPTO = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT"]
FOREX = [
    "GBPUSD", "GBPJPY", "USDJPY", "EURUSD", "EURJPY", "AUDJPY",
    "AUDUSD", "NZDUSD", "USDCAD", "USDCHF", "EURGBP", "NZDJPY", "CADJPY"
]
SYMBOLS = CRYPTO + FOREX

PAPER_INTERVAL_SECONDS = 60
MIN_SCORE_THRESHOLD = 85.0

THAI_TZ = timezone(timedelta(hours=7))
BASE = Path(__file__).resolve().parent

MEMORY_FILE = BASE / "sigzy_v6_memory.json"
STATE_FILE = BASE / "sigzy_v6_state.json"

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "").strip()

# ============================================================
# TIME / IO
# ============================================================

def thai_now():
    return datetime.now(THAI_TZ)

def log(msg):
    print(msg, flush=True)

def http_json(url, payload=None, timeout=20, headers=None):
    h = {
        "User-Agent": "SIGZY-BRAIN-V6.2",
        "Accept": "application/json",
    }
    if headers:
        h.update(headers)

    data = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        h["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=data, headers=h, method="POST" if data else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))

def save_json(path, obj):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)

# ============================================================
# MEMORY PRESERVATION
# ============================================================

def default_memory():
    return {
        "version": "SIGZY_V6.2",
        "created_at": thai_now().isoformat(),
        "stats": {
            "total_setups": 0,
            "wins": 0,
            "losses": 0,
            "draws": 0,
            "invalid": 0,
            "opportunity_1": {"wins": 0, "losses": 0, "draws": 0},
            "opportunity_2": {"wins": 0, "losses": 0, "draws": 0},
            "opportunity_3": {"wins": 0, "losses": 0, "draws": 0},
        },
        "pairs": {},
        "setups": {},
        "lessons": [],
    }

def load_memory():
    if not MEMORY_FILE.exists():
        mem = default_memory()
        save_json(MEMORY_FILE, mem)
        return mem

    try:
        mem = json.loads(MEMORY_FILE.read_text(encoding="utf-8"))
        base = default_memory()

        if not isinstance(mem, dict):
            return base

        for k, v in base.items():
            if k not in mem:
                mem[k] = v

        for k, v in base["stats"].items():
            if k not in mem["stats"]:
                mem["stats"][k] = v

        return mem
    except Exception as e:
        log(f"[MEMORY] load error: {e}")
        return default_memory()

MEMORY = load_memory()

# ============================================================
# DISCORD
# ============================================================

def discord(msg):
    if not DISCORD_WEBHOOK_URL:
        log("[WARN] DISCORD_WEBHOOK_URL is not set.")
        return False

    try:
        data = json.dumps({"content": msg}, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            DISCORD_WEBHOOK_URL,
            data=data,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "SIGZY-V6.2"
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15):
            pass
        return True
    except Exception as e:
        log(f"[DISCORD ERROR] {e}")
        return False

# ============================================================
# MARKET DATA
# ============================================================

def fetch_crypto_klines(symbol, limit=80):
    url = (
        "https://api.binance.com/api/v3/klines"
        f"?symbol={symbol}&interval=1m&limit={limit}"
    )
    raw = http_json(url)

    candles = []
    for x in raw:
        candles.append({
            "open": float(x[1]),
            "high": float(x[2]),
            "low": float(x[3]),
            "close": float(x[4]),
            "volume": float(x[5]),
            "time": int(x[0]),
        })
    return candles

def fetch_forex_klines(symbol, limit=80):
    ysymbol = symbol + "=X"
    url = (
        "https://query1.finance.yahoo.com/v8/finance/chart/"
        f"{ysymbol}?interval=1m&range=1d"
    )

    raw = http_json(url)
    result = raw["chart"]["result"][0]
    quote = result["indicators"]["quote"][0]

    opens = quote.get("open", [])
    highs = quote.get("high", [])
    lows = quote.get("low", [])
    closes = quote.get("close", [])
    volumes = quote.get("volume", [])
    timestamps = result.get("timestamp", [])

    candles = []
    for i in range(min(len(opens), len(highs), len(lows), len(closes))):
        if None in (opens[i], highs[i], lows[i], closes[i]):
            continue

        vol = 0.0
        if i < len(volumes) and volumes[i] is not None:
            vol = float(volumes[i])

        ts = int(timestamps[i]) if i < len(timestamps) else 0

        candles.append({
            "open": float(opens[i]),
            "high": float(highs[i]),
            "low": float(lows[i]),
            "close": float(closes[i]),
            "volume": vol,
            "time": ts,
        })

    return candles[-limit:]

def fetch_klines(symbol, limit=80):
    if symbol in CRYPTO:
        return fetch_crypto_klines(symbol, limit)
    return fetch_forex_klines(symbol, limit)

# ============================================================
# TECHNICAL CALCULATIONS
# ============================================================

def ema(values, period):
    if len(values) < period:
        return None

    k = 2.0 / (period + 1.0)
    value = sum(values[:period]) / period

    for x in values[period:]:
        value = x * k + value * (1.0 - k)

    return value

def safe_avg(values):
    return sum(values) / len(values) if values else 0.0

def market_score(candles):
    if len(candles) < 30:
        return None

    closes = [c["close"] for c in candles]
    volumes = [c["volume"] for c in candles]

    last = candles[-1]
    prev = candles[-2]

    ema9 = ema(closes, 9)
    ema21 = ema(closes, 21)

    if ema9 is None or ema21 is None:
        return None

    bull = 0
    bear = 0
    reasons = []

    if ema9 > ema21:
        bull += 1
        reasons.append("EMA_UP")
    elif ema9 < ema21:
        bear += 1
        reasons.append("EMA_DOWN")

    if last["close"] > last["open"]:
        bull += 1
        reasons.append("CANDLE_BULL")
    elif last["close"] < last["open"]:
        bear += 1
        reasons.append("CANDLE_BEAR")

    if prev["close"] > prev["open"]:
        bull += 1
    elif prev["close"] < prev["open"]:
        bear += 1

    momentum = closes[-1] - closes[-6]
    if momentum > 0:
        bull += 1
        reasons.append("MOMENTUM_UP")
    elif momentum < 0:
        bear += 1
        reasons.append("MOMENTUM_DOWN")

    avg_vol = safe_avg(volumes[-10:])
    if avg_vol > 0 and last["volume"] > avg_vol * 1.10:
        if last["close"] > last["open"]:
            bull += 1
            reasons.append("VOLUME_BULL")
        elif last["close"] < last["open"]:
            bear += 1
            reasons.append("VOLUME_BEAR")

    if bull == bear:
        return {
            "score": 0.0,
            "signal": "NEUTRAL",
            "bull": bull,
            "bear": bear,
            "price": last["close"],
            "ema9": ema9,
            "ema21": ema21,
            "reasons": reasons + ["NO_EDGE"],
        }

    signal = "CALL" if bull > bear else "PUT"
    edge = abs(bull - bear)
    score = 85.0 + min(14.0, edge / 4.0 * 14.0)

    return {
        "score": round(score, 2),
        "signal": signal,
        "bull": bull,
        "bear": bear,
        "price": last["close"],
        "ema9": ema9,
        "ema21": ema21,
        "reasons": reasons,
    }

# ============================================================
# AI INTEGRATION
# ============================================================

def parse_ai_json(text):
    text = str(text).strip()
    if text.startswith("```"):
        text = text.replace("
