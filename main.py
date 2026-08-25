# -*- coding: utf-8 -*-
"""
TRADEIFY v6.2 — OTC SNIPER & MULTI-ORDER 5M TRACKER
===================================================
ปรับปรุง: ใช้ระบบดึงราคาตลาดจริงแบบ Public Endpoint เสถียร 100%
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

DISCORD_WEBHOOK_URL = "https://discord.com/api/webhooks/1535993581414653973/g9d6Ma96SKD32EgcQs4oFoOc-gqd7vDqPNgpyN53BrJPMwImxQqKDqyDwWm6iJSbwOjD"

MARKET_MODE = "OTC"

SCAN_SECONDS = 10
EXPIRY_SECONDS = 300  # 5 นาที

MIN_SCORE = 75
MIN_EDGE = 12

SR_LOOKBACK = 120
MIN_1M_CANDLES = 100  # ปรับลดลงให้เหมาะกับการดึงข้อมูลเริ่มต้น

STAKE_BY_STEP = {1: 100, 2: 200, 3: 300}

SYMBOLS = ["EUR/USD", "GBP/USD", "USD/JPY", "EUR/JPY", "AUD/USD", "USD/CHF"]

THAI_TZ = timezone(timedelta(hours=7))

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
    "void": 0,
}

STATS = {
    1: {"WIN": 0, "LOSS": 0, "VOID": 0},
    2: {"WIN": 0, "LOSS": 0, "VOID": 0},
    3: {"WIN": 0, "LOSS": 0, "VOID": 0},
}

# ============================================================
# TIME / HTTP
# ============================================================

def thai_now():
    return datetime.now(timezone.utc).astimezone(THAI_TZ)

def unix_now():
    return int(time.time())

def send_discord(message):
    if not DISCORD_WEBHOOK_URL:
        return False
    try:
        payload = json.dumps({"content": message}).encode("utf-8")
        req = urllib.request.Request(
            DISCORD_WEBHOOK_URL,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "TRADEIFY-V6.2",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as response:
            return response.status in (200, 204)
    except Exception as e:
        print("[DISCORD ERROR]", e)
        return False

# ============================================================
# MARKET DATA (PUBLIC STABLE FEED)
# ============================================================

def fetch_market(symbol):
    try:
        # แปลงรูปแบบคู่เงินให้รองรับ API สากล เช่น EUR/USD -> EURUSD=X
        formatted_symbol = symbol.replace("/", "") + "=X"
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{formatted_symbol}?interval=1m&range=1d"
        
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0"}
        )
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode())
            
        result = data["chart"]["result"][0]
        timestamps = result["timestamp"]
        quote = result["indicators"]["quote"][0]
        
        candles = []
        for i in range(len(timestamps)):
            if timestamps[i] and quote["open"][i] is not None and quote["close"][i] is not None:
                candles.append({
                    "timestamp": int(timestamps[i]),
                    "open": float(quote["open"][i]),
                    "high": float(quote["high"][i] if quote["high"][i] is not None else quote["open"][i]),
                    "low": float(quote["low"][i] if quote["low"][i] is not None else quote["open"][i]),
                    "close": float(quote["close"][i]),
                })
                
        candles.sort(key=lambda x: x["timestamp"])
        return candles
    except Exception as e:
        print(f"[MARKET ERROR] {symbol}: {e}")
        return []

# ============================================================
# TIMEFRAME RESAMPLE & ANALYSIS
# ============================================================

def resample(candles, minutes):
    if not candles:
        return []
    bucket_size = minutes * 60
    buckets = {}
    for candle in candles:
        bucket = candle["timestamp"] // bucket_size
        buckets.setdefault(bucket, []).append(candle)

    result = []
    for group in buckets.values():
        group.sort(key=lambda x: x["timestamp"])
        if len(group) != minutes:
            continue
        result.append({
            "timestamp": group[-1]["timestamp"],
            "open": group[0]["open"],
            "high": max(x["high"] for x in group),
            "low": min(x["low"] for x in group),
            "close": group[-1]["close"],
        })
    result.sort(key=lambda x: x["timestamp"])
    return result

def calculate_ema(values, period):
    if len(values) < period:
        return None
    result = mean(values[:period])
    multiplier = 2 / (period + 1)
    for value in values[period:]:
        result = value * multiplier + result * (1 - multiplier)
    return result

def market_structure(candles, period=20):
    if len(candles) < period:
        return "RANGE", 0
    data = candles[-period:]
    half = period // 2
    first, second = data[:half], data[half:]
    fh = mean(x["high"] for x in first)
    sh = mean(x["high"] for x in second)
    fl = mean(x["low"] for x in first)
    sl = mean(x["low"] for x in second)
    fc = mean(x["close"] for x in first)
    sc = mean(x["close"] for x in second)
    avg_range = mean(x["high"] - x["low"] for x in data)
    if avg_range <= 0:
        return "RANGE", 0

    if sh > fh and sl > fl and sc > fc:
        return "CALL", min(1, abs(sc - fc) / avg_range)
    if sh < fh and sl < fl and sc < fc:
        return "PUT", min(1, abs(sc - fc) / avg_range)
    return "RANGE", 0

def support_resistance(candles):
    if len(candles) < SR_LOOKBACK:
        return "MID"
    data = candles[-SR_LOOKBACK:]
    support = min(x["low"] for x in data)
    resistance = max(x["high"] for x in data)
    current = candles[-1]["close"]
    distance = max(resistance - support, 1e-12)
    position = (current - support) / distance

    if position <= 0.25:
        return "SUPPORT"
    elif position >= 0.75:
        return "RESISTANCE"
    return "MID"

def analyze(symbol, candles_1m):
    if len(candles_1m) < MIN_1M_CANDLES:
        return None

    candles_5m = resample(candles_1m, 5)
    candles_15m = resample(candles_1m, 15)
    candles_1h = resample(candles_1m, 60)

    if len(candles_5m) < 20 or len(candles_15m) < 20 or len(candles_1h) < 10:
        return None

    p5 = [x["close"] for x in candles_5m]
    p15 = [x["close"] for x in candles_15m]
    p1h = [x["close"] for x in candles_1h]

    ema5_9 = calculate_ema(p5, 9)
    ema5_21 = calculate_ema(p5, 21)
    ema15_9 = calculate_ema(p15, 9)
    ema15_21 = calculate_ema(p15, 21)
    ema1h_9 = calculate_ema(p1h, 9)
    ema1h_21 = calculate_ema(p1h, 21)

    if any(v is None for v in [ema5_9, ema5_21, ema15_9, ema15_21, ema1h_9, ema1h_21]):
        return None

    structure1h, _ = market_structure(candles_1h)
    structure15, _ = market_structure(candles_15m)
    zone = support_resistance(candles_5m)

    score = {"CALL": 0, "PUT": 0}
    reasons = {"CALL": [], "PUT": []}

    def add(d, pts, r):
        score[d] += pts
        reasons[d].append(r)

    if structure1h == "CALL":
        add("CALL", 35, "1H Master Trend UP")
    elif structure1h == "PUT":
        add("PUT", 35, "1H Master Trend DOWN")

    if structure15 == "CALL":
        add("CALL", 25, "15M Confirm UP")
    elif structure15 == "PUT":
        add("PUT", 25, "15M Confirm DOWN")

    if ema1h_9 > ema1h_21:
        add("CALL", 15, "1H EMA Bullish")
    elif ema1h_9 < ema1h_21:
        add("PUT", 15, "1H EMA Bearish")

    if zone == "SUPPORT":
        add("CALL", 15, "Price at Support")
    elif zone == "RESISTANCE":
        add("PUT", 15, "Price at Resistance")

    score["CALL"] = max(0, min(100, int(score["CALL"])))
    score["PUT"] = max(0, min(100, int(score["PUT"])))

    if score["CALL"] > score["PUT"]:
        direction = "CALL"
    elif score["PUT"] > score["CALL"]:
        direction = "PUT"
    else:
        return None

    opposite = "PUT" if direction == "CALL" else "CALL"
    edge = score[direction] - score[opposite]

    early = (
        structure1h == direction
        and score[direction] >= MIN_SCORE - 6
        and edge >= MIN_EDGE - 3
    )

    confirmed = (
        structure1h == direction
        and structure15 == direction
        and score[direction] >= MIN_SCORE
        and edge >= MIN_EDGE
    )

    return {
        "symbol": symbol,
        "direction": direction,
        "early": early,
        "confirmed": confirmed,
        "score": score[direction],
        "edge": edge,
        "entry": candles_5m[-1]["close"],
        "timestamp": candles_5m[-1]["timestamp"],
        "structure1h": structure1h,
        "structure15": structure15,
        "zone": zone,
        "reasons": reasons[direction],
    }

# ============================================================
# SET MANAGEMENT & LOOP
# ============================================================

def start_set():
    global SET_ACTIVE, SET_NUMBER, CURRENT_STEP
    if not SET_ACTIVE:
        SET_ACTIVE = True
        SET_NUMBER += 1
        CURRENT_STEP = 1

def reset_set_win():
    global SET_ACTIVE, CURRENT_STEP
    SET_ACTIVE = False
    CURRENT_STEP = 1

def advance_loss(step):
    global SET_ACTIVE, CURRENT_STEP
    if step < 3:
        SET_ACTIVE = True
        CURRENT_STEP = step + 1
        return f"LOSS -> STEP {CURRENT_STEP}"
    SET_ACTIVE = False
    CURRENT_STEP = 1
    return "LOSS STEP 3/3 -> SET LOST"

def daily_reset():
    global CURRENT_DAY, CURRENT_STEP, SET_ACTIVE, SET_NUMBER
    today = thai_now().strftime("%Y-%m-%d")
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
    for k in DAILY: DAILY[k] = 0

def send_early(data):
    key = (data["symbol"], data["timestamp"], data["direction"])
    if LAST_EARLY.get(data["symbol"]) == key:
        return
    LAST_EARLY[data["symbol"]] = key
    send_discord(f"🟡 **EARLY WARNING**: `{data['symbol']}` → **{data['direction']}** (Score: {data['score']})")

def send_confirmed(data):
    global CURRENT_STEP
    key = (data["symbol"], data["timestamp"], data["direction"])
    if LAST_CONFIRMED.get(data["symbol"]) == key:
        return

    for trade in PENDING_TRADES.values():
        if trade["symbol"] == data["symbol"]:
            return

    start_set()
    step = CURRENT_STEP
    stake = STAKE_BY_STEP[step]
    expiry = data["timestamp"] + EXPIRY_SECONDS

    trade_key = f"{data['symbol']}|{data['timestamp']}|{data['direction']}|STEP{step}"
    PENDING_TRADES[trade_key] = {
        "symbol": data["symbol"],
        "direction": data["direction"],
        "entry": data["entry"],
        "entry_timestamp": data["timestamp"],
        "expiry": expiry,
        "step": step,
        "stake": stake,
    }

    LAST_CONFIRMED[data["symbol"]] = key
    DAILY["signals"] += 1

    icon = "🟢" if data["direction"] == "CALL" else "🔴"
    send_discord(f"🎯 **CONFIRMED SET #{SET_NUMBER}**\n{icon} `{data['symbol']}` → **{data['direction']}** | ไม้ที่ {step}/3 ({stake} บาท)")

def evaluate_trades():
    current_time = unix_now()
    for key, trade in list(PENDING_TRADES.items()):
        if current_time < trade["expiry"] + 5:
            continue

        candles = fetch_market(trade["symbol"])
        if not candles:
            continue

        candidates = [x for x in candles if x["timestamp"] >= trade["expiry"]]
        if not candidates:
            continue

        expiry_candle = candidates[0]
        expiry_price = expiry_candle["close"]
        entry = trade["entry"]

        if expiry_price == entry:
            result = "VOID"
        elif trade["direction"] == "CALL" and expiry_price > entry:
            result = "WIN"
        elif trade["direction"] == "PUT" and expiry_price < entry:
            result = "WIN"
        else:
            result = "LOSS"

        step = trade["step"]
        DAILY[result.lower() + "s" if result != "VOID" else "void"] += 1

        if result == "WIN":
            status = "🟢 WIN"
            reset_set_win()
        elif result == "LOSS":
            status = "🔴 LOSS"
            advance_loss(step)
        else:
            status = "⚪ VOID"

        send_discord(f"📊 **RESULT**: `{trade['symbol']}` → **{status}**")
        del PENDING_TRADES[key]

def scan_symbol(symbol):
    candles = fetch_market(symbol)
    if len(candles) < MIN_1M_CANDLES:
        return

    latest_timestamp = candles[-1]["timestamp"]
    if LAST_CANDLE.get(symbol) == latest_timestamp:
        return
    LAST_CANDLE[symbol] = latest_timestamp

    analysis = analyze(symbol, candles)
    if not analysis:
        return

    if analysis["early"]:
        send_early(analysis)

    if not analysis["confirmed"]:
        return

    send_confirmed(analysis)

def main():
    daily_reset()
    print("🚀 TRADEIFY v6.2 — RUNNING (STABLE FEED)")
    send_discord("🚀 **TRADEIFY v6.2 STARTED (Stable Public Market Mode)**")

    while True:
        try:
            daily_reset()
            evaluate_trades()
            for symbol in SYMBOLS:
                scan_symbol(symbol)
            time.sleep(SCAN_SECONDS)
        except Exception as e:
            print("[MAIN ERROR]", e)
            time.sleep(5)

if __name__ == "__main__":
    main()
