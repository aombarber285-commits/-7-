# -*- coding: utf-8 -*-

"""
============================================================
TRADEIFY A+ MTF SNIPER V7.1
============================================================

15M = MASTER
5M  = CONFIRM
1M  = ENTRY / REJECTION

MODE      = RUN ALL DAY
STOP      = NONE
STEP      = 100 / 200 / 300
EXPIRY    = 5 MIN

IMPORTANT:
- ใช้ CLOSED CANDLES เท่านั้น
- ไม่มี signal จากแท่งที่ยังไม่ปิด
- 1 pending trade ต่อครั้ง
- Step 1 -> 2 -> 3 เฉพาะเมื่อ LOSS
- WIN -> reset Step 1
- ไม่หยุดหลัง 2 SET WIN
- Flask + worker อยู่ process เดียว
- Worker ไม่สร้างซ้ำ
- Scanner error ไม่ทำให้ process ตาย
- มี heartbeat
- Yahoo FX data เป็น public FX data
- ไม่ใช่ OTC ของ 8xTrade
============================================================
"""

import os
import json
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from statistics import mean
from threading import Thread, Lock

from flask import Flask, jsonify


# ============================================================
# APP
# ============================================================

app = Flask(__name__)


# ============================================================
# CONFIG
# ============================================================

BOT_NAME = "TRADEIFY A+ MTF SNIPER V7.1"

PORT = int(
    os.environ.get(
        "PORT",
        "8080"
    )
)

SCAN_SECONDS = 10

HEARTBEAT_SECONDS = 60

EXPIRY_SECONDS = 300

MIN_1M_CANDLES = 100

MIN_SCORE = 82

MIN_GAP = 12

SR_PERIOD = 100

STAKE_BY_STEP = {
    1: 100,
    2: 200,
    3: 300
}

MAX_STEP = 3

STATE_FILE = os.environ.get(
    "TRADEIFY_STATE_FILE",
    "tradeify_v71_state.json"
)

DISCORD_WEBHOOK_URL = os.environ.get(
    "DISCORD_WEBHOOK_URL",
    ""
).strip()

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

STATE_LOCK = Lock()

CURRENT_DAY = None

CURRENT_STEP = 1

SET_ACTIVE = False

SET_NUMBER = 0

LAST_SIGNAL_TIME = 0

LAST_HEARTBEAT = 0

LAST_CANDLE = {}

LAST_SIGNAL = {}

PENDING_TRADES = {}

DAILY = {
    "signals": 0,
    "wins": 0,
    "losses": 0,
    "void": 0
}


# ============================================================
# FLASK ROUTES
# ============================================================

@app.route("/")
def home():

    return jsonify({
        "status": "online",
        "bot": BOT_NAME,
        "mode": "RUN ALL DAY",
        "stop": "NONE",
        "step": CURRENT_STEP,
        "set": SET_NUMBER,
        "pending": len(PENDING_TRADES)
    })


@app.route("/health")
def health():

    return jsonify({
        "status": "running",
        "bot": BOT_NAME,
        "day": CURRENT_DAY,
        "mode": "RUN ALL DAY",
        "stop": "NONE",
        "step": CURRENT_STEP,
        "set_number": SET_NUMBER,
        "set_active": SET_ACTIVE,
        "signals": DAILY["signals"],
        "wins": DAILY["wins"],
        "losses": DAILY["losses"],
        "void": DAILY["void"],
        "pending": len(PENDING_TRADES),
        "worker": "ONLINE"
    })


# ============================================================
# TIME
# ============================================================

def now_thai():

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
# SAFE STATE
# ============================================================

def save_state():

    try:

        with STATE_LOCK:

            state = {
                "current_day": CURRENT_DAY,
                "current_step": CURRENT_STEP,
                "set_active": SET_ACTIVE,
                "set_number": SET_NUMBER,
                "last_signal_time": LAST_SIGNAL_TIME,
                "last_candle": LAST_CANDLE,
                "last_signal": LAST_SIGNAL,
                "pending_trades": PENDING_TRADES,
                "daily": DAILY
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
    global LAST_SIGNAL_TIME
    global LAST_CANDLE
    global LAST_SIGNAL
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

            data = json.load(f)

        CURRENT_DAY = data.get(
            "current_day"
        )

        CURRENT_STEP = int(
            data.get(
                "current_step",
                1
            )
        )

        SET_ACTIVE = bool(
            data.get(
                "set_active",
                False
            )
        )

        SET_NUMBER = int(
            data.get(
                "set_number",
                0
            )
        )

        LAST_SIGNAL_TIME = int(
            data.get(
                "last_signal_time",
                0
            )
        )

        LAST_CANDLE = data.get(
            "last_candle",
            {}
        )

        LAST_SIGNAL = data.get(
            "last_signal",
            {}
        )

        PENDING_TRADES = data.get(
            "pending_trades",
            {}
        )

        saved_daily = data.get(
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

        print(
            "⚠️ Starting clean state"
        )


# ============================================================
# DISCORD
# ============================================================

def discord(message):

    if not DISCORD_WEBHOOK_URL:

        return

    try:

        payload = json.dumps({
            "content": message
        }).encode("utf-8")

        request = urllib.request.Request(
            DISCORD_WEBHOOK_URL,
            data=payload,
            headers={
                "Content-Type":
                    "application/json",
                "User-Agent":
                    "TRADEIFY-V7.1"
            },
            method="POST"
        )

        urllib.request.urlopen(
            request,
            timeout=10
        )

    except Exception as e:

        print(
            "[DISCORD ERROR]",
            repr(e)
        )


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
            raw.decode("utf-8")
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

        candles = []

        for i, ts in enumerate(
            timestamps
        ):

            try:

                op = quote["open"][i]
                hi = quote["high"][i]
                lo = quote["low"][i]
                cl = quote["close"][i]

                if None in (
                    op,
                    hi,
                    lo,
                    cl
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

        # Remove current open minute
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
            f"{symbol}: "
            f"{repr(e)}"
        )

        return []


# ============================================================
# RESAMPLE
# ============================================================

def resample(candles, minutes):

    if not candles:

        return []

    size = minutes * 60

    buckets = {}

    for candle in candles:

        bucket = (
            candle["timestamp"]
            // size
        )

        buckets.setdefault(
            bucket,
            []
        ).append(
            candle
        )

    result = []

    for bucket in sorted(
        buckets
    ):

        group = buckets[
            bucket
        ]

        group.sort(
            key=lambda x:
                x["timestamp"]
        )

        if len(group) != minutes:

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
# EMA
# ============================================================

def ema(values, period):

    if len(values) < period:

        return None

    value = mean(
        values[:period]
    )

    multiplier = (
        2 /
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

def rsi(candles, period=14):

    if len(candles) <= period:

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
            max(change, 0)
        )

        losses.append(
            max(-change, 0)
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

def structure(candles):

    if len(candles) < 10:

        return "RANGE"

    recent = candles[-10:]

    first = recent[:5]
    last = recent[5:]

    fh = mean(
        x["high"]
        for x in first
    )

    sh = mean(
        x["high"]
        for x in last
    )

    fl = mean(
        x["low"]
        for x in first
    )

    sl = mean(
        x["low"]
        for x in last
    )

    fc = mean(
        x["close"]
        for x in first
    )

    sc = mean(
        x["close"]
        for x in last
    )

    if (
        sh > fh
        and
        sl > fl
        and
        sc > fc
    ):

        return "CALL"

    if (
        sh < fh
        and
        sl < fl
        and
        sc < fc
    ):

        return "PUT"

    return "RANGE"


# ============================================================
# S/R
# ============================================================

def sr(candles):

    if len(candles) < SR_PERIOD:

        return (
            "MID",
            None,
            None
        )

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

    price = candles[-1][
        "close"
    ]

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

def candle_features(c):

    rng = max(
        c["high"] - c["low"],
        1e-12
    )

    body = abs(
        c["close"] -
        c["open"]
    )

    upper = (
        c["high"]
        -
        max(
            c["open"],
            c["close"]
        )
    )

    lower = (
        min(
            c["open"],
            c["close"]
        )
        -
        c["low"]
    )

    return {

        "bull":
            c["close"] > c["open"],

        "bear":
            c["close"] < c["open"],

        "body_ratio":
            body / rng,

        "upper_ratio":
            upper / rng,

        "lower_ratio":
            lower / rng
    }


# ============================================================
# A+ ANALYSIS
# ============================================================

def analyze(symbol, candles):

    if len(candles) < MIN_1M_CANDLES:

        return None

    c1 = resample(
        candles,
        1
    )

    c5 = resample(
        candles,
        5
    )

    c15 = resample(
        candles,
        15
    )

    if len(c5) < 25:
        return None

    if len(c15) < 15:
        return None

    # --------------------------------------------------------
    # 15M MASTER
    # --------------------------------------------------------

    p15 = [
        x["close"]
        for x in c15
    ]

    e15_9 = ema(
        p15,
        9
    )

    e15_21 = ema(
        p15,
        21
    )

    if (
        e15_9 is None
        or
        e15_21 is None
    ):

        return None

    s15 = structure(
        c15
    )

    trend15_call = (
        s15 == "CALL"
        and
        e15_9 > e15_21
    )

    trend15_put = (
        s15 == "PUT"
        and
        e15_9 < e15_21
    )

    # --------------------------------------------------------
    # 5M CONFIRM
    # --------------------------------------------------------

    p5 = [
        x["close"]
        for x in c5
    ]

    e5_9 = ema(
        p5,
        9
    )

    e5_21 = ema(
        p5,
        21
    )

    if (
        e5_9 is None
        or
        e5_21 is None
    ):

        return None

    s5 = structure(
        c5
    )

    trend5_call = (
        s5 == "CALL"
        and
        e5_9 > e5_21
    )

    trend5_put = (
        s5 == "PUT"
        and
        e5_9 < e5_21
    )

    # --------------------------------------------------------
    # 1M ENTRY
    # --------------------------------------------------------

    current = c1[-1]

    prev = c1[-2]

    f = candle_features(
        current
    )

    bull_rejection = (
        f["lower_ratio"] >= 0.25
        and
        f["bull"]
    )

    bear_rejection = (
        f["upper_ratio"] >= 0.25
        and
        f["bear"]
    )

    strong_bull = (
        f["bull"]
        and
        f["body_ratio"] >= 0.45
    )

    strong_bear = (
        f["bear"]
        and
        f["body_ratio"] >= 0.45
    )

    # --------------------------------------------------------
    # EMA 1M
    # --------------------------------------------------------

    p1 = [
        x["close"]
        for x in c1
    ]

    e1_9 = ema(
        p1,
        9
    )

    e1_21 = ema(
        p1,
        21
    )

    e1_50 = ema(
        p1,
        50
    )

    if any(
        x is None
        for x in (
            e1_9,
            e1_21,
            e1_50
        )
    ):

        return None

    ema_up = (
        e1_9 > e1_21
        and
        e1_21 > e1_50
        and
        e1_9 > e1_9
        if False
        else
        (
            e1_9 > e1_21
            and
            e1_21 > e1_50
        )
    )

    ema_down = (
        e1_9 < e1_21
        and
        e1_21 < e1_50
    )

    # --------------------------------------------------------
    # FLOW
    # --------------------------------------------------------

    flow_up = (
        c1[-1]["close"]
        >
        c1[-2]["close"]
        >
        c1[-3]["close"]
        >
        c1[-4]["close"]
    )

    flow_down = (
        c1[-1]["close"]
        <
        c1[-2]["close"]
        <
        c1[-3]["close"]
        <
        c1[-4]["close"]
    )

    # --------------------------------------------------------
    # RSI
    # --------------------------------------------------------

    r = rsi(
        c1,
        14
    )

    if r is None:

        return None

    # --------------------------------------------------------
    # S/R
    # --------------------------------------------------------

    zone, support, resistance = sr(
        c1
    )

    distance = max(
        (resistance - support)
        if support is not None
        and resistance is not None
        else 1,
        1e-12
    )

    room_call = (
        (resistance - current["close"])
        / distance
        if resistance is not None
        else 0
    )

    room_put = (
        (current["close"] - support)
        / distance
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
        current["low"] <= e1_9
        and
        current["close"] > e1_9
    )

    pullback_put = (
        current["high"] >= e1_9
        and
        current["close"] < e1_9
    )

    # --------------------------------------------------------
    # EXHAUSTION
    # --------------------------------------------------------

    overextended_call = (
        r >= 70
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
        r <= 30
        or
        (
            f["bear"]
            and
            f["body_ratio"] >= 0.78
            and
            f["lower_ratio"] <= 0.08
        )
    )

    # --------------------------------------------------------
    # SCORE
    # --------------------------------------------------------

    call = 0
    put = 0

    # 15M
    if trend15_call:
        call += 30

    if trend15_put:
        put += 30

    # 5M
    if trend5_call:
        call += 25

    if trend5_put:
        put += 25

    # EMA
    if ema_up:
        call += 12

    if ema_down:
        put += 12

    # FLOW
    if flow_up:
        call += 10

    if flow_down:
        put += 10

    # REJECTION
    if bull_rejection:
        call += 12

    if bear_rejection:
        put += 12

    # CANDLE
    if strong_bull:
        call += 6

    if strong_bear:
        put += 6

    # RSI
    if (
        30 < r <= 48
    ):

        call += 5

    if (
        52 <= r < 70
    ):

        put += 5

    # S/R
    if zone == "SUPPORT":

        call += 10

    if zone == "RESISTANCE":

        put += 10

    # PULLBACK
    if pullback_call:

        call += 8

    if pullback_put:

        put += 8

    # ROOM
    if enough_room_call:

        call += 5

    if enough_room_put:

        put += 5

    # --------------------------------------------------------
    # PENALTIES
    # --------------------------------------------------------

    if zone == "RESISTANCE":

        call -= 15

    if zone == "SUPPORT":

        put -= 15

    if overextended_call:

        call -= 20

    if overextended_put:

        put -= 20

    if (
        trend15_call
        and
        trend5_put
    ):

        call -= 25
        put -= 25

    if (
        trend15_put
        and
        trend5_call
    ):

        call -= 25
        put -= 25

    call = max(
        0,
        min(
            100,
            call
        )
    )

    put = max(
        0,
        min(
            100,
            put
        )
    )

    if call > put:

        direction = "CALL"

        score = call

        gap = call - put

    elif put > call:

        direction = "PUT"

        score = put

        gap = put - call

    else:

        return {
            "symbol": symbol,
            "signal": False,
            "reason": "EQUAL SCORE"
        }

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
    # CONFIRMED
    # --------------------------------------------------------

    confirmed_call = (
        major_call
        and
        score >= MIN_SCORE
        and
        gap >= MIN_GAP
        and
        f["bull"]
        and
        bull_rejection
        and
        pullback_call
        and
        flow_up
    )

    confirmed_put = (
        major_put
        and
        score >= MIN_SCORE
        and
        gap >= MIN_GAP
        and
        f["bear"]
        and
        bear_rejection
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

    return {

        "symbol":
            symbol,

        "direction":
            direction,

        "score":
            int(score),

        "call_score":
            int(call),

        "put_score":
            int(put),

        "gap":
            int(gap),

        "confirmed":
            bool(confirmed),

        "entry":
            float(current["close"]),

        "timestamp":
            int(current["timestamp"]),

        "zone":
            zone,

        "rsi":
            float(r),

        "support":
            support,

        "resistance":
            resistance
    }


# ============================================================
# CREATE TRADE
# ============================================================

def create_trade(a):

    global CURRENT_STEP
    global SET_ACTIVE
    global SET_NUMBER
    global LAST_SIGNAL_TIME

    symbol = a["symbol"]

    direction = a["direction"]

    timestamp = a["timestamp"]

    if PENDING_TRADES:

        return False

    if symbol in LAST_SIGNAL:

        if (
            LAST_SIGNAL[symbol]
            ==
            timestamp
        ):

            return False

    if not SET_ACTIVE:

        SET_ACTIVE = True

        SET_NUMBER += 1

        CURRENT_STEP = 1

    step = CURRENT_STEP

    stake = STAKE_BY_STEP[
        step
    ]

    expiry = (
        timestamp
        +
        EXPIRY_SECONDS
    )

    key = (
        f"{symbol}|"
        f"{timestamp}|"
        f"{direction}"
    )

    PENDING_TRADES[key] = {

        "symbol":
            symbol,

        "direction":
            direction,

        "entry":
            a["entry"],

        "expiry":
            expiry,

        "step":
            step,

        "stake":
            stake,

        "set_number":
            SET_NUMBER
    }

    LAST_SIGNAL[
        symbol
    ] = timestamp

    LAST_SIGNAL_TIME = unix_now()

    DAILY["signals"] += 1

    save_state()

    icon = (
        "🟢"
        if direction == "CALL"
        else
        "🔴"
    )

    message = (
        f"{icon} **TRADEIFY A+ CONFIRMED**\n\n"
        f"📌 `{symbol}`\n"
        f"➡️ **{direction}**\n"
        f"📊 Score: `{a['score']}/100`\n"
        f"⚡ Gap: `+{a['gap']}`\n"
        f"📍 Zone: `{a['zone']}`\n"
        f"📈 RSI: `{a['rsi']:.1f}`\n"
        f"💰 Entry: `{a['entry']}`\n"
        f"🎯 SET #{SET_NUMBER}\n"
        f"🔥 STEP {step}/3\n"
        f"💵 Stake: `{stake}` บาท\n"
        f"⏱ Expiry: `5 MIN`\n"
        f"🔄 MODE: `RUN ALL DAY`"
    )

    print("\n" + message + "\n")

    discord(message)

    return True


# ============================================================
# RESULT
# ============================================================

def check_results():

    global CURRENT_STEP
    global SET_ACTIVE

    if not PENDING_TRADES:

        return

    now = unix_now()

    finished = []

    for key, trade in list(
        PENDING_TRADES.items()
    ):

        if now < int(
            trade["expiry"]
        ):

            continue

        candles = fetch_market(
            trade["symbol"]
        )

        if not candles:

            continue

        result_candle = None

        for candle in candles:

            if candle["timestamp"] >= int(
                trade["expiry"]
            ):

                result_candle = candle
                break

        if result_candle is None:

            continue

        entry = float(
            trade["entry"]
        )

        exit_price = float(
            result_candle["close"]
        )

        direction = trade[
            "direction"
        ]

        if exit_price == entry:

            result = "VOID"

        elif (
            direction == "CALL"
            and
            exit_price > entry
        ):

            result = "WIN"

        elif (
            direction == "PUT"
            and
            exit_price < entry
        ):

            result = "WIN"

        else:

            result = "LOSS"

        step = int(
            trade["step"]
        )

        if result == "WIN":

            DAILY["wins"] += 1

            CURRENT_STEP = 1

            SET_ACTIVE = False

        elif result == "LOSS":

            DAILY["losses"] += 1

            if step < MAX_STEP:

                CURRENT_STEP = (
                    step + 1
                )

                SET_ACTIVE = True

            else:

                CURRENT_STEP = 1

                SET_ACTIVE = False

        else:

            DAILY["void"] += 1

            CURRENT_STEP = 1

            SET_ACTIVE = False

        print(
            f"[RESULT] "
            f"{trade['symbol']} "
            f"{direction} "
            f"STEP {step} "
            f"=> {result}"
        )

        discord(
            f"📊 **TRADE RESULT**\n"
            f"📌 `{trade['symbol']}`\n"
            f"➡️ `{direction}`\n"
            f"🎯 STEP `{step}/3`\n"
            f"💰 Entry `{entry}`\n"
            f"🏁 Exit `{exit_price}`\n"
            f"📊 **{result}**\n"
            f"➡️ Next STEP `{CURRENT_STEP}/3`"
        )

        finished.append(
            key
        )

    for key in finished:

        PENDING_TRADES.pop(
            key,
            None
        )

    if finished:

        save_state()


# ============================================================
# DAILY STATE
# ============================================================

def daily_reset():

    global CURRENT_DAY
    global CURRENT_STEP
    global SET_ACTIVE
    global SET_NUMBER
    global LAST_SIGNAL_TIME

    today = now_thai().strftime(
        "%Y-%m-%d"
    )

    if CURRENT_DAY == today:

        return

    CURRENT_DAY = today

    CURRENT_STEP = 1

    SET_ACTIVE = False

    SET_NUMBER = 0

    LAST_SIGNAL_TIME = 0

    LAST_CANDLE.clear()

    LAST_SIGNAL.clear()

    PENDING_TRADES.clear()

    for key in DAILY:

        DAILY[key] = 0

    save_state()

    print(
        f"🌅 NEW DAY: {today}"
    )


# ============================================================
# SCANNER
# ============================================================

def scan_once():

    print(
        f"\n🔎 SCAN "
        f"{now_thai().strftime('%H:%M:%S')} "
        f"| pending={len(PENDING_TRADES)} "
        f"| step={CURRENT_STEP}"
    )

    for symbol in SYMBOLS:

        try:

            candles = fetch_market(
                symbol
            )

            if len(candles) < MIN_1M_CANDLES:

                print(
                    f"[DATA] {symbol} "
                    f"insufficient="
                    f"{len(candles)}"
                )

                continue

            latest = candles[-1][
                "timestamp"
            ]

            if (
                LAST_CANDLE.get(
                    symbol
                )
                ==
                latest
            ):

                continue

            LAST_CANDLE[
                symbol
            ] = latest

            a = analyze(
                symbol,
                candles
            )

            if not a:

                continue

            if "signal" in a:

                continue

            print(
                f"[CHECK] {symbol} "
                f"CALL={a['call_score']} "
                f"PUT={a['put_score']} "
                f"DIR={a['direction']} "
                f"GAP={a['gap']}"
            )

            if a["confirmed"]:

                create_trade(
                    a
                )

                break

        except Exception as e:

            print(
                f"[SCANNER ERROR] "
                f"{symbol}: "
                f"{repr(e)}"
            )

            # สำคัญ:
            # error ของคู่เดียว
            # ห้ามทำให้ worker ตาย
            continue


# ============================================================
# WORKER
# ============================================================

def bot_worker():

    global LAST_HEARTBEAT

    print(
        "=========================================="
    )

    print(
        "🚀 TRADEIFY A+ MTF SNIPER V7.1"
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
        "MODE      = RUN ALL DAY"
    )

    print(
        "STOP      = NONE"
    )

    print(
        "=========================================="
    )

    print(
        "ℹ️ Loading state..."
    )

    load_state()

    print(
        "=========================================="
    )

    print(
        "✅ Bot worker started"
    )

    print(
        "=========================================="
    )

    while True:

        loop_started = time.time()

        try:

            daily_reset()

            check_results()

            scan_once()

            LAST_HEARTBEAT = unix_now()

            save_state()

            elapsed = (
                time.time()
                -
                loop_started
            )

            sleep_for = max(
                1,
                SCAN_SECONDS
                -
                elapsed
            )

            time.sleep(
                sleep_for
            )

        except Exception as e:

            # ------------------------------------------------
            # CRITICAL:
            # ห้าม worker ตาย
            # ------------------------------------------------

            print(
                "=========================================="
            )

            print(
                "⚠️ WORKER ERROR"
            )

            print(
                repr(e)
            )

            print(
                "🔄 Worker will restart loop"
            )

            print(
                "=========================================="
            )

            time.sleep(5)


# ============================================================
# STARTUP
# ============================================================

_worker_started = False


def start_worker_once():

    global _worker_started

    if _worker_started:

        print(
            "ℹ️ Worker already running"
        )

        return

    _worker_started = True

    worker = Thread(
        target=bot_worker,
        daemon=True,
        name="TRADEIFY-BOT"
    )

    worker.start()


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":

    print(
        "=========================================="
    )

    print(
        "🚀 STARTING "
        "TRADEIFY V7.1"
    )

    print(
        "=========================================="
    )

    start_worker_once()

    print(
        "🌐 Flask listening on "
        f"0.0.0.0:{PORT}"
    )

    # ปิด reloader เพื่อป้องกัน
    # worker ถูกสร้างซ้ำ

    app.run(
        host="0.0.0.0",
        port=PORT,
        debug=False,
        use_reloader=False,
        threaded=True
    )
