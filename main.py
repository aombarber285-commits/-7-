# -*- coding: utf-8 -*-
"""
TRADEIFY V2.1
15M MASTER + 5M ENTRY + 3 OPPORTUNITIES + MEMORY + DISCORD

V2.1 fixes:
- 15M uses 900-second candle close; 5M uses 300-second close.
- Never uses an incomplete candle.
- Never creates a new signal from an unchanged candle.
- OPP1/2/3 uses the exact expected 5M candle; no jumping to later candles.
- Gemini uses Chat.send_message() and no tools/function calling.
- Gemini is optional and cannot stop the scanner.
- Active series is hard-limited and a Linux PID lock prevents duplicate processes.
- Memory uses a relative path and atomic writes.
- Weekend OTC: Yahoo is only a public FX proxy. For REAL broker OTC data,
  configure OTC_API_URL. If no fresh data exists, the bot pauses instead
  of inventing a signal.

Optional OTC API:
GET {OTC_API_URL}?symbol=EURUSD&interval=5m&limit=300
Expected JSON: {"candles":[{"timestamp":...,"open":...,"high":...,"low":...,"close":...}]}
timestamp may be Unix seconds/milliseconds or ISO-8601.
"""

import atexit
import json
import os
import sys
import time
import requests
from datetime import datetime, timezone, timedelta
from threading import Thread, Lock
from http.server import HTTPServer, BaseHTTPRequestHandler

import yfinance as yf

try:
    from google import genai
except Exception:
    genai = None

# ---------------- CONFIG ----------------

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite").strip()

PORT = int(os.getenv("PORT", "8080"))
SCAN_SECONDS = max(15, int(os.getenv("SCAN_SECONDS", "60")))
REPORT_SECONDS = max(60, int(os.getenv("REPORT_SECONDS", "1800")))
MEMORY_FILE = os.getenv("MEMORY_FILE", "tradeify_memory.json").strip()
MARKET_MODE = os.getenv("MARKET_MODE", "AUTO").upper()

MAX_OPPORTUNITIES = 3
MAX_ACTIVE_SERIES = max(1, int(os.getenv("MAX_ACTIVE_SERIES", "3")))
SIGNAL_COOLDOWN_SECONDS = max(60, int(os.getenv("SIGNAL_COOLDOWN_SECONDS", "900")))
MAX_NEW_SIGNALS_PER_SCAN = max(1, int(os.getenv("MAX_NEW_SIGNALS_PER_SCAN", "1")))
MIN_HISTORY_FOR_RATE = max(1, int(os.getenv("MIN_HISTORY_FOR_RATE", "10")))

OTC_API_URL = os.getenv("OTC_API_URL", "").strip()
OTC_API_KEY = os.getenv("OTC_API_KEY", "").strip()
REQUEST_TIMEOUT = max(5, int(os.getenv("REQUEST_TIMEOUT", "15")))
YF_RETRIES = max(1, int(os.getenv("YF_RETRIES", "2")))
MAX_DATA_AGE_SECONDS = max(0, int(os.getenv("MAX_DATA_AGE_SECONDS", "1200")))
LOCK_FILE = os.getenv("TRADEIFY_LOCK_FILE", "tradeify_v21.lock").strip()

SYMBOL_MAP = {
    "EUR/USD": "EURUSD=X", "GBP/USD": "GBPUSD=X", "USD/JPY": "JPY=X",
    "AUD/USD": "AUDUSD=X", "USD/CHF": "CHF=X", "USD/CAD": "CAD=X",
    "NZD/USD": "NZDUSD=X", "EUR/JPY": "EURJPY=X",
}
SYMBOLS_ENV = os.getenv("SYMBOLS", "").strip()
SYMBOLS = [s.strip() for s in SYMBOLS_ENV.split(",") if s.strip() in SYMBOL_MAP] if SYMBOLS_ENV else list(SYMBOL_MAP)
if not SYMBOLS:
    SYMBOLS = list(SYMBOL_MAP)

TF5 = 300
TF15 = 900
THAI_TZ = timezone(timedelta(hours=7))

LOCK = Lock()
HISTORICAL_MEMORY = []
ACTIVE_SERIES = []
SENT_SIGNALS = {}
LAST_CLOSED = {"5m": {}, "15m": {}}
LAST_STALE_LOG = {}
STATS = {
    "signals": 0, "wins": 0, "losses": 0, "draws": 0,
    "series_completed": 0, "series_wins": 0, "series_full_loss": 0,
}
AI_CLIENT = None
AI_CHAT = None
LOCK_HANDLE = None

# ---------------- TIME / LOG ----------------

def now_thai():
    return datetime.now(timezone.utc).astimezone(THAI_TZ)

def thai_text(dt=None):
    return (dt or now_thai()).strftime("%Y-%m-%d %H:%M:%S")

def thai_hm(dt=None):
    return (dt or now_thai()).strftime("%H:%M")

def mode_now():
    if MARKET_MODE in ("LIVE", "OTC"):
        return MARKET_MODE
    return "OTC" if now_thai().weekday() >= 5 else "LIVE"

def utc_to_thai(ts):
    return datetime.fromtimestamp(float(ts), timezone.utc).astimezone(THAI_TZ)

def log(msg):
    print(f"[{thai_text()}] {msg}", flush=True)

def interval_seconds(interval):
    return TF5 if interval == "5m" else TF15 if interval == "15m" else 0

# ---------------- PROCESS LOCK ----------------

def acquire_process_lock():
    global LOCK_HANDLE
    try:
        import fcntl
        LOCK_HANDLE = open(LOCK_FILE, "a+", encoding="utf-8")
        fcntl.flock(LOCK_HANDLE.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        LOCK_HANDLE.seek(0)
        LOCK_HANDLE.truncate()
        LOCK_HANDLE.write(str(os.getpid()))
        LOCK_HANDLE.flush()
        atexit.register(release_process_lock)
        log(f"Process lock acquired: {LOCK_FILE} pid={os.getpid()}")
        return True
    except BlockingIOError:
        log(f"ABORT: another TRADEIFY process owns {LOCK_FILE}")
        return False
    except Exception as e:
        log(f"Process lock error: {e}")
        return False

def release_process_lock():
    global LOCK_HANDLE
    if LOCK_HANDLE is None:
        return
    try:
        import fcntl
        fcntl.flock(LOCK_HANDLE.fileno(), fcntl.LOCK_UN)
    except Exception:
        pass
    try:
        LOCK_HANDLE.close()
    except Exception:
        pass
    LOCK_HANDLE = None

# ---------------- DISCORD ----------------

def send_discord(message):
    if not DISCORD_WEBHOOK_URL:
        log("Discord: webhook not configured")
        return False
    try:
        r = requests.post(DISCORD_WEBHOOK_URL, json={"content": message[:1900]}, timeout=10)
        if r.status_code in (200, 204):
            return True
        log(f"Discord HTTP {r.status_code}: {r.text[:200]}")
    except Exception as e:
        log(f"Discord error: {e}")
    return False

# ---------------- GEMINI ----------------

def init_gemini():
    global AI_CLIENT, AI_CHAT
    if genai is None or not GEMINI_API_KEY:
        log("Gemini OFF")
        return
    try:
        AI_CLIENT = genai.Client(api_key=GEMINI_API_KEY)
        AI_CHAT = AI_CLIENT.chats.create(model=GEMINI_MODEL)
        log(f"Gemini ready model={GEMINI_MODEL}")
    except Exception as e:
        AI_CLIENT = AI_CHAT = None
        log(f"Gemini init failed: {e}")

def ai_comment(signal):
    if AI_CHAT is None:
        return "AI: OFF"
    prompt = (
        "à¸à¸­à¸à¸ à¸²à¸©à¸²à¹à¸à¸¢à¹à¸¡à¹à¹à¸à¸´à¸ 2 à¸à¸£à¸£à¸à¸±à¸ à¹à¸«à¹à¹à¸à¹à¸à¹à¸à¸µà¸¢à¸ market-context comment "
        "à¸«à¹à¸²à¸¡à¸£à¸±à¸à¸à¸£à¸°à¸à¸±à¸à¸à¸³à¹à¸£à¹à¸¥à¸°à¸«à¹à¸²à¸¡à¸­à¹à¸²à¸à¸§à¹à¸²à¹à¸à¹à¸à¸à¸³à¹à¸à¸°à¸à¸³à¸à¸²à¸à¸à¸²à¸£à¹à¸à¸´à¸ "
        f"Pair={signal['symbol']}; Mode={signal['mode']}; "
        f"Direction={signal['decision']}; 15M_RSI={signal['rsi15']:.1f}; "
        f"5M_RSI={signal['rsi5']:.1f}; Zone={signal['zone_state']}."
    )
    try:
        response = AI_CHAT.send_message(prompt)
        text = getattr(response, "text", None)
        return text.strip()[:400] if text else "AI: no response"
    except Exception as e:
        return f"AI unavailable: {str(e)[:100]}"

# ---------------- MEMORY ----------------

def load_memory():
    global HISTORICAL_MEMORY
    if not os.path.exists(MEMORY_FILE):
        HISTORICAL_MEMORY = []
        log(f"Memory new: {MEMORY_FILE}")
        return
    try:
        with open(MEMORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        HISTORICAL_MEMORY = data if isinstance(data, list) else data.get("history", []) if isinstance(data, dict) else []
        if not isinstance(HISTORICAL_MEMORY, list):
            HISTORICAL_MEMORY = []
        log(f"Memory loaded: {len(HISTORICAL_MEMORY)} records")
    except Exception as e:
        log(f"Memory load error: {e}")
        try:
            backup = f"{MEMORY_FILE}.corrupt.{int(time.time())}"
            os.replace(MEMORY_FILE, backup)
            log(f"Corrupt memory backed up: {backup}")
        except Exception:
            pass
        HISTORICAL_MEMORY = []

def save_memory():
    try:
        os.makedirs(os.path.dirname(os.path.abspath(MEMORY_FILE)), exist_ok=True)
        tmp = MEMORY_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(HISTORICAL_MEMORY, f, ensure_ascii=False, indent=2)
        os.replace(tmp, MEMORY_FILE)
    except Exception as e:
        log(f"Memory save error: {e}")

# ---------------- DATA ----------------

def parse_timestamp(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        v = float(value)
        return v / 1000 if v > 10_000_000_000 else v
    text = str(value).strip()
    try:
        return float(text)
    except Exception:
        pass
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except Exception:
        return None

def normalize_candle(raw):
    if not isinstance(raw, dict):
        return None
    ts = parse_timestamp(raw.get("timestamp", raw.get("time", raw.get("datetime", raw.get("date")))))
    if ts is None:
        return None
    def num(*keys):
        for k in keys:
            if raw.get(k) is not None:
                try:
                    return float(raw[k])
                except Exception:
                    return None
        return None
    o, h, l, c = num("open", "Open", "o"), num("high", "High", "h"), num("low", "Low", "l"), num("close", "Close", "c")
    if None in (o, h, l, c) or h < max(o, c) or l > min(o, c):
        return None
    return {
        "datetime": datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "timestamp": ts, "open": o, "high": h, "low": l, "close": c,
    }

def normalize_otc_response(data):
    raw = data.get("candles") or data.get("data") or data.get("results") or data.get("quotes") if isinstance(data, dict) else data
    if not isinstance(raw, list):
        return []
    out = {}
    for item in raw:
        c = normalize_candle(item)
        if c:
            out[c["timestamp"]] = c
    return [out[k] for k in sorted(out)]

def clean_dataframe(df):
    if df is None or df.empty:
        return None
    try:
        df = df.copy()
        if getattr(df.index, "tz", None) is not None:
            df.index = df.index.tz_convert("UTC").tz_localize(None)
        return df.dropna(subset=["Open", "High", "Low", "Close"])
    except Exception:
        return df

def get_otc_candles(symbol, interval, limit=300):
    if not OTC_API_URL:
        return []
    params = {"symbol": symbol.replace("/", ""), "interval": interval, "limit": limit}
    if OTC_API_KEY:
        params["apikey"] = OTC_API_KEY
    try:
        r = requests.get(OTC_API_URL, params=params, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        candles = normalize_otc_response(r.json())
        if candles:
            return candles
        log(f"OTC provider: no valid candles for {symbol} {interval}")
    except Exception as e:
        log(f"OTC provider error {symbol} {interval}: {e}")
    return []

def get_yahoo_candles(symbol, interval, period):
    ticker_symbol = SYMBOL_MAP.get(symbol, symbol)
    for attempt in range(1, YF_RETRIES + 1):
        try:
            df = yf.Ticker(ticker_symbol).history(
                period=period, interval=interval,
                auto_adjust=False, prepost=False
            )
            df = clean_dataframe(df)
            if df is None or len(df) < 10:
                if attempt < YF_RETRIES:
                    time.sleep(1)
                continue
            out = []
            for idx, row in df.iterrows():
                ts = idx.to_pydatetime()
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                ts = ts.astimezone(timezone.utc)
                out.append({
                    "datetime": ts.strftime("%Y-%m-%d %H:%M:%S"),
                    "timestamp": ts.timestamp(),
                    "open": float(row["Open"]), "high": float(row["High"]),
                    "low": float(row["Low"]), "close": float(row["Close"]),
                })
            return out
        except Exception as e:
            log(f"Yahoo {symbol} {interval} attempt={attempt}: {e}")
            if attempt < YF_RETRIES:
                time.sleep(1)
    return []

def get_candles(symbol, interval, period="5d", limit=300):
    if mode_now() == "OTC" and OTC_API_URL:
        otc = get_otc_candles(symbol, interval, limit)
        if otc:
            return otc
        log(f"{symbol} {interval}: OTC empty -> Yahoo proxy fallback")
    return get_yahoo_candles(symbol, interval, period)

def is_closed(candle, interval):
    return candle["timestamp"] + interval_seconds(interval) <= time.time()

def closed_only(candles, interval):
    return [c for c in candles if is_closed(c, interval)]

def latest_closed(candles, interval):
    c = closed_only(candles, interval)
    return c[-1] if c else None

def five_min_age(candle):
    return max(0, time.time() - (candle["timestamp"] + TF5))

def fresh_5m(candle):
    if candle is None:
        return False
    return MAX_DATA_AGE_SECONDS <= 0 or five_min_age(candle) <= MAX_DATA_AGE_SECONDS

def is_new_closed(symbol, interval, candle):
    if candle is None:
        return False
    with LOCK:
        old = LAST_CLOSED[interval].get(symbol)
    return old is None or candle["timestamp"] > old

def mark_closed(symbol, interval, candle):
    if candle:
        with LOCK:
            LAST_CLOSED[interval][symbol] = candle["timestamp"]

def stale_log(symbol, interval, candle):
    stamp = candle["timestamp"] if candle else None
    key = (symbol, interval, stamp)
    if time.time() - LAST_STALE_LOG.get(key, 0) < 300:
        return
    LAST_STALE_LOG[key] = time.time()
    if candle:
        log(f"{symbol} {interval}: STALE DATA last={candle['datetime']} UTC age={five_min_age(candle)/60:.1f}m")
    else:
        log(f"{symbol} {interval}: NO CLOSED CANDLE")

# ---------------- INDICATORS ----------------

def ema(values, period):
    if len(values) < period:
        return None
    k = 2 / (period + 1)
    value = sum(values[:period]) / period
    for x in values[period:]:
        value = (x - value) * k + value
    return value

def rsi_wilder(values, period=14):
    if len(values) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(values)):
        d = values[i] - values[i - 1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    ag, al = sum(gains[:period]) / period, sum(losses[:period]) / period
    for i in range(period, len(gains)):
        ag = ((ag * (period - 1)) + gains[i]) / period
        al = ((al * (period - 1)) + losses[i]) / period
    if al == 0:
        return 100.0
    rs = ag / al
    return 100 - 100 / (1 + rs)

def atr(candles, period=14):
    if len(candles) < period + 1:
        return None
    trs = []
    for i in range(1, len(candles)):
        c, p = candles[i], candles[i - 1]
        trs.append(max(c["high"] - c["low"], abs(c["high"] - p["close"]), abs(c["low"] - p["close"])))
    return sum(trs[-period:]) / period

def candle_features(c0, c1):
    body = abs(c0["close"] - c0["open"])
    rng = max(c0["high"] - c0["low"], 1e-12)
    upper = c0["high"] - max(c0["open"], c0["close"])
    lower = min(c0["open"], c0["close"]) - c0["low"]
    ratio = body / rng
    return {
        "strong_bull": c0["close"] > c0["open"] and ratio >= .65,
        "strong_bear": c0["close"] < c0["open"] and ratio >= .65,
        "hammer": lower >= body * 2 and upper <= rng * .25 and ratio <= .45,
        "shooting_star": upper >= body * 2 and lower <= rng * .25 and ratio <= .45,
        "bull_engulf": c0["close"] > c0["open"] and c1["close"] < c1["open"] and c0["open"] <= c1["close"] and c0["close"] >= c1["open"] and body > abs(c1["close"] - c1["open"]),
        "bear_engulf": c0["close"] < c0["open"] and c1["close"] > c1["open"] and c0["open"] >= c1["close"] and c0["close"] <= c1["open"] and body > abs(c1["close"] - c1["open"]),
    }

# ---------------- ZONES ----------------

def build_zones(candles, lookback=240):
    if len(candles) < 30:
        return []
    data = candles[-lookback:]
    zones = []
    for i in range(2, len(data) - 2):
        h, l = data[i]["high"], data[i]["low"]
        if all(h >= data[j]["high"] for j in range(i - 2, i + 3) if j != i):
            zones.append({"type": "RESISTANCE", "price": h, "timestamp": data[i]["timestamp"]})
        if all(l <= data[j]["low"] for j in range(i - 2, i + 3) if j != i):
            zones.append({"type": "SUPPORT", "price": l, "timestamp": data[i]["timestamp"]})
    return zones

def zone_analysis(candles, price, direction):
    a = atr(candles, 14)
    if not a or a <= 0:
        return {"state": "NONE", "score": 0, "level": None}
    zones = build_zones(candles)
    tol = a * .35
    candidates = [(abs(price - z["price"]), z) for z in zones if abs(price - z["price"]) <= tol]
    if not candidates:
        return {"state": "NONE", "score": 0, "level": None}
    candidates.sort(key=lambda x: x[0])
    dist, nearest = candidates[0]
    recent = candles[-12:]
    above = any(c["close"] > nearest["price"] + tol * .15 for c in recent[:-2])
    below = any(c["close"] < nearest["price"] - tol * .15 for c in recent[:-2])
    state, score = nearest["type"], 10
    if nearest["type"] == "RESISTANCE":
        if direction == "PUT":
            score += 15
        if above and price < nearest["price"]:
            state, score = "FLIPPED_RESISTANCE", score + 20
    if nearest["type"] == "SUPPORT":
        if direction == "CALL":
            score += 15
        if below and price > nearest["price"]:
            state, score = "FLIPPED_SUPPORT", score + 20
    if dist <= tol * .5:
        score += 10
    return {"state": state, "score": min(score, 45), "level": nearest["price"]}

# ---------------- 15M MASTER ----------------

def analyze_15m(candles):
    if len(candles) < 80:
        return None
    c0, c1 = candles[-1], candles[-2]
    closes = [c["close"] for c in candles]
    e20, e50, rsi, a = ema(closes, 20), ema(closes, 50), rsi_wilder(closes), atr(candles)
    if None in (e20, e50, rsi, a):
        return None
    price = c0["close"]
    p = candle_features(c0, c1)
    call = put = 0
    rc, rp = [], []
    if price > e20: call += 15; rc.append("price>EMA20")
    else: put += 15; rp.append("price<EMA20")
    if price > e50: call += 20; rc.append("price>EMA50")
    else: put += 20; rp.append("price<EMA50")
    if e20 > e50: call += 10; rc.append("EMA20>EMA50")
    else: put += 10; rp.append("EMA20<EMA50")
    if 50 <= rsi <= 68: call += 8; rc.append(f"RSI={rsi:.1f}")
    if 32 <= rsi < 50: put += 8; rp.append(f"RSI={rsi:.1f}")
    if p["strong_bull"] or p["hammer"] or p["bull_engulf"]: call += 12; rc.append("bullish-candle")
    if p["strong_bear"] or p["shooting_star"] or p["bear_engulf"]: put += 12; rp.append("bearish-candle")
    zc, zp = zone_analysis(candles, price, "CALL"), zone_analysis(candles, price, "PUT")
    call += zc["score"]; put += zp["score"]
    if zc["score"] > 10: rc.append(f"ZONE={zc['state']}")
    if zp["score"] > 10: rp.append(f"ZONE={zp['state']}")
    if call > put and call >= 58:
        decision, strength, reasons, zone = "CALL", min(99, call), rc, zc
    elif put > call and put >= 58:
        decision, strength, reasons, zone = "PUT", min(99, put), rp, zp
    else:
        return None
    return {
        "decision": decision, "setup_strength": strength, "price": price, "atr": a,
        "rsi": rsi, "zone_state": zone["state"], "zone_level": zone["level"],
        "reasons": " | ".join(reasons), "candle_time": c0["datetime"], "candle_ts": c0["timestamp"],
    }

# ---------------- 5M CONTEXT ----------------

def analyze_5m(candles, master_direction):
    if len(candles) < 70:
        return None
    c0, c1 = candles[-1], candles[-2]
    closes = [c["close"] for c in candles]
    e20, e50, rsi = ema(closes, 20), ema(closes, 50), rsi_wilder(closes)
    if None in (e20, e50, rsi):
        return None
    p = candle_features(c0, c1)
    call = put = 0
    rc, rp = [], []
    if c0["close"] > e20: call += 15; rc.append("5M>EMA20")
    else: put += 15; rp.append("5M<EMA20")
    if c0["close"] > e50: call += 10; rc.append("5M>EMA50")
    else: put += 10; rp.append("5M<EMA50")
    if rsi > 50: call += 8; rc.append(f"RSI={rsi:.1f}")
    else: put += 8; rp.append(f"RSI={rsi:.1f}")
    if p["strong_bull"] or p["bull_engulf"] or p["hammer"]: call += 15; rc.append("bullish-candle")
    if p["strong_bear"] or p["bear_engulf"] or p["shooting_star"]: put += 15; rp.append("bearish-candle")
    if master_direction == "CALL": call += 12; rc.append("15M-master-CALL")
    else: put += 12; rp.append("15M-master-PUT")
    if call > put: decision, score, reasons = "CALL", min(99, call), rc
    elif put > call: decision, score, reasons = "PUT", min(99, put), rp
    else: decision, score, reasons = "UNKNOWN", 50, ["balanced"]
    return {"decision": decision, "score": score, "rsi": rsi, "candle_time": c0["datetime"], "candle_ts": c0["timestamp"], "reasons": " | ".join(reasons)}

# ---------------- HISTORY ----------------

def setup_signature(symbol, mode, direction, zone_state):
    return symbol, mode, direction, zone_state

def historical_stats(signature):
    with LOCK:
        records = [
            r for r in HISTORICAL_MEMORY
            if r.get("type") == "SERIES"
            and setup_signature(r.get("symbol"), r.get("mode"), r.get("decision"), r.get("zone_state")) == signature
        ]
    total = len(records)
    sw = sum(r.get("status") == "SERIES_WIN" for r in records)
    fw = sum(r.get("first_opportunity_result") == "WIN" for r in records)
    if total >= MIN_HISTORY_FOR_RATE:
        return {"samples": total, "series_win_rate": round(sw / total * 100, 1), "first_win_rate": round(fw / total * 100, 1), "confidence": "USABLE"}
    return {"samples": total, "series_win_rate": None, "first_win_rate": None, "confidence": "INSUFFICIENT_DATA"}

# ---------------- SERIES / COOLDOWN ----------------

def active_series_count():
    with LOCK:
        return len(ACTIVE_SERIES)

def has_active_series(symbol):
    with LOCK:
        return any(s["symbol"] == symbol for s in ACTIVE_SERIES)

def cleanup_signal_cache():
    cutoff = time.time() - SIGNAL_COOLDOWN_SECONDS * 3
    with LOCK:
        for key in [k for k, ts in SENT_SIGNALS.items() if ts < cutoff]:
            SENT_SIGNALS.pop(key, None)

def signal_recently_sent(key):
    with LOCK:
        ts = SENT_SIGNALS.get(key)
    return ts is not None and time.time() - ts < SIGNAL_COOLDOWN_SECONDS

def remember_signal(key):
    with LOCK:
        SENT_SIGNALS[key] = time.time()

# ---------------- SIGNAL ----------------

def scan_symbol(symbol):
    c15 = closed_only(get_candles(symbol, "15m", "5d", 300), "15m")
    if len(c15) < 80:
        return None
    master_candle = c15[-1]

    # Important: first observation is seeded, not emitted as a signal.
    if not is_new_closed(symbol, "15m", master_candle):
        return None

    c5 = closed_only(get_candles(symbol, "5m", "5d", 500), "5m")
    if len(c5) < 70:
        return None
    latest5 = c5[-1]

    if not fresh_5m(latest5):
        stale_log(symbol, "5m", latest5)
        return None

    if not is_new_closed(symbol, "5m", latest5):
        return None

    master = analyze_15m(c15)
    if not master:
        return None

    timing = analyze_5m(c5, master["decision"])
    if not timing:
        return None

    context = "5M_CONFIRM" if timing["decision"] == master["decision"] else "5M_UNKNOWN" if timing["decision"] == "UNKNOWN" else "5M_PULLBACK"
    if master["setup_strength"] < 62:
        return None

    mode = mode_now()
    hist = historical_stats(setup_signature(symbol, mode, master["decision"], master["zone_state"]))
    next_open_ts = latest5["timestamp"] + TF5
    next_close_ts = next_open_ts + TF5

    # Seed only after all checks pass.
    mark_closed(symbol, "15m", master_candle)
    mark_closed(symbol, "5m", latest5)

    return {
        "symbol": symbol, "mode": mode, "decision": master["decision"],
        "setup_strength": round(master["setup_strength"], 1),
        "rsi15": master["rsi"], "rsi5": timing["rsi"],
        "entry_score": timing["score"], "entry_context": context,
        "zone_state": master["zone_state"], "zone_level": master["zone_level"],
        "price": master["price"], "atr": master["atr"],
        "reasons15": master["reasons"], "reasons5": timing["reasons"],
        "signal_candle15": master["candle_time"], "last_closed_5m": latest5["datetime"],
        "signal_ts": time.time(), "next_open_ts": next_open_ts, "next_close_ts": next_close_ts,
        "history": hist, "created_at": thai_text(),
    }

def create_series(signal):
    with LOCK:
        if len(ACTIVE_SERIES) >= MAX_ACTIVE_SERIES or any(s["symbol"] == signal["symbol"] for s in ACTIVE_SERIES):
            return None
        tracker = {
            "type": "ACTIVE_SERIES",
            "series_id": f"{signal['symbol'].replace('/','')}_{int(signal['signal_ts'])}",
            "symbol": signal["symbol"], "mode": signal["mode"],
            "master_direction": signal["decision"], "setup_strength": signal["setup_strength"],
            "entry_context": signal["entry_context"], "zone_state": signal["zone_state"],
            "zone_level": signal["zone_level"], "signal_time": signal["created_at"],
            "signal_ts": signal["signal_ts"], "next_entry_ts": signal["next_open_ts"],
            "next_close_ts": signal["next_close_ts"], "opportunity": 1,
            "wins": 0, "losses": 0, "draws": 0, "first_opportunity_result": None,
            "processed_5m": [], "max_mfe": 0.0, "max_mae": 0.0,
        }
        ACTIVE_SERIES.append(tracker)
        STATS["signals"] += 1
        return tracker

# ---------------- OPP ENGINE ----------------

def exact_entry_candle(candles, entry_ts):
    # Exact timestamp only: no >= fallback.
    for c in candles:
        if abs(c["timestamp"] - entry_ts) < 1:
            return c if is_closed(c, "5m") else None
    return None

def evaluate_opportunity(tracker):
    candles = closed_only(get_candles(tracker["symbol"], "5m", "2d", 200), "5m")
    if len(candles) < 20:
        return None
    c = exact_entry_candle(candles, tracker["next_entry_ts"])
    if c is None or c["datetime"] in tracker["processed_5m"]:
        return None

    entry, close, direction = c["open"], c["close"], tracker["master_direction"]
    if close > entry:
        result = "WIN" if direction == "CALL" else "LOSS"
    elif close < entry:
        result = "WIN" if direction == "PUT" else "LOSS"
    else:
        result = "DRAW"

    if direction == "CALL":
        mfe, mae = c["high"] - entry, entry - c["low"]
    else:
        mfe, mae = entry - c["low"], c["high"] - entry

    return {
        "result": result, "candle": c, "entry_price": entry, "close_price": close,
        "mfe": max(0, mfe), "mae": max(0, mae),
    }

def record_opportunity(tracker, outcome):
    tracker["processed_5m"].append(outcome["candle"]["datetime"])
    tracker["max_mfe"] = max(tracker["max_mfe"], outcome["mfe"])
    tracker["max_mae"] = max(tracker["max_mae"], outcome["mae"])
    result = outcome["result"]
    with LOCK:
        if result == "WIN":
            tracker["wins"] += 1; STATS["wins"] += 1
        elif result == "LOSS":
            tracker["losses"] += 1; STATS["losses"] += 1
        else:
            tracker["draws"] += 1; STATS["draws"] += 1
    if tracker["opportunity"] == 1:
        tracker["first_opportunity_result"] = result
    return result

# ---------------- FINALIZE ----------------

def finalize_series(tracker, status):
    record = {
        "type": "SERIES", "series_id": tracker["series_id"], "symbol": tracker["symbol"],
        "mode": tracker["mode"], "decision": tracker["master_direction"],
        "setup_strength": tracker["setup_strength"], "entry_context": tracker["entry_context"],
        "zone_state": tracker["zone_state"], "zone_level": tracker["zone_level"],
        "signal_time": tracker["signal_time"], "status": status,
        "wins": tracker["wins"], "losses": tracker["losses"], "draws": tracker["draws"],
        "opportunities_used": tracker["opportunity"],
        "first_opportunity_result": tracker["first_opportunity_result"],
        "max_mfe": tracker["max_mfe"], "max_mae": tracker["max_mae"], "recorded_at": thai_text(),
    }
    with LOCK:
        HISTORICAL_MEMORY.append(record)
        STATS["series_completed"] += 1
        if status == "SERIES_WIN": STATS["series_wins"] += 1
        if status == "FULL_LOSS": STATS["series_full_loss"] += 1
    save_memory()
    h = historical_stats(setup_signature(tracker["symbol"], tracker["mode"], tracker["master_direction"], tracker["zone_state"]))
    rate = f"{h['series_win_rate']}%" if h["series_win_rate"] is not None else "INSUFFICIENT_DATA"
    icon = "ð¢" if status == "SERIES_WIN" else "ð´"
    send_discord(
        f"{icon} **[TRADEIFY SERIES COMPLETE V2.1]**\n"
        f"ââââââââââââââââââââ\n"
        f"ð± à¸à¸¹à¹: **{tracker['symbol']}**\nð Mode: **{tracker['mode']}**\n"
        f"ð Direction: **{tracker['master_direction']}**\nð Status: **{status}**\n"
        f"ð¯ WIN: **{tracker['wins']}**\nâ LOSS: **{tracker['losses']}**\nâ DRAW: **{tracker['draws']}**\n"
        f"ð¢ Opportunities: **{tracker['opportunity']}/3**\n"
        f"ð Historical Series Win Rate: **{rate}**\nð Samples: **{h['samples']}**\n"
        f"ð§­ Zone: **{tracker['zone_state']}**\nð à¹à¸§à¸¥à¸²à¹à¸à¸¢: **{thai_text()}**"
    )

# ---------------- TRACKER ----------------

def tracker_loop():
    while True:
        try:
            with LOCK:
                trackers = list(ACTIVE_SERIES)
            for tracker in trackers:
                outcome = evaluate_opportunity(tracker)
                if outcome is None:
                    continue
                result = record_opportunity(tracker, outcome)
                c = outcome["candle"]
                icon = "ð¢" if result == "WIN" else "ð´" if result == "LOSS" else "ð¡"
                send_discord(
                    f"{icon} **[TRADEIFY 5M RESULT V2.1]**\n"
                    f"ââââââââââââââââââââ\nð± **{tracker['symbol']}**\n"
                    f"ð Mode: **{tracker['mode']}**\nð Master: **{tracker['master_direction']}**\n"
                    f"ð¯ OPP: **{tracker['opportunity']}/3**\nð Result: **{result}**\n"
                    f"ð° Entry: **{outcome['entry_price']:.8f}**\nð Close: **{outcome['close_price']:.8f}**\n"
                    f"ð Candle UTC: **{c['datetime']}**\nð¹ð­ à¹à¸§à¸¥à¸²à¹à¸à¹à¸: **{thai_text()}**\n"
                    f"ð MFE: **{outcome['mfe']:.8f}**\nð MAE: **{outcome['mae']:.8f}**\n"
                    f"â ï¸ à¸à¸±à¸à¸à¸¥à¸«à¸¥à¸±à¸à¹à¸à¹à¸ 5M à¸à¸´à¸à¹à¸¥à¹à¸§à¹à¸à¹à¸²à¸à¸±à¹à¸"
                )
                if result == "WIN" or tracker["opportunity"] >= MAX_OPPORTUNITIES:
                    status = "SERIES_WIN" if result == "WIN" else "FULL_LOSS"
                    finalize_series(tracker, status)
                    with LOCK:
                        if tracker in ACTIVE_SERIES:
                            ACTIVE_SERIES.remove(tracker)
                else:
                    tracker["opportunity"] += 1
                    tracker["next_entry_ts"] = c["timestamp"] + TF5
                    tracker["next_close_ts"] = tracker["next_entry_ts"] + TF5
                    dt1, dt2 = utc_to_thai(tracker["next_entry_ts"]), utc_to_thai(tracker["next_close_ts"])
                    send_discord(
                        f"ð **[TRADEIFY NEXT OPPORTUNITY V2.1]**\n"
                        f"ð± {tracker['symbol']}\nð Mode: {tracker['mode']}\n"
                        f"ð Direction à¹à¸à¸´à¸¡: **{tracker['master_direction']}**\n"
                        f"ð¯ OPP: **{tracker['opportunity']}/3**\n"
                        f"â° à¹à¸à¹à¸²: **{thai_hm(dt1)} à¸.**\nð à¸à¸´à¸: **{thai_hm(dt2)} à¸.**\n"
                        f"â ï¸ exact 5M candle à¹à¸à¹à¸²à¸à¸±à¹à¸"
                    )
        except Exception as e:
            log(f"Tracker error: {e}")
        time.sleep(10)

# ---------------- SCANNER ----------------

def scanner_loop():
    while True:
        try:
            cleanup_signal_cache()
            if active_series_count() >= MAX_ACTIVE_SERIES:
                log(f"Scanner paused: active series {active_series_count()}/{MAX_ACTIVE_SERIES}")
                time.sleep(SCAN_SECONDS)
                continue

            best = []
            for symbol in SYMBOLS:
                if has_active_series(symbol):
                    continue
                try:
                    s = scan_symbol(symbol)
                    if s:
                        best.append(s)
                except Exception as e:
                    log(f"Scanner {symbol}: {e}")

            best.sort(key=lambda x: x["setup_strength"], reverse=True)
            created = 0
            for s in best:
                if active_series_count() >= MAX_ACTIVE_SERIES:
                    break
                key = (s["symbol"], s["mode"], s["decision"], s["signal_candle15"])
                if signal_recently_sent(key) or has_active_series(s["symbol"]):
                    continue
                remember_signal(key)
                tracker = create_series(s)
                if tracker is None:
                    continue

                h = s["history"]
                sr = f"{h['series_win_rate']}%" if h["series_win_rate"] is not None else "INSUFFICIENT_DATA"
                fr = f"{h['first_win_rate']}%" if h["first_win_rate"] is not None else "INSUFFICIENT_DATA"
                d1, d2 = utc_to_thai(s["next_open_ts"]), utc_to_thai(s["next_close_ts"])
                ai = ai_comment(s)
                icon = "ð¢" if s["decision"] == "CALL" else "ð´"

                send_discord(
                    f"ð¨ **[TRADEIFY SIGNAL V2.1]**\nââââââââââââââââââââ\n"
                    f"â±ï¸ TF: **5M**\nð± à¸à¸¹à¹: **{s['symbol']}**\n\n"
                    f"â­ï¸ **à¹à¸à¸·à¸­à¸à¹à¸§à¸¥à¸² {thai_hm()} à¸.** â­ï¸\nð¹ð­ à¹à¸§à¸¥à¸²à¹à¸à¸¢\n\n"
                    f"ð Mode: **{s['mode']}**\nð Direction: **{s['decision']}** {icon}\n"
                    f"ð¢ **à¹à¸à¸£à¸µà¸¢à¸¡à¸à¸·à¹à¸­à¹à¸à¹à¸à¸«à¸à¹à¸²**\n\n"
                    f"ð SETUP STRENGTH: **{s['setup_strength']}/100**\n"
                    f"ð 15M RSI: **{s['rsi15']:.1f}**\nð 5M RSI: **{s['rsi5']:.1f}**\n"
                    f"â±ï¸ 5M Context: **{s['entry_context']}**\nð§­ Zone: **{s['zone_state']}**\n\n"
                    f"ð HISTORICAL\nâ¢ Samples: **{h['samples']}**\nâ¢ Series Win Rate: **{sr}**\nâ¢ First Entry Win Rate: **{fr}**\n\n"
                    f"ð¯ Opportunity: **1/3**\nâ° à¹à¸à¹à¸²à¹à¸à¹à¸: **{thai_hm(d1)} à¸.**\nð à¸à¸´à¸à¹à¸à¹à¸: **{thai_hm(d2)} à¸.**\n\n"
                    f"ð 15M: {s['reasons15']}\nð 5M: {s['reasons5']}\nð¤ {ai}\n\n"
                    f"â ï¸ **à¸à¸¥ WIN/LOSS à¸à¸±à¸à¸ªà¸´à¸à¸«à¸¥à¸±à¸à¹à¸à¹à¸ 5M à¸à¸´à¸à¹à¸à¹à¸²à¸à¸±à¹à¸**\n"
                    f"ð Signal: **{s['created_at']}**\nð Active Series: **{active_series_count()}/{MAX_ACTIVE_SERIES}**"
                )
                log(f"NEW {s['symbol']} {s['decision']} mode={s['mode']} strength={s['setup_strength']} active={active_series_count()}/{MAX_ACTIVE_SERIES}")
                created += 1
                if created >= MAX_NEW_SIGNALS_PER_SCAN:
                    break
        except Exception as e:
            log(f"Scanner loop error: {e}")
        time.sleep(SCAN_SECONDS)

# ---------------- STATUS / HEALTH ----------------

def calculate_stats():
    with LOCK:
        total = STATS["wins"] + STATS["losses"] + STATS["draws"]
        return {
            **STATS,
            "total_opportunities": total,
            "win_rate": round(STATS["wins"] / total * 100, 2) if total else None,
            "active_series": len(ACTIVE_SERIES),
            "memory_records": len(HISTORICAL_MEMORY),
        }

def latest_snapshot():
    with LOCK:
        return {
            symbol: {
                "5m": LAST_CLOSED["5m"].get(symbol),
                "15m": LAST_CLOSED["15m"].get(symbol),
            }
            for symbol in SYMBOLS
        }

def reporter_loop():
    while True:
        try:
            s = calculate_stats()
            wr = f"{s['win_rate']}%" if s["win_rate"] is not None else "NO_DATA"
            lines = []
            for symbol, d in latest_snapshot().items():
                t = datetime.fromtimestamp(d["5m"], timezone.utc).strftime("%H:%M:%S UTC") if d["5m"] else "-"
                lines.append(f"{symbol}: 5M={t}")
            send_discord(
                f"ð **[TRADEIFY STATUS V2.1]**\nââââââââââââââââââââ\n"
                f"ð¹ð­ à¹à¸§à¸¥à¸²à¹à¸à¸¢: **{thai_text()}**\nð Mode: **{mode_now()}**\n"
                f"ð£ Signals: **{s['signals']}**\nð¢ WIN: **{s['wins']}**\nð´ LOSS: **{s['losses']}**\nð¡ DRAW: **{s['draws']}**\n"
                f"ð Opportunity Win Rate: **{wr}**\nð Series Complete: **{s['series_completed']}**\n"
                f"ð¢ Series Win: **{s['series_wins']}**\nð´ Full Loss: **{s['series_full_loss']}**\n"
                f"ð Active Series: **{s['active_series']}/{MAX_ACTIVE_SERIES}**\nð¾ Memory: **{s['memory_records']}**\n\n"
                f"ð Latest closed 5M:\n" + "\n".join(lines) +
                f"\n\nð¡ OTC API: **{'CONFIGURED' if OTC_API_URL else 'NOT CONFIGURED'}**"
            )
        except Exception as e:
            log(f"Reporter error: {e}")
        time.sleep(REPORT_SECONDS)

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path not in ("/", "/health", "/status"):
            self.send_response(404); self.end_headers(); return
        s = calculate_stats()
        snap = latest_snapshot()
        body = {
            "status": "running", "version": "V2.1", "pid": os.getpid(),
            "time_thai": thai_text(), "mode": mode_now(), "symbols": SYMBOLS,
            "active_series": s["active_series"], "max_active_series": MAX_ACTIVE_SERIES,
            "memory_records": s["memory_records"], "wins": s["wins"], "losses": s["losses"],
            "draws": s["draws"], "win_rate": s["win_rate"],
            "otc_api_configured": bool(OTC_API_URL),
            "latest_closed_5m": {
                k: datetime.fromtimestamp(v["5m"], timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC") if v["5m"] else None
                for k, v in snap.items()
            },
        }
        raw = json.dumps(body, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)
    def log_message(self, format, *args):
        return

def run_health_server():
    try:
        HTTPServer(("0.0.0.0", PORT), HealthHandler).serve_forever()
    except Exception as e:
        log(f"Health server error: {e}")

# ---------------- STARTUP ----------------

def startup_message():
    send_discord(
        f"ð **[TRADEIFY V2.1 STARTED]**\nââââââââââââââââââââ\n"
        f"ð¹ð­ à¹à¸§à¸¥à¸²à¹à¸à¸¢: **{thai_text()}**\nð Mode: **{mode_now()}**\n"
        f"â±ï¸ Master: **15M closed candle**\nð¯ Entry/Result: **5M closed candle**\n"
        f"ð¢ Opportunities: **1â3**\nð Max Active Series: **{MAX_ACTIVE_SERIES}**\n"
        f"â³ Cooldown: **{SIGNAL_COOLDOWN_SECONDS}s**\nð¾ Memory: **ON**\n"
        f"ð¡ Symbols: **{len(SYMBOLS)}**\nð¤ Gemini: **{GEMINI_MODEL if AI_CHAT else 'OFF'}**\n"
        f"ð¡ OTC API: **{'ON' if OTC_API_URL else 'OFF'}**\n\n"
        f"â ï¸ à¸à¹à¸² OTC API OFF à¸£à¸°à¸à¸à¹à¸à¹ Yahoo public FX proxy à¹à¸¥à¸°à¸à¸°à¹à¸¡à¹à¸ªà¸£à¹à¸²à¸ signal à¸à¸²à¸à¸à¹à¸­à¸¡à¸¹à¸¥à¹à¸à¹à¸²"
    )

def main():
    log("=" * 70)
    log("TRADEIFY 15M + 5M + 3 OPPORTUNITIES â V2.1")
    log("=" * 70)
    log(f"Python process started | PID={os.getpid()}")
    log(f"Mode: {mode_now()}")
    log(f"Memory: {MEMORY_FILE}")
    log(f"Max active series: {MAX_ACTIVE_SERIES}")
    log(f"Symbols: {', '.join(SYMBOLS)}")
    log(f"OTC API: {'CONFIGURED' if OTC_API_URL else 'NOT CONFIGURED'}")

    if not acquire_process_lock():
        sys.exit(1)

    load_memory()
    init_gemini()

    Thread(target=run_health_server, daemon=True).start()
    Thread(target=tracker_loop, daemon=True).start()
    Thread(target=scanner_loop, daemon=True).start()
    Thread(target=reporter_loop, daemon=True).start()

    startup_message()
    log("=" * 70)

    while True:
        try:
            time.sleep(60)
        except KeyboardInterrupt:
            log("Stopping...")
            break

if __name__ == "__main__":
    main()
