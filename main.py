# -*- coding: utf-8 -*-
"""
TRADEIFY v6 — 1-IN-3 REAL TRACKER
=================================
15M MASTER / 5M CONFIRM / 1M ENTRY

หลัก:
- สัญญาณ Confirmed เท่านั้นที่เข้า 1 ใน 3
- STEP 1 -> LOSS = STEP 2
- STEP 2 -> LOSS = STEP 3
- WIN ทุก Step = จบชุด / กลับ STEP 1
- LOSS STEP 3 = จบชุด LOSS / กลับ STEP 1
- VOID = ไม่เลื่อน Step
- ล็อกคู่ที่มี Pending อยู่ ไม่ยิงซ้ำ
- 1 สัญญาณต่อแท่ง 1M
- ประเมินผลจากราคาตลาดจริงที่ timestamp expiry
- ไม่สร้างราคา OTC ปลอม
- ส่ง Early Warning ได้ แต่ Early ไม่ถูกนับเป็นไม้
- ส่ง Confirmed / Result / Daily Summary เข้า Discord
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

# ฝัง Discord Webhook URL ตามที่คุณต้องการ
DISCORD_WEBHOOK_URL = os.getenv(
    "DISCORD_WEBHOOK_URL", 
    "https://discord.com/api/webhooks/1535993581414653973/g9d6Ma96SKD32EgcQs4oFoOc-gqd7vDqPNgpyN53BrJPMwImxQqKDqyDwWm6iJSbwOjD"
).strip()

MARKET_MODE = os.getenv("MARKET_MODE", "AUTO").upper()
OTC_API_URL = os.getenv("OTC_API_URL", "").strip()

SCAN_SECONDS = int(os.getenv("SCAN_SECONDS", "10"))
EXPIRY_SECONDS = int(os.getenv("EXPIRY_SECONDS", "60"))

MIN_SCORE = 80
MIN_EDGE = 15

SR_LOOKBACK = 120
MIN_1M_CANDLES = 120
MIN_5M_CANDLES = 30
MIN_15M_CANDLES = 20

STAKE_BY_STEP = {1: 100, 2: 200, 3: 300}

SYMBOLS = [
    x.strip() for x in os.getenv(
        "SYMBOLS",
        "EUR/USD,GBP/USD,USD/JPY,EUR/JPY"
    ).split(",") if x.strip()
]

YAHOO_MAP = {
    "EUR/USD": "EURUSD=X",
    "GBP/USD": "GBPUSD=X",
    "USD/JPY": "JPY=X",
    "EUR/JPY": "EURJPY=X",
    "AUD/USD": "AUDUSD=X",
    "USD/CHF": "CHF=X",
    "USD/CAD": "CAD=X",
    "GBP/JPY": "GBPJPY=X",
}

THAI_TZ = timezone(timedelta(hours=7))

# ============================================================
# STATE
# ============================================================

CURRENT_DAY = None
CURRENT_STEP = 1
SET_ACTIVE = False
SET_NUMBER = 0

# กันยิงซ้ำแท่งเดิม
LAST_CANDLE = {}

# กัน Early ซ้ำ
LAST_EARLY = {}

# กัน Confirmed ซ้ำ
LAST_CONFIRMED = {}

# trade_key -> trade data
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

def http_json(url, timeout=10):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "TRADEIFY-V6",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode())

# ============================================================
# DISCORD
# ============================================================

def send_discord(message):
    if not DISCORD_WEBHOOK_URL:
        print("[DISCORD DISABLED]\n" + message)
        return False

    try:
        payload = json.dumps({"content": message}).encode("utf-8")
        req = urllib.request.Request(
            DISCORD_WEBHOOK_URL,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "TRADEIFY-V6",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as response:
            return response.status in (200, 204)
    except Exception as e:
        print("[DISCORD ERROR]", e)
        return False

# ============================================================
# MARKET DATA
# ============================================================

def normalize_candle(x):
    if isinstance(x, dict):
        timestamp = x.get("timestamp", x.get("time", x.get("t")))
        op = x.get("open", x.get("o"))
        hi = x.get("high", x.get("h"))
        lo = x.get("low", x.get("l"))
        cl = x.get("close", x.get("c"))
    elif isinstance(x, (list, tuple)) and len(x) >= 5:
        timestamp, op, hi, lo, cl = x[:5]
    else:
        return None

    try:
        timestamp = float(timestamp)
        if timestamp > 10_000_000_000:
            timestamp /= 1000

        return {
            "timestamp": int(timestamp),
            "open": float(op),
            "high": float(hi),
            "low": float(lo),
            "close": float(cl),
        }
    except Exception:
        return None

def fetch_yahoo(symbol):
    ticker = YAHOO_MAP.get(symbol, symbol)
    url = (
        "https://query1.finance.yahoo.com/v8/finance/chart/"
        + urllib.parse.quote(ticker, safe="")
        + "?interval=1m&range=1d"
    )

    try:
        data = http_json(url)
        result = data["chart"]["result"][0]
        timestamps = result.get("timestamp", [])
        quote = result["indicators"]["quote"][0]
        candles = []

        for i, ts in enumerate(timestamps):
            try:
                candles.append({
                    "timestamp": int(ts),
                    "open": float(quote["open"][i]),
                    "high": float(quote["high"][i]),
                    "low": float(quote["low"][i]),
                    "close": float(quote["close"][i]),
                })
            except Exception:
                continue

        cutoff = unix_now() - 60
        return [c for c in candles if c["timestamp"] <= cutoff]
    except Exception as e:
        print("[YAHOO]", symbol, e)
        return []

def fetch_otc(symbol):
    if not OTC_API_URL:
        return []

    try:
        separator = "&" if "?" in OTC_API_URL else "?"
        url = OTC_API_URL + separator + urllib.parse.urlencode({
            "symbol": symbol,
            "interval": "1m",
            "limit": 500,
        })

        response = http_json(url)

        if isinstance(response, list):
            raw = response
        elif isinstance(response, dict):
            raw = response.get("candles", response.get("data", []))
        else:
            raw = []

        candles = []
        for item in raw:
            candle = normalize_candle(item)
            if candle:
                candles.append(candle)

        candles.sort(key=lambda x: x["timestamp"])

        cutoff = unix_now() - 60
        return [c for c in candles if c["timestamp"] <= cutoff]
    except Exception as e:
        print("[OTC ERROR]", symbol, e)
        return []

def fetch_market(symbol):
    if MARKET_MODE == "OTC":
        return fetch_otc(symbol)

    if MARKET_MODE == "LIVE":
        return fetch_yahoo(symbol)

    if OTC_API_URL:
        otc = fetch_otc(symbol)
        if otc:
            return otc

    return fetch_yahoo(symbol)

# ============================================================
# TIMEFRAME
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

# ============================================================
# INDICATORS
# ============================================================

def calculate_ema(values, period):
    if len(values) < period:
        return None

    result = mean(values[:period])
    multiplier = 2 / (period + 1)

    for value in values[period:]:
        result = value * multiplier + result * (1 - multiplier)

    return result

def calculate_rsi(values, period=14):
    if len(values) < period + 1:
        return None

    gains = []
    losses = []

    for i in range(1, len(values)):
        diff = values[i] - values[i - 1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))

    avg_gain = mean(gains[:period])
    avg_loss = mean(losses[:period])

    for i in range(period, len(gains)):
        avg_gain = ((avg_gain * (period - 1)) + gains[i]) / period
        avg_loss = ((avg_loss * (period - 1)) + losses[i]) / period

    if avg_loss == 0:
        return 100

    return 100 - 100 / (1 + avg_gain / avg_loss)

def market_structure(candles, period=20):
    if len(candles) < period:
        return "RANGE", 0

    data = candles[-period:]
    half = period // 2
    first = data[:half]
    second = data[half:]

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
        strength = min(1, abs(sc - fc) / avg_range)
        return "CALL", strength

    if sh < fh and sl < fl and sc < fc:
        strength = min(1, abs(sc - fc) / avg_range)
        return "PUT", strength

    return "RANGE", 0

def candle_info(candle):
    full = max(candle["high"] - candle["low"], 1e-12)
    body = abs(candle["close"] - candle["open"])

    upper = candle["high"] - max(candle["open"], candle["close"])
    lower = min(candle["open"], candle["close"]) - candle["low"]

    return {
        "bull": candle["close"] > candle["open"],
        "bear": candle["close"] < candle["open"],
        "body": body / full,
        "upper": upper / full,
        "lower": lower / full,
    }

def price_flow(candles):
    if len(candles) < 5:
        return "RANGE"

    recent = candles[-5:]
    up = sum(x["close"] > x["open"] for x in recent)
    down = sum(x["close"] < x["open"] for x in recent)

    if up >= 4:
        return "CALL"
    if down >= 4:
        return "PUT"
    return "RANGE"

def support_resistance(candles):
    if len(candles) < SR_LOOKBACK:
        return None

    data = candles[-SR_LOOKBACK:]
    support = min(x["low"] for x in data)
    resistance = max(x["high"] for x in data)
    current = candles[-1]["close"]

    distance = max(resistance - support, 1e-12)
    position = (current - support) / distance

    if position <= 0.20:
        zone = "SUPPORT"
    elif position >= 0.80:
        zone = "RESISTANCE"
    else:
        zone = "MID"

    return {
        "support": support,
        "resistance": resistance,
        "zone": zone,
    }

# ============================================================
# ANALYSIS
# ============================================================

def analyze(symbol, candles_1m):
    if len(candles_1m) < MIN_1M_CANDLES:
        return None

    candles_5m = resample(candles_1m, 5)
    candles_15m = resample(candles_1m, 15)

    if len(candles_5m) < MIN_5M_CANDLES:
        return None
    if len(candles_15m) < MIN_15M_CANDLES:
        return None

    p1 = [x["close"] for x in candles_1m]
    p5 = [x["close"] for x in candles_5m]
    p15 = [x["close"] for x in candles_15m]

    ema9 = calculate_ema(p1, 9)
    ema21 = calculate_ema(p1, 21)
    ema50 = calculate_ema(p1, 50)

    ema5_9 = calculate_ema(p5, 9)
    ema5_21 = calculate_ema(p5, 21)

    ema15_9 = calculate_ema(p15, 9)
    ema15_21 = calculate_ema(p15, 21)

    rsi1 = calculate_rsi(p1)
    rsi5 = calculate_rsi(p5)
    rsi15 = calculate_rsi(p15)

    if any(v is None for v in [
        ema9, ema21, ema50,
        ema5_9, ema5_21,
        ema15_9, ema15_21,
        rsi1, rsi5, rsi15
    ]):
        return None

    structure15, strength15 = market_structure(candles_15m)
    structure5, strength5 = market_structure(candles_5m)
    flow = price_flow(candles_1m)
    sr = support_resistance(candles_1m)
    zone = sr["zone"] if sr else "UNKNOWN"
    candle = candle_info(candles_1m[-1])

    score = {"CALL": 0, "PUT": 0}
    reasons = {"CALL": [], "PUT": []}

    def add(direction, points, reason):
        score[direction] += points
        reasons[direction].append(reason)

    if structure15 == "CALL":
        add("CALL", 30, "15M structure bullish")
    elif structure15 == "PUT":
        add("PUT", 30, "15M structure bearish")

    if ema15_9 > ema15_21:
        add("CALL", 12, "15M EMA bullish")
    elif ema15_9 < ema15_21:
        add("PUT", 12, "15M EMA bearish")

    if 52 <= rsi15 <= 68:
        add("CALL", 8, "15M RSI bullish regime")
    elif 32 <= rsi15 <= 48:
        add("PUT", 8, "15M RSI bearish regime")

    if structure5 == "CALL":
        add("CALL", 22, "5M structure confirms")
    elif structure5 == "PUT":
        add("PUT", 22, "5M structure confirms")

    if ema5_9 > ema5_21:
        add("CALL", 10, "5M EMA momentum UP")
    elif ema5_9 < ema5_21:
        add("PUT", 10, "5M EMA momentum DOWN")

    if flow == "CALL":
        add("CALL", 8, "1M flow UP")
    elif flow == "PUT":
        add("PUT", 8, "1M flow DOWN")

    if ema9 > ema21 > ema50:
        add("CALL", 8, "1M EMA 9>21>50")
    elif ema9 < ema21 < ema50:
        add("PUT", 8, "1M EMA 9<21<50")

    if candle["lower"] >= 0.25:
        add("CALL", 8, "1M lower rejection")
    if candle["upper"] >= 0.25:
        add("PUT", 8, "1M upper rejection")

    if zone == "SUPPORT":
        add("CALL", 10, "Major Support")
    elif zone == "RESISTANCE":
        add("PUT", 10, "Major Resistance")

    if rsi1 >= 75 and zone == "RESISTANCE":
        score["CALL"] -= 15
    if rsi1 <= 25 and zone == "SUPPORT":
        score["PUT"] -= 15

    sideway = structure15 == "RANGE" or structure5 == "RANGE"
    if sideway:
        score["CALL"] -= 12
        score["PUT"] -= 12

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

    master_ok = structure15 == direction
    confirm_ok = structure5 == direction

    ema_ok = (
        (direction == "CALL" and ema9 > ema21 > ema50)
        or
        (direction == "PUT" and ema9 < ema21 < ema50)
    )

    early = (
        master_ok and confirm_ok and ema_ok
        and score[direction] >= MIN_SCORE - 6
        and edge >= MIN_EDGE - 3
    )

    confirmed = (
        master_ok and confirm_ok and ema_ok
        and not sideway
        and score[direction] >= MIN_SCORE
        and edge >= MIN_EDGE
    )

    if direction == "CALL":
        candle_ok = candle["bull"] or candle["lower"] >= 0.30
    else:
        candle_ok = candle["bear"] or candle["upper"] >= 0.30

    confirmed = confirmed and candle_ok

    return {
        "symbol": symbol,
        "direction": direction,
        "early": early,
        "confirmed": confirmed,
        "score": score[direction],
        "edge": edge,
        "entry": candles_1m[-1]["close"],
        "timestamp": candles_1m[-1]["timestamp"],
        "structure15": structure15,
        "structure5": structure5,
        "flow": flow,
        "zone": zone,
        "rsi1": rsi1,
        "rsi5": rsi5,
        "rsi15": rsi15,
        "strength15": strength15,
        "strength5": strength5,
        "reasons": reasons[direction],
    }

# ============================================================
# 1-IN-3 SET MANAGEMENT
# ============================================================

def start_set():
    global SET_ACTIVE, SET_NUMBER, CURRENT_STEP

    if not SET_ACTIVE:
        SET_ACTIVE = True
        SET_NUMBER += 1
        CURRENT_STEP = 1
        print(f"[SET] #{SET_NUMBER} START -> STEP 1")

def reset_set_after_win():
    global SET_ACTIVE, CURRENT_STEP
    SET_ACTIVE = False
    CURRENT_STEP = 1

def advance_after_loss(step):
    global SET_ACTIVE, CURRENT_STEP

    if step < 3:
        SET_ACTIVE = True
        CURRENT_STEP = step + 1
        return f"LOSS -> STEP {CURRENT_STEP}"

    SET_ACTIVE = False
    CURRENT_STEP = 1
    return "LOSS STEP 3/3 -> SET LOST"

# ============================================================
# DAILY RESET
# ============================================================

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

    for key in DAILY:
        DAILY[key] = 0

    for step in STATS:
        for result in STATS[step]:
            STATS[step][result] = 0

    print("[RESET]", today)

# ============================================================
# EARLY WARNING
# ============================================================

def send_early(data):
    key = (
        data["symbol"],
        data["timestamp"],
        data["direction"]
    )

    if LAST_EARLY.get(data["symbol"]) == key:
        return

    LAST_EARLY[data["symbol"]] = key

    icon = "🟡 CALL" if data["direction"] == "CALL" else "🟠 PUT"

    send_discord(
        f"{icon} **EARLY WARNING**\n"
        f"คู่: `{data['symbol']}`\n"
        f"Score: `{data['score']}/100`\n"
        f"Edge: `+{data['edge']}`\n"
        f"15M: `{data['structure15']}`\n"
        f"5M: `{data['structure5']}`\n"
        f"1M Flow: `{data['flow']}`\n"
        f"Zone: `{data['zone']}`\n\n"
        f"⚠️ **ยังไม่เข้าไม้** — รอ Confirmed"
    )

# ============================================================
# CONFIRMED — ONE SIGNAL ONLY
# ============================================================

def send_confirmed(data):
    global CURRENT_STEP

    key = (
        data["symbol"],
        data["timestamp"],
        data["direction"]
    )

    if LAST_CONFIRMED.get(data["symbol"]) == key:
        return

    for trade in PENDING_TRADES.values():
        if trade["symbol"] == data["symbol"]:
            return

    start_set()

    step = CURRENT_STEP
    stake = STAKE_BY_STEP[step]
    expiry = data["timestamp"] + EXPIRY_SECONDS

    trade_key = (
        f"{data['symbol']}|"
        f"{data['timestamp']}|"
        f"{data['direction']}|"
        f"STEP{step}"
    )

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
    entry_time = datetime.fromtimestamp(
        data["timestamp"], timezone.utc
    ).astimezone(THAI_TZ)

    reason_text = "\n".join(
        "• " + x for x in data["reasons"][:7]
    )

    send_discord(
        f"🎯 **TRADEIFY A++ CONFIRMED — SET #{SET_NUMBER}**\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"{icon} `{data['symbol']}` → **{data['direction']}**\n"
        f"🕐 Entry: `{entry_time:%H:%M:%S}`\n"
        f"⏳ Expiry: `+{EXPIRY_SECONDS//60} MIN`\n\n"
        f"🎯 **ไม้ที่ {step}/3**\n"
        f"💰 Stake: **{stake} บาท**\n"
        f"🧠 Score: **{data['score']}/100**\n"
        f"⚡ Edge: **+{data['edge']}**\n\n"
        f"15M: `{data['structure15']}`\n"
        f"5M: `{data['structure5']}`\n"
        f"1M Flow: `{data['flow']}`\n"
        f"S/R: `{data['zone']}`\n"
        f"RSI 15M: `{data['rsi15']:.1f}`\n"
        f"RSI 5M: `{data['rsi5']:.1f}`\n"
        f"RSI 1M: `{data['rsi1']:.1f}`\n\n"
        f"📌 เหตุผล:\n{reason_text}\n\n"
        f"🔒 **ล็อกคู่จนกว่าจะรู้ผลไม้ปัจจุบัน**\n"
        f"⚠️ ระบบเป็นการคัดกรอง ไม่รับประกันผล"
    )

# ============================================================
# RESULT — REAL MARKET PRICE
# ============================================================

def evaluate_trades():
    current_time = unix_now()

    for key, trade in list(PENDING_TRADES.items()):

        if current_time < trade["expiry"] + 5:
            continue

        candles = fetch_market(trade["symbol"])
        if not candles:
            print("[RESULT WAIT] ไม่มีข้อมูลตลาดจริง:", trade["symbol"])
            continue

        candidates = [
            x for x in candles
            if x["timestamp"] >= trade["expiry"]
        ]

        if not candidates:
            print(
                "[RESULT WAIT] ยังไม่มีราคา expiry จริง:",
                trade["symbol"],
                trade["expiry"]
            )
            continue

        expiry_candle = candidates[0]
        expiry_price = expiry_candle["close"]
        entry = trade["entry"]

        if expiry_price == entry:
            result = "VOID"
        elif (
            trade["direction"] == "CALL"
            and expiry_price > entry
        ):
            result = "WIN"
        elif (
            trade["direction"] == "PUT"
            and expiry_price < entry
        ):
            result = "WIN"
        else:
            result = "LOSS"

        step = trade["step"]
        STATS[step][result] += 1
        DAILY[result.lower() + "s" if result != "VOID" else "void"] += 1

        if result == "WIN":
            status = "🟢 WIN"
            set_status = "จบชุด → กลับ STEP 1"
            reset_set_after_win()

        elif result == "LOSS":
            status = "🔴 LOSS"
            set_status = advance_after_loss(step)

        else:
            status = "⚪ VOID"
            set_status = "ไม่เลื่อน Step"

        total = DAILY["wins"] + DAILY["losses"]
        winrate = (DAILY["wins"] / total * 100) if total else 0

        expiry_dt = datetime.fromtimestamp(
            expiry_candle["timestamp"],
            timezone.utc
        ).astimezone(THAI_TZ)

        send_discord(
            f"📊 **TRADE RESULT — SET #{SET_NUMBER}**\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"คู่: `{trade['symbol']}`\n"
            f"Direction: `{trade['direction']}`\n"
            f"ไม้: `{step}/3`\n"
            f"Entry: `{entry:.6f}`\n"
            f"Expiry: `{expiry_price:.6f}`\n"
            f"Result Time: `{expiry_dt:%H:%M:%S}`\n\n"
            f"ผล: **{status}**\n"
            f"สถานะชุด: **{set_status}**\n\n"
            f"📈 วันนี้\n"
            f"🟢 WIN: `{DAILY['wins']}`\n"
            f"🔴 LOSS: `{DAILY['losses']}`\n"
            f"⚪ VOID: `{DAILY['void']}`\n"
            f"Win Rate: `{winrate:.2f}%`\n\n"
            f"STEP 1: W{STATS[1]['WIN']}/L{STATS[1]['LOSS']}\n"
            f"STEP 2: W{STATS[2]['WIN']}/L{STATS[2]['LOSS']}\n"
            f"STEP 3: W{STATS[3]['WIN']}/L{STATS[3]['LOSS']}"
        )

        del PENDING_TRADES[key]

# ============================================================
# SCAN
# ============================================================

def scan_symbol(symbol):
    candles = fetch_market(symbol)

    if len(candles) < MIN_1M_CANDLES:
        print(f"[WAIT] {symbol}: candles={len(candles)}")
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

# ============================================================
# STATUS
# ============================================================

def print_status():
    total = DAILY["wins"] + DAILY["losses"]
    winrate = DAILY["wins"] / total * 100 if total else 0

    print(
        "\n━━━━━━━━━━━━━━━━━━━━\n"
        f"TIME: {thai_now():%Y-%m-%d %H:%M:%S}\n"
        f"SET: #{SET_NUMBER} ACTIVE={SET_ACTIVE}\n"
        f"CURRENT STEP: {CURRENT_STEP}/3\n"
        f"SIGNALS: {DAILY['signals']}\n"
        f"WIN: {DAILY['wins']}\n"
        f"LOSS: {DAILY['losses']}\n"
        f"VOID: {DAILY['void']}\n"
        f"WINRATE: {winrate:.2f}%\n"
        f"PENDING: {len(PENDING_TRADES)}\n"
        "━━━━━━━━━━━━━━━━━━━━"
    )

# ============================================================
# MAIN
# ============================================================

def main():
    daily_reset()

    print("======================================")
    print("🚀 TRADEIFY v6 — 1-IN-3 REAL TRACKER")
    print("15M MASTER / 5M CONFIRM / 1M ENTRY")
    print("MARKET:", MARKET_MODE)
    print("SYMBOLS:", SYMBOLS)
    print("======================================")

    if MARKET_MODE == "OTC" and not OTC_API_URL:
        print("⚠️ OTC_API_URL ยังไม่ได้ตั้งค่า")
        print("ระบบจะไม่สร้างราคา OTC ปลอม")

    send_discord(
        "🚀 **TRADEIFY v6 STARTED**\n"
        "15M Master → 5M Confirm → 1M Entry\n"
        "🎯 1 ใน 3 จริง\n"
        "🔒 ล็อกคู่ระหว่าง Pending\n"
        "📊 WIN/LOSS จากราคาตลาดจริง\n"
        "🚫 ไม่สร้างราคา OTC ปลอม"
    )

    while True:
        try:
            daily_reset()
            evaluate_trades()

            for symbol in SYMBOLS:
                try:
                    scan_symbol(symbol)
                except Exception as e:
                    print("[SCAN ERROR]", symbol, e)

            print_status()
            time.sleep(SCAN_SECONDS)

        except KeyboardInterrupt:
            print("🛑 BOT STOPPED")
            break

        except Exception as e:
            print("[MAIN ERROR]", e)
            time.sleep(5)

if __name__ == "__main__":
    main()
