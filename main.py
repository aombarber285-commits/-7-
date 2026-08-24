# -*- coding: utf-8 -*-

"""
SIGZY + TRADEIFY v5 A++ MTF SNIPER
===================================

15M = MASTER TREND
5M  = CONFIRMATION
1M  = ENTRY

ระบบ:
- วิเคราะห์แนวโน้มขึ้น / ลง
- ตรวจ Trend Flow
- EMA 9 / 21 / 50
- RSI 14
- ATR
- Support / Resistance
- Candle rejection
- Momentum
- Sideway filter
- Over-extension filter
- Early Warning
- Confirmed Signal
- 1 signal / candle
- ไม่ใช้ข้อมูล OTC แบบสุ่ม
- นับ WIN / LOSS / VOID จากราคาที่รับมา
- STEP 1 -> 2 -> 3
- Discord notification
- รันต่อเนื่อง

สำคัญ:
OTC_API_URL ต้องคืนข้อมูลแท่งจริงของ OTC
หากไม่มีข้อมูล -> WAIT
ไม่สร้างราคาปลอม
"""

import os
import json
import time
import urllib.request
import urllib.parse
from datetime import datetime, timezone, timedelta
from statistics import mean


# ============================================================
# CONFIG
# ============================================================

DISCORD_WEBHOOK_URL = os.getenv(
    "DISCORD_WEBHOOK_URL",
    "https://discord.com/api/webhooks/1539153931194605662/R7ajA_x3iX6dFyOg97st9U9O4ZmAgD1I6BKgJhmfKO3pvtLAkFdWtvq5aZpWVFPMiYoa"
).strip()

MARKET_MODE = os.getenv(
    "MARKET_MODE",
    "AUTO"
).upper()

OTC_API_URL = os.getenv(
    "OTC_API_URL",
    ""
).strip()

SCAN_SECONDS = int(
    os.getenv("SCAN_SECONDS", "10")
)

MIN_SCORE = 80
MIN_EDGE = 15

SR_LOOKBACK = 120
MIN_1M_CANDLES = 120
MIN_5M_CANDLES = 30
MIN_15M_CANDLES = 20

EXPIRY_SECONDS = 60

STAKE_BY_STEP = {
    1: 100,
    2: 200,
    3: 300
}

SYMBOLS = [
    x.strip()
    for x in os.getenv(
        "SYMBOLS",
        "EUR/USD,GBP/USD,USD/JPY,EUR/JPY"
    ).split(",")
    if x.strip()
]


YAHOO_MAP = {
    "EUR/USD": "EURUSD=X",
    "GBP/USD": "GBPUSD=X",
    "USD/JPY": "JPY=X",
    "EUR/JPY": "EURJPY=X",
    "AUD/USD": "AUDUSD=X",
    "USD/CHF": "CHF=X",
    "USD/CAD": "CAD=X",
    "GBP/JPY": "GBPJPY=X"
}


THAI_TZ = timezone(
    timedelta(hours=7)
)


# ============================================================
# STATE
# ============================================================

CURRENT_DAY = None

CURRENT_STEP = 1
SET_ACTIVE = False
SET_NUMBER = 0

LAST_CANDLE = {}
LAST_EARLY = {}
LAST_CONFIRMED = {}

PENDING_TRADES = {}

DAILY = {
    "signals": 0,
    "wins": 0,
    "losses": 0,
    "void": 0
}

STATS = {
    1: {"WIN": 0, "LOSS": 0, "VOID": 0},
    2: {"WIN": 0, "LOSS": 0, "VOID": 0},
    3: {"WIN": 0, "LOSS": 0, "VOID": 0}
}


# ============================================================
# TIME
# ============================================================

def thai_now():
    return datetime.now(
        timezone.utc
    ).astimezone(THAI_TZ)


def unix_now():
    return int(time.time())


# ============================================================
# HTTP
# ============================================================

def http_json(url, timeout=10):

    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "TRADEIFY-V5",
            "Accept": "application/json"
        }
    )

    with urllib.request.urlopen(
        req,
        timeout=timeout
    ) as response:

        return json.loads(
            response.read().decode()
        )


# ============================================================
# DISCORD
# ============================================================

def send_discord(message):

    if not DISCORD_WEBHOOK_URL:

        print(message)

        return False

    try:

        payload = json.dumps({
            "content": message
        }).encode("utf-8")

        req = urllib.request.Request(
            DISCORD_WEBHOOK_URL,
            data=payload,
            headers={
                "Content-Type":
                    "application/json"
            },
            method="POST"
        )

        with urllib.request.urlopen(
            req,
            timeout=10
        ):

            pass

        return True

    except Exception as e:

        print(
            "[DISCORD ERROR]",
            e
        )

        return False


# ============================================================
# NORMALIZE CANDLE
# ============================================================

def normalize_candle(x):

    if isinstance(x, dict):

        timestamp = x.get(
            "timestamp",
            x.get(
                "time",
                x.get("t")
            )
        )

        op = x.get(
            "open",
            x.get("o")
        )

        hi = x.get(
            "high",
            x.get("h")
        )

        lo = x.get(
            "low",
            x.get("l")
        )

        cl = x.get(
            "close",
            x.get("c")
        )

    elif isinstance(
        x,
        (list, tuple)
    ) and len(x) >= 5:

        timestamp, op, hi, lo, cl = x[:5]

    else:

        return None

    try:

        timestamp = float(timestamp)

        if timestamp > 10_000_000_000:

            timestamp /= 1000

        return {
            "timestamp":
                int(timestamp),

            "open":
                float(op),

            "high":
                float(hi),

            "low":
                float(lo),

            "close":
                float(cl)
        }

    except Exception:

        return None


# ============================================================
# YAHOO LIVE
# ============================================================

def fetch_yahoo(symbol):

    ticker = YAHOO_MAP.get(
        symbol,
        symbol
    )

    url = (
        "https://query1.finance.yahoo.com/"
        "v8/finance/chart/"
        + urllib.parse.quote(
            ticker,
            safe=""
        )
        + "?interval=1m&range=1d"
    )

    try:

        data = http_json(url)

        result = (
            data["chart"]
            ["result"][0]
        )

        timestamps = result.get(
            "timestamp",
            []
        )

        quote = (
            result["indicators"]
            ["quote"][0]
        )

        candles = []

        for i, timestamp in enumerate(
            timestamps
        ):

            try:

                candle = {
                    "timestamp":
                        int(timestamp),

                    "open":
                        float(
                            quote["open"][i]
                        ),

                    "high":
                        float(
                            quote["high"][i]
                        ),

                    "low":
                        float(
                            quote["low"][i]
                        ),

                    "close":
                        float(
                            quote["close"][i]
                        )
                }

                candles.append(
                    candle
                )

            except Exception:

                continue

        cutoff = (
            unix_now() - 60
        )

        candles = [
            x for x in candles
            if x["timestamp"]
            <= cutoff
        ]

        return candles

    except Exception as e:

        print(
            "[YAHOO]",
            symbol,
            e
        )

        return []


# ============================================================
# OTC REAL API
# ============================================================

def fetch_otc(symbol):

    if not OTC_API_URL:

        print(
            "[OTC] "
            "OTC_API_URL ยังไม่ได้ตั้งค่า"
        )

        return []

    try:

        separator = (
            "&"
            if "?" in OTC_API_URL
            else "?"
        )

        url = (
            OTC_API_URL
            + separator
            + urllib.parse.urlencode({
                "symbol": symbol,
                "interval": "1m",
                "limit": 500
            })
        )

        response = http_json(url)

        if isinstance(
            response,
            list
        ):

            raw = response

        elif isinstance(
            response,
            dict
        ):

            raw = response.get(
                "candles",
                response.get(
                    "data",
                    []
                )
            )

        else:

            raw = []

        candles = []

        for item in raw:

            candle = normalize_candle(
                item
            )

            if candle:

                candles.append(
                    candle
                )

        candles.sort(
            key=lambda x:
            x["timestamp"]
        )

        cutoff = (
            unix_now() - 60
        )

        return [
            x for x in candles
            if x["timestamp"]
            <= cutoff
        ]

    except Exception as e:

        print(
            "[OTC ERROR]",
            symbol,
            e
        )

        return []


# ============================================================
# MARKET SELECTOR
# ============================================================

def fetch_market(symbol):

    if MARKET_MODE == "OTC":

        return fetch_otc(
            symbol
        )

    if MARKET_MODE == "LIVE":

        return fetch_yahoo(
            symbol
        )

    # AUTO

    if OTC_API_URL:

        otc = fetch_otc(
            symbol
        )

        if otc:

            return otc

    return fetch_yahoo(
        symbol
    )


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

    for group in buckets.values():

        group.sort(
            key=lambda x:
            x["timestamp"]
        )

        # ต้องมีแท่งครบ
        if len(group) < minutes:

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

    result.sort(
        key=lambda x:
        x["timestamp"]
    )

    return result


# ============================================================
# EMA
# ============================================================

def calculate_ema(values, period):

    if len(values) < period:

        return None

    result = mean(
        values[:period]
    )

    multiplier = (
        2 / (period + 1)
    )

    for value in values[period:]:

        result = (
            value
            * multiplier
            +
            result
            * (1 - multiplier)
        )

    return result


# ============================================================
# RSI
# ============================================================

def calculate_rsi(
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

    avg_gain = mean(
        gains[:period]
    )

    avg_loss = mean(
        losses[:period]
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
            +
            gains[i]
        ) / period

        avg_loss = (
            (
                avg_loss
                * (period - 1)
            )
            +
            losses[i]
        ) / period

    if avg_loss == 0:

        return 100

    rs = (
        avg_gain
        / avg_loss
    )

    return (
        100
        -
        100 / (1 + rs)
    )


# ============================================================
# ATR
# ============================================================

def calculate_atr(
    candles,
    period=14
):

    if len(candles) < period + 1:

        return None

    values = []

    for i in range(
        1,
        len(candles)
    ):

        current = candles[i]
        previous = candles[i - 1]

        tr = max(

            current["high"]
            -
            current["low"],

            abs(
                current["high"]
                -
                previous["close"]
            ),

            abs(
                current["low"]
                -
                previous["close"]
            )
        )

        values.append(tr)

    return mean(
        values[-period:]
    )


# ============================================================
# STRUCTURE
# ============================================================

def market_structure(
    candles,
    period=20
):

    if len(candles) < period:

        return (
            "RANGE",
            0
        )

    data = candles[-period:]

    half = period // 2

    first = data[:half]
    second = data[half:]

    first_high = mean(
        x["high"]
        for x in first
    )

    second_high = mean(
        x["high"]
        for x in second
    )

    first_low = mean(
        x["low"]
        for x in first
    )

    second_low = mean(
        x["low"]
        for x in second
    )

    first_close = mean(
        x["close"]
        for x in first
    )

    second_close = mean(
        x["close"]
        for x in second
    )

    avg_range = mean(
        x["high"] - x["low"]
        for x in data
    )

    if avg_range <= 0:

        return (
            "RANGE",
            0
        )

    if (
        second_high > first_high
        and
        second_low > first_low
        and
        second_close > first_close
    ):

        strength = min(
            1,
            abs(
                second_close
                -
                first_close
            )
            / avg_range
        )

        return (
            "CALL",
            strength
        )

    if (
        second_high < first_high
        and
        second_low < first_low
        and
        second_close < first_close
    ):

        strength = min(
            1,
            abs(
                second_close
                -
                first_close
            )
            / avg_range
        )

        return (
            "PUT",
            strength
        )

    return (
        "RANGE",
        0
    )


# ============================================================
# CANDLE
# ============================================================

def candle_info(candle):

    full = max(
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

    return {

        "bull":
            candle["close"]
            >
            candle["open"],

        "bear":
            candle["close"]
            <
            candle["open"],

        "body":
            body / full,

        "upper":
            upper / full,

        "lower":
            lower / full,

        "range":
            full
    }


# ============================================================
# FLOW
# ============================================================

def price_flow(candles):

    if len(candles) < 5:

        return "RANGE"

    recent = candles[-5:]

    up = sum(
        1
        for x in recent
        if x["close"] > x["open"]
    )

    down = sum(
        1
        for x in recent
        if x["close"] < x["open"]
    )

    if up >= 4:

        return "CALL"

    if down >= 4:

        return "PUT"

    return "RANGE"


# ============================================================
# SUPPORT / RESISTANCE
# ============================================================

def support_resistance(
    candles
):

    if len(candles) < SR_LOOKBACK:

        return None

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

    current = candles[-1]["close"]

    distance = max(
        resistance - support,
        1e-12
    )

    position = (
        current - support
    ) / distance

    if position <= 0.20:

        zone = "SUPPORT"

    elif position >= 0.80:

        zone = "RESISTANCE"

    else:

        zone = "MID"

    return {
        "support": support,
        "resistance": resistance,
        "zone": zone
    }


# ============================================================
# ANALYSIS ENGINE
# ============================================================

def analyze(
    symbol,
    candles_1m
):

    if len(candles_1m) < MIN_1M_CANDLES:

        return None

    candles_5m = resample(
        candles_1m,
        5
    )

    candles_15m = resample(
        candles_1m,
        15
    )

    if (
        len(candles_5m)
        <
        MIN_5M_CANDLES
    ):

        return None

    if (
        len(candles_15m)
        <
        MIN_15M_CANDLES
    ):

        return None

    prices_1m = [
        x["close"]
        for x in candles_1m
    ]

    prices_5m = [
        x["close"]
        for x in candles_5m
    ]

    prices_15m = [
        x["close"]
        for x in candles_15m
    ]

    ema9 = calculate_ema(
        prices_1m,
        9
    )

    ema21 = calculate_ema(
        prices_1m,
        21
    )

    ema50 = calculate_ema(
        prices_1m,
        50
    )

    ema5_9 = calculate_ema(
        prices_5m,
        9
    )

    ema5_21 = calculate_ema(
        prices_5m,
        21
    )

    ema15_9 = calculate_ema(
        prices_15m,
        9
    )

    ema15_21 = calculate_ema(
        prices_15m,
        21
    )

    rsi1 = calculate_rsi(
        prices_1m
    )

    rsi5 = calculate_rsi(
        prices_5m
    )

    rsi15 = calculate_rsi(
        prices_15m
    )

    atr1 = calculate_atr(
        candles_1m
    )

    if any(
        x is None
        for x in [
            ema9,
            ema21,
            ema50,
            ema5_9,
            ema5_21,
            ema15_9,
            ema15_21,
            rsi1,
            rsi5,
            rsi15,
            atr1
        ]
    ):

        return None

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

    flow = price_flow(
        candles_1m
    )

    sr = support_resistance(
        candles_1m
    )

    last = candles_1m[-1]

    candle = candle_info(
        last
    )

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
        ].append(reason)

    # ========================================================
    # 15M MASTER
    # ========================================================

    if structure15 == "CALL":

        add(
            "CALL",
            30,
            "15M Higher High / Higher Low"
        )

    elif structure15 == "PUT":

        add(
            "PUT",
            30,
            "15M Lower High / Lower Low"
        )

    # 15M EMA

    if ema15_9 > ema15_21:

        add(
            "CALL",
            12,
            "15M EMA trend UP"
        )

    elif ema15_9 < ema15_21:

        add(
            "PUT",
            12,
            "15M EMA trend DOWN"
        )

    # ========================================================
    # 15M RSI
    # ========================================================

    if (
        52 <= rsi15 <= 68
    ):

        add(
            "CALL",
            8,
            "15M RSI bullish regime"
        )

    elif (
        32 <= rsi15 <= 48
    ):

        add(
            "PUT",
            8,
            "15M RSI bearish regime"
        )

    # ========================================================
    # 5M CONFIRM
    # ========================================================

    if structure5 == "CALL":

        add(
            "CALL",
            22,
            "5M structure confirms"
        )

    elif structure5 == "PUT":

        add(
            "PUT",
            22,
            "5M structure confirms"
        )

    if ema5_9 > ema5_21:

        add(
            "CALL",
            10,
            "5M EMA momentum UP"
        )

    elif ema5_9 < ema5_21:

        add(
            "PUT",
            10,
            "5M EMA momentum DOWN"
        )

    # ========================================================
    # 1M ENTRY
    # ========================================================

    if flow == "CALL":

        add(
            "CALL",
            8,
            "1M flow UP"
        )

    elif flow == "PUT":

        add(
            "PUT",
            8,
            "1M flow DOWN"
        )

    if (
        ema9 > ema21 > ema50
    ):

        add(
            "CALL",
            8,
            "1M EMA 9>21>50"
        )

    elif (
        ema9 < ema21 < ema50
    ):

        add(
            "PUT",
            8,
            "1M EMA 9<21<50"
        )

    # ========================================================
    # REJECTION
    # ========================================================

    if candle["lower"] >= 0.25:

        add(
            "CALL",
            8,
            "1M lower rejection"
        )

    if candle["upper"] >= 0.25:

        add(
            "PUT",
            8,
            "1M upper rejection"
        )

    # ========================================================
    # SUPPORT / RESISTANCE
    # ========================================================

    zone = (
        sr["zone"]
        if sr
        else "UNKNOWN"
    )

    if zone == "SUPPORT":

        add(
            "CALL",
            10,
            "Major Support"
        )

    elif zone == "RESISTANCE":

        add(
            "PUT",
            10,
            "Major Resistance"
        )

    # ========================================================
    # OVEREXTENSION FILTER
    # ========================================================

    if (
        rsi1 >= 75
        and
        zone == "RESISTANCE"
    ):

        score["CALL"] -= 15

    if (
        rsi1 <= 25
        and
        zone == "SUPPORT"
    ):

        score["PUT"] -= 15

    # ========================================================
    # SIDEWAY FILTER
    # ========================================================

    sideway = (
        structure15 == "RANGE"
        or
        structure5 == "RANGE"
    )

    if sideway:

        score["CALL"] -= 12
        score["PUT"] -= 12

    # ========================================================
    # FINAL SCORE
    # ========================================================

    score["CALL"] = max(
        0,
        min(
            100,
            int(score["CALL"])
        )
    )

    score["PUT"] = max(
        0,
        min(
            100,
            int(score["PUT"])
        )
    )

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

    # ========================================================
    # STRICT MASTER FILTER
    # ========================================================

    master_ok = (
        structure15
        ==
        direction
    )

    confirm_ok = (
        structure5
        ==
        direction
    )

    ema_ok = (

        (
            direction == "CALL"
            and
            ema9 > ema21 > ema50
        )

        or

        (
            direction == "PUT"
            and
            ema9 < ema21 < ema50
        )
    )

    # ========================================================
    # EARLY WARNING
    # ========================================================

    early = (

        master_ok
        and
        confirm_ok
        and
        ema_ok
        and
        score[direction]
        >=
        MIN_SCORE - 6
        and
        edge
        >=
        MIN_EDGE - 3
    )

    # ========================================================
    # CONFIRMED
    # ========================================================

    confirmed = (

        master_ok
        and
        confirm_ok
        and
        ema_ok
        and
        not sideway
        and
        score[direction]
        >=
        MIN_SCORE
        and
        edge
        >=
        MIN_EDGE
    )

    # Require candle agreement

    if direction == "CALL":

        candle_ok = (
            candle["bull"]
            or
            candle["lower"] >= 0.30
        )

    else:

        candle_ok = (
            candle["bear"]
            or
            candle["upper"] >= 0.30
        )

    confirmed = (
        confirmed
        and
        candle_ok
    )

    return {

        "symbol":
            symbol,

        "direction":
            direction,

        "early":
            early,

        "confirmed":
            confirmed,

        "score":
            score[direction],

        "edge":
            edge,

        "entry":
            last["close"],

        "timestamp":
            last["timestamp"],

        "structure15":
            structure15,

        "structure5":
            structure5,

        "flow":
            flow,

        "zone":
            zone,

        "rsi1":
            rsi1,

        "rsi5":
            rsi5,

        "rsi15":
            rsi15,

        "strength15":
            strength15,

        "strength5":
            strength5,

        "reasons":
            reasons[direction]
    }


# ============================================================
# DAILY RESET
# ============================================================

def daily_reset():

    global CURRENT_DAY
    global CURRENT_STEP
    global SET_ACTIVE
    global SET_NUMBER

    today = thai_now().strftime(
        "%Y-%m-%d"
    )

    if today == CURRENT_DAY:

        return

    CURRENT_DAY = today

    CURRENT_STEP = 1

    SET_ACTIVE = False

    SET_NUMBER = 0

    PENDING_TRADES.clear()

    LAST_CANDLE.clear()

    LAST_EARLY.clear()

    LAST_CONFIRMED.clear()

    for key in DAILY:

        DAILY[key] = 0

    for step in STATS:

        for result in STATS[step]:

            STATS[step][result] = 0

    print(
        "🔄 DAILY RESET",
        today
    )


# ============================================================
# START SET
# ============================================================

def start_set():

    global SET_ACTIVE
    global SET_NUMBER
    global CURRENT_STEP

    if not SET_ACTIVE:

        SET_ACTIVE = True

        SET_NUMBER += 1

        CURRENT_STEP = 1

        print(
            f"🆕 SET #{SET_NUMBER}"
        )


# ============================================================
# SEND EARLY
# ============================================================

def send_early(data):

    key = (
        data["symbol"],
        data["timestamp"],
        data["direction"]
    )

    if LAST_EARLY.get(
        data["symbol"]
    ) == key:

        return

    LAST_EARLY[
        data["symbol"]
    ] = key

    icon = (
        "🟡 CALL"
        if data["direction"]
        == "CALL"
        else
        "🟠 PUT"
    )

    message = (

        f"{icon} **EARLY WARNING**\n"

        f"คู่: `{data['symbol']}`\n"

        f"Score: "
        f"`{data['score']}/100`\n"

        f"Edge: "
        f"`+{data['edge']}`\n"

        f"15M: "
        f"`{data['structure15']}`\n"

        f"5M: "
        f"`{data['structure5']}`\n"

        f"Flow: "
        f"`{data['flow']}`\n"

        f"Zone: "
        f"`{data['zone']}`\n\n"

        f"⚠️ **ยังไม่ใช่จุดเข้า**\n"
        f"รอ 1M ยืนยันก่อน"
    )

    send_discord(
        message
    )


# ============================================================
# SEND CONFIRMED
# ============================================================

def send_confirmed(data):

    key = (
        data["symbol"],
        data["timestamp"],
        data["direction"]
    )

    if LAST_CONFIRMED.get(
        data["symbol"]
    ) == key:

        return

    LAST_CONFIRMED[
        data["symbol"]
    ] = key

    start_set()

    global CURRENT_STEP

    step = CURRENT_STEP

    stake = (
        STAKE_BY_STEP[step]
    )

    expiry = (
        data["timestamp"]
        +
        EXPIRY_SECONDS
    )

    trade_key = (
        f"{data['symbol']}:"
        f"{data['timestamp']}:"
        f"{data['direction']}:"
        f"{step}"
    )

    PENDING_TRADES[
        trade_key
    ] = {

        "symbol":
            data["symbol"],

        "direction":
            data["direction"],

        "entry":
            data["entry"],

        "expiry":
            expiry,

        "step":
            step,

        "stake":
            stake
    }

    DAILY["signals"] += 1

    icon = (
        "🟢"
        if data["direction"]
        == "CALL"
        else
        "🔴"
    )

    entry_time = datetime.fromtimestamp(
        data["timestamp"],
        timezone.utc
    ).astimezone(
        THAI_TZ
    )

    reason_text = "\n".join(
        "• " + x
        for x in data["reasons"][:7]
    )

    message = (

        f"🎯 **TRADEIFY A++ CONFIRMED**\n"
        f"━━━━━━━━━━━━━━━━━━\n"

        f"{icon} "
        f"`{data['symbol']}` "
        f"→ **{data['direction']}**\n\n"

        f"🕐 Entry: "
        f"`{entry_time:%H:%M:%S}`\n"

        f"⏳ Expiry: "
        f"`+1 MIN`\n\n"

        f"🧠 Score: "
        f"**{data['score']}/100**\n"

        f"⚡ Edge: "
        f"**+{data['edge']}**\n\n"

        f"15M: "
        f"`{data['structure15']}`\n"

        f"5M: "
        f"`{data['structure5']}`\n"

        f"1M Flow: "
        f"`{data['flow']}`\n"

        f"S/R: "
        f"`{data['zone']}`\n\n"

        f"RSI 15M: "
        f"`{data['rsi15']:.1f}`\n"

        f"RSI 5M: "
        f"`{data['rsi5']:.1f}`\n"

        f"RSI 1M: "
        f"`{data['rsi1']:.1f}`\n\n"

        f"💰 STEP "
        f"**{step}** "
        f"= **{stake} บาท**\n\n"

        f"📌 เหตุผล:\n"
        f"{reason_text}\n\n"

        f"⚠️ ระบบคัดกรอง ไม่ใช่ "
        f"การรับประกันผลชนะ"
    )

    send_discord(
        message
    )


# ============================================================
# RESULT
# ============================================================

def evaluate_trades():

    global CURRENT_STEP
    global SET_ACTIVE

    current_time = unix_now()

    for key, trade in list(
        PENDING_TRADES.items()
    ):

        if (
            current_time
            <
            trade["expiry"]
            + 5
        ):

            continue

        candles = fetch_market(
            trade["symbol"]
        )

        if not candles:

            continue

        candidates = [
            x for x in candles
            if x["timestamp"]
            >=
            trade["expiry"]
        ]

        if not candidates:

            continue

        expiry_price = (
            candidates[0]["close"]
        )

        entry = trade["entry"]

        if expiry_price == entry:

            result = "VOID"

        elif (
            trade["direction"]
            == "CALL"
            and
            expiry_price
            > entry
        ):

            result = "WIN"

        elif (
            trade["direction"]
            == "PUT"
            and
            expiry_price
            < entry
        ):

            result = "WIN"

        else:

            result = "LOSS"

        step = trade["step"]

        STATS[
            step
        ][result] += 1

        if result == "WIN":

            DAILY["wins"] += 1

            SET_ACTIVE = False

            CURRENT_STEP = 1

            status = (
                "🟢 WIN "
                "→ จบชุด"
            )

        elif result == "LOSS":

            DAILY["losses"] += 1

            if step < 3:

                CURRENT_STEP = (
                    step + 1
                )

                status = (
                    f"🔴 LOSS "
                    f"→ STEP "
                    f"{CURRENT_STEP}"
                )

            else:

                SET_ACTIVE = False

                CURRENT_STEP = 1

                status = (
                    "🔴 LOSS 3/3 "
                    "→ จบชุด"
                )

        else:

            DAILY["void"] += 1

            status = (
                "⚪ VOID "
                "→ ไม่เลื่อน Step"
            )

        total = (
            DAILY["wins"]
            +
            DAILY["losses"]
        )

        winrate = (
            (
                DAILY["wins"]
                /
                total
            )
            * 100
            if total
            else 0
        )

        message = (

            f"📊 **TRADE RESULT**\n"
            f"คู่: `{trade['symbol']}`\n"
            f"Direction: "
            f"`{trade['direction']}`\n"
            f"STEP: `{step}`\n\n"

            f"Entry: "
            f"`{entry:.6f}`\n"

            f"Expiry: "
            f"`{expiry_price:.6f}`\n\n"

            f"ผล: **{status}**\n\n"

            f"วันนี้:\n"
            f"🟢 WIN "
            f"`{DAILY['wins']}`\n"
            f"🔴 LOSS "
            f"`{DAILY['losses']}`\n"
            f"⚪ VOID "
            f"`{DAILY['void']}`\n"

            f"📈 Win Rate: "
            f"`{winrate:.2f}%`"
        )

        send_discord(
            message
        )

        del PENDING_TRADES[
            key
        ]


# ============================================================
# SCAN
# ============================================================

def scan_symbol(symbol):

    candles = fetch_market(
        symbol
    )

    if len(candles) < MIN_1M_CANDLES:

        print(
            f"[WAIT] "
            f"{symbol}: "
            f"candles "
            f"{len(candles)}"
        )

        return

    latest_timestamp = (
        candles[-1]["timestamp"]
    )

    # ป้องกันยิงซ้ำแท่งเดิม

    if (
        LAST_CANDLE.get(symbol)
        ==
        latest_timestamp
    ):

        return

    LAST_CANDLE[
        symbol
    ] = latest_timestamp

    analysis = analyze(
        symbol,
        candles
    )

    if not analysis:

        return

    if analysis["early"]:

        send_early(
            analysis
        )

    if not analysis["confirmed"]:

        return

    # ไม่เปิดซ้ำคู่เดิมขณะที่ยังรอผล

    for trade in (
        PENDING_TRADES.values()
    ):

        if (
            trade["symbol"]
            ==
            symbol
        ):

            return

    send_confirmed(
        analysis
    )


# ============================================================
# STATUS
# ============================================================

def print_status():

    total = (
        DAILY["wins"]
        +
        DAILY["losses"]
    )

    winrate = (
        DAILY["wins"]
        /
        total
        *
        100
        if total
        else 0
    )

    print(
        "\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"TIME: "
        f"{thai_now():%Y-%m-%d %H:%M:%S}\n"
        f"SIGNALS: "
        f"{DAILY['signals']}\n"
        f"WIN: "
        f"{DAILY['wins']}\n"
        f"LOSS: "
        f"{DAILY['losses']}\n"
        f"VOID: "
        f"{DAILY['void']}\n"
        f"WINRATE: "
        f"{winrate:.2f}%\n"
        f"STEP: "
        f"{CURRENT_STEP}\n"
        f"PENDING: "
        f"{len(PENDING_TRADES)}\n"
        "━━━━━━━━━━━━━━━━━━━━"
    )


# ============================================================
# MAIN
# ============================================================

def main():

    daily_reset()

    print(
        "======================================"
    )

    print(
        "🚀 TRADEIFY v5 A++ MTF SNIPER"
    )

    print(
        "15M MASTER / 5M CONFIRM / 1M ENTRY"
    )

    print(
        "MARKET:",
        MARKET_MODE
    )

    print(
        "SYMBOLS:",
        SYMBOLS
    )

    print(
        "======================================"
    )

    if MARKET_MODE == "OTC":

        if not OTC_API_URL:

            print(
                "⚠️ OTC_API_URL "
                "ยังไม่ได้ตั้งค่า"
            )

            print(
                "ระบบจะไม่สร้าง "
                "ข้อมูล OTC ปลอม"
            )

    send_discord(
        "🚀 **TRADEIFY v5 A++ STARTED**\n"
        "15M Master → 5M Confirm → 1M Entry\n"
        "ระบบ Tracking + WIN/LOSS จริง"
    )

    while True:

        try:

            daily_reset()

            evaluate_trades()

            for symbol in SYMBOLS:

                try:

                    scan_symbol(
                        symbol
                    )

                except Exception as e:

                    print(
                        "[SCAN ERROR]",
                        symbol,
                        e
                    )

            print_status()

            time.sleep(
                SCAN_SECONDS
            )

        except KeyboardInterrupt:

            print(
                "🛑 BOT STOPPED"
            )

            break

        except Exception as e:

            print(
                "[MAIN ERROR]",
                e
            )

            time.sleep(5)


if __name__ == "__main__":

    main()
