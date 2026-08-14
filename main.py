from pathlib import Path

code = r'''# -*- coding: utf-8 -*-
"""
TRADEIFY.py
15M MASTER + 5M ENTRY + 3 OPPORTUNITIES + MEMORY + DISCORD + THAI TIME
LIVE / OTC AUTO MODE

สำคัญ:
- โหมด OTC ในไฟล์นี้เป็น "โหมดการทำงาน" ไม่ใช่ข้อมูลราคาจาก 8X โดยตรง
- yfinance ไม่มีฟีด OTC ของ 8X ดังนั้น OTC ที่ทดสอบด้วยไฟล์นี้ใช้ข้อมูล FX สาธารณะเป็น proxy
- ห้ามตีความ Historical Win Rate เป็นการการันตีกำไร
- WIN/LOSS จะตัดสินจากแท่ง 5M ที่ "ปิดแล้ว" เท่านั้น
- เมื่อ signal ออกหลังแท่งปัจจุบันปิด ระบบจะเตรียมเข้าแท่ง 5M ถัดไป
- หลังแพ้ไม้ 1 จะยังคงทิศทางเดิมสำหรับไม้ 2/3 ตามกติกา Series
- ครบ 3 ไม้ = จบ Series
"""

import os
import json
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

# ============================================================
# CONFIG
# ============================================================

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

PORT = int(os.getenv("PORT", "8080"))
SCAN_SECONDS = int(os.getenv("SCAN_SECONDS", "60"))
REPORT_SECONDS = int(os.getenv("REPORT_SECONDS", "1800"))

MEMORY_FILE = os.getenv("MEMORY_FILE", "tradeify_memory.json")

# AUTO = เสาร์/อาทิตย์เป็น OTC, วันธรรมดาเป็น LIVE
# LIVE / OTC = บังคับโหมด
MARKET_MODE = os.getenv("MARKET_MODE", "AUTO").upper()

MAX_OPPORTUNITIES = 3
TF5_SECONDS = 300
MIN_HISTORY_FOR_RATE = int(os.getenv("MIN_HISTORY_FOR_RATE", "10"))

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

LOCK = Lock()
HISTORICAL_MEMORY = []
ACTIVE_SERIES = []
SENT_SIGNALS = set()

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
# TIME / MODE
# ============================================================

THAI_TZ = timezone(timedelta(hours=7))

def now_utc():
    return datetime.now(timezone.utc)

def now_thai():
    return now_utc().astimezone(THAI_TZ)

def thai_text(dt=None):
    dt = dt or now_thai()
    return dt.strftime("%Y-%m-%d %H:%M:%S")

def thai_hm(dt=None):
    dt = dt or now_thai()
    return dt.strftime("%H:%M")

def mode_now():
    if MARKET_MODE in ("LIVE", "OTC"):
        return MARKET_MODE

    # AUTO: เสาร์/อาทิตย์ = OTC, จันทร์-ศุกร์ = LIVE
    return "OTC" if now_thai().weekday() >= 5 else "LIVE"

def utc_to_thai(ts):
    return datetime.fromtimestamp(ts, timezone.utc).astimezone(THAI_TZ)

def log(msg):
    print(f"[{thai_text()}] {msg}", flush=True)

# ============================================================
# DISCORD
# ============================================================

def send_discord(message):
    if not DISCORD_WEBHOOK_URL:
        log("Discord: webhook not configured")
        return False

    try:
        r = requests.post(
            DISCORD_WEBHOOK_URL,
            json={"content": message[:1900]},
            timeout=10,
        )

        if r.status_code in (200, 204):
            return True

        log(f"Discord HTTP {r.status_code}: {r.text[:200]}")
        return False

    except Exception as e:
        log(f"Discord error: {e}")
        return False

# ============================================================
# GEMINI - OPTIONAL COMMENT ONLY
# ============================================================

AI_CLIENT = None

if genai and GEMINI_API_KEY:
    try:
        AI_CLIENT = genai.Client(api_key=GEMINI_API_KEY)
        log("Gemini ready")
    except Exception as e:
        log(f"Gemini init failed: {e}")

def ai_comment(signal):
    if AI_CLIENT is None:
        return "AI: OFF"

    try:
        prompt = (
            "Give a short Thai market-context comment, max 2 lines. "
            "Do not give guaranteed-profit language. "
            f"Pair={signal['symbol']}, mode={signal['mode']}, "
            f"direction={signal['decision']}, "
            f"15M_RSI={signal['rsi15']:.1f}, "
            f"5M_RSI={signal['rsi5']:.1f}, "
            f"zone={signal['zone_state']}."
        )

        response = AI_CLIENT.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
        )

        text = getattr(response, "text", None)
        return text.strip() if text else "AI: no response"

    except Exception as e:
        # AI ไม่ควรทำให้ระบบ signal หยุด
        return f"AI unavailable: {str(e)[:80]}"

# ============================================================
# MEMORY
# ============================================================

def load_memory():
    global HISTORICAL_MEMORY

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
        df = df.copy()

        if getattr(df.index, "tz", None) is not None:
            df.index = df.index.tz_convert("UTC").tz_localize(None)

        return df.dropna(subset=["Open", "High", "Low", "Close"])

    except Exception:
        return df

def get_candles(symbol, interval, period="5d"):
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
        log(f"Yahoo {symbol} {interval}: {e}")
        return []

def candle_close_ts(c):
    return c["timestamp"] + TF5_SECONDS

def is_closed_5m(c, current_ts=None):
    current_ts = current_ts or time.time()
    return candle_close_ts(c) <= current_ts

def closed_only(candles):
    now_ts = time.time()
    return [c for c in candles if is_closed_5m(c, now_ts)]

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
        d = values[i] - values[i - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))

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

        trs.append(max(
            c["high"] - c["low"],
            abs(c["high"] - p["close"]),
            abs(c["low"] - p["close"]),
        ))

    return sum(trs[-period:]) / period

# ============================================================
# CANDLE PATTERN
# ============================================================

def candle_features(c0, c1):
    body = abs(c0["close"] - c0["open"])
    rng = max(c0["high"] - c0["low"], 1e-12)

    upper = c0["high"] - max(c0["open"], c0["close"])
    lower = min(c0["open"], c0["close"]) - c0["low"]

    ratio = body / rng

    return {
        "strong_bull": c0["close"] > c0["open"] and ratio >= 0.65,
        "strong_bear": c0["close"] < c0["open"] and ratio >= 0.65,
        "hammer": lower >= body * 2 and upper <= rng * 0.25 and ratio <= 0.45,
        "shooting_star": upper >= body * 2 and lower <= rng * 0.25 and ratio <= 0.45,
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

        high_ok = all(
            h >= data[j]["high"]
            for j in range(i - 2, i + 3)
            if j != i
        )

        low_ok = all(
            l <= data[j]["low"]
            for j in range(i - 2, i + 3)
            if j != i
        )

        if high_ok:
            zones.append({
                "type": "RESISTANCE",
                "price": h,
                "timestamp": data[i]["timestamp"],
            })

        if low_ok:
            zones.append({
                "type": "SUPPORT",
                "price": l,
                "timestamp": data[i]["timestamp"],
            })

    return zones

def zone_analysis(candles, price, direction):
    current_atr = atr(candles, 14)

    if not current_atr or current_atr <= 0:
        return {"state": "NONE", "score": 0, "level": None}

    zones = build_zones(candles)
    tolerance = current_atr * 0.35

    candidates = [
        (abs(price - z["price"]), z)
        for z in zones
        if abs(price - z["price"]) <= tolerance
    ]

    if not candidates:
        return {"state": "NONE", "score": 0, "level": None}

    candidates.sort(key=lambda x: x[0])
    distance, nearest = candidates[0]

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

    if nearest["type"] == "SUPPORT":
        if direction == "CALL":
            score += 15

        if broke_below and price > nearest["price"]:
            state = "FLIPPED_SUPPORT"
            score += 20

    if distance <= tolerance * 0.50:
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
    rsi = rsi_wilder(closes, 14)
    current_atr = atr(candles, 14)

    if None in (e20, e50, rsi, current_atr):
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
        setup_strength = min(99, call)
        reasons = rc
        zone = zc
    elif put > call and put >= 58:
        decision = "PUT"
        setup_strength = min(99, put)
        reasons = rp
        zone = zp
    else:
        return None

    return {
        "decision": decision,
        "setup_strength": setup_strength,
        "price": price,
        "atr": current_atr,
        "rsi": rsi,
        "zone_state": zone["state"],
        "zone_level": zone["level"],
        "reasons": " | ".join(reasons),
        "candle_time": c0["datetime"],
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
    rsi = rsi_wilder(closes, 14)

    if e20 is None or e50 is None or rsi is None:
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
        "reasons": " | ".join(reasons),
    }

# ============================================================
# HISTORICAL RATE
# ============================================================

def setup_signature(symbol, mode, direction, zone_state):
    return (
        symbol,
        mode,
        direction,
        zone_state,
    )

def historical_stats(signature):
    """
    นับเฉพาะ SERIES ที่จบแล้ว และ signature ตรงกัน
    Series Win = ชนะภายใน 1-3 opportunities
    """

    records = []

    with LOCK:
        for r in HISTORICAL_MEMORY:
            if r.get("type") != "SERIES":
                continue

            sig = setup_signature(
                r.get("symbol"),
                r.get("mode"),
                r.get("decision"),
                r.get("zone_state"),
            )

            if sig == signature:
                records.append(r)

    total = len(records)
    series_wins = sum(
        1 for r in records
        if r.get("status") == "SERIES_WIN"
    )

    first_win = sum(
        1 for r in records
        if r.get("first_opportunity_result") == "WIN"
    )

    if total >= MIN_HISTORY_FOR_RATE:
        series_rate = round(series_wins / total * 100, 1)
        first_rate = round(first_win / total * 100, 1)
        confidence = "USABLE"
    else:
        series_rate = None
        first_rate = None
        confidence = "INSUFFICIENT_DATA"

    return {
        "samples": total,
        "series_win_rate": series_rate,
        "first_win_rate": first_rate,
        "confidence": confidence,
    }

# ============================================================
# SIGNAL
# ============================================================

def scan_symbol(symbol):
    c15 = closed_only(
        get_candles(symbol, "15m", "5d")
    )

    if len(c15) < 80:
        return None

    master = analyze_15m(c15)

    if not master:
        return None

    c5 = closed_only(
        get_candles(symbol, "5m", "5d")
    )

    if len(c5) < 70:
        return None

    timing = analyze_5m(c5, master["decision"])

    if not timing:
        return None

    if timing["decision"] == master["decision"]:
        context = "5M_CONFIRM"
    elif timing["decision"] == "UNKNOWN":
        context = "5M_UNKNOWN"
    else:
        context = "5M_PULLBACK"

    # ไม่ hard-block ถ้า 5M สวนชั่วคราว
    if master["setup_strength"] < 62:
        return None

    mode = mode_now()

    signature = setup_signature(
        symbol,
        mode,
        master["decision"],
        master["zone_state"],
    )

    hist = historical_stats(signature)

    # Signal time = ตอนระบบตรวจพบ setup
    signal_ts = time.time()

    # เข้าแท่ง 5M ถัดไปจากแท่งที่ปิดล่าสุด
    latest5 = c5[-1]
    next_open_ts = latest5["timestamp"] + TF5_SECONDS
    next_close_ts = next_open_ts + TF5_SECONDS

    return {
        "symbol": symbol,
        "mode": mode,
        "decision": master["decision"],
        "setup_strength": round(master["setup_strength"], 1),
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
        "signal_ts": signal_ts,
        "next_open_ts": next_open_ts,
        "next_close_ts": next_close_ts,
        "history": hist,
        "created_at": thai_text(),
    }

# ============================================================
# SERIES
# ============================================================

def has_active_series(symbol):
    with LOCK:
        return any(
            s["symbol"] == symbol
            for s in ACTIVE_SERIES
        )

def create_series(signal):
    series_id = (
        f"{signal['symbol'].replace('/', '')}_"
        f"{int(signal['signal_ts'])}"
    )

    tracker = {
        "type": "ACTIVE_SERIES",
        "series_id": series_id,
        "symbol": signal["symbol"],
        "mode": signal["mode"],
        "master_direction": signal["decision"],
        "setup_strength": signal["setup_strength"],
        "entry_context": signal["entry_context"],
        "zone_state": signal["zone_state"],
        "zone_level": signal["zone_level"],
        "signal_time": signal["created_at"],
        "signal_ts": signal["signal_ts"],

        # OPP1 = แท่งถัดไป
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

    with LOCK:
        ACTIVE_SERIES.append(tracker)
        STATS["signals"] += 1

    return tracker

# ============================================================
# OPPORTUNITY ENGINE
# ============================================================

def find_candle_for_entry(candles, entry_ts):
    """
    หาแท่งที่เปิดตรง/หลังเวลา entry_ts
    และแท่งนั้นต้องปิดแล้วจึงจะนำมาตัดสินผล
    """

    candidates = [
        c for c in candles
        if c["timestamp"] >= entry_ts
        and c["datetime"] not in []
    ]

    if not candidates:
        return None

    c = candidates[0]

    if not is_closed_5m(c):
        return None

    return c

def evaluate_opportunity(tracker):
    candles = closed_only(
        get_candles(
            tracker["symbol"],
            "5m",
            "2d",
        )
    )

    if len(candles) < 20:
        return None

    # ใช้ next_entry_ts ที่ระบบกำหนดไว้
    entry_ts = tracker["next_entry_ts"]

    candidate = find_candle_for_entry(candles, entry_ts)

    if not candidate:
        return None

    if candidate["datetime"] in tracker["processed_5m"]:
        return None

    entry_price = candidate["open"]
    close_price = candidate["close"]
    direction = tracker["master_direction"]

    if close_price > entry_price:
        result = "WIN" if direction == "CALL" else "LOSS"
    elif close_price < entry_price:
        result = "WIN" if direction == "PUT" else "LOSS"
    else:
        result = "DRAW"

    if direction == "CALL":
        mfe = candidate["high"] - entry_price
        mae = entry_price - candidate["low"]
    else:
        mfe = entry_price - candidate["low"]
        mae = candidate["high"] - entry_price

    return {
        "result": result,
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
        tracker["max_mfe"],
        outcome["mfe"],
    )

    tracker["max_mae"] = max(
        tracker["max_mae"],
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
        "first_opportunity_result": tracker["first_opportunity_result"],
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

    hist = historical_stats(
        setup_signature(
            tracker["symbol"],
            tracker["mode"],
            tracker["master_direction"],
            tracker["zone_state"],
        )
    )

    rate_text = (
        f"{hist['series_win_rate']}%"
        if hist["series_win_rate"] is not None
        else "INSUFFICIENT_DATA"
    )

    icon = "🟢" if status == "SERIES_WIN" else "🔴"

    send_discord(
        f"{icon} **[TRADEIFY SERIES COMPLETE]**\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💱 คู่: **{tracker['symbol']}**\n"
        f"🌐 Mode: **{tracker['mode']}**\n"
        f"📌 Direction: **{tracker['master_direction']}**\n"
        f"🏁 Status: **{status}**\n"
        f"🎯 WIN: **{tracker['wins']}**\n"
        f"❌ LOSS: **{tracker['losses']}**\n"
        f"➖ DRAW: **{tracker['draws']}**\n"
        f"🔢 Opportunities: **{tracker['opportunity']}/3**\n"
        f"📈 Historical Series Win Rate: **{rate_text}**\n"
        f"📚 Historical Samples: **{hist['samples']}**\n"
        f"🧭 Zone: **{tracker['zone_state']}**\n"
        f"🕐 เวลาไทย: **{thai_text()}**"
    )

# ============================================================
# TRACKER LOOP
# ============================================================

def tracker_loop():
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

                candle = outcome["candle"]

                send_discord(
                    f"{'🟢' if result == 'WIN' else '🔴' if result == 'LOSS' else '🟡'} "
                    f"**[TRADEIFY 5M RESULT]**\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"💱 **{tracker['symbol']}**\n"
                    f"🌐 Mode: **{tracker['mode']}**\n"
                    f"📌 Master: **{tracker['master_direction']}**\n"
                    f"🎯 OPP: **{tracker['opportunity']}/3**\n"
                    f"🏁 Result: **{result}**\n"
                    f"💰 Entry: **{outcome['entry_price']:.8f}**\n"
                    f"🔚 Close: **{outcome['close_price']:.8f}**\n"
                    f"🕐 Candle UTC: **{candle['datetime']}**\n"
                    f"🇹🇭 เวลาแจ้ง: **{thai_text()}**\n"
                    f"📈 MFE: **{outcome['mfe']:.8f}**\n"
                    f"📉 MAE: **{outcome['mae']:.8f}**\n"
                    f"⚠️ ผลถูกตัดสินหลังแท่ง 5M ปิดแล้วเท่านั้น"
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
                    # แพ้ = เดินหน้าทิศทางเดิม
                    tracker["opportunity"] += 1

                    # เข้าแท่ง 5M ถัดจากแท่งที่เพิ่งตัดสิน
                    next_ts = candle["timestamp"] + TF5_SECONDS

                    tracker["next_entry_ts"] = next_ts
                    tracker["next_close_ts"] = next_ts + TF5_SECONDS

                    log(
                        f"{tracker['symbol']} LOSS -> "
                        f"keep {tracker['master_direction']} "
                        f"for OPP{tracker['opportunity']}"
                    )

                    dt_open = utc_to_thai(next_ts)
                    dt_close = utc_to_thai(next_ts + TF5_SECONDS)

                    send_discord(
                        f"🔁 **[TRADEIFY NEXT OPPORTUNITY]**\n"
                        f"💱 {tracker['symbol']}\n"
                        f"🌐 Mode: {tracker['mode']}\n"
                        f"📌 Direction เดิม: **{tracker['master_direction']}**\n"
                        f"🎯 OPP: **{tracker['opportunity']}/3**\n"
                        f"🟢 เตรียมเข้าแท่งหน้า\n"
                        f"⏰ เข้า: **{thai_hm(dt_open)}**\n"
                        f"🔚 ปิดแท่ง: **{thai_hm(dt_close)}**\n"
                        f"⚠️ รอแท่งปิดก่อนตัดผล"
                    )

        except Exception as e:
            log(f"Tracker error: {e}")

        time.sleep(10)

# ============================================================
# SCANNER LOOP
# ============================================================

def scanner_loop():
    while True:
        try:
            best = []

            for symbol in SYMBOLS:
                try:
                    signal = scan_symbol(symbol)

                    if signal:
                        best.append(signal)

                except Exception as e:
                    log(f"Scanner {symbol}: {e}")

            best.sort(
                key=lambda x: x["setup_strength"],
                reverse=True,
            )

            for signal in best:
                # ใช้แท่ง 15M เป็นตัวกันสัญญาณซ้ำ
                key = (
                    signal["symbol"],
                    signal["mode"],
                    signal["decision"],
                    signal["signal_candle15"],
                )

                if key in SENT_SIGNALS:
                    continue

                if has_active_series(signal["symbol"]):
                    continue

                SENT_SIGNALS.add(key)

                tracker = create_series(signal)

                hist = signal["history"]

                if hist["series_win_rate"] is None:
                    hist_text = "INSUFFICIENT_DATA"
                else:
                    hist_text = f"{hist['series_win_rate']}%"

                dt_open = utc_to_thai(signal["next_open_ts"])
                dt_close = utc_to_thai(signal["next_close_ts"])

                direction_icon = (
                    "🟢" if signal["decision"] == "CALL"
                    else "🔴"
                )

                ai_text = ai_comment(signal)

                message = (
                    f"🚨 **[TRADEIFY SIGNAL]**\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"⏱️ TF: **5M**\n"
                    f"💱 คู่: **{signal['symbol']}**\n\n"
                    f"⭐️ **เตือนเวลา {thai_hm()} น.** ⭐️\n"
                    f"🇹🇭 เวลาไทย\n\n"
                    f"🌐 Mode: **{signal['mode']}**\n"
                    f"📌 Direction: **{signal['decision']}** {direction_icon}\n"
                    f"🟢 **เตรียมซื้อแท่งหน้า**\n\n"
                    f"📊 SETUP STRENGTH: **{signal['setup_strength']}/100**\n"
                    f"📈 15M RSI: **{signal['rsi15']:.1f}**\n"
                    f"📉 5M RSI: **{signal['rsi5']:.1f}**\n"
                    f"⏱️ 5M Context: **{signal['entry_context']}**\n"
                    f"🧭 Zone: **{signal['zone_state']}**\n\n"
                    f"📚 HISTORICAL (เฉพาะ setup นี้)\n"
                    f"• Samples: **{hist['samples']}**\n"
                    f"• Series Win Rate 1–3: **{hist_text}**\n"
                    f"• First Entry Win Rate: **"
                    f"{hist['first_win_rate'] if hist['first_win_rate'] is not None else 'INSUFFICIENT_DATA'}"
                    f"{'%' if hist['first_win_rate'] is not None else ''}**\n\n"
                    f"🎯 Opportunity: **1/3**\n"
                    f"⏰ เข้าแท่ง: **{thai_hm(dt_open)} น.**\n"
                    f"🔚 ปิดแท่ง: **{thai_hm(dt_close)} น.**\n\n"
                    f"🔎 15M: {signal['reasons15']}\n"
                    f"🔎 5M: {signal['reasons5']}\n"
                    f"🤖 {ai_text}\n\n"
                    f"⚠️ **รอแท่งใหม่ก่อนเข้า**\n"
                    f"⚠️ **ผล WIN/LOSS ตัดสินหลังแท่ง 5M ปิดเท่านั้น**\n"
                    f"🕐 Signal: **{signal['created_at']}**"
                )

                send_discord(message)

                log(
                    f"NEW {signal['symbol']} "
                    f"{signal['decision']} "
                    f"mode={signal['mode']} "
                    f"strength={signal['setup_strength']}"
                )

                # 1 signal ต่อ scan cycle
                break

        except Exception as e:
            log(f"Scanner loop error: {e}")

        time.sleep(SCAN_SECONDS)

# ============================================================
# REPORT
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
            "win_rate": round(
                STATS["wins"] / total * 100, 2
            ) if total else None,
            "active_series": len(ACTIVE_SERIES),
            "memory_records": len(HISTORICAL_MEMORY),
        }

def reporter_loop():
    while True:
        try:
            s = calculate_stats()

            wr = (
                f"{s['win_rate']}%"
                if s["win_rate"] is not None
                else "INSUFFICIENT_DATA"
            )

            send_discord(
                f"📊 **[TRADEIFY STATUS]**\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"🇹🇭 เวลาไทย: **{thai_text()}**\n"
                f"📡 Mode ตอนนี้: **{mode_now()}**\n"
                f"📣 Signals: **{s['signals']}**\n"
                f"🟢 WIN: **{s['wins']}**\n"
                f"🔴 LOSS: **{s['losses']}**\n"
                f"🟡 DRAW: **{s['draws']}**\n"
                f"📈 Opportunity Win Rate: **{wr}**\n"
                f"🏁 Series Complete: **{s['series_completed']}**\n"
                f"🟢 Series Win: **{s['series_wins']}**\n"
                f"🔴 Full Loss: **{s['series_full_loss']}**\n"
                f"🔄 Active Series: **{s['active_series']}**\n"
                f"💾 Memory: **{s['memory_records']}**"
            )

        except Exception as e:
            log(f"Reporter error: {e}")

        time.sleep(REPORT_SECONDS)

# ============================================================
# HEALTH SERVER
# ============================================================

class HealthHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        if self.path in ("/", "/health", "/status"):
            s = calculate_stats()

            body = {
                "status": "running",
                "time_thai": thai_text(),
                "mode": mode_now(),
                "symbols": SYMBOLS,
                "active_series": s["active_series"],
                "memory_records": s["memory_records"],
                "wins": s["wins"],
                "losses": s["losses"],
                "draws": s["draws"],
                "win_rate": s["win_rate"],
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
            return

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

        log(f"Health server :{PORT}")
        server.serve_forever()

    except Exception as e:
        log(f"Health server error: {e}")

# ============================================================
# STARTUP
# ============================================================

def startup_message():
    send_discord(
        f"🚀 **[TRADEIFY STARTED]**\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🇹🇭 เวลาไทย: **{thai_text()}**\n"
        f"🌐 Mode: **{mode_now()}**\n"
        f"⏱️ Master: **15M**\n"
        f"🎯 Entry/Result: **5M**\n"
        f"🔢 Opportunities: **1–3**\n"
        f"💾 Memory: **ON**\n"
        f"📡 Symbols: **{len(SYMBOLS)}**\n"
        f"⏳ Scanner: **{SCAN_SECONDS}s**\n\n"
        f"⚠️ OTC เป็น proxy จาก public FX feed "
        f"ไม่ใช่ราคา OTC จาก 8X โดยตรง"
    )

def main():
    log("=" * 70)
    log("TRADEIFY 15M + 5M + 3 OPPORTUNITIES")
    log("=" * 70)

    load_memory()

    Thread(
        target=run_health_server,
        daemon=True,
    ).start()

    Thread(
        target=tracker_loop,
        daemon=True,
    ).start()

    Thread(
        target=scanner_loop,
        daemon=True,
    ).start()

    Thread(
        target=reporter_loop,
        daemon=True,
    ).start()

    startup_message()

    while True:
        try:
            time.sleep(60)
        except KeyboardInterrupt:
            log("Stopping...")
            break

if __name__ == "__main__":
    main()
'''

path = Path("/mnt/data/tradeify.py")
path.write_text(code, encoding="utf-8")
print(f"สร้างไฟล์เรียบร้อย: {path}")
print(f"จำนวนบรรทัด: {len(code.splitlines())}")
อันนี้นะ
