from pathlib import Path

code = r'''# -*- coding: utf-8 -*-
"""
============================================================
TRADEIFY V8 — SIGZY TRADEIFY A+ MTF SNIPER V2 MOBILE
============================================================

สูตรให้ตรงกับอินดิเคเตอร์:

15M = MASTER TREND
5M  = CONFIRMATION
CURRENT 5M = ENTRY / REJECTION

GREEN = CALL
RED   = PUT
YELLOW = PRE SIGNAL

IMPORTANT
- Python จะตรงกับอินดิเคเตอร์ได้เมื่อใช้ข้อมูลตลาดชุดเดียวกัน
- Yahoo Finance เป็น public FX feed ไม่ใช่ราคา OTC ภายใน Broker
- ถ้า Broker ใช้ OTC ราคาจะไม่จำเป็นต้องตรงกับ Yahoo
- ระบบไม่รับประกัน Win Rate หรือ 1-in-3
- ใช้ current 5M candle สำหรับ signal เพื่อเลียนแบบอินดิเคเตอร์
- เมื่อ signal เกิดกลางแท่ง: แจ้ง SIGNAL และกำหนด entry เป็นแท่ง 5M ถัดไป
- ผล WIN/LOSS เป็นการเทียบราคาจาก feed ที่เชื่อมต่ออยู่

RUN ALL DAY
STOP = NONE
STEP = 100 / 200 / 300
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
# APP
# ============================================================

app = Flask(__name__)

# ============================================================
# CONFIG
# ============================================================

DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()

SCAN_SECONDS = int(os.environ.get("SCAN_SECONDS", "5"))
EXPIRY_SECONDS = 300

# Run all day
DAILY_STOP = False

# Indicator settings — MUST MATCH V2 MOBILE
MIN_SCORE = 68
MIN_GAP = 8
STRICT_MODE = False

EMA_FAST = 9
EMA_SLOW = 21
EMA_TREND = 50

RSI_PERIOD = 14
RSI_MID = 50

SR_PERIOD = 80

# Entry is the NEXT 5M candle after a live signal.
NEXT_CANDLE_ENTRY = True

STAKE_BY_STEP = {
    1: 100,
    2: 200,
    3: 300,
}

MAX_STEP = 3

STATE_FILE = os.environ.get(
    "TRADEIFY_STATE_FILE",
    "tradeify_v8_state.json"
)

THAI_TZ = timezone(timedelta(hours=7))

SYMBOLS = [
    "EUR/USD",
    "GBP/USD",
    "USD/JPY",
    "EUR/JPY",
    "AUD/USD",
    "USD/CHF",
]

# ============================================================
# GLOBAL STATE
# ============================================================

LOCK = Lock()

CURRENT_DAY = None
CURRENT_STEP = 1
SET_ACTIVE = False
SET_NUMBER = 0

LAST_SIGNAL_KEY = {}
LAST_PRE_KEY = {}

PENDING_TRADES = {}

DAILY = {
    "signals": 0,
    "wins": 0,
    "losses": 0,
    "void": 0,
}

# ============================================================
# TIME
# ============================================================

def now_ts():
    return int(time.time())


def thai_now():
    return datetime.now(timezone.utc).astimezone(THAI_TZ)


# ============================================================
# STATE
# ============================================================

def save_state():
    try:
        with LOCK:
            state = {
                "current_day": CURRENT_DAY,
                "current_step": CURRENT_STEP,
                "set_active": SET_ACTIVE,
                "set_number": SET_NUMBER,
                "last_signal_key": LAST_SIGNAL_KEY,
                "last_pre_key": LAST_PRE_KEY,
                "pending_trades": PENDING_TRADES,
                "daily": DAILY,
            }

            tmp = STATE_FILE + ".tmp"

            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)

            os.replace(tmp, STATE_FILE)

    except Exception as e:
        print("[STATE SAVE ERROR]", repr(e))


def load_state():
    global CURRENT_DAY
    global CURRENT_STEP
    global SET_ACTIVE
    global SET_NUMBER
    global LAST_SIGNAL_KEY
    global LAST_PRE_KEY
    global PENDING_TRADES
    global DAILY

    try:
        if not os.path.exists(STATE_FILE):
            print("ℹ️ No previous state found")
            return

        with open(STATE_FILE, "r", encoding="utf-8") as f:
            state = json.load(f)

        CURRENT_DAY = state.get("current_day")
        CURRENT_STEP = int(state.get("current_step", 1))
        SET_ACTIVE = bool(state.get("set_active", False))
        SET_NUMBER = int(state.get("set_number", 0))

        LAST_SIGNAL_KEY = state.get("last_signal_key", {})
        LAST_PRE_KEY = state.get("last_pre_key", {})
        PENDING_TRADES = state.get("pending_trades", {})

        saved = state.get("daily", {})
        for k in DAILY:
            DAILY[k] = int(saved.get(k, 0))

        print("💾 Previous state loaded")

    except Exception as e:
        print("[STATE LOAD ERROR]", repr(e))


# ============================================================
# DISCORD
# ============================================================

def send_discord(message):
    if not DISCORD_WEBHOOK_URL:
        print("[DISCORD]", message)
        return False

    try:
        payload = json.dumps({
            "content": message
        }).encode("utf-8")

        req = urllib.request.Request(
            DISCORD_WEBHOOK_URL,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "TRADEIFY-V8"
            },
            method="POST"
        )

        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status in (200, 204)

    except Exception as e:
        print("[DISCORD ERROR]", repr(e))
        return False


# ============================================================
# MARKET DATA
# ============================================================

def fetch_market(symbol):
    try:
        yahoo = symbol.replace("/", "") + "=X"

        url = (
            "https://query1.finance.yahoo.com/"
            "v8/finance/chart/"
            f"{yahoo}"
            "?interval=1m&range=7d"
        )

        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0"}
        )

        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        result = data.get("chart", {}).get("result")
        if not result:
            return []

        result = result[0]

        timestamps = result.get("timestamp", [])
        quote = (
            result.get("indicators", {})
            .get("quote", [{}])[0]
        )

        candles = []

        for i, ts in enumerate(timestamps):
            try:
                op = quote["open"][i]
                hi = quote["high"][i]
                lo = quote["low"][i]
                cl = quote["close"][i]

                if None in (ts, op, hi, lo, cl):
                    continue

                candles.append({
                    "timestamp": int(ts),
                    "open": float(op),
                    "high": float(hi),
                    "low": float(lo),
                    "close": float(cl),
                })

            except Exception:
                continue

        candles.sort(key=lambda x: x["timestamp"])

        return candles

    except Exception as e:
        print(f"[MARKET ERROR] {symbol}: {e}")
        return []


# ============================================================
# RESAMPLE
# ============================================================

def resample(candles, minutes):
    """
    Build timeframe candles from 1M data.

    For historical closed candles, require a complete block.
    The CURRENT block is also returned so that V2 Mobile can
    behave like the indicator's current-TF calculations.
    """

    if not candles:
        return []

    size = minutes * 60
    buckets = {}

    for c in candles:
        bucket = c["timestamp"] // size
        buckets.setdefault(bucket, []).append(c)

    result = []

    current_bucket = now_ts() // size

    for bucket_id in sorted(buckets):
        group = sorted(
            buckets[bucket_id],
            key=lambda x: x["timestamp"]
        )

        # For closed historical candles, require full minutes.
        # Current candle can be partial, matching live indicator.
        if bucket_id != current_bucket and len(group) < minutes:
            continue

        if bucket_id != current_bucket:
            expected_ts = group[0]["timestamp"]

            valid = True
            for c in group[1:]:
                if c["timestamp"] - expected_ts != 60:
                    valid = False
                    break
                expected_ts = c["timestamp"]

            if not valid:
                continue

        result.append({
            "timestamp": bucket_id * size,
            "open": group[0]["open"],
            "high": max(x["high"] for x in group),
            "low": min(x["low"] for x in group),
            "close": group[-1]["close"],
            "complete": bucket_id != current_bucket,
            "count": len(group),
        })

    return result


# ============================================================
# EMA
# ============================================================

def ema(values, period):
    if len(values) < period:
        return None

    value = mean(values[:period])
    multiplier = 2.0 / (period + 1)

    for v in values[period:]:
        value = v * multiplier + value * (1 - multiplier)

    return value


# ============================================================
# RSI
# ============================================================

def rsi(candles, period=14):
    if len(candles) < period + 1:
        return None

    closes = [c["close"] for c in candles]

    gains = []
    losses = []

    for i in range(1, len(closes)):
        change = closes[i] - closes[i - 1]
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))

    if len(gains) < period:
        return None

    avg_gain = mean(gains[-period:])
    avg_loss = mean(losses[-period:])

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


# ============================================================
# INDICATOR FORMULA
# ============================================================

def indicator_analysis(symbol, candles_1m):
    """
    Replicates the supplied SIGZY TRADEIFY A+ MTF SNIPER V2 MOBILE.

    15M:
        uses [1] and [2] closed candles

    5M:
        uses [1] and [2] closed candles

    CURRENT 5M:
        local EMA / RSI / candle anatomy / flow / S/R / pullback

    This is the important change from V7:
    V7's Python scoring was NOT the same formula.
    V8 uses the supplied indicator's scoring.
    """

    if len(candles_1m) < 300:
        return None

    c5 = resample(candles_1m, 5)
    c15 = resample(candles_1m, 15)

    if len(c5) < 60 or len(c15) < 20:
        return None

    # Need current 5M candle plus two closed 5M candles.
    current5 = c5[-1]

    # If current 5M is unavailable/old, use latest.
    if not current5:
        return None

    # 15M closed [1], previous [2]
    if len(c15) < 3:
        return None

    m15 = c15[-2]
    m15_prev = c15[-3]

    # 5M closed [1], previous [2]
    if len(c5) < 3:
        return None

    m5 = c5[-2]
    m5_prev = c5[-3]

    # --------------------------------------------------------
    # 15M MASTER
    # --------------------------------------------------------

    m15_bull = m15["close"] > m15["open"]
    m15_bear = m15["close"] < m15["open"]

    m15_structure_up = (
        m15["high"] >= m15_prev["high"]
        or
        m15["low"] >= m15_prev["low"]
    )

    m15_structure_down = (
        m15["high"] <= m15_prev["high"]
        or
        m15["low"] <= m15_prev["low"]
    )

    trend15_call = (
        m15_bull
        and m15_structure_up
        and m15["close"] >= m15_prev["close"]
    )

    trend15_put = (
        m15_bear
        and m15_structure_down
        and m15["close"] <= m15_prev["close"]
    )

    # --------------------------------------------------------
    # 5M CONFIRMATION
    # --------------------------------------------------------

    m5_bull = m5["close"] > m5["open"]
    m5_bear = m5["close"] < m5["open"]

    m5_structure_up = (
        m5["high"] >= m5_prev["high"]
        or
        m5["low"] >= m5_prev["low"]
    )

    m5_structure_down = (
        m5["high"] <= m5_prev["high"]
        or
        m5["low"] <= m5_prev["low"]
    )

    trend5_call = (
        m5_bull
        and m5_structure_up
        and m5["close"] >= m5_prev["close"]
    )

    trend5_put = (
        m5_bear
        and m5_structure_down
        and m5["close"] <= m5_prev["close"]
    )

    # --------------------------------------------------------
    # CURRENT TF = CURRENT 5M
    # --------------------------------------------------------

    local = c5

    closes = [x["close"] for x in local]

    ema_fast = ema(closes, EMA_FAST)
    ema_slow = ema(closes, EMA_SLOW)
    ema_trend = ema(closes, EMA_TREND)

    rsi_value = rsi(local, RSI_PERIOD)

    if None in (
        ema_fast,
        ema_slow,
        ema_trend,
        rsi_value
    ):
        return None

    ema_call = (
        ema_fast > ema_slow
        and ema_slow > ema_trend
    )

    ema_put = (
        ema_fast < ema_slow
        and ema_slow < ema_trend
    )

    # --------------------------------------------------------
    # CURRENT CANDLE ANATOMY
    # --------------------------------------------------------

    body = abs(
        current5["close"] - current5["open"]
    )

    bar_range = max(
        current5["high"] - current5["low"],
        1e-12
    )

    upper_wick = (
        current5["high"]
        -
        max(
            current5["open"],
            current5["close"]
        )
    )

    lower_wick = (
        min(
            current5["open"],
            current5["close"]
        )
        -
        current5["low"]
    )

    body_ratio = body / bar_range
    upper_ratio = upper_wick / bar_range
    lower_ratio = lower_wick / bar_range

    bull = current5["close"] > current5["open"]
    bear = current5["close"] < current5["open"]

    # --------------------------------------------------------
    # REJECTION
    # --------------------------------------------------------

    bull_rejection = (
        bull
        and lower_ratio >= 0.18
    )

    bear_rejection = (
        bear
        and upper_ratio >= 0.18
    )

    # --------------------------------------------------------
    # FLOW
    # --------------------------------------------------------

    if len(local) < 3:
        return None

    flow_up = (
        local[-1]["close"] >= local[-2]["close"]
        and
        local[-2]["close"] >= local[-3]["close"]
    )

    flow_down = (
        local[-1]["close"] <= local[-2]["close"]
        and
        local[-2]["close"] <= local[-3]["close"]
    )

    # --------------------------------------------------------
    # S/R
    # --------------------------------------------------------

    sr_data = local[-SR_PERIOD:]

    support = min(x["low"] for x in sr_data)
    resistance = max(x["high"] for x in sr_data)

    range_sr = max(
        resistance - support,
        1e-12
    )

    near_support = (
        current5["close"]
        <=
        support + range_sr * 0.22
    )

    near_resistance = (
        current5["close"]
        >=
        resistance - range_sr * 0.22
    )

    room_call = (
        resistance - current5["close"]
    ) / range_sr

    room_put = (
        current5["close"] - support
    ) / range_sr

    enough_room_call = room_call >= 0.15
    enough_room_put = room_put >= 0.15

    # --------------------------------------------------------
    # PULLBACK
    # --------------------------------------------------------

    pullback_call = (
        (
            current5["low"] <= ema_fast
            or
            current5["low"] <= ema_slow
            or
            near_support
        )
        and
        current5["close"] >= ema_fast
    )

    pullback_put = (
        (
            current5["high"] >= ema_fast
            or
            current5["high"] >= ema_slow
            or
            near_resistance
        )
        and
        current5["close"] <= ema_fast
    )

    # --------------------------------------------------------
    # SCORE
    # --------------------------------------------------------

    call_score = 0
    put_score = 0

    reasons_call = []
    reasons_put = []

    def add_call(points, reason):
        nonlocal call_score
        call_score += points
        reasons_call.append(reason)

    def add_put(points, reason):
        nonlocal put_score
        put_score += points
        reasons_put.append(reason)

    # 15M MASTER = 30
    if trend15_call:
        add_call(30, "15M MASTER UP")

    if trend15_put:
        add_put(30, "15M MASTER DOWN")

    # 5M CONFIRM = 25
    if trend5_call:
        add_call(25, "5M CONFIRM UP")

    if trend5_put:
        add_put(25, "5M CONFIRM DOWN")

    # EMA = 12
    if ema_call:
        add_call(12, "EMA 9>21>50")

    if ema_put:
        add_put(12, "EMA 9<21<50")

    # FLOW = 8
    if flow_up:
        add_call(8, "FLOW UP")

    if flow_down:
        add_put(8, "FLOW DOWN")

    # REJECTION = 12
    if bull_rejection:
        add_call(12, "BULL REJECTION")

    if bear_rejection:
        add_put(12, "BEAR REJECTION")

    # RSI = 5
    if rsi_value > RSI_MID:
        add_call(5, "RSI > 50")

    if rsi_value < RSI_MID:
        add_put(5, "RSI < 50")

    # PULLBACK = 8
    if pullback_call:
        add_call(8, "PULLBACK CALL")

    if pullback_put:
        add_put(8, "PULLBACK PUT")

    # ROOM = 5
    if enough_room_call:
        add_call(5, "ROOM CALL")

    if enough_room_put:
        add_put(5, "ROOM PUT")

    # SUPPORT = 5
    if near_support:
        add_call(5, "NEAR SUPPORT")

    # RESISTANCE = 5
    if near_resistance:
        add_put(5, "NEAR RESISTANCE")

    # --------------------------------------------------------
    # PENALTY
    # --------------------------------------------------------

    if near_resistance:
        call_score -= 8

    if near_support:
        put_score -= 8

    call_score = max(0, min(int(call_score), 100))
    put_score = max(0, min(int(put_score), 100))

    gap_call = call_score - put_score
    gap_put = put_score - call_score

    # --------------------------------------------------------
    # MASTER DIRECTION
    # --------------------------------------------------------

    master_call = (
        trend15_call
        and trend5_call
    )

    master_put = (
        trend15_put
        and trend5_put
    )

    # --------------------------------------------------------
    # PRE
    # --------------------------------------------------------

    pre_call = (
        master_call
        and call_score >= MIN_SCORE - 10
        and gap_call >= MIN_GAP
    )

    pre_put = (
        master_put
        and put_score >= MIN_SCORE - 10
        and gap_put >= MIN_GAP
    )

    # --------------------------------------------------------
    # FINAL SIGNAL
    # --------------------------------------------------------

    call_signal = (
        master_call
        and call_score >= MIN_SCORE
        and gap_call >= MIN_GAP
        and ema_call
        and bull_rejection
        and pullback_call
    )

    put_signal = (
        master_put
        and put_score >= MIN_SCORE
        and gap_put >= MIN_GAP
        and ema_put
        and bear_rejection
        and pullback_put
    )

    # --------------------------------------------------------
    # STRICT
    # --------------------------------------------------------

    if STRICT_MODE:
        call_signal = (
            call_signal
            and flow_up
        )

        put_signal = (
            put_signal
            and flow_down
        )

    # --------------------------------------------------------
    # CONFLICT
    # --------------------------------------------------------

    if master_call and master_put:
        call_signal = False
        put_signal = False

    if pre_call and pre_put:
        pre_call = False
        pre_put = False

    # --------------------------------------------------------
    # DIRECTION
    # --------------------------------------------------------

    if call_score > put_score:
        direction = "CALL"
        score = call_score
        gap = gap_call

    elif put_score > call_score:
        direction = "PUT"
        score = put_score
        gap = gap_put

    else:
        direction = None
        score = 0
        gap = 0

    return {
        "symbol": symbol,
        "direction": direction,
        "score": score,
        "call_score": call_score,
        "put_score": put_score,
        "gap": gap,
        "pre_call": bool(pre_call),
        "pre_put": bool(pre_put),
        "call_signal": bool(call_signal),
        "put_signal": bool(put_signal),
        "confirmed": bool(call_signal or put_signal),
        "rsi": float(rsi_value),
        "support": support,
        "resistance": resistance,
        "entry_price": current5["close"],
        "current_5m_open": current5["timestamp"],
        "current_5m_complete": current5["complete"],
        "current_5m_count": current5["count"],
        "reasons": (
            reasons_call
            if direction == "CALL"
            else reasons_put
        ),
    }


# ============================================================
# ENTRY NEXT 5M CANDLE
# ============================================================

def next_5m_open(timestamp):
    return ((int(timestamp) // 300) + 1) * 300


# ============================================================
# CREATE TRADE
# ============================================================

def create_trade(a):
    global CURRENT_STEP
    global SET_ACTIVE
    global SET_NUMBER

    symbol = a["symbol"]
    direction = a["direction"]

    if DAILY_STOP:
        return False

    if not a["confirmed"]:
        return False

    signal_bucket = a["current_5m_open"]

    signal_key = (
        f"{symbol}|{signal_bucket}|{direction}"
    )

    if LAST_SIGNAL_KEY.get(symbol) == signal_key:
        return False

    # Only one pending trade at a time.
    if PENDING_TRADES:
        return False

    if not SET_ACTIVE:
        SET_ACTIVE = True
        SET_NUMBER += 1
        CURRENT_STEP = 1

    step = CURRENT_STEP
    stake = STAKE_BY_STEP[step]

    if NEXT_CANDLE_ENTRY:
        entry_time = next_5m_open(signal_bucket)
    else:
        entry_time = now_ts()

    expiry = entry_time + EXPIRY_SECONDS

    trade_key = (
        f"{symbol}|{entry_time}|"
        f"{direction}|STEP{step}"
    )

    PENDING_TRADES[trade_key] = {
        "symbol": symbol,
        "direction": direction,
        "signal_time": signal_bucket,
        "entry_time": entry_time,
        "expiry": expiry,
        "entry": float(a["entry_price"]),
        "step": step,
        "stake": stake,
        "set_number": SET_NUMBER,
        "score": a["score"],
        "gap": a["gap"],
        "created_at": now_ts(),
        "entry_captured": False,
    }

    LAST_SIGNAL_KEY[symbol] = signal_key
    DAILY["signals"] += 1

    icon = "🟢" if direction == "CALL" else "🔴"

    send_discord(
        "🎯 **TRADEIFY V8 CONFIRMED**\n\n"
        f"{icon} `{symbol}` → **{direction}**\n"
        f"📊 Score: `{a['score']}/100`\n"
        f"⚡ Gap: `+{a['gap']}`\n"
        f"📈 RSI: `{a['rsi']:.1f}`\n"
        f"📍 Entry reference: `{a['entry_price']}`\n"
        f"⏭️ **เข้าแท่ง 5M ถัดไป**\n"
        f"⏱️ Expiry: `5 นาที`\n"
        f"🎯 SET #{SET_NUMBER} / STEP {step}/3\n"
        f"💵 Stake: `{stake}` บาท\n"
        f"🧩 15M MASTER + 5M CONFIRM + 5M REJECTION\n"
        f"🟢/🔴 ให้ตรงกับอินดิเคเตอร์ V2"
    )

    save_state()
    return True


# ============================================================
# CAPTURE REAL ENTRY PRICE
# ============================================================

def capture_entries():
    """
    When the next 5M candle opens, replace the reference price
    with the first available 1M open from the target 5M block.
    """

    for key, trade in list(PENDING_TRADES.items()):
        if trade.get("entry_captured"):
            continue

        if now_ts() < trade["entry_time"]:
            continue

        candles = fetch_market(trade["symbol"])

        if not candles:
            continue

        target = trade["entry_time"]

        first = None

        for c in candles:
            if c["timestamp"] >= target:
                first = c
                break

        if first is None:
            continue

        trade["entry"] = float(first["open"])
        trade["entry_captured"] = True

        save_state()

        send_discord(
            "▶️ **ENTRY OPENED**\n"
            f"📌 `{trade['symbol']}`\n"
            f"➡️ **{trade['direction']}**\n"
            f"💰 Entry: `{trade['entry']}`\n"
            f"⏱️ Expiry: `5 นาที`\n"
            f"🎯 STEP {trade['step']}/3"
        )


# ============================================================
# RESULT
# ============================================================

def check_results():
    global CURRENT_STEP
    global SET_ACTIVE

    if not PENDING_TRADES:
        return

    now = now_ts()

    for key, trade in list(PENDING_TRADES.items()):

        if not trade.get("entry_captured"):
            continue

        if now < trade["expiry"]:
            continue

        candles = fetch_market(trade["symbol"])

        if not candles:
            continue

        result_candle = None

        for c in candles:
            if c["timestamp"] >= trade["expiry"]:
                result_candle = c
                break

        if result_candle is None:
            continue

        exit_price = float(result_candle["close"])
        entry = float(trade["entry"])
        direction = trade["direction"]

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

        step = int(trade["step"])

        if result == "WIN":
            DAILY["wins"] += 1
            SET_ACTIVE = False
            CURRENT_STEP = 1

        elif result == "LOSS":
            DAILY["losses"] += 1

            if step < MAX_STEP:
                CURRENT_STEP = step + 1
                SET_ACTIVE = True
            else:
                CURRENT_STEP = 1
                SET_ACTIVE = False

        else:
            DAILY["void"] += 1
            CURRENT_STEP = 1
            SET_ACTIVE = False

        emoji = {
            "WIN": "✅",
            "LOSS": "❌",
            "VOID": "⚪"
        }[result]

        next_step = (
            f"STEP {CURRENT_STEP}/3"
            if result == "LOSS" and step < 3
            else "RESET STEP 1"
        )

        send_discord(
            f"{emoji} **TRADE RESULT: {result}**\n"
            f"📌 `{trade['symbol']}`\n"
            f"➡️ **{direction}**\n"
            f"💰 Entry: `{entry}`\n"
            f"🏁 Exit: `{exit_price}`\n"
            f"🎯 Step: `{step}/3`\n"
            f"📊 Score: `{trade['score']}`\n"
            f"⚡ Gap: `+{trade['gap']}`\n"
            f"🔁 {next_step}\n"
            f"🌐 MODE: **RUN ALL DAY**"
        )

        PENDING_TRADES.pop(key, None)
        save_state()


# ============================================================
# DAILY RESET — DOES NOT STOP BOT
# ============================================================

def daily_reset():
    global CURRENT_DAY
    global CURRENT_STEP
    global SET_ACTIVE
    global SET_NUMBER

    day = thai_now().strftime("%Y-%m-%d")

    if CURRENT_DAY == day:
        return

    CURRENT_DAY = day
    CURRENT_STEP = 1
    SET_ACTIVE = False
    SET_NUMBER = 0

    PENDING_TRADES.clear()
    LAST_SIGNAL_KEY.clear()
    LAST_PRE_KEY.clear()

    for k in DAILY:
        DAILY[k] = 0

    save_state()

    send_discord(
        "🌅 **TRADEIFY V8 NEW DAY**\n"
        f"📅 `{day}`\n"
        "🎯 MODE: RUN ALL DAY\n"
        "🛑 STOP: NONE\n"
        "💰 STEP: 100 / 200 / 300"
    )


# ============================================================
# SCANNER
# ============================================================

def scanner():
    print("==========================================")
    print("🚀 TRADEIFY V8 A+ MTF SNIPER")
    print("==========================================")
    print("15M = MASTER")
    print("5M  = CONFIRM")
    print("5M  = ENTRY / REJECTION")
    print(f"MIN SCORE = {MIN_SCORE}")
    print(f"MIN GAP   = {MIN_GAP}")
    print("EXPIRY    = 5 MIN")
    print("STEP      = 100 / 200 / 300")
    print("MODE      = RUN ALL DAY")
    print("STOP      = NONE")
    print("==========================================")

    load_state()

    print("==========================================")
    print("✅ Bot worker started")
    print("==========================================")

    while True:
        try:
            daily_reset()

            capture_entries()
            check_results()

            print(
                f"🔎 SCAN "
                f"{thai_now().strftime('%H:%M:%S')} | "
                f"pending={len(PENDING_TRADES)} | "
                f"step={CURRENT_STEP}"
            )

            if not PENDING_TRADES:
                for symbol in SYMBOLS:

                    candles = fetch_market(symbol)

                    if not candles:
                        print(
                            f"[CHECK] {symbol} NO DATA"
                        )
                        continue

                    a = indicator_analysis(
                        symbol,
                        candles
                    )

                    if not a:
                        print(
                            f"[CHECK] {symbol} "
                            f"INSUFFICIENT DATA"
                        )
                        continue

                    print(
                        f"[CHECK] {symbol} "
                        f"CALL={a['call_score']} "
                        f"PUT={a['put_score']} "
                        f"DIR={a['direction']} "
                        f"GAP={a['gap']} "
                        f"SCORE={a['score']} "
                        f"PRE={a['pre_call'] or a['pre_put']} "
                        f"CONFIRMED={a['confirmed']}"
                    )

                    # PRE signal
                    pre_direction = None

                    if a["pre_call"]:
                        pre_direction = "CALL"

                    elif a["pre_put"]:
                        pre_direction = "PUT"

                    if pre_direction:
                        pre_key = (
                            f"{symbol}|"
                            f"{a['current_5m_open']}|"
                            f"{pre_direction}"
                        )

                        if LAST_PRE_KEY.get(symbol) != pre_key:
                            LAST_PRE_KEY[symbol] = pre_key

                            icon = (
                                "🟡"
                                if pre_direction == "CALL"
                                else "🟠"
                            )

                            send_discord(
                                f"{icon} **A+ PRE SIGNAL**\n"
                                f"`{symbol}` → "
                                f"**{pre_direction}**\n"
                                f"Score: `{a['score']}`\n"
                                f"Gap: `+{a['gap']}`\n"
                                f"⚠️ ยังไม่ใช่ออเดอร์"
                            )

                    # CONFIRMED
                    if a["confirmed"]:
                        if create_trade(a):
                            break

            save_state()
            time.sleep(SCAN_SECONDS)

        except Exception as e:
            print("[SCANNER ERROR]", repr(e))
            time.sleep(5)


# ============================================================
# FLASK
# ============================================================

@app.route("/")
def home():
    return (
        "TRADEIFY V8 A+ MTF SNIPER RUNNING | "
        "15M MASTER | 5M CONFIRM | 5M ENTRY | "
        "RUN ALL DAY"
    )


@app.route("/health")
def health():
    return jsonify({
        "status": "running",
        "version": "TRADEIFY V8",
        "mode": "RUN ALL DAY",
        "stop": "NONE",
        "current_day": CURRENT_DAY,
        "step": CURRENT_STEP,
        "set_number": SET_NUMBER,
        "pending": len(PENDING_TRADES),
        "daily": DAILY,
        "min_score": MIN_SCORE,
        "min_gap": MIN_GAP,
    })


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    load_state()

    worker = Thread(
        target=scanner,
        daemon=True,
        name="tradeify-v8-worker"
    )

    worker.start()

    port = int(
        os.environ.get(
            "PORT",
            "8080"
        )
    )

    print("🌐 Flask listening on 0.0.0.0:" + str(port))

    # IMPORTANT:
    # use_reloader=False prevents Flask from starting
    # a second Python worker in development mode.
    app.run(
        host="0.0.0.0",
        port=port,
        debug=False,
        use_reloader=False,
    )
'''

path = Path("/mnt/data/tradeify_v8.py")
path.write_text(code, encoding="utf-8")
print(f"สร้างไฟล์เรียบร้อย: {path}")
