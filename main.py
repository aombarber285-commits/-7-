# -*- coding: utf-8 -*-

import os
import json
import time
import urllib.request
from datetime import datetime, timezone, timedelta
from statistics import mean
from threading import Thread, Lock
from flask import Flask

# ============================================================
# TRADEIFY v6.3 REAL 1-IN-3 TRACKER
#
# 1H  = MASTER TREND
# 15M = STRUCTURE CONFIRM
# 5M  = ENTRY
# 1M  = MARKET RESULT
#
# ระบบ:
# - Real market data
# - Closed candle only
# - Support / Resistance
# - Reversal / Rejection
# - EMA 9 / 21 / 50
# - RSI
# - Momentum
# - Score
# - Edge
# - Signal Lock
# - 60 minute global order lock
# - 1 in 3 opportunity
# - Step 1 / 2 / 3
# - WIN / LOSS / VOID
# - 2 SET WIN -> STOP DAY
# - Discord notification
# - Persistent state
#
# IMPORTANT:
# Yahoo Finance เป็น public FX data
# ไม่ใช่ราคา OTC ภายใน Broker
#
# ถ้าต้องการผล WIN/LOSS ของ OTC จริง
# ต้องเปลี่ยน fetch_market() เป็น Broker OTC API
# ============================================================


# ============================================================
# FLASK
# ============================================================

app = Flask(__name__)


@app.route("/")
def home():
    return "TRADEIFY v6.3 REAL 1-IN-3 BOT IS RUNNING!"


@app.route("/health")
def health():
    return {
        "status": "running",
        "bot": "TRADEIFY v6.3",
        "day": CURRENT_DAY,
        "set_number": SET_NUMBER,
        "set_wins": DAILY["set_wins"],
        "step": CURRENT_STEP,
        "set_active": SET_ACTIVE,
        "orders": DAILY["signals"],
        "wins": DAILY["wins"],
        "losses": DAILY["losses"],
        "void": DAILY["void"],
        "daily_stop": DAILY_STOP
    }


# ============================================================
# CONFIG
# ============================================================

DISCORD_WEBHOOK_URL = os.environ.get(
    "DISCORD_WEBHOOK_URL",
    ""
).strip()

SCAN_SECONDS = 10

# 5 minute expiry
EXPIRY_SECONDS = 300

# Global order cooldown
ORDER_COOLDOWN_SECONDS = 3600

# Minimum data
MIN_1M_CANDLES = 100

# Analysis
MIN_SCORE = 75
MIN_EDGE = 12

SR_LOOKBACK = 120

# Money management
STAKE_BY_STEP = {
    1: 100,
    2: 200,
    3: 300
}

MAX_STEP = 3

# Daily target
TARGET_SET_WINS = 2

# State file
STATE_FILE = os.environ.get(
    "TRADEIFY_STATE_FILE",
    "tradeify_state.json"
)

THAI_TZ = timezone(
    timedelta(hours=7)
)

# Symbols
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
# PERSISTENT STATE
# ============================================================

def save_state():
    try:

        with STATE_LOCK:

            state = {
                "current_day": CURRENT_DAY,
                "current_step": CURRENT_STEP,
                "set_active": SET_ACTIVE,
                "set_number": SET_NUMBER,
                "daily_stop": DAILY_STOP,
                "last_global_signal_time": LAST_GLOBAL_SIGNAL_TIME,
                "last_candle": LAST_CANDLE,
                "last_early": LAST_EARLY,
                "last_confirmed": LAST_CONFIRMED,
                "locked_symbols": LOCKED_SYMBOLS,
                "pending_trades": PENDING_TRADES,
                "daily": DAILY
            }

            temp_file = STATE_FILE + ".tmp"

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
            e
        )


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
            "💾 TRADEIFY state loaded"
        )

    except Exception as e:

        print(
            "[STATE LOAD ERROR]",
            e
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
                "content": message
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
                    "TRADEIFY-V6.3"
            },

            method="POST"
        )

        with urllib.request.urlopen(
            req,
            timeout=10
        ) as resp:

            return resp.status in (
                200,
                204
            )

    except Exception as e:

        print(
            "[DISCORD ERROR]",
            e
        )

        return False


# ============================================================
# MARKET DATA
# ============================================================

def fetch_market(symbol):

    try:

        formatted_symbol = (
            symbol.replace(
                "/",
                ""
            )
            + "=X"
        )

        # 5 days gives enough 1M data
        # for 1H / 15M calculations
        url = (
            "https://query1.finance.yahoo.com/"
            "v8/finance/chart/"
            f"{formatted_symbol}"
            "?interval=1m&range=5d"
        )

        req = urllib.request.Request(

            url,

            headers={
                "User-Agent":
                    "Mozilla/5.0"
            }
        )

        with urllib.request.urlopen(
            req,
            timeout=15
        ) as resp:

            data = json.loads(
                resp.read().decode(
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

        candles = []

        for i in range(
            len(timestamps)
        ):

            try:

                ts = timestamps[i]

                op = quote["open"][i]
                hi = quote["high"][i]
                lo = quote["low"][i]
                cl = quote["close"][i]

                if ts is None:
                    continue

                if (
                    op is None
                    or hi is None
                    or lo is None
                    or cl is None
                ):
                    continue

                candles.append(
                    {
                        "timestamp": int(ts),
                        "open": float(op),
                        "high": float(hi),
                        "low": float(lo),
                        "close": float(cl)
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
            // 60
        ) * 60

        candles = [
            c for c in candles
            if c["timestamp"]
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

def resample(candles, minutes):

    if not candles:

        return []

    bucket_size = (
        minutes * 60
    )

    buckets = {}

    for candle in candles:

        bucket = (
            candle["timestamp"]
            // bucket_size
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

        # Need a complete timeframe
        expected = minutes

        if len(group) < expected:

            continue

        # Verify continuous minutes
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
            -expected:
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

def calculate_ema(
    values,
    period
):

    if len(values) < period:

        return None

    result = mean(
        values[:period]
    )

    multiplier = (
        2 /
        (period + 1)
    )

    for value in values[
        period:
    ]:

        result = (
            value * multiplier
            +
            result *
            (1 - multiplier)
        )

    return result


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
        100 -
        (100 / (1 + rs))
    )


# ============================================================
# MARKET STRUCTURE
# ============================================================

def market_structure(
    candles,
    period=20
):

    if len(candles) < period:

        return "RANGE", 0

    data = candles[
        -period:
    ]

    half = period // 2

    first = data[
        :half
    ]

    second = data[
        half:
    ]

    fh = mean(
        x["high"]
        for x in first
    )

    sh = mean(
        x["high"]
        for x in second
    )

    fl = mean(
        x["low"]
        for x in first
    )

    sl = mean(
        x["low"]
        for x in second
    )

    fc = mean(
        x["close"]
        for x in first
    )

    sc = mean(
        x["close"]
        for x in second
    )

    avg_range = mean(
        x["high"] - x["low"]
        for x in data
    )

    if avg_range <= 0:

        return "RANGE", 0

    strength = min(
        1.0,
        abs(sc - fc)
        /
        avg_range
    )

    if (
        sh > fh
        and sl > fl
        and sc > fc
    ):

        return "CALL", strength

    if (
        sh < fh
        and sl < fl
        and sc < fc
    ):

        return "PUT", strength

    return "RANGE", 0


# ============================================================
# SUPPORT / RESISTANCE
# ============================================================

def support_resistance(
    candles
):

    if len(candles) < SR_LOOKBACK:

        return (
            "MID",
            None,
            None
        )

    data = candles[
        -SR_LOOKBACK:
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

    distance = max(
        resistance - support,
        1e-12
    )

    position = (
        current - support
    ) / distance

    if position <= 0.25:

        zone = "SUPPORT"

    elif position >= 0.75:

        zone = "RESISTANCE"

    else:

        zone = "MID"

    return (
        zone,
        support,
        resistance
    )


# ============================================================
# CANDLE REJECTION
# ============================================================

def candle_rejection(
    candle
):

    rng = max(
        candle["high"]
        -
        candle["low"],
        1e-12
    )

    body = abs(
        candle["close"]
        -
        candle["open"]
    )

    upper = (
        candle["high"]
        -
        max(
            candle["open"],
            candle["close"]
        )
    )

    lower = (
        min(
            candle["open"],
            candle["close"]
        )
        -
        candle["low"]
    )

    body_ratio = (
        body / rng
    )

    lower_ratio = (
        lower / rng
    )

    upper_ratio = (
        upper / rng
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

    return {
        "bull": bull_rejection,
        "bear": bear_rejection,
        "body_ratio": body_ratio,
        "upper_ratio": upper_ratio,
        "lower_ratio": lower_ratio
    }


# ============================================================
# ANALYSIS
# ============================================================

def analyze(
    symbol,
    candles_1m
):

    if len(candles_1m) < (
        MIN_1M_CANDLES
    ):

        return None

    candles_5m = resample(
        candles_1m,
        5
    )

    candles_15m = resample(
        candles_1m,
        15
    )

    candles_1h = resample(
        candles_1m,
        60
    )

    if len(candles_5m) < 20:

        return None

    if len(candles_15m) < 20:

        return None

    if len(candles_1h) < 10:

        return None

    # --------------------------------------------------------
    # PRICE SERIES
    # --------------------------------------------------------

    p1h = [
        x["close"]
        for x in candles_1h
    ]

    p15 = [
        x["close"]
        for x in candles_15m
    ]

    p5 = [
        x["close"]
        for x in candles_5m
    ]

    # --------------------------------------------------------
    # EMA
    # --------------------------------------------------------

    ema1h_9 = calculate_ema(
        p1h,
        9
    )

    ema1h_21 = calculate_ema(
        p1h,
        21
    )

    ema15_9 = calculate_ema(
        p15,
        9
    )

    ema15_21 = calculate_ema(
        p15,
        21
    )

    ema5_9 = calculate_ema(
        p5,
        9
    )

    ema5_21 = calculate_ema(
        p5,
        21
    )

    if any(
        x is None
        for x in [
            ema1h_9,
            ema1h_21,
            ema15_9,
            ema15_21,
            ema5_9,
            ema5_21
        ]
    ):

        return None

    # --------------------------------------------------------
    # STRUCTURE
    # --------------------------------------------------------

    structure1h, strength1h = (
        market_structure(
            candles_1h
        )
    )

    structure15, strength15 = (
        market_structure(
            candles_15m
        )
    )

    structure5, strength5 = (
        market_structure(
            candles_5m
        )
    )

    # --------------------------------------------------------
    # ZONE
    # --------------------------------------------------------

    zone, support, resistance = (
        support_resistance(
            candles_5m
        )
    )

    # --------------------------------------------------------
    # RSI
    # --------------------------------------------------------

    rsi1 = calculate_rsi(
        candles_1m,
        14
    )

    rsi5 = calculate_rsi(
        candles_5m,
        14
    )

    rsi15 = calculate_rsi(
        candles_15m,
        14
    )

    if rsi1 is None:

        return None

    if rsi5 is None:

        return None

    if rsi15 is None:

        return None

    # --------------------------------------------------------
    # CURRENT CANDLE
    # --------------------------------------------------------

    current = candles_1m[
        -1
    ]

    rejection = candle_rejection(
        current
    )

    # --------------------------------------------------------
    # MASTER TREND
    # --------------------------------------------------------

    master_call = (
        structure1h == "CALL"
        and
        ema1h_9 > ema1h_21
    )

    master_put = (
        structure1h == "PUT"
        and
        ema1h_9 < ema1h_21
    )

    # --------------------------------------------------------
    # 15M CONFIRM
    # --------------------------------------------------------

    confirm_call = (
        structure15 == "CALL"
        and
        ema15_9 > ema15_21
    )

    confirm_put = (
        structure15 == "PUT"
        and
        ema15_9 < ema15_21
    )

    # --------------------------------------------------------
    # 5M ENTRY
    # --------------------------------------------------------

    entry_call = (
        structure5 == "CALL"
        and
        ema5_9 > ema5_21
    )

    entry_put = (
        structure5 == "PUT"
        and
        ema5_9 < ema5_21
    )

    # --------------------------------------------------------
    # REVERSAL
    # --------------------------------------------------------

    reversal_call = (
        zone == "SUPPORT"
        and
        rejection["bull"]
        and
        rsi1 <= 48
    )

    reversal_put = (
        zone == "RESISTANCE"
        and
        rejection["bear"]
        and
        rsi1 >= 52
    )

    # --------------------------------------------------------
    # MOMENTUM
    # --------------------------------------------------------

    momentum_call = (
        current["close"]
        >
        candles_1m[-2]["close"]
        and
        ema5_9 > ema5_21
    )

    momentum_put = (
        current["close"]
        <
        candles_1m[-2]["close"]
        and
        ema5_9 < ema5_21
    )

    # --------------------------------------------------------
    # FLOW
    # --------------------------------------------------------

    flow_call = (
        candles_1m[-1]["close"]
        >
        candles_1m[-2]["close"]
        >
        candles_1m[-3]["close"]
    )

    flow_put = (
        candles_1m[-1]["close"]
        <
        candles_1m[-2]["close"]
        <
        candles_1m[-3]["close"]
    )

    # --------------------------------------------------------
    # SCORE
    # --------------------------------------------------------

    score = {
        "CALL": 0,
        "PUT": 0
    }

    reasons = {
        "CALL": [],
        "PUT": []
    }

    def add(
        direction,
        points,
        reason
    ):

        score[direction] += points

        reasons[
            direction
        ].append(
            reason
        )

    # 1H = 30
    if master_call:

        add(
            "CALL",
            30,
            "1H MASTER UP"
        )

    if master_put:

        add(
            "PUT",
            30,
            "1H MASTER DOWN"
        )

    # 15M = 20
    if confirm_call:

        add(
            "CALL",
            20,
            "15M CONFIRM UP"
        )

    if confirm_put:

        add(
            "PUT",
            20,
            "15M CONFIRM DOWN"
        )

    # 5M = 15
    if entry_call:

        add(
            "CALL",
            15,
            "5M ENTRY UP"
        )

    if entry_put:

        add(
            "PUT",
            15,
            "5M ENTRY DOWN"
        )

    # EMA = 10
    if (
        ema1h_9 > ema1h_21
    ):

        add(
            "CALL",
            10,
            "EMA BULLISH"
        )

    if (
        ema1h_9 < ema1h_21
    ):

        add(
            "PUT",
            10,
            "EMA BEARISH"
        )

    # S/R reversal = 15
    if reversal_call:

        add(
            "CALL",
            15,
            "SUPPORT REJECTION"
        )

    if reversal_put:

        add(
            "PUT",
            15,
            "RESISTANCE REJECTION"
        )

    # Candle rejection = 8
    if rejection["bull"]:

        add(
            "CALL",
            8,
            "BULL REJECTION"
        )

    if rejection["bear"]:

        add(
            "PUT",
            8,
            "BEAR REJECTION"
        )

    # RSI context
    if (
        30 < rsi1 <= 48
    ):

        add(
            "CALL",
            6,
            "RSI CALL ZONE"
        )

    if (
        52 <= rsi1 < 70
    ):

        add(
            "PUT",
            6,
            "RSI PUT ZONE"
        )

    # Momentum
    if momentum_call:

        add(
            "CALL",
            7,
            "MOMENTUM UP"
        )

    if momentum_put:

        add(
            "PUT",
            7,
            "MOMENTUM DOWN"
        )

    # Flow
    if flow_call:

        add(
            "CALL",
            5,
            "PRICE FLOW UP"
        )

    if flow_put:

        add(
            "PUT",
            5,
            "PRICE FLOW DOWN"
        )

    # --------------------------------------------------------
    # PENALTIES
    # --------------------------------------------------------

    # CALL at resistance
    if zone == "RESISTANCE":

        score["CALL"] -= 15

    # PUT at support
    if zone == "SUPPORT":

        score["PUT"] -= 15

    # CALL overextended
    if rsi1 >= 75:

        score["CALL"] -= 20

    # PUT overextended
    if rsi1 <= 25:

        score["PUT"] -= 20

    # HTF conflict
    if (
        master_call
        and
        confirm_put
    ):

        score["CALL"] -= 25
        score["PUT"] -= 25

    if (
        master_put
        and
        confirm_call
    ):

        score["CALL"] -= 25
        score["PUT"] -= 25

    # --------------------------------------------------------
    # NORMALIZE
    # --------------------------------------------------------

    score["CALL"] = max(
        0,
        min(
            100,
            int(
                score["CALL"]
            )
        )
    )

    score["PUT"] = max(
        0,
        min(
            100,
            int(
                score["PUT"]
            )
        )
    )

    # --------------------------------------------------------
    # DIRECTION
    # --------------------------------------------------------

    if (
        score["CALL"]
        >
        score["PUT"]
    ):

        direction = "CALL"

    elif (
        score["PUT"]
        >
        score["CALL"]
    ):

        direction = "PUT"

    else:

        return None

    opposite = (
        "PUT"
        if direction == "CALL"
        else "CALL"
    )

    edge = (
        score[direction]
        -
        score[opposite]
    )

    # --------------------------------------------------------
    # SIDEWAY FILTER
    # --------------------------------------------------------

    sideway = (
        structure1h == "RANGE"
        or
        structure15 == "RANGE"
        or
        structure5 == "RANGE"
    )

    # --------------------------------------------------------
    # EARLY
    # --------------------------------------------------------

    early = (

        (
            master_call
            and
            confirm_call
        )

        or

        (
            master_put
            and
            confirm_put
        )

    ) and (

        score[direction]
        >=
        MIN_SCORE - 8

    ) and (

        edge
        >=
        MIN_EDGE - 3

    ) and not sideway

    # --------------------------------------------------------
    # CONFIRMED
    # --------------------------------------------------------

    confirmed = (

        (
            direction == "CALL"
            and
            master_call
            and
            confirm_call
            and
            entry_call
            and
            (
                rejection["bull"]
                or
                reversal_call
            )
            and
            momentum_call
        )

        or

        (
            direction == "PUT"
            and
            master_put
            and
            confirm_put
            and
            entry_put
            and
            (
                rejection["bear"]
                or
                reversal_put
            )
            and
            momentum_put
        )

    ) and (

        score[direction]
        >=
        MIN_SCORE

    ) and (

        edge
        >=
        MIN_EDGE

    ) and not sideway

    # --------------------------------------------------------
    # ENTRY
    # --------------------------------------------------------

    entry_price = (
        candles_5m[-1]["close"]
    )

    signal_timestamp = (
        candles_5m[-1]["timestamp"]
    )

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
            score[direction],

        "call_score":
            score["CALL"],

        "put_score":
            score["PUT"],

        "edge":
            edge,

        "entry":
            entry_price,

        "timestamp":
            signal_timestamp,

        "zone":
            zone,

        "rsi":
            rsi1,

        "support":
            support,

        "resistance":
            resistance,

        "reasons":
            reasons[direction]

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
# PENDING TRADE RESULT
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

        symbol = trade["symbol"]

        direction = trade[
            "direction"
        ]

        entry = float(
            trade["entry"]
        )

        # ----------------------------------------------------
        # Fetch fresh market data
        # ----------------------------------------------------

        candles = fetch_market(
            symbol
        )

        if not candles:

            print(
                f"[RESULT WAIT] "
                f"{symbol}: no market data"
            )

            continue

        # Find first closed 1M candle
        # at or after expiry
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

        step = int(
            trade["step"]
        )

        set_number = int(
            trade["set_number"]
        )

        stake = int(
            trade["stake"]
        )

        # ----------------------------------------------------
        # UPDATE STATS
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

            SET_MESSAGE = (
                f"🏆 SET #{set_number} WIN"
            )

            if (
                DAILY["set_wins"]
                >=
                TARGET_SET_WINS
            ):

                DAILY_STOP = True

                SET_MESSAGE += (
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
                f"📊 **{result}**\n"
                f"🏆 {SET_MESSAGE}\n"
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
                    f"📊 **LOSS**\n\n"
                    f"🔁 ไปไม้ถัดไป "
                    f"**STEP {CURRENT_STEP}/3**\n"
                    f"💵 Stake: "
                    f"`{STAKE_BY_STEP[CURRENT_STEP]}` บาท"
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
                    f"📊 **LOSS**\n\n"
                    f"💀 **SET #{set_number} LOSS**\n"
                    f"🔄 เริ่ม SET ใหม่ที่ STEP 1"
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
                f"⚪ ราคาเท่ากัน\n"
                f"🔄 Reset เป็น STEP 1"
            )

        # ----------------------------------------------------
        # UNLOCK SYMBOL
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
# DAILY RESET
# ============================================================

def reset_daily():

    global CURRENT_DAY
    global CURRENT_STEP
    global SET_ACTIVE
    global SET_NUMBER
    global DAILY_STOP
    global LAST_GLOBAL_SIGNAL_TIME

    today = thai_now().strftime(
        "%Y-%m-%d"
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

        "🌅 **TRADEIFY NEW DAY**\n"
        f"📅 `{today}`\n"
        "🎯 Target: **2 SET WIN**\n"
        "💰 STEP 1 = 100 บาท\n"
        "💰 STEP 2 = 200 บาท\n"
        "💰 STEP 3 = 300 บาท\n"
        "🔒 1 ORDER / 60 MIN"
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
    # Daily stop
    # --------------------------------------------------------

    if DAILY_STOP:

        return False

    # --------------------------------------------------------
    # Global cooldown
    # --------------------------------------------------------

    if not global_order_available():

        return False

    # --------------------------------------------------------
    # Same symbol lock
    # --------------------------------------------------------

    if symbol in LOCKED_SYMBOLS:

        return False

    # --------------------------------------------------------
    # Any pending trade
    # --------------------------------------------------------

    if PENDING_TRADES:

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

    # --------------------------------------------------------
    # Entry
    # --------------------------------------------------------

    entry = float(
        analysis["entry"]
    )

    expiry = (
        int(timestamp)
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

    send_discord(

        f"🎯 **TRADEIFY CONFIRMED**\n\n"

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

        f"🎯 **SET #{SET_NUMBER}**\n"

        f"🔥 **OPP {step}/3**\n"

        f"💵 Stake: "
        f"`{stake}` บาท\n"

        f"⏱ Expiry: "
        f"`5 นาที`\n\n"

        f"🔒 **LOCKED 60 MIN**"
    )

    return True


# ============================================================
# BOT LOOP
# ============================================================

def bot_loop():

    global LAST_CANDLE

    print(
        "🚀 "
        "TRADEIFY v6.3 "
        "REAL 1-IN-3 "
        "BACKGROUND WORKER STARTED"
    )

    load_state()

    while True:

        try:

            # ------------------------------------------------
            # DAILY RESET
            # ------------------------------------------------

            reset_daily()

            # ------------------------------------------------
            # CHECK RESULT FIRST
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
            # GLOBAL COOLDOWN
            # ------------------------------------------------

            if not global_order_available():

                remaining = (
                    cooldown_remaining()
                )

                print(
                    "[LOCK] "
                    f"Global cooldown "
                    f"{remaining}s"
                )

                time.sleep(
                    SCAN_SECONDS
                )

                continue

            # ------------------------------------------------
            # SET 1-3
            # ------------------------------------------------

            for symbol in SYMBOLS:

                # One pending order only
                if PENDING_TRADES:

                    break

                candles = fetch_market(
                    symbol
                )

                if len(candles) < (
                    MIN_1M_CANDLES
                ):

                    continue

                # ------------------------------------------------
                # CLOSED CANDLE LOCK
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

                # ------------------------------------------------
                # EARLY
                # ------------------------------------------------

                if analysis[
                    "early"
                ]:

                    early_key = (

                        analysis[
                            "symbol"
                        ],

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
                            f"**EARLY WARNING**\n"
                            f"`{symbol}` → "
                            f"**{analysis['direction']}**\n"
                            f"Score: "
                            f"`{analysis['score']}`\n"
                            f"Edge: "
                            f"`+{analysis['edge']}`\n"
                            f"Zone: "
                            f"`{analysis['zone']}`\n"
                            f"⚠️ ยังไม่ใช่ออเดอร์"
                        )

                # ------------------------------------------------
                # CONFIRMED
                # ------------------------------------------------

                if analysis[
                    "confirmed"
                ]:

                    confirmed_key = (

                        analysis[
                            "symbol"
                        ],

                        analysis[
                            "timestamp"
                        ],

                        analysis[
                            "direction"
                        ]
                    )

                    # Absolute duplicate lock
                    if (
                        LAST_CONFIRMED.get(
                            symbol
                        )
                        ==
                        confirmed_key
                    ):

                        continue

                    # Create trade
                    created = create_trade(
                        analysis
                    )

                    if created:

                        break

            save_state()

            time.sleep(
                SCAN_SECONDS
            )

        except Exception as e:

            print(
                "[LOOP ERROR]",
                repr(e)
            )

            time.sleep(5)


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    load_state()

    worker = Thread(
        target=bot_loop,
        daemon=True
    )

    worker.start()

    port = int(
        os.environ.get(
            "PORT",
            "5000"
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

        use_reloader=False
    )
