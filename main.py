# -*- coding: utf-8 -*-

"""
TRADEIFY
15M MASTER + 5M ENTRY
3 OPPORTUNITIES
MEMORY + DISCORD + GEMINI
THAI TIME
LIVE / OTC AUTO MODE

IMPORTANT
--------
- OTC mode is a working mode only.
- yfinance does NOT provide 8X OTC prices.
- OTC therefore uses public FX data as a proxy.
- Historical win rate is NOT a guarantee of profit.
- WIN/LOSS is determined only after the 5M candle closes.
- After LOSS, the same direction is used for OPP 2 / OPP 3.
- Maximum 3 opportunities per series.
"""

import os
import json
import time
import threading
from datetime import datetime, timezone, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler

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

SCAN_SECONDS = int(
    os.getenv(
        "SCAN_SECONDS",
        "60"
    )
)

REPORT_SECONDS = int(
    os.getenv(
        "REPORT_SECONDS",
        "1800"
    )
)

MEMORY_FILE = os.getenv(
    "MEMORY_FILE",
    "tradeify_memory.json"
)

# AUTO:
# Monday-Friday = LIVE
# Saturday-Sunday = OTC
#
# You can force:
# MARKET_MODE=LIVE
# MARKET_MODE=OTC
MARKET_MODE = os.getenv(
    "MARKET_MODE",
    "AUTO"
).upper()

MAX_OPPORTUNITIES = 3

TF5_SECONDS = 300

MIN_HISTORY_FOR_RATE = int(
    os.getenv(
        "MIN_HISTORY_FOR_RATE",
        "10"
    )
)


# ============================================================
# SYMBOLS
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
}


# ============================================================
# TIME
# ============================================================

THAI_TZ = timezone(
    timedelta(hours=7)
)


def now_utc():
    return datetime.now(
        timezone.utc
    )


def now_ts():
    return time.time()


def now_thai():
    return now_utc().astimezone(
        THAI_TZ
    )


def thai_text(dt=None):
    dt = dt or now_thai()

    return dt.strftime(
        "%Y-%m-%d %H:%M:%S"
    )


def thai_hm(dt=None):
    dt = dt or now_thai()

    return dt.strftime(
        "%H:%M"
    )


def utc_to_thai(ts):
    return datetime.fromtimestamp(
        ts,
        timezone.utc
    ).astimezone(
        THAI_TZ
    )


def log(message):
    print(
        f"[{thai_text()}] {message}",
        flush=True
    )


# ============================================================
# MARKET MODE
# ============================================================

def mode_now():

    if MARKET_MODE in (
        "LIVE",
        "OTC"
    ):
        return MARKET_MODE

    # AUTO
    # Monday = 0
    # Sunday = 6
    weekday = now_thai().weekday()

    if weekday >= 5:
        return "OTC"

    return "LIVE"


# ============================================================
# DISCORD
# ============================================================

def send_discord(message):

    if not DISCORD_WEBHOOK_URL:
        log(
            "Discord disabled: "
            "DISCORD_WEBHOOK_URL is empty"
        )
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
            f"Discord HTTP "
            f"{response.status_code}: "
            f"{response.text[:200]}"
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


def init_gemini():

    global AI_CLIENT

    if genai is None:
        log(
            "Gemini package not installed"
        )
        return

    if not GEMINI_API_KEY:
        log(
            "Gemini disabled: "
            "GEMINI_API_KEY is empty"
        )
        return

    try:

        AI_CLIENT = genai.Client(
            api_key=GEMINI_API_KEY
        )

        log(
            "Gemini ready"
        )

    except Exception as exc:

        AI_CLIENT = None

        log(
            f"Gemini init failed: {exc}"
        )


def ai_comment(signal):

    if AI_CLIENT is None:
        return "AI: OFF"

    try:

        prompt = (
            "Give a short Thai market-context "
            "comment, maximum 2 lines. "
            "Do not guarantee profit. "
            f"Pair={signal['symbol']}, "
            f"mode={signal['mode']}, "
            f"direction={signal['decision']}, "
            f"15M_RSI={signal['rsi15']:.1f}, "
            f"5M_RSI={signal['rsi5']:.1f}, "
            f"zone={signal['zone_state']}."
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
            f"{str(exc)[:80]}"
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
            f"Memory new: "
            f"{MEMORY_FILE}"
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

            history = data.get(
                "history",
                []
            )

            HISTORICAL_MEMORY = (
                history
                if isinstance(
                    history,
                    list
                )
                else []
            )

        else:

            HISTORICAL_MEMORY = []

        log(
            f"Memory loaded: "
            f"{len(HISTORICAL_MEMORY)} records"
        )

    except Exception as exc:

        log(
            f"Memory load error: "
            f"{exc}"
        )

        HISTORICAL_MEMORY = []


def save_memory():

    try:

        directory = os.path.dirname(
            os.path.abspath(
                MEMORY_FILE
            )
        )

        if directory:
            os.makedirs(
                directory,
                exist_ok=True
            )

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
                indent=2
            )

        os.replace(
            temp_file,
            MEMORY_FILE
        )

    except Exception as exc:

        log(
            f"Memory save error: "
            f"{exc}"
        )


# ============================================================
# DATAFRAME CLEANING
# ============================================================

def clean_dataframe(df):

    if df is None:
        return None

    if df.empty:
        return None

    try:

        df = df.copy()

        # Handle possible MultiIndex columns
        if hasattr(
            df.columns,
            "levels"
        ):

            try:

                if len(
                    df.columns.levels
                ) > 1:

                    df.columns = [
                        column[0]
                        if isinstance(
                            column,
                            tuple
                        )
                        else column
                        for column in df.columns
                    ]

            except Exception:
                pass

        required = [
            "Open",
            "High",
            "Low",
            "Close",
        ]

        for column in required:

            if column not in df.columns:
                return None

        # Convert timezone correctly
        try:

            if getattr(
                df.index,
                "tz",
                None
            ) is not None:

                df.index = (
                    df.index
                    .tz_convert("UTC")
                    .tz_localize(None)
                )

        except Exception:
            pass

        df = df.dropna(
            subset=[
                "Open",
                "High",
                "Low",
                "Close",
            ]
        )

        return df

    except Exception as exc:

        log(
            f"Dataframe clean error: "
            f"{exc}"
        )

        return None


# ============================================================
# MARKET DATA
# ============================================================

def get_candles(
    symbol,
    interval,
    period="5d"
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
            prepost=False
        )

        df = clean_dataframe(
            df
        )

        if df is None:
            return []

        if len(df) < 10:
            return []

        candles = []

        for idx, row in df.iterrows():

            try:

                timestamp = (
                    idx.to_pydatetime()
                )

                if timestamp.tzinfo is None:

                    timestamp = (
                        timestamp.replace(
                            tzinfo=timezone.utc
                        )
                    )

                timestamp = (
                    timestamp.astimezone(
                        timezone.utc
                    )
                )

                candles.append(
                    {
                        "datetime":
                            timestamp.strftime(
                                "%Y-%m-%d %H:%M:%S"
                            ),

                        "timestamp":
                            timestamp.timestamp(),

                        "open":
                            float(
                                row["Open"]
                            ),

                        "high":
                            float(
                                row["High"]
                            ),

                        "low":
                            float(
                                row["Low"]
                            ),

                        "close":
                            float(
                                row["Close"]
                            ),
                    }
                )

            except Exception:
                continue

        candles.sort(
            key=lambda x:
            x["timestamp"]
        )

        return candles

    except Exception as exc:

        log(
            f"Yahoo "
            f"{symbol} "
            f"{interval}: "
            f"{exc}"
        )

        return []


# ============================================================
# CANDLE TIME
# ============================================================

def candle_close_ts(candle):

    return (
        candle["timestamp"]
        + TF5_SECONDS
    )


def is_closed_5m(
    candle,
    current_ts=None
):

    if current_ts is None:
        current_ts = now_ts()

    return (
        candle_close_ts(candle)
        <= current_ts
    )


def closed_only(candles):

    current_ts = now_ts()

    return [
        candle
        for candle in candles
        if is_closed_5m(
            candle,
            current_ts
        )
    ]


def next_5m_open_after(
    timestamp
):

    # Strictly future 5-minute boundary
    return (
        int(timestamp // TF5_SECONDS)
        + 1
    ) * TF5_SECONDS


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

        difference = (
            values[i]
            - values[i - 1]
        )

        gains.append(
            max(
                difference,
                0.0
            )
        )

        losses.append(
            max(
                -difference,
                0.0
            )
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

    true_ranges = []

    for i in range(
        1,
        len(candles)
    ):

        current = candles[i]
        previous = candles[i - 1]

        true_ranges.append(
            max(
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
        )

    return (
        sum(
            true_ranges[-period:]
        )
        / period
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
        - current["open"]
    )

    candle_range = max(
        current["high"]
        - current["low"],
        1e-12
    )

    upper_wick = (
        current["high"]
        - max(
            current["open"],
            current["close"]
        )
    )

    lower_wick = (
        min(
            current["open"],
            current["close"]
        )
        - current["low"]
    )

    body_ratio = (
        body
        / candle_range
    )

    previous_body = abs(
        previous["close"]
        - previous["open"]
    )

    return {

        "strong_bull":
            (
                current["close"]
                > current["open"]
                and body_ratio >= 0.65
            ),

        "strong_bear":
            (
                current["close"]
                < current["open"]
                and body_ratio >= 0.65
            ),

        "hammer":
            (
                lower_wick >= body * 2
                and upper_wick
                <= candle_range * 0.25
                and body_ratio <= 0.45
            ),

        "shooting_star":
            (
                upper_wick >= body * 2
                and lower_wick
                <= candle_range * 0.25
                and body_ratio <= 0.45
            ),

        "bull_engulf":
            (
                current["close"]
                > current["open"]
                and previous["close"]
                < previous["open"]
                and current["open"]
                <= previous["close"]
                and current["close"]
                >= previous["open"]
                and body
                > previous_body
            ),

        "bear_engulf":
            (
                current["close"]
                < current["open"]
                and previous["close"]
                > previous["open"]
                and current["open"]
                >= previous["close"]
                and current["close"]
                <= previous["open"]
                and body
                > previous_body
            ),
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

    for i in range(
        2,
        len(data) - 2
    ):

        high = data[i]["high"]
        low = data[i]["low"]

        high_ok = all(
            high >= data[j]["high"]
            for j in range(
                i - 2,
                i + 3
            )
            if j != i
        )

        low_ok = all(
            low <= data[j]["low"]
            for j in range(
                i - 2,
                i + 3
            )
            if j != i
        )

        if high_ok:

            zones.append(
                {
                    "type":
                        "RESISTANCE",

                    "price":
                        high,

                    "timestamp":
                        data[i]["timestamp"],
                }
            )

        if low_ok:

            zones.append(
                {
                    "type":
                        "SUPPORT",

                    "price":
                        low,

                    "timestamp":
                        data[i]["timestamp"],
                }
            )

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
            "level": None,
        }

    if current_atr <= 0:
        return {
            "state": "NONE",
            "score": 0,
            "level": None,
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
            "level": None,
        }

    candidates.sort(
        key=lambda item:
        item[0]
    )

    distance, nearest = (
        candidates[0]
    )

    state = nearest["type"]

    score = 10

    if (
        nearest["type"]
        == "RESISTANCE"
    ):

        if direction == "PUT":
            score += 15

    elif (
        nearest["type"]
        == "SUPPORT"
    ):

        if direction == "CALL":
            score += 15

    if distance <= (
        tolerance * 0.50
    ):

        score += 10

    return {
        "state": state,
        "score": min(
            score,
            45
        ),
        "level":
            nearest["price"],
    }


# ============================================================
# 15M MASTER
# ============================================================

def analyze_15m(
    candles
):

    if len(candles) < 80:
        return None

    current = candles[-1]
    previous = candles[-2]

    closes = [
        candle["close"]
        for candle in candles
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

    current_atr = atr(
        candles,
        14
    )

    if any(
        value is None
        for value in (
            ema20,
            ema50,
            rsi,
            current_atr
        )
    ):
        return None

    price = current["close"]

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
            "price>EMA20"
        )

    else:

        put += 15
        put_reasons.append(
            "price<EMA20"
        )

    if price > ema50:

        call += 20
        call_reasons.append(
            "price>EMA50"
        )

    else:

        put += 20
        put_reasons.append(
            "price<EMA50"
        )

    if ema20 > ema50:

        call += 10
        call_reasons.append(
            "EMA20>EMA50"
        )

    else:

        put += 10
        put_reasons.append(
            "EMA20<EMA50"
        )

    if 50 <= rsi <= 68:

        call += 8
        call_reasons.append(
            f"RSI={rsi:.1f}"
        )

    if 32 <= rsi < 50:

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
            "bullish-candle"
        )

    if (
        pattern["strong_bear"]
        or pattern["shooting_star"]
        or pattern["bear_engulf"]
    ):

        put += 12
        put_reasons.append(
            "bearish-candle"
        )

    call_zone = zone_analysis(
        candles,
        price,
        "CALL"
    )

    put_zone = zone_analysis(
        candles,
        price,
        "PUT"
    )

    call += call_zone["score"]
    put += put_zone["score"]

    if call_zone["score"] > 10:

        call_reasons.append(
            f"ZONE={call_zone['state']}"
        )

    if put_zone["score"] > 10:

        put_reasons.append(
            f"ZONE={put_zone['state']}"
        )

    if (
        call > put
        and call >= 58
    ):

        decision = "CALL"
        strength = min(
            99,
            call
        )
        reasons = call_reasons
        zone = call_zone

    elif (
        put > call
        and put >= 58
    ):

        decision = "PUT"
        strength = min(
            99,
            put
        )
        reasons = put_reasons
        zone = put_zone

    else:

        return None

    return {
        "decision":
            decision,

        "setup_strength":
            strength,

        "price":
            price,

        "atr":
            current_atr,

        "rsi":
            rsi,

        "zone_state":
            zone["state"],

        "zone_level":
            zone["level"],

        "reasons":
            " | ".join(
                reasons
            ),

        "candle_time":
            current["datetime"],
    }


# ============================================================
# 5M ENTRY CONTEXT
# ============================================================

def analyze_5m(
    candles,
    master_direction
):

    if len(candles) < 70:
        return None

    current = candles[-1]
    previous = candles[-2]

    closes = [
        candle["close"]
        for candle in candles
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

    if (
        ema20 is None
        or ema50 is None
        or rsi is None
    ):
        return None

    pattern = candle_features(
        current,
        previous
    )

    call = 0
    put = 0

    call_reasons = []
    put_reasons = []

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

    if rsi > 50:

        call += 8
        call_reasons.append(
            f"RSI={rsi:.1f}"
        )

    else:

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
            "bullish-candle"
        )

    if (
        pattern["strong_bear"]
        or pattern["bear_engulf"]
        or pattern["shooting_star"]
    ):

        put += 15
        put_reasons.append(
            "bearish-candle"
        )

    if master_direction == "CALL":

        call += 12
        call_reasons.append(
            "15M-master-CALL"
        )

    else:

        put += 12
        put_reasons.append(
            "15M-master-PUT"
        )

    if call > put:

        decision = "CALL"
        score = min(
            99,
            call
        )
        reasons = call_reasons

    elif put > call:

        decision = "PUT"
        score = min(
            99,
            put
        )
        reasons = put_reasons

    else:

        decision = "UNKNOWN"
        score = 50
        reasons = [
            "balanced"
        ]

    return {
        "decision":
            decision,

        "score":
            score,

        "rsi":
            rsi,

        "candle_time":
            current["datetime"],

        "reasons":
            " | ".join(
                reasons
            ),
    }


# ============================================================
# HISTORICAL
# ============================================================

def setup_signature(
    symbol,
    mode,
    direction,
    zone_state
):

    return (
        symbol,
        mode,
        direction,
        zone_state
    )


def historical_stats(
    signature
):

    records = []

    with LOCK:

        for record in HISTORICAL_MEMORY:

            if record.get(
                "type"
            ) != "SERIES":

                continue

            record_signature = (
                record.get(
                    "symbol"
                ),
                record.get(
                    "mode"
                ),
                record.get(
                    "decision"
                ),
                record.get(
                    "zone_state"
                ),
            )

            if (
                record_signature
                == signature
            ):

                records.append(
                    record
                )

    total = len(
        records
    )

    series_wins = sum(
        1
        for record in records
        if record.get(
            "status"
        ) == "SERIES_WIN"
    )

    first_wins = sum(
        1
        for record in records
        if record.get(
            "first_opportunity_result"
        ) == "WIN"
    )

    if (
        total
        >= MIN_HISTORY_FOR_RATE
    ):

        series_rate = round(
            series_wins
            / total
            * 100,
            1
        )

        first_rate = round(
            first_wins
            / total
            * 100,
            1
        )

        confidence = (
            "USABLE"
        )

    else:

        series_rate = None
        first_rate = None

        confidence = (
            "INSUFFICIENT_DATA"
        )

    return {
        "samples":
            total,

        "series_win_rate":
            series_rate,

        "first_win_rate":
            first_rate,

        "confidence":
            confidence,
    }


# ============================================================
# NEXT ENTRY TIME
# ============================================================

def calculate_entry_time(
    latest_closed_5m
):

    current = now_ts()

    normal_next = (
        latest_closed_5m["timestamp"]
        + TF5_SECONDS
    )

    # If the next candle has already opened,
    # do NOT enter a candle that is already running.
    if normal_next <= current:

        return next_5m_open_after(
            current
        )

    return normal_next


# ============================================================
# SCAN SYMBOL
# ============================================================

def scan_symbol(
    symbol
):

    # ----------------------------
    # 15M
    # ----------------------------

    raw15 = get_candles(
        symbol,
        "15m",
        "5d"
    )

    closed15 = closed_only(
        raw15
    )

    if len(closed15) < 80:
        return None

    master = analyze_15m(
        closed15
    )

    if master is None:
        return None

    # ----------------------------
    # 5M
    # ----------------------------

    raw5 = get_candles(
        symbol,
        "5m",
        "5d"
    )

    closed5 = closed_only(
        raw5
    )

    if len(closed5) < 70:
        return None

    timing = analyze_5m(
        closed5,
        master["decision"]
    )

    if timing is None:
        return None

    if (
        timing["decision"]
        == master["decision"]
    ):

        context = (
            "5M_CONFIRM"
        )

    elif (
        timing["decision"]
        == "UNKNOWN"
    ):

        context = (
            "5M_UNKNOWN"
        )

    else:

        context = (
            "5M_PULLBACK"
        )

    if master[
        "setup_strength"
    ] < 62:

        return None

    mode = mode_now()

    signature = setup_signature(
        symbol,
        mode,
        master["decision"],
        master["zone_state"]
    )

    history = historical_stats(
        signature
    )

    latest5 = closed5[-1]

    entry_ts = calculate_entry_time(
        latest5
    )

    close_ts = (
        entry_ts
        + TF5_SECONDS
    )

    signal_timestamp = now_ts()

    return {

        "symbol":
            symbol,

        "mode":
            mode,

        "decision":
            master["decision"],

        "setup_strength":
            round(
                master["setup_strength"],
                1
            ),

        "rsi15":
            master["rsi"],

        "rsi5":
            timing["rsi"],

        "entry_score":
            timing["score"],

        "entry_context":
            context,

        "zone_state":
            master["zone_state"],

        "zone_level":
            master["zone_level"],

        "price":
            master["price"],

        "atr":
            master["atr"],

        "reasons15":
            master["reasons"],

        "reasons5":
            timing["reasons"],

        "signal_candle15":
            master["candle_time"],

        "last_closed_5m":
            latest5["datetime"],

        "signal_ts":
            signal_timestamp,

        "next_open_ts":
            entry_ts,

        "next_close_ts":
            close_ts,

        "history":
            history,

        "created_at":
            thai_text(),
    }


# ============================================================
# ACTIVE SERIES
# ============================================================

def has_active_series(
    symbol
):

    with LOCK:

        return any(
            series["symbol"]
            == symbol
            for series
            in ACTIVE_SERIES
        )


def create_series(
    signal
):

    series_id = (
        f"{signal['symbol'].replace('/', '')}_"
        f"{int(signal['signal_ts'])}"
    )

    tracker = {

        "type":
            "ACTIVE_SERIES",

        "series_id":
            series_id,

        "symbol":
            signal["symbol"],

        "mode":
            signal["mode"],

        "master_direction":
            signal["decision"],

        "setup_strength":
            signal["setup_strength"],

        "entry_context":
            signal["entry_context"],

        "zone_state":
            signal["zone_state"],

        "zone_level":
            signal["zone_level"],

        "signal_time":
            signal["created_at"],

        "signal_ts":
            signal["signal_ts"],

        "next_entry_ts":
            signal["next_open_ts"],

        "next_close_ts":
            signal["next_close_ts"],

        "opportunity":
            1,

        "wins":
            0,

        "losses":
            0,

        "draws":
            0,

        "first_opportunity_result":
            None,

        "processed_5m":
            [],

        "max_mfe":
            0.0,

        "max_mae":
            0.0,
    }

    with LOCK:

        ACTIVE_SERIES.append(
            tracker
        )

        STATS["signals"] += 1

    return tracker


# ============================================================
# FIND ENTRY CANDLE
# ============================================================

def find_candle_for_entry(
    candles,
    entry_ts
):

    tolerance = 2.0

    for candle in candles:

        candle_open = (
            candle["timestamp"]
        )

        if abs(
            candle_open
            - entry_ts
        ) <= tolerance:

            if is_closed_5m(
                candle
            ):

                return candle

    return None


# ============================================================
# EVALUATE OPPORTUNITY
# ============================================================

def evaluate_opportunity(
    tracker
):

    candles = closed_only(
        get_candles(
            tracker["symbol"],
            "5m",
            "2d"
        )
    )

    if len(candles) < 20:
        return None

    entry_ts = (
        tracker["next_entry_ts"]
    )

    candidate = (
        find_candle_for_entry(
            candles,
            entry_ts
        )
    )

    if candidate is None:
        return None

    candle_id = (
        candidate["datetime"]
    )

    if candle_id in (
        tracker["processed_5m"]
    ):

        return None

    entry_price = (
        candidate["open"]
    )

    close_price = (
        candidate["close"]
    )

    direction = (
        tracker["master_direction"]
    )

    if close_price > entry_price:

        if direction == "CALL":
            result = "WIN"
        else:
            result = "LOSS"

    elif close_price < entry_price:

        if direction == "PUT":
            result = "WIN"
        else:
            result = "LOSS"

    else:

        result = "DRAW"

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
            result,

        "candle":
            candidate,

        "entry_price":
            entry_price,

        "close_price":
            close_price,

        "mfe":
            max(
                0.0,
                mfe
            ),

        "mae":
            max(
                0.0,
                mae
            ),
    }


# ============================================================
# RECORD OPPORTUNITY
# ============================================================

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
        tracker["max_mfe"],
        outcome["mfe"]
    )

    tracker["max_mae"] = max(
        tracker["max_mae"],
        outcome["mae"]
    )

    result = (
        outcome["result"]
    )

    if result == "WIN":

        tracker["wins"] += 1
        STATS["wins"] += 1

    elif result == "LOSS":

        tracker["losses"] += 1
        STATS["losses"] += 1

    else:

        tracker["draws"] += 1
        STATS["draws"] += 1

    if tracker[
        "opportunity"
    ] == 1:

        tracker[
            "first_opportunity_result"
        ] = result

    return result


# ============================================================
# FINALIZE SERIES
# ============================================================

def finalize_series(
    tracker,
    status
):

    record = {

        "type":
            "SERIES",

        "series_id":
            tracker["series_id"],

        "symbol":
            tracker["symbol"],

        "mode":
            tracker["mode"],

        "decision":
            tracker[
                "master_direction"
            ],

        "setup_strength":
            tracker[
                "setup_strength"
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

        "signal_time":
            tracker[
                "signal_time"
            ],

        "status":
            status,

        "wins":
            tracker["wins"],

        "losses":
            tracker["losses"],

        "draws":
            tracker["draws"],

        "opportunities_used":
            tracker[
                "opportunity"
            ],

        "first_opportunity_result":
            tracker[
                "first_opportunity_result"
            ],

        "max_mfe":
            tracker["max_mfe"],

        "max_mae":
            tracker["max_mae"],

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

        if status == "SERIES_WIN":

            STATS[
                "series_wins"
            ] += 1

        elif status == "FULL_LOSS":

            STATS[
                "series_full_loss"
            ] += 1

    save_memory()

    history = historical_stats(
        setup_signature(
            tracker["symbol"],
            tracker["mode"],
            tracker[
                "master_direction"
            ],
            tracker[
                "zone_state"
            ]
        )
    )

    if (
        history[
            "series_win_rate"
        ]
        is None
    ):

        rate_text = (
            "INSUFFICIENT_DATA"
        )

    else:

        rate_text = (
            f"{history['series_win_rate']}%"
        )

    if status == "SERIES_WIN":

        icon = "🟢"

    else:

        icon = "🔴"

    send_discord(

        f"{icon} "
        f"**[TRADEIFY SERIES COMPLETE]**\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💱 คู่: **{tracker['symbol']}**\n"
        f"🌐 Mode: **{tracker['mode']}**\n"
        f"📌 Direction: "
        f"**{tracker['master_direction']}**\n"
        f"🏁 Status: **{status}**\n"
        f"🎯 WIN: **{tracker['wins']}**\n"
        f"❌ LOSS: **{tracker['losses']}**\n"
        f"➖ DRAW: **{tracker['draws']}**\n"
        f"🔢 Opportunities: "
        f"**{tracker['opportunity']}/3**\n"
        f"📈 Historical Series Win Rate: "
        f"**{rate_text}**\n"
        f"📚 Historical Samples: "
        f"**{history['samples']}**\n"
        f"🧭 Zone: "
        f"**{tracker['zone_state']}**\n"
        f"🕐 เวลาไทย: "
        f"**{thai_text()}**"
    )


# ============================================================
# REMOVE ACTIVE SERIES
# ============================================================

def remove_active_series(
    tracker
):

    with LOCK:

        if tracker in ACTIVE_SERIES:

            ACTIVE_SERIES.remove(
                tracker
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
                    evaluate_opportunity(
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

                candle = (
                    outcome["candle"]
                )

                if result == "WIN":

                    icon = "🟢"

                elif result == "LOSS":

                    icon = "🔴"

                else:

                    icon = "🟡"

                send_discord(

                    f"{icon} "
                    f"**[TRADEIFY 5M RESULT]**\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"💱 **{tracker['symbol']}**\n"
                    f"🌐 Mode: "
                    f"**{tracker['mode']}**\n"
                    f"📌 Master: "
                    f"**{tracker['master_direction']}**\n"
                    f"🎯 OPP: "
                    f"**{tracker['opportunity']}/3**\n"
                    f"🏁 Result: **{result}**\n"
                    f"💰 Entry: "
                    f"**{outcome['entry_price']:.8f}**\n"
                    f"🔚 Close: "
                    f"**{outcome['close_price']:.8f}**\n"
                    f"🕐 Candle UTC: "
                    f"**{candle['datetime']}**\n"
                    f"🇹🇭 เวลาแจ้ง: "
                    f"**{thai_text()}**\n"
                    f"📈 MFE: "
                    f"**{outcome['mfe']:.8f}**\n"
                    f"📉 MAE: "
                    f"**{outcome['mae']:.8f}**\n"
                    f"⚠️ ผลถูกตัดสิน "
                    f"หลังแท่ง 5M ปิดแล้วเท่านั้น"
                )

                # -------------------------
                # WIN
                # -------------------------

                if result == "WIN":

                    finalize_series(
                        tracker,
                        "SERIES_WIN"
                    )

                    remove_active_series(
                        tracker
                    )

                    continue

                # -------------------------
                # DRAW
                # -------------------------

                if result == "DRAW":

                    # DRAW ไม่ถือเป็น WIN
                    # และไม่เพิ่ม opportunity
                    # แต่ให้รอแท่งถัดไป
                    if (
                        tracker[
                            "opportunity"
                        ]
                        >= MAX_OPPORTUNITIES
                    ):

                        finalize_series(
                            tracker,
                            "FULL_LOSS"
                        )

                        remove_active_series(
                            tracker
                        )

                        continue

                # -------------------------
                # LOSS / DRAW
                # -------------------------

                if (
                    tracker[
                        "opportunity"
                    ]
                    >= MAX_OPPORTUNITIES
                ):

                    finalize_series(
                        tracker,
                        "FULL_LOSS"
                    )

                    remove_active_series(
                        tracker
                    )

                    continue

                tracker[
                    "opportunity"
                ] += 1

                # IMPORTANT:
                # Next opportunity is the next
                # 5M candle after the candle
                # that was just evaluated.
                next_entry = (
                    candle["timestamp"]
                    + TF5_SECONDS
                )

                # If next candle is already
                # running, wait for the following
                # complete 5M boundary.
                if next_entry <= now_ts():

                    next_entry = (
                        next_5m_open_after(
                            now_ts()
                        )
                    )

                tracker[
                    "next_entry_ts"
                ] = next_entry

                tracker[
                    "next_close_ts"
                ] = (
                    next_entry
                    + TF5_SECONDS
                )

                log(

                    f"{tracker['symbol']} "
                    f"{result} -> "
                    f"OPP"
                    f"{tracker['opportunity']}/3 "
                    f"keep "
                    f"{tracker['master_direction']}"
                )

                open_dt = utc_to_thai(
                    next_entry
                )

                close_dt = utc_to_thai(
                    next_entry
                    + TF5_SECONDS
                )

                send_discord(

                    f"🔁 "
                    f"**[TRADEIFY NEXT OPPORTUNITY]**\n"
                    f"💱 {tracker['symbol']}\n"
                    f"🌐 Mode: "
                    f"{tracker['mode']}\n"
                    f"📌 Direction เดิม: "
                    f"**{tracker['master_direction']}**\n"
                    f"🎯 OPP: "
                    f"**{tracker['opportunity']}/3**\n"
                    f"🟢 เตรียมเข้าแท่งหน้า\n"
                    f"⏰ เข้า: "
                    f"**{thai_hm(open_dt)}**\n"
                    f"🔚 ปิดแท่ง: "
                    f"**{thai_hm(close_dt)}**\n"
                    f"⚠️ รอแท่งปิดก่อนตัดผล"
                )

        except Exception as exc:

            log(
                f"Tracker error: "
                f"{exc}"
            )

        time.sleep(10)


# ============================================================
# SCANNER LOOP
# ============================================================

def scanner_loop():

    while True:

        try:

            candidates = []

            for symbol in SYMBOLS:

                try:

                    signal = scan_symbol(
                        symbol
                    )

                    if signal:

                        candidates.append(
                            signal
                        )

                except Exception as exc:

                    log(
                        f"Scanner "
                        f"{symbol}: "
                        f"{exc}"
                    )

            candidates.sort(
                key=lambda signal:
                signal[
                    "setup_strength"
                ],
                reverse=True
            )

            for signal in candidates:

                # Prevent duplicate signal
                key = (
                    signal["symbol"],
                    signal["mode"],
                    signal["decision"],
                    signal[
                        "signal_candle15"
                    ]
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

                tracker = (
                    create_series(
                        signal
                    )
                )

                history = (
                    signal["history"]
                )

                if (
                    history[
                        "series_win_rate"
                    ]
                    is None
                ):

                    history_rate = (
                        "INSUFFICIENT_DATA"
                    )

                else:

                    history_rate = (
                        f"{history['series_win_rate']}%"
                    )

                if (
                    signal["decision"]
                    == "CALL"
                ):

                    direction_icon = "🟢"

                else:

                    direction_icon = "🔴"

                open_dt = utc_to_thai(
                    signal[
                        "next_open_ts"
                    ]
                )

                close_dt = utc_to_thai(
                    signal[
                        "next_close_ts"
                    ]
                )

                ai_text = (
                    ai_comment(
                        signal
                    )
                )

                first_rate = (
                    history[
                        "first_win_rate"
                    ]
                )

                if first_rate is None:

                    first_rate_text = (
                        "INSUFFICIENT_DATA"
                    )

                else:

                    first_rate_text = (
                        f"{first_rate}%"
                    )

                message = (

                    f"🚨 "
                    f"**[TRADEIFY SIGNAL]**\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"⏱️ TF: **5M**\n"
                    f"💱 คู่: **{signal['symbol']}**\n\n"

                    f"⭐️ "
                    f"**เตือนเวลา "
                    f"{thai_hm()} น.** ⭐️\n"
                    f"🇹🇭 เวลาไทย\n\n"

                    f"🌐 Mode: "
                    f"**{signal['mode']}**\n"

                    f"📌 Direction: "
                    f"**{signal['decision']}** "
                    f"{direction_icon}\n"

                    f"🟢 "
                    f"**เตรียมซื้อแท่งหน้า**\n\n"

                    f"📊 SETUP STRENGTH: "
                    f"**{signal['setup_strength']}/100**\n"

                    f"📈 15M RSI: "
                    f"**{signal['rsi15']:.1f}**\n"

                    f"📉 5M RSI: "
                    f"**{signal['rsi5']:.1f}**\n"

                    f"⏱️ 5M Context: "
                    f"**{signal['entry_context']}**\n"

                    f"🧭 Zone: "
                    f"**{signal['zone_state']}**\n\n"

                    f"📚 HISTORICAL "
                    f"(เฉพาะ setup นี้)\n"

                    f"• Samples: "
                    f"**{history['samples']}**\n"

                    f"• Series Win Rate 1–3: "
                    f"**{history_rate}**\n"

                    f"• First Entry Win Rate: "
                    f"**{first_rate_text}**\n\n"

                    f"🎯 Opportunity: **1/3**\n"

                    f"⏰ เข้าแท่ง: "
                    f"**{thai_hm(open_dt)} น.**\n"

                    f"🔚 ปิดแท่ง: "
                    f"**{thai_hm(close_dt)} น.**\n\n"

                    f"🔎 15M: "
                    f"{signal['reasons15']}\n"

                    f"🔎 5M: "
                    f"{signal['reasons5']}\n"

                    f"🤖 {ai_text}\n\n"

                    f"⚠️ "
                    f"**รอแท่งใหม่ก่อนเข้า**\n"

                    f"⚠️ "
                    f"**ผล WIN/LOSS "
                    f"ตัดสินหลังแท่ง 5M ปิดเท่านั้น**\n"

                    f"🕐 Signal: "
                    f"**{signal['created_at']}**"
                )

                send_discord(
                    message
                )

                log(
                    f"NEW "
                    f"{signal['symbol']} "
                    f"{signal['decision']} "
                    f"mode={signal['mode']} "
                    f"strength="
                    f"{signal['setup_strength']}"
                )

                # Only one new signal
                # per scan cycle
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
# STATS
# ============================================================

def calculate_stats():

    with LOCK:

        total = (
            STATS["wins"]
            + STATS["losses"]
            + STATS["draws"]
        )

        if total:

            win_rate = round(
                STATS["wins"]
                / total
                * 100,
                2
            )

        else:

            win_rate = None

        return {

            **STATS,

            "total_opportunities":
                total,

            "win_rate":
                win_rate,

            "active_series":
                len(
                    ACTIVE_SERIES
                ),

            "memory_records":
                len(
                    HISTORICAL_MEMORY
                ),
        }


# ============================================================
# REPORTER
# ============================================================

def reporter_loop():

    while True:

        try:

            stats = (
                calculate_stats()
            )

            if (
                stats["win_rate"]
                is None
            ):

                win_rate_text = (
                    "INSUFFICIENT_DATA"
                )

            else:

                win_rate_text = (
                    f"{stats['win_rate']}%"
                )

            send_discord(

                f"📊 "
                f"**[TRADEIFY STATUS]**\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"🇹🇭 เวลาไทย: "
                f"**{thai_text()}**\n"

                f"📡 Mode ตอนนี้: "
                f"**{mode_now()}**\n"

                f"📣 Signals: "
                f"**{stats['signals']}**\n"

                f"🟢 WIN: "
                f"**{stats['wins']}**\n"

                f"🔴 LOSS: "
                f"**{stats['losses']}**\n"

                f"🟡 DRAW: "
                f"**{stats['draws']}**\n"

                f"📈 Opportunity Win Rate: "
                f"**{win_rate_text}**\n"

                f"🏁 Series Complete: "
                f"**{stats['series_completed']}**\n"

                f"🟢 Series Win: "
                f"**{stats['series_wins']}**\n"

                f"🔴 Full Loss: "
                f"**{stats['series_full_loss']}**\n"

                f"🔄 Active Series: "
                f"**{stats['active_series']}**\n"

                f"💾 Memory: "
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

                "status":
                    "running",

                "time_thai":
                    thai_text(),

                "mode":
                    mode_now(),

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

                "wins":
                    stats["wins"],

                "losses":
                    stats["losses"],

                "draws":
                    stats["draws"],

                "win_rate":
                    stats["win_rate"],
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
            f"Health server :{PORT}"
        )

        server.serve_forever()

    except Exception as exc:

        log(
            f"Health server error: "
            f"{exc}"
        )


# ============================================================
# STARTUP MESSAGE
# ============================================================

def startup_message():

    send_discord(

        f"🚀 "
        f"**[TRADEIFY STARTED]**\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"

        f"🇹🇭 เวลาไทย: "
        f"**{thai_text()}**\n"

        f"🌐 Mode: "
        f"**{mode_now()}**\n"

        f"⏱️ Master: "
        f"**15M**\n"

        f"🎯 Entry/Result: "
        f"**5M**\n"

        f"🔢 Opportunities: "
        f"**1–3**\n"

        f"💾 Memory: "
        f"**ON**\n"

        f"📡 Symbols: "
        f"**{len(SYMBOLS)}**\n"

        f"⏳ Scanner: "
        f"**{SCAN_SECONDS}s**\n\n"

        f"⚠️ OTC เป็น proxy จาก "
        f"public FX feed "
        f"ไม่ใช่ราคา OTC จาก 8X โดยตรง"
    )


# ============================================================
# MAIN
# ============================================================

def main():

    log(
        "=" * 70
    )

    log(
        "TRADEIFY "
        "15M + 5M + "
        "3 OPPORTUNITIES"
    )

    log(
        "=" * 70
    )

    log(
        f"Python process started"
    )

    log(
        f"Mode: {mode_now()}"
    )

    log(
        f"Memory file: "
        f"{MEMORY_FILE}"
    )

    load_memory()

    init_gemini()

    # Health server
    threading.Thread(
        target=run_health_server,
        daemon=True
    ).start()

    # Tracker
    threading.Thread(
        target=tracker_loop,
        daemon=True
    ).start()

    # Scanner
    threading.Thread(
        target=scanner_loop,
        daemon=True
    ).start()

    # Reporter
    threading.Thread(
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


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    main()
