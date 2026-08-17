# -*- coding: utf-8 -*-

"""
============================================================
SIGZY 5M SERIES LOCK - UPGRADED
5M MASTER + 1M FILTER
3-STEP SAME-DIRECTION TRACKER
THAI TIME + CLEAR ENTRY + STATE RECOVERY
============================================================

หลักการหลัก
------------------------------------------------------------
1. วิเคราะห์ Setup ใหม่เฉพาะตอน "ไม่มี Series"
2. 5M = MASTER
3. 1M = FILTER
4. เมื่อได้ CALL -> LOCK CALL
5. เมื่อได้ PUT  -> LOCK PUT
6. LOSS ไม้ 1 -> Direction เดิม ไม้ 2
7. LOSS ไม้ 2 -> Direction เดิม ไม้ 3
8. WIN ไม้ใด -> จบ Series
9. LOSS ครบ 3 -> FULL LOSS
10. ระหว่าง Series:
      - ไม่วิเคราะห์ 5M ใหม่
      - ไม่วิเคราะห์ 1M ใหม่เพื่อเปลี่ยนทิศ
      - ไม่รับ Signal ใหม่
      - ไม่มี AI
      - ไม่เปลี่ยน Direction
11. หลัง Series จบ:
      - รอแท่ง 5M ใหม่
      - วิเคราะห์ Setup ใหม่
12. ใช้แท่งที่ปิดแล้ว
13. แสดงเวลาไทย
14. แสดง ENTRY ของทุกไม้ชัดเจน
15. บันทึก Series + Active State
16. หากโปรแกรม restart ระหว่าง Series
    สามารถโหลด Series กลับมาได้

หมายเหตุ:
ระบบนี้เป็นระบบจำลอง/ติดตามผล
ไม่รับประกันกำไรหรือ WIN 1 ใน 3 ทุก Series
"""

import os
import json
import time
import requests
import yfinance as yf

from datetime import datetime, timezone, timedelta


# ============================================================
# CONFIG
# ============================================================

# อย่าฝัง Webhook จริงไว้ใน Source Code
DISCORD_WEBHOOK_URL = os.getenv(
    "DISCORD_WEBHOOK_URL",
    ""
)

MEMORY_FILE = "v13_memory_5m.json"
STATE_FILE = "v13_active_series_state.json"

# ตรวจทุก 30 วินาที
SCAN_SECONDS = 30

# 1 Series = สูงสุด 3 ไม้
MAX_STEPS = 3

# Indicators
EMA_PERIOD = 50
ATR_PERIOD = 14

# ใช้สำหรับแสดง TP/SL Simulation
ATR_MULTIPLIER = 0.50

# คะแนนขั้นต่ำก่อนอนุญาตให้สร้าง Series
MIN_SETUP_SCORE = 80

# ต้องมีแท่งเพียงพอ
MIN_5M_CANDLES = 60
MIN_1M_CANDLES = 60

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


# ============================================================
# GLOBAL STATE
# ============================================================

HISTORICAL_MEMORY = []

ACTIVE_SERIES = None

SERIES_NUMBER = 0

# ป้องกันการใช้แท่งเดิมสร้าง Series ซ้ำ
LAST_ANALYZED_CANDLE = {}


# ============================================================
# THAI TIME
# ============================================================

THAI_TZ = timezone(timedelta(hours=7))


def thai_now():
    return datetime.now(timezone.utc).astimezone(THAI_TZ)


def now_text():
    return thai_now().strftime(
        "%d/%m/%Y %H:%M:%S"
    )


def log(message):
    print(
        f"[{now_text()}] {message}"
    )


# ============================================================
# DISCORD
# ============================================================

def send_discord(message):

    if not DISCORD_WEBHOOK_URL:
        print(
            "⚠️ ไม่มี DISCORD_WEBHOOK_URL"
        )
        return False

    try:

        response = requests.post(
            DISCORD_WEBHOOK_URL,
            json={
                "content": message
            },
            timeout=10
        )

        if response.status_code in (
            200,
            204
        ):
            print(
                "✅ Discord ส่งสำเร็จ"
            )
            return True

        print(
            f"❌ Discord Error "
            f"{response.status_code}: "
            f"{response.text[:200]}"
        )

    except Exception as e:

        print(
            f"❌ Discord Exception: {e}"
        )

    return False


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
            "📄 ยังไม่มี Memory"
        )

        return

    try:

        with open(
            MEMORY_FILE,
            "r",
            encoding="utf-8"
        ) as f:

            HISTORICAL_MEMORY = json.load(
                f
            )

        if not isinstance(
            HISTORICAL_MEMORY,
            list
        ):
            HISTORICAL_MEMORY = []

        log(
            f"📂 โหลด Memory "
            f"{len(HISTORICAL_MEMORY)} Series"
        )

    except Exception as e:

        log(
            f"⚠️ โหลด Memory ไม่สำเร็จ: {e}"
        )

        HISTORICAL_MEMORY = []


def save_memory():

    try:

        with open(
            MEMORY_FILE,
            "w",
            encoding="utf-8"
        ) as f:

            json.dump(
                HISTORICAL_MEMORY,
                f,
                ensure_ascii=False,
                indent=2
            )

    except Exception as e:

        log(
            f"⚠️ Save Memory Error: {e}"
        )


# ============================================================
# ACTIVE SERIES STATE
# ============================================================

def save_active_state():

    if ACTIVE_SERIES is None:

        try:

            if os.path.exists(
                STATE_FILE
            ):
                os.remove(
                    STATE_FILE
                )

        except Exception:
            pass

        return

    try:

        with open(
            STATE_FILE,
            "w",
            encoding="utf-8"
        ) as f:

            json.dump(
                ACTIVE_SERIES,
                f,
                ensure_ascii=False,
                indent=2
            )

    except Exception as e:

        log(
            f"⚠️ Save Active State Error: {e}"
        )


def load_active_state():

    global ACTIVE_SERIES
    global SERIES_NUMBER

    if not os.path.exists(
        STATE_FILE
    ):
        return

    try:

        with open(
            STATE_FILE,
            "r",
            encoding="utf-8"
        ) as f:

            state = json.load(f)

        if not isinstance(
            state,
            dict
        ):
            return

        ACTIVE_SERIES = state

        SERIES_NUMBER = max(
            SERIES_NUMBER,
            int(
                state.get(
                    "series_id",
                    0
                )
            )
        )

        log(
            f"🔄 RECOVER SERIES "
            f"#{state.get('series_id')} "
            f"{state.get('symbol')} "
            f"{state.get('direction')} "
            f"STEP "
            f"{state.get('step')}/3"
        )

    except Exception as e:

        log(
            f"⚠️ Recover State Error: {e}"
        )


# ============================================================
# MARKET DATA
# ============================================================

def get_market_data(
    symbol,
    interval
):

    ticker_symbol = SYMBOL_MAP.get(
        symbol,
        symbol
    )

    try:

        ticker = yf.Ticker(
            ticker_symbol
        )

        period = (
            "5d"
            if interval == "1m"
            else "10d"
        )

        df = ticker.history(
            period=period,
            interval=interval,
            auto_adjust=False
        )

        if df.empty:
            return []

        candles = []

        for idx, row in df.iterrows():

            values = [
                row.get("Open"),
                row.get("High"),
                row.get("Low"),
                row.get("Close")
            ]

            if any(
                value is None
                for value in values
            ):
                continue

            try:

                o = float(
                    row["Open"]
                )

                h = float(
                    row["High"]
                )

                l = float(
                    row["Low"]
                )

                c = float(
                    row["Close"]
                )

            except Exception:

                continue

            candles.append({

                "datetime":
                    idx.strftime(
                        "%Y-%m-%d %H:%M:%S"
                    ),

                "open": o,

                "high": h,

                "low": l,

                "close": c
            })

        return candles

    except Exception as e:

        log(
            f"[DATA ERROR] "
            f"{symbol} {interval}: "
            f"{e}"
        )

        return []


# ============================================================
# CLOSED CANDLES
# ============================================================

def get_closed_candles(
    symbol,
    interval
):

    candles = get_market_data(
        symbol,
        interval
    )

    if len(candles) < 3:
        return []

    # ตัดแท่งล่าสุดออก
    # เพื่อหลีกเลี่ยง incomplete candle
    return candles[:-1]


# ============================================================
# EMA
# ============================================================

def calculate_ema(
    candles,
    period=50
):

    if len(candles) < period:
        return None

    closes = [
        c["close"]
        for c in candles
    ]

    multiplier = (
        2 / (period + 1)
    )

    ema = sum(
        closes[:period]
    ) / period

    for price in closes[period:]:

        ema = (
            (price - ema)
            * multiplier
            + ema
        )

    return ema


# ============================================================
# ATR
# ============================================================

def calculate_atr(
    candles,
    period=14
):

    if len(candles) < (
        period + 1
    ):
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
            )
        )

        trs.append(tr)

    return sum(
        trs[-period:]
    ) / period


# ============================================================
# CANDLE BODY
# ============================================================

def candle_body_ratio(
    candle
):

    total_range = (
        candle["high"]
        - candle["low"]
    )

    if total_range <= 0:
        return 0

    body = abs(
        candle["close"]
        - candle["open"]
    )

    return (
        body / total_range
    )


# ============================================================
# SETUP ANALYSIS
# ============================================================

def analyze_5m_strategy(
    symbol
):

    candles_5m = get_closed_candles(
        symbol,
        "5m"
    )

    candles_1m = get_closed_candles(
        symbol,
        "1m"
    )

    if (
        len(candles_5m)
        < MIN_5M_CANDLES
        or
        len(candles_1m)
        < MIN_1M_CANDLES
    ):

        return {
            "decision": "WAIT",
            "score": 0
        }

    c5 = candles_5m[-1]

    c5_prev = candles_5m[-2]

    c1 = candles_1m[-1]

    ema_5m = calculate_ema(
        candles_5m,
        EMA_PERIOD
    )

    ema_1m = calculate_ema(
        candles_1m,
        EMA_PERIOD
    )

    atr_5m = calculate_atr(
        candles_5m,
        ATR_PERIOD
    )

    if (
        ema_5m is None
        or ema_1m is None
        or atr_5m is None
        or atr_5m <= 0
    ):

        return {
            "decision": "WAIT",
            "score": 0
        }

    # ========================================================
    # 5M MASTER
    # ========================================================

    bullish_5m = (
        c5["close"] > ema_5m
        and
        c5["close"] > c5["open"]
    )

    bearish_5m = (
        c5["close"] < ema_5m
        and
        c5["close"] < c5["open"]
    )

    # EMA slope
    ema_5m_prev = calculate_ema(
        candles_5m[:-1],
        EMA_PERIOD
    )

    ema_slope_up = (
        ema_5m_prev is not None
        and
        ema_5m > ema_5m_prev
    )

    ema_slope_down = (
        ema_5m_prev is not None
        and
        ema_5m < ema_5m_prev
    )

    body_ratio = candle_body_ratio(
        c5
    )

    # ========================================================
    # 1M FILTER
    # ========================================================

    bullish_1m = (
        c1["close"] > ema_1m
    )

    bearish_1m = (
        c1["close"] < ema_1m
    )

    # ========================================================
    # SCORE
    # ========================================================

    call_score = 0
    put_score = 0

    call_reasons = []
    put_reasons = []

    # 5M price location
    if c5["close"] > ema_5m:

        call_score += 30

        call_reasons.append(
            "5M > EMA50"
        )

    if c5["close"] < ema_5m:

        put_score += 30

        put_reasons.append(
            "5M < EMA50"
        )

    # 5M candle
    if c5["close"] > c5["open"]:

        call_score += 20

        call_reasons.append(
            "5M Bull Candle"
        )

    if c5["close"] < c5["open"]:

        put_score += 20

        put_reasons.append(
            "5M Bear Candle"
        )

    # EMA slope
    if ema_slope_up:

        call_score += 15

        call_reasons.append(
            "EMA50 Rising"
        )

    if ema_slope_down:

        put_score += 15

        put_reasons.append(
            "EMA50 Falling"
        )

    # 1M filter
    if bullish_1m:

        call_score += 25

        call_reasons.append(
            "1M > EMA50"
        )

    if bearish_1m:

        put_score += 25

        put_reasons.append(
            "1M < EMA50"
        )

    # Strong candle body
    if body_ratio >= 0.55:

        if c5["close"] > c5["open"]:

            call_score += 10

            call_reasons.append(
                "Strong 5M Body"
            )

        elif c5["close"] < c5["open"]:

            put_score += 10

            put_reasons.append(
                "Strong 5M Body"
            )

    # ========================================================
    # DIRECTION
    # ========================================================

    if (
        call_score >= MIN_SETUP_SCORE
        and
        call_score > put_score
    ):

        direction = "CALL"

        score = min(
            100,
            call_score
        )

        reasons = (
            " + ".join(
                call_reasons
            )
        )

    elif (
        put_score >= MIN_SETUP_SCORE
        and
        put_score > call_score
    ):

        direction = "PUT"

        score = min(
            100,
            put_score
        )

        reasons = (
            " + ".join(
                put_reasons
            )
        )

    else:

        return {
            "decision": "WAIT",
            "score": max(
                call_score,
                put_score
            )
        }

    # ========================================================
    # ENTRY
    # ========================================================

    price = c5["close"]

    distance = (
        atr_5m
        * ATR_MULTIPLIER
    )

    if direction == "CALL":

        tp = price + distance

        sl = price - distance

    else:

        tp = price - distance

        sl = price + distance

    return {

        "decision":
            direction,

        "score":
            score,

        "symbol":
            symbol,

        "price":
            price,

        "atr":
            atr_5m,

        "tp":
            tp,

        "sl":
            sl,

        "reasons":
            reasons,

        "candle_time":
            c5["datetime"],

        "setup_name":
            "5M_MASTER_1M_FILTER_SERIES_LOCK",

        "body_ratio":
            body_ratio
    }


# ============================================================
# FIND NEW SETUP
# ============================================================

def find_new_setup():

    log(
        "🔎 ไม่มี Series "
        "→ ค้นหา Setup ใหม่"
    )

    for symbol in SYMBOLS:

        try:

            result = analyze_5m_strategy(
                symbol
            )

            if result["decision"] not in (
                "CALL",
                "PUT"
            ):
                continue

            candle_time = (
                result["candle_time"]
            )

            last_candle = (
                LAST_ANALYZED_CANDLE.get(
                    symbol
                )
            )

            # ป้องกันแท่งเดิม
            if candle_time == last_candle:
                continue

            LAST_ANALYZED_CANDLE[
                symbol
            ] = candle_time

            return result

        except Exception as e:

            log(
                f"[SCAN ERROR] "
                f"{symbol}: {e}"
            )

    return None


# ============================================================
# START SERIES
# ============================================================

def start_series(
    result
):

    global ACTIVE_SERIES
    global SERIES_NUMBER

    SERIES_NUMBER += 1

    direction = result[
        "decision"
    ]

    ACTIVE_SERIES = {

        "series_id":
            SERIES_NUMBER,

        "symbol":
            result["symbol"],

        "direction":
            direction,

        "score":
            result["score"],

        "setup_name":
            result["setup_name"],

        "signal_time":
            result["candle_time"],

        "signal_price":
            result["price"],

        "atr":
            result["atr"],

        "step":
            1,

        "wins":
            0,

        "losses":
            0,

        "step_results":
            [],

        "started_at":
            now_text(),

        "last_processed_candle":
            result["candle_time"],

        "setup_reasons":
            result["reasons"],

        "body_ratio":
            result.get(
                "body_ratio",
                0
            )
    }

    save_active_state()

    icon = (
        "🟢"
        if direction == "CALL"
        else "🔴"
    )

    message = (

        f"🎯 **NEW SERIES "
        f"#{SERIES_NUMBER}**\n"

        f"━━━━━━━━━━━━━━━━━━━━\n"

        f"🇹🇭 **เวลาไทย:** "
        f"{now_text()}\n"

        f"💱 คู่เงิน: "
        f"**{result['symbol']}**\n"

        f"{icon} Direction: "
        f"**{direction}**\n"

        f"🏆 Setup Score: "
        f"**{result['score']}/100**\n\n"

        f"🎯 **ENTRY ไม้ 1/3**\n"
        f"💰 **{result['price']:.5f}**\n"

        f"📊 5M Master + 1M Filter\n"

        f"🔎 {result['reasons']}\n\n"

        f"🔒 **SERIES LOCK**\n"

        f"Direction = **{direction}**\n"

        f"1️⃣ ไม้ 1 → "
        f"**{direction}**\n"

        f"2️⃣ ถ้าแพ้ → "
        f"**{direction}** ไม้ 2\n"

        f"3️⃣ ถ้าแพ้อีก → "
        f"**{direction}** ไม้ 3\n\n"

        f"🏁 ชนะไม้ใด = "
        f"จบ Series\n"

        f"🛑 แพ้ครบ 3 = "
        f"FULL LOSS\n\n"

        f"🚫 **ระหว่าง Series "
        f"ไม่มีการเปลี่ยน Direction**"
    )

    send_discord(
        message
    )

    log(
        f"🔒 SERIES "
        f"#{SERIES_NUMBER} "
        f"LOCK "
        f"{result['symbol']} "
        f"{direction} "
        f"ENTRY="
        f"{result['price']:.5f}"
    )


# ============================================================
# EVALUATE STEP
# ============================================================

def evaluate_step(
    series,
    candle
):

    direction = (
        series["direction"]
    )

    step = (
        series["step"]
    )

    # --------------------------------------------------------
    # ENTRY
    # --------------------------------------------------------

    if step == 1:

        entry = (
            series["signal_price"]
        )

        entry_type = (
            "Signal Close"
        )

    else:

        entry = (
            candle["open"]
        )

        entry_type = (
            "New 5M Candle Open"
        )

    # --------------------------------------------------------
    # TP / SL Simulation
    # --------------------------------------------------------

    atr = (
        series["atr"]
    )

    distance = (
        atr
        * ATR_MULTIPLIER
    )

    if direction == "CALL":

        tp = (
            entry
            + distance
        )

        sl = (
            entry
            - distance
        )

        tp_hit = (
            candle["high"]
            >= tp
        )

        sl_hit = (
            candle["low"]
            <= sl
        )

        # สำหรับ Series นี้
        # ผลหลักใช้ CLOSE ของแท่ง
        # ไม่เปลี่ยน Direction กลางแท่ง
        is_win = (
            candle["close"]
            > entry
        )

    else:

        tp = (
            entry
            - distance
        )

        sl = (
            entry
            + distance
        )

        tp_hit = (
            candle["low"]
            <= tp
        )

        sl_hit = (
            candle["high"]
            >= sl
        )

        is_win = (
            candle["close"]
            < entry
        )

    return {

        "step":
            step,

        "direction":
            direction,

        "entry":
            entry,

        "entry_type":
            entry_type,

        "tp":
            tp,

        "sl":
            sl,

        "tp_hit":
            tp_hit,

        "sl_hit":
            sl_hit,

        "candle_time":
            candle["datetime"],

        "close":
            candle["close"],

        "result":
            "WIN"
            if is_win
            else "LOSS"
    }


# ============================================================
# PROCESS ACTIVE SERIES
# ============================================================

def process_active_series():

    global ACTIVE_SERIES

    if ACTIVE_SERIES is None:
        return

    series = ACTIVE_SERIES

    symbol = (
        series["symbol"]
    )

    direction = (
        series["direction"]
    )

    step = (
        series["step"]
    )

    candles = get_closed_candles(
        symbol,
        "5m"
    )

    if len(candles) < 2:
        return

    new_candles = [

        c

        for c in candles

        if c["datetime"]
        > series[
            "last_processed_candle"
        ]
    ]

    if not new_candles:
        return

    # ใช้แท่งใหม่ทีละแท่ง
    candle = new_candles[0]

    result = evaluate_step(
        series,
        candle
    )

    series[
        "last_processed_candle"
    ] = candle[
        "datetime"
    ]

    series[
        "step_results"
    ].append(
        result
    )

    # ========================================================
    # WIN
    # ========================================================

    if result["result"] == "WIN":

        series["wins"] += 1

        send_discord(

            f"🎯 **SERIES "
            f"#{series['series_id']} "
            f"→ WIN 🟢**\n"

            f"━━━━━━━━━━━━━━━━━━━━\n"

            f"🇹🇭 เวลาไทย: "
            f"**{now_text()}**\n"

            f"💱 {symbol}\n"

            f"📌 Direction: "
            f"**{direction}**\n"

            f"🏁 ผล: "
            f"**WIN ไม้ "
            f"{step}/3**\n\n"

            f"🎯 **ENTRY:** "
            f"**{result['entry']:.5f}**\n"

            f"📌 Entry Type: "
            f"{result['entry_type']}\n"

            f"📊 Close: "
            f"**{result['close']:.5f}**\n"

            f"🕐 Candle: "
            f"{result['candle_time']}\n\n"

            f"🔓 **Series จบแล้ว**\n"

            f"🔎 ระบบจะรอแท่ง 5M ใหม่ "
            f"เพื่อหา Setup ใหม่"
        )

        finish_series(
            status="WIN"
        )

        return

    # ========================================================
    # LOSS
    # ========================================================

    series["losses"] += 1

    # --------------------------------------------------------
    # LOSS แต่ยังเหลือไม้
    # --------------------------------------------------------

    if step < MAX_STEPS:

        next_step = (
            step + 1
        )

        series["step"] = (
            next_step
        )

        save_active_state()

        send_discord(

            f"⚠️ **SERIES "
            f"#{series['series_id']} "
            f"→ LOSS ไม้ "
            f"{step}/3**\n"

            f"━━━━━━━━━━━━━━━━━━━━\n"

            f"🇹🇭 เวลาไทย: "
            f"**{now_text()}**\n"

            f"💱 {symbol}\n"

            f"📌 Direction เดิม: "
            f"**{direction}**\n\n"

            f"❌ ENTRY ไม้ก่อนหน้า: "
            f"**{result['entry']:.5f}**\n"

            f"📊 Close: "
            f"**{result['close']:.5f}**\n\n"

            f"🔒 **DIRECTION LOCK**\n"

            f"🚫 ไม่คำนวณ Direction ใหม่\n"
            f"🚫 ไม่เปลี่ยนคู่\n"
            f"🚫 ไม่รับ Signal ใหม่\n\n"

            f"➡️ **NEXT ENTRY "
            f"{next_step}/3**\n"

            f"📌 Direction: "
            f"**{direction}**\n"

            f"🎯 Entry: "
            f"**ราคาเปิดแท่ง 5M ใหม่**\n\n"

            f"⏳ รอแท่ง 5M ถัดไป..."
        )

        log(

            f"LOSS "
            f"STEP {step}/3 → "

            f"LOCK "
            f"{direction} → "

            f"NEXT "
            f"{next_step}/3"
        )

        return

    # ========================================================
    # FULL LOSS
    # ========================================================

    send_discord(

        f"🛑 **SERIES "
        f"#{series['series_id']} "
        f"→ FULL LOSS 🔴**\n"

        f"━━━━━━━━━━━━━━━━━━━━\n"

        f"🇹🇭 เวลาไทย: "
        f"**{now_text()}**\n"

        f"💱 {symbol}\n"

        f"📌 Direction: "
        f"**{direction}**\n"

        f"❌ แพ้ครบ "
        f"**3/3 ไม้**\n\n"

        f"🔓 **Series ปลดล็อก**\n"

        f"⏳ ระบบจะรอแท่ง 5M ใหม่\n"

        f"🔎 แล้วจึงคำนวณ "
        f"Direction ใหม่"
    )

    finish_series(
        status="FULL_LOSS"
    )


# ============================================================
# FINISH SERIES
# ============================================================

def finish_series(
    status
):

    global ACTIVE_SERIES

    if ACTIVE_SERIES is None:
        return

    series = ACTIVE_SERIES

    win_at_step = next(

        (
            x["step"]

            for x
            in series[
                "step_results"
            ]

            if x["result"]
            == "WIN"
        ),

        0
    )

    record = {

        "series_id":
            series["series_id"],

        "symbol":
            series["symbol"],

        "decision":
            series["direction"],

        "score":
            series["score"],

        "setup_name":
            series["setup_name"],

        "signal_time":
            series["signal_time"],

        "signal_price":
            series["signal_price"],

        "status":
            status,

        "win_at_step":
            win_at_step,

        "steps":
            series["step_results"],

        "started_at":
            series["started_at"],

        "finished_at":
            now_text()
    }

    HISTORICAL_MEMORY.append(
        record
    )

    save_memory()

    log(
        f"🏁 SERIES "
        f"#{series['series_id']} "
        f"FINISHED: "
        f"{status}"
    )

    ACTIVE_SERIES = None

    save_active_state()


# ============================================================
# STATISTICS
# ============================================================

def print_stats():

    total = len(
        HISTORICAL_MEMORY
    )

    wins = sum(

        1

        for x
        in HISTORICAL_MEMORY

        if x.get("status")
        == "WIN"
    )

    losses = sum(

        1

        for x
        in HISTORICAL_MEMORY

        if x.get("status")
        == "FULL_LOSS"
    )

    win_step_1 = sum(

        1

        for x
        in HISTORICAL_MEMORY

        if x.get("win_at_step")
        == 1
    )

    win_step_2 = sum(

        1

        for x
        in HISTORICAL_MEMORY

        if x.get("win_at_step")
        == 2
    )

    win_step_3 = sum(

        1

        for x
        in HISTORICAL_MEMORY

        if x.get("win_at_step")
        == 3
    )

    rate = (

        wins
        / total
        * 100

        if total
        else 0
    )

    print()

    print(
        "======================================"
    )

    print(
        "📊 SIGZY SERIES STATISTICS"
    )

    print(
        "======================================"
    )

    print(
        f"Series:       {total}"
    )

    print(
        f"WIN:          {wins}"
    )

    print(
        f"FULL LOSS:    {losses}"
    )

    print(
        f"WIN RATE:     {rate:.2f}%"
    )

    print(
        "--------------------------------------"
    )

    print(
        f"WIN STEP 1:   {win_step_1}"
    )

    print(
        f"WIN STEP 2:   {win_step_2}"
    )

    print(
        f"WIN STEP 3:   {win_step_3}"
    )

    print(
        "======================================"
    )

    print()


# ============================================================
# STATUS MESSAGE
# ============================================================

def send_current_status():

    if ACTIVE_SERIES is None:

        send_discord(

            f"🟦 **SIGZY STATUS**\n"
            f"🇹🇭 เวลาไทย: {now_text()}\n"
            f"สถานะ: **WAITING FOR NEW SETUP**\n"
            f"🔎 กำลังรอแท่ง 5M ใหม่"
        )

        return

    series = ACTIVE_SERIES

    step = series["step"]

    send_discord(

        f"🔒 **SIGZY ACTIVE SERIES**\n"
        f"🇹🇭 เวลาไทย: {now_text()}\n"
        f"💱 {series['symbol']}\n"
        f"📌 Direction: **{series['direction']}**\n"
        f"🎯 Step: **{step}/3**\n"
        f"🏆 Setup Score: "
        f"**{series['score']}/100**\n"
        f"🔒 Direction LOCKED\n"
        f"🚫 ไม่มีการวิเคราะห์ใหม่กลาง Series"
    )


# ============================================================
# MAIN
# ============================================================

def main():

    load_memory()

    load_active_state()

    send_discord(

        "🚀 **SIGZY 5M SERIES LOCK "
        "UPGRADED START**\n\n"

        "🇹🇭 Thai Time Enabled\n"

        "🎯 Clear Entry Enabled\n"

        "5M = MASTER\n"
        "1M = FILTER\n\n"

        "🔒 1 Series = 1 Direction\n"

        "1 → 2 → 3 "
        "ทิศทางเดิม\n\n"

        "❌ ไม่มีการเปลี่ยน Direction "
        "กลาง Series\n"

        "❌ ไม่มี AI กลาง Series\n\n"

        "🏁 WIN = จบ Series\n"

        "🛑 3 LOSS = Full Loss\n\n"

        "🔎 จบ Series แล้วเท่านั้น "
        "จึงหา Setup ใหม่"
    )

    print_stats()

    while True:

        try:

            # ==================================================
            # MODE 1
            # ไม่มี Series
            # ==================================================

            if ACTIVE_SERIES is None:

                result = find_new_setup()

                if result:

                    start_series(
                        result
                    )

                else:

                    log(
                        "⏳ ไม่มี Setup "
                        "ที่ผ่าน Filter "
                        "→ รอแท่ง 5M ใหม่"
                    )

            # ==================================================
            # MODE 2
            # มี Series
            # ==================================================

            else:

                # สำคัญที่สุด
                #
                # ตรงนี้จะไม่เรียก
                # analyze_5m_strategy()
                #
                # Direction ถูก LOCK
                #
                process_active_series()

                series = (
                    ACTIVE_SERIES
                )

                if series:

                    log(

                        f"🔒 SERIES "
                        f"#{series['series_id']} | "

                        f"{series['symbol']} | "

                        f"{series['direction']} | "

                        f"STEP "
                        f"{series['step']}/3 | "

                        f"ENTRY MODE LOCK"
                    )

            print_stats()

        except Exception as e:

            log(
                f"⚠️ MAIN ERROR: {e}"
            )

        time.sleep(
            SCAN_SECONDS
        )


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    print(
        "=============================================="
    )

    print(
        "🚀 SIGZY 5M SERIES LOCK UPGRADED"
    )

    print(
        "5M MASTER + 1M FILTER"
    )

    print(
        "3 STEP SAME DIRECTION"
    )

    print(
        "THAI TIME + CLEAR ENTRY"
    )

    print(
        "STATE RECOVERY"
    )

    print(
        "=============================================="
    )

    main()
