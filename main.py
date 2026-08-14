# -*- coding: utf-8 -*-
"""
TRADEIFY
24/7 + LIVE + OTC + 15M + 5M + 3 OPPORTUNITIES + DISCORD + THAI TIME + MEMORY

Python 3.10+

============================================================
ระบบ
============================================================

15M
- Master Trend
- EMA20 / EMA50
- RSI
- ATR
- Historical Support / Resistance
- Break / Flip
- Candle Pattern

5M
- Entry timing
- Closed candle result
- ใช้แท่ง 5M ที่ปิดแล้วในการตัดผล

Series
- 1 Signal = 1 Series
- สูงสุด 3 Opportunities
- WIN -> จบ Series
- LOSS -> ไป OPP ถัดไป
- ครบ 3 LOSS -> FULL_LOSS
- DRAW -> ไม่ถือเป็น WIN และไม่ย้อนกลับ
- Pair เดิมกลับมาได้เมื่อเกิด setup 15M ใหม่

MARKET MODE
- AUTO
- LIVE
- OTC

LIVE
- ใช้ Yahoo Finance เป็น data source สำหรับตลาดที่มีข้อมูล

OTC
- ต้องมี OTC_DATA_URL ที่ให้ข้อมูลแท่ง OTC จริง
- ไม่ใช้ Yahoo เป็นตัวแทน OTC
- ถ้า OTC feed ใช้งานไม่ได้ -> แจ้ง OFFLINE

เวลา
- Asia/Bangkok
- UTC+7

Discord
- Startup
- Signal
- OPP1 / OPP2 / OPP3
- Series Complete
- Status
- Data Offline
- Error

Memory
- tradeify_memory.json
- ไม่ reset เมื่อ restart
- ใช้ atomic save

Health
- /
- /health
- /status

============================================================
OTC DATA FORMAT
============================================================

OTC_DATA_URL ต้องคืน JSON รูปแบบประมาณนี้:

{
  "EUR/USD": {
    "5m": [
      {
        "datetime": "2026-08-15T12:00:00Z",
        "open": 1.1700,
        "high": 1.1710,
        "low": 1.1695,
        "close": 1.1707
      }
    ],
    "15m": [
      {
        "datetime": "2026-08-15T12:00:00Z",
        "open": 1.1690,
        "high": 1.1720,
        "low": 1.1685,
        "close": 1.1707
      }
    ]
  }
}

สามารถส่ง timestamp เป็น Unix timestamp ได้เช่นกัน

============================================================
ENV
============================================================

DISCORD_WEBHOOK_URL=...
GEMINI_API_KEY=...

MARKET_MODE=AUTO
OTC_DATA_URL=https://your-otc-feed.example/data

SCAN_SECONDS=60
REPORT_SECONDS=1800

MEMORY_FILE=tradeify_memory.json

PORT=8080

MIN_MASTER_SCORE=62
MAX_OPPORTUNITIES=3

============================================================
"""

import os
import json
import time
import math
import threading
from datetime import datetime, timezone, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse

import requests
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
    os.getenv(
        "PORT",
        "8080"
    )
)

SCAN_SECONDS = max(
    30,
    int(
        os.getenv(
            "SCAN_SECONDS",
            "60"
        )
    )
)

REPORT_SECONDS = max(
    300,
    int(
        os.getenv(
            "REPORT_SECONDS",
            "1800"
        )
    )
)

MEMORY_FILE = os.getenv(
    "MEMORY_FILE",
    "tradeify_memory.json"
)

MARKET_MODE = os.getenv(
    "MARKET_MODE",
    "AUTO"
).upper().strip()

OTC_DATA_URL = os.getenv(
    "OTC_DATA_URL",
    ""
).strip()

MIN_MASTER_SCORE = float(
    os.getenv(
        "MIN_MASTER_SCORE",
        "62"
    )
)

MAX_OPPORTUNITIES = int(
    os.getenv(
        "MAX_OPPORTUNITIES",
        "3"
    )
)

FIVE_MIN_SECONDS = 300

TIMEZONE_NAME = "Asia/Bangkok"


# ============================================================
# TIMEZONE
# ============================================================

try:
    from zoneinfo import ZoneInfo

    THAI_TZ = ZoneInfo(
        TIMEZONE_NAME
    )

except Exception:
    THAI_TZ = timezone(
        timedelta(hours=7)
    )


def now_utc():
    return datetime.now(
        timezone.utc
    )


def now_thai():
    return now_utc().astimezone(
        THAI_TZ
    )


def thai_text(dt=None):
    if dt is None:
        dt = now_thai()

    if dt.tzinfo is None:
        dt = dt.replace(
            tzinfo=timezone.utc
        )

    dt = dt.astimezone(
        THAI_TZ
    )

    return dt.strftime(
        "%Y-%m-%d %H:%M:%S"
    )


def thai_short(dt=None):
    if dt is None:
        dt = now_thai()

    if dt.tzinfo is None:
        dt = dt.replace(
            tzinfo=timezone.utc
        )

    return dt.astimezone(
        THAI_TZ
    ).strftime(
        "%d/%m/%Y %H:%M"
    )


def log(message):
    print(
        f"[{thai_text()} TH] {message}",
        flush=True
    )


# ============================================================
# MARKET SYMBOLS
# ============================================================

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

SYMBOLS = list(
    SYMBOL_MAP.keys()
)


# ============================================================
# GLOBAL STATE
# ============================================================

LOCK = threading.RLock()

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
    "data_errors": 0,
}


# ============================================================
# DISCORD
# ============================================================

def discord_enabled():
    return bool(
        DISCORD_WEBHOOK_URL
    )


def send_discord(message):
    if not discord_enabled():
        return False

    try:

        response = requests.post(
            DISCORD_WEBHOOK_URL,
            json={
                "content": message[:1900]
            },
            timeout=10
        )

        if response.status_code in (
            200,
            204
        ):
            return True

        log(
            "Discord HTTP "
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

if (
    genai is not None
    and GEMINI_API_KEY
):

    try:

        AI_CLIENT = genai.Client(
            api_key=GEMINI_API_KEY
        )

        log(
            "Gemini client READY"
        )

    except Exception as exc:

        log(
            f"Gemini init error: {exc}"
        )

        AI_CLIENT = None


def ai_comment(
    symbol,
    direction,
    score,
    zone
):

    if AI_CLIENT is None:
        return "AI: OFF"

    try:

        prompt = (
            f"Forex signal {symbol}. "
            f"Direction={direction}. "
            f"Score={score}/100. "
            f"Zone={zone}. "
            "ตอบภาษาไทยสั้น ๆ ไม่เกิน 2 บรรทัด "
            "ห้ามรับประกันกำไร"
        )

        response = AI_CLIENT.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt
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
            + str(exc)[:100]
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
            f"Memory NEW: {MEMORY_FILE}"
        )

        return

    try:

        with open(
            MEMORY_FILE,
            "r",
            encoding="utf-8"
        ) as file:

            data = json.load(
                file
            )

        if isinstance(
            data,
            list
        ):

            HISTORICAL_MEMORY = data

        elif isinstance(
            data,
            dict
        ):

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

        temporary = (
            MEMORY_FILE
            + ".tmp"
        )

        with open(
            temporary,
            "w",
            encoding="utf-8"
        ) as file:

            json.dump(
                HISTORICAL_MEMORY,
                file,
                ensure_ascii=False,
                indent=2
            )

        os.replace(
            temporary,
            MEMORY_FILE
        )

    except Exception as exc:

        log(
            f"Memory save error: {exc}"
        )


# ============================================================
# MARKET MODE
# ============================================================

def get_market_mode():

    if MARKET_MODE in (
        "LIVE",
        "OTC"
    ):
        return MARKET_MODE

    # AUTO
    #
    # Saturday / Sunday -> OTC
    # Monday-Friday -> LIVE
    #
    # แต่ OTC ต้องมี feed จริง
    weekday = now_utc().weekday()

    if weekday >= 5:
        return "OTC"

    return "LIVE"


# ============================================================
# DATA NORMALIZATION
# ============================================================

def normalize_timestamp(value):

    if isinstance(
        value,
        (int, float)
    ):

        return float(value)

    if not value:
        return None

    text = str(
        value
    ).strip()

    try:

        dt = datetime.fromisoformat(
            text.replace(
                "Z",
                "+00:00"
            )
        )

        if dt.tzinfo is None:

            dt = dt.replace(
                tzinfo=timezone.utc
            )

        return dt.timestamp()

    except Exception:

        return None


def normalize_candle(raw):

    try:

        timestamp = normalize_timestamp(
            raw.get("timestamp")
            if "timestamp" in raw
            else raw.get("datetime")
        )

        if timestamp is None:
            return None

        open_price = float(
            raw.get("open")
        )

        high_price = float(
            raw.get("high")
        )

        low_price = float(
            raw.get("low")
        )

        close_price = float(
            raw.get("close")
        )

        return {
            "timestamp": timestamp,
            "datetime": datetime.fromtimestamp(
                timestamp,
                timezone.utc
            ).strftime(
                "%Y-%m-%d %H:%M:%S"
            ),
            "open": open_price,
            "high": high_price,
            "low": low_price,
            "close": close_price,
        }

    except Exception:

        return None


def normalize_candles(raw_list):

    result = []

    if not isinstance(
        raw_list,
        list
    ):
        return result

    for item in raw_list:

        candle = normalize_candle(
            item
        )

        if candle:
            result.append(
                candle
            )

    result.sort(
        key=lambda x: x["timestamp"]
    )

    return result


# ============================================================
# YAHOO LIVE DATA
# ============================================================

def get_live_candles(
    symbol,
    interval,
    period="5d"
):

    ticker_symbol = SYMBOL_MAP.get(
        symbol
    )

    if not ticker_symbol:
        return []

    try:

        ticker = yf.Ticker(
            ticker_symbol
        )

        dataframe = ticker.history(
            period=period,
            interval=interval,
            auto_adjust=False,
            prepost=False
        )

        if dataframe is None:
            return []

        if dataframe.empty:
            return []

        result = []

        for index, row in dataframe.iterrows():

            try:

                dt = index.to_pydatetime()

                if dt.tzinfo is None:
                    dt = dt.replace(
                        tzinfo=timezone.utc
                    )

                dt = dt.astimezone(
                    timezone.utc
                )

                result.append({
                    "timestamp": dt.timestamp(),
                    "datetime": dt.strftime(
                        "%Y-%m-%d %H:%M:%S"
                    ),
                    "open": float(
                        row["Open"]
                    ),
                    "high": float(
                        row["High"]
                    ),
                    "low": float(
                        row["Low"]
                    ),
                    "close": float(
                        row["Close"]
                    ),
                })

            except Exception:
                continue

        return result

    except Exception as exc:

        log(
            f"LIVE data error "
            f"{symbol} {interval}: "
            f"{exc}"
        )

        with LOCK:
            STATS["data_errors"] += 1

        return []


# ============================================================
# OTC DATA
# ============================================================

def get_otc_payload():

    if not OTC_DATA_URL:

        return None

    try:

        response = requests.get(
            OTC_DATA_URL,
            timeout=15
        )

        response.raise_for_status()

        return response.json()

    except Exception as exc:

        log(
            f"OTC feed error: {exc}"
        )

        with LOCK:
            STATS["data_errors"] += 1

        return None


def get_otc_candles(
    symbol,
    interval
):

    payload = get_otc_payload()

    if payload is None:
        return []

    try:

        pair_data = payload.get(
            symbol,
            {}
        )

        raw = pair_data.get(
            interval,
            []
        )

        return normalize_candles(
            raw
        )

    except Exception as exc:

        log(
            f"OTC parse error "
            f"{symbol} {interval}: "
            f"{exc}"
        )

        return []


# ============================================================
# DATA PROVIDER
# ============================================================

def get_candles(
    symbol,
    interval,
    period="5d"
):

    mode = get_market_mode()

    if mode == "OTC":

        return get_otc_candles(
            symbol,
            interval
        )

    return get_live_candles(
        symbol,
        interval,
        period
    )


def data_status():

    mode = get_market_mode()

    if mode == "OTC":

        if OTC_DATA_URL:
            return "OTC FEED CONFIGURED"

        return "OTC FEED OFFLINE"

    return "LIVE / YAHOO"


# ============================================================
# CLOSED CANDLES
# ============================================================

def candle_is_closed(
    candle,
    interval_seconds
):

    return (
        time.time()
        >= candle["timestamp"]
        + interval_seconds
    )


def closed_only(
    candles,
    interval_seconds
):

    return [
        c
        for c in candles
        if candle_is_closed(
            c,
            interval_seconds
        )
    ]


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
        /
        (period + 1.0)
    )

    value = sum(
        values[:period]
    ) / period

    for price in values[period:]:

        value = (
            price - value
        ) * multiplier + value

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
            -
            values[i - 1]
        )

        gains.append(
            max(diff, 0)
        )

        losses.append(
            max(-diff, 0)
        )

    avg_gain = (
        sum(gains[:period])
        /
        period
    )

    avg_loss = (
        sum(losses[:period])
        /
        period
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
        /
        avg_loss
    )

    return (
        100
        -
        (
            100
            /
            (1 + rs)
        )
    )


def atr(
    candles,
    period=14
):

    if len(candles) < period + 1:
        return None

    true_ranges = []

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
            )
        )

        true_ranges.append(
            tr
        )

    return (
        sum(
            true_ranges[-period:]
        )
        /
        period
    )


# ============================================================
# CANDLE PATTERN
# ============================================================

def candle_features(
    current,
    previous
):

    body = abs(
        current["close"]
        -
        current["open"]
    )

    full_range = max(
        current["high"]
        -
        current["low"],
        1e-12
    )

    upper = (
        current["high"]
        -
        max(
            current["open"],
            current["close"]
        )
    )

    lower = (
        min(
            current["open"],
            current["close"]
        )
        -
        current["low"]
    )

    body_ratio = (
        body
        /
        full_range
    )

    strong_bull = (
        current["close"]
        >
        current["open"]
        and body_ratio >= 0.65
    )

    strong_bear = (
        current["close"]
        <
        current["open"]
        and body_ratio >= 0.65
    )

    hammer = (
        lower >= body * 2
        and
        upper <= full_range * 0.25
        and
        body_ratio <= 0.45
    )

    shooting_star = (
        upper >= body * 2
        and
        lower <= full_range * 0.25
        and
        body_ratio <= 0.45
    )

    bull_engulf = (
        current["close"]
        >
        current["open"]
        and
        previous["close"]
        <
        previous["open"]
        and
        current["open"]
        <=
        previous["close"]
        and
        current["close"]
        >=
        previous["open"]
        and
        body
        >
        abs(
            previous["close"]
            -
            previous["open"]
        )
    )

    bear_engulf = (
        current["close"]
        <
        current["open"]
        and
        previous["close"]
        >
        previous["open"]
        and
        current["open"]
        >=
        previous["close"]
        and
        current["close"]
        <=
        previous["open"]
        and
        body
        >
        abs(
            previous["close"]
            -
            previous["open"]
        )
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

    lr = 2

    for i in range(
        lr,
        len(data) - lr
    ):

        high = data[i]["high"]
        low = data[i]["low"]

        swing_high = all(
            high >= data[j]["high"]
            for j in range(
                i - lr,
                i + lr + 1
            )
            if j != i
        )

        swing_low = all(
            low <= data[j]["low"]
            for j in range(
                i - lr,
                i + lr + 1
            )
            if j != i
        )

        if swing_high:

            zones.append({
                "type": "RESISTANCE",
                "price": high,
                "timestamp":
                    data[i]["timestamp"]
            })

        if swing_low:

            zones.append({
                "type": "SUPPORT",
                "price": low,
                "timestamp":
                    data[i]["timestamp"]
            })

    return zones


def zone_analysis(
    candles,
    price,
    direction
):

    current_atr = atr(
        candles,
        14
    )

    if not current_atr:
        return {
            "state": "NONE",
            "score": 0,
            "level": None
        }

    zones = build_zones(
        candles
    )

    tolerance = (
        current_atr * 0.35
    )

    candidates = []

    for zone in zones:

        distance = abs(
            price
            -
            zone["price"]
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
            "level": None
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
        >
        nearest["price"]
        +
        tolerance * 0.15
        for c in recent[:-2]
    )

    broke_below = any(
        c["close"]
        <
        nearest["price"]
        -
        tolerance * 0.15
        for c in recent[:-2]
    )

    state = nearest["type"]

    score = 10

    if nearest["type"] == "RESISTANCE":

        if direction == "PUT":
            score += 15

        if (
            broke_above
            and
            price < nearest["price"]
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
            and
            price > nearest["price"]
        ):

            state = (
                "FLIPPED_SUPPORT"
            )

            score += 20

    if (
        distance
        <=
        tolerance * 0.50
    ):

        score += 10

    return {
        "state": state,
        "score": min(
            score,
            45
        ),
        "level": nearest["price"]
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
            "decision": "WAIT",
            "score": 0,
            "reason":
                "not enough 15M candles"
        }

    current = candles[-1]
    previous = candles[-2]

    closes = [
        c["close"]
        for c in candles
    ]

    price = current["close"]

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
            "decision": "WAIT",
            "score": 0,
            "reason":
                "indicator unavailable"
        }

    pattern = candle_features(
        current,
        previous
    )

    call = 0
    put = 0

    call_reasons = []
    put_reasons = []

    if price > ema20:

        call += 15
        call_reasons.append(
            "Price>EMA20"
        )

    elif price < ema20:

        put += 15
        put_reasons.append(
            "Price<EMA20"
        )

    if price > ema50:

        call += 20
        call_reasons.append(
            "Price>EMA50"
        )

    elif price < ema50:

        put += 20
        put_reasons.append(
            "Price<EMA50"
        )

    if ema20 > ema50:

        call += 10
        call_reasons.append(
            "EMA20>EMA50"
        )

    elif ema20 < ema50:

        put += 10
        put_reasons.append(
            "EMA20<EMA50"
        )

    if 50 <= rsi <= 68:

        call += 8
        call_reasons.append(
            f"RSI={rsi:.1f}"
        )

    elif 32 <= rsi < 50:

        put += 8
        put_reasons.append(
            f"RSI={rsi:.1f}"
        )

    if (
        pattern["strong_bull"]
        or pattern["hammer"]
        or pattern["bull_engulf"]
    ):

        call += 12
        call_reasons.append(
            "Bullish Candle"
        )

    if (
        pattern["strong_bear"]
        or pattern["shooting_star"]
        or pattern["bear_engulf"]
    ):

        put += 12
        put_reasons.append(
            "Bearish Candle"
        )

    zone_call = zone_analysis(
        candles,
        price,
        "CALL"
    )

    zone_put = zone_analysis(
        candles,
        price,
        "PUT"
    )

    call += zone_call["score"]
    put += zone_put["score"]

    if zone_call["score"] > 10:

        call_reasons.append(
            "ZONE "
            +
            zone_call["state"]
        )

    if zone_put["score"] > 10:

        put_reasons.append(
            "ZONE "
            +
            zone_put["state"]
        )

    if (
        call > put
        and call >= 58
    ):

        return {
            "decision": "CALL",
            "score": min(
                99,
                call
            ),
            "price": price,
            "atr": current_atr,
            "rsi": rsi,
            "ema20": ema20,
            "ema50": ema50,
            "zone_state":
                zone_call["state"],
            "zone_level":
                zone_call["level"],
            "reasons":
                " | ".join(
                    call_reasons
                ),
            "candle_time":
                current["datetime"]
        }

    if (
        put > call
        and put >= 58
    ):

        return {
            "decision": "PUT",
            "score": min(
                99,
                put
            ),
            "price": price,
            "atr": current_atr,
            "rsi": rsi,
            "ema20": ema20,
            "ema50": ema50,
            "zone_state":
                zone_put["state"],
            "zone_level":
                zone_put["level"],
            "reasons":
                " | ".join(
                    put_reasons
                ),
            "candle_time":
                current["datetime"]
        }

    return {
        "decision": "WATCH",
        "score": max(
            call,
            put
        ),
        "price": price,
        "atr": current_atr,
        "rsi": rsi,
        "ema20": ema20,
        "ema50": ema50,
        "zone_state": "MIXED",
        "zone_level": None,
        "reasons":
            "15M conflict",
        "candle_time":
            current["datetime"]
    }


# ============================================================
# 5M
# ============================================================

def analyze_5m(
    symbol,
    candles,
    master_direction
):

    if len(candles) < 70:

        return {
            "decision": "UNKNOWN",
            "score": 50,
            "reason":
                "not enough 5M candles"
        }

    current = candles[-1]
    previous = candles[-2]

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
        current,
        previous
    )

    call = 0
    put = 0

    call_reasons = []
    put_reasons = []

    if ema20 is not None:

        if current["close"] > ema20:

            call += 15
            call_reasons.append(
                "5M>EMA20"
            )

        else:

            put += 15
            put_reasons.append(
                "5M<EMA20"
            )

    if ema50 is not None:

        if current["close"] > ema50:

            call += 10
            call_reasons.append(
                "5M>EMA50"
            )

        else:

            put += 10
            put_reasons.append(
                "5M<EMA50"
            )

    if rsi is not None:

        if rsi > 50:

            call += 8
            call_reasons.append(
                f"RSI={rsi:.1f}"
            )

        elif rsi < 50:

            put += 8
            put_reasons.append(
                f"RSI={rsi:.1f}"
            )

    if (
        pattern["strong_bull"]
        or pattern["bull_engulf"]
        or pattern["hammer"]
    ):

        call += 15
        call_reasons.append(
            "Bullish Candle"
        )

    if (
        pattern["strong_bear"]
        or pattern["bear_engulf"]
        or pattern["shooting_star"]
    ):

        put += 15
        put_reasons.append(
            "Bearish Candle"
        )

    if master_direction == "CALL":

        call += 12
        call_reasons.append(
            "15M Master CALL"
        )

    elif master_direction == "PUT":

        put += 12
        put_reasons.append(
            "15M Master PUT"
        )

    if call > put:

        return {
            "decision": "CALL",
            "score": min(
                99,
                call
            ),
            "rsi": rsi,
            "candle_time":
                current["datetime"],
            "reasons":
                " | ".join(
                    call_reasons
                )
        }

    if put > call:

        return {
            "decision": "PUT",
            "score": min(
                99,
                put
            ),
            "rsi": rsi,
            "candle_time":
                current["datetime"],
            "reasons":
                " | ".join(
                    put_reasons
                )
        }

    return {
        "decision": "UNKNOWN",
        "score": 50,
        "rsi": rsi,
        "candle_time":
            current["datetime"],
        "reasons":
            "5M balanced"
    }


# ============================================================
# SCAN
# ============================================================

def scan_symbol(
    symbol
):

    candles15 = get_candles(
        symbol,
        "15m",
        "5d"
    )

    candles15 = closed_only(
        candles15,
        900
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

    if master["score"] < MIN_MASTER_SCORE:
        return None

    candles5 = get_candles(
        symbol,
        "5m",
        "5d"
    )

    candles5 = closed_only(
        candles5,
        300
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
        ==
        master["decision"]
    ):

        final_score = (
            master["score"]
            * 0.70
            +
            timing["score"]
            * 0.30
            +
            5
        )

        context = "5M_CONFIRM"

    elif timing["decision"] == "UNKNOWN":

        final_score = (
            master["score"]
            * 0.75
        )

        context = "5M_UNKNOWN"

    else:

        final_score = (
            master["score"]
            * 0.65
        )

        context = "5M_PULLBACK"

    return {
        "symbol": symbol,
        "mode": get_market_mode(),
        "decision":
            master["decision"],
        "score":
            round(
                min(
                    99,
                    final_score
                ),
                1
            ),
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
            master["zone_level"],
        "reasons_15m":
            master["reasons"],
        "reasons_5m":
            timing["reasons"],
        "signal_candle_15m":
            master["candle_time"],
        "last_5m_candle":
            timing["candle_time"],
        "created_at":
            thai_text(),
        "created_ts":
            time.time()
    }


# ============================================================
# SERIES
# ============================================================

def series_key(signal):

    return (
        signal["mode"],
        signal["symbol"],
        signal["decision"],
        signal["signal_candle_15m"]
    )


def has_active_series(
    symbol,
    mode
):

    with LOCK:

        return any(
            x["symbol"] == symbol
            and
            x["mode"] == mode
            for x in ACTIVE_SERIES
        )


def create_series(
    signal
):

    series_id = (
        f"{signal['mode']}_"
        f"{signal['symbol'].replace('/', '')}_"
        f"{int(signal['created_ts'])}"
    )

    tracker = {
        "series_id":
            series_id,

        "mode":
            signal["mode"],

        "symbol":
            signal["symbol"],

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
            signal["zone_level"],

        "signal_candle_15m":
            signal["signal_candle_15m"],

        "signal_time":
            signal["created_at"],

        "signal_ts":
            signal["created_ts"],

        "opportunity": 1,

        "completed": False,

        "wins": 0,

        "losses": 0,

        "draws": 0,

        "max_mfe": 0.0,

        "max_mae": 0.0,

        "processed_5m": [],

        "entry_price": signal["price"],

        "last_result_time": None,
    }

    with LOCK:

        ACTIVE_SERIES.append(
            tracker
        )

        STATS["signals"] += 1

    return tracker


# ============================================================
# OPPORTUNITY ENGINE
# ============================================================

def find_candle_for_opportunity(
    candles,
    tracker
):

    signal_ts = tracker[
        "signal_ts"
    ]

    used = set(
        tracker[
            "processed_5m"
        ]
    )

    step = tracker[
        "opportunity"
    ]

    # OPP1
    #
    # ถ้า Signal เกิดกลางแท่ง
    # ให้ใช้แท่งนั้นเมื่อปิด
    if step == 1:

        for candle in candles:

            start = candle[
                "timestamp"
            ]

            end = (
                start
                +
                FIVE_MIN_SECONDS
            )

            if (
                start
                <= signal_ts
                <
                end
            ):

                if (
                    candle["datetime"]
                    in used
                ):
                    continue

                if not candle_is_closed(
                    candle,
                    FIVE_MIN_SECONDS
                ):
                    return None

                return candle

    # OPP2 / OPP3
    #
    # ใช้แท่ง 5M ปิดแล้ว
    # ถัดจากแท่งที่ใช้ไป
    candidates = []

    for candle in candles:

        if (
            candle["datetime"]
            in used
        ):
            continue

        close_time = (
            candle["timestamp"]
            +
            FIVE_MIN_SECONDS
        )

        if (
            close_time
            <= time.time()
        ):

            if close_time > signal_ts:

                candidates.append(
                    candle
                )

    if not candidates:
        return None

    candidates.sort(
        key=lambda x:
        x["timestamp"]
    )

    return candidates[0]


def evaluate_opportunity(
    tracker
):

    candles = get_candles(
        tracker["symbol"],
        "5m",
        "2d"
    )

    candles = closed_only(
        candles,
        FIVE_MIN_SECONDS
    )

    if len(candles) < 20:
        return None

    candle = find_candle_for_opportunity(
        candles,
        tracker
    )

    if candle is None:
        return None

    entry_price = tracker[
        "entry_price"
    ]

    close_price = candle[
        "close"
    ]

    direction = tracker[
        "master_direction"
    ]

    if (
        close_price
        ==
        entry_price
    ):

        result = "DRAW"

    elif (
        direction == "CALL"
        and
        close_price > entry_price
    ):

        result = "WIN"

    elif (
        direction == "PUT"
        and
        close_price < entry_price
    ):

        result = "WIN"

    else:

        result = "LOSS"

    if direction == "CALL":

        mfe = (
            candle["high"]
            -
            entry_price
        )

        mae = (
            entry_price
            -
            candle["low"]
        )

    else:

        mfe = (
            entry_price
            -
            candle["low"]
        )

        mae = (
            candle["high"]
            -
            entry_price
        )

    return {
        "result": result,
        "step":
            tracker["opportunity"],
        "candle": candle,
        "entry_price":
            entry_price,
        "close_price":
            close_price,
        "mfe":
            max(0, mfe),
        "mae":
            max(0, mae)
    }


# ============================================================
# RECORD RESULT
# ============================================================

def record_opportunity(
    tracker,
    outcome
):

    candle = outcome[
        "candle"
    ]

    tracker[
        "processed_5m"
    ].append(
        candle["datetime"]
    )

    tracker[
        "last_result_time"
    ] = thai_text()

    tracker[
        "max_mfe"
    ] = max(
        tracker["max_mfe"],
        outcome["mfe"]
    )

    tracker[
        "max_mae"
    ] = max(
        tracker["max_mae"],
        outcome["mae"]
    )

    result = outcome[
        "result"
    ]

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

    return result


# ============================================================
# DISCORD RESULT
# ============================================================

def send_opportunity_discord(
    tracker,
    outcome
):

    result = outcome[
        "result"
    ]

    if result == "WIN":
        icon = "🟢"

    elif result == "LOSS":
        icon = "🔴"

    else:
        icon = "🟡"

    candle = outcome[
        "candle"
    ]

    message = (
        f"{icon} **[TRADEIFY "
        f"{tracker['mode']} RESULT]**\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"💱 คู่: **{tracker['symbol']}**\n"
        f"📌 Master: **{tracker['master_direction']}**\n"
        f"🎯 Opportunity: "
        f"**{tracker['opportunity']}/"
        f"{MAX_OPPORTUNITIES}**\n"
        f"🏁 Result: **{result}**\n\n"
        f"💰 Entry: "
        f"**{outcome['entry_price']:.8f}**\n"
        f"🏁 Close: "
        f"**{outcome['close_price']:.8f}**\n"
        f"📈 MFE: "
        f"{outcome['mfe']:.8f}\n"
        f"📉 MAE: "
        f"{outcome['mae']:.8f}\n\n"
        f"🕐 Candle UTC: "
        f"{candle['datetime']}\n"
        f"🇹🇭 Result Time: "
        f"**{thai_text()} TH**\n"
        f"🧭 Zone: "
        f"{tracker['zone_state']}\n"
        f"📊 15M Score: "
        f"{tracker['master_score']}/100\n"
        f"🧠 Series: "
        f"`{tracker['series_id']}`"
    )

    send_discord(
        message
    )


# ============================================================
# FINALIZE SERIES
# ============================================================

def finalize_series(
    tracker,
    status
):

    record = {
        "type": "SERIES",

        "series_id":
            tracker["series_id"],

        "mode":
            tracker["mode"],

        "symbol":
            tracker["symbol"],

        "decision":
            tracker["master_direction"],

        "master_score":
            tracker["master_score"],

        "signal_score":
            tracker["signal_score"],

        "entry_context":
            tracker["entry_context"],

        "zone_state":
            tracker["zone_state"],

        "zone_level":
            tracker["zone_level"],

        "signal_candle_15m":
            tracker["signal_candle_15m"],

        "signal_time":
            tracker["signal_time"],

        "status":
            status,

        "wins":
            tracker["wins"],

        "losses":
            tracker["losses"],

        "draws":
            tracker["draws"],

        "opportunities_used":
            tracker["opportunity"],

        "max_mfe":
            tracker["max_mfe"],

        "max_mae":
            tracker["max_mae"],

        "recorded_at":
            thai_text()
    }

    with LOCK:

        HISTORICAL_MEMORY.append(
            record
        )

        STATS[
            "series_completed"
        ] += 1

        if status == "SERIES_WIN":

            STATS[
                "series_wins"
            ] += 1

        elif status == "FULL_LOSS":

            STATS[
                "series_full_loss"
            ] += 1

    save_memory()

    icon = (
        "🟢"
        if status == "SERIES_WIN"
        else "🔴"
    )

    message = (
        f"{icon} **[TRADEIFY "
        f"{tracker['mode']} SERIES COMPLETE]**\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"💱 **{tracker['symbol']}**\n"
        f"📌 Direction: "
        f"**{tracker['master_direction']}**\n"
        f"🏁 Status: **{status}**\n\n"
        f"🟢 WIN: {tracker['wins']}\n"
        f"🔴 LOSS: {tracker['losses']}\n"
        f"🟡 DRAW: {tracker['draws']}\n"
        f"🎯 Opportunities: "
        f"{tracker['opportunity']}/"
        f"{MAX_OPPORTUNITIES}\n\n"
        f"📊 15M Score: "
        f"{tracker['master_score']}/100\n"
        f"🧱 Zone: "
        f"{tracker['zone_state']}\n"
        f"🕐 Signal TH: "
        f"{tracker['signal_time']}\n"
        f"🕐 Complete TH: "
        f"**{thai_text()}**\n"
        f"🧠 Memory: SAVED"
    )

    send_discord(
        message
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

                outcome = evaluate_opportunity(
                    tracker
                )

                if outcome is None:
                    continue

                result = record_opportunity(
                    tracker,
                    outcome
                )

                log(
                    f"{tracker['mode']} "
                    f"{tracker['symbol']} "
                    f"OPP"
                    f"{tracker['opportunity']} "
                    f"{result} "
                    f"entry="
                    f"{outcome['entry_price']:.8f} "
                    f"close="
                    f"{outcome['close_price']:.8f}"
                )

                send_opportunity_discord(
                    tracker,
                    outcome
                )

                # WIN = Series finished
                if result == "WIN":

                    finalize_series(
                        tracker,
                        "SERIES_WIN"
                    )

                    with LOCK:

                        if tracker in ACTIVE_SERIES:

                            ACTIVE_SERIES.remove(
                                tracker
                            )

                    continue

                # DRAW
                #
                # ให้ไป opportunity ถัดไป
                if (
                    tracker["opportunity"]
                    >=
                    MAX_OPPORTUNITIES
                ):

                    finalize_series(
                        tracker,
                        "FULL_LOSS"
                    )

                    with LOCK:

                        if tracker in ACTIVE_SERIES:

                            ACTIVE_SERIES.remove(
                                tracker
                            )

                    continue

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

        time.sleep(15)


# ============================================================
# SCANNER LOOP
# ============================================================

def scanner_loop():

    while True:

        try:

            mode = get_market_mode()

            log(
                f"SCAN START | "
                f"MODE={mode} | "
                f"DATA={data_status()}"
            )

            # OTC feed missing
            if (
                mode == "OTC"
                and
                not OTC_DATA_URL
            ):

                send_discord(
                    "⚠️ **[TRADEIFY OTC OFFLINE]**\n"
                    "วันนี้ระบบเข้าสู่ OTC mode "
                    "แต่ยังไม่มี OTC_DATA_URL\n"
                    "ระบบจะไม่ใช้ Yahoo เป็นตัวแทนราคา OTC"
                )

                time.sleep(
                    SCAN_SECONDS
                )

                continue

            signals = []

            for symbol in SYMBOLS:

                try:

                    signal = scan_symbol(
                        symbol
                    )

                    if signal:

                        signals.append(
                            signal
                        )

                except Exception as exc:

                    log(
                        f"Scan {symbol} "
                        f"error: {exc}"
                    )

            signals.sort(
                key=lambda x:
                x["score"],
                reverse=True
            )

            for signal in signals:

                key = series_key(
                    signal
                )

                if key in SENT_SIGNALS:
                    continue

                if has_active_series(
                    signal["symbol"],
                    signal["mode"]
                ):
                    continue

                SENT_SIGNALS.add(
                    key
                )

                tracker = create_series(
                    signal
                )

                ai_text = ai_comment(
                    signal["symbol"],
                    signal["decision"],
                    signal["score"],
                    signal["zone_state"]
                )

                zone = (
                    signal["zone_state"]
                )

                if (
                    signal["zone_level"]
                    is not None
                ):

                    zone += (
                        " @ "
                        +
                        f"{signal['zone_level']:.8f}"
                    )

                icon = (
                    "🟢"
                    if signal["decision"]
                    ==
                    "CALL"
                    else
                    "🔴"
                )

                message = (
                    f"🚨 **[TRADEIFY "
                    f"{signal['mode']} SIGNAL]** "
                    f"{icon}\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"💱 คู่เงิน: "
                    f"**{signal['symbol']}**\n"
                    f"📌 ทิศทาง 15M: "
                    f"**{signal['decision']}**\n"
                    f"🏆 Final Score: "
                    f"**{signal['score']}/100**\n"
                    f"📊 15M Score: "
                    f"**{signal['master_score']}/100**\n"
                    f"⏱️ 5M Score: "
                    f"**{signal['entry_score']}/100**\n"
                    f"🧭 Context: "
                    f"**{signal['entry_context']}**\n"
                    f"🧱 Zone: "
                    f"**{zone}**\n"
                    f"💰 Price: "
                    f"**{signal['price']:.8f}**\n\n"
                    f"🔎 15M: "
                    f"{signal['reasons_15m']}\n"
                    f"🔎 5M: "
                    f"{signal['reasons_5m']}\n\n"
                    f"🤖 {ai_text}\n\n"
                    f"🎯 Series: "
                    f"**สูงสุด "
                    f"{MAX_OPPORTUNITIES} Opportunities**\n"
                    f"📍 OPP1 Entry: "
                    f"**{signal['price']:.8f}**\n"
                    f"🕐 Signal TH: "
                    f"**{signal['created_at']}**\n"
                    f"🕐 15M Candle UTC: "
                    f"{signal['signal_candle_15m']}\n"
                    f"🕐 5M Candle UTC: "
                    f"{signal['last_5m_candle']}\n"
                    f"📡 Data: "
                    f"**{data_status()}**\n"
                    f"🧠 Memory: "
                    f"ACTIVE"
                )

                send_discord(
                    message
                )

                log(
                    f"NEW SIGNAL | "
                    f"{signal['mode']} | "
                    f"{signal['symbol']} | "
                    f"{signal['decision']} | "
                    f"score="
                    f"{signal['score']}"
                )

                # จำกัด 1 signal ต่อ scan cycle
                break

        except Exception as exc:

            log(
                f"Scanner error: {exc}"
            )

        time.sleep(
            SCAN_SECONDS
        )


# ============================================================
# STATISTICS
# ============================================================

def calculate_stats():

    with LOCK:

        total = (
            STATS["wins"]
            +
            STATS["losses"]
            +
            STATS["draws"]
        )

        if total > 0:

            win_rate = (
                STATS["wins"]
                /
                total
                *
                100
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
            "mode":
                get_market_mode(),
            "data_status":
                data_status(),
            "thai_time":
                thai_text()
        }


# ============================================================
# STATUS DISCORD
# ============================================================

def send_status():

    stats = calculate_stats()

    message = (
        f"📊 **[TRADEIFY STATUS]**\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🕐 เวลาไทย: "
        f"**{stats['thai_time']}**\n"
        f"📡 Mode: "
        f"**{stats['mode']}**\n"
        f"📡 Data: "
        f"**{stats['data_status']}**\n\n"
        f"🚨 Signals: "
        f"{stats['signals']}\n"
        f"🟢 WIN: "
        f"{stats['wins']}\n"
        f"🔴 LOSS: "
        f"{stats['losses']}\n"
        f"🟡 DRAW: "
        f"{stats['draws']}\n"
        f"📈 Win Rate: "
        f"**{stats['win_rate']}%**\n\n"
        f"🏁 Series Completed: "
        f"{stats['series_completed']}\n"
        f"🟢 Series WIN: "
        f"{stats['series_wins']}\n"
        f"🔴 Full Loss: "
        f"{stats['series_full_loss']}\n"
        f"🔥 Active Series: "
        f"{stats['active_series']}\n"
        f"🧠 Memory: "
        f"{stats['memory_records']}"
    )

    send_discord(
        message
    )


def reporter_loop():

    while True:

        try:

            send_status()

        except Exception as exc:

            log(
                f"Reporter error: {exc}"
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

        path = urlparse(
            self.path
        ).path

        if path in (
            "/",
            "/health",
            "/status"
        ):

            stats = calculate_stats()

            body = {
                "app":
                    APP_NAME,

                "status":
                    "running",

                "time_thai":
                    stats["thai_time"],

                "timezone":
                    TIMEZONE_NAME,

                "mode":
                    stats["mode"],

                "data":
                    stats["data_status"],

                "symbols":
                    SYMBOLS,

                "active_series":
                    stats["active_series"],

                "memory_records":
                    stats["memory_records"],

                "signals":
                    stats["signals"],

                "wins":
                    stats["wins"],

                "losses":
                    stats["losses"],

                "draws":
                    stats["draws"],

                "win_rate":
                    stats["win_rate"],

                "max_opportunities":
                    MAX_OPPORTUNITIES
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
                "application/json; charset=utf-8"
            )

            self.send_header(
                "Content-Length",
                str(len(raw))
            )

            self.end_headers()

            self.wfile.write(
                raw
            )

            return

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
# STARTUP
# ============================================================

def startup_message():

    mode = get_market_mode()

    otc_status = (
        "CONFIGURED"
        if OTC_DATA_URL
        else "NOT CONFIGURED"
    )

    message = (
        f"🚀 **{APP_NAME} STARTED**\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🕐 เวลาไทย: "
        f"**{thai_text()}**\n"
        f"📡 Current Mode: "
        f"**{mode}**\n"
        f"📊 LIVE Data: "
        f"Yahoo Finance\n"
        f"📡 OTC Feed: "
        f"**{otc_status}**\n\n"
        f"🧭 15M = Master Trend\n"
        f"⏱️ 5M = Entry / Result\n"
        f"🎯 Opportunities = "
        f"**{MAX_OPPORTUNITIES}**\n"
        f"🧠 Memory = "
        f"**{MEMORY_FILE}**\n"
        f"🔔 Discord = "
        f"{'ON' if discord_enabled() else 'OFF'}\n"
        f"🤖 Gemini = "
        f"{'ON' if AI_CLIENT else 'OFF'}\n"
        f"⏳ Scan = "
        f"{SCAN_SECONDS}s\n"
        f"🌐 Port = "
        f"{PORT}\n"
        f"━━━━━━━━━━━━━━━━━━"
    )

    send_discord(
        message
    )


# ============================================================
# MAIN
# ============================================================

def main():

    log(
        "=" * 70
    )

    log(
        "TRADEIFY STARTING"
    )

    log(
        "=" * 70
    )

    load_memory()

    threading.Thread(
        target=run_health_server,
        daemon=True
    ).start()

    threading.Thread(
        target=tracker_loop,
        daemon=True
    ).start()

    threading.Thread(
        target=scanner_loop,
        daemon=True
    ).start()

    threading.Thread(
        target=reporter_loop,
        daemon=True
    ).start()

    startup_message()

    log(
        f"Mode={get_market_mode()}"
    )

    log(
        f"Data={data_status()}"
    )

    log(
        f"Thai Time={thai_text()}"
    )

    while True:

        try:

            time.sleep(
                60
            )

        except KeyboardInterrupt:

            log(
                "TRADEIFY stopped"
            )

            break

        except Exception as exc:

            log(
                f"Main error: {exc}"
            )


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":
    main()
