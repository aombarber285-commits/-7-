# -*- coding: utf-8 -*-

"""
SIGZY + TRADEIFY v3
===================

ระบบวิเคราะห์:
    15M  = Master Trend / Structure
    5M   = Momentum Confirmation
    1M   = Entry Trigger / Rejection
    50-100 candles = Major Support / Resistance
    Candle Strength
    Market State

ระบบติดตาม:
    STEP 1 = 100
    STEP 2 = 200
    STEP 3 = 300

กติกาชุด:
    WIN STEP 1 -> จบชุด
    LOSS STEP 1 -> STEP 2
    WIN STEP 2 -> จบชุด
    LOSS STEP 2 -> STEP 3
    WIN STEP 3 -> จบชุด
    LOSS STEP 3 -> จบชุด LOSS

เป้าหมาย:
    2 ชุด WIN / วัน -> หยุดส่งสัญญาณ

สำคัญ:
    - ใช้แท่งที่ปิดแล้วเท่านั้น
    - ไม่ตัดสิน WIN/LOSS ก่อน expiry
    - OTC เป็นข้อมูล synthetic สำหรับทดสอบโค้ดเท่านั้น
    - ห้ามนำสถิติ OTC ไปสรุปว่าเป็น win rate ของตลาดจริง
"""

import json
import time
import random
import urllib.request
from datetime import datetime, timedelta, timezone
from statistics import mean


# ============================================================
# CONFIG
# ============================================================

# ใส่ WEBHOOK ใหม่ของคุณตรงนี้
# อย่าใช้ Webhook ที่เคยเปิดเผยในแชต
DISCORD_WEBHOOK_URL = "https://discord.com/api/webhooks/1535993581414653973/g9d6Ma96SKD32EgcQs4oFoOc-gqd7vDqPNgpyN53BrJPMwImxQqKDqyDwWm6iJSbwOjD"
SYMBOL_MAP = {
    "EUR/JPY": "EURJPY=X",
    "EUR/USD": "EURUSD=X",
    "GBP/USD": "GBPUSD=X",
    "USD/JPY": "JPY=X",
}

SCAN_SECONDS = 60

# ============================================================
# SIGNAL SETTINGS
# ============================================================

STRUCTURE_LOOKBACK = 100
MIN_STRUCTURE_CANDLES = 50

PAIR_LOCK_MINUTES = 15

# คะแนนขั้นต่ำสำหรับ TRADE
TRADE_SCORE = 65

# คะแนน WAIT
WAIT_SCORE = 50

# ต้องการชนะกี่ชุดต่อวัน
DAILY_WIN_TARGET = 2

# ============================================================
# MONEY MANAGEMENT - SIMULATION ONLY
# ============================================================

STAKE_BY_STEP = {
    1: 100,
    2: 200,
    3: 300,
}

MAX_STEP = 3


# ============================================================
# STATE
# ============================================================

SENT_SIGNALS = set()

PAIR_LOCKED_UNTIL = {}

PENDING_TRADES = {}

OTC_HISTORY = {}

CURRENT_DAY = None

DAILY_SIGNAL_COUNT = 0

DAILY_WIN_COUNT = 0

DAILY_LOSS_COUNT = 0

CURRENT_STEP = 1

SET_NUMBER = 0

SET_ACTIVE = False

SET_LOSS_COUNT = 0

TRADE_STATS = {
    1: {"WIN": 0, "LOSS": 0},
    2: {"WIN": 0, "LOSS": 0},
    3: {"WIN": 0, "LOSS": 0},
}


# ============================================================
# TIME
# ============================================================

def get_thai_time(dt=None):

    if dt is None:
        dt = datetime.now(timezone.utc)

    return dt.astimezone(
        timezone(timedelta(hours=7))
    )


def get_utc_now():

    return datetime.now(timezone.utc)


def is_weekend():

    return get_thai_time().weekday() in [5, 6]


# ============================================================
# DAILY RESET
# ============================================================

def check_daily_reset():

    global CURRENT_DAY
    global DAILY_SIGNAL_COUNT
    global DAILY_WIN_COUNT
    global DAILY_LOSS_COUNT
    global CURRENT_STEP
    global SET_NUMBER
    global SET_ACTIVE
    global SET_LOSS_COUNT
    global SENT_SIGNALS
    global PENDING_TRADES
    global PAIR_LOCKED_UNTIL

    today = get_thai_time().strftime("%Y-%m-%d")

    if CURRENT_DAY != today:

        CURRENT_DAY = today

        DAILY_SIGNAL_COUNT = 0
        DAILY_WIN_COUNT = 0
        DAILY_LOSS_COUNT = 0

        CURRENT_STEP = 1

        SET_NUMBER = 0

        SET_ACTIVE = False

        SET_LOSS_COUNT = 0

        SENT_SIGNALS.clear()

        PENDING_TRADES.clear()

        PAIR_LOCKED_UNTIL.clear()

        for step in TRADE_STATS:

            TRADE_STATS[step]["WIN"] = 0
            TRADE_STATS[step]["LOSS"] = 0

        print("🔄 Daily reset")


# ============================================================
# DISCORD
# ============================================================

def send_discord(message):

    if not DISCORD_WEBHOOK_URL.strip():

        print("⚠️ Discord Webhook ยังไม่ได้ตั้งค่า")

        return False

    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0"
    }

    data = json.dumps({
        "content": message
    }).encode("utf-8")

    try:

        req = urllib.request.Request(
            url=DISCORD_WEBHOOK_URL,
            data=data,
            headers=headers,
            method="POST"
        )

        with urllib.request.urlopen(
            req,
            timeout=10
        ) as response:

            if response.status in (200, 204):

                print("✅ Discord ส่งสำเร็จ")

                return True

    except Exception as e:

        print(
            f"❌ Discord error: {e}"
        )

    return False


# ============================================================
# YAHOO DATA
# ============================================================

def fetch_yahoo_candles(symbol_ticker):

    url = (
        "https://query1.finance.yahoo.com/v8/finance/chart/"
        f"{symbol_ticker}"
        "?interval=1m&range=1d"
    )

    try:

        req = urllib.request.Request(
            url,
            headers={
                "User-Agent":
                "Mozilla/5.0"
            }
        )

        with urllib.request.urlopen(
            req,
            timeout=10
        ) as response:

            data = json.loads(
                response.read().decode()
            )

        result = data["chart"]["result"][0]

        timestamps = result.get(
            "timestamp",
            []
        )

        quote = result[
            "indicators"
        ]["quote"][0]

        candles = []

        for i in range(
            len(timestamps)
        ):

            try:

                o = quote["open"][i]
                h = quote["high"][i]
                l = quote["low"][i]
                c = quote["close"][i]

            except Exception:

                continue

            if None in (
                o,
                h,
                l,
                c
            ):

                continue

            candles.append({

                "timestamp":
                    int(timestamps[i]),

                "open":
                    float(o),

                "high":
                    float(h),

                "low":
                    float(l),

                "close":
                    float(c)

            })

        return candles

    except Exception as e:

        print(
            f"Yahoo error: {e}"
        )

        return []


# ============================================================
# REMOVE INCOMPLETE 1M CANDLE
# ============================================================

def get_closed_1m_candles(candles):

    if not candles:
        return []

    now_ts = int(
        time.time()
    )

    closed = []

    for c in candles:

        # แท่งจะถือว่าปิดแล้ว
        # เมื่อผ่านเวลาเปิดแท่งมาอย่างน้อย 60 วินาที

        if (
            c["timestamp"]
            <= now_ts - 60
        ):

            closed.append(c)

    return closed


# ============================================================
# OTC SYNTHETIC DATA
# ============================================================

def generate_otc_candles_persistent(
    symbol_name
):

    now_ts = int(time.time())

    if symbol_name not in OTC_HISTORY:

        base_price = (
            162.500
            if "JPY" in symbol_name
            else 1.0850
        )

        candles = []

        trend_bias = random.choice(
            [-0.04, 0.04]
        )

        for i in range(
            150,
            0,
            -1
        ):

            ts = (
                now_ts
                - i * 60
            )

            change = (
                trend_bias
                + random.uniform(
                    -0.03,
                    0.03
                )
            )

            open_p = base_price

            close_p = (
                open_p + change
            )

            high_p = (
                max(
                    open_p,
                    close_p
                )
                + abs(
                    random.uniform(
                        0.005,
                        0.02
                    )
                )
            )

            low_p = (
                min(
                    open_p,
                    close_p
                )
                - abs(
                    random.uniform(
                        0.005,
                        0.02
                    )
                )
            )

            candles.append({

                "timestamp": ts,

                "open":
                    round(
                        open_p,
                        5
                    ),

                "high":
                    round(
                        high_p,
                        5
                    ),

                "low":
                    round(
                        low_p,
                        5
                    ),

                "close":
                    round(
                        close_p,
                        5
                    )
            })

            base_price = close_p

        OTC_HISTORY[
            symbol_name
        ] = candles

    else:

        last = OTC_HISTORY[
            symbol_name
        ][-1]

        if (
            now_ts
            - last["timestamp"]
            >= 60
        ):

            open_p = last["close"]

            change = random.uniform(
                -0.05,
                0.05
            )

            close_p = (
                open_p + change
            )

            high_p = (
                max(
                    open_p,
                    close_p
                )
                + abs(
                    random.uniform(
                        0.005,
                        0.025
                    )
                )
            )

            low_p = (
                min(
                    open_p,
                    close_p
                )
                - abs(
                    random.uniform(
                        0.005,
                        0.025
                    )
                )
            )

            OTC_HISTORY[
                symbol_name
            ].append({

                "timestamp":
                    now_ts,

                "open":
                    round(
                        open_p,
                        5
                    ),

                "high":
                    round(
                        high_p,
                        5
                    ),

                "low":
                    round(
                        low_p,
                        5
                    ),

                "close":
                    round(
                        close_p,
                        5
                    )
            })

            OTC_HISTORY[
                symbol_name
            ] = OTC_HISTORY[
                symbol_name
            ][-150:]

    return OTC_HISTORY[
        symbol_name
    ]


# ============================================================
# RESAMPLE TIMEFRAME
# ============================================================

def resample_candles(
    candles_1m,
    timeframe_minutes
):

    if len(candles_1m) < (
        timeframe_minutes
    ):

        return []

    timeframe_seconds = (
        timeframe_minutes * 60
    )

    buckets = {}

    for candle in candles_1m:

        bucket = (
            candle["timestamp"]
            // timeframe_seconds
        )

        if bucket not in buckets:

            buckets[bucket] = []

        buckets[bucket].append(
            candle
        )

    result = []

    for bucket in sorted(
        buckets.keys()
    ):

        group = buckets[bucket]

        # ต้องมีแท่งครบ
        # เพื่อไม่เอา timeframe ที่ข้อมูลขาด
        if len(group) != timeframe_minutes:
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
# CANDLE FUNCTIONS
# ============================================================

def candle_body(c):

    return abs(
        c["close"]
        - c["open"]
    )


def candle_range(c):

    return max(
        c["high"]
        - c["low"],
        1e-9
    )


def candle_direction(c):

    if c["close"] > c["open"]:
        return "CALL"

    if c["close"] < c["open"]:
        return "PUT"

    return "NEUTRAL"


def candle_strength(
    candle,
    previous
):

    if not previous:

        return 1.0

    recent = previous[-20:]

    if not recent:

        return 1.0

    avg_range = mean(
        candle_range(x)
        for x in recent
    )

    if avg_range <= 0:

        return 1.0

    return (
        candle_range(candle)
        / avg_range
    )


# ============================================================
# STRUCTURE
# ============================================================

def calculate_structure(
    candles
):

    if len(candles) < 6:

        return {

            "direction":
                "UNKNOWN",

            "strength":
                0,

            "hh": False,
            "hl": False,
            "lh": False,
            "ll": False
        }

    recent = candles[-6:]

    highs = [
        c["high"]
        for c in recent
    ]

    lows = [
        c["low"]
        for c in recent
    ]

    hh = highs[-1] > highs[-3]

    hl = lows[-1] > lows[-3]

    lh = highs[-1] < highs[-3]

    ll = lows[-1] < lows[-3]

    if hh and hl:

        direction = "CALL"
        strength = 2

    elif lh and ll:

        direction = "PUT"
        strength = 2

    elif hh or hl:

        direction = "CALL"
        strength = 1

    elif lh or ll:

        direction = "PUT"
        strength = 1

    else:

        direction = "RANGE"
        strength = 0

    return {

        "direction":
            direction,

        "strength":
            strength,

        "hh":
            hh,

        "hl":
            hl,

        "lh":
            lh,

        "ll":
            ll
    }


# ============================================================
# SUPPORT / RESISTANCE
# ============================================================

def find_support_resistance(
    candles
):

    if len(candles) < (
        MIN_STRUCTURE_CANDLES
    ):

        return None

    lookback = candles[
        -STRUCTURE_LOOKBACK:
    ]

    support = min(
        c["low"]
        for c in lookback
    )

    resistance = max(
        c["high"]
        for c in lookback
    )

    current = candles[-1]["close"]

    total_range = max(
        resistance - support,
        1e-9
    )

    distance_support = (
        current - support
    )

    distance_resistance = (
        resistance - current
    )

    position = (
        current - support
    ) / total_range

    if position <= 0.20:

        zone = "NEAR_SUPPORT"

    elif position >= 0.80:

        zone = "NEAR_RESISTANCE"

    else:

        zone = "MID_RANGE"

    return {

        "support":
            support,

        "resistance":
            resistance,

        "position":
            position,

        "distance_support":
            distance_support,

        "distance_resistance":
            distance_resistance,

        "zone":
            zone,

        "range":
            total_range
    }


# ============================================================
# REJECTION
# ============================================================

def analyze_rejection(
    candle,
    direction
):

    full = candle_range(candle)

    upper = (
        candle["high"]
        - max(
            candle["open"],
            candle["close"]
        )
    )

    lower = (
        min(
            candle["open"],
            candle["close"]
        )
        - candle["low"]
    )

    body = candle_body(candle)

    # CALL = ต้องการไส้ล่าง
    if direction == "CALL":

        wick_ratio = (
            lower / full
        )

        return {
            "valid":
                wick_ratio >= 0.20,

            "ratio":
                wick_ratio,

            "type":
                "LOWER_WICK"
        }

    # PUT = ต้องการไส้บน
    if direction == "PUT":

        wick_ratio = (
            upper / full
        )

        return {
            "valid":
                wick_ratio >= 0.20,

            "ratio":
                wick_ratio,

            "type":
                "UPPER_WICK"
        }

    return {
        "valid": False,
        "ratio": 0,
        "type": "NONE"
    }


# ============================================================
# SIGZY
# ============================================================

def analyze_sigzy(
    c1_list,
    c5_list,
    c15_list
):

    if (
        not c1_list
        or not c5_list
        or not c15_list
    ):

        return None

    c15 = c15_list[-1]

    c5 = c5_list[-1]

    c1 = c1_list[-1]

    # --------------------------------------------------------
    # 15M MASTER TREND
    # --------------------------------------------------------

    if c15["close"] > c15["open"]:

        direction = "CALL"

    elif c15["close"] < c15["open"]:

        direction = "PUT"

    else:

        return None

    # --------------------------------------------------------
    # 5M CONFIRM
    # --------------------------------------------------------

    if direction == "CALL":

        five_ok = (
            c5["close"]
            > c5["open"]
        )

    else:

        five_ok = (
            c5["close"]
            < c5["open"]
        )

    if not five_ok:

        return None

    # --------------------------------------------------------
    # 1M REJECTION
    # --------------------------------------------------------

    rejection = analyze_rejection(
        c1,
        direction
    )

    if not rejection["valid"]:

        return None

    return {

        "direction":
            direction,

        "entry":
            c1["close"],

        "timestamp":
            c1["timestamp"],

        "rejection_ratio":
            rejection["ratio"],

        "reason":
            "15M trend + 5M confirmation + 1M rejection"
    }


# ============================================================
# TRADEIFY
# ============================================================

def analyze_tradeify(
    direction,
    c1_list,
    c5_list,
    c15_list
):

    score = 0

    reasons = []

    warnings = []

    # ========================================================
    # 15M STRUCTURE
    # ========================================================

    structure15 = calculate_structure(
        c15_list
    )

    if (
        structure15["direction"]
        == direction
    ):

        if structure15["strength"] == 2:

            score += 25

            reasons.append(
                "15M มี HH+HL / LH+LL ชัด"
            )

        else:

            score += 18

            reasons.append(
                "15M structure ไปทางเดียวกัน"
            )

    else:

        warnings.append(
            "15M structure ยังไม่ยืนยัน"
        )

    # ========================================================
    # 5M STRUCTURE
    # ========================================================

    structure5 = calculate_structure(
        c5_list
    )

    if (
        structure5["direction"]
        == direction
    ):

        if structure5["strength"] == 2:

            score += 20

            reasons.append(
                "5M structure แข็งแรง"
            )

        else:

            score += 14

            reasons.append(
                "5M structure สอดคล้อง"
            )

    else:

        warnings.append(
            "5M structure ไม่ตรงกับ 15M"
        )

    # ========================================================
    # 1M
    # ========================================================

    c1 = c1_list[-1]

    if direction == "CALL":

        if c1["close"] > c1["open"]:

            score += 8

            reasons.append(
                "1M ปิดเขียว"
            )

    else:

        if c1["close"] < c1["open"]:

            score += 8

            reasons.append(
                "1M ปิดแดง"
            )

    # ========================================================
    # REJECTION
    # ========================================================

    rejection = analyze_rejection(
        c1,
        direction
    )

    if rejection["valid"]:

        score += 10

        reasons.append(
            f"Rejection "
            f"{rejection['ratio']:.0%}"
        )

    # ========================================================
    # S/R
    # ========================================================

    sr = find_support_resistance(
        c1_list
    )

    if sr:

        if direction == "CALL":

            if sr["zone"] == "NEAR_SUPPORT":

                score += 20

                reasons.append(
                    "ราคาอยู่ใกล้ Support "
                    "จากโครงสร้างย้อนหลัง"
                )

            elif (
                sr["zone"]
                == "NEAR_RESISTANCE"
            ):

                score -= 12

                warnings.append(
                    "CALL แต่ราคาเข้าใกล้ Resistance"
                )

        else:

            if (
                sr["zone"]
                == "NEAR_RESISTANCE"
            ):

                score += 20

                reasons.append(
                    "ราคาอยู่ใกล้ Resistance "
                    "จากโครงสร้างย้อนหลัง"
                )

            elif (
                sr["zone"]
                == "NEAR_SUPPORT"
            ):

                score -= 12

                warnings.append(
                    "PUT แต่ราคาเข้าใกล้ Support"
                )

    # ========================================================
    # CANDLE STRENGTH
    # ========================================================

    strength = candle_strength(
        c1,
        c1_list[:-1]
    )

    if strength >= 1.30:

        score += 10

        reasons.append(
            f"แท่งแข็งแรง {strength:.2f}x"
        )

    elif strength < 0.70:

        score -= 5

        warnings.append(
            "แท่งเทียนค่อนข้างเบา"
        )

    # ========================================================
    # MARKET STATE
    # ========================================================

    if (
        structure15["direction"]
        == direction
        and
        structure5["direction"]
        == direction
    ):

        market_state = "TRENDING"

        score += 7

        reasons.append(
            "15M + 5M ไปทางเดียวกัน"
        )

    elif (
        structure15["direction"]
        == "RANGE"
    ):

        market_state = "RANGE"

        warnings.append(
            "ตลาดอยู่ใน Range"
        )

    else:

        market_state = "MIXED"

    # ========================================================
    # SCORE LIMIT
    # ========================================================

    score = max(
        0,
        min(
            int(score),
            100
        )
    )

    # ========================================================
    # DECISION
    # ========================================================

    if score >= TRADE_SCORE:

        decision = "TRADE"
        grade = "A"

    elif score >= WAIT_SCORE:

        decision = "WAIT"
        grade = "B"

    else:

        decision = "NO TRADE"
        grade = "C"

    return {

        "decision":
            decision,

        "grade":
            grade,

        "score":
            score,

        "market_state":
            market_state,

        "structure_15":
            structure15,

        "structure_5":
            structure5,

        "support":
            sr["support"]
            if sr else None,

        "resistance":
            sr["resistance"]
            if sr else None,

        "zone":
            sr["zone"]
            if sr else "UNKNOWN",

        "candle_strength":
            strength,

        "reasons":
            reasons,

        "warnings":
            warnings
    }


# ============================================================
# BUILD SIGNAL
# ============================================================

def build_signal_message(
    number,
    display_name,
    sigzy,
    tradeify
):

    direction = sigzy[
        "direction"
    ]

    icon = (
        "🟢"
        if direction == "CALL"
        else "🔴"
    )

    now = get_thai_time()

    expiry = (
        now
        + timedelta(
            minutes=PAIR_LOCK_MINUTES
        )
    )

    entry_time = (
        now.strftime("%H.%M")
    )

    expiry_time = (
        expiry.strftime("%H.%M")
    )

    step = CURRENT_STEP

    stake = STAKE_BY_STEP[
        step
    ]

    reasons = "\n".join(
        f"• {x}"
        for x in tradeify[
            "reasons"
        ]
    )

    warnings = "\n".join(
        f"• ⚠️ {x}"
        for x in tradeify[
            "warnings"
        ]
    )

    if not warnings:

        warnings = (
            "• ไม่มี warning สำคัญ"
        )

    message = (

        f"🎯 **SIGZY + TRADEIFY v3 "
        f"(#{number})** 🎯\n"

        f"━━━━━━━━━━━━━━━━━━━━━━━\n"

        f"⏱️ **5M {display_name}**\n"

        f"{icon} Direction: "
        f"**{direction}**\n"

        f"🕐 Entry: "
        f"**{entry_time} น.**\n"

        f"⏳ Expiry: "
        f"**{expiry_time} น.**\n\n"

        f"💰 Entry: "
        f"**{sigzy['entry']:.5f}**\n"

        f"💵 Simulation STEP: "
        f"**{step} / {stake} บาท**\n\n"

        f"🧠 **TRADEIFY**\n"

        f"Score: "
        f"**{tradeify['score']}/100**\n"

        f"Grade: "
        f"**{tradeify['grade']}**\n"

        f"Decision: "
        f"**{tradeify['decision']}**\n"

        f"Market: "
        f"**{tradeify['market_state']}**\n\n"

        f"📊 **TIMEFRAME**\n"

        f"• 15M: "
        f"**{tradeify['structure_15']['direction']}**\n"

        f"• 5M: "
        f"**{tradeify['structure_5']['direction']}**\n"

        f"• 1M: "
        f"**{direction} Trigger**\n"

        f"• S/R: "
        f"**{tradeify['zone']}**\n"

        f"• Candle Strength: "
        f"**{tradeify['candle_strength']:.2f}x**\n\n"

        f"✅ **เหตุผล**\n"
        f"{reasons}\n\n"

        f"⚠️ **ข้อควรระวัง**\n"
        f"{warnings}\n\n"

        f"━━━━━━━━━━━━━━━━━━━━━━━\n"

        f"📌 **ผลจะถูกประเมินหลัง "
        f"Expiry เท่านั้น**\n"

        f"ห้ามนับ WIN/LOSS ก่อนแท่งหมดเวลา\n"

        f"━━━━━━━━━━━━━━━━━━━━━━━"
    )

    return message, expiry


# ============================================================
# START NEW SET
# ============================================================

def start_new_set():

    global SET_NUMBER
    global SET_ACTIVE
    global SET_LOSS_COUNT
    global CURRENT_STEP

    if not SET_ACTIVE:

        SET_NUMBER += 1

        SET_ACTIVE = True

        SET_LOSS_COUNT = 0

        CURRENT_STEP = 1

        print(
            f"🆕 เริ่มชุดที่ {SET_NUMBER}"
        )


# ============================================================
# REGISTER PENDING TRADE
# ============================================================

def register_pending_trade(
    signal_key,
    symbol,
    direction,
    entry,
    entry_timestamp,
    expiry_timestamp,
    step,
    stake
):

    PENDING_TRADES[
        signal_key
    ] = {

        "symbol":
            symbol,

        "direction":
            direction,

        "entry":
            entry,

        "entry_timestamp":
            entry_timestamp,

        "expiry_timestamp":
            expiry_timestamp,

        "step":
            step,

        "stake":
            stake,

        "created_at":
            int(time.time())
    }


# ============================================================
# GET PRICE AT EXPIRY
# ============================================================

def get_expiry_price(
    symbol,
    ticker,
    expiry_timestamp
):

    if is_weekend():

        candles = (
            generate_otc_candles_persistent(
                symbol
            )
        )

    else:

        candles = (
            fetch_yahoo_candles(
                ticker
            )
        )

    if not candles:

        return None

    # ต้องใช้แท่งที่ปิดแล้ว
    candles = get_closed_1m_candles(
        candles
    )

    if not candles:

        return None

    candidates = [
        c
        for c in candles
        if c["timestamp"]
        >= expiry_timestamp
    ]

    if candidates:

        return candidates[0]["close"]

    # ถ้ายังไม่มีแท่งตรงเวลา
    # ใช้แท่งปิดล่าสุดที่ผ่าน expiry
    passed = [
        c
        for c in candles
        if c["timestamp"]
        <= expiry_timestamp
    ]

    if passed:

        return passed[-1]["close"]

    return None


# ============================================================
# EVALUATE TRADE
# ============================================================

def evaluate_pending_trades():

    global DAILY_WIN_COUNT
    global DAILY_LOSS_COUNT
    global CURRENT_STEP
    global SET_ACTIVE
    global SET_LOSS_COUNT

    now_ts = int(
        time.time()
    )

    completed = []

    for key, trade in list(
        PENDING_TRADES.items()
    ):

        if (
            now_ts
            < trade["expiry_timestamp"]
        ):

            continue

        symbol = trade[
            "symbol"
        ]

        ticker = SYMBOL_MAP.get(
            symbol
        )

        if not ticker:

            continue

        expiry_price = (
            get_expiry_price(
                symbol,
                ticker,
                trade[
                    "expiry_timestamp"
                ]
            )
        )

        if expiry_price is None:

            print(
                f"⏳ {symbol}: "
                f"ยังไม่มีราคาหลัง expiry"
            )

            continue

        entry = trade["entry"]

        direction = trade[
            "direction"
        ]

        if direction == "CALL":

            win = (
                expiry_price
                > entry
            )

        else:

            win = (
                expiry_price
                < entry
            )

        step = trade["step"]

        if win:

            result = "WIN"

            TRADE_STATS[
                step
            ]["WIN"] += 1

            DAILY_WIN_COUNT += 1

            SET_ACTIVE = False

            SET_LOSS_COUNT = 0

            CURRENT_STEP = 1

        else:

            result = "LOSS"

            TRADE_STATS[
                step
            ]["LOSS"] += 1

            SET_LOSS_COUNT += 1

            DAILY_LOSS_COUNT += 1

            if step >= MAX_STEP:

                # แพ้ครบ 3 ไม้
                SET_ACTIVE = False

                SET_LOSS_COUNT = 0

                CURRENT_STEP = 1

            else:

                # ไปไม้ถัดไป
                CURRENT_STEP = (
                    step + 1
                )

        result_message = (

            f"📊 **TRADEIFY RESULT**\n"

            f"━━━━━━━━━━━━━━━━━━━━━━━\n"

            f"คู่: **{symbol}**\n"

            f"Direction: "
            f"**{direction}**\n"

            f"STEP: **{step}**\n"

            f"เงินจำลอง: "
            f"**{trade['stake']} บาท**\n\n"

            f"Entry: "
            f"**{entry:.5f}**\n"

            f"Expiry Price: "
            f"**{expiry_price:.5f}**\n\n"

            f"ผล: "
            f"**{'🟢 WIN' if win else '🔴 LOSS'}**\n\n"

            f"Daily WIN: "
            f"**{DAILY_WIN_COUNT}/"
            f"{DAILY_WIN_TARGET}**\n"

            f"Daily LOSS: "
            f"**{DAILY_LOSS_COUNT}**\n"

            f"Next STEP: "
            f"**{CURRENT_STEP}**\n"

            f"━━━━━━━━━━━━━━━━━━━━━━━"
        )

        print(
            "\n" + result_message
        )

        send_discord(
            result_message
        )

        completed.append(key)

    for key in completed:

        PENDING_TRADES.pop(
            key,
            None
        )


# ============================================================
# STATISTICS
# ============================================================

def print_statistics():

    print("\n")
    print(
        "========== TRADEIFY STATS =========="
    )

    for step in [
        1,
        2,
        3
    ]:

        w = TRADE_STATS[
            step
        ]["WIN"]

        l = TRADE_STATS[
            step
        ]["LOSS"]

        total = w + l

        rate = (
            w / total * 100
            if total
            else 0
        )

        print(
            f"ไม้ {step}: "
            f"W={w} "
            f"L={l} "
            f"WinRate={rate:.2f}%"
        )

    print(
        f"Daily Signals: "
        f"{DAILY_SIGNAL_COUNT}"
    )

    print(
        f"Daily WIN: "
        f"{DAILY_WIN_COUNT}/"
        f"{DAILY_WIN_TARGET}"
    )

    print(
        f"Daily LOSS: "
        f"{DAILY_LOSS_COUNT}"
    )

    print(
        f"Current STEP: "
        f"{CURRENT_STEP}"
    )

    print(
        f"Pending: "
        f"{len(PENDING_TRADES)}"
    )

    print(
        "===================================="
    )


# ============================================================
# ANALYZE PAIR
# ============================================================

def analyze_pair(
    symbol_name,
    ticker_symbol
):

    global DAILY_SIGNAL_COUNT

    check_daily_reset()

    # --------------------------------------------------------
    # STOP AFTER 2 WIN
    # --------------------------------------------------------

    if (
        DAILY_WIN_COUNT
        >= DAILY_WIN_TARGET
    ):

        return None, (
            f"-> {symbol_name}: "
            f"🏁 WIN ครบ "
            f"{DAILY_WIN_TARGET} ชุดแล้ว"
        )

    # --------------------------------------------------------
    # DO NOT CREATE NEW SIGNAL
    # WHILE A SET IS WAITING FOR RESULT
    # --------------------------------------------------------

    if SET_ACTIVE:

        return None, (
            f"-> {symbol_name}: "
            f"กำลังเดินชุด "
            f"STEP {CURRENT_STEP}"
        )

    # --------------------------------------------------------
    # PAIR LOCK
    # --------------------------------------------------------

    now = get_thai_time()

    if (
        symbol_name
        in PAIR_LOCKED_UNTIL
    ):

        if (
            now
            < PAIR_LOCKED_UNTIL[
                symbol_name
            ]
        ):

            return None, (
                f"-> {symbol_name}: "
                f"PAIR LOCK"
            )

    # --------------------------------------------------------
    # DATA
    # --------------------------------------------------------

    if is_weekend():

        candles_1m = (
            generate_otc_candles_persistent(
                symbol_name
            )
        )

        display_name = (
            f"{symbol_name} (OTC)"
        )

    else:

        candles_1m = (
            fetch_yahoo_candles(
                ticker_symbol
            )
        )

        display_name = (
            symbol_name
        )

        # สำคัญมาก
        # ตัดแท่ง 1M ที่ยังไม่ปิด
        candles_1m = (
            get_closed_1m_candles(
                candles_1m
            )
        )

    if len(candles_1m) < 30:

        return None, (
            f"-> {display_name}: "
            f"ข้อมูลไม่พอ "
            f"({len(candles_1m)})"
        )

    # --------------------------------------------------------
    # TIMEFRAMES
    # --------------------------------------------------------

    candles_5m = resample_candles(
        candles_1m,
        5
    )

    candles_15m = resample_candles(
        candles_1m,
        15
    )

    if (
        len(candles_5m) < 6
        or len(candles_15m) < 3
    ):

        return None, (
            f"-> {display_name}: "
            f"Timeframe ไม่พอ"
        )

    # --------------------------------------------------------
    # SIGZY
    # --------------------------------------------------------

    sigzy = analyze_sigzy(
        candles_1m,
        candles_5m,
        candles_15m
    )

    if not sigzy:

        return None, (
            f"-> {display_name}: "
            f"SIGZY WAIT"
        )

    # --------------------------------------------------------
    # TRADEIFY
    # --------------------------------------------------------

    tradeify = analyze_tradeify(
        sigzy["direction"],
        candles_1m,
        candles_5m,
        candles_15m
    )

    # --------------------------------------------------------
    # NO TRADE
    # --------------------------------------------------------

    if (
        tradeify["decision"]
        == "NO TRADE"
    ):

        return None, (
            f"-> {display_name}: "
            f"TRADEIFY NO TRADE "
            f"({tradeify['score']}/100)"
        )

    # --------------------------------------------------------
    # WAIT
    # --------------------------------------------------------

    if (
        tradeify["decision"]
        == "WAIT"
    ):

        return None, (
            f"-> {display_name}: "
            f"TRADEIFY WAIT "
            f"({tradeify['score']}/100)"
        )

    # --------------------------------------------------------
    # TRADE
    # --------------------------------------------------------

    start_new_set()

    DAILY_SIGNAL_COUNT += 1

    signal_key = (
        f"{display_name}_"
        f"{sigzy['timestamp']}_"
        f"{sigzy['direction']}_"
        f"{CURRENT_STEP}"
    )

    message, expiry = (
        build_signal_message(
            DAILY_SIGNAL_COUNT,
            display_name,
            sigzy,
            tradeify
        )
    )

    expiry_timestamp = int(
        expiry.timestamp()
    )

    stake = STAKE_BY_STEP[
        CURRENT_STEP
    ]

    register_pending_trade(

        signal_key=signal_key,

        symbol=symbol_name,

        direction=sigzy[
            "direction"
        ],

        entry=sigzy[
            "entry"
        ],

        entry_timestamp=sigzy[
            "timestamp"
        ],

        expiry_timestamp=
            expiry_timestamp,

        step=CURRENT_STEP,

        stake=stake
    )

    PAIR_LOCKED_UNTIL[
        symbol_name
    ] = expiry

    return (
        signal_key,
        message
    )


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":

    check_daily_reset()

    mode = (
        "OTC TEST MODE"
        if is_weekend()
        else "REAL MARKET MODE"
    )

    print(
        "=========================================="
    )

    print(
        f"🚀 SIGZY + TRADEIFY v3 "
        f"[{mode}]"
    )

    print(
        "=========================================="
    )

    print(
        "ระบบ: "
        "15M + 5M + 1M + S/R 100"
    )

    print(
        "Money Simulation: "
        "100 → 200 → 300"
    )

    print(
        f"Daily Target: "
        f"{DAILY_WIN_TARGET} WIN"
    )

    print(
        "=========================================="
    )

    if DISCORD_WEBHOOK_URL.strip():

        send_discord(

            f"🚀 **SIGZY + TRADEIFY v3 START**\n"

            f"Mode: **{mode}**\n"

            f"Daily Target: "
            f"**{DAILY_WIN_TARGET} WIN**\n"

            f"Money Simulation: "
            f"**100 → 200 → 300**\n\n"

            f"⚠️ ผล WIN/LOSS "
            f"จะประเมินหลัง Expiry เท่านั้น"
        )

    else:

        print(
            "⚠️ Discord Webhook "
            "ยังไม่ได้ตั้งค่า"
        )

    # ========================================================
    # LOOP
    # ========================================================

    while True:

        try:

            check_daily_reset()

            # ------------------------------------------------
            # ตรวจผลของออเดอร์ที่หมดเวลา
            # ------------------------------------------------

            evaluate_pending_trades()

            now = get_thai_time()

            print(
                f"\n[{now.strftime('%H:%M:%S')}] "
                f"Scanning..."
            )

            # ------------------------------------------------
            # STOP
            # ------------------------------------------------

            if (
                DAILY_WIN_COUNT
                >= DAILY_WIN_TARGET
            ):

                print(
                    "🏁 WIN ครบเป้าหมายวันนี้แล้ว"
                )

                print_statistics()

                time.sleep(
                    SCAN_SECONDS
                )

                continue

            # ------------------------------------------------
            # ถ้ามีชุดกำลังเดิน
            # ห้ามเปิดชุดใหม่
            # ------------------------------------------------

            if SET_ACTIVE:

                print(
                    f"🔒 SET ACTIVE "
                    f"| STEP {CURRENT_STEP}"
                )

                print_statistics()

                time.sleep(
                    SCAN_SECONDS
                )

                continue

            # ------------------------------------------------
            # SCAN PAIRS
            # ------------------------------------------------

            for (
                name,
                ticker
            ) in SYMBOL_MAP.items():

                signal_key, result = (
                    analyze_pair(
                        name,
                        ticker
                    )
                )

                if signal_key:

                    if (
                        signal_key
                        not in SENT_SIGNALS
                    ):

                        print(
                            "\n🎯 "
                            "TRADEIFY TRADE"
                        )

                        print(
                            result
                        )

                        send_discord(
                            result
                        )

                        SENT_SIGNALS.add(
                            signal_key
                        )

                else:

                    print(result)

            # ------------------------------------------------
            # STATS
            # ------------------------------------------------

            print_statistics()

            time.sleep(
                SCAN_SECONDS
            )

        except KeyboardInterrupt:

            print(
                "\n🛑 หยุดระบบ"
            )

            break

        except Exception as e:

            print(
                f"❌ MAIN ERROR: "
                f"{e}"
            )

            time.sleep(5)
