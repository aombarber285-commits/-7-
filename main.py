# -*- coding: utf-8 -*-
"""
============================================================
TRADEIFY V8
RAILWAY-READY / 5M MTF SNIPER
============================================================

เพิ่มระบบเวลาแบบเต็ม:
- SIGNAL TIME  = เวลาแท่ง 5M ที่ใช้เป็นสัญญาณ
- CANDLE TIME  = เวลาเปิด-ปิดของแท่ง 5M
- ENTRY TIME   = เวลาที่ระบบสร้างออเดอร์
- EXPIRY TIME  = เวลาหมดอายุ
- RESULT TIME  = เวลาที่ตรวจผล
- RESULT CANDLE= เวลาแท่งที่ใช้ตัดสินผล

Timezone: Asia/Bangkok / UTC+7

หมายเหตุ:
Yahoo Finance เป็น public FX proxy ไม่ใช่ราคา OTC ของโบรกเกอร์
หากต้องการผล OTC จริง ให้เปลี่ยน fetch_market() เป็น Broker/OTC API
"""

import os
import json
import time
import urllib.request
import urllib.parse
from pathlib import Path
from datetime import datetime, timezone, timedelta
from statistics import mean
from threading import Thread, Lock

from flask import Flask, jsonify

# ============================================================
# APP / CONFIG
# ============================================================

app = Flask(__name__)

BOT_NAME = "TRADEIFY V8"

DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()

PORT = int(os.environ.get("PORT", "8080"))
SCAN_SECONDS = int(os.environ.get("SCAN_SECONDS", "10"))

MIN_SCORE = int(os.environ.get("MIN_SCORE", "68"))
MIN_GAP = int(os.environ.get("MIN_GAP", "8"))

STRICT_MODE = os.environ.get("STRICT_MODE", "false").lower() == "true"

EXPIRY_SECONDS = 300

STAKE_BY_STEP = {1: 100, 2: 200, 3: 300}
MAX_STEP = 3

MIN_1M_CANDLES = 120
SR_PERIOD = 80

SYMBOLS = [
    "EUR/USD",
    "GBP/USD",
    "USD/JPY",
    "EUR/JPY",
    "AUD/USD",
    "USD/CHF"
]

THAI_TZ = timezone(timedelta(hours=7))

BASE_DIR = Path(__file__).resolve().parent
STATE_FILE = Path(
    os.environ.get(
        "TRADEIFY_STATE_FILE",
        str(BASE_DIR / "tradeify_v8_state.json")
    )
)

STATE_LOCK = Lock()

CURRENT_DAY = None
CURRENT_STEP = 1
SET_ACTIVE = False
SET_NUMBER = 0

LAST_CANDLE_5M = {}
LAST_PRE = {}
LAST_SIGNAL = {}
PENDING_TRADE = None

DAILY = {
    "signals": 0,
    "wins": 0,
    "losses": 0,
    "void": 0,
    "set_wins": 0,
    "set_losses": 0
}

# ============================================================
# TIME HELPERS
# ============================================================

def now_ts():
    return int(time.time())


def thai_now():
    return datetime.now(timezone.utc).astimezone(THAI_TZ)


def time_string():
    return thai_now().strftime("%Y-%m-%d %H:%M:%S")


def fmt_ts(ts):
    """Unix timestamp -> Thai time, used everywhere in logs/Discord."""
    try:
        return datetime.fromtimestamp(
            int(ts), tz=timezone.utc
        ).astimezone(THAI_TZ).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return "-"


def fmt_time_only(ts):
    try:
        return datetime.fromtimestamp(
            int(ts), tz=timezone.utc
        ).astimezone(THAI_TZ).strftime("%H:%M:%S")
    except Exception:
        return "-"


def candle_window_5m(close_ts):
    """For this script, candle timestamp = last 1M candle inside the 5M bucket."""
    try:
        close_dt = datetime.fromtimestamp(
            int(close_ts), tz=timezone.utc
        ).astimezone(THAI_TZ)
        open_ts = int(close_ts) - 4 * 60
        open_dt = datetime.fromtimestamp(
            open_ts, tz=timezone.utc
        ).astimezone(THAI_TZ)
        return (
            open_dt.strftime("%Y-%m-%d %H:%M:%S"),
            close_dt.strftime("%Y-%m-%d %H:%M:%S")
        )
    except Exception:
        return "-", "-"

# ============================================================
# FLASK
# ============================================================

@app.route("/")
def home():
    return (
        "TRADEIFY V8 RUNNING | "
        "15M MASTER | 5M CONFIRM | 5M ENTRY | "
        "TIME-TRACKING ENABLED"
    )


@app.route("/health")
def health():
    pending = None

    if PENDING_TRADE:
        pending = {
            "symbol": PENDING_TRADE["symbol"],
            "direction": PENDING_TRADE["direction"],
            "step": PENDING_TRADE["step"],
            "entry": PENDING_TRADE["entry"],
            "signal_time": fmt_ts(PENDING_TRADE["timestamp"]),
            "entry_time": fmt_ts(PENDING_TRADE["entry_time"]),
            "expiry_time": fmt_ts(PENDING_TRADE["expiry"]),
            "candle_open": fmt_ts(PENDING_TRADE["candle_open"]),
            "candle_close": fmt_ts(PENDING_TRADE["candle_close"]),
        }

    return jsonify({
        "status": "running",
        "bot": BOT_NAME,
        "mode": "RUN ALL DAY",
        "stop": "NONE",
        "timezone": "Asia/Bangkok UTC+7",
        "master": "15M",
        "confirm": "5M",
        "entry": "5M",
        "min_score": MIN_SCORE,
        "min_gap": MIN_GAP,
        "step": CURRENT_STEP,
        "set_number": SET_NUMBER,
        "pending": pending,
        "server_time": time_string(),
        "daily": DAILY
    })

# ============================================================
# STATE
# ============================================================

def save_state():
    try:
        state = {
            "current_day": CURRENT_DAY,
            "current_step": CURRENT_STEP,
            "set_active": SET_ACTIVE,
            "set_number": SET_NUMBER,
            "last_candle_5m": LAST_CANDLE_5M,
            "last_pre": LAST_PRE,
            "last_signal": LAST_SIGNAL,
            "pending_trade": PENDING_TRADE,
            "daily": DAILY
        }

        temp = Path(str(STATE_FILE) + ".tmp")

        with STATE_LOCK:
            with open(temp, "w", encoding="utf-8") as f:
                json.dump(
                    state,
                    f,
                    ensure_ascii=False,
                    indent=2
                )

            os.replace(temp, STATE_FILE)

    except Exception as e:
        print("[STATE SAVE ERROR]", repr(e))


def load_state():
    global CURRENT_DAY
    global CURRENT_STEP
    global SET_ACTIVE
    global SET_NUMBER
    global LAST_CANDLE_5M
    global LAST_PRE
    global LAST_SIGNAL
    global PENDING_TRADE
    global DAILY

    try:
        if not STATE_FILE.exists():
            print("ℹ️ No previous state found")
            return

        with open(STATE_FILE, "r", encoding="utf-8") as f:
            state = json.load(f)

        CURRENT_DAY = state.get("current_day")
        CURRENT_STEP = int(state.get("current_step", 1))
        SET_ACTIVE = bool(state.get("set_active", False))
        SET_NUMBER = int(state.get("set_number", 0))

        LAST_CANDLE_5M = state.get("last_candle_5m", {})
        LAST_PRE = state.get("last_pre", {})
        LAST_SIGNAL = state.get("last_signal", {})
        PENDING_TRADE = state.get("pending_trade")

        saved_daily = state.get("daily", {})
        for key in DAILY:
            DAILY[key] = int(saved_daily.get(key, 0))

        print("💾 Previous state loaded")

        if PENDING_TRADE:
            print(
                "⏳ RESTORED PENDING:",
                PENDING_TRADE.get("symbol"),
                PENDING_TRADE.get("direction"),
                "| SIGNAL TIME:",
                fmt_ts(PENDING_TRADE.get("timestamp", 0)),
                "| EXPIRY:",
                fmt_ts(PENDING_TRADE.get("expiry", 0))
            )

    except Exception as e:
        print("[STATE LOAD ERROR]", repr(e))


def reset_daily_if_needed():
    global CURRENT_DAY
    global CURRENT_STEP
    global SET_ACTIVE
    global SET_NUMBER
    global PENDING_TRADE
    global LAST_CANDLE_5M
    global LAST_PRE
    global LAST_SIGNAL

    today = thai_now().strftime("%Y-%m-%d")

    if CURRENT_DAY == today:
        return

    CURRENT_DAY = today
    CURRENT_STEP = 1
    SET_ACTIVE = False
    SET_NUMBER = 0
    PENDING_TRADE = None

    LAST_CANDLE_5M = {}
    LAST_PRE = {}
    LAST_SIGNAL = {}

    for key in DAILY:
        DAILY[key] = 0

    save_state()

    send_discord(
        "🌅 **TRADEIFY V8 NEW DAY**\n"
        f"📅 `{today}`\n"
        f"🕒 Reset Time: `{time_string()}`\n"
        "15M = MASTER\n"
        "5M = CONFIRM / ENTRY\n"
        "🎯 RUN ALL DAY\n"
        "🛑 STOP = NONE\n"
        "💰 STEP = 100 / 200 / 300"
    )

# ============================================================
# DISCORD
# ============================================================

def send_discord(message):
    if not DISCORD_WEBHOOK_URL:
        print("[DISCORD DISABLED]\n" + message)
        return False

    try:
        payload = json.dumps(
            {"content": message}
        ).encode("utf-8")

        req = urllib.request.Request(
            DISCORD_WEBHOOK_URL,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "TRADEIFY-V8"
            },
            method="POST"
        )

        with urllib.request.urlopen(req, timeout=10) as response:
            return response.status in (200, 204)

    except Exception as e:
        print("[DISCORD ERROR]", repr(e))
        return False

# ============================================================
# MARKET DATA
# ============================================================

def yahoo_symbol(symbol):
    return symbol.replace("/", "") + "=X"


def fetch_market(symbol):
    try:
        encoded = urllib.parse.quote(yahoo_symbol(symbol))

        url = (
            "https://query1.finance.yahoo.com/"
            "v8/finance/chart/"
            f"{encoded}"
            "?interval=1m&range=5d"
        )

        request = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 TRADEIFY-V8"}
        )

        with urllib.request.urlopen(request, timeout=15) as response:
            raw = response.read()

        data = json.loads(raw.decode("utf-8"))

        result = (
            data.get("chart", {})
            .get("result")
        )

        if not result:
            return []

        result = result[0]

        timestamps = result.get("timestamp", [])

        quote = (
            result
            .get("indicators", {})
            .get("quote", [{}])[0]
        )

        opens = quote.get("open", [])
        highs = quote.get("high", [])
        lows = quote.get("low", [])
        closes = quote.get("close", [])

        candles = []

        for i, ts in enumerate(timestamps):
            try:
                if ts is None:
                    continue

                op = opens[i]
                hi = highs[i]
                lo = lows[i]
                cl = closes[i]

                if any(x is None for x in (op, hi, lo, cl)):
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

        candles.sort(key=lambda x: x["timestamp"])

        # Remove current open 1M candle.
        current_minute = (now_ts() // 60) * 60

        candles = [
            c for c in candles
            if c["timestamp"] < current_minute
        ]

        return candles

    except Exception as e:
        print(f"[MARKET ERROR] {symbol}: {e}")
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
        bucket_id = candle["timestamp"] // size
        buckets.setdefault(bucket_id, []).append(candle)

    result = []

    for bucket_id in sorted(buckets):
        group = buckets[bucket_id]
        group.sort(key=lambda x: x["timestamp"])

        if len(group) < minutes:
            continue

        valid = True

        for i in range(1, len(group)):
            if (
                group[i]["timestamp"]
                - group[i - 1]["timestamp"]
                != 60
            ):
                valid = False
                break

        if not valid:
            continue

        group = group[-minutes:]

        result.append({
            "timestamp": group[-1]["timestamp"],
            "open": group[0]["open"],
            "high": max(x["high"] for x in group),
            "low": min(x["low"] for x in group),
            "close": group[-1]["close"]
        })

    return result

# ============================================================
# INDICATORS
# ============================================================

def ema(values, period):
    if len(values) < period:
        return None

    result = mean(values[:period])

    multiplier = 2 / (period + 1)

    for value in values[period:]:
        result = (
            value * multiplier
            + result * (1 - multiplier)
        )

    return result


def rsi(candles, period=14):
    if len(candles) < period + 1:
        return None

    closes = [x["close"] for x in candles]

    gains = []
    losses = []

    for i in range(1, len(closes)):
        change = closes[i] - closes[i - 1]

        if change > 0:
            gains.append(change)
            losses.append(0.0)
        else:
            gains.append(0.0)
            losses.append(abs(change))

    avg_gain = mean(gains[-period:])
    avg_loss = mean(losses[-period:])

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss

    return 100 - 100 / (1 + rs)


def clamp(value, low=0, high=100):
    return max(low, min(high, value))

# ============================================================
# CANDLE / STRUCTURE
# ============================================================

def candle_info(candle):
    op = candle["open"]
    hi = candle["high"]
    lo = candle["low"]
    cl = candle["close"]

    body = abs(cl - op)
    candle_range = max(hi - lo, 1e-12)

    upper_wick = hi - max(op, cl)
    lower_wick = min(op, cl) - lo

    body_ratio = body / candle_range
    upper_ratio = upper_wick / candle_range
    lower_ratio = lower_wick / candle_range

    bull = cl > op
    bear = cl < op

    bull_rejection = bull and lower_ratio >= 0.18
    bear_rejection = bear and upper_ratio >= 0.18

    return {
        "bull": bull,
        "bear": bear,
        "body_ratio": body_ratio,
        "upper_ratio": upper_ratio,
        "lower_ratio": lower_ratio,
        "bull_rejection": bull_rejection,
        "bear_rejection": bear_rejection
    }


def structure(candles):
    if len(candles) < 2:
        return False, False

    current = candles[-1]
    previous = candles[-2]

    bull = current["close"] > current["open"]
    bear = current["close"] < current["open"]

    structure_up = (
        current["high"] >= previous["high"]
        or current["low"] >= previous["low"]
    )

    structure_down = (
        current["high"] <= previous["high"]
        or current["low"] <= previous["low"]
    )

    trend_call = (
        bull
        and structure_up
        and current["close"] >= previous["close"]
    )

    trend_put = (
        bear
        and structure_down
        and current["close"] <= previous["close"]
    )

    return trend_call, trend_put

# ============================================================
# ANALYSIS
# ============================================================

def analyze(symbol, candles_1m):
    if len(candles_1m) < MIN_1M_CANDLES:
        return None

    candles_15m = resample(candles_1m, 15)
    candles_5m = resample(candles_1m, 5)

    if len(candles_15m) < 60:
        return None

    if len(candles_5m) < 80:
        return None

    current5 = candles_5m[-1]

    trend15_call, trend15_put = structure(candles_15m)
    trend5_call, trend5_put = structure(candles_5m)

    closes5 = [x["close"] for x in candles_5m]

    ema_fast = ema(closes5, 9)
    ema_slow = ema(closes5, 21)
    ema_trend = ema(closes5, 50)

    if any(x is None for x in (ema_fast, ema_slow, ema_trend)):
        return None

    rsi_value = rsi(candles_5m, 14)

    if rsi_value is None:
        return None

    candle = candle_info(current5)

    ema_call = (
        ema_fast > ema_slow
        and ema_slow > ema_trend
    )

    ema_put = (
        ema_fast < ema_slow
        and ema_slow < ema_trend
    )

    if len(candles_5m) < 3:
        return None

    flow_up = (
        candles_5m[-1]["close"] >= candles_5m[-2]["close"]
        and candles_5m[-2]["close"] >= candles_5m[-3]["close"]
    )

    flow_down = (
        candles_5m[-1]["close"] <= candles_5m[-2]["close"]
        and candles_5m[-2]["close"] <= candles_5m[-3]["close"]
    )

    sr_data = candles_5m[-SR_PERIOD:]

    support = min(x["low"] for x in sr_data)
    resistance = max(x["high"] for x in sr_data)

    sr_range = max(resistance - support, 1e-12)

    near_support = (
        current5["close"]
        <= support + sr_range * 0.22
    )

    near_resistance = (
        current5["close"]
        >= resistance - sr_range * 0.22
    )

    room_call = (
        resistance - current5["close"]
    ) / sr_range

    room_put = (
        current5["close"] - support
    ) / sr_range

    enough_room_call = room_call >= 0.15
    enough_room_put = room_put >= 0.15

    pullback_call = (
        (
            current5["low"] <= ema_fast
            or current5["low"] <= ema_slow
            or near_support
        )
        and current5["close"] >= ema_fast
    )

    pullback_put = (
        (
            current5["high"] >= ema_fast
            or current5["high"] >= ema_slow
            or near_resistance
        )
        and current5["close"] <= ema_fast
    )

    call_score = 0
    put_score = 0

    call_reasons = []
    put_reasons = []

    if trend15_call:
        call_score += 30
        call_reasons.append("15M MASTER UP")

    if trend15_put:
        put_score += 30
        put_reasons.append("15M MASTER DOWN")

    if trend5_call:
        call_score += 25
        call_reasons.append("5M CONFIRM UP")

    if trend5_put:
        put_score += 25
        put_reasons.append("5M CONFIRM DOWN")

    if ema_call:
        call_score += 12
        call_reasons.append("EMA 9/21/50 BULL")

    if ema_put:
        put_score += 12
        put_reasons.append("EMA 9/21/50 BEAR")

    if flow_up:
        call_score += 8
        call_reasons.append("FLOW UP")

    if flow_down:
        put_score += 8
        put_reasons.append("FLOW DOWN")

    if candle["bull_rejection"]:
        call_score += 12
        call_reasons.append("BULL REJECTION")

    if candle["bear_rejection"]:
        put_score += 12
        put_reasons.append("BEAR REJECTION")

    if rsi_value > 50:
        call_score += 5
        call_reasons.append("RSI > 50")

    if rsi_value < 50:
        put_score += 5
        put_reasons.append("RSI < 50")

    if pullback_call:
        call_score += 8
        call_reasons.append("CALL PULLBACK")

    if pullback_put:
        put_score += 8
        put_reasons.append("PUT PULLBACK")

    if enough_room_call:
        call_score += 5
        call_reasons.append("CALL ROOM")

    if enough_room_put:
        put_score += 5
        put_reasons.append("PUT ROOM")

    if near_support:
        call_score += 5
        call_reasons.append("NEAR SUPPORT")

    if near_resistance:
        put_score += 5
        put_reasons.append("NEAR RESISTANCE")

    if near_resistance:
        call_score -= 8

    if near_support:
        put_score -= 8

    call_score = int(clamp(call_score))
    put_score = int(clamp(put_score))

    if call_score > put_score:
        direction = "CALL"
        score = call_score
        gap = call_score - put_score
        reasons = call_reasons
    elif put_score > call_score:
        direction = "PUT"
        score = put_score
        gap = put_score - call_score
        reasons = put_reasons
    else:
        direction = "NONE"
        score = 0
        gap = 0
        reasons = []

    master_call = trend15_call and trend5_call
    master_put = trend15_put and trend5_put

    pre_call = (
        master_call
        and call_score >= MIN_SCORE - 10
        and (call_score - put_score) >= MIN_GAP
    )

    pre_put = (
        master_put
        and put_score >= MIN_SCORE - 10
        and (put_score - call_score) >= MIN_GAP
    )

    call_signal = (
        master_call
        and call_score >= MIN_SCORE
        and (call_score - put_score) >= MIN_GAP
        and ema_call
        and candle["bull_rejection"]
        and pullback_call
    )

    put_signal = (
        master_put
        and put_score >= MIN_SCORE
        and (put_score - call_score) >= MIN_GAP
        and ema_put
        and candle["bear_rejection"]
        and pullback_put
    )

    if STRICT_MODE:
        call_signal = call_signal and flow_up
        put_signal = put_signal and flow_down

    if master_call and master_put:
        call_signal = False
        put_signal = False

    if call_signal:
        final_direction = "CALL"
        final_signal = True
    elif put_signal:
        final_direction = "PUT"
        final_signal = True
    else:
        final_direction = direction
        final_signal = False

    candle_open, candle_close = candle_window_5m(
        current5["timestamp"]
    )

    return {
        "symbol": symbol,
        "direction": final_direction,
        "signal": final_signal,
        "pre_call": pre_call,
        "pre_put": pre_put,
        "call_signal": call_signal,
        "put_signal": put_signal,
        "call_score": call_score,
        "put_score": put_score,
        "score": score,
        "gap": gap,
        "rsi": rsi_value,
        "entry": current5["close"],
        "timestamp": current5["timestamp"],
        "candle_open": candle_open,
        "candle_close": candle_close,
        "support": support,
        "resistance": resistance,
        "zone": (
            "SUPPORT"
            if near_support
            else "RESISTANCE"
            if near_resistance
            else "MID"
        ),
        "reasons": reasons
    }

# ============================================================
# RESULT CHECK
# ============================================================

def check_pending_trade():
    global PENDING_TRADE
    global CURRENT_STEP
    global SET_ACTIVE

    if not PENDING_TRADE:
        return

    expiry = int(PENDING_TRADE["expiry"])

    if now_ts() < expiry:
        return

    symbol = PENDING_TRADE["symbol"]
    direction = PENDING_TRADE["direction"]
    entry = float(PENDING_TRADE["entry"])
    step = int(PENDING_TRADE["step"])
    stake = int(PENDING_TRADE["stake"])

    signal_ts = int(PENDING_TRADE["timestamp"])
    entry_time_ts = int(PENDING_TRADE["entry_time"])

    candles = fetch_market(symbol)

    if not candles:
        print(
            f"[RESULT WAIT] {symbol}: no data | "
            f"expiry={fmt_ts(expiry)}"
        )
        return

    result_candle = None

    for candle in candles:
        if candle["timestamp"] >= expiry:
            result_candle = candle
            break

    if result_candle is None:
        print(
            f"[RESULT WAIT] {symbol}: "
            f"result candle not available yet | "
            f"expiry={fmt_ts(expiry)}"
        )
        return

    exit_price = float(result_candle["close"])
    result_ts = int(result_candle["timestamp"])

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

    result_open, result_close = candle_window_5m(result_ts)

    print(
        f"[RESULT] {symbol} {direction} {result} | "
        f"signal={fmt_ts(signal_ts)} | "
        f"entry_time={fmt_ts(entry_time_ts)} | "
        f"expiry={fmt_ts(expiry)} | "
        f"result_candle={result_open} -> {result_close} | "
        f"entry={entry} exit={exit_price}"
    )

    if result == "WIN":
        DAILY["wins"] += 1
        DAILY["set_wins"] += 1

        SET_ACTIVE = False
        CURRENT_STEP = 1

        send_discord(
            "🟢 **TRADEIFY V8 RESULT: WIN**\n\n"
            f"📌 `{symbol}`\n"
            f"➡️ **{direction}**\n"
            f"🎯 STEP `{step}/3`\n"
            f"💵 Stake `{stake}` บาท\n\n"
            f"🕒 Signal Candle: `{fmt_ts(signal_ts)}`\n"
            f"🚀 Entry Time: `{fmt_ts(entry_time_ts)}`\n"
            f"📍 Entry: `{entry}`\n"
            f"⏰ Expiry: `{fmt_ts(expiry)}`\n"
            f"🏁 Result Candle: `{result_open}` → `{result_close}`\n"
            f"🏁 Exit: `{exit_price}`\n"
            f"🕒 Checked: `{time_string()}`\n\n"
            "✅ SET WIN\n"
            "🔄 RESET → STEP 1\n"
            "♻️ RUN ALL DAY"
        )

    elif result == "LOSS":
        DAILY["losses"] += 1

        if step < MAX_STEP:
            CURRENT_STEP = step + 1
            SET_ACTIVE = True

            next_stake = STAKE_BY_STEP[CURRENT_STEP]

            send_discord(
                "🔴 **TRADEIFY V8 RESULT: LOSS**\n\n"
                f"📌 `{symbol}`\n"
                f"➡️ **{direction}**\n"
                f"🎯 STEP `{step}/3`\n"
                f"💵 Stake `{stake}` บาท\n\n"
                f"🕒 Signal Candle: `{fmt_ts(signal_ts)}`\n"
                f"🚀 Entry Time: `{fmt_ts(entry_time_ts)}`\n"
                f"📍 Entry: `{entry}`\n"
                f"⏰ Expiry: `{fmt_ts(expiry)}`\n"
                f"🏁 Result Candle: `{result_open}` → `{result_close}`\n"
                f"🏁 Exit: `{exit_price}`\n"
                f"🕒 Checked: `{time_string()}`\n\n"
                f"🔁 NEXT → STEP `{CURRENT_STEP}/3`\n"
                f"💵 Next Stake `{next_stake}` บาท"
            )

        else:
            DAILY["set_losses"] += 1
            CURRENT_STEP = 1
            SET_ACTIVE = False

            send_discord(
                "🔴 **TRADEIFY V8 SET LOSS**\n\n"
                f"📌 `{symbol}`\n"
                f"➡️ **{direction}**\n"
                f"🎯 STEP `3/3`\n"
                f"💵 Stake `{stake}` บาท\n\n"
                f"🕒 Signal Candle: `{fmt_ts(signal_ts)}`\n"
                f"🚀 Entry Time: `{fmt_ts(entry_time_ts)}`\n"
                f"📍 Entry: `{entry}`\n"
                f"⏰ Expiry: `{fmt_ts(expiry)}`\n"
                f"🏁 Result Candle: `{result_open}` → `{result_close}`\n"
                f"🏁 Exit: `{exit_price}`\n"
                f"🕒 Checked: `{time_string()}`\n\n"
                "💀 SET LOSS\n"
                "🔄 RESET → STEP 1\n"
                "♻️ RUN ALL DAY"
            )

    else:
        DAILY["void"] += 1
        CURRENT_STEP = 1
        SET_ACTIVE = False

        send_discord(
            "⚪ **TRADEIFY V8 RESULT: VOID**\n\n"
            f"📌 `{symbol}`\n"
            f"➡️ **{direction}**\n"
            f"🎯 STEP `{step}/3`\n\n"
            f"🕒 Signal Candle: `{fmt_ts(signal_ts)}`\n"
            f"🚀 Entry Time: `{fmt_ts(entry_time_ts)}`\n"
            f"📍 Entry: `{entry}`\n"
            f"⏰ Expiry: `{fmt_ts(expiry)}`\n"
            f"🏁 Result Candle: `{result_open}` → `{result_close}`\n"
            f"🏁 Exit: `{exit_price}`\n"
            f"🕒 Checked: `{time_string()}`\n\n"
            "🔄 RESET → STEP 1"
        )

    PENDING_TRADE = None
    save_state()

# ============================================================
# CREATE TRADE
# ============================================================

def create_trade(analysis):
    global PENDING_TRADE
    global SET_ACTIVE
    global SET_NUMBER
    global CURRENT_STEP

    if PENDING_TRADE:
        return False

    direction = analysis["direction"]

    if direction not in ("CALL", "PUT"):
        return False

    symbol = analysis["symbol"]
    timestamp = int(analysis["timestamp"])

    signal_key = f"{symbol}|{timestamp}|{direction}"

    if LAST_SIGNAL.get(symbol) == signal_key:
        return False

    if not SET_ACTIVE:
        SET_NUMBER += 1
        SET_ACTIVE = True
        CURRENT_STEP = 1

    step = CURRENT_STEP
    stake = STAKE_BY_STEP[step]

    entry = float(analysis["entry"])

    # The signal candle is already CLOSED.
    # Expiry = 5 minutes after that closed candle timestamp.
    expiry = timestamp + EXPIRY_SECONDS

    entry_time = now_ts()

    PENDING_TRADE = {
        "symbol": symbol,
        "direction": direction,
        "entry": entry,
        "timestamp": timestamp,
        "entry_time": entry_time,
        "expiry": expiry,
        "step": step,
        "stake": stake,
        "set_number": SET_NUMBER,
        "score": analysis["score"],
        "gap": analysis["gap"],
        "candle_open": analysis["candle_open"],
        "candle_close": analysis["candle_close"]
    }

    LAST_SIGNAL[symbol] = signal_key
    DAILY["signals"] += 1

    save_state()

    icon = "🟢" if direction == "CALL" else "🔴"

    send_discord(
        f"{icon} **TRADEIFY V8 SIGNAL**\n\n"
        f"📌 `{symbol}`\n"
        f"{icon} **{direction}**\n\n"

        f"🕒 **SIGNAL CANDLE**\n"
        f"เปิด: `{analysis['candle_open']}`\n"
        f"ปิด:  `{analysis['candle_close']}`\n"
        f"Unix TS: `{timestamp}`\n\n"

        f"🚀 **ENTRY TIME:** `{fmt_ts(entry_time)}`\n"
        f"📍 **ENTRY:** `{entry}`\n"
        f"⏰ **EXPIRY:** `{fmt_ts(expiry)}`\n"
        f"⏱ **DURATION:** `5 MIN`\n\n"

        f"📊 Score: `{analysis['score']}`\n"
        f"⚡ Gap: `+{analysis['gap']}`\n"
        f"📈 RSI: `{analysis['rsi']:.1f}`\n"
        f"📍 Zone: `{analysis['zone']}`\n\n"

        f"🎯 SET #{SET_NUMBER}\n"
        f"🔥 STEP `{step}/3`\n"
        f"💵 Stake: `{stake}` บาท\n\n"

        "📌 **สัญญาณนี้สร้างจากแท่ง 5M ที่ปิดแล้ว**\n"
        "♻️ **MODE = RUN ALL DAY**"
    )

    print(
        f"🚨 SIGNAL {symbol} {direction} | "
        f"candle={analysis['candle_open']} -> {analysis['candle_close']} | "
        f"entry_time={fmt_ts(entry_time)} | "
        f"expiry={fmt_ts(expiry)} | "
        f"entry={entry}"
    )

    return True

# ============================================================
# SCAN
# ============================================================

def scan():
    global LAST_CANDLE_5M

    if PENDING_TRADE:
        return

    for symbol in SYMBOLS:
        if PENDING_TRADE:
            break

        candles = fetch_market(symbol)

        if len(candles) < MIN_1M_CANDLES:
            continue

        candles5 = resample(candles, 5)

        if not candles5:
            continue

        latest_5m_ts = candles5[-1]["timestamp"]

        if LAST_CANDLE_5M.get(symbol) == latest_5m_ts:
            continue

        LAST_CANDLE_5M[symbol] = latest_5m_ts

        analysis = analyze(symbol, candles)

        if not analysis:
            continue

        print(
            f"[CHECK] {symbol} "
            f"| CANDLE={analysis['candle_open']} -> {analysis['candle_close']} "
            f"| CALL={analysis['call_score']} "
            f"| PUT={analysis['put_score']} "
            f"| DIR={analysis['direction']} "
            f"| GAP={analysis['gap']} "
            f"| SCORE={analysis['score']} "
            f"| PRE={analysis['pre_call'] or analysis['pre_put']} "
            f"| CONFIRMED={analysis['signal']}"
        )

        # ----------------------------------------------------
        # PRE SIGNAL
        # ----------------------------------------------------

        if analysis["pre_call"] or analysis["pre_put"]:
            pre_direction = (
                "CALL"
                if analysis["pre_call"]
                else "PUT"
            )

            pre_key = (
                f"{symbol}|"
                f"{latest_5m_ts}|"
                f"{pre_direction}"
            )

            if LAST_PRE.get(symbol) != pre_key:
                LAST_PRE[symbol] = pre_key

                icon = "🟡" if pre_direction == "CALL" else "🟠"

                send_discord(
                    f"{icon} **A+ PRE WARNING**\n"
                    f"`{symbol}` → **{pre_direction}**\n\n"
                    f"🕒 Candle: `{analysis['candle_open']}` → "
                    f"`{analysis['candle_close']}`\n"
                    f"📊 Score: `{analysis['score']}`\n"
                    f"⚡ Gap: `+{analysis['gap']}`\n"
                    f"📍 Zone: `{analysis['zone']}`\n"
                    f"🕒 Warning Time: `{time_string()}`\n"
                    "⚠️ **ยังไม่ใช่ออเดอร์**"
                )

        # ----------------------------------------------------
        # FINAL
        # ----------------------------------------------------

        if analysis["signal"]:
            if create_trade(analysis):
                break

# ============================================================
# WORKER
# ============================================================

def bot_worker():
    print("==========================================")
    print("🚀 TRADEIFY A+ MTF SNIPER V8")
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
    print("TIMEZONE  = Asia/Bangkok UTC+7")
    print("TIME TRACKING = ON")
    print("==========================================")

    load_state()
    reset_daily_if_needed()

    print("✅ Bot worker started")

    while True:
        try:
            reset_daily_if_needed()

            check_pending_trade()

            print(
                f"🔎 SCAN {time_string()} "
                f"| pending={1 if PENDING_TRADE else 0} "
                f"| step={CURRENT_STEP}"
            )

            if not PENDING_TRADE:
                scan()

            save_state()

            time.sleep(SCAN_SECONDS)

        except Exception as e:
            print("[WORKER ERROR]", repr(e))
            time.sleep(5)

# ============================================================
# MAIN
# ============================================================

def main():
    worker = Thread(
        target=bot_worker,
        daemon=True,
        name="tradeify-worker"
    )

    worker.start()

    print(
        f"🌐 Flask listening on 0.0.0.0:{PORT}"
    )

    app.run(
        host="0.0.0.0",
        port=PORT,
        debug=False,
        use_reloader=False
    )


if __name__ == "__main__":
    main()
