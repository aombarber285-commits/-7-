# -*- coding: utf-8 -*-

import os
import json
import time
import math
import requests
import yfinance as yf

from datetime import datetime, timezone, timedelta
from threading import Thread, Lock
from http.server import HTTPServer, BaseHTTPRequestHandler

try:
    from google import genai
except Exception:
    genai = None


# ============================================================
# SIGZY 15M + 5M CONFLUENCE / CLOSE RESULT / 3-OPP TRACKER
# Railway / Python 3.10
#
# PRINCIPLES
# 1) 15M = PRIMARY SIGNAL
# 2) 5M = CONFIRMATION / CONTEXT, NEVER A HARD BLOCK
# 3) CLOSED CANDLES ONLY
# 4) RESULT = CANDLE CLOSE
# 5) WIN / LOSS / DRAW
# 6) OPP1 -> OPP2 -> OPP3 tracker
# 7) Persistent memory
# 8) Gemini failure must NOT stop scanner
# 9) Railway PORT compatible
# ============================================================


# ============================================================
# CONFIG
# ============================================================

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()

GEMINI_MODEL = os.getenv(
    "GEMINI_MODEL",
    "gemini-2.5-flash"
)

PORT = int(os.getenv("PORT", "8080"))

MEMORY_FILE = os.getenv(
    "MEMORY_FILE",
    "v13_memory.json"
)

SIGNAL_TIMEFRAME = "15m"
CONFIRM_TIMEFRAME = "5m"

# สแกนทุก 3 นาที เพื่อไม่พลาดช่วงปิดแท่ง
SCAN_SECONDS = 180

# ไม่บังคับให้ Signal ต้องถึง 3/3
# เพราะเราไม่ต้องการปิดโอกาสมากเกินไป
SIGNAL_SCORE = 72

# ถ้าคะแนนถึงระดับนี้ แต่ยังไม่ถึง Signal
# ใช้แค่ WATCH log ไม่ส่ง Signal
WATCH_SCORE = 62

# จำนวนโอกาสสูงสุดของ Tracker
MAX_OPPORTUNITIES = 3

# Tracker ใช้ 5M เพื่อดูพฤติกรรมระยะสั้น
TRACKER_INTERVAL = "5m"

# ถ้าใช้ close-based result จะไม่ใช้ TP/SL เป็นตัวตัดสิน
# ATR ยังใช้เป็นข้อมูล MFE/MAE ได้
ATR_PERIOD = 14


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

SYMBOLS = list(SYMBOL_MAP.keys())


# ============================================================
# GLOBAL MEMORY
# ============================================================

SENT_SIGNALS = set()

PENDING_TRADES = []

TRADE_HISTORY = []

HISTORICAL_MEMORY = []

ACTIVE_TRACKERS = []

STATE_LOCK = Lock()


# ============================================================
# TIME
# ============================================================

def now_text():
    utc_now = datetime.now(timezone.utc)
    thai = utc_now + timedelta(hours=7)
    return thai.strftime("%Y-%m-%d %H:%M:%S")


def log(msg):
    print(f"[{now_text()}] {msg}", flush=True)


# ============================================================
# DISCORD
# ============================================================

def send_discord(message):
    if not DISCORD_WEBHOOK_URL:
        log("⚠️ DISCORD_WEBHOOK_URL ยังไม่ได้ตั้งค่า")
        return False

    try:
        response = requests.post(
            DISCORD_WEBHOOK_URL,
            json={"content": message},
            timeout=8
        )

        if response.status_code in (200, 204):
            return True

        log(
            f"Discord Error: "
            f"{response.status_code} "
            f"{response.text[:200]}"
        )

    except Exception as e:
        log(f"Discord Exception: {e}")

    return False


# ============================================================
# SAFE FLOAT
# ============================================================

def safe_float(value, default=0.0):
    try:
        value = float(value)

        if math.isnan(value) or math.isinf(value):
            return default

        return value

    except Exception:
        return default


# ============================================================
# YAHOO DATA
# ============================================================

def get_yahoo_candles(symbol, interval="15m", period="3d"):
    yf_symbol = SYMBOL_MAP.get(symbol, symbol)

    try:
        ticker = yf.Ticker(yf_symbol)

        df = ticker.history(
            period=period,
            interval=interval,
            auto_adjust=False,
            prepost=False
        )

        if df is None or df.empty:
            return []

        candles = []

        for idx, row in df.iterrows():

            o = safe_float(row.get("Open"))
            h = safe_float(row.get("High"))
            l = safe_float(row.get("Low"))
            c = safe_float(row.get("Close"))

            if not all([o, h, l, c]):
                continue

            candles.append({
                "datetime": idx.strftime(
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
            f"[Yahoo {symbol} {interval}] "
            f"Error: {e}"
        )

        return []


def get_closed_candles(
    symbol,
    interval="15m",
    period="3d"
):
    """
    ตัดแท่งล่าสุดออกเสมอ
    เพราะแท่งล่าสุดอาจกำลังวิ่งอยู่
    """

    candles = get_yahoo_candles(
        symbol,
        interval,
        period
    )

    if len(candles) < 3:
        return []

    return candles[:-1]


# ============================================================
# ATR
# ============================================================

def calculate_atr(candles, period=14):

    if len(candles) < period + 1:
        return None

    trs = []

    for i in range(1, len(candles)):

        current = candles[i]
        previous = candles[i - 1]

        tr = max(
            current["high"] - current["low"],
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

    if len(trs) < period:
        return None

    return sum(trs[-period:]) / period


# ============================================================
# EMA
# ============================================================

def calculate_ema(candles, period):

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


# ============================================================
# RSI
# ============================================================

def calculate_rsi(candles, period=14):

    if len(candles) < period + 1:
        return None

    closes = [
        c["close"]
        for c in candles
    ]

    gains = []
    losses = []

    for i in range(1, len(closes)):

        change = closes[i] - closes[i - 1]

        if change > 0:
            gains.append(change)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(abs(change))

    avg_gain = sum(
        gains[:period]
    ) / period

    avg_loss = sum(
        losses[:period]
    ) / period

    for i in range(period, len(gains)):

        avg_gain = (
            (avg_gain * (period - 1))
            + gains[i]
        ) / period

        avg_loss = (
            (avg_loss * (period - 1))
            + losses[i]
        ) / period

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss

    return 100 - (
        100 / (1 + rs)
    )


# ============================================================
# CANDLE PATTERN
# ============================================================

def candle_pattern(c0, c1):

    body = abs(
        c0["close"] - c0["open"]
    )

    candle_range = max(
        c0["high"] - c0["low"],
        1e-12
    )

    upper = (
        c0["high"]
        - max(
            c0["open"],
            c0["close"]
        )
    )

    lower = (
        min(
            c0["open"],
            c0["close"]
        )
        - c0["low"]
    )

    ratio = body / candle_range

    strong_bull = (
        c0["close"] > c0["open"]
        and ratio >= 0.70
    )

    strong_bear = (
        c0["close"] < c0["open"]
        and ratio >= 0.70
    )

    hammer = (
        lower >= body * 2.5
        and upper <= candle_range * 0.20
        and ratio <= 0.40
    )

    shooting_star = (
        upper >= body * 2.5
        and lower <= candle_range * 0.20
        and ratio <= 0.40
    )

    bullish_engulfing = (
        c0["close"] > c0["open"]
        and c1["close"] < c1["open"]
        and c0["open"] <= c1["close"]
        and c0["close"] >= c1["open"]
        and body >
        abs(
            c1["close"]
            - c1["open"]
        )
    )

    bearish_engulfing = (
        c0["close"] < c0["open"]
        and c1["close"] > c1["open"]
        and c0["open"] >= c1["close"]
        and c0["close"] <= c1["open"]
        and body >
        abs(
            c1["close"]
            - c1["open"]
        )
    )

    return {
        "bull":
            strong_bull
            or hammer
            or bullish_engulfing,

        "bear":
            strong_bear
            or shooting_star
            or bearish_engulfing,

        "strong_bull":
            strong_bull,

        "strong_bear":
            strong_bear,

        "bullish_engulfing":
            bullish_engulfing,

        "bearish_engulfing":
            bearish_engulfing,

        "ratio":
            ratio
    }


# ============================================================
# 15M PRIMARY ANALYSIS
# ============================================================

def analyze_15m(symbol):

    candles = get_closed_candles(
        symbol,
        "15m",
        "3d"
    )

    if len(candles) < 60:
        return {
            "decision": "WAIT",
            "score": 0
        }

    c0 = candles[-1]
    c1 = candles[-2]

    price = c0["close"]

    ema50 = calculate_ema(
        candles,
        50
    )

    ema20 = calculate_ema(
        candles,
        20
    )

    rsi = calculate_rsi(
        candles,
        14
    )

    atr = calculate_atr(
        candles,
        ATR_PERIOD
    )

    if not all(
        x is not None
        for x in [ema50, ema20, rsi, atr]
    ):
        return {
            "decision": "WAIT",
            "score": 0
        }

    pattern = candle_pattern(
        c0,
        c1
    )

    call_score = 50
    put_score = 50

    call_reasons = []
    put_reasons = []

    # --------------------------------------------------------
    # EMA50 = PRIMARY TREND
    # --------------------------------------------------------

    if price > ema50:
        call_score += 10
        call_reasons.append(
            "ราคาเหนือ EMA50"
        )

    elif price < ema50:
        put_score += 10
        put_reasons.append(
            "ราคาใต้ EMA50"
        )

    # --------------------------------------------------------
    # EMA20 = MOMENTUM
    # --------------------------------------------------------

    if price > ema20:
        call_score += 6
        call_reasons.append(
            "ราคาเหนือ EMA20"
        )

    elif price < ema20:
        put_score += 6
        put_reasons.append(
            "ราคาใต้ EMA20"
        )

    # --------------------------------------------------------
    # RSI
    #
    # ไม่ใช้แบบสุดโต่งจนปิดโอกาส
    # --------------------------------------------------------

    if 52 <= rsi <= 68:
        call_score += 7
        call_reasons.append(
            f"RSI สนับสนุน CALL ({rsi:.1f})"
        )

    elif 32 <= rsi <= 48:
        put_score += 7
        put_reasons.append(
            f"RSI สนับสนุน PUT ({rsi:.1f})"
        )

    # --------------------------------------------------------
    # CANDLE
    # --------------------------------------------------------

    if pattern["bull"]:
        call_score += 12
        call_reasons.append(
            "Bullish Candle"
        )

    if pattern["bear"]:
        put_score += 12
        put_reasons.append(
            "Bearish Candle"
        )

    # Strong body bonus
    if pattern["strong_bull"]:
        call_score += 4

    if pattern["strong_bear"]:
        put_score += 4

    # Engulfing bonus
    if pattern["bullish_engulfing"]:
        call_score += 5
        call_reasons.append(
            "Bullish Engulfing"
        )

    if pattern["bearish_engulfing"]:
        put_score += 5
        put_reasons.append(
            "Bearish Engulfing"
        )

    call_score = min(
        int(call_score),
        99
    )

    put_score = min(
        int(put_score),
        99
    )

    # --------------------------------------------------------
    # PRIMARY DECISION
    #
    # 15M เป็นตัวหลัก
    # ไม่ต้องรอ 5M
    # --------------------------------------------------------

    if (
        call_score >= SIGNAL_SCORE
        and call_score > put_score
    ):
        direction = "CALL"
        score = call_score
        reasons = call_reasons

    elif (
        put_score >= SIGNAL_SCORE
        and put_score > call_score
    ):
        direction = "PUT"
        score = put_score
        reasons = put_reasons

    else:

        if max(
            call_score,
            put_score
        ) >= WATCH_SCORE:

            return {
                "decision": "WATCH",
                "score": max(
                    call_score,
                    put_score
                ),
                "symbol": symbol,
                "price": price,
                "candle_time": c0["datetime"]
            }

        return {
            "decision": "WAIT",
            "score": 0
        }

    return {
        "decision": direction,
        "score": score,
        "symbol": symbol,
        "price": price,
        "atr": atr,
        "ema20": ema20,
        "ema50": ema50,
        "rsi": rsi,
        "reasons": " | ".join(reasons),
        "candle_time": c0["datetime"],
        "setup_name": "SIGZY_15M_PRIMARY"
    }


# ============================================================
# 5M CONTEXT
# ============================================================

def analyze_5m_context(symbol):

    candles = get_closed_candles(
        symbol,
        "5m",
        "2d"
    )

    if len(candles) < 60:
        return {
            "direction": "UNKNOWN",
            "score": 0,
            "reason": "5M data unavailable"
        }

    c0 = candles[-1]
    c1 = candles[-2]

    ema20 = calculate_ema(
        candles,
        20
    )

    ema50 = calculate_ema(
        candles,
        50
    )

    rsi = calculate_rsi(
        candles,
        14
    )

    if not all(
        x is not None
        for x in [ema20, ema50, rsi]
    ):
        return {
            "direction": "UNKNOWN",
            "score": 0,
            "reason": "5M indicators unavailable"
        }

    pattern = candle_pattern(
        c0,
        c1
    )

    call = 50
    put = 50

    reasons_call = []
    reasons_put = []

    if c0["close"] > ema20:
        call += 7
        reasons_call.append(
            "เหนือ EMA20"
        )

    else:
        put += 7
        reasons_put.append(
            "ใต้ EMA20"
        )

    if c0["close"] > ema50:
        call += 7
        reasons_call.append(
            "เหนือ EMA50"
        )

    else:
        put += 7
        reasons_put.append(
            "ใต้ EMA50"
        )

    if rsi >= 52:
        call += 6
        reasons_call.append(
            f"RSI {rsi:.1f}"
        )

    elif rsi <= 48:
        put += 6
        reasons_put.append(
            f"RSI {rsi:.1f}"
        )

    if pattern["bull"]:
        call += 8
        reasons_call.append(
            "Bullish Candle"
        )

    if pattern["bear"]:
        put += 8
        reasons_put.append(
            "Bearish Candle"
        )

    if call > put:

        return {
            "direction": "CALL",
            "score": min(call, 99),
            "reason":
                " | ".join(reasons_call)
        }

    if put > call:

        return {
            "direction": "PUT",
            "score": min(put, 99),
            "reason":
                " | ".join(reasons_put)
        }

    return {
        "direction": "NEUTRAL",
        "score": 50,
        "reason": "5M Neutral"
    }


# ============================================================
# CONFLUENCE
#
# IMPORTANT:
# 5M NEVER BLOCKS 15M
# ============================================================

def apply_5m_context(
    primary,
    context
):

    direction = primary["decision"]

    if direction not in ("CALL", "PUT"):
        return primary

    five_direction = context.get(
        "direction",
        "UNKNOWN"
    )

    five_score = context.get(
        "score",
        0
    )

    if five_direction == direction:

        primary["score"] = min(
            99,
            primary["score"] + 5
        )

        primary["context"] = (
            "5M_CONFIRM"
        )

        primary["context_reason"] = (
            f"5M {five_direction} "
            f"{five_score}/100 "
            f"ตรงกับ 15M"
        )

    elif five_direction in (
        "CALL",
        "PUT"
    ):

        # สวนกัน แต่ไม่ block
        primary["score"] = max(
            70,
            primary["score"] - 3
        )

        primary["context"] = (
            "COUNTER_TREND"
        )

        primary["context_reason"] = (
            f"5M {five_direction} "
            f"{five_score}/100 "
            f"สวน 15M {direction}"
        )

    else:

        primary["context"] = (
            "5M_UNKNOWN"
        )

        primary["context_reason"] = (
            "ไม่มี 5M confirmation"
        )

    return primary


# ============================================================
# SIGNAL KEY
# ============================================================

def make_signal_key(result):

    return (
        result["symbol"],
        result["candle_time"],
        result["decision"]
    )


# ============================================================
# TRACKER MEMORY
# ============================================================

def load_memory():

    global HISTORICAL_MEMORY

    if not os.path.exists(
        MEMORY_FILE
    ):

        log(
            "📄 ไม่พบ Memory "
            "เริ่มฐานข้อมูลใหม่"
        )

        HISTORICAL_MEMORY = []

        return

    try:

        with open(
            MEMORY_FILE,
            "r",
            encoding="utf-8"
        ) as f:

            data = json.load(f)

        if isinstance(data, list):

            HISTORICAL_MEMORY = data

        else:

            HISTORICAL_MEMORY = []

        log(
            f"📂 โหลด Memory สำเร็จ "
            f"{len(HISTORICAL_MEMORY)} รายการ"
        )

    except Exception as e:

        log(
            f"⚠️ Memory load error: {e}"
        )

        HISTORICAL_MEMORY = []


def save_memory():

    try:

        tmp_file = (
            MEMORY_FILE
            + ".tmp"
        )

        with open(
            tmp_file,
            "w",
            encoding="utf-8"
        ) as f:

            json.dump(
                HISTORICAL_MEMORY,
                f,
                ensure_ascii=False,
                indent=2
            )

        os.replace(
            tmp_file,
            MEMORY_FILE
        )

    except Exception as e:

        log(
            f"⚠️ Memory save error: {e}"
        )


# ============================================================
# RECORD RESULT
# ============================================================

def record_history(
    tracker,
    status,
    step,
    entry,
    close_price,
    mfe,
    mae
):

    record = {
        "symbol":
            tracker["symbol"],

        "decision":
            tracker["decision"],

        "score":
            tracker["score"],

        "setup_name":
            tracker["setup_name"],

        "signal_time":
            tracker["signal_time"],

        "status":
            status,

        "win_at_step":
            step if status == "WIN"
            else 0,

        "step":
            step,

        "entry_price":
            entry,

        "close_price":
            close_price,

        "price_change":
            close_price - entry,

        "max_mfe":
            tracker.get(
                "max_mfe",
                0
            ),

        "max_mae":
            tracker.get(
                "max_mae",
                0
            ),

        "step_mfe":
            mfe,

        "step_mae":
            mae,

        "five_context":
            tracker.get(
                "five_context",
                "UNKNOWN"
            ),

        "five_score":
            tracker.get(
                "five_score",
                0
            ),

        "recorded_at":
            now_text()
    }

    HISTORICAL_MEMORY.append(
        record
    )

    save_memory()


# ============================================================
# CLOSE-BASED RESULT
# ============================================================

def evaluate_close(
    direction,
    entry,
    close
):

    # CALL
    if direction == "CALL":

        if close > entry:
            return "WIN"

        if close < entry:
            return "LOSS"

        return "DRAW"

    # PUT
    if direction == "PUT":

        if close < entry:
            return "WIN"

        if close > entry:
            return "LOSS"

        return "DRAW"

    return "DRAW"


# ============================================================
# TRACKER
#
# 5M CLOSED CANDLE
# RESULT = CLOSE
#
# High/Low ONLY for MFE/MAE
# ============================================================

def run_tracker():

    global ACTIVE_TRACKERS

    if not ACTIVE_TRACKERS:
        return

    remaining = []

    for tracker in ACTIVE_TRACKERS:

        try:

            candles = get_closed_candles(
                tracker["symbol"],
                TRACKER_INTERVAL,
                "2d"
            )

            if len(candles) < 3:

                remaining.append(
                    tracker
                )

                continue

            future = [
                c for c in candles
                if c["datetime"]
                > tracker["last_checked_time"]
            ]

            step = tracker["step"]

            if len(future) < step:

                remaining.append(
                    tracker
                )

                continue

            target = future[
                step - 1
            ]

            # ------------------------------------------------
            # Entry
            # ------------------------------------------------

            entry = (
                tracker["entry_price"]
                if step == 1
                else target["open"]
            )

            close_price = target["close"]

            direction = tracker[
                "decision"
            ]

            # ------------------------------------------------
            # CLOSE = FINAL RESULT
            # ------------------------------------------------

            status = evaluate_close(
                direction,
                entry,
                close_price
            )

            # ------------------------------------------------
            # MFE / MAE
            #
            # เก็บไว้ดูพฤติกรรม
            # ไม่ใช้ตัด WIN/LOSS
            # ------------------------------------------------

            if direction == "CALL":

                mfe = (
                    target["high"]
                    - entry
                )

                mae = (
                    entry
                    - target["low"]
                )

            else:

                mfe = (
                    entry
                    - target["low"]
                )

                mae = (
                    target["high"]
                    - entry
                )

            mfe = max(
                mfe,
                0
            )

            mae = max(
                mae,
                0
            )

            tracker["max_mfe"] = max(
                tracker.get(
                    "max_mfe",
                    0
                ),
                mfe
            )

            tracker["max_mae"] = max(
                tracker.get(
                    "max_mae",
                    0
                ),
                mae
            )

            tracker[
                "last_checked_time"
            ] = target["datetime"]

            # ------------------------------------------------
            # WIN
            # ------------------------------------------------

            if status == "WIN":

                record_history(
                    tracker,
                    "WIN",
                    step,
                    entry,
                    close_price,
                    mfe,
                    mae
                )

                send_discord(
                    f"🎯 **SIGZY RESULT**\n"
                    f"💱 {tracker['symbol']}\n"
                    f"📌 {direction}\n"
                    f"🏁 **WIN 🟢**\n"
                    f"🎯 OPP{step}/3\n"
                    f"Entry: `{entry:.5f}`\n"
                    f"Close: `{close_price:.5f}`\n"
                    f"📈 MFE: `{tracker['max_mfe']:.5f}`\n"
                    f"📉 MAE: `{tracker['max_mae']:.5f}`"
                )

                continue

            # ------------------------------------------------
            # DRAW
            #
            # DRAW ไม่ถือว่า LOSS
            # ให้ข้ามไปดูโอกาสถัดไป
            # ------------------------------------------------

            if status == "DRAW":

                record_history(
                    tracker,
                    "DRAW",
                    step,
                    entry,
                    close_price,
                    mfe,
                    mae
                )

                if step < MAX_OPPORTUNITIES:

                    tracker["step"] += 1

                    remaining.append(
                        tracker
                    )

                else:

                    send_discord(
                        f"⚪ **SIGZY RESULT**\n"
                        f"💱 {tracker['symbol']}\n"
                        f"📌 {direction}\n"
                        f"🏁 **DRAW ⚪**\n"
                        f"ครบ {step} โอกาส"
                    )

                continue

            # ------------------------------------------------
            # LOSS
            # ------------------------------------------------

            if status == "LOSS":

                if step < MAX_OPPORTUNITIES:

                    tracker["step"] += 1

                    remaining.append(
                        tracker
                    )

                    send_discord(
                        f"⚠️ **SIGZY OPP{step}**\n"
                        f"💱 {tracker['symbol']}\n"
                        f"📌 {direction}\n"
                        f"ผล: **LOSS 🔴**\n"
                        f"Entry: `{entry:.5f}`\n"
                        f"Close: `{close_price:.5f}`\n"
                        f"➡️ ไป OPP{step + 1}"
                    )

                else:

                    record_history(
                        tracker,
                        "FULL_LOSS",
                        step,
                        entry,
                        close_price,
                        mfe,
                        mae
                    )

                    send_discord(
                        f"🛑 **SIGZY SERIES RESULT**\n"
                        f"💱 {tracker['symbol']}\n"
                        f"📌 {direction}\n"
                        f"🏁 **FULL LOSS 🔴**\n"
                        f"แพ้ครบ 3 โอกาส"
                    )

        except Exception as e:

            log(
                f"[Tracker Error] "
                f"{tracker.get('symbol')}: {e}"
            )

            remaining.append(
                tracker
            )

    ACTIVE_TRACKERS = remaining


# ============================================================
# CREATE TRACKER
# ============================================================

def create_tracker(result):

    tracker = {
        "symbol":
            result["symbol"],

        "decision":
            result["decision"],

        "score":
            result["score"],

        "setup_name":
            result["setup_name"],

        "signal_time":
            result["candle_time"],

        "entry_price":
            result["price"],

        "atr":
            result.get("atr", 0),

        "step":
            1,

        "max_mfe":
            0,

        "max_mae":
            0,

        "last_checked_time":
            result["candle_time"],

        "five_context":
            result.get(
                "context",
                "UNKNOWN"
            ),

        "five_score":
            result.get(
                "five_score",
                0
            )
    }

    ACTIVE_TRACKERS.append(
        tracker
    )


# ============================================================
# SYSTEM 1 SCANNER
# ============================================================

def run_scanner():

    log(
        "🔍 [ระบบ 1] "
        "กำลังสแกน 15M..."
    )

    for symbol in SYMBOLS:

        try:

            primary = analyze_15m(
                symbol
            )

            if primary["decision"] not in (
                "CALL",
                "PUT"
            ):

                if (
                    primary["decision"]
                    == "WATCH"
                ):

                    log(
                        f"👀 WATCH "
                        f"{symbol} "
                        f"{primary['score']}"
                    )

                continue

            # ------------------------------------------------
            # 5M CONTEXT
            # ------------------------------------------------

            context = analyze_5m_context(
                symbol
            )

            primary = apply_5m_context(
                primary,
                context
            )

            primary["five_score"] = (
                context.get(
                    "score",
                    0
                )
            )

            # ------------------------------------------------
            # SIGNAL KEY
            # ------------------------------------------------

            signal_key = make_signal_key(
                primary
            )

            if signal_key in SENT_SIGNALS:
                continue

            SENT_SIGNALS.add(
                signal_key
            )

            create_tracker(
                primary
            )

            icon = (
                "🟢"
                if primary["decision"]
                == "CALL"
                else "🔴"
            )

            context_icon = {
                "5M_CONFIRM": "⭐",
                "COUNTER_TREND": "⚠️",
                "5M_UNKNOWN": "➖"
            }.get(
                primary.get(
                    "context",
                    "5M_UNKNOWN"
                ),
                "➖"
            )

            msg = (
                f"🚨 **SIGZY 15M SIGNAL** {icon}\n\n"
                f"💱 คู่เงิน: "
                f"**{primary['symbol']}**\n"
                f"📌 ทิศทาง: "
                f"**{primary['decision']}**\n"
                f"🏆 15M Score: "
                f"**{primary['score']}/100**\n"
                f"💰 Entry: "
                f"`{primary['price']:.5f}`\n\n"
                f"{context_icon} 5M Context: "
                f"**{primary.get('context', 'UNKNOWN')}**\n"
                f"5M Score: "
                f"**{primary.get('five_score', 0)}/100**\n"
                f"📝 {primary.get('context_reason', '')}\n\n"
                f"🔎 15M: "
                f"{primary['reasons']}\n"
                f"📊 RSI: "
                f"{primary['rsi']:.1f}\n"
                f"📈 EMA20: "
                f"{primary['ema20']:.5f}\n"
                f"📉 EMA50: "
                f"{primary['ema50']:.5f}\n\n"
                f"🕯️ Closed Candle: "
                f"{primary['candle_time']}\n"
                f"🕐 Alert: "
                f"{now_text()}\n\n"
                f"ℹ️ 5M ไม่ได้ใช้บล็อก Signal "
                f"เพื่อไม่ปิดโอกาส"
            )

            send_discord(msg)

            log(
                f"🚨 SIGNAL "
                f"{symbol} "
                f"{primary['decision']} "
                f"{primary['score']}"
            )

        except Exception as e:

            log(
                f"[Scanner Error] "
                f"{symbol}: {e}"
            )


# ============================================================
# GEMINI
#
# AI เป็น Reporter เท่านั้น
# AI error ไม่กระทบ Signal
# ============================================================

def create_ai_client():

    if not GEMINI_API_KEY:
        log(
            "⚠️ GEMINI_API_KEY "
            "ยังไม่ได้ตั้งค่า"
        )
        return None

    if genai is None:

        log(
            "⚠️ ไม่พบ google-genai"
        )

        return None

    try:

        return genai.Client(
            api_key=GEMINI_API_KEY
        )

    except Exception as e:

        log(
            f"Gemini init error: {e}"
        )

        return None


AI_CLIENT = create_ai_client()


def ai_market_report(symbol):

    if AI_CLIENT is None:

        return (
            "AI ไม่พร้อมใช้งาน "
            "แต่ระบบ Signal "
            "ยังทำงานตาม Quant ได้"
        )

    try:

        candles = get_closed_candles(
            symbol,
            "15m",
            "2d"
        )

        if len(candles) < 30:

            return (
                "ข้อมูลแท่งไม่เพียงพอ"
            )

        last = candles[-1]

        ema20 = calculate_ema(
            candles,
            20
        )

        ema50 = calculate_ema(
            candles,
            50
        )

        rsi = calculate_rsi(
            candles,
            14
        )

        prompt = f"""
วิเคราะห์ข้อมูลตลาดที่ปิดแท่งแล้วของ {symbol}

Timeframe: 15M

Close: {last['close']}
EMA20: {ema20}
EMA50: {ema50}
RSI: {rsi}

Open: {last['open']}
High: {last['high']}
Low: {last['low']}
Close: {last['close']}

ให้ตอบสั้น ๆ:
1. แนวโน้ม
2. Momentum
3. สิ่งที่ต้องระวัง

ห้ามสร้างราคาใหม่
ห้ามอ้างข้อมูลที่ไม่ได้ให้
"""

        response = AI_CLIENT.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt
        )

        return response.text.strip()

    except Exception as e:

        return (
            f"AI error: {e}"
        )


def market_reporter_loop():

    while True:

        try:

            analysis = ai_market_report(
                "EUR/USD"
            )

            send_discord(
                "📊 **SIGZY AI MARKET REPORT**\n"
                "--------------------------------\n"
                f"{analysis}\n"
                "--------------------------------"
            )

        except Exception as e:

            log(
                f"AI Reporter Error: {e}"
            )

        # ทุกประมาณ 5 นาที
        time.sleep(300)


# ============================================================
# STATISTICS
# ============================================================

def calculate_stats():

    if not HISTORICAL_MEMORY:
        return {
            "win": 0,
            "loss": 0,
            "draw": 0,
            "total": 0
        }

    win = sum(
        1
        for x in HISTORICAL_MEMORY
        if x.get("status") == "WIN"
    )

    loss = sum(
        1
        for x in HISTORICAL_MEMORY
        if x.get("status")
        in ("LOSS", "FULL_LOSS")
    )

    draw = sum(
        1
        for x in HISTORICAL_MEMORY
        if x.get("status") == "DRAW"
    )

    total = win + loss + draw

    return {
        "win": win,
        "loss": loss,
        "draw": draw,
        "total": total
    }


# ============================================================
# HEALTH SERVER FOR RAILWAY
# ============================================================

class HealthHandler(
    BaseHTTPRequestHandler
):

    def do_GET(self):

        if self.path == "/health":

            body = (
                "SIGZY ONLINE\n"
                f"time={now_text()}\n"
                f"active_trackers="
                f"{len(ACTIVE_TRACKERS)}\n"
            ).encode(
                "utf-8"
            )

            self.send_response(200)

        else:

            body = (
                "SIGZY BOT IS RUNNING"
            ).encode(
                "utf-8"
            )

            self.send_response(200)

        self.send_header(
            "Content-Type",
            "text/plain; charset=utf-8"
        )

        self.send_header(
            "Content-Length",
            str(len(body))
        )

        self.end_headers()

        self.wfile.write(body)

    def log_message(
        self,
        format,
        *args
    ):
        return


def run_health_server():

    try:

        server = HTTPServer(
            ("0.0.0.0", PORT),
            HealthHandler
        )

        log(
            f"🌐 Health server "
            f"port {PORT}"
        )

        server.serve_forever()

    except Exception as e:

        log(
            f"Health server error: {e}"
        )


# ============================================================
# MAIN
# ============================================================

def main():

    log(
        "🚀 SIGZY STARTED"
    )

    log(
        "15M = PRIMARY | "
        "5M = CONTEXT | "
        "CLOSE = RESULT | "
        "OPP1-3 = TRACKER"
    )

    log(
        f"Memory: {MEMORY_FILE}"
    )

    load_memory()

    # AI reporter
    reporter_thread = Thread(
        target=market_reporter_loop,
        daemon=True
    )

    reporter_thread.start()

    while True:

        cycle_start = time.time()

        try:

            # 1. ประเมินผลของ Signal เก่า
            run_tracker()

            # 2. หา Signal ใหม่
            run_scanner()

            stats = calculate_stats()

            log(
                "📊 STATS "
                f"W={stats['win']} "
                f"L={stats['loss']} "
                f"D={stats['draw']} "
                f"TOTAL={stats['total']}"
            )

        except Exception as e:

            log(
                f"⚠️ MAIN ERROR: {e}"
            )

        elapsed = (
            time.time()
            - cycle_start
        )

        sleep_time = max(
            30,
            SCAN_SECONDS - elapsed
        )

        log(
            f"⏳ รอบถัดไปใน "
            f"{int(sleep_time)} วินาที"
        )

        time.sleep(
            sleep_time
        )


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    server_thread = Thread(
        target=run_health_server,
        daemon=True
    )

    server_thread.start()

    main()
