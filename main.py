from pathlib import Path

code = r'''# -*- coding: utf-8 -*-
"""
TRADEIFY V2.2
15M MASTER + 5M ENTRY + 3 OPPORTUNITIES + MEMORY + DISCORD + GEMINI
Railway-safe / boot diagnostics / candle synchronization

Major V2.2 fixes:
- Never uses incomplete candles.
- 15M and 5M freshness are both checked.
- First observation seeds candle state; it does not create a signal.
- Signal is created only when BOTH a new closed 15M candle and a new closed 5M
  candle are observed, preserving the original V2.1 behavior but with explicit
  diagnostics.
- OPP1/2/3 uses exact expected 5M candle timestamp; never jumps forward.
- OTC weekend does NOT silently use stale Yahoo proxy data. If MARKET_MODE is
  OTC and OTC_API_URL is missing/unavailable, scanner pauses.
- Yahoo data is allowed in LIVE mode as a public FX proxy.
- Railway PORT is respected.
- Process lock is retained but lock failures are reported clearly.
- Memory writes are atomic and directory-safe.
- Worker threads are isolated; one worker failure cannot kill the process.
- Discord/Gemini are optional and cannot stop scanner startup.
- Health endpoint is available at /health and /status.
- Boot diagnostics make Railway failures obvious.
"""

import atexit
import json
import os
import sys
import time
import traceback
import requests
from datetime import datetime, timezone, timedelta
from threading import Thread, Lock
from http.server import HTTPServer, BaseHTTPRequestHandler

import yfinance as yf

try:
    from google import genai
except Exception:
    genai = None


# ============================================================
# CONFIG
# ============================================================

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
MAX_15M_DATA_AGE_SECONDS = max(0, int(os.getenv("MAX_15M_DATA_AGE_SECONDS", "2400")))

LOCK_FILE = os.getenv("TRADEIFY_LOCK_FILE", "tradeify_v22.lock").strip()
HEALTH_BIND = os.getenv("HEALTH_BIND", "0.0.0.0").strip()

TF5 = 300
TF15 = 900
THAI_TZ = timezone(timedelta(hours=7))

SYMBOL_MAP = {
    "EUR/USD": "EURUSD=X",
    "GBP/USD": "GBPUSD=X",
    "USD/JPY": "JPY=X",
    "AUD/USD": "AUDUSD=X",
    "USD/CHF": "CHF=X",
    "USD/CAD": "CAD=X",
    "NZD/USD": "NZDUSD=X",
    "EUR/JPY": "EURJPY=X",
}

SYMBOLS_ENV = os.getenv("SYMBOLS", "").strip()
SYMBOLS = (
    [s.strip() for s in SYMBOLS_ENV.split(",") if s.strip() in SYMBOL_MAP]
    if SYMBOLS_ENV
    else list(SYMBOL_MAP)
)
if not SYMBOLS:
    SYMBOLS = list(SYMBOL_MAP)


# ============================================================
# GLOBAL STATE
# ============================================================

LOCK = Lock()

HISTORICAL_MEMORY = []
ACTIVE_SERIES = []

SENT_SIGNALS = {}
LAST_CLOSED = {"5m": {}, "15m": {}}
LAST_DATA_STATUS = {}

STATS = {
    "signals": 0,
    "wins": 0,
    "losses": 0,
    "draws": 0,
    "series_completed": 0,
    "series_wins": 0,
    "series_full_loss": 0,
    "scanner_cycles": 0,
    "scanner_errors": 0,
    "tracker_cycles": 0,
    "data_errors": 0,
}

AI_CLIENT = None
AI_CHAT = None
LOCK_HANDLE = None
HEALTH_SERVER = None


# ============================================================
# TIME / LOG
# ============================================================

def now_thai():
    return datetime.now(timezone.utc).astimezone(THAI_TZ)


def thai_text(dt=None):
    return (dt or now_thai()).strftime("%Y-%m-%d %H:%M:%S")


def thai_hm(dt=None):
    return (dt or now_thai()).strftime("%H:%M")


def mode_now():
    if MARKET_MODE in ("LIVE", "OTC"):
        return MARKET_MODE
    # Saturday=5, Sunday=6
    return "OTC" if now_thai().weekday() >= 5 else "LIVE"


def utc_to_thai(ts):
    return datetime.fromtimestamp(float(ts), timezone.utc).astimezone(THAI_TZ)


def interval_seconds(interval):
    return TF5 if interval == "5m" else TF15 if interval == "15m" else 0


def log(msg):
    print(f"[{thai_text()}] {msg}", flush=True)


def boot(step, msg):
    print(f"[BOOT {step}] {msg}", flush=True)


# ============================================================
# PROCESS LOCK
# ============================================================

def acquire_process_lock():
    global LOCK_HANDLE

    try:
        import fcntl

        dirname = os.path.dirname(os.path.abspath(LOCK_FILE))
        if dirname:
            os.makedirs(dirname, exist_ok=True)

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
        log(f"Process lock error: {type(e).__name__}: {e}")
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


# ============================================================
# DISCORD
# ============================================================

def repair_mojibake(value):
    if not isinstance(value, str):
        return value

    markers = ("Ã°", "Ã", "Ã", "Ã¢", "Ã Â¸", "Ã Â¹", "Ã¯Â¸")
    if not any(m in value for m in markers):
        return value

    for encoding in ("latin1", "cp1252"):
        try:
            fixed = value.encode(encoding).decode("utf-8")
            if sum(value.count(m) for m in markers) > sum(
                fixed.count(m) for m in markers
            ):
                return fixed
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass

    return value


def send_discord(message):
    if not DISCORD_WEBHOOK_URL:
        return False

    try:
        message = repair_mojibake(str(message))

        payload = json.dumps(
            {"content": message[:1900]},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")

        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "Accept": "application/json",
        }

        r = requests.post(
            DISCORD_WEBHOOK_URL,
            data=payload,
            headers=headers,
            timeout=10,
        )

        if r.status_code in (200, 204):
            return True

        log(f"Discord HTTP {r.status_code}: {r.text[:200]}")
        return False

    except Exception as e:
        log(f"Discord error: {type(e).__name__}: {e}")
        return False


# ============================================================
# GEMINI
# ============================================================

def init_gemini():
    global AI_CLIENT, AI_CHAT

    if genai is None:
        log("Gemini OFF: google.genai import unavailable")
        return

    if not GEMINI_API_KEY:
        log("Gemini OFF: GEMINI_API_KEY not configured")
        return

    try:
        AI_CLIENT = genai.Client(api_key=GEMINI_API_KEY)
        AI_CHAT = AI_CLIENT.chats.create(model=GEMINI_MODEL)
        log(f"Gemini ready model={GEMINI_MODEL}")
    except Exception as e:
        AI_CLIENT = None
        AI_CHAT = None
        log(f"Gemini init failed: {type(e).__name__}: {e}")


def ai_comment(signal):
    if AI_CHAT is None:
        return "AI: OFF"

    prompt = (
        "ตอบภาษาไทยไม่เกิน 2 บรรทัด เป็น market-context comment เท่านั้น "
        "ห้ามรับประกันกำไร และห้ามอ้างว่าเป็นคำแนะนำทางการเงิน "
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


# ============================================================
# MEMORY
# ============================================================

def load_memory():
    global HISTORICAL_MEMORY

    dirname = os.path.dirname(os.path.abspath(MEMORY_FILE))
    os.makedirs(dirname, exist_ok=True)

    if not os.path.exists(MEMORY_FILE):
        HISTORICAL_MEMORY = []
        log(f"Memory new: {MEMORY_FILE}")
        return

    try:
        with open(MEMORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        if isinstance(data, list):
            HISTORICAL_MEMORY = data
        elif isinstance(data, dict):
            HISTORICAL_MEMORY = data.get("history", [])
        else:
            HISTORICAL_MEMORY = []

        if not isinstance(HISTORICAL_MEMORY, list):
            HISTORICAL_MEMORY = []

        log(f"Memory loaded: {len(HISTORICAL_MEMORY)} records")

    except Exception as e:
        log(f"Memory load error: {type(e).__name__}: {e}")

        try:
            backup = f"{MEMORY_FILE}.corrupt.{int(time.time())}"
            os.replace(MEMORY_FILE, backup)
            log(f"Corrupt memory backed up: {backup}")
        except Exception:
            pass

        HISTORICAL_MEMORY = []


def save_memory():
    try:
        dirname = os.path.dirname(os.path.abspath(MEMORY_FILE))
        os.makedirs(dirname, exist_ok=True)

        tmp = MEMORY_FILE + ".tmp"

        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(
                HISTORICAL_MEMORY,
                f,
                ensure_ascii=False,
                indent=2,
            )

        os.replace(tmp, MEMORY_FILE)

    except Exception as e:
        log(f"Memory save error: {type(e).__name__}: {e}")


# ============================================================
# DATA NORMALIZATION
# ============================================================

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

    ts = parse_timestamp(
        raw.get(
            "timestamp",
            raw.get("time", raw.get("datetime", raw.get("date"))),
        )
    )

    if ts is None:
        return None

    def num(*keys):
        for key in keys:
            if raw.get(key) is not None:
                try:
                    return float(raw[key])
                except Exception:
                    return None
        return None

    o = num("open", "Open", "o")
    h = num("high", "High", "h")
    l = num("low", "Low", "l")
    c = num("close", "Close", "c")

    if None in (o, h, l, c):
        return None

    if h < max(o, c) or l > min(o, c):
        return None

    return {
        "datetime": datetime.fromtimestamp(
            ts, timezone.utc
        ).strftime("%Y-%m-%d %H:%M:%S"),
        "timestamp": ts,
        "open": o,
        "high": h,
        "low": l,
        "close": c,
    }


def normalize_otc_response(data):
    if isinstance(data, dict):
        raw = (
            data.get("candles")
            or data.get("data")
            or data.get("results")
            or data.get("quotes")
        )
    else:
        raw = data

    if not isinstance(raw, list):
        return []

    out = {}

    for item in raw:
        candle = normalize_candle(item)
        if candle:
            out[candle["timestamp"]] = candle

    return [out[k] for k in sorted(out)]


def clean_dataframe(df):
    if df is None or df.empty:
        return None

    try:
        df = df.copy()

        if getattr(df.index, "tz", None) is not None:
            df.index = df.index.tz_convert("UTC").tz_localize(None)

        return df.dropna(
            subset=["Open", "High", "Low", "Close"]
        )

    except Exception:
        return df


# ============================================================
# DATA PROVIDERS
# ============================================================

def get_otc_candles(symbol, interval, limit=300):
    if not OTC_API_URL:
        return []

    params = {
        "symbol": symbol.replace("/", ""),
        "interval": interval,
        "limit": limit,
    }

    if OTC_API_KEY:
        params["apikey"] = OTC_API_KEY

    try:
        r = requests.get(
            OTC_API_URL,
            params=params,
            timeout=REQUEST_TIMEOUT,
        )
        r.raise_for_status()

        candles = normalize_otc_response(r.json())

        if candles:
            return candles

        log(f"OTC provider: no valid candles {symbol} {interval}")

    except Exception as e:
        with LOCK:
            STATS["data_errors"] += 1

        log(
            f"OTC provider error {symbol} {interval}: "
            f"{type(e).__name__}: {e}"
        )

    return []


def get_yahoo_candles(symbol, interval, period):
    ticker_symbol = SYMBOL_MAP.get(symbol, symbol)

    for attempt in range(1, YF_RETRIES + 1):
        try:
            df = yf.Ticker(ticker_symbol).history(
                period=period,
                interval=interval,
                auto_adjust=False,
                prepost=False,
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

                out.append(
                    {
                        "datetime": ts.strftime("%Y-%m-%d %H:%M:%S"),
                        "timestamp": ts.timestamp(),
                        "open": float(row["Open"]),
                        "high": float(row["High"]),
                        "low": float(row["Low"]),
                        "close": float(row["Close"]),
                    }
                )

            return out

        except Exception as e:
            with LOCK:
                STATS["data_errors"] += 1

            log(
                f"Yahoo {symbol} {interval} attempt={attempt}: "
                f"{type(e).__name__}: {e}"
            )

            if attempt < YF_RETRIES:
                time.sleep(1)

    return []


def get_candles(symbol, interval, period="5d", limit=300):
    mode = mode_now()

    # Weekend/explicit OTC mode requires a real OTC provider.
    # We intentionally do not silently substitute Yahoo.
    if mode == "OTC":
        if not OTC_API_URL:
            LAST_DATA_STATUS[(symbol, interval)] = {
                "status": "PAUSED_NO_OTC_API",
                "updated_at": time.time(),
            }
            return []

        candles = get_otc_candles(symbol, interval, limit)

        if candles:
            return candles

        LAST_DATA_STATUS[(symbol, interval)] = {
            "status": "PAUSED_OTC_PROVIDER_EMPTY",
            "updated_at": time.time(),
        }
        return []

    candles = get_yahoo_candles(symbol, interval, period)

    LAST_DATA_STATUS[(symbol, interval)] = {
        "status": "YAHOO_OK" if candles else "YAHOO_EMPTY",
        "updated_at": time.time(),
    }

    return candles


# ============================================================
# CANDLE STATE / FRESHNESS
# ============================================================

def is_closed(candle, interval):
    if not candle:
        return False

    return candle["timestamp"] + interval_seconds(interval) <= time.time()


def closed_only(candles, interval):
    return [
        c for c in candles
        if is_closed(c, interval)
    ]


def latest_closed(candles, interval):
    data = closed_only(candles, interval)
    return data[-1] if data else None


def candle_age_seconds(candle, interval):
    if candle is None:
        return float("inf")

    return max(
        0,
        time.time()
        - (candle["timestamp"] + interval_seconds(interval)),
    )


def fresh_candle(candle, interval):
    if candle is None:
        return False

    age = candle_age_seconds(candle, interval)

    limit = (
        MAX_DATA_AGE_SECONDS
        if interval == "5m"
        else MAX_15M_DATA_AGE_SECONDS
    )

    return limit <= 0 or age <= limit


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
    if candle:
        age = candle_age_seconds(candle, interval)
        log(
            f"{symbol} {interval}: STALE DATA "
            f"last={candle['datetime']} UTC age={age/60:.1f}m"
        )
    else:
        log(f"{symbol} {interval}: NO CLOSED CANDLE")


# ============================================================
# INDICATORS
# ============================================================

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

    gains = []
    losses = []

    for i in range(1, len(values)):
        d = values[i] - values[i - 1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))

    ag = sum(gains[:period]) / period
    al = sum(losses[:period]) / period

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
        c = candles[i]
        p = candles[i - 1]

        trs.append(
            max(
                c["high"] - c["low"],
                abs(c["high"] - p["close"]),
                abs(c["low"] - p["close"]),
            )
        )

    return sum(trs[-period:]) / period


def candle_features(c0, c1):
    body = abs(c0["close"] - c0["open"])
    rng = max(c0["high"] - c0["low"], 1e-12)

    upper = c0["high"] - max(c0["open"], c0["close"])
    lower = min(c0["open"], c0["close"]) - c0["low"]

    ratio = body / rng

    return {
        "strong_bull": (
            c0["close"] > c0["open"] and ratio >= 0.65
        ),
        "strong_bear": (
            c0["close"] < c0["open"] and ratio >= 0.65
        ),
        "hammer": (
            lower >= body * 2
            and upper <= rng * 0.25
            and ratio <= 0.45
        ),
        "shooting_star": (
            upper >= body * 2
            and lower <= rng * 0.25
            and ratio <= 0.45
        ),
        "bull_engulf": (
            c0["close"] > c0["open"]
            and c1["close"] < c1["open"]
            and c0["open"] <= c1["close"]
            and c0["close"] >= c1["open"]
            and body > abs(c1["close"] - c1["open"])
        ),
        "bear_engulf": (
            c0["close"] < c0["open"]
            and c1["close"] > c1["open"]
            and c0["open"] >= c1["close"]
            and c0["close"] <= c1["open"]
            and body > abs(c1["close"] - c1["open"])
        ),
    }


# ============================================================
# ZONES
# ============================================================

def build_zones(candles, lookback=240):
    if len(candles) < 30:
        return []

    data = candles[-lookback:]
    zones = []

    for i in range(2, len(data) - 2):
        h = data[i]["high"]
        l = data[i]["low"]

        if all(
            h >= data[j]["high"]
            for j in range(i - 2, i + 3)
            if j != i
        ):
            zones.append(
                {
                    "type": "RESISTANCE",
                    "price": h,
                    "timestamp": data[i]["timestamp"],
                }
            )

        if all(
            l <= data[j]["low"]
            for j in range(i - 2, i + 3)
            if j != i
        ):
            zones.append(
                {
                    "type": "SUPPORT",
                    "price": l,
                    "timestamp": data[i]["timestamp"],
                }
            )

    return zones


def zone_analysis(candles, price, direction):
    a = atr(candles, 14)

    if not a or a <= 0:
        return {
            "state": "NONE",
            "score": 0,
            "level": None,
        }

    zones = build_zones(candles)
    tol = a * 0.35

    candidates = [
        (abs(price - z["price"]), z)
        for z in zones
        if abs(price - z["price"]) <= tol
    ]

    if not candidates:
        return {
            "state": "NONE",
            "score": 0,
            "level": None,
        }

    candidates.sort(key=lambda x: x[0])
    dist, nearest = candidates[0]

    recent = candles[-12:]

    above = any(
        c["close"] > nearest["price"] + tol * 0.15
        for c in recent[:-2]
    )

    below = any(
        c["close"] < nearest["price"] - tol * 0.15
        for c in recent[:-2]
    )

    state = nearest["type"]
    score = 10

    if nearest["type"] == "RESISTANCE":
        if direction == "PUT":
            score += 15

        if above and price < nearest["price"]:
            state = "FLIPPED_RESISTANCE"
            score += 20

    if nearest["type"] == "SUPPORT":
        if direction == "CALL":
            score += 15

        if below and price > nearest["price"]:
            state = "FLIPPED_SUPPORT"
            score += 20

    if dist <= tol * 0.5:
        score += 10

    return {
        "state": state,
        "score": min(score, 45),
        "level": nearest["price"],
    }


# ============================================================
# 15M MASTER
# ============================================================

def analyze_15m(candles):
    if len(candles) < 80:
        return None

    c0 = candles[-1]
    c1 = candles[-2]

    closes = [c["close"] for c in candles]

    e20 = ema(closes, 20)
    e50 = ema(closes, 50)
    rsi = rsi_wilder(closes)
    a = atr(candles)

    if None in (e20, e50, rsi, a):
        return None

    price = c0["close"]
    p = candle_features(c0, c1)

    call = 0
    put = 0

    rc = []
    rp = []

    if price > e20:
        call += 15
        rc.append("price>EMA20")
    else:
        put += 15
        rp.append("price<EMA20")

    if price > e50:
        call += 20
        rc.append("price>EMA50")
    else:
        put += 20
        rp.append("price<EMA50")

    if e20 > e50:
        call += 10
        rc.append("EMA20>EMA50")
    else:
        put += 10
        rp.append("EMA20<EMA50")

    if 50 <= rsi <= 68:
        call += 8
        rc.append(f"RSI={rsi:.1f}")

    if 32 <= rsi < 50:
        put += 8
        rp.append(f"RSI={rsi:.1f}")

    if p["strong_bull"] or p["hammer"] or p["bull_engulf"]:
        call += 12
        rc.append("bullish-candle")

    if p["strong_bear"] or p["shooting_star"] or p["bear_engulf"]:
        put += 12
        rp.append("bearish-candle")

    zc = zone_analysis(candles, price, "CALL")
    zp = zone_analysis(candles, price, "PUT")

    call += zc["score"]
    put += zp["score"]

    if zc["score"] > 10:
        rc.append(f"ZONE={zc['state']}")

    if zp["score"] > 10:
        rp.append(f"ZONE={zp['state']}")

    if call > put and call >= 58:
        decision = "CALL"
        strength = min(99, call)
        reasons = rc
        zone = zc

    elif put > call and put >= 58:
        decision = "PUT"
        strength = min(99, put)
        reasons = rp
        zone = zp

    else:
        return None

    return {
        "decision": decision,
        "setup_strength": strength,
        "price": price,
        "atr": a,
        "rsi": rsi,
        "zone_state": zone["state"],
        "zone_level": zone["level"],
        "reasons": " | ".join(reasons),
        "candle_time": c0["datetime"],
        "candle_ts": c0["timestamp"],
    }


# ============================================================
# 5M CONTEXT
# ============================================================

def analyze_5m(candles, master_direction):
    if len(candles) < 70:
        return None

    c0 = candles[-1]
    c1 = candles[-2]

    closes = [c["close"] for c in candles]

    e20 = ema(closes, 20)
    e50 = ema(closes, 50)
    rsi = rsi_wilder(closes)

    if None in (e20, e50, rsi):
        return None

    p = candle_features(c0, c1)

    call = 0
    put = 0

    rc = []
    rp = []

    if c0["close"] > e20:
        call += 15
        rc.append("5M>EMA20")
    else:
        put += 15
        rp.append("5M<EMA20")

    if c0["close"] > e50:
        call += 10
        rc.append("5M>EMA50")
    else:
        put += 10
        rp.append("5M<EMA50")

    if rsi > 50:
        call += 8
        rc.append(f"RSI={rsi:.1f}")
    else:
        put += 8
        rp.append(f"RSI={rsi:.1f}")

    if p["strong_bull"] or p["bull_engulf"] or p["hammer"]:
        call += 15
        rc.append("bullish-candle")

    if p["strong_bear"] or p["bear_engulf"] or p["shooting_star"]:
        put += 15
        rp.append("bearish-candle")

    if master_direction == "CALL":
        call += 12
        rc.append("15M-master-CALL")
    else:
        put += 12
        rp.append("15M-master-PUT")

    if call > put:
        decision = "CALL"
        score = min(99, call)
        reasons = rc

    elif put > call:
        decision = "PUT"
        score = min(99, put)
        reasons = rp

    else:
        decision = "UNKNOWN"
        score = 50
        reasons = ["balanced"]

    return {
        "decision": decision,
        "score": score,
        "rsi": rsi,
        "candle_time": c0["datetime"],
        "candle_ts": c0["timestamp"],
        "reasons": " | ".join(reasons),
    }


# ============================================================
# HISTORY
# ============================================================

def setup_signature(symbol, mode, direction, zone_state):
    return symbol, mode, direction, zone_state


def historical_stats(signature):
    with LOCK:
        records = [
            r
            for r in HISTORICAL_MEMORY
            if r.get("type") == "SERIES"
            and setup_signature(
                r.get("symbol"),
                r.get("mode"),
                r.get("decision"),
                r.get("zone_state"),
            )
            == signature
        ]

    total = len(records)

    sw = sum(
        r.get("status") == "SERIES_WIN"
        for r in records
    )

    fw = sum(
        r.get("first_opportunity_result") == "WIN"
        for r in records
    )

    if total >= MIN_HISTORY_FOR_RATE:
        return {
            "samples": total,
            "series_win_rate": round(sw / total * 100, 1),
            "first_win_rate": round(fw / total * 100, 1),
            "confidence": "USABLE",
        }

    return {
        "samples": total,
        "series_win_rate": None,
        "first_win_rate": None,
        "confidence": "INSUFFICIENT_DATA",
    }


# ============================================================
# SERIES / COOLDOWN
# ============================================================

def active_series_count():
    with LOCK:
        return len(ACTIVE_SERIES)


def has_active_series(symbol):
    with LOCK:
        return any(
            s["symbol"] == symbol
            for s in ACTIVE_SERIES
        )


def cleanup_signal_cache():
    cutoff = (
        time.time()
        - SIGNAL_COOLDOWN_SECONDS * 3
    )

    with LOCK:
        for key in list(SENT_SIGNALS):
            if SENT_SIGNALS[key] < cutoff:
                SENT_SIGNALS.pop(key, None)


def signal_recently_sent(key):
    with LOCK:
        ts = SENT_SIGNALS.get(key)

    return (
        ts is not None
        and time.time() - ts < SIGNAL_COOLDOWN_SECONDS
    )


def remember_signal(key):
    with LOCK:
        SENT_SIGNALS[key] = time.time()


# ============================================================
# SIGNAL SCANNER
# ============================================================

def scan_symbol(symbol):
    # ---------------- 15M ----------------

    c15_raw = get_candles(
        symbol,
        "15m",
        "5d",
        300,
    )

    c15 = closed_only(c15_raw, "15m")

    if len(c15) < 80:
        return None

    latest15 = c15[-1]

    if not fresh_candle(latest15, "15m"):
        stale_log(symbol, "15m", latest15)
        return None

    new15 = is_new_closed(
        symbol,
        "15m",
        latest15,
    )

    # ---------------- 5M ----------------

    c5_raw = get_candles(
        symbol,
        "5m",
        "5d",
        500,
    )

    c5 = closed_only(c5_raw, "5m")

    if len(c5) < 70:
        return None

    latest5 = c5[-1]

    if not fresh_candle(latest5, "5m"):
        stale_log(symbol, "5m", latest5)
        return None

    new5 = is_new_closed(
        symbol,
        "5m",
        latest5,
    )

    # First observation only seeds state.
    if not new15 or not new5:
        if new15:
            mark_closed(symbol, "15m", latest15)

        if new5:
            mark_closed(symbol, "5m", latest5)

        return None

    # ---------------- ANALYSIS ----------------

    master = analyze_15m(c15)

    if not master:
        mark_closed(symbol, "15m", latest15)
        mark_closed(symbol, "5m", latest5)
        return None

    if master["setup_strength"] < 62:
        mark_closed(symbol, "15m", latest15)
        mark_closed(symbol, "5m", latest5)
        return None

    timing = analyze_5m(
        c5,
        master["decision"],
    )

    if not timing:
        mark_closed(symbol, "15m", latest15)
        mark_closed(symbol, "5m", latest5)
        return None

    if timing["decision"] == master["decision"]:
        context = "5M_CONFIRM"
    elif timing["decision"] == "UNKNOWN":
        context = "5M_UNKNOWN"
    else:
        context = "5M_PULLBACK"

    mode = mode_now()

    hist = historical_stats(
        setup_signature(
            symbol,
            mode,
            master["decision"],
            master["zone_state"],
        )
    )

    next_open_ts = latest5["timestamp"] + TF5
    next_close_ts = next_open_ts + TF5

    # Mark only after all analysis checks complete.
    mark_closed(symbol, "15m", latest15)
    mark_closed(symbol, "5m", latest5)

    return {
        "symbol": symbol,
        "mode": mode,
        "decision": master["decision"],
        "setup_strength": round(
            master["setup_strength"], 1
        ),
        "rsi15": master["rsi"],
        "rsi5": timing["rsi"],
        "entry_score": timing["score"],
        "entry_context": context,
        "zone_state": master["zone_state"],
        "zone_level": master["zone_level"],
        "price": master["price"],
        "atr": master["atr"],
        "reasons15": master["reasons"],
        "reasons5": timing["reasons"],
        "signal_candle15": master["candle_time"],
        "last_closed_5m": latest5["datetime"],
        "signal_ts": time.time(),
        "next_open_ts": next_open_ts,
        "next_close_ts": next_close_ts,
        "history": hist,
        "created_at": thai_text(),
    }


def create_series(signal):
    with LOCK:
        if (
            len(ACTIVE_SERIES) >= MAX_ACTIVE_SERIES
            or any(
                s["symbol"] == signal["symbol"]
                for s in ACTIVE_SERIES
            )
        ):
            return None

        tracker = {
            "type": "ACTIVE_SERIES",
            "series_id": (
                f"{signal['symbol'].replace('/', '')}_"
                f"{int(signal['signal_ts'])}"
            ),
            "symbol": signal["symbol"],
            "mode": signal["mode"],
            "master_direction": signal["decision"],
            "setup_strength": signal["setup_strength"],
            "entry_context": signal["entry_context"],
            "zone_state": signal["zone_state"],
            "zone_level": signal["zone_level"],
            "signal_time": signal["created_at"],
            "signal_ts": signal["signal_ts"],
            "next_entry_ts": signal["next_open_ts"],
            "next_close_ts": signal["next_close_ts"],
            "opportunity": 1,
            "wins": 0,
            "losses": 0,
            "draws": 0,
            "first_opportunity_result": None,
            "processed_5m": [],
            "max_mfe": 0.0,
            "max_mae": 0.0,
        }

        ACTIVE_SERIES.append(tracker)
        STATS["signals"] += 1

        return tracker


# ============================================================
# OPPORTUNITY ENGINE
# ============================================================

def exact_entry_candle(candles, entry_ts):
    for c in candles:
        if abs(c["timestamp"] - entry_ts) < 1:
            return c if is_closed(c, "5m") else None

    return None


def evaluate_opportunity(tracker):
    candles = closed_only(
        get_candles(
            tracker["symbol"],
            "5m",
            "2d",
            200,
        ),
        "5m",
    )

    if len(candles) < 20:
        return None

    c = exact_entry_candle(
        candles,
        tracker["next_entry_ts"],
    )

    if c is None:
        return None

    if c["datetime"] in tracker["processed_5m"]:
        return None

    entry = c["open"]
    close = c["close"]
    direction = tracker["master_direction"]

    if close > entry:
        result = (
            "WIN"
            if direction == "CALL"
            else "LOSS"
        )
    elif close < entry:
        result = (
            "WIN"
            if direction == "PUT"
            else "LOSS"
        )
    else:
        result = "DRAW"

    if direction == "CALL":
        mfe = c["high"] - entry
        mae = entry - c["low"]
    else:
        mfe = entry - c["low"]
        mae = c["high"] - entry

    return {
        "result": result,
        "candle": c,
        "entry_price": entry,
        "close_price": close,
        "mfe": max(0, mfe),
        "mae": max(0, mae),
    }


def record_opportunity(tracker, outcome):
    tracker["processed_5m"].append(
        outcome["candle"]["datetime"]
    )

    tracker["max_mfe"] = max(
        tracker["max_mfe"],
        outcome["mfe"],
    )

    tracker["max_mae"] = max(
        tracker["max_mae"],
        outcome["mae"],
    )

    result = outcome["result"]

    with LOCK:
        if result == "WIN":
            tracker["wins"] += 1
            STATS["wins"] += 1

        elif result == "LOSS":
            tracker["losses"] += 1
            STATS["losses"] += 1

        else:
            tracker["draws"] += 1
            STATS["draws"] += 1

    if tracker["opportunity"] == 1:
        tracker["first_opportunity_result"] = result

    return result


# ============================================================
# FINALIZE
# ============================================================

def finalize_series(tracker, status):
    record = {
        "type": "SERIES",
        "series_id": tracker["series_id"],
        "symbol": tracker["symbol"],
        "mode": tracker["mode"],
        "decision": tracker["master_direction"],
        "setup_strength": tracker["setup_strength"],
        "entry_context": tracker["entry_context"],
        "zone_state": tracker["zone_state"],
        "zone_level": tracker["zone_level"],
        "signal_time": tracker["signal_time"],
        "status": status,
        "wins": tracker["wins"],
        "losses": tracker["losses"],
        "draws": tracker["draws"],
        "opportunities_used": tracker["opportunity"],
        "first_opportunity_result": tracker[
            "first_opportunity_result"
        ],
        "max_mfe": tracker["max_mfe"],
        "max_mae": tracker["max_mae"],
        "recorded_at": thai_text(),
    }

    with LOCK:
        HISTORICAL_MEMORY.append(record)

        STATS["series_completed"] += 1

        if status == "SERIES_WIN":
            STATS["series_wins"] += 1

        if status == "FULL_LOSS":
            STATS["series_full_loss"] += 1

    save_memory()

    h = historical_stats(
        setup_signature(
            tracker["symbol"],
            tracker["mode"],
            tracker["master_direction"],
            tracker["zone_state"],
        )
    )

    rate = (
        f"{h['series_win_rate']}%"
        if h["series_win_rate"] is not None
        else "INSUFFICIENT_DATA"
    )

    icon = (
        "🟢"
        if status == "SERIES_WIN"
        else "🔴"
    )

    send_discord(
        f"{icon} **[TRADEIFY SERIES COMPLETE V2.2]**\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🪙 คู่: **{tracker['symbol']}**\n"
        f"🌐 Mode: **{tracker['mode']}**\n"
        f"📌 Direction: **{tracker['master_direction']}**\n"
        f"🏁 Status: **{status}**\n"
        f"🎯 WIN: **{tracker['wins']}**\n"
        f"❌ LOSS: **{tracker['losses']}**\n"
        f"➖ DRAW: **{tracker['draws']}**\n"
        f"🔢 Opportunities: **{tracker['opportunity']}/3**\n"
        f"📈 Historical Series Win Rate: **{rate}**\n"
        f"📚 Samples: **{h['samples']}**\n"
        f"🧩 Zone: **{tracker['zone_state']}**\n"
        f"🕐 เวลาไทย: **{thai_text()}**"
    )


# ============================================================
# TRACKER
# ============================================================

def tracker_loop():
    log("Tracker thread started")

    while True:
        try:
            with LOCK:
                trackers = list(ACTIVE_SERIES)

            for tracker in trackers:
                outcome = evaluate_opportunity(tracker)

                if outcome is None:
                    continue

                result = record_opportunity(
                    tracker,
                    outcome,
                )

                c = outcome["candle"]

                icon = (
                    "🟢"
                    if result == "WIN"
                    else "🔴"
                    if result == "LOSS"
                    else "🟡"
                )

                send_discord(
                    f"{icon} **[TRADEIFY 5M RESULT V2.2]**\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"🪙 **{tracker['symbol']}**\n"
                    f"🌐 Mode: **{tracker['mode']}**\n"
                    f"📌 Master: **{tracker['master_direction']}**\n"
                    f"🎯 OPP: **{tracker['opportunity']}/3**\n"
                    f"🏁 Result: **{result}**\n"
                    f"💰 Entry: **{outcome['entry_price']:.8f}**\n"
                    f"🔚 Close: **{outcome['close_price']:.8f}**\n"
                    f"🕐 Candle UTC: **{c['datetime']}**\n"
                    f"🇹🇭 เวลาแจ้ง: **{thai_text()}**\n"
                    f"📈 MFE: **{outcome['mfe']:.8f}**\n"
                    f"📉 MAE: **{outcome['mae']:.8f}**\n"
                    f"⚠️ ตัดผลหลังแท่ง 5M ปิดแล้วเท่านั้น"
                )

                if (
                    result == "WIN"
                    or tracker["opportunity"] >= MAX_OPPORTUNITIES
                ):
                    status = (
                        "SERIES_WIN"
                        if result == "WIN"
                        else "FULL_LOSS"
                    )

                    finalize_series(
                        tracker,
                        status,
                    )

                    with LOCK:
                        if tracker in ACTIVE_SERIES:
                            ACTIVE_SERIES.remove(tracker)

                else:
                    tracker["opportunity"] += 1

                    tracker["next_entry_ts"] = (
                        c["timestamp"] + TF5
                    )

                    tracker["next_close_ts"] = (
                        tracker["next_entry_ts"] + TF5
                    )

                    dt1 = utc_to_thai(
                        tracker["next_entry_ts"]
                    )
                    dt2 = utc_to_thai(
                        tracker["next_close_ts"]
                    )

                    send_discord(
                        f"🔄 **[TRADEIFY NEXT OPPORTUNITY V2.2]**\n"
                        f"🪙 {tracker['symbol']}\n"
                        f"🌐 Mode: {tracker['mode']}\n"
                        f"📌 Direction เดิม: "
                        f"**{tracker['master_direction']}**\n"
                        f"🎯 OPP: "
                        f"**{tracker['opportunity']}/3**\n"
                        f"⏰ เข้า: **{thai_hm(dt1)} น.**\n"
                        f"🔚 ปิด: **{thai_hm(dt2)} น.**\n"
                        f"⚠️ exact 5M candle เท่านั้น"
                    )

        except Exception as e:
            log(
                f"Tracker error: "
                f"{type(e).__name__}: {e}"
            )
            traceback.print_exc()

        with LOCK:
            STATS["tracker_cycles"] += 1

        time.sleep(10)


# ============================================================
# SCANNER
# ============================================================

def scanner_loop():
    log("Scanner thread started")

    while True:
        try:
            cleanup_signal_cache()

            with LOCK:
                STATS["scanner_cycles"] += 1

            if active_series_count() >= MAX_ACTIVE_SERIES:
                log(
                    f"Scanner paused: active series "
                    f"{active_series_count()}/{MAX_ACTIVE_SERIES}"
                )
                time.sleep(SCAN_SECONDS)
                continue

            if mode_now() == "OTC" and not OTC_API_URL:
                log(
                    "Scanner paused: OTC mode but OTC_API_URL "
                    "is not configured. No Yahoo fallback."
                )
                time.sleep(SCAN_SECONDS)
                continue

            best = []

            for symbol in SYMBOLS:
                if has_active_series(symbol):
                    continue

                try:
                    signal = scan_symbol(symbol)

                    if signal:
                        best.append(signal)

                except Exception as e:
                    log(
                        f"Scanner {symbol}: "
                        f"{type(e).__name__}: {e}"
                    )

            best.sort(
                key=lambda x: x["setup_strength"],
                reverse=True,
            )

            created = 0

            for signal in best:
                if active_series_count() >= MAX_ACTIVE_SERIES:
                    break

                key = (
                    signal["symbol"],
                    signal["mode"],
                    signal["decision"],
                    signal["signal_candle15"],
                )

                if signal_recently_sent(key):
                    continue

                if has_active_series(signal["symbol"]):
                    continue

                remember_signal(key)

                tracker = create_series(signal)

                if tracker is None:
                    continue

                h = signal["history"]

                sr = (
                    f"{h['series_win_rate']}%"
                    if h["series_win_rate"] is not None
                    else "INSUFFICIENT_DATA"
                )

                fr = (
                    f"{h['first_win_rate']}%"
                    if h["first_win_rate"] is not None
                    else "INSUFFICIENT_DATA"
                )

                d1 = utc_to_thai(
                    signal["next_open_ts"]
                )
                d2 = utc_to_thai(
                    signal["next_close_ts"]
                )

                ai = ai_comment(signal)

                icon = (
                    "🟢"
                    if signal["decision"] == "CALL"
                    else "🔴"
                )

                send_discord(
                    f"🚨 **[TRADEIFY SIGNAL V2.2]**\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"⏱️ TF: **5M**\n"
                    f"🪙 คู่: **{signal['symbol']}**\n\n"
                    f"⭐ **เตือนเวลา {thai_hm()} น.** ⭐\n"
                    f"🇹🇭 เวลาไทย\n\n"
                    f"🌐 Mode: **{signal['mode']}**\n"
                    f"📌 Direction: **{signal['decision']}** {icon}\n"
                    f"🟢 **เตรียมซื้อแท่งหน้า**\n\n"
                    f"📊 SETUP STRENGTH: "
                    f"**{signal['setup_strength']}/100**\n"
                    f"📈 15M RSI: **{signal['rsi15']:.1f}**\n"
                    f"📉 5M RSI: **{signal['rsi5']:.1f}**\n"
                    f"⏱️ 5M Context: "
                    f"**{signal['entry_context']}**\n"
                    f"🧩 Zone: **{signal['zone_state']}**\n\n"
                    f"📚 HISTORICAL\n"
                    f"• Samples: **{h['samples']}**\n"
                    f"• Series Win Rate: **{sr}**\n"
                    f"• First Entry Win Rate: **{fr}**\n\n"
                    f"🎯 Opportunity: **1/3**\n"
                    f"⏰ เข้าแท่ง: **{thai_hm(d1)} น.**\n"
                    f"🔚 ปิดแท่ง: **{thai_hm(d2)} น.**\n\n"
                    f"🔍 15M: {signal['reasons15']}\n"
                    f"🔍 5M: {signal['reasons5']}\n"
                    f"🤖 {ai}\n\n"
                    f"⚠️ **ผล WIN/LOSS ตัดสินหลังแท่ง 5M ปิดเท่านั้น**\n"
                    f"🕐 Signal: **{signal['created_at']}**\n"
                    f"🔒 Active Series: "
                    f"**{active_series_count()}/{MAX_ACTIVE_SERIES}**"
                )

                log(
                    f"NEW {signal['symbol']} "
                    f"{signal['decision']} "
                    f"mode={signal['mode']} "
                    f"strength={signal['setup_strength']} "
                    f"active={active_series_count()}/"
                    f"{MAX_ACTIVE_SERIES}"
                )

                created += 1

                if created >= MAX_NEW_SIGNALS_PER_SCAN:
                    break

        except Exception as e:
            with LOCK:
                STATS["scanner_errors"] += 1

            log(
                f"Scanner loop error: "
                f"{type(e).__name__}: {e}"
            )
            traceback.print_exc()

        time.sleep(SCAN_SECONDS)


# ============================================================
# STATUS / HEALTH
# ============================================================

def calculate_stats():
    with LOCK:
        total = (
            STATS["wins"]
            + STATS["losses"]
            + STATS["draws"]
        )

        return {
            **STATS,
            "total_opportunities": total,
            "win_rate": (
                round(
                    STATS["wins"] / total * 100,
                    2,
                )
                if total
                else None
            ),
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
    log("Reporter thread started")

    while True:
        try:
            s = calculate_stats()

            wr = (
                f"{s['win_rate']}%"
                if s["win_rate"] is not None
                else "NO_DATA"
            )

            lines = []

            for symbol, d in latest_snapshot().items():
                t = (
                    datetime.fromtimestamp(
                        d["5m"],
                        timezone.utc,
                    ).strftime("%H:%M:%S UTC")
                    if d["5m"]
                    else "-"
                )

                lines.append(
                    f"{symbol}: 5M={t}"
                )

            send_discord(
                f"📊 **[TRADEIFY STATUS V2.2]**\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"🇹🇭 เวลาไทย: **{thai_text()}**\n"
                f"🌐 Mode: **{mode_now()}**\n"
                f"📢 Signals: **{s['signals']}**\n"
                f"🟢 WIN: **{s['wins']}**\n"
                f"🔴 LOSS: **{s['losses']}**\n"
                f"🟡 DRAW: **{s['draws']}**\n"
                f"📈 Opportunity Win Rate: **{wr}**\n"
                f"🏁 Series Complete: "
                f"**{s['series_completed']}**\n"
                f"🟢 Series Win: **{s['series_wins']}**\n"
                f"🔴 Full Loss: **{s['series_full_loss']}**\n"
                f"🔄 Active Series: "
                f"**{s['active_series']}/{MAX_ACTIVE_SERIES}**\n"
                f"💾 Memory: **{s['memory_records']}**\n"
                f"📡 OTC API: "
                f"**{'CONFIGURED' if OTC_API_URL else 'NOT CONFIGURED'}**\n\n"
                f"🕐 Latest closed 5M:\n"
                + "\n".join(lines)
            )

        except Exception as e:
            log(
                f"Reporter error: "
                f"{type(e).__name__}: {e}"
            )

        time.sleep(REPORT_SECONDS)


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path not in (
            "/",
            "/health",
            "/status",
        ):
            self.send_response(404)
            self.end_headers()
            return

        s = calculate_stats()
        snap = latest_snapshot()

        body = {
            "status": "running",
            "version": "V2.2",
            "pid": os.getpid(),
            "time_thai": thai_text(),
            "mode": mode_now(),
            "symbols": SYMBOLS,
            "active_series": s["active_series"],
            "max_active_series": MAX_ACTIVE_SERIES,
            "memory_records": s["memory_records"],
            "wins": s["wins"],
            "losses": s["losses"],
            "draws": s["draws"],
            "win_rate": s["win_rate"],
            "otc_api_configured": bool(OTC_API_URL),
            "gemini_enabled": bool(AI_CHAT),
            "discord_configured": bool(DISCORD_WEBHOOK_URL),
            "scanner_cycles": s["scanner_cycles"],
            "scanner_errors": s["scanner_errors"],
            "tracker_cycles": s["tracker_cycles"],
            "data_errors": s["data_errors"],
            "latest_closed_5m": {
                k: (
                    datetime.fromtimestamp(
                        v["5m"],
                        timezone.utc,
                    ).strftime(
                        "%Y-%m-%d %H:%M:%S UTC"
                    )
                    if v["5m"]
                    else None
                )
                for k, v in snap.items()
            },
        }

        raw = json.dumps(
            body,
            ensure_ascii=False,
            indent=2,
        ).encode("utf-8")

        self.send_response(200)
        self.send_header(
            "Content-Type",
            "application/json; charset=utf-8",
        )
        self.send_header(
            "Content-Length",
            str(len(raw)),
        )
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, format, *args):
        return


def run_health_server():
    global HEALTH_SERVER

    try:
        HEALTH_SERVER = HTTPServer(
            (HEALTH_BIND, PORT),
            HealthHandler,
        )

        log(
            f"Health server listening "
            f"{HEALTH_BIND}:{PORT}"
        )

        HEALTH_SERVER.serve_forever()

    except Exception as e:
        log(
            f"Health server error: "
            f"{type(e).__name__}: {e}"
        )


# ============================================================
# BOOT / STARTUP
# ============================================================

def startup_message():
    ok = send_discord(
        f"🚀 **[TRADEIFY V2.2 STARTED]**\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🇹🇭 เวลาไทย: **{thai_text()}**\n"
        f"🌐 Mode: **{mode_now()}**\n"
        f"⏱️ Master: **15M closed candle**\n"
        f"🎯 Entry/Result: **5M closed candle**\n"
        f"🔢 Opportunities: **1–3**\n"
        f"🔒 Max Active Series: **{MAX_ACTIVE_SERIES}**\n"
        f"⏳ Cooldown: **{SIGNAL_COOLDOWN_SECONDS}s**\n"
        f"💾 Memory: **ON**\n"
        f"📡 Symbols: **{len(SYMBOLS)}**\n"
        f"🤖 Gemini: "
        f"**{GEMINI_MODEL if AI_CHAT else 'OFF'}**\n"
        f"📡 OTC API: "
        f"**{'ON' if OTC_API_URL else 'OFF'}**\n\n"
        f"🛡️ V2.2: no stale OTC/Yahoo fallback on weekend."
    )

    log(
        f"Discord startup message: "
        f"{'SENT' if ok else 'NOT SENT'}"
    )


def main():
    print("==============================================", flush=True)
    print("🚀 TRADEIFY V2.2 BOOT", flush=True)
    print("==============================================", flush=True)

    boot("1", "CONFIG OK")
    log(f"Python: {sys.version.split()[0]}")
    log(f"PID: {os.getpid()}")
    log(f"PORT: {PORT}")
    log(f"Mode: {mode_now()}")
    log(f"Memory: {MEMORY_FILE}")
    log(f"Symbols: {', '.join(SYMBOLS)}")
    log(
        f"OTC API: "
        f"{'CONFIGURED' if OTC_API_URL else 'NOT CONFIGURED'}"
    )

    # --------------------------------------------------------
    # Lock
    # --------------------------------------------------------

    if not acquire_process_lock():
        boot("2", "PROCESS LOCK FAILED")
        sys.exit(1)

    boot("2", "PROCESS LOCK OK")

    # --------------------------------------------------------
    # Memory
    # --------------------------------------------------------

    try:
        load_memory()
        boot("3", f"MEMORY OK ({len(HISTORICAL_MEMORY)} records)")
    except Exception as e:
        boot("3", f"MEMORY FAILED: {e}")
        raise

    # --------------------------------------------------------
    # Gemini
    # --------------------------------------------------------

    try:
        init_gemini()
        boot(
            "4",
            "GEMINI OK"
            if AI_CHAT
            else "GEMINI OFF (optional)",
        )
    except Exception as e:
        boot("4", f"GEMINI WARNING: {e}")

    # --------------------------------------------------------
    # Health server
    # --------------------------------------------------------

    health_thread = Thread(
        target=run_health_server,
        name="tradeify-health",
        daemon=True,
    )

    health_thread.start()
    time.sleep(0.25)

    boot("5", "HEALTH SERVER STARTED")

    # --------------------------------------------------------
    # Tracker
    # --------------------------------------------------------

    tracker_thread = Thread(
        target=tracker_loop,
        name="tradeify-tracker",
        daemon=True,
    )

    tracker_thread.start()

    boot("6", "TRACKER STARTED")

    # --------------------------------------------------------
    # Scanner
    # --------------------------------------------------------

    scanner_thread = Thread(
        target=scanner_loop,
        name="tradeify-scanner",
        daemon=True,
    )

    scanner_thread.start()

    boot("7", "SCANNER STARTED")

    # --------------------------------------------------------
    # Reporter
    # --------------------------------------------------------

    reporter_thread = Thread(
        target=reporter_loop,
        name="tradeify-reporter",
        daemon=True,
    )

    reporter_thread.start()

    boot("8", "REPORTER STARTED")

    # --------------------------------------------------------
    # Discord
    # --------------------------------------------------------

    if DISCORD_WEBHOOK_URL:
        startup_message()
        boot("9", "DISCORD STARTUP ATTEMPTED")
    else:
        boot("9", "DISCORD OFF / WEBHOOK NOT CONFIGURED")

    # --------------------------------------------------------
    # Complete
    # --------------------------------------------------------

    print("==============================================", flush=True)
    print("✅ TRADEIFY V2.2 RUNNING", flush=True)
    print("==============================================", flush=True)

    while True:
        try:
            time.sleep(60)

        except KeyboardInterrupt:
            log("Stopping TRADEIFY V2.2...")
            release_process_lock()
            break

        except Exception as e:
            log(
                f"Main loop error: "
                f"{type(e).__name__}: {e}"
            )


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("==============================================", flush=True)
        print("❌ TRADEIFY V2.2 FATAL BOOT ERROR", flush=True)
        print(f"{type(e).__name__}: {e}", flush=True)
        traceback.print_exc()
        print("==============================================", flush=True)
        release_process_lock()
        raise
'''

requirements = """requests
yfinance
google-genai
"""

Path("/mnt/data/tradeify_v2_2.py").write_text(code, encoding="utf-8")
Path("/mnt/data/requirements.txt").write_text(requirements, encoding="utf-8")

print("สร้างไฟล์ TRADEIFY V2.2 และ requirements.txt เรียบร้อย")
