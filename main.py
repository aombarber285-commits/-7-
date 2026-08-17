# -*- coding: utf-8 -*-

"""
SIGZY 5M SERIES LOCK
5M MASTER + 1M FILTER
3-STEP SAME-DIRECTION TRACKER

หลักการสำคัญ
------------------------------------------------------------
1. วิเคราะห์ Setup ใหม่เฉพาะตอน "ไม่มี Series กำลังเดิน"
2. เมื่อได้ CALL → ล็อก CALL จนครบ Series
3. เมื่อได้ PUT  → ล็อก PUT จนครบ Series
4. LOSS ไม้ 1 → ไม้ 2 ยังคงทิศเดิม
5. LOSS ไม้ 2 → ไม้ 3 ยังคงทิศเดิม
6. WIN ไม้ใด → จบ Series ทันที
7. LOSS ครบ 3 ไม้ → จบ Series
8. ระหว่าง Series:
      - ไม่วิเคราะห์ 5M ใหม่
      - ไม่เปลี่ยน Direction
      - ไม่รับ Signal ใหม่
      - ไม่ให้ AI วิเคราะห์มากวน
9. หลัง Series จบ → รอแท่ง 5M ใหม่ → วิเคราะห์ Setup ใหม่
10. ใช้แท่ง 5M ที่ปิดแล้ว
11. ใช้แท่ง 1M ที่ปิดแล้วเป็น Filter
12. บันทึกผลลง v13_memory_5m.json

หมายเหตุ:
ระบบนี้เป็นระบบจำลอง/ติดตามผล ไม่รับประกันกำไร
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

DISCORD_WEBHOOK_URL = os.getenv(
    "DISCORD_WEBHOOK_URL",
    ""
)

MEMORY_FILE = "v13_memory_5m.json"

# ตรวจทุก 30 วินาที
# แต่จะ "วิเคราะห์" เฉพาะตอนที่ไม่มี Series
SCAN_SECONDS = 30

MAX_STEPS = 3

EMA_PERIOD = 50
ATR_PERIOD = 14

# ใช้ ATR เป็นระยะ TP/SL สำหรับ Simulation
ATR_MULTIPLIER = 0.50

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

SENT_SIGNALS = set()

# มีได้ "Series เดียว" เพื่อไม่ให้สัญญาณหลายคู่มารบกวน
ACTIVE_SERIES = None

SERIES_NUMBER = 0

LAST_ANALYZED_CANDLE = {}


# ============================================================
# TIME
# ============================================================

THAI_TZ = timezone(timedelta(hours=7))


def thai_now():
    return datetime.now(timezone.utc).astimezone(THAI_TZ)


def now_text():
    return thai_now().strftime("%Y-%m-%d %H:%M:%S")


def log(message):
    print(f"[{now_text()}] {message}")


# ============================================================
# DISCORD
# ============================================================

def send_discord(message):
    if not DISCORD_WEBHOOK_URL:
        print("⚠️ DISCORD_WEBHOOK_URL ยังไม่ได้ตั้งค่า")
        return False

    try:
        response = requests.post(
            DISCORD_WEBHOOK_URL,
            json={"content": message},
            timeout=10
        )

        if response.status_code in (200, 204):
            print("✅ Discord ส่งสำเร็จ")
            return True

        print(
            f"❌ Discord Error: "
            f"{response.status_code} "
            f"{response.text[:200]}"
        )

    except Exception as e:
        print(f"❌ Discord Exception: {e}")

    return False


# ============================================================
# MEMORY
# ============================================================

def load_memory():
    global HISTORICAL_MEMORY

    if not os.path.exists(MEMORY_FILE):
        HISTORICAL_MEMORY = []
        log("📄 ยังไม่มี Memory — เริ่มฐานข้อมูลใหม่")
        return

    try:
        with open(
            MEMORY_FILE,
            "r",
            encoding="utf-8"
        ) as f:
            HISTORICAL_MEMORY = json.load(f)

        log(
            f"📂 โหลด Memory สำเร็จ "
            f"{len(HISTORICAL_MEMORY)} Series"
        )

    except Exception as e:
        log(f"⚠️ โหลด Memory ไม่สำเร็จ: {e}")
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
        log(f"⚠️ Save Memory Error: {e}")


# ============================================================
# MARKET DATA
# ============================================================

def get_market_data(symbol, interval):
    ticker_symbol = SYMBOL_MAP.get(
        symbol,
        symbol
    )

    try:
        ticker = yf.Ticker(ticker_symbol)

        if interval == "1m":
            period = "5d"
        else:
            period = "10d"

        df = ticker.history(
            period=period,
            interval=interval,
            auto_adjust=False
        )

        if df.empty:
            return []

        candles = []

        for idx, row in df.iterrows():

            if any(
                str(row.get(x)) == "nan"
                for x in ["Open", "High", "Low", "Close"]
            ):
                continue

            candles.append({
                "datetime": idx.strftime(
                    "%Y-%m-%d %H:%M:%S"
                ),
                "open": float(row["Open"]),
                "high": float(row["High"]),
                "low": float(row["Low"]),
                "close": float(row["Close"]),
            })

        return candles

    except Exception as e:
        log(
            f"[DATA ERROR] "
            f"{symbol} {interval}: {e}"
        )
        return []


# ============================================================
# CLOSED CANDLES
# ============================================================

def get_closed_candles(symbol, interval):
    candles = get_market_data(
        symbol,
        interval
    )

    if len(candles) < 3:
        return []

    # Yahoo แท่งล่าสุดอาจยังไม่ปิด
    # ตัดแท่งล่าสุดออก
    return candles[:-1]


# ============================================================
# INDICATORS
# ============================================================

def calculate_ema(candles, period=50):

    if len(candles) < period:
        return None

    closes = [
        c["close"]
        for c in candles
    ]

    multiplier = 2 / (period + 1)

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


def calculate_atr(
    candles,
    period=14
):

    if len(candles) < period + 1:
        return None

    trs = []

    for i in range(1, len(candles)):

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
# 5M + 1M STRATEGY
# ============================================================

def analyze_5m_strategy(symbol):

    candles_5m = get_closed_candles(
        symbol,
        "5m"
    )

    candles_1m = get_closed_candles(
        symbol,
        "1m"
    )

    if (
        len(candles_5m) < 60
        or len(candles_1m) < 60
    ):
        return {
            "decision": "WAIT",
            "score": 0
        }

    c5 = candles_5m[-1]
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

    # --------------------------------------------------------
    # 5M MASTER
    # --------------------------------------------------------

    bullish_5m = (
        c5["close"] > ema_5m
        and c5["close"] > c5["open"]
    )

    bearish_5m = (
        c5["close"] < ema_5m
        and c5["close"] < c5["open"]
    )

    # --------------------------------------------------------
    # 1M FILTER
    # --------------------------------------------------------

    bullish_1m = (
        c1["close"] > ema_1m
    )

    bearish_1m = (
        c1["close"] < ema_1m
    )

    # --------------------------------------------------------
    # DIRECTION
    # --------------------------------------------------------

    if bullish_5m and bullish_1m:

        direction = "CALL"

        reasons = (
            "5M Bullish + "
            "1M Momentum Confirm"
        )

    elif bearish_5m and bearish_1m:

        direction = "PUT"

        reasons = (
            "5M Bearish + "
            "1M Momentum Confirm"
        )

    else:

        return {
            "decision": "WAIT",
            "score": 0
        }

    price = c5["close"]

    tp_distance = (
        atr_5m
        * ATR_MULTIPLIER
    )

    if direction == "CALL":

        tp = price + tp_distance
        sl = price - tp_distance

    else:

        tp = price - tp_distance
        sl = price + tp_distance

    return {
        "decision": direction,
        "score": 85,
        "symbol": symbol,
        "price": price,
        "atr": atr_5m,
        "tp": tp,
        "sl": sl,
        "reasons": reasons,
        "candle_time": c5["datetime"],
        "setup_name":
            "5M_Strategy_1M_Filter",
    }


# ============================================================
# FIND NEW SETUP
# ============================================================

def find_new_setup():

    log(
        "🔎 ไม่มี Series "
        "กำลังค้นหา Setup ใหม่..."
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

            # ป้องกันใช้แท่งเดิมซ้ำ
            last_candle = (
                LAST_ANALYZED_CANDLE
                .get(symbol)
            )

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

def start_series(result):

    global ACTIVE_SERIES
    global SERIES_NUMBER

    SERIES_NUMBER += 1

    direction = result["decision"]

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
    }

    icon = (
        "🟢"
        if direction == "CALL"
        else "🔴"
    )

    message = (
        f"🎯 **NEW SERIES #{SERIES_NUMBER}**\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💱 คู่เงิน: **{result['symbol']}**\n"
        f"{icon} Direction: **{direction}**\n"
        f"🏆 Score: **{result['score']}/100**\n"
        f"💰 Entry: **{result['price']:.5f}**\n\n"
        f"📊 Setup:\n"
        f"• 5M Master\n"
        f"• 1M Filter\n"
        f"• {result['reasons']}\n\n"
        f"🔒 **SERIES LOCK**\n"
        f"ทิศทาง **{direction}** ถูกล็อก\n"
        f"จนกว่า Series นี้จะจบ\n\n"
        f"1️⃣ ไม้ 1 → {direction}\n"
        f"2️⃣ ถ้าแพ้ → {direction} ไม้ 2\n"
        f"3️⃣ ถ้าแพ้อีก → {direction} ไม้ 3\n"
        f"🏁 ชนะเมื่อไหร่ = จบ Series\n\n"
        f"⚠️ ระหว่าง Series "
        f"จะไม่มีการวิเคราะห์ Direction ใหม่"
    )

    send_discord(message)

    log(
        f"🔒 SERIES #{SERIES_NUMBER} "
        f"LOCK {result['symbol']} "
        f"{direction}"
    )


# ============================================================
# EVALUATE ONE STEP
# ============================================================

def evaluate_step(
    series,
    candle
):

    direction = series["direction"]

    step = series["step"]

    # --------------------------------------------------------
    # ไม้ 1 ใช้ Entry จาก Signal
    # ไม้ 2/3 ใช้ Open ของแท่งใหม่
    # --------------------------------------------------------

    if step == 1:

        entry = series["signal_price"]

    else:

        entry = candle["open"]

    atr = series["atr"]

    distance = (
        atr
        * ATR_MULTIPLIER
    )

    if direction == "CALL":

        tp = entry + distance
        sl = entry - distance

        tp_hit = (
            candle["high"] >= tp
        )

        sl_hit = (
            candle["low"] <= sl
        )

        # หากชนทั้ง TP และ SL
        # ในแท่งเดียวกัน เราไม่เดาว่าอะไรเกิดก่อน
        # ให้ใช้ Close เป็นตัวตัดสิน
        if tp_hit and sl_hit:

            is_win = (
                candle["close"]
                > entry
            )

            outcome_note = (
                "TP/SL ชนในแท่งเดียวกัน "
                "ใช้ Close ตัดสิน"
            )

        else:

            is_win = (
                candle["close"]
                > entry
            )

            outcome_note = (
                "Close Direction"
            )

    else:

        tp = entry - distance
        sl = entry + distance

        tp_hit = (
            candle["low"] <= tp
        )

        sl_hit = (
            candle["high"] >= sl
        )

        if tp_hit and sl_hit:

            is_win = (
                candle["close"]
                < entry
            )

            outcome_note = (
                "TP/SL ชนในแท่งเดียวกัน "
                "ใช้ Close ตัดสิน"
            )

        else:

            is_win = (
                candle["close"]
                < entry
            )

            outcome_note = (
                "Close Direction"
            )

    result = {
        "step": step,
        "direction": direction,
        "entry": entry,
        "tp": tp,
        "sl": sl,
        "candle_time":
            candle["datetime"],
        "close":
            candle["close"],
        "result":
            "WIN"
            if is_win
            else "LOSS",
        "note":
            outcome_note,
    }

    return result


# ============================================================
# PROCESS ACTIVE SERIES
# ============================================================

def process_active_series():

    global ACTIVE_SERIES

    if ACTIVE_SERIES is None:
        return

    series = ACTIVE_SERIES

    symbol = series["symbol"]

    direction = series["direction"]

    step = series["step"]

    candles = get_closed_candles(
        symbol,
        "5m"
    )

    if len(candles) < 2:
        return

    # --------------------------------------------------------
    # หาแท่งที่ยังไม่ได้ประเมิน
    # --------------------------------------------------------

    new_candles = [
        c
        for c in candles
        if c["datetime"]
        > series["last_processed_candle"]
    ]

    if not new_candles:
        return

    # ใช้ทีละแท่ง
    candle = new_candles[0]

    result = evaluate_step(
        series,
        candle
    )

    # ป้องกันประเมินแท่งซ้ำ
    series["last_processed_candle"] = (
        candle["datetime"]
    )

    series["step_results"].append(
        result
    )

    if result["result"] == "WIN":

        series["wins"] += 1

        send_discord(
            f"🎯 **SERIES #{series['series_id']} "
            f"→ WIN 🟢**\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"💱 {symbol}\n"
            f"📌 Direction: **{direction}**\n"
            f"🏁 ชนะที่ไม้: **{step}/3**\n"
            f"💰 Entry: **{result['entry']:.5f}**\n"
            f"📊 Close: **{result['close']:.5f}**\n\n"
            f"🔓 Series จบแล้ว\n"
            f"ระบบจะกลับไปหา Setup ใหม่"
        )

        finish_series(
            status="WIN"
        )

        return

    # --------------------------------------------------------
    # LOSS
    # --------------------------------------------------------

    series["losses"] += 1

    if step < MAX_STEPS:

        next_step = step + 1

        series["step"] = next_step

        # สำคัญมาก:
        # Direction ไม่ถูกคำนวณใหม่
        send_discord(
            f"⚠️ **SERIES #{series['series_id']} "
            f"→ LOSS ไม้ {step}**\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"💱 {symbol}\n"
            f"📌 Direction ยังคง: **{direction}**\n\n"
            f"🔒 **ห้ามเปลี่ยนทิศทาง**\n"
            f"➡️ ไม้ถัดไป: **{direction} "
            f"ไม้ {next_step}/3**\n\n"
            f"❗ ระบบจะไม่วิเคราะห์ 5M/1M ใหม่ "
            f"ระหว่าง Series"
        )

        log(
            f"LOSS STEP {step} → "
            f"LOCK {direction} "
            f"STEP {next_step}"
        )

        return

    # --------------------------------------------------------
    # FULL LOSS
    # --------------------------------------------------------

    send_discord(
        f"🛑 **SERIES #{series['series_id']} "
        f"→ FULL LOSS 🔴**\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💱 {symbol}\n"
        f"📌 Direction: **{direction}**\n"
        f"❌ แพ้ครบ **3/3 ไม้**\n\n"
        f"🔓 Series จบแล้ว\n"
        f"ระบบจะเริ่มค้นหา Direction ใหม่"
    )

    finish_series(
        status="FULL_LOSS"
    )


# ============================================================
# FINISH SERIES
# ============================================================

def finish_series(status):

    global ACTIVE_SERIES

    if ACTIVE_SERIES is None:
        return

    series = ACTIVE_SERIES

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

        "status":
            status,

        "steps":
            series["step_results"],

        "win_at_step":
            next(
                (
                    x["step"]
                    for x
                    in series["step_results"]
                    if x["result"] == "WIN"
                ),
                0
            ),

        "recorded_at":
            now_text(),
    }

    HISTORICAL_MEMORY.append(record)

    save_memory()

    log(
        f"🏁 SERIES #{series['series_id']} "
        f"FINISHED: {status}"
    )

    ACTIVE_SERIES = None


# ============================================================
# STATS
# ============================================================

def print_stats():

    total = len(
        HISTORICAL_MEMORY
    )

    wins = sum(
        1
        for x in HISTORICAL_MEMORY
        if x.get("status") == "WIN"
    )

    losses = sum(
        1
        for x in HISTORICAL_MEMORY
        if x.get("status") == "FULL_LOSS"
    )

    win_step_1 = sum(
        1
        for x in HISTORICAL_MEMORY
        if x.get("win_at_step") == 1
    )

    win_step_2 = sum(
        1
        for x in HISTORICAL_MEMORY
        if x.get("win_at_step") == 2
    )

    win_step_3 = sum(
        1
        for x in HISTORICAL_MEMORY
        if x.get("win_at_step") == 3
    )

    rate = (
        (wins / total) * 100
        if total
        else 0
    )

    print()
    print("======================================")
    print("📊 SIGZY SERIES STATISTICS")
    print("======================================")
    print(f"Series:       {total}")
    print(f"WIN:          {wins}")
    print(f"FULL LOSS:    {losses}")
    print(f"WIN RATE:     {rate:.2f}%")
    print("--------------------------------------")
    print(f"WIN STEP 1:   {win_step_1}")
    print(f"WIN STEP 2:   {win_step_2}")
    print(f"WIN STEP 3:   {win_step_3}")
    print("======================================")
    print()


# ============================================================
# MAIN
# ============================================================

def main():

    load_memory()

    send_discord(
        "🚀 **SIGZY 5M SERIES LOCK START**\n"
        "5M Master + 1M Filter\n\n"
        "🔒 1 Series = 1 Direction\n"
        "1 → 2 → 3 ไม้ทิศเดิม\n"
        "ไม่มีการเปลี่ยน Direction กลาง Series\n"
        "จบ Series แล้วจึงหา Setup ใหม่"
    )

    print_stats()

    while True:

        try:

            # ==================================================
            # MODE 1: ไม่มี Series
            # ==================================================

            if ACTIVE_SERIES is None:

                result = find_new_setup()

                if result:

                    start_series(
                        result
                    )

                else:

                    log(
                        "⏳ ยังไม่มี Setup "
                        "รอแท่ง 5M ใหม่..."
                    )

            # ==================================================
            # MODE 2: มี Series
            # ==================================================

            else:

                # สำคัญ:
                # ตรงนี้ไม่เรียก analyze_5m_strategy()
                # และไม่หา Direction ใหม่
                process_active_series()

                series = ACTIVE_SERIES

                if series:

                    log(
                        f"🔒 SERIES "
                        f"#{series['series_id']} | "
                        f"{series['symbol']} | "
                        f"{series['direction']} | "
                        f"STEP "
                        f"{series['step']}/3"
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
        "🚀 SIGZY 5M SERIES LOCK"
    )

    print(
        "5M MASTER + 1M FILTER"
    )

    print(
        "3 STEP SAME DIRECTION"
    )

    print(
        "=============================================="
    )

    main()
