# -*- coding: utf-8 -*-

"""
============================================================
TRADEIFY V8
A+ MTF SNIPER — PYTHON ENGINE
============================================================

15M = MASTER TREND
5M  = CONFIRMATION
1M  = ENTRY / REJECTION

MATCHED TO:
SIGZY TRADEIFY A+ MTF SNIPER

SCORE ENGINE
------------------------------------------------------------
15M MASTER       +30
5M CONFIRM       +25
EMA ALIGNMENT    +12
PRICE FLOW       +10
REJECTION        +12
CANDLE QUALITY    +6
RSI CONTEXT       +5
S/R LOCATION     +10
PULLBACK          +8
ROOM              +5

PENALTIES
------------------------------------------------------------
CALL near resistance  -15
PUT  near support     -15
CALL overextended     -20
PUT  overextended     -20
HTF conflict          -25

FILTER
------------------------------------------------------------
MIN SCORE = 82
MIN GAP   = 12

CONFIRMED:
15M + 5M + EMA + ROOM + NOT EXTENDED
+
bull/bear candle
+
rejection
+
pullback
+
strict flow

MONEY MANAGEMENT
------------------------------------------------------------
STEP 1 = 100
STEP 2 = 200
STEP 3 = 300

WIN:
    reset STEP 1

LOSS:
    STEP 1 -> STEP 2
    STEP 2 -> STEP 3
    STEP 3 -> SET LOSS -> STEP 1

RUN ALL DAY
NO DAILY STOP

IMPORTANT:
This system does NOT guarantee win rate.
"""

import os
import json
import time
import urllib.request
from datetime import datetime, timezone, timedelta
from statistics import mean
from threading import Thread, Lock

from flask import Flask, jsonify


# ============================================================
# FLASK
# ============================================================

app = Flask(__name__)

STATE_LOCK = Lock()


@app.route("/")
def home():
    return """
    <html>
    <head>
        <title>TRADEIFY V8</title>
    </head>
    <body>
        <h1>TRADEIFY V8 A+ MTF SNIPER</h1>
        <p>15M MASTER / 5M CONFIRM / 1M ENTRY</p>
        <p>RUN ALL DAY</p>
    </body>
    </html>
    """


@app.route("/health")
def health():

    return jsonify({
        "status": "running",
        "bot": "TRADEIFY V8",
        "mode": "RUN ALL DAY",
        "master": "15M",
        "confirm": "5M",
        "entry": "1M",
        "min_score": MIN_SCORE,
        "min_gap": MIN_GAP,
        "step": CURRENT_STEP,
        "set_number": SET_NUMBER,
        "set_active": SET_ACTIVE,
        "pending": len(PENDING_TRADES),
        "signals": DAILY["signals"],
        "wins": DAILY["wins"],
        "losses": DAILY["losses"],
        "void": DAILY["void"]
    })


# ============================================================
# CONFIG
# ============================================================

DISCORD_WEBHOOK_URL = os.environ.get(
    "DISCORD_WEBHOOK_URL",
    ""
).strip()

SCAN_SECONDS = 10

EXPIRY_SECONDS = 300

ORDER_COOLDOWN_SECONDS = 3600

MIN_1M_CANDLES = 250

MIN_SCORE = 82
MIN_GAP = 12

EMA_FAST_LEN = 9
EMA_SLOW_LEN = 21
EMA_TREND_LEN = 50

FLOW_BARS = 3

RSI_PERIOD = 14

RSI_CALL_MAX = 48
RSI_PUT_MIN = 52

RSI_EXTREME_LOW = 30
RSI_EXTREME_HIGH = 70

BB_PERIOD = 20
BB_DEV = 2.0

SR_PERIOD = 100
SR_ZONE = 0.18

STRICT_MODE = True

STAKE_BY_STEP = {
    1: 100,
    2: 200,
    3: 300
}

MAX_STEP = 3

STATE_FILE = os.environ.get(
    "TRADEIFY_STATE_FILE",
    "tradeify_v8_state.json"
)

THAI_TZ = timezone(
    timedelta(hours=7)
)

SYMBOLS = [
    "EUR/USD",
    "GBP/USD",
    "USD/JPY",
    "EUR/JPY",
    "AUD/USD",
    "USD/CHF"
]


# ============================================================
# GLOBAL STATE
# ============================================================

CURRENT_DAY = None

CURRENT_STEP = 1

SET_ACTIVE = False

SET_NUMBER = 0

LAST_GLOBAL_SIGNAL_TIME = 0

LAST_CANDLE = {}

LAST_EARLY = {}

LAST_CONFIRMED = {}

PENDING_TRADES = {}

LOCKED_SYMBOLS = {}

DAILY = {
    "signals": 0,
    "wins": 0,
    "losses": 0,
    "void": 0,
    "set_wins": 0,
    "set_losses": 0
}


# ============================================================
# TIME
# ============================================================

def thai_now():

    return datetime.now(
        timezone.utc
    ).astimezone(
        THAI_TZ
    )


def unix_now():

    return int(
        time.time()
    )


# ============================================================
# STATE SAVE
# ============================================================

def save_state():

    try:

        with STATE_LOCK:

            state = {
                "current_day": CURRENT_DAY,
                "current_step": CURRENT_STEP,
                "set_active": SET_ACTIVE,
                "set_number": SET_NUMBER,
                "last_global_signal_time":
                    LAST_GLOBAL_SIGNAL_TIME,
                "last_candle":
                    LAST_CANDLE,
                "last_early":
                    LAST_EARLY,
                "last_confirmed":
                    LAST_CONFIRMED,
                "locked_symbols":
                    LOCKED_SYMBOLS,
                "pending_trades":
                    PENDING_TRADES,
                "daily":
                    DAILY
            }

            temp = STATE_FILE + ".tmp"

            with open(
                temp,
                "w",
                encoding="utf-8"
            ) as f:

                json.dump(
                    state,
                    f,
                    ensure_ascii=False,
                    indent=2
                )

            os.replace(
                temp,
                STATE_FILE
            )

    except Exception as e:

        print(
            "[STATE SAVE ERROR]",
            repr(e)
        )


# ============================================================
# STATE LOAD
# ============================================================

def load_state():

    global CURRENT_DAY
    global CURRENT_STEP
    global SET_ACTIVE
    global SET_NUMBER
    global LAST_GLOBAL_SIGNAL_TIME
    global LAST_CANDLE
    global LAST_EARLY
    global LAST_CONFIRMED
    global LOCKED_SYMBOLS
    global PENDING_TRADES
    global DAILY

    if not os.path.exists(
        STATE_FILE
    ):

        print(
            "ℹ️ No previous state found"
        )

        return

    try:

        with open(
            STATE_FILE,
            "r",
            encoding="utf-8"
        ) as f:

            state = json.load(f)

        CURRENT_DAY = state.get(
            "current_day"
        )

        CURRENT_STEP = int(
            state.get(
                "current_step",
                1
            )
        )

        SET_ACTIVE = bool(
            state.get(
                "set_active",
                False
            )
        )

        SET_NUMBER = int(
            state.get(
                "set_number",
                0
            )
        )

        LAST_GLOBAL_SIGNAL_TIME = int(
            state.get(
                "last_global_signal_time",
                0
            )
        )

        LAST_CANDLE = state.get(
            "last_candle",
            {}
        )

        LAST_EARLY = state.get(
            "last_early",
            {}
        )

        LAST_CONFIRMED = state.get(
            "last_confirmed",
            {}
        )

        LOCKED_SYMBOLS = state.get(
            "locked_symbols",
            {}
        )

        PENDING_TRADES = state.get(
            "pending_trades",
            {}
        )

        saved_daily = state.get(
            "daily",
            {}
        )

        for key in DAILY:

            DAILY[key] = int(
                saved_daily.get(
                    key,
                    0
                )
            )

        print(
            "💾 Previous state loaded"
        )

    except Exception as e:

        print(
            "[STATE LOAD ERROR]",
            repr(e)
        )


# ============================================================
# DISCORD
# ============================================================

def send_discord(message):

    if not DISCORD_WEBHOOK_URL:

        print(
            "[DISCORD] webhook not configured"
        )

        return False

    try:

        payload = json.dumps(
            {
                "content": message
            }
        ).encode(
            "utf-8"
        )

        request = urllib.request.Request(

            DISCORD_WEBHOOK_URL,

            data=payload,

            headers={
                "Content-Type":
                    "application/json",
                "User-Agent":
                    "TRADEIFY-V8"
            },

            method="POST"
        )

        with urllib.request.urlopen(
            request,
            timeout=10
        ) as response:

            return response.status in (
                200,
                204
            )

    except Exception as e:

        print(
            "[DISCORD ERROR]",
            repr(e)
        )

        return False


# ============================================================
# MARKET DATA
# ============================================================

def fetch_market(symbol):

    try:

        yahoo_symbol = (
            symbol.replace(
                "/",
                ""
            )
            + "=X"
        )

        url = (
            "https://query1.finance.yahoo.com/"
            "v8/finance/chart/"
            f"{yahoo_symbol}"
            "?interval=1m&range=5d"
        )

        request = urllib.request.Request(

            url,

            headers={
                "User-Agent":
                    "Mozilla/5.0"
            }
        )

        with urllib.request.urlopen(
            request,
            timeout=15
        ) as response:

            raw = response.read()

        data = json.loads(
            raw.decode(
                "utf-8"
            )
        )

        result = (
            data
            .get("chart", {})
            .get("result")
        )

        if not result:

            return []

        result = result[0]

        timestamps = result.get(
            "timestamp",
            []
        )

        quote = (
            result
            .get("indicators", {})
            .get("quote", [{}])[0]
        )

        opens = quote.get(
            "open",
            []
        )

        highs = quote.get(
            "high",
            []
        )

        lows = quote.get(
            "low",
            []
        )

        closes = quote.get(
            "close",
            []
        )

        candles = []

        for i, ts in enumerate(
            timestamps
        ):

            try:

                if ts is None:
                    continue

                op = opens[i]
                hi = highs[i]
                lo = lows[i]
                cl = closes[i]

                if any(
                    x is None
                    for x in (
                        op,
                        hi,
                        lo,
                        cl
                    )
                ):
                    continue

                candles.append({
                    "timestamp": int(ts),
                    "open": float(op),
                    "high": float(hi),
                    "low": float(lo),
                    "close": float(cl)
                })

            except Exception:

                continue

        candles.sort(
            key=lambda x:
                x["timestamp"]
        )

        # ----------------------------------------------------
        # REMOVE CURRENT OPEN 1M CANDLE
        # ----------------------------------------------------

        current_minute = (
            int(time.time())
            // 60
        ) * 60

        candles = [
            x
            for x in candles
            if x["timestamp"]
            < current_minute
        ]

        return candles

    except Exception as e:

        print(
            f"[MARKET ERROR] "
            f"{symbol}: {e}"
        )

        return []


# ============================================================
# RESAMPLE
# ============================================================

def resample(
    candles,
    minutes
):

    if not candles:

        return []

    size = (
        minutes * 60
    )

    buckets = {}

    for candle in candles:

        bucket_id = (
            candle["timestamp"]
            // size
        )

        buckets.setdefault(
            bucket_id,
            []
        ).append(
            candle
        )

    result = []

    for bucket_id in sorted(
        buckets
    ):

        group = buckets[
            bucket_id
        ]

        group.sort(
            key=lambda x:
                x["timestamp"]
        )

        if len(group) < minutes:

            continue

        valid = True

        for i in range(
            1,
            len(group)
        ):

            if (
                group[i]["timestamp"]
                -
                group[i - 1]["timestamp"]
                != 60
            ):

                valid = False
                break

        if not valid:

            continue

        group = group[
            -minutes:
        ]

        result.append({
            "timestamp":
                group[-1]["timestamp"],

            "open":
                group[0]["open"],

            "high":
                max(
                    x["high"]
                    for x in group
                ),

            "low":
                min(
                    x["low"]
                    for x in group
                ),

            "close":
                group[-1]["close"]
        })

    return result


# ============================================================
# EMA SERIES
# ============================================================

def ema_series(
    values,
    period
):

    if len(values) < period:

        return []

    seed = mean(
        values[:period]
    )

    multiplier = (
        2 /
        (period + 1)
    )

    result = [
        seed
    ]

    previous = seed

    for value in values[
        period:
    ]:

        previous = (
            value * multiplier
            +
            previous *
            (1 - multiplier)
        )

        result.append(
            previous
        )

    return result


def latest_ema(
    values,
    period
):

    series = ema_series(
        values,
        period
    )

    if not series:

        return None

    return series[-1]


def previous_ema(
    values,
    period
):

    series = ema_series(
        values,
        period
    )

    if len(series) < 2:

        return None

    return series[-2]


# ============================================================
# RSI
# ============================================================

def calculate_rsi(
    candles,
    period=14
):

    if len(candles) < (
        period + 1
    ):

        return None

    closes = [
        x["close"]
        for x in candles
    ]

    gains = []
    losses = []

    for i in range(
        1,
        len(closes)
    ):

        change = (
            closes[i]
            -
            closes[i - 1]
        )

        gains.append(
            max(change, 0)
        )

        losses.append(
            max(-change, 0)
        )

    if len(gains) < period:

        return None

    avg_gain = mean(
        gains[-period:]
    )

    avg_loss = mean(
        losses[-period:]
    )

    if avg_loss == 0:

        return 100.0

    rs = (
        avg_gain /
        avg_loss
    )

    return (
        100
        -
        (
            100 /
            (1 + rs)
        )
    )


# ============================================================
# CANDLE ANATOMY
# ============================================================

def candle_features(
    candle
):

    body = abs(
        candle["close"]
        -
        candle["open"]
    )

    candle_range = max(
        candle["high"]
        -
        candle["low"],
        1e-12
    )

    upper_wick = (
        candle["high"]
        -
        max(
            candle["open"],
            candle["close"]
        )
    )

    lower_wick = (
        min(
            candle["open"],
            candle["close"]
        )
        -
        candle["low"]
    )

    body_ratio = (
        body /
        candle_range
    )

    upper_ratio = (
        upper_wick /
        candle_range
    )

    lower_ratio = (
        lower_wick /
        candle_range
    )

    bull = (
        candle["close"]
        >
        candle["open"]
    )

    bear = (
        candle["close"]
        <
        candle["open"]
    )

    bull_rejection = (
        lower_ratio >= 0.25
        and
        candle["close"]
        >=
        candle["open"]
    )

    bear_rejection = (
        upper_ratio >= 0.25
        and
        candle["close"]
        <=
        candle["open"]
    )

    strong_bull = (
        bull
        and
        body_ratio >= 0.45
    )

    strong_bear = (
        bear
        and
        body_ratio >= 0.45
    )

    return {
        "body": body,
        "range": candle_range,
        "upper_ratio": upper_ratio,
        "lower_ratio": lower_ratio,
        "body_ratio": body_ratio,
        "bull": bull,
        "bear": bear,
        "bull_rejection":
            bull_rejection,
        "bear_rejection":
            bear_rejection,
        "strong_bull":
            strong_bull,
        "strong_bear":
            strong_bear
    }


# ============================================================
# 15M / 5M STRUCTURE
#
# EXACT LOGIC FROM INDICATOR:
#
# current closed HTF candle = [-1]
# previous closed HTF candle = [-2]
# ============================================================

def timeframe_structure(
    candles
):

    if len(candles) < 2:

        return {
            "bull": False,
            "bear": False,
            "higher_high": False,
            "higher_low": False,
            "lower_high": False,
            "lower_low": False
        }

    current = candles[-1]
    previous = candles[-2]

    bull = (
        current["close"]
        >
        current["open"]
    )

    bear = (
        current["close"]
        <
        current["open"]
    )

    higher_high = (
        current["high"]
        >
        previous["high"]
    )

    higher_low = (
        current["low"]
        >
        previous["low"]
    )

    lower_high = (
        current["high"]
        <
        previous["high"]
    )

    lower_low = (
        current["low"]
        <
        previous["low"]
    )

    return {
        "bull": bull,
        "bear": bear,
        "higher_high":
            higher_high,
        "higher_low":
            higher_low,
        "lower_high":
            lower_high,
        "lower_low":
            lower_low
    }


# ============================================================
# SUPPORT / RESISTANCE
#
# SAME LOGIC AS INDICATOR:
#
# lowest(low, 100)
# highest(high, 100)
# ============================================================

def calculate_sr(
    candles
):

    if len(candles) < SR_PERIOD:

        return None

    data = candles[
        -SR_PERIOD:
    ]

    support = min(
        x["low"]
        for x in data
    )

    resistance = max(
        x["high"]
        for x in data
    )

    current = candles[
        -1
    ]["close"]

    sr_range = max(
        resistance - support,
        1e-12
    )

    near_support = (
        current
        <=
        support
        +
        sr_range * SR_ZONE
    )

    near_resistance = (
        current
        >=
        resistance
        -
        sr_range * SR_ZONE
    )

    room_call = (
        resistance - current
    ) / sr_range

    room_put = (
        current - support
    ) / sr_range

    enough_room_call = (
        room_call >= 0.20
    )

    enough_room_put = (
        room_put >= 0.20
    )

    if near_support:

        zone = "SUPPORT"

    elif near_resistance:

        zone = "RESISTANCE"

    else:

        zone = "MID"

    return {
        "support":
            support,

        "resistance":
            resistance,

        "range":
            sr_range,

        "near_support":
            near_support,

        "near_resistance":
            near_resistance,

        "room_call":
            room_call,

        "room_put":
            room_put,

        "enough_room_call":
            enough_room_call,

        "enough_room_put":
            enough_room_put,

        "zone":
            zone
    }


# ============================================================
# ANALYZE A+ MTF
# ============================================================

def analyze(
    symbol,
    candles_1m
):

    if len(candles_1m) < MIN_1M_CANDLES:

        return None

    # ========================================================
    # RESAMPLE
    # ========================================================

    candles_5m = resample(
        candles_1m,
        5
    )

    candles_15m = resample(
        candles_1m,
        15
    )

    if len(candles_5m) < 70:

        return None

    if len(candles_15m) < 20:

        return None

    # ========================================================
    # LOCAL 1M
    # ========================================================

    close_values = [
        x["close"]
        for x in candles_1m
    ]

    ema_fast = latest_ema(
        close_values,
        EMA_FAST_LEN
    )

    ema_slow = latest_ema(
        close_values,
        EMA_SLOW_LEN
    )

    ema_trend = latest_ema(
        close_values,
        EMA_TREND_LEN
    )

    ema_fast_prev = previous_ema(
        close_values,
        EMA_FAST_LEN
    )

    if any(
        x is None
        for x in (
            ema_fast,
            ema_slow,
            ema_trend,
            ema_fast_prev
        )
    ):

        return None

    # ========================================================
    # RSI
    # ========================================================

    rsi_value = calculate_rsi(
        candles_1m,
        RSI_PERIOD
    )

    if rsi_value is None:

        return None

    # ========================================================
    # BB
    # ========================================================

    if len(close_values) < BB_PERIOD:

        return None

    bb_data = close_values[
        -BB_PERIOD:
    ]

    bb_mid = mean(
        bb_data
    )

    variance = mean(
        (
            x - bb_mid
        ) ** 2
        for x in bb_data
    )

    bb_std = (
        variance ** 0.5
    )

    bb_upper = (
        bb_mid
        +
        BB_DEV * bb_std
    )

    bb_lower = (
        bb_mid
        -
        BB_DEV * bb_std
    )

    # ========================================================
    # 15M STRUCTURE
    # ========================================================

    s15 = timeframe_structure(
        candles_15m
    )

    trend15_call = (
        s15["bull"]
        and
        (
            s15["higher_high"]
            or
            s15["higher_low"]
        )
        and
        candles_15m[-1]["close"]
        >
        candles_15m[-2]["close"]
    )

    trend15_put = (
        s15["bear"]
        and
        (
            s15["lower_high"]
            or
            s15["lower_low"]
        )
        and
        candles_15m[-1]["close"]
        <
        candles_15m[-2]["close"]
    )

    # ========================================================
    # 5M STRUCTURE
    # ========================================================

    s5 = timeframe_structure(
        candles_5m
    )

    trend5_call = (
        s5["bull"]
        and
        (
            s5["higher_high"]
            or
            s5["higher_low"]
        )
        and
        candles_5m[-1]["close"]
        >
        candles_5m[-2]["close"]
    )

    trend5_put = (
        s5["bear"]
        and
        (
            s5["lower_high"]
            or
            s5["lower_low"]
        )
        and
        candles_5m[-1]["close"]
        <
        candles_5m[-2]["close"]
    )

    # ========================================================
    # 1M CANDLE
    # ========================================================

    current = candles_1m[-1]

    f = candle_features(
        current
    )

    bull = f["bull"]
    bear = f["bear"]

    bull_rejection = (
        f["bull_rejection"]
    )

    bear_rejection = (
        f["bear_rejection"]
    )

    strong_bull = (
        f["strong_bull"]
    )

    strong_bear = (
        f["strong_bear"]
    )

    # ========================================================
    # FLOW
    # ========================================================

    if len(candles_1m) < 4:

        return None

    flow_up = (
        candles_1m[-1]["close"]
        >
        candles_1m[-2]["close"]
        >
        candles_1m[-3]["close"]
        >
        candles_1m[-4]["close"]
    )

    flow_down = (
        candles_1m[-1]["close"]
        <
        candles_1m[-2]["close"]
        <
        candles_1m[-3]["close"]
        <
        candles_1m[-4]["close"]
    )

    # ========================================================
    # EMA ALIGNMENT
    # ========================================================

    ema_up = (
        ema_fast
        >
        ema_slow
        >
        ema_trend
        and
        ema_fast
        >
        ema_fast_prev
    )

    ema_down = (
        ema_fast
        <
        ema_slow
        <
        ema_trend
        and
        ema_fast
        <
        ema_fast_prev
    )

    # ========================================================
    # S/R
    # ========================================================

    sr = calculate_sr(
        candles_1m
    )

    if sr is None:

        return None

    near_support = sr[
        "near_support"
    ]

    near_resistance = sr[
        "near_resistance"
    ]

    enough_room_call = sr[
        "enough_room_call"
    ]

    enough_room_put = sr[
        "enough_room_put"
    ]

    # ========================================================
    # OVEREXTENDED
    # ========================================================

    overextended_call = (
        rsi_value >=
        RSI_EXTREME_HIGH
        or
        (
            bull
            and
            f["body_ratio"] >= 0.78
            and
            f["upper_ratio"] <= 0.08
        )
    )

    overextended_put = (
        rsi_value <=
        RSI_EXTREME_LOW
        or
        (
            bear
            and
            f["body_ratio"] >= 0.78
            and
            f["lower_ratio"] <= 0.08
        )
    )

    # ========================================================
    # PULLBACK
    # ========================================================

    pullback_call = (
        (
            current["low"]
            <=
            ema_fast
        )
        or
        (
            current["low"]
            <=
            bb_mid
        )
        or
        near_support
    ) and (
        current["close"]
        >
        ema_fast
    )

    pullback_put = (
        (
            current["high"]
            >=
            ema_fast
        )
        or
        (
            current["high"]
            >=
            bb_mid
        )
        or
        near_resistance
    ) and (
        current["close"]
        <
        ema_fast
    )

    # ========================================================
    # SCORE
    # ========================================================

    call_score = 0
    put_score = 0

    call_reasons = []
    put_reasons = []

    # --------------------------------------------------------
    # 15M MASTER = 30
    # --------------------------------------------------------

    if trend15_call:

        call_score += 30

        call_reasons.append(
            "15M MASTER CALL +30"
        )

    if trend15_put:

        put_score += 30

        put_reasons.append(
            "15M MASTER PUT +30"
        )

    # --------------------------------------------------------
    # 5M CONFIRM = 25
    # --------------------------------------------------------

    if trend5_call:

        call_score += 25

        call_reasons.append(
            "5M CONFIRM CALL +25"
        )

    if trend5_put:

        put_score += 25

        put_reasons.append(
            "5M CONFIRM PUT +25"
        )

    # --------------------------------------------------------
    # EMA = 12
    # --------------------------------------------------------

    if ema_up:

        call_score += 12

        call_reasons.append(
            "EMA ALIGNMENT CALL +12"
        )

    if ema_down:

        put_score += 12

        put_reasons.append(
            "EMA ALIGNMENT PUT +12"
        )

    # --------------------------------------------------------
    # FLOW = 10
    # --------------------------------------------------------

    if flow_up:

        call_score += 10

        call_reasons.append(
            "PRICE FLOW UP +10"
        )

    if flow_down:

        put_score += 10

        put_reasons.append(
            "PRICE FLOW DOWN +10"
        )

    # --------------------------------------------------------
    # REJECTION = 12
    # --------------------------------------------------------

    if bull_rejection:

        call_score += 12

        call_reasons.append(
            "BULL REJECTION +12"
        )

    if bear_rejection:

        put_score += 12

        put_reasons.append(
            "BEAR REJECTION +12"
        )

    # --------------------------------------------------------
    # CANDLE = 6
    # --------------------------------------------------------

    if strong_bull:

        call_score += 6

        call_reasons.append(
            "STRONG BULL +6"
        )

    if strong_bear:

        put_score += 6

        put_reasons.append(
            "STRONG BEAR +6"
        )

    # --------------------------------------------------------
    # RSI = 5
    # --------------------------------------------------------

    if (
        rsi_value <= RSI_CALL_MAX
        and
        rsi_value > RSI_EXTREME_LOW
    ):

        call_score += 5

        call_reasons.append(
            "RSI CALL ZONE +5"
        )

    if (
        rsi_value >= RSI_PUT_MIN
        and
        rsi_value < RSI_EXTREME_HIGH
    ):

        put_score += 5

        put_reasons.append(
            "RSI PUT ZONE +5"
        )

    # --------------------------------------------------------
    # S/R = 10
    # --------------------------------------------------------

    if near_support:

        call_score += 10

        call_reasons.append(
            "NEAR SUPPORT +10"
        )

    if near_resistance:

        put_score += 10

        put_reasons.append(
            "NEAR RESISTANCE +10"
        )

    # --------------------------------------------------------
    # PULLBACK = 8
    # --------------------------------------------------------

    if pullback_call:

        call_score += 8

        call_reasons.append(
            "CALL PULLBACK +8"
        )

    if pullback_put:

        put_score += 8

        put_reasons.append(
            "PUT PULLBACK +8"
        )

    # --------------------------------------------------------
    # ROOM = 5
    # --------------------------------------------------------

    if enough_room_call:

        call_score += 5

        call_reasons.append(
            "CALL ROOM +5"
        )

    if enough_room_put:

        put_score += 5

        put_reasons.append(
            "PUT ROOM +5"
        )

    # ========================================================
    # PENALTIES
    # ========================================================

    if near_resistance:

        call_score -= 15

        call_reasons.append(
            "CALL RESISTANCE -15"
        )

    if near_support:

        put_score -= 15

        put_reasons.append(
            "PUT SUPPORT -15"
        )

    if overextended_call:

        call_score -= 20

        call_reasons.append(
            "CALL EXTENDED -20"
        )

    if overextended_put:

        put_score -= 20

        put_reasons.append(
            "PUT EXTENDED -20"
        )

    # --------------------------------------------------------
    # 15M / 5M CONFLICT
    # --------------------------------------------------------

    if (
        trend15_call
        and
        trend5_put
    ):

        call_score -= 25
        put_score -= 25

        call_reasons.append(
            "HTF CONFLICT -25"
        )

        put_reasons.append(
            "HTF CONFLICT -25"
        )

    if (
        trend15_put
        and
        trend5_call
    ):

        call_score -= 25
        put_score -= 25

        call_reasons.append(
            "HTF CONFLICT -25"
        )

        put_reasons.append(
            "HTF CONFLICT -25"
        )

    # ========================================================
    # NORMALIZE
    # ========================================================

    call_score = max(
        0,
        min(
            int(call_score),
            100
        )
    )

    put_score = max(
        0,
        min(
            int(put_score),
            100
        )
    )

    # ========================================================
    # DIRECTION
    # ========================================================

    if call_score > put_score:

        direction = "CALL"

        score = call_score
        opposite = put_score

    elif put_score > call_score:

        direction = "PUT"

        score = put_score
        opposite = call_score

    else:

        return None

    gap = (
        score
        -
        opposite
    )

    # ========================================================
    # MAJOR CONDITIONS
    # ========================================================

    major_call = (
        trend15_call
        and
        trend5_call
        and
        ema_up
        and
        enough_room_call
        and
        not overextended_call
    )

    major_put = (
        trend15_put
        and
        trend5_put
        and
        ema_down
        and
        enough_room_put
        and
        not overextended_put
    )

    # ========================================================
    # EARLY
    # ========================================================

    early_call = (
        major_call
        and
        call_score >= (
            MIN_SCORE - 8
        )
        and
        (
            call_score
            -
            put_score
        )
        >= MIN_GAP
        and
        (
            pullback_call
            or
            bull_rejection
            or
            rsi_value <=
            RSI_CALL_MAX
        )
    )

    early_put = (
        major_put
        and
        put_score >= (
            MIN_SCORE - 8
        )
        and
        (
            put_score
            -
            call_score
        )
        >= MIN_GAP
        and
        (
            pullback_put
            or
            bear_rejection
            or
            rsi_value >=
            RSI_PUT_MIN
        )
    )

    # ========================================================
    # CONFIRMED
    # ========================================================

    call_signal = (
        major_call
        and
        call_score >= MIN_SCORE
        and
        (
            call_score
            -
            put_score
        ) >= MIN_GAP
        and
        bull
        and
        bull_rejection
        and
        pullback_call
    )

    put_signal = (
        major_put
        and
        put_score >= MIN_SCORE
        and
        (
            put_score
            -
            call_score
        ) >= MIN_GAP
        and
        bear
        and
        bear_rejection
        and
        pullback_put
    )

    # ========================================================
    # STRICT MODE
    # ========================================================

    if STRICT_MODE:

        call_signal = (
            call_signal
            and
            flow_up
        )

        put_signal = (
            put_signal
            and
            flow_down
        )

    # ========================================================
    # FINAL SIGNAL
    # ========================================================

    confirmed = (
        call_signal
        if direction == "CALL"
        else
        put_signal
    )

    early = (
        early_call
        if direction == "CALL"
        else
        early_put
    )

    # ========================================================
    # SIGNAL CANDLE
    # ========================================================

    signal_timestamp = (
        candles_1m[-1][
            "timestamp"
        ]
    )

    entry_price = float(
        candles_1m[-1][
            "close"
        ]
    )

    return {

        "symbol":
            symbol,

        "direction":
            direction,

        "score":
            score,

        "call_score":
            call_score,

        "put_score":
            put_score,

        "gap":
            gap,

        "early":
            bool(early),

        "confirmed":
            bool(confirmed),

        "timestamp":
            signal_timestamp,

        "entry":
            entry_price,

        "rsi":
            rsi_value,

        "zone":
            sr["zone"],

        "support":
            sr["support"],

        "resistance":
            sr["resistance"],

        "bb_mid":
            bb_mid,

        "bb_upper":
            bb_upper,

        "bb_lower":
            bb_lower,

        "trend15_call":
            trend15_call,

        "trend15_put":
            trend15_put,

        "trend5_call":
            trend5_call,

        "trend5_put":
            trend5_put,

        "ema_up":
            ema_up,

        "ema_down":
            ema_down,

        "flow_up":
            flow_up,

        "flow_down":
            flow_down,

        "pullback_call":
            pullback_call,

        "pullback_put":
            pullback_put,

        "bull_rejection":
            bull_rejection,

        "bear_rejection":
            bear_rejection,

        "overextended_call":
            overextended_call,

        "overextended_put":
            overextended_put,

        "reasons":
            (
                call_reasons
                if direction == "CALL"
                else
                put_reasons
            )
    }


# ============================================================
# GLOBAL COOLDOWN
# ============================================================

def global_order_available():

    if LAST_GLOBAL_SIGNAL_TIME <= 0:

        return True

    return (
        unix_now()
        -
        LAST_GLOBAL_SIGNAL_TIME
        >=
        ORDER_COOLDOWN_SECONDS
    )


def cooldown_remaining():

    if LAST_GLOBAL_SIGNAL_TIME <= 0:

        return 0

    return max(
        0,
        ORDER_COOLDOWN_SECONDS
        -
        (
            unix_now()
            -
            LAST_GLOBAL_SIGNAL_TIME
        )
    )


# ============================================================
# DAILY
#
# RUN ALL DAY
# ไม่มี DAILY STOP
# ============================================================

def reset_daily():

    global CURRENT_DAY
    global CURRENT_STEP
    global SET_ACTIVE
    global SET_NUMBER
    global LAST_GLOBAL_SIGNAL_TIME

    today = thai_now().strftime(
        "%Y-%m-%d"
    )

    if CURRENT_DAY == today:

        return

    CURRENT_DAY = today

    CURRENT_STEP = 1

    SET_ACTIVE = False

    SET_NUMBER = 0

    LAST_GLOBAL_SIGNAL_TIME = 0

    PENDING_TRADES.clear()

    LOCKED_SYMBOLS.clear()

    LAST_CANDLE.clear()

    LAST_EARLY.clear()

    LAST_CONFIRMED.clear()

    for key in DAILY:

        DAILY[key] = 0

    save_state()

    send_discord(
        "🌅 **TRADEIFY V8 NEW DAY**\n"
        f"📅 `{today}`\n"
        "🟢 MODE: **RUN ALL DAY**\n"
        "🛑 DAILY STOP: **NONE**\n"
        "🎯 MIN SCORE: `82`\n"
        "⚡ MIN GAP: `12`\n"
        "💰 STEP: `100 / 200 / 300`"
    )


# ============================================================
# CREATE TRADE
# ============================================================

def create_trade(
    analysis
):

    global CURRENT_STEP
    global SET_ACTIVE
    global SET_NUMBER
    global LAST_GLOBAL_SIGNAL_TIME

    symbol = analysis[
        "symbol"
    ]

    direction = analysis[
        "direction"
    ]

    timestamp = analysis[
        "timestamp"
    ]

    # --------------------------------------------------------
    # One active trade only
    # --------------------------------------------------------

    if PENDING_TRADES:

        return False

    # --------------------------------------------------------
    # Global cooldown
    # --------------------------------------------------------

    if not global_order_available():

        return False

    # --------------------------------------------------------
    # Symbol lock
    # --------------------------------------------------------

    if symbol in LOCKED_SYMBOLS:

        return False

    # --------------------------------------------------------
    # Start new SET
    # --------------------------------------------------------

    if not SET_ACTIVE:

        SET_ACTIVE = True

        SET_NUMBER += 1

        CURRENT_STEP = 1

    step = CURRENT_STEP

    stake = STAKE_BY_STEP[
        step
    ]

    entry = float(
        analysis["entry"]
    )

    expiry = (
        timestamp
        +
        EXPIRY_SECONDS
    )

    trade_key = (
        f"{symbol}|"
        f"{timestamp}|"
        f"{direction}|"
        f"STEP{step}"
    )

    # --------------------------------------------------------
    # Store
    # --------------------------------------------------------

    PENDING_TRADES[
        trade_key
    ] = {

        "symbol":
            symbol,

        "direction":
            direction,

        "entry":
            entry,

        "signal_timestamp":
            timestamp,

        "expiry":
            expiry,

        "step":
            step,

        "stake":
            stake,

        "set_number":
            SET_NUMBER,

        "score":
            analysis["score"],

        "gap":
            analysis["gap"],

        "created_at":
            unix_now()
    }

    LOCKED_SYMBOLS[
        symbol
    ] = {
        "timestamp":
            timestamp,

        "direction":
            direction
    }

    LAST_GLOBAL_SIGNAL_TIME = (
        unix_now()
    )

    LAST_CONFIRMED[
        symbol
    ] = (
        timestamp,
        direction
    )

    DAILY["signals"] += 1

    save_state()

    icon = (
        "🟢"
        if direction == "CALL"
        else
        "🔴"
    )

    reasons = "\n".join(
        "• " + x
        for x in analysis[
            "reasons"
        ][-6:]
    )

    send_discord(

        f"{icon} **TRADEIFY V8 A+ CONFIRMED**\n\n"

        f"📌 `{symbol}`\n"

        f"➡️ **{direction}**\n"

        f"📊 Score: "
        f"`{analysis['score']}/100`\n"

        f"⚡ Gap: "
        f"`+{analysis['gap']}`\n"

        f"🕐 15M MASTER: "
        f"`{'CALL' if analysis['trend15_call'] else 'PUT' if analysis['trend15_put'] else 'NONE'}`\n"

        f"🕔 5M CONFIRM: "
        f"`{'CALL' if analysis['trend5_call'] else 'PUT' if analysis['trend5_put'] else 'NONE'}`\n"

        f"📍 Zone: "
        f"`{analysis['zone']}`\n"

        f"📈 RSI: "
        f"`{analysis['rsi']:.2f}`\n"

        f"💰 Entry: "
        f"`{entry}`\n"

        f"🎯 SET: "
        f"`#{SET_NUMBER}`\n"

        f"🔥 STEP: "
        f"`{step}/3`\n"

        f"💵 Stake: "
        f"`{stake}` บาท\n"

        f"⏱ Expiry: "
        f"`5 MIN`\n\n"

        f"**A+ CONDITIONS**\n"
        f"{reasons}\n\n"

        f"🔒 Global Lock: "
        f"`60 MIN`"
    )

    return True


# ============================================================
# CHECK RESULT
# ============================================================

def check_pending_trades():

    global CURRENT_STEP
    global SET_ACTIVE

    if not PENDING_TRADES:

        return

    now = unix_now()

    completed = []

    for trade_key, trade in list(
        PENDING_TRADES.items()
    ):

        expiry = int(
            trade["expiry"]
        )

        if now < expiry:

            continue

        symbol = trade[
            "symbol"
        ]

        direction = trade[
            "direction"
        ]

        entry = float(
            trade["entry"]
        )

        step = int(
            trade["step"]
        )

        stake = int(
            trade["stake"]
        )

        set_number = int(
            trade["set_number"]
        )

        candles = fetch_market(
            symbol
        )

        if not candles:

            continue

        # ----------------------------------------------------
        # RESULT = FIRST CLOSED 1M CANDLE AT OR AFTER EXPIRY
        # ----------------------------------------------------

        result_candle = None

        for candle in candles:

            if (
                candle["timestamp"]
                >= expiry
            ):

                result_candle = candle

                break

        if result_candle is None:

            continue

        exit_price = float(
            result_candle[
                "close"
            ]
        )

        # ----------------------------------------------------
        # RESULT
        # ----------------------------------------------------

        if direction == "CALL":

            if exit_price > entry:

                result = "WIN"

            elif exit_price < entry:

                result = "LOSS"

            else:

                result = "VOID"

        else:

            if exit_price < entry:

                result = "WIN"

            elif exit_price > entry:

                result = "LOSS"

            else:

                result = "VOID"

        # ====================================================
        # WIN
        # ====================================================

        if result == "WIN":

            DAILY["wins"] += 1
            DAILY["set_wins"] += 1

            SET_ACTIVE = False

            CURRENT_STEP = 1

            send_discord(

                "✅ **TRADEIFY RESULT — WIN**\n\n"
                f"📌 `{symbol}`\n"
                f"➡️ `{direction}`\n"
                f"💰 Entry: `{entry}`\n"
                f"🏁 Exit: `{exit_price}`\n"
                f"🎯 Step: `{step}/3`\n"
                f"💵 Stake: `{stake}` บาท\n"
                f"🏆 SET #{set_number} **WIN**\n"
                "🔄 Next opportunity → STEP 1\n"
                "🟢 MODE: RUN ALL DAY"
            )

        # ====================================================
        # LOSS
        # ====================================================

        elif result == "LOSS":

            DAILY["losses"] += 1

            if step < MAX_STEP:

                CURRENT_STEP = (
                    step + 1
                )

                SET_ACTIVE = True

                send_discord(

                    "❌ **TRADEIFY RESULT — LOSS**\n\n"
                    f"📌 `{symbol}`\n"
                    f"➡️ `{direction}`\n"
                    f"💰 Entry: `{entry}`\n"
                    f"🏁 Exit: `{exit_price}`\n"
                    f"🎯 Step: `{step}/3`\n\n"
                    f"🔁 NEXT STEP: "
                    f"**{CURRENT_STEP}/3**\n"
                    f"💵 Stake: "
                    f"`{STAKE_BY_STEP[CURRENT_STEP]}` บาท\n\n"
                    "⚠️ รอสัญญาณ A+ ใหม่\n"
                    "ไม่บังคับทิศทางเดิม"
                )

            else:

                DAILY["set_losses"] += 1

                SET_ACTIVE = False

                CURRENT_STEP = 1

                send_discord(

                    "💀 **TRADEIFY SET LOSS**\n\n"
                    f"📌 `{symbol}`\n"
                    f"➡️ `{direction}`\n"
                    f"🎯 STEP 3/3\n"
                    f"💵 Stake: `{stake}` บาท\n"
                    f"🏁 Exit: `{exit_price}`\n\n"
                    f"❌ SET #{set_number} LOSS\n"
                    "🔄 New SET → STEP 1\n"
                    "🟢 RUN ALL DAY"
                )

        # ====================================================
        # VOID
        # ====================================================

        else:

            DAILY["void"] += 1

            SET_ACTIVE = False

            CURRENT_STEP = 1

            send_discord(

                "⚪ **TRADEIFY RESULT — VOID**\n\n"
                f"📌 `{symbol}`\n"
                f"➡️ `{direction}`\n"
                f"💰 Entry: `{entry}`\n"
                f"🏁 Exit: `{exit_price}`\n"
                "🔄 Reset → STEP 1"
            )

        LOCKED_SYMBOLS.pop(
            symbol,
            None
        )

        completed.append(
            trade_key
        )

        save_state()

    for key in completed:

        PENDING_TRADES.pop(
            key,
            None
        )

    if completed:

        save_state()


# ============================================================
# SCAN LOG
# ============================================================

def print_check(
    analysis
):

    print(
        f"[CHECK] "
        f"{analysis['symbol']} "
        f"CALL={analysis['call_score']} "
        f"PUT={analysis['put_score']} "
        f"DIR={analysis['direction']} "
        f"GAP={analysis['gap']} "
        f"SCORE={analysis['score']} "
        f"EARLY={analysis['early']} "
        f"CONFIRMED={analysis['confirmed']}"
    )


# ============================================================
# BOT LOOP
# ============================================================

def bot_loop():

    print(
        "=========================================="
    )

    print(
        "🚀 TRADEIFY V8 A+ MTF SNIPER"
    )

    print(
        "=========================================="
    )

    print(
        "15M = MASTER"
    )

    print(
        "5M  = CONFIRM"
    )

    print(
        "1M  = ENTRY / REJECTION"
    )

    print(
        "MIN SCORE = 82"
    )

    print(
        "MIN GAP   = 12"
    )

    print(
        "EXPIRY    = 5 MIN"
    )

    print(
        "STEP      = 100 / 200 / 300"
    )

    print(
        "MODE      = RUN ALL DAY"
    )

    print(
        "STOP      = NONE"
    )

    print(
        "=========================================="
    )

    load_state()

    print(
        "✅ Bot worker started"
    )

    while True:

        try:

            reset_daily()

            # =================================================
            # RESULT FIRST
            # =================================================

            check_pending_trades()

            # =================================================
            # STATUS
            # =================================================

            print(
                f"🔎 SCAN "
                f"{thai_now().strftime('%H:%M:%S')} "
                f"| pending="
                f"{len(PENDING_TRADES)} "
                f"| step="
                f"{CURRENT_STEP}"
            )

            # =================================================
            # DON'T SCAN WHILE TRADE ACTIVE
            # =================================================

            if PENDING_TRADES:

                time.sleep(
                    SCAN_SECONDS
                )

                continue

            # =================================================
            # GLOBAL LOCK
            # =================================================

            if not global_order_available():

                print(
                    "[LOCK] "
                    f"remaining="
                    f"{cooldown_remaining()}s"
                )

                time.sleep(
                    SCAN_SECONDS
                )

                continue

            # =================================================
            # SCAN SYMBOLS
            # =================================================

            for symbol in SYMBOLS:

                if PENDING_TRADES:

                    break

                candles = fetch_market(
                    symbol
                )

                if len(candles) < (
                    MIN_1M_CANDLES
                ):

                    print(
                        f"[DATA] "
                        f"{symbol} "
                        f"candles="
                        f"{len(candles)}"
                    )

                    continue

                # =================================================
                # CLOSED CANDLE ID
                # =================================================

                latest_timestamp = (
                    candles[-1][
                        "timestamp"
                    ]
                )

                # Don't recalculate same 1M candle
                if (
                    LAST_CANDLE.get(
                        symbol
                    )
                    ==
                    latest_timestamp
                ):

                    continue

                LAST_CANDLE[
                    symbol
                ] = latest_timestamp

                analysis = analyze(
                    symbol,
                    candles
                )

                if not analysis:

                    continue

                print_check(
                    analysis
                )

                # =================================================
                # EARLY WARNING
                # =================================================

                if analysis[
                    "early"
                ]:

                    early_key = (
                        analysis[
                            "timestamp"
                        ],
                        analysis[
                            "direction"
                        ]
                    )

                    if (
                        LAST_EARLY.get(
                            symbol
                        )
                        !=
                        early_key
                    ):

                        LAST_EARLY[
                            symbol
                        ] = early_key

                        icon = (
                            "🟡"
                            if
                            analysis[
                                "direction"
                            ]
                            ==
                            "CALL"
                            else
                            "🟠"
                        )

                        send_discord(

                            f"{icon} "
                            "**A+ EARLY WARNING**\n"
                            f"`{symbol}` → "
                            f"**{analysis['direction']}**\n"
                            f"Score: "
                            f"`{analysis['score']}`\n"
                            f"Gap: "
                            f"`+{analysis['gap']}`\n"
                            f"Zone: "
                            f"`{analysis['zone']}`\n"
                            "⚠️ **ยังไม่ใช่ออเดอร์**"
                        )

                # =================================================
                # CONFIRMED
                # =================================================

                if not analysis[
                    "confirmed"
                ]:

                    continue

                confirmed_key = (
                    analysis[
                        "timestamp"
                    ],
                    analysis[
                        "direction"
                    ]
                )

                if (
                    LAST_CONFIRMED.get(
                        symbol
                    )
                    ==
                    confirmed_key
                ):

                    continue

                created = create_trade(
                    analysis
                )

                if created:

                    print(
                        f"🎯 TRADE CREATED "
                        f"{symbol} "
                        f"{analysis['direction']} "
                        f"score="
                        f"{analysis['score']} "
                        f"gap="
                        f"{analysis['gap']}"
                    )

                    break

            save_state()

            time.sleep(
                SCAN_SECONDS
            )

        except Exception as e:

            print(
                "[BOT LOOP ERROR]",
                repr(e)
            )

            time.sleep(5)


# ============================================================
# STARTUP
# ============================================================

def start_worker():

    worker = Thread(
        target=bot_loop,
        daemon=True,
        name="TRADEIFY-V8-WORKER"
    )

    worker.start()

    return worker


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":

    print(
        "=========================================="
    )

    print(
        "🚀 STARTING TRADEIFY V8"
    )

    print(
        "=========================================="
    )

    load_state()

    start_worker()

    port = int(
        os.environ.get(
            "PORT",
            "8080"
        )
    )

    print(
        f"🌐 Flask listening on "
        f"0.0.0.0:{port}"
    )

    app.run(
        host="0.0.0.0",
        port=port,
        debug=False,
        use_reloader=False,
        threaded=True
    )
