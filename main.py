# -*- coding: utf-8 -*-
"""
TRADEIFY.py
============================================================
TRADEIFY 24/7 + LIVE + OTC + 15M + 5M + 3 OPPORTUNITIES
THAI TIME + DISCORD + MEMORY

Python 3.10+

ระบบ:
- 15M = Master Trend / Zone / Break / Flip
- 5M  = Timing / Candle Confirmation
- สูงสุด 3 Opportunities ต่อ Series
- Memory ไม่ reset
- LIVE / OTC แยกโหมด
- เวลาไทยทุกข้อความ
- Discord แจ้งละเอียด
- Health server สำหรับ Railway
- Gemini เป็นเพียงคำอธิบาย ไม่ใช่ตัวตัดสินหลัก

สำคัญ:
ข้อมูลจาก Yahoo ไม่ใช่ข้อมูลแท่งของ 8X โดยตรง
ดังนั้น OTC / Weekend อาจไม่ตรงกับกราฟ 8X 100%
"""

import os
import json
import time
import requests
from datetime import datetime, timezone, timedelta
from threading import Thread, Lock
from http.server import HTTPServer, BaseHTTPRequestHandler

import yfinance as yf

# ============================================================
# OPTIONAL GEMINI
# ============================================================

try:
    from google import genai
except Exception:
    genai = None


# ============================================================
# CONFIG
# ============================================================

APP_NAME = "TRADEIFY"

DISCORD_WEBHOOK_URL = os.getenv(
    "DISCORD_WEBHOOK_URL",
    ""
).strip()

GEMINI_API_KEY = os.getenv(
    "GEMINI_API_KEY",
    ""
).strip()

GEMINI_MODEL = os.getenv(
    "GEMINI_MODEL",
    "gemini-2.5-flash"
)

PORT = int(
    os.getenv("PORT", "8080")
)

SCAN_SECONDS = int(
    os.getenv("SCAN_SECONDS", "60")
)

REPORT_SECONDS = int(
    os.getenv("REPORT_SECONDS", "1800")
)

FIVE_MIN_SECONDS = 300

MAX_OPPORTUNITIES = 3

MEMORY_FILE = os.getenv(
    "MEMORY_FILE",
    "tradeify_memory.json"
)

# ------------------------------------------------------------
# SIGNAL THRESHOLDS
# ------------------------------------------------------------

MASTER_MIN_SCORE = 58

STRONG_SCORE = 75

# ------------------------------------------------------------
# MARKET MODES
# ------------------------------------------------------------

LIVE_MODE = "LIVE"
OTC_MODE = "OTC"

# ตั้งค่า default เป็น AUTO
# LIVE วันธรรมดา
# OTC เสาร์/อาทิตย์
MARKET_MODE = os.getenv(
    "MARKET_MODE",
    "AUTO"
).upper()

# ------------------------------------------------------------
# SYMBOLS
# ------------------------------------------------------------

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

MASTER_INTERVAL = "15m"
ENTRY_INTERVAL = "5m"


# ============================================================
# GLOBAL STATE
# ============================================================

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
# TIME / THAI
# ============================================================

THAI_TZ = timezone(
    timedelta(hours=7)
)


def now_utc():
    return datetime.now(timezone.utc)


def now_thai():
    return datetime.now(THAI_TZ)


def thai_text(dt=None):
    if dt is None:
        dt = now_thai()

    return dt.strftime(
        "%Y-%m-%d %H:%M:%S"
    )


def thai_short(dt=None):
    if dt is None:
        dt = now_thai()

    return dt.strftime(
        "%d/%m/%Y %H:%M:%S"
    )


def weekday_thai():
    names = {
        0: "จันทร์",
        1: "อังคาร",
        2: "พุธ",
        3: "พฤหัสบดี",
        4: "ศุกร์",
        5: "เสาร์",
        6: "อาทิตย์",
    }

    return names[now_thai().weekday()]


def is_weekend():
    return now_thai().weekday() >= 5


def automatic_market_mode():
    if MARKET_MODE in (
        LIVE_MODE,
        OTC_MODE,
    ):
        return MARKET_MODE

    if is_weekend():
        return OTC_MODE

    return LIVE_MODE


def market_label():
    mode = automatic_market_mode()

    if mode == OTC_MODE:
        return "🟣 OTC / WEEKEND"

    return "🟢 LIVE / WEEKDAY"


def log(message):
    print(
        f"[{thai_text()}] {message}",
        flush=True
    )


# ============================================================
# DISCORD
# ============================================================

def send_discord(message):

    if not DISCORD_WEBHOOK_URL:
        log(
            "Discord disabled: "
            "DISCORD_WEBHOOK_URL not set"
        )
        return False

    try:

        response = requests.post(
            DISCORD_WEBHOOK_URL,
            json={
                "content": message[:1900]
            },
            timeout=10,
        )

        if response.status_code == 204:
            return True

        log(
            f"Discord HTTP "
            f"{response.status_code}: "
            f"{response.text[:300]}"
        )

        return False

    except Exception as exc:

        log(
            f"Discord error: {exc}"
        )

        return False


# ============================================================
# GEMINI
# ============================================================

AI_CLIENT = None

if genai and GEMINI_API_KEY:

    try:

        AI_CLIENT = genai.Client(
            api_key=GEMINI_API_KEY
        )

        log(
            "Gemini client ready"
        )

    except Exception as exc:

        log(
            f"Gemini init failed: {exc}"
        )

else:

    log(
        "Gemini disabled"
    )


def ai_comment(
    symbol,
    direction,
    score,
    zone_state,
    mode,
):

    if AI_CLIENT is None:
        return "AI: OFF"

    try:

        prompt = (
            f"Forex {symbol}. "
            f"Mode={mode}. "
            f"15M direction={direction}. "
            f"score={score}/100. "
            f"zone={zone_state}. "
            f"Give a very short Thai analysis, "
            f"maximum 2 lines. "
            f"Do not guarantee profit."
        )

        response = AI_CLIENT.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
        )

        text = getattr(
            response,
            "text",
            None
        )

        if text:
            return text.strip()

        return "AI: no response"

    except Exception as exc:

        return (
            "AI unavailable: "
            + str(exc)[:120]
        )


# ============================================================
# MEMORY
# ============================================================

def load_memory():

    global HISTORICAL_MEMORY

    if not os.path.exists(
        MEMORY_FILE
    ):

        HISTORICAL_MEMORY = []

        log(
            f"Memory new: {MEMORY_FILE}"
        )

        return

    try:

        with open(
            MEMORY_FILE,
            "r",
            encoding="utf-8"
        ) as file:

            data = json.load(file)

        if isinstance(data, list):

            HISTORICAL_MEMORY = data

        elif isinstance(data, dict):

            HISTORICAL_MEMORY = data.get(
                "history",
                []
            )

        else:

            HISTORICAL_MEMORY = []

        log(
            "Memory loaded: "
            f"{len(HISTORICAL_MEMORY)} records"
        )

    except Exception as exc:

        log(
            f"Memory load error: {exc}"
        )

        HISTORICAL_MEMORY = []


def save_memory():

    try:

        temp_file = (
            MEMORY_FILE
            + ".tmp"
        )

        with open(
            temp_file,
            "w",
            encoding="utf-8"
        ) as file:

            json.dump(
                HISTORICAL_MEMORY,
                file,
                ensure_ascii=False,
                indent=2,
            )

        os.replace(
            temp_file,
            MEMORY_FILE
        )

    except Exception as exc:

        log(
            f"Memory save error: {exc}"
        )


# ============================================================
# MARKET DATA
# ============================================================

def clean_dataframe(df):

    if df is None or df.empty:
        return None

    try:

        if getattr(
            df.index,
            "tz",
            None
        ) is not None:

            df = df.copy()

            df.index = (
                df.index
                .tz_convert("UTC")
                .tz_localize(None)
            )

        return df.dropna(
            subset=[
                "Open",
                "High",
                "Low",
                "Close",
            ]
        )

    except Exception:

        return df


def get_candles(
    symbol,
    interval,
    period="5d",
    closed_only=True,
):

    ticker_symbol = SYMBOL_MAP.get(
        symbol,
        symbol
    )

    try:

        ticker = yf.Ticker(
            ticker_symbol
        )

        df = ticker.history(
            period=period,
            interval=interval,
            auto_adjust=False,
            prepost=False,
        )

        df = clean_dataframe(df)

        if df is None or len(df) < 10:
            return []

        # ตัดแท่งล่าสุดออก
        # เพื่อใช้เฉพาะแท่งที่ปิดแล้ว
        if (
            closed_only
            and len(df) > 1
        ):

            df = df.iloc[:-1]

        candles = []

        for idx, row in df.iterrows():

            ts = idx.to_pydatetime()

            if ts.tzinfo is None:

                ts = ts.replace(
                    tzinfo=timezone.utc
                )

            ts = ts.astimezone(
                timezone.utc
            )

            candles.append({

                "datetime":
                    ts.strftime(
                        "%Y-%m-%d %H:%M:%S"
                    ),

                "timestamp":
                    ts.timestamp(),

                "open":
                    float(row["Open"]),

                "high":
                    float(row["High"]),

                "low":
                    float(row["Low"]),

                "close":
                    float(row["Close"]),
            })

        return candles

    except Exception as exc:

        log(
            f"Data error "
            f"{symbol} "
            f"{interval}: "
            f"{exc}"
        )

        return []


# ============================================================
# INDICATORS
# ============================================================

def ema(
    values,
    period
):

    if len(values) < period:
        return None

    multiplier = (
        2.0
        / (period + 1.0)
    )

    value = sum(
        values[:period]
    ) / period

    for price in values[period:]:

        value = (
            (price - value)
            * multiplier
            + value
        )

    return value


def rsi_wilder(
    values,
    period=14
):

    if len(values) < period + 1:
        return None

    gains = []
    losses = []

    for i in range(
        1,
        len(values)
    ):

        diff = (
            values[i]
            - values[i - 1]
        )

        gains.append(
            max(diff, 0)
        )

        losses.append(
            max(-diff, 0)
        )

    avg_gain = (
        sum(gains[:period])
        / period
    )

    avg_loss = (
        sum(losses[:period])
        / period
    )

    for i in range(
        period,
        len(gains)
    ):

        avg_gain = (
            (
                avg_gain
                * (period - 1)
            )
            + gains[i]
        ) / period

        avg_loss = (
            (
                avg_loss
                * (period - 1)
            )
            + losses[i]
        ) / period

    if avg_loss == 0:
        return 100.0

    rs = (
        avg_gain
        / avg_loss
    )

    return (
        100.0
        - (
            100.0
            / (1.0 + rs)
        )
    )


def atr(
    candles,
    period=14
):

    if len(candles) < period + 1:
        return None

    trs = []

    for i in range(
        1,
        len(candles)
    ):

        current = candles[i]
        previous = candles[i - 1]

        tr = max(
            current["high"]
            - current["low"],

            abs(
                current["high"]
                - previous["close"]
            ),

            abs(
                current["low"]
                - previous["close"]
            ),
        )

        trs.append(tr)

    return (
        sum(trs[-period:])
        / period
    )


# ============================================================
# CANDLE PATTERN
# ============================================================

def candle_features(
    c0,
    c1
):

    body = abs(
        c0["close"]
        - c0["open"]
    )

    full_range = max(
        c0["high"]
        - c0["low"],
        1e-12
    )

    upper = (
        c0["high"]
        - max(
            c0["open"],
            c0["close"]
        )
    )

    lower = (
        min(
            c0["open"],
            c0["close"]
        )
        - c0["low"]
    )

    body_ratio = (
        body
        / full_range
    )

    strong_bull = (
        c0["close"]
        > c0["open"]
        and body_ratio >= 0.65
    )

    strong_bear = (
        c0["close"]
        < c0["open"]
        and body_ratio >= 0.65
    )

    hammer = (
        lower >= body * 2
        and upper
        <= full_range * 0.25
        and body_ratio <= 0.45
    )

    shooting_star = (
        upper >= body * 2
        and lower
        <= full_range * 0.25
        and body_ratio <= 0.45
    )

    bull_engulf = (
        c0["close"]
        > c0["open"]
        and c1["close"]
        < c1["open"]
        and c0["open"]
        <= c1["close"]
        and c0["close"]
        >= c1["open"]
        and body
        > abs(
            c1["close"]
            - c1["open"]
        )
    )

    bear_engulf = (
        c0["close"]
        < c0["open"]
        and c1["close"]
        > c1["open"]
        and c0["open"]
        >= c1["close"]
        and c0["close"]
        <= c1["open"]
        and body
        > abs(
            c1["close"]
            - c1["open"]
        )
    )

    return {

        "strong_bull":
            strong_bull,

        "strong_bear":
            strong_bear,

        "hammer":
            hammer,

        "shooting_star":
            shooting_star,

        "bull_engulf":
            bull_engulf,

        "bear_engulf":
            bear_engulf,

        "body_ratio":
            body_ratio,
    }


# ============================================================
# ZONES
# ============================================================

def build_zones(
    candles,
    lookback=240
):

    if len(candles) < 30:
        return []

    data = candles[
        -lookback:
    ]

    zones = []

    left_right = 2

    for i in range(
        left_right,
        len(data) - left_right
    ):

        high = data[i]["high"]
        low = data[i]["low"]

        swing_high = all(
            high >= data[j]["high"]
            for j in range(
                i - left_right,
                i + left_right + 1
            )
            if j != i
        )

        swing_low = all(
            low <= data[j]["low"]
            for j in range(
                i - left_right,
                i + left_right + 1
            )
            if j != i
        )

        if swing_high:

            zones.append({
                "type":
                    "RESISTANCE",

                "price":
                    high,

                "timestamp":
                    data[i]["timestamp"],
            })

        if swing_low:

            zones.append({
                "type":
                    "SUPPORT",

                "price":
                    low,

                "timestamp":
                    data[i]["timestamp"],
            })

    return zones


def zone_analysis(
    candles,
    price,
    direction
):

    if len(candles) < 40:

        return {
            "state": "NONE",
            "score": 0,
            "distance": None,
            "level": None,
        }

    current_atr = atr(
        candles,
        14
    )

    if not current_atr:

        return {
            "state": "NONE",
            "score": 0,
            "distance": None,
            "level": None,
        }

    zones = build_zones(
        candles
    )

    tolerance = (
        current_atr
        * 0.35
    )

    candidates = []

    for zone in zones:

        distance = abs(
            price
            - zone["price"]
        )

        if distance <= tolerance:

            candidates.append(
                (
                    distance,
                    zone
                )
            )

    if not candidates:

        return {
            "state": "NONE",
            "score": 0,
            "distance": None,
            "level": None,
        }

    candidates.sort(
        key=lambda x: x[0]
    )

    distance, nearest = (
        candidates[0]
    )

    recent = candles[-12:]

    broke_above = any(
        c["close"]
        > nearest["price"]
        + tolerance * 0.15
        for c in recent[:-2]
    )

    broke_below = any(
        c["close"]
        < nearest["price"]
        - tolerance * 0.15
        for c in recent[:-2]
    )

    state = nearest["type"]

    score = 10

    if nearest["type"] == "RESISTANCE":

        if direction == "PUT":
            score += 15

        if (
            broke_above
            and price < nearest["price"]
        ):

            state = (
                "FLIPPED_RESISTANCE"
            )

            score += 20

    elif nearest["type"] == "SUPPORT":

        if direction == "CALL":
            score += 15

        if (
            broke_below
            and price > nearest["price"]
        ):

            state = (
                "FLIPPED_SUPPORT"
            )

            score += 20

    if (
        distance
        <= tolerance * 0.50
    ):

        score += 10

    return {

        "state": state,

        "score":
            min(score, 45),

        "distance":
            distance,

        "level":
            nearest["price"],
    }


# ============================================================
# 15M MASTER
# ============================================================

def analyze_15m(
    symbol,
    candles
):

    if len(candles) < 80:

        return {
            "decision":
                "WAIT",

            "score":
                0,

            "reason":
                "not enough 15M candles",
        }

    c0 = candles[-1]
    c1 = candles[-2]

    closes = [
        c["close"]
        for c in candles
    ]

    price = c0["close"]

    ema20 = ema(
        closes,
        20
    )

    ema50 = ema(
        closes,
        50
    )

    current_atr = atr(
        candles,
        14
    )

    rsi = rsi_wilder(
        closes,
        14
    )

    if any(
        x is None
        for x in (
            ema20,
            ema50,
            current_atr,
            rsi
        )
    ):

        return {
            "decision":
                "WAIT",

            "score":
                0,

            "reason":
                "indicator unavailable",
        }

    pattern = candle_features(
        c0,
        c1
    )

    call = 0
    put = 0

    reasons_call = []
    reasons_put = []

    if price > ema20:

        call += 15

        reasons_call.append(
            "price>EMA20"
        )

    if price > ema50:

        call += 20

        reasons_call.append(
            "price>EMA50"
        )

    if price < ema20:

        put += 15

        reasons_put.append(
            "price<EMA20"
        )

    if price < ema50:

        put += 20

        reasons_put.append(
            "price<EMA50"
        )

    if ema20 > ema50:

        call += 10

        reasons_call.append(
            "EMA20>EMA50"
        )

    if ema20 < ema50:

        put += 10

        reasons_put.append(
            "EMA20<EMA50"
        )

    if 50 <= rsi <= 68:

        call += 8

        reasons_call.append(
            f"RSI {rsi:.1f}"
        )

    if 32 <= rsi <= 50:

        put += 8

        reasons_put.append(
            f"RSI {rsi:.1f}"
        )

    if (
        pattern["strong_bull"]
        or pattern["hammer"]
        or pattern["bull_engulf"]
    ):

        call += 12

        reasons_call.append(
            "bullish candle"
        )

    if (
        pattern["strong_bear"]
        or pattern["shooting_star"]
        or pattern["bear_engulf"]
    ):

        put += 12

        reasons_put.append(
            "bearish candle"
        )

    z_call = zone_analysis(
        candles,
        price,
        "CALL"
    )

    z_put = zone_analysis(
        candles,
        price,
        "PUT"
    )

    call += z_call["score"]
    put += z_put["score"]

    if z_call["score"] > 10:

        reasons_call.append(
            "ZONE "
            + z_call["state"]
        )

    if z_put["score"] > 10:

        reasons_put.append(
            "ZONE "
            + z_put["state"]
        )

    if (
        call > put
        and call >= MASTER_MIN_SCORE
    ):

        decision = "CALL"

        score = min(
            99,
            call
        )

        reasons = reasons_call

        zone = z_call

    elif (
        put > call
        and put >= MASTER_MIN_SCORE
    ):

        decision = "PUT"

        score = min(
            99,
            put
        )

        reasons = reasons_put

        zone = z_put

    else:

        return {
            "decision":
                "WATCH",

            "score":
                max(
                    call,
                    put
                ),

            "price":
                price,

            "atr":
                current_atr,

            "rsi":
                rsi,

            "ema20":
                ema20,

            "ema50":
                ema50,

            "zone_state":
                "MIXED",

            "zone_level":
                None,

            "reasons":
                "15M conflict",

            "candle_time":
                c0["datetime"],
        }

    return {

        "decision":
            decision,

        "score":
            score,

        "price":
            price,

        "atr":
            current_atr,

        "rsi":
            rsi,

        "ema20":
            ema20,

        "ema50":
            ema50,

        "zone_state":
            zone["state"],

        "zone_level":
            zone["level"],

        "reasons":
            " | ".join(
                reasons
            ),

        "candle_time":
            c0["datetime"],
    }


# ============================================================
# 5M TIMING
# ============================================================

def analyze_5m(
    symbol,
    candles,
    master_direction
):

    if len(candles) < 70:

        return {
            "decision":
                "UNKNOWN",

            "score":
                50,

            "reason":
                "not enough 5M",
        }

    c0 = candles[-1]
    c1 = candles[-2]

    closes = [
        c["close"]
        for c in candles
    ]

    ema20 = ema(
        closes,
        20
    )

    ema50 = ema(
        closes,
        50
    )

    rsi = rsi_wilder(
        closes,
        14
    )

    pattern = candle_features(
        c0,
        c1
    )

    call = 0
    put = 0

    reasons_call = []
    reasons_put = []

    if (
        ema20 is not None
        and c0["close"] > ema20
    ):

        call += 15

        reasons_call.append(
            "5M>EMA20"
        )

    if (
        ema20 is not None
        and c0["close"] < ema20
    ):

        put += 15

        reasons_put.append(
            "5M<EMA20"
        )

    if (
        ema50 is not None
        and c0["close"] > ema50
    ):

        call += 10

        reasons_call.append(
            "5M>EMA50"
        )

    if (
        ema50 is not None
        and c0["close"] < ema50
    ):

        put += 10

        reasons_put.append(
            "5M<EMA50"
        )

    if rsi is not None:

        if rsi > 50:

            call += 8

            reasons_call.append(
                f"RSI {rsi:.1f}"
            )

        if rsi < 50:

            put += 8

            reasons_put.append(
                f"RSI {rsi:.1f}"
            )

    if (
        pattern["strong_bull"]
        or pattern["bull_engulf"]
        or pattern["hammer"]
    ):

        call += 15

        reasons_call.append(
            "bullish candle"
        )

    if (
        pattern["strong_bear"]
        or pattern["bear_engulf"]
        or pattern["shooting_star"]
    ):

        put += 15

        reasons_put.append(
            "bearish candle"
        )

    if master_direction == "CALL":

        call += 12

        reasons_call.append(
            "15M master CALL"
        )

    elif master_direction == "PUT":

        put += 12

        reasons_put.append(
            "15M master PUT"
        )

    if call > put:

        direction = "CALL"

        score = min(
            99,
            call
        )

        reasons = reasons_call

    elif put > call:

        direction = "PUT"

        score = min(
            99,
            put
        )

        reasons = reasons_put

    else:

        direction = "UNKNOWN"

        score = 50

        reasons = [
            "5M balanced"
        ]

    return {

        "decision":
            direction,

        "score":
            score,

        "rsi":
            rsi,

        "candle_time":
            c0["datetime"],

        "reasons":
            " | ".join(
                reasons
            ),
    }


# ============================================================
# SIGNAL
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

    master = analyze_15m(
        symbol,
        candles15
    )

    if master["decision"] not in (
        "CALL",
        "PUT"
    ):

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
        master["decision"]
    )

    if (
        timing["decision"]
        == master["decision"]
    ):

        final_score = min(
            99,
            master["score"]
            * 0.70
            + timing["score"]
            * 0.30
            + 5
        )

        context = (
            "5M_CONFIRM"
        )

    elif (
        timing["decision"]
        == "UNKNOWN"
    ):

        final_score = (
            master["score"]
            * 0.75
        )

        context = (
            "5M_UNKNOWN"
        )

    else:

        final_score = (
            master["score"]
            * 0.65
        )

        context = (
            "5M_PULLBACK"
        )

    final_score = round(
        final_score,
        1
    )

    if (
        master["score"]
        < MASTER_MIN_SCORE
    ):

        return None

    return {

        "symbol":
            symbol,

        "mode":
            automatic_market_mode(),

        "decision":
            master["decision"],

        "score":
            final_score,

        "master_score":
            master["score"],

        "entry_score":
            timing["score"],

        "entry_context":
            context,

        "price":
            master["price"],

        "atr":
            master["atr"],

        "zone_state":
            master["zone_state"],

        "zone_level":
            master.get(
                "zone_level"
            ),

        "reasons_15m":
            master["reasons"],

        "reasons_5m":
            timing["reasons"],

        "signal_candle_15m":
            master[
                "candle_time"
            ],

        "last_5m_candle":
            timing[
                "candle_time"
            ],

        "created_at":
            thai_text(),

        "created_ts":
            time.time(),
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


def has_active_series(
    symbol
):

    with LOCK:

        return any(
            x["symbol"]
            == symbol

            for x in ACTIVE_SERIES
        )


def create_series(signal):

    series_id = (
        f"{signal['symbol'].replace('/', '')}_"
        f"{int(signal['created_ts'])}"
    )

    tracker = {

        "series_id":
            series_id,

        "symbol":
            signal["symbol"],

        "mode":
            signal["mode"],

        "master_direction":
            signal["decision"],

        "master_score":
            signal["master_score"],

        "signal_score":
            signal["score"],

        "entry_context":
            signal["entry_context"],

        "zone_state":
            signal["zone_state"],

        "zone_level":
            signal.get(
                "zone_level"
            ),

        "signal_candle_15m":
            signal[
                "signal_candle_15m"
            ],

        "signal_time":
            signal["created_at"],

        "signal_ts":
            signal["created_ts"],

        "opportunity":
            1,

        "completed":
            False,

        "wins":
            0,

        "losses":
            0,

        "draws":
            0,

        "max_mfe":
            0.0,

        "max_mae":
            0.0,

        "processed_5m":
            [],

        "entry_price":
            signal["price"],
    }

    with LOCK:

        ACTIVE_SERIES.append(
            tracker
        )

        STATS["signals"] += 1

    return tracker


# ============================================================
# RESULT ENGINE
# ============================================================

def find_first_closed_5m_after(
    candles,
    timestamp
):

    for candle in candles:

        start = candle[
            "timestamp"
        ]

        end = (
            start
            + FIVE_MIN_SECONDS
        )

        if (
            start
            <= timestamp
            < end
        ):

            return candle

    return None


def find_closed_candles_after(
    candles,
    timestamp
):

    result = []

    for candle in candles:

        close_time = (
            candle["timestamp"]
            + FIVE_MIN_SECONDS
        )

        if close_time > timestamp:

            result.append(
                candle
            )

    return result


def evaluate_one_opportunity(
    tracker
):

    symbol = tracker[
        "symbol"
    ]

    candles = get_candles(
        symbol,
        ENTRY_INTERVAL,
        period="2d",
        closed_only=True,
    )

    if len(candles) < 20:
        return None

    signal_ts = tracker[
        "signal_ts"
    ]

    step = tracker[
        "opportunity"
    ]

    first_candle = (
        find_first_closed_5m_after(
            candles,
            signal_ts
        )
    )

    if (
        step == 1
        and first_candle is not None
    ):

        candidate = first_candle

    else:

        candidates = (
            find_closed_candles_after(
                candles,
                signal_ts
            )
        )

        used = set(
            tracker[
                "processed_5m"
            ]
        )

        candidates = [
            candle
            for candle in candidates
            if candle[
                "datetime"
            ] not in used
        ]

        if not candidates:
            return None

        candidate = candidates[0]

    if (
        candidate["datetime"]
        in tracker["processed_5m"]
    ):

        return None

    entry_price = (
        tracker.get(
            "entry_price"
        )
        if step == 1
        else candidate["open"]
    )

    if entry_price is None:
        entry_price = candidate[
            "open"
        ]

    direction = tracker[
        "master_direction"
    ]

    close_price = candidate[
        "close"
    ]

    if close_price > entry_price:

        raw_result = (
            "WIN"
            if direction == "CALL"
            else "LOSS"
        )

    elif close_price < entry_price:

        raw_result = (
            "WIN"
            if direction == "PUT"
            else "LOSS"
        )

    else:

        raw_result = "DRAW"

    if direction == "CALL":

        mfe = (
            candidate["high"]
            - entry_price
        )

        mae = (
            entry_price
            - candidate["low"]
        )

    else:

        mfe = (
            entry_price
            - candidate["low"]
        )

        mae = (
            candidate["high"]
            - entry_price
        )

    return {

        "result":
            raw_result,

        "step":
            step,

        "candle":
            candidate,

        "entry_price":
            entry_price,

        "close_price":
            close_price,

        "mfe":
            max(0, mfe),

        "mae":
            max(0, mae),
    }


def record_opportunity(
    tracker,
    outcome
):

    tracker[
        "processed_5m"
    ].append(
        outcome[
            "candle"
        ]["datetime"]
    )

    tracker["max_mfe"] = max(
        tracker.get(
            "max_mfe",
            0
        ),
        outcome["mfe"]
    )

    tracker["max_mae"] = max(
        tracker.get(
            "max_mae",
            0
        ),
        outcome["mae"]
    )

    result = outcome[
        "result"
    ]

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


# ============================================================
# FINALIZE SERIES
# ============================================================

def finalize_series(
    tracker,
    final_status
):

    record = {

        "type":
            "SERIES",

        "series_id":
            tracker[
                "series_id"
            ],

        "symbol":
            tracker[
                "symbol"
            ],

        "mode":
            tracker[
                "mode"
            ],

        "decision":
            tracker[
                "master_direction"
            ],

        "master_score":
            tracker[
                "master_score"
            ],

        "signal_score":
            tracker[
                "signal_score"
            ],

        "entry_context":
            tracker[
                "entry_context"
            ],

        "zone_state":
            tracker[
                "zone_state"
            ],

        "zone_level":
            tracker[
                "zone_level"
            ],

        "signal_candle_15m":
            tracker[
                "signal_candle_15m"
            ],

        "signal_time":
            tracker[
                "signal_time"
            ],

        "status":
            final_status,

        "wins":
            tracker[
                "wins"
            ],

        "losses":
            tracker[
                "losses"
            ],

        "draws":
            tracker[
                "draws"
            ],

        "opportunities_used":
            tracker[
                "opportunity"
            ],

        "max_mfe":
            tracker[
                "max_mfe"
            ],

        "max_mae":
            tracker[
                "max_mae"
            ],

        "recorded_at":
            thai_text(),
    }

    with LOCK:

        HISTORICAL_MEMORY.append(
            record
        )

        STATS[
            "series_completed"
        ] += 1

        if final_status == "SERIES_WIN":

            STATS[
                "series_wins"
            ] += 1

        if final_status == "FULL_LOSS":

            STATS[
                "series_full_loss"
            ] += 1

    save_memory()

    if final_status == "SERIES_WIN":

        icon = "🟢"

    else:

        icon = "🔴"

    send_discord(

        f"{icon} **[TRADEIFY SERIES COMPLETE]**\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💱 คู่: **{tracker['symbol']}**\n"
        f"🌐 Mode: **{tracker['mode']}**\n"
        f"📌 Direction: **{tracker['master_direction']}**\n"
        f"🏁 Status: **{final_status}**\n"
        f"🎯 WIN: **{tracker['wins']}**\n"
        f"❌ LOSS: **{tracker['losses']}**\n"
        f"➖ DRAW: **{tracker['draws']}**\n"
        f"🔢 Opportunities: "
        f"**{tracker['opportunity']}/{MAX_OPPORTUNITIES}**\n"
        f"📊 15M Score: "
        f"**{tracker['master_score']}/100**\n"
        f"🧭 Zone: "
        f"**{tracker['zone_state']}**\n"
        f"🕐 เวลาไทย: "
        f"**{thai_text()}**"
    )


# ============================================================
# TRACKER LOOP
# ============================================================

def tracker_loop():

    while True:

        try:

            with LOCK:

                trackers = list(
                    ACTIVE_SERIES
                )

            for tracker in trackers:

                outcome = (
                    evaluate_one_opportunity(
                        tracker
                    )
                )

                if outcome is None:
                    continue

                result = (
                    record_opportunity(
                        tracker,
                        outcome
                    )
                )

                candle = outcome[
                    "candle"
                ]

                log(
                    f"TRACKER "
                    f"{tracker['symbol']} "
                    f"OPP{tracker['opportunity']} "
                    f"{result}"
                )

                icon = (
                    "🟢"
                    if result == "WIN"
                    else
                    "🔴"
                    if result == "LOSS"
                    else
                    "🟡"
                )

                send_discord(

                    f"{icon} **[TRADEIFY 5M RESULT]**\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"💱 **{tracker['symbol']}**\n"
                    f"🌐 Mode: **{tracker['mode']}**\n"
                    f"📌 Master: **{tracker['master_direction']}**\n"
                    f"🎯 OPP: **{tracker['opportunity']}/{MAX_OPPORTUNITIES}**\n"
                    f"🏁 Result: **{result}**\n"
                    f"💰 Entry: **{outcome['entry_price']:.6f}**\n"
                    f"🔚 Close: **{outcome['close_price']:.6f}**\n"
                    f"🕐 Candle: **{candle['datetime']} UTC**\n"
                    f"🇹🇭 เวลาแจ้ง: **{thai_text()}**\n"
                    f"📈 MFE: **{outcome['mfe']:.6f}**\n"
                    f"📉 MAE: **{outcome['mae']:.6f}**"
                )

                if result == "WIN":

                    finalize_series(
                        tracker,
                        "SERIES_WIN"
                    )

                    with LOCK:

                        if (
                            tracker
                            in ACTIVE_SERIES
                        ):

                            ACTIVE_SERIES.remove(
                                tracker
                            )

                elif (
                    tracker[
                        "opportunity"
                    ]
                    >= MAX_OPPORTUNITIES
                ):

                    finalize_series(
                        tracker,
                        "FULL_LOSS"
                    )

                    with LOCK:

                        if (
                            tracker
                            in ACTIVE_SERIES
                        ):

                            ACTIVE_SERIES.remove(
                                tracker
                            )

                else:

                    tracker[
                        "opportunity"
                    ] += 1

                    log(
                        f"{tracker['symbol']} "
                        f"-> OPP"
                        f"{tracker['opportunity']}"
                    )

        except Exception as exc:

            log(
                f"Tracker error: {exc}"
            )

        time.sleep(20)


# ============================================================
# SCANNER LOOP
# ============================================================

def scanner_loop():

    while True:

        try:

            best = []

            for symbol in SYMBOLS:

                try:

                    signal = scan_symbol(
                        symbol
                    )

                    if signal is not None:

                        best.append(
                            signal
                        )

                except Exception as exc:

                    log(
                        f"Scanner "
                        f"{symbol}: "
                        f"{exc}"
                    )

            best.sort(
                key=lambda x:
                x["score"],
                reverse=True
            )

            for signal in best:

                key = series_key(
                    signal
                )

                if key in SENT_SIGNALS:
                    continue

                if has_active_series(
                    signal["symbol"]
                ):
                    continue

                SENT_SIGNALS.add(
                    key
                )

                tracker = create_series(
                    signal
                )

                mode = signal[
                    "mode"
                ]

                if (
                    signal["score"]
                    >= STRONG_SCORE
                ):

                    strength = (
                        "🔥 STRONG"
                    )

                elif (
                    signal["score"]
                    >= MASTER_MIN_SCORE
                ):

                    strength = (
                        "🟡 VALID SETUP"
                    )

                else:

                    strength = (
                        "⚪ WATCH"
                    )

                ai_text = ai_comment(
                    signal["symbol"],
                    signal["decision"],
                    signal["score"],
                    signal["zone_state"],
                    mode,
                )

                zone_text = (
                    signal[
                        "zone_state"
                    ]
                )

                if (
                    signal.get(
                        "zone_level"
                    )
                    is not None
                ):

                    zone_text += (
                        " @ "
                        f"{signal['zone_level']:.6f}"
                    )

                icon = (
                    "🟢"
                    if signal[
                        "decision"
                    ] == "CALL"
                    else
                    "🔴"
                )

                message = (

                    f"{icon} **[TRADEIFY NEW SIGNAL]**\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"

                    f"💱 คู่เงิน: "
                    f"**{signal['symbol']}**\n"

                    f"🌐 ตลาด: "
                    f"**{mode}**\n"

                    f"📅 วัน: "
                    f"**{weekday_thai()}**\n"

                    f"🟢🔴 สถานะ: "
                    f"**{strength}**\n"

                    f"📌 15M Direction: "
                    f"**{signal['decision']}**\n"

                    f"🏆 Final Score: "
                    f"**{signal['score']}/100**\n"

                    f"📊 15M Score: "
                    f"**{signal['master_score']}/100**\n"

                    f"⏱️ 5M Score: "
                    f"**{signal['entry_score']}/100**\n"

                    f"🧭 5M Context: "
                    f"**{signal['entry_context']}**\n"

                    f"🧱 Zone: "
                    f"**{zone_text}**\n"

                    f"💰 Price: "
                    f"**{signal['price']:.6f}**\n\n"

                    f"🔎 15M:\n"
                    f"{signal['reasons_15m']}\n\n"

                    f"🔎 5M:\n"
                    f"{signal['reasons_5m']}\n\n"

                    f"🤖 AI:\n"
                    f"{ai_text}\n\n"

                    f"🎯 Series: "
                    f"**สูงสุด {MAX_OPPORTUNITIES} ไม้**\n"

                    f"🕐 เวลาไทย:\n"
                    f"**{signal['created_at']}**\n"

                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"⚠️ Signal "
                    f"ไม่ใช่การรับประกันผลลัพธ์"
                )

                send_discord(
                    message
                )

                log(
                    f"NEW "
                    f"{signal['symbol']} "
                    f"{signal['decision']} "
                    f"score="
                    f"{signal['score']} "
                    f"mode={mode}"
                )

                # 1 setup ต่อ scan cycle
                break

        except Exception as exc:

            log(
                f"Scanner loop error: "
                f"{exc}"
            )

        time.sleep(
            SCAN_SECONDS
        )


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

            win_rate = (
                STATS["wins"]
                / total
                * 100
            )

        else:

            win_rate = 0

        return {

            **STATS,

            "total_opportunities":
                total,

            "win_rate":
                round(
                    win_rate,
                    2
                ),

            "active_series":
                len(
                    ACTIVE_SERIES
                ),

            "memory_records":
                len(
                    HISTORICAL_MEMORY
                ),
        }


def reporter_loop():

    while True:

        try:

            stats = calculate_stats()

            send_discord(

                f"📊 **[TRADEIFY STATUS]**\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"🌐 Mode: **{automatic_market_mode()}**\n"
                f"📅 {weekday_thai()}\n"
                f"🕐 เวลาไทย: **{thai_text()}**\n\n"

                f"📡 Signals: "
                f"**{stats['signals']}**\n"

                f"🟢 WIN: "
                f"**{stats['wins']}**\n"

                f"🔴 LOSS: "
                f"**{stats['losses']}**\n"

                f"🟡 DRAW: "
                f"**{stats['draws']}**\n"

                f"📈 Win Rate: "
                f"**{stats['win_rate']}%**\n\n"

                f"🏁 Series Completed: "
                f"**{stats['series_completed']}**\n"

                f"🟢 Series WIN: "
                f"**{stats['series_wins']}**\n"

                f"🔴 Full Loss: "
                f"**{stats['series_full_loss']}**\n"

                f"🔥 Active Series: "
                f"**{stats['active_series']}**\n"

                f"🧠 Memory: "
                f"**{stats['memory_records']}**"
            )

        except Exception as exc:

            log(
                f"Reporter error: "
                f"{exc}"
            )

        time.sleep(
            REPORT_SECONDS
        )


# ============================================================
# HEALTH SERVER
# ============================================================

class HealthHandler(
    BaseHTTPRequestHandler
):

    def do_GET(self):

        if self.path in (
            "/",
            "/health",
            "/status"
        ):

            stats = (
                calculate_stats()
            )

            body = {

                "app":
                    APP_NAME,

                "status":
                    "running",

                "market_mode":
                    automatic_market_mode(),

                "market_label":
                    market_label(),

                "weekday":
                    weekday_thai(),

                "time_thai":
                    thai_text(),

                "symbols":
                    SYMBOLS,

                "active_series":
                    stats[
                        "active_series"
                    ],

                "memory_records":
                    stats[
                        "memory_records"
                    ],

                "signals":
                    stats[
                        "signals"
                    ],

                "wins":
                    stats[
                        "wins"
                    ],

                "losses":
                    stats[
                        "losses"
                    ],

                "draws":
                    stats[
                        "draws"
                    ],

                "win_rate":
                    stats[
                        "win_rate"
                    ],
            }

            raw = json.dumps(
                body,
                ensure_ascii=False,
                indent=2
            ).encode(
                "utf-8"
            )

            self.send_response(
                200
            )

            self.send_header(
                "Content-Type",
                "application/json; "
                "charset=utf-8"
            )

            self.send_header(
                "Content-Length",
                str(len(raw))
            )

            self.end_headers()

            self.wfile.write(
                raw
            )

        else:

            self.send_response(
                404
            )

            self.end_headers()

    def log_message(
        self,
        format,
        *args
    ):

        return


def run_health_server():

    try:

        server = HTTPServer(
            (
                "0.0.0.0",
                PORT
            ),
            HealthHandler
        )

        log(
            f"Health server "
            f"0.0.0.0:{PORT}"
        )

        server.serve_forever()

    except Exception as exc:

        log(
            f"Health server error: "
            f"{exc}"
        )


# ============================================================
# STARTUP DISCORD
# ============================================================

def startup_message():

    mode = (
        automatic_market_mode()
    )

    send_discord(

        f"🚀 **[TRADEIFY STARTED]**\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"

        f"🌐 Market Mode: "
        f"**{mode}**\n"

        f"📅 วัน: "
        f"**{weekday_thai()}**\n"

        f"🕐 เวลาไทย: "
        f"**{thai_text()}**\n\n"

        f"🧭 15M = Trend + Zone + Break/Flip\n"
        f"⏱️ 5M = Timing + Candle\n"
        f"🎯 Series = สูงสุด "
        f"**{MAX_OPPORTUNITIES} opportunities**\n"
        f"🧠 Memory = Persistent\n"
        f"📡 Symbols = "
        f"**{len(SYMBOLS)}**\n"
        f"⏳ Scanner = "
        f"**{SCAN_SECONDS}s**\n"
        f"🌐 Port = "
        f"**{PORT}**\n\n"

        f"⚠️ LIVE/OTC label "
        f"ไม่ได้ทำให้ข้อมูล Yahoo "
        f"กลายเป็นราคา 8X โดยอัตโนมัติ\n"
        f"⚠️ OTC ต้องใช้ feed ของ OTC "
        f"ที่ตรงกับแพลตฟอร์ม หากต้องการ "
        f"ให้แท่งตรงกับ 8X"
    )


# ============================================================
# MAIN
# ============================================================

def main():

    log("=" * 65)

    log(
        "TRADEIFY 24/7 "
        "LIVE + OTC STARTING"
    )

    log("=" * 65)

    load_memory()

    Thread(
        target=run_health_server,
        daemon=True
    ).start()

    Thread(
        target=tracker_loop,
        daemon=True
    ).start()

    Thread(
        target=scanner_loop,
        daemon=True
    ).start()

    Thread(
        target=reporter_loop,
        daemon=True
    ).start()

    startup_message()

    while True:

        try:

            time.sleep(
                60
            )

        except KeyboardInterrupt:

            log(
                "Stopping..."
            )

            break

        except Exception as exc:

            log(
                f"Main error: "
                f"{exc}"
            )


if __name__ == "__main__":

    main()
