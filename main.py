from pathlib import Path

code = r'''# -*- coding: utf-8 -*-
"""
SIGZY 15M + 5M ZONE FLIP / 3-OPPORTUNITY TRACKER
Python 3.10 / Railway ready

หลักการ:
- 15M = Trend + Historical Support/Resistance + Break/Flip
- 5M = จังหวะเข้า
- Signal ไม่ถูกยกเลิกเพียงเพราะ 5M สวนชั่วคราว
- 1 Series มีสูงสุด 3 opportunities
- ผลของแต่ละไม้ตัดสินจาก "แท่ง 5M ที่ปิดแล้ว"
- Signal ที่ออกกลางแท่ง 5M -> ไม้แรกวัดจากราคาตอน signal ถึง CLOSE ของแท่งนั้น
- ถ้า signal เกิดหลังแท่ง 5M ปิดแล้ว -> ใช้แท่ง 5M ถัดไปเป็นไม้แรก
- ครบ 3 ไม้ -> จบ Series แล้ว scan ใหม่
- คู่เดิมสามารถกลับมาได้ หากเป็น setup ใหม่
- Memory ไม่ถูก reset
"""

import os
import json
import time
import math
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

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

PORT = int(os.getenv("PORT", "8080"))

SCAN_SECONDS = int(os.getenv("SCAN_SECONDS", "180"))
FIVE_MIN_SECONDS = 300

MEMORY_FILE = os.getenv("MEMORY_FILE", "v13_memory.json")

MAX_OPPORTUNITIES = 3

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

SYMBOLS = list(SYMBOL_MAP.keys())

# 15M is the master timeframe.
MASTER_INTERVAL = "15m"

# 5M is used for timing/result tracking.
ENTRY_INTERVAL = "5m"


# ============================================================
# GLOBAL STATE
# ============================================================

LOCK = Lock()

HISTORICAL_MEMORY = []
ACTIVE_SERIES = []

# Prevent duplicate signals while process is running.
SENT_SIGNALS = set()

# Basic counters.
STATS = {
    "signals": 0,
    "wins": 0,
    "losses": 0,
    "draws": 0,
    "series_completed": 0,
    "series_wins": 0,
    "series_full_loss": 0,
}


# ============================================================
# TIME
# ============================================================

def now_utc():
    return datetime.now(timezone.utc)


def now_thai():
    return now_utc() + timedelta(hours=7)


def now_text():
    return now_thai().strftime("%Y-%m-%d %H:%M:%S")


def log(msg):
    print(f"[{now_text()}] {msg}", flush=True)


# ============================================================
# DISCORD
# ============================================================

def send_discord(message):
    if not DISCORD_WEBHOOK_URL:
        log("Discord disabled: DISCORD_WEBHOOK_URL not set")
        return False

    try:
        r = requests.post(
            DISCORD_WEBHOOK_URL,
            json={"content": message[:1900]},
            timeout=10,
        )

        if r.status_code == 204:
            return True

        log(f"Discord HTTP {r.status_code}: {r.text[:300]}")
        return False

    except Exception as e:
        log(f"Discord error: {e}")
        return False


# ============================================================
# GEMINI
# ============================================================

AI_CLIENT = None

if genai and GEMINI_API_KEY:
    try:
        AI_CLIENT = genai.Client(api_key=GEMINI_API_KEY)
        log("Gemini client ready")
    except Exception as e:
        log(f"Gemini init failed: {e}")
        AI_CLIENT = None
else:
    log("Gemini disabled")


def ai_comment(symbol, direction, score, zone_state):
    """
    AI เป็นคำอธิบายเสริมเท่านั้น
    ไม่ใช้เป็น hard blocker
    """
    if AI_CLIENT is None:
        return "AI: OFF"

    try:
        prompt = (
            f"Forex {symbol}, master 15M direction={direction}, "
            f"score={score}/100, zone={zone_state}. "
            f"Give a very short Thai comment, max 2 lines. "
            f"Do not give guaranteed-profit language."
        )

        response = AI_CLIENT.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
        )

        text = getattr(response, "text", None)
        return text.strip() if text else "AI: no response"

    except Exception as e:
        return f"AI unavailable: {str(e)[:100]}"


# ============================================================
# MEMORY
# ============================================================

def load_memory():
    global HISTORICAL_MEMORY

    if not os.path.exists(MEMORY_FILE):
        HISTORICAL_MEMORY = []
        log(f"Memory not found -> create new: {MEMORY_FILE}")
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

        log(f"Memory loaded: {len(HISTORICAL_MEMORY)} records")

    except Exception as e:
        log(f"Memory load error: {e}")
        HISTORICAL_MEMORY = []


def save_memory():
    try:
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
        log(f"Memory save error: {e}")


# ============================================================
# MARKET DATA
# ============================================================

def clean_dataframe(df):
    if df is None or df.empty:
        return None

    try:
        # Remove timezone only for easier candle comparison.
        if getattr(df.index, "tz", None) is not None:
            df = df.copy()
            df.index = df.index.tz_convert("UTC").tz_localize(None)

        return df.dropna(subset=["Open", "High", "Low", "Close"])
    except Exception:
        return df


def get_candles(symbol, interval, period="5d", closed_only=False):
    ticker_symbol = SYMBOL_MAP.get(symbol, symbol)

    try:
        ticker = yf.Ticker(ticker_symbol)

        df = ticker.history(
            period=period,
            interval=interval,
            auto_adjust=False,
            prepost=False,
        )

        df = clean_dataframe(df)

        if df is None or len(df) < 10:
            return []

        # Yahoo's last intraday candle may still be forming.
        # For analysis that requires closed candles, remove it.
        if closed_only and len(df) > 1:
            df = df.iloc[:-1]

        candles = []

        for idx, row in df.iterrows():
            ts = idx.to_pydatetime()

            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)

            ts = ts.astimezone(timezone.utc)

            candles.append({
                "datetime": ts.strftime("%Y-%m-%d %H:%M:%S"),
                "timestamp": ts.timestamp(),
                "open": float(row["Open"]),
                "high": float(row["High"]),
                "low": float(row["Low"]),
                "close": float(row["Close"]),
            })

        return candles

    except Exception as e:
        log(f"Yahoo {symbol} {interval} error: {e}")
        return []


# ============================================================
# INDICATORS
# ============================================================

def ema(values, period):
    if len(values) < period:
        return None

    multiplier = 2.0 / (period + 1.0)

    value = sum(values[:period]) / period

    for price in values[period:]:
        value = (price - value) * multiplier + value

    return value


def rsi_wilder(values, period=14):
    if len(values) < period + 1:
        return None

    gains = []
    losses = []

    for i in range(1, len(values)):
        diff = values[i] - values[i - 1]

        gains.append(max(diff, 0.0))
        losses.append(max(-diff, 0.0))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(gains)):
        avg_gain = ((avg_gain * (period - 1)) + gains[i]) / period
        avg_loss = ((avg_loss * (period - 1)) + losses[i]) / period

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss

    return 100.0 - (100.0 / (1.0 + rs))


def atr(candles, period=14):
    if len(candles) < period + 1:
        return None

    trs = []

    for i in range(1, len(candles)):
        c = candles[i]
        p = candles[i - 1]

        tr = max(
            c["high"] - c["low"],
            abs(c["high"] - p["close"]),
            abs(c["low"] - p["close"]),
        )

        trs.append(tr)

    return sum(trs[-period:]) / period


# ============================================================
# CANDLE PATTERNS
# ============================================================

def candle_features(c0, c1):
    body = abs(c0["close"] - c0["open"])

    full_range = max(
        c0["high"] - c0["low"],
        1e-12,
    )

    upper = c0["high"] - max(c0["open"], c0["close"])
    lower = min(c0["open"], c0["close"]) - c0["low"]

    body_ratio = body / full_range

    strong_bull = (
        c0["close"] > c0["open"]
        and body_ratio >= 0.65
    )

    strong_bear = (
        c0["close"] < c0["open"]
        and body_ratio >= 0.65
    )

    hammer = (
        lower >= body * 2.0
        and upper <= full_range * 0.25
        and body_ratio <= 0.45
    )

    shooting_star = (
        upper >= body * 2.0
        and lower <= full_range * 0.25
        and body_ratio <= 0.45
    )

    bull_engulf = (
        c0["close"] > c0["open"]
        and c1["close"] < c1["open"]
        and c0["open"] <= c1["close"]
        and c0["close"] >= c1["open"]
        and body > abs(c1["close"] - c1["open"])
    )

    bear_engulf = (
        c0["close"] < c0["open"]
        and c1["close"] > c1["open"]
        and c0["open"] >= c1["close"]
        and c0["close"] <= c1["open"]
        and body > abs(c1["close"] - c1["open"])
    )

    return {
        "strong_bull": strong_bull,
        "strong_bear": strong_bear,
        "hammer": hammer,
        "shooting_star": shooting_star,
        "bull_engulf": bull_engulf,
        "bear_engulf": bear_engulf,
        "body_ratio": body_ratio,
    }


# ============================================================
# HISTORICAL ZONES
# ============================================================

def build_zones(candles, lookback=240):
    """
    ใช้ pivot แบบง่ายจากข้อมูลที่ปิดแล้ว
    เพื่อสร้างโซนจาก swing high / swing low
    """

    if len(candles) < 30:
        return []

    data = candles[-lookback:]

    zones = []

    left_right = 2

    for i in range(left_right, len(data) - left_right):

        h = data[i]["high"]
        l = data[i]["low"]

        is_swing_high = all(
            h >= data[j]["high"]
            for j in range(i - left_right, i + left_right + 1)
            if j != i
        )

        is_swing_low = all(
            l <= data[j]["low"]
            for j in range(i - left_right, i + left_right + 1)
            if j != i
        )

        if is_swing_high:
            zones.append({
                "type": "RESISTANCE",
                "price": h,
                "timestamp": data[i]["timestamp"],
            })

        if is_swing_low:
            zones.append({
                "type": "SUPPORT",
                "price": l,
                "timestamp": data[i]["timestamp"],
            })

    return zones


def zone_analysis(candles, price, direction):
    """
    ตรวจ zone เดิม + break + flip
    ไม่ใช้ zone เป็น hard blocker
    """

    if len(candles) < 40:
        return {
            "state": "NONE",
            "score": 0,
            "distance": None,
            "level": None,
        }

    current_atr = atr(candles, 14)

    if not current_atr or current_atr <= 0:
        return {
            "state": "NONE",
            "score": 0,
            "distance": None,
            "level": None,
        }

    zones = build_zones(candles)

    tolerance = current_atr * 0.35

    candidates = []

    for z in zones:
        distance = abs(price - z["price"])

        if distance <= tolerance:
            candidates.append((distance, z))

    if not candidates:
        return {
            "state": "NONE",
            "score": 0,
            "distance": None,
            "level": None,
        }

    candidates.sort(key=lambda x: x[0])

    distance, nearest = candidates[0]

    # Detect a recent break and possible flip.
    recent = candles[-12:]

    broke_above = any(
        c["close"] > nearest["price"] + tolerance * 0.15
        for c in recent[:-2]
    )

    broke_below = any(
        c["close"] < nearest["price"] - tolerance * 0.15
        for c in recent[:-2]
    )

    state = nearest["type"]
    score = 10

    if nearest["type"] == "RESISTANCE":
        if direction == "PUT":
            score += 15

        if broke_above and price < nearest["price"]:
            state = "FLIPPED_RESISTANCE"
            score += 20

    elif nearest["type"] == "SUPPORT":
        if direction == "CALL":
            score += 15

        if broke_below and price > nearest["price"]:
            state = "FLIPPED_SUPPORT"
            score += 20

    # Retest proximity bonus.
    if distance <= tolerance * 0.50:
        score += 10

    return {
        "state": state,
        "score": min(score, 45),
        "distance": distance,
        "level": nearest["price"],
    }


# ============================================================
# 15M MASTER ANALYSIS
# ============================================================

def analyze_15m(symbol, candles):
    if len(candles) < 80:
        return {
            "decision": "WAIT",
            "score": 0,
            "reason": "not enough 15M candles",
        }

    c0 = candles[-1]
    c1 = candles[-2]

    closes = [c["close"] for c in candles]

    price = c0["close"]

    ema20 = ema(closes, 20)
    ema50 = ema(closes, 50)

    current_atr = atr(candles, 14)
    rsi = rsi_wilder(closes, 14)

    if None in (ema20, ema50, current_atr, rsi):
        return {
            "decision": "WAIT",
            "score": 0,
            "reason": "indicator unavailable",
        }

    pattern = candle_features(c0, c1)

    call = 0
    put = 0

    reasons_call = []
    reasons_put = []

    # Trend.
    if price > ema20:
        call += 15
        reasons_call.append("price>EMA20")

    if price > ema50:
        call += 20
        reasons_call.append("price>EMA50")

    if price < ema20:
        put += 15
        reasons_put.append("price<EMA20")

    if price < ema50:
        put += 20
        reasons_put.append("price<EMA50")

    if ema20 > ema50:
        call += 10
        reasons_call.append("EMA20>EMA50")

    if ema20 < ema50:
        put += 10
        reasons_put.append("EMA20<EMA50")

    # RSI is a supporting factor, not a hard blocker.
    if 50 <= rsi <= 68:
        call += 8
        reasons_call.append(f"RSI {rsi:.1f}")

    if 32 <= rsi <= 50:
        put += 8
        reasons_put.append(f"RSI {rsi:.1f}")

    # Candle.
    if pattern["strong_bull"] or pattern["hammer"] or pattern["bull_engulf"]:
        call += 12
        reasons_call.append("bullish candle")

    if pattern["strong_bear"] or pattern["shooting_star"] or pattern["bear_engulf"]:
        put += 12
        reasons_put.append("bearish candle")

    # Zone.
    preliminary = "CALL" if call >= put else "PUT"

    z_call = zone_analysis(candles, price, "CALL")
    z_put = zone_analysis(candles, price, "PUT")

    call += z_call["score"]
    put += z_put["score"]

    if z_call["score"] > 10:
        reasons_call.append(
            f"ZONE {z_call['state']}"
        )

    if z_put["score"] > 10:
        reasons_put.append(
            f"ZONE {z_put['state']}"
        )

    # Direction.
    if call > put and call >= 58:
        decision = "CALL"
        score = min(99, call)
        reasons = reasons_call

    elif put > call and put >= 58:
        decision = "PUT"
        score = min(99, put)
        reasons = reasons_put

    else:
        return {
            "decision": "WATCH",
            "score": max(call, put),
            "price": price,
            "atr": current_atr,
            "rsi": rsi,
            "ema20": ema20,
            "ema50": ema50,
            "zone_state": "MIXED",
            "reasons": "15M conflict",
            "candle_time": c0["datetime"],
        }

    return {
        "decision": decision,
        "score": score,
        "price": price,
        "atr": current_atr,
        "rsi": rsi,
        "ema20": ema20,
        "ema50": ema50,
        "zone_state": (
            z_call["state"] if decision == "CALL"
            else z_put["state"]
        ),
        "zone_level": (
            z_call["level"] if decision == "CALL"
            else z_put["level"]
        ),
        "reasons": " | ".join(reasons),
        "candle_time": c0["datetime"],
    }


# ============================================================
# 5M ENTRY CONFIRMATION
# ============================================================

def analyze_5m(symbol, candles, master_direction):
    if len(candles) < 70:
        return {
            "decision": "UNKNOWN",
            "score": 50,
            "reason": "not enough 5M data",
        }

    c0 = candles[-1]
    c1 = candles[-2]

    closes = [c["close"] for c in candles]

    ema20 = ema(closes, 20)
    ema50 = ema(closes, 50)
    rsi = rsi_wilder(closes, 14)

    pattern = candle_features(c0, c1)

    call = 0
    put = 0
    reasons_call = []
    reasons_put = []

    if ema20 is not None and c0["close"] > ema20:
        call += 15
        reasons_call.append("5M>EMA20")

    if ema20 is not None and c0["close"] < ema20:
        put += 15
        reasons_put.append("5M<EMA20")

    if ema50 is not None and c0["close"] > ema50:
        call += 10
        reasons_call.append("5M>EMA50")

    if ema50 is not None and c0["close"] < ema50:
        put += 10
        reasons_put.append("5M<EMA50")

    if rsi is not None:
        if rsi > 50:
            call += 8
            reasons_call.append(f"RSI {rsi:.1f}")

        if rsi < 50:
            put += 8
            reasons_put.append(f"RSI {rsi:.1f}")

    if pattern["strong_bull"] or pattern["bull_engulf"] or pattern["hammer"]:
        call += 15
        reasons_call.append("bullish candle")

    if pattern["strong_bear"] or pattern["bear_engulf"] or pattern["shooting_star"]:
        put += 15
        reasons_put.append("bearish candle")

    # Master direction receives a bonus, not a forced direction.
    if master_direction == "CALL":
        call += 12
        reasons_call.append("15M master CALL")

    elif master_direction == "PUT":
        put += 12
        reasons_put.append("15M master PUT")

    if call > put:
        direction = "CALL"
        score = min(99, call)
        reasons = reasons_call

    elif put > call:
        direction = "PUT"
        score = min(99, put)
        reasons = reasons_put

    else:
        direction = "UNKNOWN"
        score = 50
        reasons = ["5M balanced"]

    return {
        "decision": direction,
        "score": score,
        "rsi": rsi,
        "candle_time": c0["datetime"],
        "reasons": " | ".join(reasons),
    }


# ============================================================
# SIGNAL DECISION
# ============================================================

def scan_symbol(symbol):
    candles15 = get_candles(
        symbol,
        MASTER_INTERVAL,
        period="5d",
        closed_only=True,
    )

    if len(candles15) < 80:
        return None

    master = analyze_15m(symbol, candles15)

    if master["decision"] not in ("CALL", "PUT"):
        return None

    candles5 = get_candles(
        symbol,
        ENTRY_INTERVAL,
        period="5d",
        closed_only=True,
    )

    if len(candles5) < 70:
        return None

    timing = analyze_5m(
        symbol,
        candles5,
        master["decision"],
    )

    # 5M does not have to agree to keep the 15M opportunity alive.
    # It only affects timing score.
    if timing["decision"] == master["decision"]:
        final_score = min(
            99,
            master["score"] * 0.70
            + timing["score"] * 0.30
            + 5
        )
        context = "5M_CONFIRM"

    elif timing["decision"] == "UNKNOWN":
        final_score = master["score"] * 0.75
        context = "5M_UNKNOWN"

    else:
        final_score = master["score"] * 0.65
        context = "5M_PULLBACK"

    final_score = round(final_score, 1)

    # Avoid excessive filtering.
    if master["score"] < 62:
        return None

    return {
        "symbol": symbol,
        "decision": master["decision"],
        "score": final_score,
        "master_score": master["score"],
        "entry_score": timing["score"],
        "entry_context": context,
        "price": master["price"],
        "atr": master["atr"],
        "zone_state": master["zone_state"],
        "zone_level": master.get("zone_level"),
        "reasons_15m": master["reasons"],
        "reasons_5m": timing["reasons"],
        "signal_candle_15m": master["candle_time"],
        "last_5m_candle": timing["candle_time"],
        "created_at": now_text(),
        "created_ts": time.time(),
    }


# ============================================================
# SERIES
# ============================================================

def series_key(signal):
    return (
        signal["symbol"],
        signal["decision"],
        signal["signal_candle_15m"],
    )


def has_active_series(symbol):
    with LOCK:
        return any(
            x["symbol"] == symbol
            for x in ACTIVE_SERIES
        )


def create_series(signal):
    series_id = (
        f"{signal['symbol'].replace('/', '')}_"
        f"{int(signal['created_ts'])}"
    )

    tracker = {
        "series_id": series_id,
        "symbol": signal["symbol"],
        "master_direction": signal["decision"],
        "master_score": signal["master_score"],
        "signal_score": signal["score"],
        "entry_context": signal["entry_context"],
        "zone_state": signal["zone_state"],
        "zone_level": signal.get("zone_level"),
        "signal_candle_15m": signal["signal_candle_15m"],
        "signal_time": signal["created_at"],
        "signal_ts": signal["created_ts"],
        "opportunity": 1,
        "completed": False,
        "wins": 0,
        "losses": 0,
        "draws": 0,
        "max_mfe": 0.0,
        "max_mae": 0.0,
        "processed_5m": [],
    }

    with LOCK:
        ACTIVE_SERIES.append(tracker)
        STATS["signals"] += 1

    return tracker


# ============================================================
# 5M RESULT ENGINE
# ============================================================

def find_first_closed_5m_after(candles, timestamp):
    """
    Signal ระหว่างแท่ง:
    หาแท่ง 5M ที่มี signal_ts อยู่ระหว่าง open และ close
    แล้วใช้ close ของแท่งนั้นเป็นผลไม้แรก

    เนื่องจากข้อมูล candle มี timestamp เป็นเวลาเปิดแท่ง
    เราจึงใช้ timestamp + 300 เป็นเวลาปิดโดยประมาณ
    """

    for c in candles:
        start = c["timestamp"]
        end = start + FIVE_MIN_SECONDS

        if start <= timestamp < end:
            return c

    return None


def find_closed_candles_after(candles, timestamp):
    result = []

    for c in candles:
        close_time = c["timestamp"] + FIVE_MIN_SECONDS

        if close_time > timestamp:
            result.append(c)

    return result


def evaluate_one_opportunity(tracker):
    symbol = tracker["symbol"]

    candles = get_candles(
        symbol,
        ENTRY_INTERVAL,
        period="2d",
        closed_only=True,
    )

    if len(candles) < 20:
        return None

    signal_ts = tracker["signal_ts"]
    step = tracker["opportunity"]

    # First opportunity:
    # If signal occurred inside a candle, that candle is OPP1.
    first_candle = find_first_closed_5m_after(
        candles,
        signal_ts,
    )

    if step == 1 and first_candle is not None:
        candidate = first_candle
    else:
        candidates = find_closed_candles_after(
            candles,
            signal_ts,
        )

        # Avoid candles already consumed.
        used = set(tracker["processed_5m"])

        candidates = [
            c for c in candidates
            if c["datetime"] not in used
        ]

        if not candidates:
            return None

        candidate = candidates[0]

    if candidate["datetime"] in tracker["processed_5m"]:
        return None

    entry_price = (
        tracker.get("entry_price")
        if step == 1 and tracker.get("entry_price") is not None
        else candidate["open"]
    )

    if entry_price is None:
        entry_price = candidate["open"]

    direction = tracker["master_direction"]

    close_price = candidate["close"]

    # Exact candle-close result.
    if close_price > entry_price:
        raw_result = "WIN" if direction == "CALL" else "LOSS"

    elif close_price < entry_price:
        raw_result = "WIN" if direction == "PUT" else "LOSS"

    else:
        raw_result = "DRAW"

    mfe = (
        candidate["high"] - entry_price
        if direction == "CALL"
        else entry_price - candidate["low"]
    )

    mae = (
        entry_price - candidate["low"]
        if direction == "CALL"
        else candidate["high"] - entry_price
    )

    return {
        "result": raw_result,
        "step": step,
        "candle": candidate,
        "entry_price": entry_price,
        "close_price": close_price,
        "mfe": max(0.0, mfe),
        "mae": max(0.0, mae),
    }


def record_opportunity(tracker, outcome):
    tracker["processed_5m"].append(
        outcome["candle"]["datetime"]
    )

    tracker["max_mfe"] = max(
        tracker.get("max_mfe", 0),
        outcome["mfe"],
    )

    tracker["max_mae"] = max(
        tracker.get("max_mae", 0),
        outcome["mae"],
    )

    result = outcome["result"]

    if result == "WIN":
        tracker["wins"] += 1
        STATS["wins"] += 1

    elif result == "LOSS":
        tracker["losses"] += 1
        STATS["losses"] += 1

    else:
        tracker["draws"] += 1
        STATS["draws"] += 1

    return result


def finalize_series(tracker, final_status):
    record = {
        "type": "SERIES",
        "series_id": tracker["series_id"],
        "symbol": tracker["symbol"],
        "decision": tracker["master_direction"],
        "master_score": tracker["master_score"],
        "signal_score": tracker["signal_score"],
        "entry_context": tracker["entry_context"],
        "zone_state": tracker["zone_state"],
        "zone_level": tracker["zone_level"],
        "signal_candle_15m": tracker["signal_candle_15m"],
        "signal_time": tracker["signal_time"],
        "status": final_status,
        "wins": tracker["wins"],
        "losses": tracker["losses"],
        "draws": tracker["draws"],
        "opportunities_used": tracker["opportunity"],
        "max_mfe": tracker["max_mfe"],
        "max_mae": tracker["max_mae"],
        "recorded_at": now_text(),
    }

    with LOCK:
        HISTORICAL_MEMORY.append(record)
        STATS["series_completed"] += 1

        if final_status == "SERIES_WIN":
            STATS["series_wins"] += 1

        if final_status == "FULL_LOSS":
            STATS["series_full_loss"] += 1

    save_memory()

    icon = "🟢" if final_status == "SERIES_WIN" else "🔴"

    send_discord(
        f"{icon} **[SIGZY SERIES COMPLETE]**\n"
        f"💱 {tracker['symbol']} | {tracker['master_direction']}\n"
        f"🏁 **{final_status}**\n"
        f"🎯 WIN: {tracker['wins']} | "
        f"LOSS: {tracker['losses']} | "
        f"DRAW: {tracker['draws']}\n"
        f"🔢 ใช้โอกาส: {tracker['opportunity']}/{MAX_OPPORTUNITIES}\n"
        f"🧭 Zone: {tracker['zone_state']}\n"
        f"📊 15M Score: {tracker['master_score']}/100\n"
        f"🕐 Signal: {tracker['signal_time']}"
    )


def tracker_loop():
    while True:
        try:
            with LOCK:
                trackers = list(ACTIVE_SERIES)

            if trackers:
                for tracker in trackers:

                    outcome = evaluate_one_opportunity(tracker)

                    if outcome is None:
                        continue

                    result = record_opportunity(
                        tracker,
                        outcome,
                    )

                    candle = outcome["candle"]

                    log(
                        f"TRACKER {tracker['symbol']} "
                        f"OPP{tracker['opportunity']} "
                        f"{result} "
                        f"entry={outcome['entry_price']:.6f} "
                        f"close={outcome['close_price']:.6f}"
                    )

                    send_discord(
                        f"📌 **[5M OPPORTUNITY RESULT]**\n"
                        f"💱 {tracker['symbol']}\n"
                        f"🧭 Master: **{tracker['master_direction']}**\n"
                        f"🎯 OPP{tracker['opportunity']}: "
                        f"**{result}**\n"
                        f"💰 Entry: **{outcome['entry_price']:.6f}**\n"
                        f"🏁 Close: **{outcome['close_price']:.6f}**\n"
                        f"🕐 Candle: {candle['datetime']}\n"
                        f"📈 MFE: {outcome['mfe']:.6f}\n"
                        f"📉 MAE: {outcome['mae']:.6f}"
                    )

                    if result == "WIN":
                        finalize_series(
                            tracker,
                            "SERIES_WIN",
                        )

                        with LOCK:
                            if tracker in ACTIVE_SERIES:
                                ACTIVE_SERIES.remove(tracker)

                    elif tracker["opportunity"] >= MAX_OPPORTUNITIES:
                        finalize_series(
                            tracker,
                            "FULL_LOSS",
                        )

                        with LOCK:
                            if tracker in ACTIVE_SERIES:
                                ACTIVE_SERIES.remove(tracker)

                    else:
                        # Do not automatically reverse direction.
                        # The next opportunity will be evaluated by
                        # the 5M candle context.
                        tracker["opportunity"] += 1

                        # Re-evaluate next closed candle.
                        log(
                            f"{tracker['symbol']} -> "
                            f"move to OPP{tracker['opportunity']}"
                        )

        except Exception as e:
            log(f"Tracker loop error: {e}")

        time.sleep(20)


# ============================================================
# SIGNAL SCANNER
# ============================================================

def scanner_loop():
    while True:
        try:
            best = []

            for symbol in SYMBOLS:
                try:
                    signal = scan_symbol(symbol)

                    if signal is not None:
                        best.append(signal)

                except Exception as e:
                    log(f"Scanner {symbol} error: {e}")

            # Sort strongest setups first.
            best.sort(
                key=lambda x: x["score"],
                reverse=True,
            )

            # Send only new setup per 15M candle.
            for signal in best:

                key = series_key(signal)

                if key in SENT_SIGNALS:
                    continue

                # Same pair is allowed again only if there is
                # a genuinely new 15M candle.
                if has_active_series(signal["symbol"]):
                    continue

                SENT_SIGNALS.add(key)

                tracker = create_series(signal)

                ai_text = ai_comment(
                    signal["symbol"],
                    signal["decision"],
                    signal["score"],
                    signal["zone_state"],
                )

                zone_text = signal["zone_state"]

                if signal.get("zone_level") is not None:
                    zone_text += (
                        f" @ {signal['zone_level']:.6f}"
                    )

                icon = (
                    "🟢"
                    if signal["decision"] == "CALL"
                    else "🔴"
                )

                message = (
                    f"🚨 **[SIGZY NEW SIGNAL]** {icon}\n\n"
                    f"💱 คู่เงิน: **{signal['symbol']}**\n"
                    f"📌 ทิศทาง 15M: **{signal['decision']}**\n"
                    f"🏆 Final Score: **{signal['score']}/100**\n"
                    f"📊 15M Score: **{signal['master_score']}/100**\n"
                    f"⏱️ 5M Score: **{signal['entry_score']}/100**\n"
                    f"🧭 5M Context: **{signal['entry_context']}**\n"
                    f"🧱 Zone: **{zone_text}**\n"
                    f"💰 Price: **{signal['price']:.6f}**\n\n"
                    f"🔎 15M: {signal['reasons_15m']}\n"
                    f"🔎 5M: {signal['reasons_5m']}\n\n"
                    f"🤖 {ai_text}\n\n"
                    f"🎯 Series: **สูงสุด {MAX_OPPORTUNITIES} ไม้**\n"
                    f"🕐 Signal: **{signal['created_at']}**"
                )

                send_discord(message)

                log(
                    f"NEW {signal['symbol']} "
                    f"{signal['decision']} "
                    f"score={signal['score']} "
                    f"zone={signal['zone_state']}"
                )

                # Only one new setup per scan cycle.
                # This prevents a flood of signals.
                break

        except Exception as e:
            log(f"Scanner loop error: {e}")

        time.sleep(SCAN_SECONDS)


# ============================================================
# REPORTER
# ============================================================

def calculate_stats():
    with LOCK:
        total = (
            STATS["wins"]
            + STATS["losses"]
            + STATS["draws"]
        )

        if total:
            wr = STATS["wins"] / total * 100
        else:
            wr = 0

        return {
            **STATS,
            "total_opportunities": total,
            "win_rate": round(wr, 2),
            "active_series": len(ACTIVE_SERIES),
            "memory_records": len(HISTORICAL_MEMORY),
        }


def reporter_loop():
    while True:
        try:
            s = calculate_stats()

            send_discord(
                f"📊 **[SIGZY STATUS]**\n"
                f"Signals: {s['signals']}\n"
                f"5M WIN: {s['wins']} | "
                f"LOSS: {s['losses']} | "
                f"DRAW: {s['draws']}\n"
                f"Win Rate: **{s['win_rate']}%**\n"
                f"Series Completed: {s['series_completed']}\n"
                f"Series WIN: {s['series_wins']}\n"
                f"Series Full Loss: {s['series_full_loss']}\n"
                f"Active Series: {s['active_series']}\n"
                f"Memory: {s['memory_records']}"
            )

        except Exception as e:
            log(f"Reporter error: {e}")

        # 30 minutes.
        time.sleep(1800)


# ============================================================
# HEALTH SERVER FOR RAILWAY
# ============================================================

class HealthHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        if self.path in ("/", "/health", "/status"):

            stats = calculate_stats()

            body = {
                "status": "running",
                "time_thai": now_text(),
                "symbols": SYMBOLS,
                "active_series": stats["active_series"],
                "memory_records": stats["memory_records"],
                "wins": stats["wins"],
                "losses": stats["losses"],
                "draws": stats["draws"],
                "win_rate": stats["win_rate"],
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

        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        return


def run_health_server():
    try:
        server = HTTPServer(
            ("0.0.0.0", PORT),
            HealthHandler,
        )

        log(f"Health server listening on 0.0.0.0:{PORT}")

        server.serve_forever()

    except Exception as e:
        log(f"Health server error: {e}")


# ============================================================
# MAIN
# ============================================================

def startup_message():
    msg = (
        "🚀 **SIGZY STARTED**\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "🧭 15M = Trend + Historical Zone + Break/Flip\n"
        "⏱️ 5M = Entry / Closed Candle Result\n"
        "🎯 Series = maximum 3 opportunities\n"
        "🧠 AI = supporting analysis only\n"
        "💾 Memory = persistent\n"
        f"📡 Symbols = {len(SYMBOLS)}\n"
        f"⏳ Scanner = {SCAN_SECONDS}s\n"
        f"🌐 Port = {PORT}\n"
        "━━━━━━━━━━━━━━━━━━━━"
    )

    send_discord(msg)


def main():
    log("=" * 65)
    log("SIGZY 15M + 5M ZONE FLIP STARTING")
    log("=" * 65)

    load_memory()

    # Health server.
    Thread(
        target=run_health_server,
        daemon=True,
    ).start()

    # Tracker.
    Thread(
        target=tracker_loop,
        daemon=True,
    ).start()

    # Scanner.
    Thread(
        target=scanner_loop,
        daemon=True,
    ).start()

    # Reporter.
    Thread(
        target=reporter_loop,
        daemon=True,
    ).start()

    startup_message()

    # Keep main process alive.
    while True:
        try:
            time.sleep(60)

        except KeyboardInterrupt:
            log("Stopping...")
            break

        except Exception as e:
            log(f"Main keepalive error: {e}")


if __name__ == "__main__":
    main()
'''

path = Path("/mnt/data/main.py")
path.write_text(code, encoding="utf-8")

# Also create a minimal requirements file suitable for Railway/Python 3.10.
requirements = """requests>=2.31.0
yfinance>=0.2.40
google-genai>=1.0.0
"""
req_path = Path("/mnt/data/requirements.txt")
req_path.write_text(requirements, encoding="utf-8")

print(f"สร้างไฟล์สำเร็จ: {path}")
print(f"สร้างไฟล์สำเร็จ: {req_path}")
print(f"main.py ขนาด {path.stat().st_size:,} bytes")
