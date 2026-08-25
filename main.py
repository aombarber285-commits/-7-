# -*- coding: utf-8 -*-

"""
============================================================
TRADEIFY A+ MTF SNIPER V8
============================================================

15M = MASTER
5M  = CONFIRM
1M  = ENTRY / REJECTION

A+ FILTER
- MIN SCORE = 82
- MIN GAP   = 12

TRADE
- Expiry = 5 minutes
- Step 1 = 100
- Step 2 = 200
- Step 3 = 300

SET RULE
- WIN STEP 1 -> SET WIN -> STEP 1
- LOSS STEP 1 -> STEP 2
- WIN STEP 2 -> SET WIN -> STEP 1
- LOSS STEP 2 -> STEP 3
- WIN STEP 3 -> SET WIN -> STEP 1
- LOSS STEP 3 -> SET LOSS -> STEP 1

IMPORTANT
- ไม่มี DAILY TARGET
- ไม่มี STOP หลัง 2 SET WIN
- RUN ALL DAY
- 1 pending trade at a time
- Global lock 60 minutes
- Closed candle only
============================================================
"""

import os
import json
import time
import urllib.request
from datetime import datetime, timezone, timedelta
from statistics import mean
from threading import Thread, Lock
from flask import Flask


# ============================================================
# FLASK
# ============================================================

app = Flask(__name__)

STATE_LOCK = Lock()


@app.route("/")
def home():
    return "TRADEIFY A+ MTF SNIPER V8 - RUN ALL DAY"


@app.route("/health")
def health():

    return {
        "status": "running",
        "bot": "TRADEIFY A+ MTF SNIPER V8",
        "mode": "RUN_ALL_DAY",

        "current_day": CURRENT_DAY,

        "set_number": SET_NUMBER,
        "set_active": SET_ACTIVE,

        "step": CURRENT_STEP,

        "set_wins": DAILY["set_wins"],
        "set_losses": DAILY["set_losses"],

        "signals": DAILY["signals"],
        "wins": DAILY["wins"],
        "losses": DAILY["losses"],
        "void": DAILY["void"],

        "pending": len(PENDING_TRADES),

        "global_lock_remaining":
            cooldown_remaining()
    }


# ============================================================
# CONFIG
# ============================================================

DISCORD_WEBHOOK_URL = os.environ.get(
    "DISCORD_WEBHOOK_URL",
    ""
).strip()

PORT = int(
    os.environ.get(
        "PORT",
        "8080"
    )
)

SCAN_SECONDS = 10

# ============================================================
# A+ SETTINGS
# ============================================================

MIN_SCORE = 82
MIN_GAP = 12

# ============================================================
# TIMEFRAME
# ============================================================

TF_1M = 1
TF_5M = 5
TF_15M = 15

# ============================================================
# TRADE
# ============================================================

EXPIRY_SECONDS = 300

ORDER_COOLDOWN_SECONDS = 3600

STAKE_BY_STEP = {
    1: 100,
    2: 200,
    3: 300
}

MAX_STEP = 3

# ============================================================
# DATA
# ============================================================

MIN_1M_CANDLES = 300

SR_LOOKBACK = 100

SYMBOLS = [
    "EUR/USD",
    "GBP/USD",
    "USD/JPY",
    "EUR/JPY",
    "AUD/USD",
    "USD/CHF"
]

STATE_FILE = os.environ.get(
    "TRADEIFY_STATE_FILE",
    "tradeify_state.json"
)

THAI_TZ = timezone(
    timedelta(hours=7)
)


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
# STATE
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

            tmp = STATE_FILE + ".tmp"

            with open(
                tmp,
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
                tmp,
                STATE_FILE
            )

    except Exception as e:

        print(
            "[STATE SAVE ERROR]",
            repr(e)
        )


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

        results = (
            data
            .get("chart", {})
            .get("result")
        )

        if not results:

            return []

        result = results[0]

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

        for i, ts in enumerate(
            timestamps
        ):

            try:

                op = quote["open"][i]
                hi = quote["high"][i]
                lo = quote["low"][i]
                cl = quote["close"][i]

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
            // 60
        ) * 60

        candles = [
            c
            for c in candles
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

def resample(
    candles,
    minutes
):

    if not candles:

        return []

    bucket_seconds = (
        minutes * 60
    )

    buckets = {}

    for candle in candles:

        bucket = (
            candle["timestamp"]
            //
            bucket_seconds
        )

        buckets.setdefault(
            bucket,
            []
        ).append(
            candle
        )

    output = []

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

        # Must be continuous
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

        output.append(
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

    return output


# ============================================================
# EMA
# ============================================================

def ema(
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

def rsi(
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
        avg_gain /
        avg_loss
    )

    return (
        100 -
        100 /
        (1 + rs)
    )


# ============================================================
# STRUCTURE
# ============================================================

def structure(
    candles,
    period=20
):

    if len(candles) < period:

        return "RANGE"

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

    first_high = max(
        x["high"]
        for x in first
    )

    second_high = max(
        x["high"]
        for x in second
    )

    first_low = min(
        x["low"]
        for x in first
    )

    second_low = min(
        x["low"]
        for x in second
    )

    if (
        second_high > first_high
        and
        second_low > first_low
    ):

        return "CALL"

    if (
        second_high < first_high
        and
        second_low < first_low
    ):

        return "PUT"

    return "RANGE"


# ============================================================
# S/R
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

    price = candles[
        -1
    ]["close"]

    distance = max(
        resistance - support,
        1e-12
    )

    position = (
        price - support
    ) / distance

    if position <= 0.18:

        zone = "SUPPORT"

    elif position >= 0.82:

        zone = "RESISTANCE"

    else:

        zone = "MID"

    return (
        zone,
        support,
        resistance
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
        body /
        candle_range
    )

    upper_ratio = (
        upper /
        candle_range
    )

    lower_ratio = (
        lower /
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
        "bull": bull,
        "bear": bear,

        "bull_rejection":
            bull_rejection,

        "bear_rejection":
            bear_rejection,

        "strong_bull":
            strong_bull,

        "strong_bear":
            strong_bear,

        "body_ratio":
            body_ratio,

        "upper_ratio":
            upper_ratio,

        "lower_ratio":
            lower_ratio
    }


# ============================================================
# MAIN A+ ANALYSIS
# ============================================================

def analyze(
    symbol,
    candles
):

    if len(candles) < MIN_1M_CANDLES:

        return None

    c5 = resample(
        candles,
        TF_5M
    )

    c15 = resample(
        candles,
        TF_15M
    )

    if len(c5) < 70:

        return None

    if len(c15) < 30:

        return None

    # --------------------------------------------------------
    # CURRENT CLOSED 1M
    # --------------------------------------------------------

    current = candles[-1]

    previous = candles[-2]

    f = candle_features(
        current
    )

    # --------------------------------------------------------
    # 15M MASTER
    # --------------------------------------------------------

    p15 = [
        x["close"]
        for x in c15
    ]

    ema15_9 = ema(
        p15,
        9
    )

    ema15_21 = ema(
        p15,
        21
    )

    ema15_50 = ema(
        p15,
        50
    )

    if any(
        x is None
        for x in (
            ema15_9,
            ema15_21,
            ema15_50
        )
    ):

        return None

    structure15 = structure(
        c15
    )

    trend15_call = (
        structure15 == "CALL"
        and
        ema15_9 > ema15_21
        and
        ema15_21 > ema15_50
    )

    trend15_put = (
        structure15 == "PUT"
        and
        ema15_9 < ema15_21
        and
        ema15_21 < ema15_50
    )

    # --------------------------------------------------------
    # 5M CONFIRM
    # --------------------------------------------------------

    p5 = [
        x["close"]
        for x in c5
    ]

    ema5_9 = ema(
        p5,
        9
    )

    ema5_21 = ema(
        p5,
        21
    )

    ema5_50 = ema(
        p5,
        50
    )

    if any(
        x is None
        for x in (
            ema5_9,
            ema5_21,
            ema5_50
        )
    ):

        return None

    structure5 = structure(
        c5
    )

    trend5_call = (
        structure5 == "CALL"
        and
        ema5_9 > ema5_21
        and
        ema5_21 > ema5_50
    )

    trend5_put = (
        structure5 == "PUT"
        and
        ema5_9 < ema5_21
        and
        ema5_21 < ema5_50
    )

    # --------------------------------------------------------
    # 1M EMA
    # --------------------------------------------------------

    p1 = [
        x["close"]
        for x in candles
    ]

    ema1_9 = ema(
        p1,
        9
    )

    ema1_21 = ema(
        p1,
        21
    )

    ema1_50 = ema(
        p1,
        50
    )

    if any(
        x is None
        for x in (
            ema1_9,
            ema1_21,
            ema1_50
        )
    ):

        return None

    ema_up = (
        ema1_9 > ema1_21
        and
        ema1_21 > ema1_50
        and
        ema1_9 > ema1_9_previous(
            candles
        )
    )

    ema_down = (
        ema1_9 < ema1_21
        and
        ema1_21 < ema1_50
        and
        ema1_9 < ema1_9_previous(
            candles
        )
    )

    # --------------------------------------------------------
    # RSI
    # --------------------------------------------------------

    rsi1 = rsi(
        candles,
        14
    )

    if rsi1 is None:

        return None

    # --------------------------------------------------------
    # PRICE FLOW
    # --------------------------------------------------------

    flow_up = (
        candles[-1]["close"]
        >
        candles[-2]["close"]
        >
        candles[-3]["close"]
        >
        candles[-4]["close"]
    )

    flow_down = (
        candles[-1]["close"]
        <
        candles[-2]["close"]
        <
        candles[-3]["close"]
        <
        candles[-4]["close"]
    )

    # --------------------------------------------------------
    # S/R
    # --------------------------------------------------------

    zone, support, resistance = (
        support_resistance(c5)
    )

    distance = max(
        (
            resistance - support
        )
        if
        support is not None
        and
        resistance is not None
        else 1,
        1e-12
    )

    room_call = (
        (
            resistance
            -
            current["close"]
        )
        /
        distance
        if resistance is not None
        else 0
    )

    room_put = (
        (
            current["close"]
            -
            support
        )
        /
        distance
        if support is not None
        else 0
    )

    enough_room_call = (
        room_call >= 0.20
    )

    enough_room_put = (
        room_put >= 0.20
    )

    # --------------------------------------------------------
    # PULLBACK
    # --------------------------------------------------------

    pullback_call = (
        current["low"]
        <=
        ema1_9
        and
        current["close"]
        >
        ema1_9
    )

    pullback_put = (
        current["high"]
        >=
        ema1_9
        and
        current["close"]
        <
        ema1_9
    )

    # --------------------------------------------------------
    # EXHAUSTION
    # --------------------------------------------------------

    overextended_call = (
        rsi1 >= 70
        or
        (
            f["bull"]
            and
            f["body_ratio"] >= 0.78
            and
            f["upper_ratio"] <= 0.08
        )
    )

    overextended_put = (
        rsi1 <= 30
        or
        (
            f["bear"]
            and
            f["body_ratio"] >= 0.78
            and
            f["lower_ratio"] <= 0.08
        )
    )

    # ========================================================
    # SCORE
    # ========================================================

    call_score = 0
    put_score = 0

    call_reasons = []
    put_reasons = []

    # 15M MASTER = 30
    if trend15_call:

        call_score += 30
        call_reasons.append(
            "15M MASTER UP"
        )

    if trend15_put:

        put_score += 30
        put_reasons.append(
            "15M MASTER DOWN"
        )

    # 5M CONFIRM = 25
    if trend5_call:

        call_score += 25
        call_reasons.append(
            "5M CONFIRM UP"
        )

    if trend5_put:

        put_score += 25
        put_reasons.append(
            "5M CONFIRM DOWN"
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
    if f["bull_rejection"]:

        call_score += 12
        call_reasons.append(
            "BULL REJECTION"
        )

    if f["bear_rejection"]:

        put_score += 12
        put_reasons.append(
            "BEAR REJECTION"
        )

    # CANDLE = 6
    if f["strong_bull"]:

        call_score += 6
        call_reasons.append(
            "STRONG BULL"
        )

    if f["strong_bear"]:

        put_score += 6
        put_reasons.append(
            "STRONG BEAR"
        )

    # RSI = 5
    if (
        30 < rsi1 <= 48
    ):

        call_score += 5
        call_reasons.append(
            "RSI CALL ZONE"
        )

    if (
        52 <= rsi1 < 70
    ):

        put_score += 5
        put_reasons.append(
            "RSI PUT ZONE"
        )

    # S/R = 10
    if zone == "SUPPORT":

        call_score += 10
        call_reasons.append(
            "NEAR SUPPORT"
        )

    if zone == "RESISTANCE":

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

    # SPACE = 5
    if enough_room_call:

        call_score += 5
        call_reasons.append(
            "CALL HAS ROOM"
        )

    if enough_room_put:

        put_score += 5
        put_reasons.append(
            "PUT HAS ROOM"
        )

    # ========================================================
    # PENALTIES
    # ========================================================

    if zone == "RESISTANCE":

        call_score -= 15

    if zone == "SUPPORT":

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

    # ========================================================
    # DIRECTION
    # ========================================================

    if call_score > put_score:

        direction = "CALL"

    elif put_score > call_score:

        direction = "PUT"

    else:

        return None

    if direction == "CALL":

        score = call_score
        opposite = put_score
        reasons = call_reasons

    else:

        score = put_score
        opposite = call_score
        reasons = put_reasons

    gap = score - opposite

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
        call_score >= MIN_SCORE - 8
        and
        (
            call_score - put_score
        ) >= MIN_GAP - 3
        and
        (
            pullback_call
            or
            f["bull_rejection"]
            or
            rsi1 <= 48
        )
    )

    early_put = (
        major_put
        and
        put_score >= MIN_SCORE - 8
        and
        (
            put_score - call_score
        ) >= MIN_GAP - 3
        and
        (
            pullback_put
            or
            f["bear_rejection"]
            or
            rsi1 >= 52
        )
    )

    # ========================================================
    # CONFIRMED
    # ========================================================

    confirmed_call = (
        major_call
        and
        call_score >= MIN_SCORE
        and
        (
            call_score - put_score
        ) >= MIN_GAP
        and
        f["bull"]
        and
        f["bull_rejection"]
        and
        pullback_call
        and
        flow_up
    )

    confirmed_put = (
        major_put
        and
        put_score >= MIN_SCORE
        and
        (
            put_score - call_score
        ) >= MIN_GAP
        and
        f["bear"]
        and
        f["bear_rejection"]
        and
        pullback_put
        and
        flow_down
    )

    confirmed = (
        confirmed_call
        if direction == "CALL"
        else confirmed_put
    )

    early = (
        early_call
        if direction == "CALL"
        else early_put
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
            early,

        "confirmed":
            confirmed,

        "entry":
            current["close"],

        "timestamp":
            current["timestamp"],

        "zone":
            zone,

        "rsi":
            rsi1,

        "support":
            support,

        "resistance":
            resistance,

        "reasons":
            reasons
    }


# ============================================================
# EMA PREVIOUS
# ============================================================

def ema1_9_previous(
    candles
):

    if len(candles) < 20:

        return 0

    values = [
        x["close"]
        for x in candles[:-1]
    ]

    return ema(
        values,
        9
    ) or 0


# ============================================================
# COOLDOWN
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
        "♾️ **RUN ALL DAY**\n"
        "🛑 ไม่มี DAILY STOP\n"
        "🎯 A+ Score ≥ 82\n"
        "⚡ Gap ≥ 12\n"
        "💰 STEP 1 = 100\n"
        "💰 STEP 2 = 200\n"
        "💰 STEP 3 = 300\n"
        "🔒 Global Lock = 60 MIN"
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

    symbol = analysis["symbol"]

    direction = analysis["direction"]

    timestamp = analysis["timestamp"]

    # --------------------------------------------------------
    # GLOBAL LOCK
    # --------------------------------------------------------

    if not global_order_available():

        return False

    # --------------------------------------------------------
    # EXISTING TRADE
    # --------------------------------------------------------

    if PENDING_TRADES:

        return False

    # --------------------------------------------------------
    # SYMBOL LOCK
    # --------------------------------------------------------

    if symbol in LOCKED_SYMBOLS:

        return False

    # --------------------------------------------------------
    # NEW SET
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
        f"S{step}"
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

    send_discord(

        "🎯 **TRADEIFY A+ CONFIRMED**\n\n"

        f"{icon} `{symbol}`\n"

        f"➡️ **{direction}**\n"

        f"📊 Score: "
        f"`{analysis['score']}/100`\n"

        f"⚡ Gap: "
        f"`+{analysis['gap']}`\n"

        f"📍 Zone: "
        f"`{analysis['zone']}`\n"

        f"📈 RSI: "
        f"`{analysis['rsi']:.1f}`\n"

        f"💰 Entry: "
        f"`{entry}`\n"

        f"🎯 SET: "
        f"`#{SET_NUMBER}`\n"

        f"🔥 OPP: "
        f"`{step}/3`\n"

        f"💵 Stake: "
        f"`{stake}` บาท\n"

        f"⏱ Expiry: "
        f"`5 MIN`\n\n"

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

    for key, trade in list(
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

        # ====================================================
        # WIN
        # ====================================================

        if result == "WIN":

            DAILY["wins"] += 1

            DAILY["set_wins"] += 1

            SET_ACTIVE = False

            CURRENT_STEP = 1

            send_discord(

                "✅ **TRADE RESULT — WIN**\n"
                f"📌 `{symbol}`\n"
                f"➡️ `{direction}`\n"
                f"🎯 SET `#{set_number}`\n"
                f"🔥 STEP `{step}/3`\n"
                f"💵 Stake `{stake}` บาท\n"
                f"💰 Entry `{entry}`\n"
                f"🏁 Exit `{exit_price}`\n\n"
                f"🏆 **SET WIN**\n"
                f"🔄 Next SET → STEP 1\n"
                f"♾️ **BOT CONTINUES ALL DAY**"
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

                next_stake = (
                    STAKE_BY_STEP[
                        CURRENT_STEP
                    ]
                )

                send_discord(

                    "❌ **TRADE RESULT — LOSS**\n"
                    f"📌 `{symbol}`\n"
                    f"➡️ `{direction}`\n"
                    f"🎯 SET `#{set_number}`\n"
                    f"🔥 STEP `{step}/3`\n"
                    f"💰 Entry `{entry}`\n"
                    f"🏁 Exit `{exit_price}`\n\n"
                    f"🔁 **NEXT STEP "
                    f"{CURRENT_STEP}/3**\n"
                    f"💵 Stake `{next_stake}` บาท"
                )

            else:

                DAILY["set_losses"] += 1

                SET_ACTIVE = False

                CURRENT_STEP = 1

                send_discord(

                    "❌ **SET LOSS**\n"
                    f"📌 `{symbol}`\n"
                    f"➡️ `{direction}`\n"
                    f"🎯 SET `#{set_number}`\n"
                    f"🔥 STEP `3/3`\n"
                    f"💰 Entry `{entry}`\n"
                    f"🏁 Exit `{exit_price}`\n\n"
                    f"💀 **SET #{set_number} LOSS**\n"
                    f"🔄 New SET → STEP 1\n"
                    f"♾️ **BOT CONTINUES ALL DAY**"
                )

        # ====================================================
        # VOID
        # ====================================================

        else:

            DAILY["void"] += 1

            SET_ACTIVE = False

            CURRENT_STEP = 1

            send_discord(

                "⚪ **TRADE RESULT — VOID**\n"
                f"📌 `{symbol}`\n"
                f"➡️ `{direction}`\n"
                f"🎯 SET `#{set_number}`\n"
                f"🔥 STEP `{step}/3`\n"
                f"💰 Entry `{entry}`\n"
                f"🏁 Exit `{exit_price}`\n\n"
                f"🔄 Reset → STEP 1"
            )

        LOCKED_SYMBOLS.pop(
            symbol,
            None
        )

        completed.append(
            key
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
        "🚀 TRADEIFY A+ MTF SNIPER V8"
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

            # ------------------------------------------------
            # RESET DAY
            # ------------------------------------------------

            reset_daily()

            # ------------------------------------------------
            # RESULT FIRST
            # ------------------------------------------------

            check_pending_trades()

            # ------------------------------------------------
            # IF TRADE EXISTS
            # ------------------------------------------------

            if PENDING_TRADES:

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
                    f"{remaining}s remaining"
                )

                time.sleep(
                    SCAN_SECONDS
                )

                continue

            # ------------------------------------------------
            # SCAN
            # ------------------------------------------------

            for symbol in SYMBOLS:

                if PENDING_TRADES:

                    break

                if symbol in LOCKED_SYMBOLS:

                    continue

                candles = fetch_market(
                    symbol
                )

                if len(candles) < (
                    MIN_1M_CANDLES
                ):

                    continue

                latest_timestamp = (
                    candles[-1][
                        "timestamp"
                    ]
                )

                # ------------------------------------------------
                # CLOSED CANDLE DUPLICATE LOCK
                # ------------------------------------------------

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

                # =================================================
                # EARLY
                # =================================================

                if analysis["early"]:

                    early_key = (
                        analysis["timestamp"],
                        analysis["direction"]
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
                            f"**A+ EARLY WARNING**\n"
                            f"`{symbol}` → "
                            f"**{analysis['direction']}**\n"
                            f"Score: "
                            f"`{analysis['score']}`\n"
                            f"Gap: "
                            f"`+{analysis['gap']}`\n"
                            f"Zone: "
                            f"`{analysis['zone']}`\n"
                            f"⚠️ "
                            f"**ยังไม่ใช่ออเดอร์**"
                        )

                # =================================================
                # CONFIRMED
                # =================================================

                if not analysis["confirmed"]:

                    continue

                confirmed_key = (
                    analysis["timestamp"],
                    analysis["direction"]
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
# START
# ============================================================

if __name__ == "__main__":

    load_state()

    worker = Thread(
        target=bot_loop,
        daemon=True
    )

    worker.start()

    print(
        f"🌐 Flask listening "
        f"on 0.0.0.0:{PORT}"
    )

    app.run(
        host="0.0.0.0",
        port=PORT,
        debug=False,
        use_reloader=False
    )
