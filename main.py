# -*- coding: utf-8 -*-

"""
============================================================
TRADEIFY A+ MTF SNIPER V7
============================================================

15M = MASTER TREND
5M  = CONFIRMATION
1M  = ENTRY / REJECTION

Logic aligned with:
SIGZY TRADEIFY A+ MTF SNIPER

FEATURES
- Closed candle only
- EMA 9 / 21 / 50
- RSI 14
- Bollinger 20 / 2
- Support / Resistance 100
- Pullback
- Rejection
- Price Flow 3
- Momentum
- Score
- Edge
- A+ Early
- A+ Confirmed
- Step 1 / 2 / 3
- 5 minute expiry
- One pending trade
- Global order lock
- Daily 2 SET WIN target
- Persistent state
- Discord
- Flask health endpoint
- Fatal error logging

IMPORTANT
Yahoo public FX data != Broker OTC data.
For real OTC execution/result, replace fetch_market()
with the broker's actual market-data API.
============================================================
"""

import os
import json
import time
import traceback
import urllib.request
from datetime import datetime, timezone, timedelta
from statistics import mean
from threading import Thread, Lock

from flask import Flask


# ============================================================
# FLASK
# ============================================================

app = Flask(__name__)


# ============================================================
# CONFIG
# ============================================================

BOT_NAME = "TRADEIFY A+ MTF SNIPER V7"

DISCORD_WEBHOOK_URL = os.environ.get(
    "DISCORD_WEBHOOK_URL",
    ""
).strip()

# Scan every 15 seconds.
# This is enough because the signal is based on CLOSED candles.
SCAN_SECONDS = 15

# 1M entry -> 5 minute expiry
EXPIRY_SECONDS = 300

# Global lock between orders
ORDER_COOLDOWN_SECONDS = 3600

# Minimum 1M history
MIN_1M_CANDLES = 150

# ------------------------------------------------------------
# A+ FILTER
# ------------------------------------------------------------

MIN_SCORE = 82
MIN_GAP = 12

# ------------------------------------------------------------
# INDICATORS
# ------------------------------------------------------------

EMA_FAST = 9
EMA_SLOW = 21
EMA_TREND = 50

RSI_PERIOD = 14

RSI_CALL_MAX = 48
RSI_PUT_MIN = 52

RSI_EXTREME_LOW = 30
RSI_EXTREME_HIGH = 70

BB_PERIOD = 20
BB_DEV = 2.0

# ------------------------------------------------------------
# SUPPORT / RESISTANCE
# ------------------------------------------------------------

SR_PERIOD = 100
SR_ZONE = 0.18

# ------------------------------------------------------------
# MONEY MANAGEMENT
# ------------------------------------------------------------

STAKE_BY_STEP = {
    1: 100,
    2: 200,
    3: 300
}

MAX_STEP = 3

# ------------------------------------------------------------
# DAILY TARGET
# ------------------------------------------------------------

TARGET_SET_WINS = 2

# ------------------------------------------------------------
# STATE
# ------------------------------------------------------------

STATE_FILE = os.environ.get(
    "TRADEIFY_STATE_FILE",
    "tradeify_a_plus_v7_state.json"
)

THAI_TZ = timezone(
    timedelta(hours=7)
)

# ------------------------------------------------------------
# SYMBOLS
# ------------------------------------------------------------

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

STATE_LOCK = Lock()

CURRENT_DAY = None

CURRENT_STEP = 1

SET_ACTIVE = False

SET_NUMBER = 0

DAILY_STOP = False

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
# HEALTH
# ============================================================

@app.route("/")
def home():

    return (
        "TRADEIFY A+ MTF SNIPER V7 IS RUNNING"
    )


@app.route("/health")
def health():

    return {

        "status":
            "running",

        "bot":
            BOT_NAME,

        "day":
            CURRENT_DAY,

        "set_number":
            SET_NUMBER,

        "set_wins":
            DAILY["set_wins"],

        "set_losses":
            DAILY["set_losses"],

        "step":
            CURRENT_STEP,

        "set_active":
            SET_ACTIVE,

        "daily_stop":
            DAILY_STOP,

        "orders":
            DAILY["signals"],

        "wins":
            DAILY["wins"],

        "losses":
            DAILY["losses"],

        "void":
            DAILY["void"],

        "pending":
            len(PENDING_TRADES),

        "global_lock_remaining":
            cooldown_remaining()

    }


# ============================================================
# STATE SAVE
# ============================================================

def save_state():

    try:

        with STATE_LOCK:

            state = {

                "current_day":
                    CURRENT_DAY,

                "current_step":
                    CURRENT_STEP,

                "set_active":
                    SET_ACTIVE,

                "set_number":
                    SET_NUMBER,

                "daily_stop":
                    DAILY_STOP,

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

            temp_file = (
                STATE_FILE
                +
                ".tmp"
            )

            with open(
                temp_file,
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
                temp_file,
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
    global DAILY_STOP
    global LAST_GLOBAL_SIGNAL_TIME
    global LAST_CANDLE
    global LAST_EARLY
    global LAST_CONFIRMED
    global LOCKED_SYMBOLS
    global PENDING_TRADES
    global DAILY

    try:

        if not os.path.exists(
            STATE_FILE
        ):

            print(
                "ℹ️ No previous state found"
            )

            return

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

        DAILY_STOP = bool(
            state.get(
                "daily_stop",
                False
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
            "✅ Previous state loaded"
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
            "[DISCORD] Webhook not configured"
        )

        return False

    try:

        payload = json.dumps(
            {
                "content":
                    message
            }
        ).encode(
            "utf-8"
        )

        req = urllib.request.Request(

            DISCORD_WEBHOOK_URL,

            data=payload,

            headers={
                "Content-Type":
                    "application/json",

                "User-Agent":
                    "TRADEIFY-A-PLUS-V7"
            },

            method="POST"
        )

        with urllib.request.urlopen(
            req,
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
            +
            "=X"
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
            .get(
                "indicators",
                {}
            )
            .get(
                "quote",
                [{}]
            )[0]
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

        for i in range(
            len(timestamps)
        ):

            try:

                ts = timestamps[i]

                op = opens[i]
                hi = highs[i]
                lo = lows[i]
                cl = closes[i]

                if ts is None:
                    continue

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

                candles.append(
                    {

                        "timestamp":
                            int(ts),

                        "open":
                            float(op),

                        "high":
                            float(hi),

                        "low":
                            float(lo),

                        "close":
                            float(cl)

                    }
                )

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
            //
            60
        ) * 60

        candles = [

            candle

            for candle in candles

            if candle["timestamp"]
            <
            current_minute

        ]

        return candles

    except Exception as e:

        print(
            f"[MARKET ERROR] "
            f"{symbol}: "
            f"{repr(e)}"
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

    bucket_size = (
        minutes * 60
    )

    buckets = {}

    for candle in candles:

        bucket = (
            candle["timestamp"]
            //
            bucket_size
        )

        buckets.setdefault(
            bucket,
            []
        ).append(
            candle
        )

    result = []

    for bucket_id in sorted(
        buckets.keys()
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

        # ----------------------------------------------------
        # Require continuous 1M candles
        # ----------------------------------------------------

        valid = True

        for i in range(
            1,
            len(group)
        ):

            if (
                group[i]["timestamp"]
                -
                group[i - 1]["timestamp"]
                !=
                60
            ):

                valid = False
                break

        if not valid:

            continue

        group = group[
            -minutes:
        ]

        result.append(
            {

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

            }
        )

    return result


# ============================================================
# EMA
# ============================================================

def ema(
    values,
    period
):

    if len(values) < period:

        return None

    value = mean(
        values[
            :period
        ]
    )

    multiplier = (
        2.0
        /
        (period + 1)
    )

    for price in values[
        period:
    ]:

        value = (
            price * multiplier
            +
            value *
            (1 - multiplier)
        )

    return value


# ============================================================
# RSI
# ============================================================

def rsi(
    candles,
    period=14
):

    if len(candles) < (
        period + 1
    ):

        return None

    closes = [
        c["close"]
        for c in candles
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
            max(
                change,
                0
            )
        )

        losses.append(
            max(
                -change,
                0
            )
        )

    avg_gain = mean(
        gains[-period:]
    )

    avg_loss = mean(
        losses[-period:]
    )

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


# ============================================================
# BOLLINGER
# ============================================================

def bollinger(
    candles,
    period=20,
    deviation=2.0
):

    if len(candles) < period:

        return (
            None,
            None,
            None
        )

    closes = [
        c["close"]
        for c in candles[
            -period:
        ]
    ]

    mid = mean(
        closes
    )

    variance = mean(
        (
            x - mid
        ) ** 2
        for x in closes
    )

    std = variance ** 0.5

    upper = (
        mid
        +
        deviation * std
    )

    lower = (
        mid
        -
        deviation * std
    )

    return (
        mid,
        upper,
        lower
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

    bar_range = max(
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
        body
        /
        bar_range
    )

    upper_ratio = (
        upper_wick
        /
        bar_range
    )

    lower_ratio = (
        lower_wick
        /
        bar_range
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

        "bull":
            bull,

        "bear":
            bear,

        "body_ratio":
            body_ratio,

        "upper_ratio":
            upper_ratio,

        "lower_ratio":
            lower_ratio,

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
# SUPPORT / RESISTANCE
# ============================================================

def support_resistance(
    candles
):

    if len(candles) < SR_PERIOD:

        return (
            None,
            None,
            None,
            None,
            None
        )

    data = candles[
        -SR_PERIOD:
    ]

    support = min(
        c["low"]
        for c in data
    )

    resistance = max(
        c["high"]
        for c in data
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
        resistance
        -
        current
    ) / sr_range

    room_put = (
        current
        -
        support
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

    return (

        zone,

        support,

        resistance,

        enough_room_call,

        enough_room_put

    )


# ============================================================
# ANALYZE A+ MTF
# ============================================================

def analyze(
    symbol,
    candles_1m
):

    if len(candles_1m) < (
        MIN_1M_CANDLES
    ):

        return None

    # --------------------------------------------------------
    # BUILD TIMEFRAMES
    # --------------------------------------------------------

    candles_5m = resample(
        candles_1m,
        5
    )

    candles_15m = resample(
        candles_1m,
        15
    )

    if len(candles_5m) < 60:

        return None

    if len(candles_15m) < 20:

        return None

    # --------------------------------------------------------
    # IMPORTANT:
    # Latest 15M and 5M candle must be CLOSED.
    #
    # The 1M feed itself has already removed current open
    # candle.
    # --------------------------------------------------------

    m15 = candles_15m[-1]
    m15_prev = candles_15m[-2]

    m5 = candles_5m[-1]
    m5_prev = candles_5m[-2]

    current = candles_1m[-1]

    # --------------------------------------------------------
    # 15M STRUCTURE
    # --------------------------------------------------------

    m15_bull = (
        m15["close"]
        >
        m15["open"]
    )

    m15_bear = (
        m15["close"]
        <
        m15["open"]
    )

    m15_higher_high = (
        m15["high"]
        >
        m15_prev["high"]
    )

    m15_higher_low = (
        m15["low"]
        >
        m15_prev["low"]
    )

    m15_lower_high = (
        m15["high"]
        <
        m15_prev["high"]
    )

    m15_lower_low = (
        m15["low"]
        <
        m15_prev["low"]
    )

    trend15_call = (
        m15_bull
        and
        (
            m15_higher_high
            or
            m15_higher_low
        )
        and
        m15["close"]
        >
        m15_prev["close"]
    )

    trend15_put = (
        m15_bear
        and
        (
            m15_lower_high
            or
            m15_lower_low
        )
        and
        m15["close"]
        <
        m15_prev["close"]
    )

    # --------------------------------------------------------
    # 5M STRUCTURE
    # --------------------------------------------------------

    m5_bull = (
        m5["close"]
        >
        m5["open"]
    )

    m5_bear = (
        m5["close"]
        <
        m5["open"]
    )

    m5_higher_high = (
        m5["high"]
        >
        m5_prev["high"]
    )

    m5_higher_low = (
        m5["low"]
        >
        m5_prev["low"]
    )

    m5_lower_high = (
        m5["high"]
        <
        m5_prev["high"]
    )

    m5_lower_low = (
        m5["low"]
        <
        m5_prev["low"]
    )

    trend5_call = (
        m5_bull
        and
        (
            m5_higher_high
            or
            m5_higher_low
        )
        and
        m5["close"]
        >
        m5_prev["close"]
    )

    trend5_put = (
        m5_bear
        and
        (
            m5_lower_high
            or
            m5_lower_low
        )
        and
        m5["close"]
        <
        m5_prev["close"]
    )

    # --------------------------------------------------------
    # 1M INDICATORS
    # --------------------------------------------------------

    closes = [
        c["close"]
        for c in candles_1m
    ]

    ema_fast = ema(
        closes,
        EMA_FAST
    )

    ema_slow = ema(
        closes,
        EMA_SLOW
    )

    ema_trend = ema(
        closes,
        EMA_TREND
    )

    # Previous EMA values for slope
    ema_fast_prev = ema(
        closes[:-1],
        EMA_FAST
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

    rsi_value = rsi(
        candles_1m,
        RSI_PERIOD
    )

    if rsi_value is None:

        return None

    bb_mid, bb_upper, bb_lower = (
        bollinger(
            candles_1m,
            BB_PERIOD,
            BB_DEV
        )
    )

    if bb_mid is None:

        return None

    # --------------------------------------------------------
    # CANDLE
    # --------------------------------------------------------

    feat = candle_features(
        current
    )

    # --------------------------------------------------------
    # FLOW
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # EMA ALIGNMENT
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # SUPPORT / RESISTANCE
    # --------------------------------------------------------

    (
        zone,
        support,
        resistance,
        enough_room_call,
        enough_room_put
    ) = support_resistance(
        candles_1m
    )

    if zone is None:

        return None

    near_support = (
        zone == "SUPPORT"
    )

    near_resistance = (
        zone == "RESISTANCE"
    )

    # --------------------------------------------------------
    # PULLBACK
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # OVEREXTENDED
    # --------------------------------------------------------

    overextended_call = (

        rsi_value
        >=
        RSI_EXTREME_HIGH

        or

        (
            feat["bull"]
            and
            feat["body_ratio"]
            >=
            0.78
            and
            feat["upper_ratio"]
            <=
            0.08
        )

    )

    overextended_put = (

        rsi_value
        <=
        RSI_EXTREME_LOW

        or

        (
            feat["bear"]
            and
            feat["body_ratio"]
            >=
            0.78
            and
            feat["lower_ratio"]
            <=
            0.08
        )

    )

    # --------------------------------------------------------
    # SCORE
    # Same weight as Indicator
    # --------------------------------------------------------

    call_score = 0
    put_score = 0

    call_reasons = []
    put_reasons = []

    # 15M MASTER = 30
    if trend15_call:

        call_score += 30

        call_reasons.append(
            "15M MASTER"
        )

    if trend15_put:

        put_score += 30

        put_reasons.append(
            "15M MASTER"
        )

    # 5M CONFIRM = 25
    if trend5_call:

        call_score += 25

        call_reasons.append(
            "5M CONFIRM"
        )

    if trend5_put:

        put_score += 25

        put_reasons.append(
            "5M CONFIRM"
        )

    # EMA = 12
    if ema_up:

        call_score += 12

        call_reasons.append(
            "EMA 9/21/50 UP"
        )

    if ema_down:

        put_score += 12

        put_reasons.append(
            "EMA 9/21/50 DOWN"
        )

    # FLOW = 10
    if flow_up:

        call_score += 10

        call_reasons.append(
            "FLOW UP"
        )

    if flow_down:

        put_score += 10

        put_reasons.append(
            "FLOW DOWN"
        )

    # REJECTION = 12
    if feat[
        "bull_rejection"
    ]:

        call_score += 12

        call_reasons.append(
            "BULL REJECTION"
        )

    if feat[
        "bear_rejection"
    ]:

        put_score += 12

        put_reasons.append(
            "BEAR REJECTION"
        )

    # CANDLE QUALITY = 6
    if feat[
        "strong_bull"
    ]:

        call_score += 6

        call_reasons.append(
            "STRONG BULL"
        )

    if feat[
        "strong_bear"
    ]:

        put_score += 6

        put_reasons.append(
            "STRONG BEAR"
        )

    # RSI = 5
    if (
        rsi_value
        <=
        RSI_CALL_MAX
        and
        rsi_value
        >
        RSI_EXTREME_LOW
    ):

        call_score += 5

        call_reasons.append(
            "RSI CALL ZONE"
        )

    if (
        rsi_value
        >=
        RSI_PUT_MIN
        and
        rsi_value
        <
        RSI_EXTREME_HIGH
    ):

        put_score += 5

        put_reasons.append(
            "RSI PUT ZONE"
        )

    # S/R = 10
    if near_support:

        call_score += 10

        call_reasons.append(
            "NEAR SUPPORT"
        )

    if near_resistance:

        put_score += 10

        put_reasons.append(
            "NEAR RESISTANCE"
        )

    # PULLBACK = 8
    if pullback_call:

        call_score += 8

        call_reasons.append(
            "CALL PULLBACK"
        )

    if pullback_put:

        put_score += 8

        put_reasons.append(
            "PUT PULLBACK"
        )

    # ROOM = 5
    if enough_room_call:

        call_score += 5

        call_reasons.append(
            "CALL ROOM"
        )

    if enough_room_put:

        put_score += 5

        put_reasons.append(
            "PUT ROOM"
        )

    # --------------------------------------------------------
    # PENALTIES
    # --------------------------------------------------------

    if near_resistance:

        call_score -= 15

    if near_support:

        put_score -= 15

    if overextended_call:

        call_score -= 20

    if overextended_put:

        put_score -= 20

    # HTF conflict
    if (
        trend15_call
        and
        trend5_put
    ):

        call_score -= 25
        put_score -= 25

    if (
        trend15_put
        and
        trend5_call
    ):

        call_score -= 25
        put_score -= 25

    # --------------------------------------------------------
    # NORMALIZE
    # --------------------------------------------------------

    call_score = max(
        0,
        min(
            100,
            int(call_score)
        )
    )

    put_score = max(
        0,
        min(
            100,
            int(put_score)
        )
    )

    # --------------------------------------------------------
    # DIRECTION
    # --------------------------------------------------------

    if (
        call_score
        >
        put_score
    ):

        direction = "CALL"

        score = call_score

        opposite_score = put_score

        reasons = call_reasons

    elif (
        put_score
        >
        call_score
    ):

        direction = "PUT"

        score = put_score

        opposite_score = call_score

        reasons = put_reasons

    else:

        return {

            "symbol":
                symbol,

            "direction":
                None,

            "early":
                False,

            "confirmed":
                False,

            "score":
                0,

            "call_score":
                call_score,

            "put_score":
                put_score,

            "edge":
                0,

            "entry":
                current["close"],

            "timestamp":
                current["timestamp"],

            "zone":
                zone,

            "rsi":
                rsi_value,

            "support":
                support,

            "resistance":
                resistance,

            "reasons":
                []

        }

    edge = (
        score
        -
        opposite_score
    )

    # --------------------------------------------------------
    # MAJOR CONDITIONS
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # EARLY
    # --------------------------------------------------------

    early_call = (

        major_call

        and

        call_score
        >=
        (
            MIN_SCORE - 8
        )

        and

        (
            call_score
            -
            put_score
        )
        >=
        MIN_GAP

        and

        (
            pullback_call
            or
            feat["bull_rejection"]
            or
            (
                rsi_value
                <=
                RSI_CALL_MAX
            )
        )

    )

    early_put = (

        major_put

        and

        put_score
        >=
        (
            MIN_SCORE - 8
        )

        and

        (
            put_score
            -
            call_score
        )
        >=
        MIN_GAP

        and

        (
            pullback_put
            or
            feat["bear_rejection"]
            or
            (
                rsi_value
                >=
                RSI_PUT_MIN
            )
        )

    )

    # --------------------------------------------------------
    # CONFIRMED
    # Same core requirement as indicator
    # --------------------------------------------------------

    confirmed_call = (

        major_call

        and

        call_score
        >=
        MIN_SCORE

        and

        (
            call_score
            -
            put_score
        )
        >=
        MIN_GAP

        and

        feat["bull"]

        and

        feat["bull_rejection"]

        and

        pullback_call

        and

        flow_up

    )

    confirmed_put = (

        major_put

        and

        put_score
        >=
        MIN_SCORE

        and

        (
            put_score
            -
            call_score
        )
        >=
        MIN_GAP

        and

        feat["bear"]

        and

        feat["bear_rejection"]

        and

        pullback_put

        and

        flow_down

    )

    confirmed = (
        confirmed_call
        if direction == "CALL"
        else
        confirmed_put
    )

    early = (
        early_call
        if direction == "CALL"
        else
        early_put
    )

    # --------------------------------------------------------
    # RETURN
    # --------------------------------------------------------

    return {

        "symbol":
            symbol,

        "direction":
            direction,

        "early":
            bool(early),

        "confirmed":
            bool(confirmed),

        "score":
            score,

        "call_score":
            call_score,

        "put_score":
            put_score,

        "edge":
            edge,

        "entry":
            float(
                current["close"]
            ),

        "timestamp":
            int(
                current["timestamp"]
            ),

        "zone":
            zone,

        "rsi":
            float(
                rsi_value
            ),

        "support":
            support,

        "resistance":
            resistance,

        "bb_mid":
            bb_mid,

        "bb_upper":
            bb_upper,

        "bb_lower":
            bb_lower,

        "ema_fast":
            ema_fast,

        "ema_slow":
            ema_slow,

        "ema_trend":
            ema_trend,

        "reasons":
            reasons

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

    remaining = (
        ORDER_COOLDOWN_SECONDS
        -
        (
            unix_now()
            -
            LAST_GLOBAL_SIGNAL_TIME
        )
    )

    return max(
        0,
        remaining
    )


# ============================================================
# DAILY RESET
# ============================================================

def reset_daily():

    global CURRENT_DAY
    global CURRENT_STEP
    global SET_ACTIVE
    global SET_NUMBER
    global DAILY_STOP
    global LAST_GLOBAL_SIGNAL_TIME

    today = (
        thai_now()
        .strftime(
            "%Y-%m-%d"
        )
    )

    if today == CURRENT_DAY:

        return

    CURRENT_DAY = today

    CURRENT_STEP = 1

    SET_ACTIVE = False

    SET_NUMBER = 0

    DAILY_STOP = False

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

        "🌅 **TRADEIFY A+ NEW DAY**\n"
        f"📅 `{today}`\n"
        "🎯 Target: **2 SET WIN**\n"
        "💰 STEP 1 = 100 บาท\n"
        "💰 STEP 2 = 200 บาท\n"
        "💰 STEP 3 = 300 บาท\n"
        "🔒 Global lock = 60 นาที"
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

    timestamp = int(
        analysis[
            "timestamp"
        ]
    )

    # --------------------------------------------------------
    # Safety
    # --------------------------------------------------------

    if DAILY_STOP:

        return False

    if not global_order_available():

        return False

    if PENDING_TRADES:

        return False

    if symbol in LOCKED_SYMBOLS:

        return False

    # --------------------------------------------------------
    # New SET
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

    # --------------------------------------------------------
    # Expiry
    #
    # Signal is based on CLOSED 1M candle.
    # Expire exactly 5 minutes after that candle.
    # --------------------------------------------------------

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

        "created_at":
            unix_now(),

        "score":
            analysis["score"],

        "edge":
            analysis["edge"]

    }

    LOCKED_SYMBOLS[
        symbol
    ] = {

        "direction":
            direction,

        "timestamp":
            timestamp

    }

    LAST_GLOBAL_SIGNAL_TIME = (
        unix_now()
    )

    LAST_CONFIRMED[
        symbol
    ] = (
        symbol,
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

    print(
        f"🎯 CONFIRMED "
        f"{symbol} "
        f"{direction} "
        f"score={analysis['score']} "
        f"edge={analysis['edge']}"
    )

    send_discord(

        "🎯 **TRADEIFY A+ CONFIRMED**\n\n"

        f"{icon} `{symbol}`\n"

        f"➡️ **{direction}**\n"

        f"📊 Score: "
        f"`{analysis['score']}/100`\n"

        f"⚡ Edge: "
        f"`+{analysis['edge']}`\n"

        f"📍 Zone: "
        f"`{analysis['zone']}`\n"

        f"📈 RSI: "
        f"`{analysis['rsi']:.1f}`\n"

        f"💰 Entry: "
        f"`{entry}`\n"

        f"🎯 SET #{SET_NUMBER}\n"

        f"🔥 OPP {step}/3\n"

        f"💵 Stake: "
        f"`{stake}` บาท\n"

        f"⏱ Expiry: "
        f"`5 นาที`\n\n"

        "🔒 Global LOCK 60 MIN\n\n"

        "⚠️ ข้อมูล: "
        "Public FX feed / ไม่ใช่ OTC Broker"
    )

    return True


# ============================================================
# CHECK RESULT
# ============================================================

def check_pending_trades():

    global CURRENT_STEP
    global SET_ACTIVE
    global DAILY_STOP

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

        set_number = int(
            trade["set_number"]
        )

        stake = int(
            trade["stake"]
        )

        candles = fetch_market(
            symbol
        )

        if not candles:

            print(
                f"[RESULT WAIT] "
                f"{symbol}: "
                "no market data"
            )

            continue

        # ----------------------------------------------------
        # Find first closed 1M candle >= expiry
        # ----------------------------------------------------

        result_candle = None

        for candle in candles:

            if (
                candle["timestamp"]
                >=
                expiry
            ):

                result_candle = candle

                break

        if result_candle is None:

            continue

        exit_price = float(
            result_candle["close"]
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

        print(
            f"📊 RESULT "
            f"{symbol} "
            f"{direction} "
            f"{result}"
        )

        # ----------------------------------------------------
        # STATS
        # ----------------------------------------------------

        if result == "WIN":

            DAILY["wins"] += 1

        elif result == "LOSS":

            DAILY["losses"] += 1

        else:

            DAILY["void"] += 1

        # ----------------------------------------------------
        # WIN
        # ----------------------------------------------------

        if result == "WIN":

            SET_ACTIVE = False

            DAILY["set_wins"] += 1

            CURRENT_STEP = 1

            message = (
                f"🏆 SET #{set_number} WIN"
            )

            if (
                DAILY["set_wins"]
                >=
                TARGET_SET_WINS
            ):

                DAILY_STOP = True

                message += (
                    "\n🛑 DAILY TARGET REACHED"
                    "\n🏆 2 SET WIN"
                    "\nระบบหยุดส่งสัญญาณวันนี้"
                )

            send_discord(

                "✅ **TRADE RESULT**\n"
                f"📌 `{symbol}`\n"
                f"➡️ **{direction}**\n"
                f"💰 Entry: `{entry}`\n"
                f"🏁 Exit: `{exit_price}`\n"
                f"🎯 Step: `{step}/3`\n"
                f"💵 Stake: `{stake}` บาท\n"
                f"📊 **WIN**\n"
                f"🏆 {message}\n"
                f"📈 Set Wins: "
                f"`{DAILY['set_wins']}/2`"
            )

        # ----------------------------------------------------
        # LOSS
        # ----------------------------------------------------

        elif result == "LOSS":

            if step < MAX_STEP:

                CURRENT_STEP = (
                    step + 1
                )

                SET_ACTIVE = True

                send_discord(

                    "❌ **TRADE RESULT**\n"
                    f"📌 `{symbol}`\n"
                    f"➡️ **{direction}**\n"
                    f"💰 Entry: `{entry}`\n"
                    f"🏁 Exit: `{exit_price}`\n"
                    f"🎯 Step: `{step}/3`\n"
                    "📊 **LOSS**\n\n"
                    f"🔁 STEP "
                    f"{CURRENT_STEP}/3\n"
                    f"💵 Stake: "
                    f"`{STAKE_BY_STEP[CURRENT_STEP]}` บาท\n"
                    "⚠️ รอสัญญาณ A+ ใหม่ "
                    "ไม่ไล่เข้าแท่งถัดไปอัตโนมัติ"
                )

            else:

                SET_ACTIVE = False

                DAILY["set_losses"] += 1

                CURRENT_STEP = 1

                send_discord(

                    "❌ **TRADE RESULT**\n"
                    f"📌 `{symbol}`\n"
                    f"➡️ **{direction}**\n"
                    f"💰 Entry: `{entry}`\n"
                    f"🏁 Exit: `{exit_price}`\n"
                    f"🎯 Step: `{step}/3`\n"
                    "📊 **LOSS**\n\n"
                    f"💀 **SET #{set_number} LOSS**\n"
                    "🔄 Reset → STEP 1\n"
                    "⚠️ ไม่ทบโดยไม่มี A+ signal"
                )

        # ----------------------------------------------------
        # VOID
        # ----------------------------------------------------

        else:

            SET_ACTIVE = False

            CURRENT_STEP = 1

            send_discord(

                "⚪ **TRADE RESULT: VOID**\n"
                f"📌 `{symbol}`\n"
                f"➡️ **{direction}**\n"
                f"💰 Entry: `{entry}`\n"
                f"🏁 Exit: `{exit_price}`\n"
                f"🎯 Step: `{step}/3`\n"
                "🔄 Reset → STEP 1"
            )

        # ----------------------------------------------------
        # UNLOCK
        # ----------------------------------------------------

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
# BOT LOOP
# ============================================================

def bot_loop():

    print(
        "=========================================="
    )

    print(
        "🚀 TRADEIFY A+ MTF SNIPER V7"
    )

    print(
        "15M MASTER"
    )

    print(
        "5M CONFIRM"
    )

    print(
        "1M ENTRY / REJECTION"
    )

    print(
        "=========================================="
    )

    while True:

        try:

            reset_daily()

            # ------------------------------------------------
            # RESULT FIRST
            # ------------------------------------------------

            check_pending_trades()

            # ------------------------------------------------
            # DAILY STOP
            # ------------------------------------------------

            if DAILY_STOP:

                time.sleep(
                    SCAN_SECONDS
                )

                continue

            # ------------------------------------------------
            # GLOBAL LOCK
            # ------------------------------------------------

            if not global_order_available():

                remaining = (
                    cooldown_remaining()
                )

                print(
                    f"[LOCK] "
                    f"Global order lock "
                    f"{remaining}s"
                )

                time.sleep(
                    SCAN_SECONDS
                )

                continue

            # ------------------------------------------------
            # SCAN
            # ------------------------------------------------

            for symbol in SYMBOLS:

                # Only one active order
                if PENDING_TRADES:

                    break

                try:

                    candles = fetch_market(
                        symbol
                    )

                    if len(candles) < (
                        MIN_1M_CANDLES
                    ):

                        print(
                            f"[DATA] "
                            f"{symbol}: "
                            f"{len(candles)} candles"
                        )

                        continue

                    # ------------------------------------------------
                    # CLOSED 1M CANDLE LOCK
                    # ------------------------------------------------

                    latest_timestamp = (
                        candles[-1][
                            "timestamp"
                        ]
                    )

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

                    if not analysis[
                        "direction"
                    ]:

                        continue

                    # ------------------------------------------------
                    # EARLY WARNING
                    # ------------------------------------------------

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
                                f"Edge: "
                                f"`+{analysis['edge']}`\n"
                                f"Zone: "
                                f"`{analysis['zone']}`\n"
                                "⚠️ "
                                "ยังไม่ใช่คำสั่งเข้า"
                            )

                            print(
                                f"🟡 EARLY "
                                f"{symbol} "
                                f"{analysis['direction']} "
                                f"{analysis['score']}"
                            )

                    # ------------------------------------------------
                    # CONFIRMED
                    # ------------------------------------------------

                    if analysis[
                        "confirmed"
                    ]:

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

                            break

                except Exception as symbol_error:

                    print(
                        f"[SYMBOL ERROR] "
                        f"{symbol}: "
                        f"{repr(symbol_error)}"
                    )

                    traceback.print_exc()

                    continue

            save_state()

            time.sleep(
                SCAN_SECONDS
            )

        except Exception as loop_error:

            print(
                "=========================================="
            )

            print(
                "❌ BOT LOOP ERROR"
            )

            print(
                repr(loop_error)
            )

            traceback.print_exc()

            print(
                "=========================================="
            )

            time.sleep(10)


# ============================================================
# STARTUP
# ============================================================

def start_bot():

    print(
        "=========================================="
    )

    print(
        f"🚀 {BOT_NAME}"
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
        "1M  = ENTRY"
    )

    print(
        f"MIN SCORE = {MIN_SCORE}"
    )

    print(
        f"MIN GAP   = {MIN_GAP}"
    )

    print(
        "EXPIRY    = 5 MIN"
    )

    print(
        "STEP      = 100 / 200 / 300"
    )

    print(
        "TARGET    = 2 SET WIN"
    )

    print(
        "=========================================="
    )

    load_state()

    worker = Thread(
        target=bot_loop,
        daemon=True,
        name="tradeify-worker"
    )

    worker.start()

    print(
        "✅ Bot worker started"
    )


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":

    try:

        start_bot()

        port = int(
            os.environ.get(
                "PORT",
                "5000"
            )
        )

        print(
            f"🌐 Flask listening "
            f"on 0.0.0.0:{port}"
        )

        print(
            "=========================================="
        )

        app.run(
            host="0.0.0.0",
            port=port,
            debug=False,
            use_reloader=False,
            threaded=True
        )

    except Exception as fatal_error:

        print(
            "=========================================="
        )

        print(
            "💀 FATAL STARTUP ERROR"
        )

        print(
            repr(fatal_error)
        )

        traceback.print_exc()

        print(
            "=========================================="
        )

        raise
