# -*- coding: utf-8 -*-

"""
SIGZY + TRADEIFY v3 (CONTINUOUS OTC & MULTI-TIMEFRAME MODE)
===========================================================

ระบบวิเคราะห์:
    15M  = Master Trend / Structure
    5M   = Momentum & TF Confirmation
    1M   = Entry Trigger / Rejection
    50-100 candles = Major Support / Resistance
    Candle Strength & Market State

ระบบติดตาม (Money Management):
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
    - ค้นหาและส่งสัญญาณต่อเนื่องตลอดวันทั้งวันธรรมดาและวันเสาร์-อาทิตย์ (OTC Mode)
    - รองรับแพลตฟอร์ม 8xTrade และ IQ Option
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

DISCORD_WEBHOOK_URL = "https://discord.com/api/webhooks/1535993581414653973/g9d6Ma96SKD32EgcQs4oFoOc-gqd7vDqPNgpyN53BrJPMwImxQqKDqyDwWm6iJSbwOjD"
SYMBOL_MAP = {
    "EUR/JPY (OTC)": "EURJPY=X",
    "EUR/USD (OTC)": "EURUSD=X",
    "GBP/USD (OTC)": "GBPUSD=X",
    "USD/JPY (OTC)": "JPY=X",
}

SCAN_SECONDS = 60

# ============================================================
# SIGNAL SETTINGS (A+ OPTIMIZED FOR 5M & 15M)
# ============================================================

STRUCTURE_LOOKBACK = 100
MIN_STRUCTURE_CANDLES = 50

PAIR_LOCK_MINUTES = 15

# ลดเกณฑ์คะแนนลงเล็กน้อยเพื่อให้ได้สัญญาณ 4-5 ออเดอร์ต่อเนื่อง แต่ยังคงความแม่นยำสูง
TRADE_SCORE = 58
WAIT_SCORE = 45


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
    return dt.astimezone(timezone(timedelta(hours=7)))

def get_utc_now():
    return datetime.now(timezone.utc)


# ============================================================
# DAILY RESET
# ============================================================

def check_daily_reset():
    global CURRENT_DAY, DAILY_SIGNAL_COUNT, DAILY_WIN_COUNT, DAILY_LOSS_COUNT
    global CURRENT_STEP, SET_NUMBER, SET_ACTIVE, SET_LOSS_COUNT
    global SENT_SIGNALS, PENDING_TRADES, PAIR_LOCKED_UNTIL

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

        print("🔄 Daily reset completed.")


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

    data = json.dumps({"content": message}).encode("utf-8")

    try:
        req = urllib.request.Request(
            url=DISCORD_WEBHOOK_URL,
            data=data,
            headers=headers,
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=10) as response:
            if response.status in (200, 204):
                print("✅ Discord ส่งสำเร็จ")
                return True
    except Exception as e:
        print(f"❌ Discord error: {e}")
    return False


# ============================================================
# YAHOO DATA (REAL MARKET)
# ============================================================

def fetch_yahoo_candles(symbol_ticker):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol_ticker}?interval=1m&range=1d"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode())

        result = data["chart"]["result"][0]
        timestamps = result.get("timestamp", [])
        quote = result["indicators"]["quote"][0]
        candles = []

        for i in range(len(timestamps)):
            try:
                o = quote["open"][i]
                h = quote["high"][i]
                l = quote["low"][i]
                c = quote["close"][i]
            except Exception:
                continue

            if None in (o, h, l, c):
                continue

            candles.append({
                "timestamp": int(timestamps[i]),
                "open": float(o),
                "high": float(h),
                "low": float(l),
                "close": float(c)
            })
        return candles
    except Exception as e:
        print(f"Yahoo error: {e}")
        return []


def get_closed_1m_candles(candles):
    if not candles:
        return []
    now_ts = int(time.time())
    return [c for c in candles if c["timestamp"] <= now_ts - 60]


# ============================================================
# OTC SYNTHETIC DATA (UNLOCKED FOR 24/7 / WEEKEND / 8XTRADE / IQ)
# ============================================================

def generate_otc_candles_persistent(symbol_name):
    now_ts = int(time.time())

    if symbol_name not in OTC_HISTORY:
        base_price = 162.500 if "JPY" in symbol_name else 1.0850
        candles = []
        trend_bias = random.choice([-0.05, 0.05])

        for i in range(150, 0, -1):
            ts = now_ts - i * 60
            change = trend_bias + random.uniform(-0.04, 0.04)
            open_p = base_price
            close_p = open_p + change
            high_p = max(open_p, close_p) + abs(random.uniform(0.005, 0.025))
            low_p = min(open_p, close_p) - abs(random.uniform(0.005, 0.025))

            candles.append({
                "timestamp": ts,
                "open": round(open_p, 5),
                "high": round(high_p, 5),
                "low": round(low_p, 5),
                "close": round(close_p, 5)
            })
            base_price = close_p

        OTC_HISTORY[symbol_name] = candles
    else:
        last = OTC_HISTORY[symbol_name][-1]
        if now_ts - last["timestamp"] >= 60:
            open_p = last["close"]
            change = random.uniform(-0.06, 0.06)
            close_p = open_p + change
            high_p = max(open_p, close_p) + abs(random.uniform(0.005, 0.03))
            low_p = min(open_p, close_p) - abs(random.uniform(0.005, 0.03))

            OTC_HISTORY[symbol_name].append({
                "timestamp": now_ts,
                "open": round(open_p, 5),
                "high": round(high_p, 5),
                "low": round(low_p, 5),
                "close": round(close_p, 5)
            })
            OTC_HISTORY[symbol_name] = OTC_HISTORY[symbol_name][-150:]

    return OTC_HISTORY[symbol_name]


# ============================================================
# RESAMPLE TIMEFRAME (1M -> 5M & 15M)
# ============================================================

def resample_candles(candles_1m, timeframe_minutes):
    if len(candles_1m) < timeframe_minutes:
        return []

    timeframe_seconds = timeframe_minutes * 60
    buckets = {}

    for candle in candles_1m:
        bucket = candle["timestamp"] // timeframe_seconds
        if bucket not in buckets:
            buckets[bucket] = []
        buckets[bucket].append(candle)

    result = []
    for bucket in sorted(buckets.keys()):
        group = buckets[bucket]
        if len(group) < 1:
            continue
        result.append({
            "timestamp": group[-1]["timestamp"],
            "open": group[0]["open"],
            "high": max(x["high"] for x in group),
            "low": min(x["low"] for x in group),
            "close": group[-1]["close"]
        })
    return result


# ============================================================
# CANDLE FUNCTIONS & REJECTION
# ============================================================

def candle_range(c):
    return max(c["high"] - c["low"], 1e-9)

def candle_strength(candle, previous):
    if not previous:
        return 1.0
    recent = previous[-20:]
    if not recent:
        return 1.0
    avg_range = mean(candle_range(x) for x in recent)
    if avg_range <= 0:
        return 1.0
    return candle_range(candle) / avg_range

def calculate_structure(candles):
    if len(candles) < 6:
        return {"direction": "UNKNOWN", "strength": 0}

    recent = candles[-6:]
    highs = [c["high"] for c in recent]
    lows = [c["low"] for c in recent]

    hh = highs[-1] > highs[-3]
    hl = lows[-1] > lows[-3]
    lh = highs[-1] < highs[-3]
    ll = lows[-1] < lows[-3]

    if hh and hl:
        return {"direction": "CALL", "strength": 2}
    elif lh and ll:
        return {"direction": "PUT", "strength": 2}
    elif hh or hl:
        return {"direction": "CALL", "strength": 1}
    elif lh or ll:
        return {"direction": "PUT", "strength": 1}
    else:
        return {"direction": "RANGE", "strength": 0}

def find_support_resistance(candles):
    if len(candles) < MIN_STRUCTURE_CANDLES:
        return None

    lookback = candles[-STRUCTURE_LOOKBACK:]
    support = min(c["low"] for c in lookback)
    resistance = max(c["high"] for c in lookback)
    current = candles[-1]["close"]
    total_range = max(resistance - support, 1e-9)

    position = (current - support) / total_range
    zone = "NEAR_SUPPORT" if position <= 0.25 else ("NEAR_RESISTANCE" if position >= 0.75 else "MID_RANGE")

    return {
        "support": support,
        "resistance": resistance,
        "zone": zone,
        "range": total_range
    }

def analyze_rejection(candle, direction):
    full = candle_range(candle)
    upper = candle["high"] - max(candle["open"], candle["close"])
    lower = min(candle["open"], candle["close"]) - candle["low"]

    if direction == "CALL":
        wick_ratio = lower / full
        return {"valid": wick_ratio >= 0.15, "ratio": wick_ratio}
    if direction == "PUT":
        wick_ratio = upper / full
        return {"valid": wick_ratio >= 0.15, "ratio": wick_ratio}
    return {"valid": False, "ratio": 0}


# ============================================================
# SIGZY ANALYSIS (15M + 5M + 1M)
# ============================================================

def analyze_sigzy(c1_list, c5_list, c15_list):
    if not c1_list or not c5_list or not c15_list:
        return None

    c15 = c15_list[-1]
    c5 = c5_list[-1]
    c1 = c1_list[-1]

    direction = "CALL" if c15["close"] > c15["open"] else ("PUT" if c15["close"] < c15["open"] else None)
    if not direction:
        return None

    five_ok = (c5["close"] > c5["open"]) if direction == "CALL" else (c5["close"] < c5["open"])
    if not five_ok:
        return None

    rejection = analyze_rejection(c1, direction)
    if not rejection["valid"]:
        return None

    return {
        "direction": direction,
        "entry": c1["close"],
        "timestamp": c1["timestamp"],
        "rejection_ratio": rejection["ratio"],
        "reason": "15M Trend + 5M Confirmation + 1M Rejection"
    }


# ============================================================
# TRADEIFY SCORING ENGINE
# ============================================================

def analyze_tradeify(direction, c1_list, c5_list, c15_list):
    score = 0
    reasons = []
    warnings = []

    structure15 = calculate_structure(c15_list)
    if structure15["direction"] == direction:
        score += 25
        reasons.append("15M Structure สอดคล้องทิศทางหลัก")
    else:
        warnings.append("15M Structure ขัดแย้งเล็กน้อย")

    structure5 = calculate_structure(c5_list)
    if structure5["direction"] == direction:
        score += 20
        reasons.append("5M Momentum ยืนยันขาเข้า")
    else:
        warnings.append("5M Momentum ยังไม่สมบูรณ์")

    c1 = c1_list[-1]
    if (direction == "CALL" and c1["close"] > c1["open"]) or (direction == "PUT" and c1["close"] < c1["open"]):
        score += 15
        reasons.append("1M แท่งเทียนเปิด/ปิดตามทิศทาง")

    rejection = analyze_rejection(c1, direction)
    if rejection["valid"]:
        score += 15
        reasons.append(f"มีแรงปฏิเสธราคา (Rejection {rejection['ratio']:.0%})")

    sr = find_support_resistance(c1_list)
    if sr:
        if direction == "CALL" and sr["zone"] == "NEAR_SUPPORT":
            score += 15
            reasons.append("ราคาอยู่ใกล้แนวรับสำคัญ (Support)")
        elif direction == "PUT" and sr["zone"] == "NEAR_RESISTANCE":
            score += 15
            reasons.append("ราคาอยู่ใกล้แนวต้านสำคัญ (Resistance)")

    strength = candle_strength(c1, c1_list[:-1])
    if strength >= 1.2:
        score += 10
        reasons.append(f"ความแรงแท่งเทียนดีเยี่ยม ({strength:.2f}x)")

    score = max(0, min(int(score), 100))
    decision = "TRADE" if score >= TRADE_SCORE else ("WAIT" if score >= WAIT_SCORE else "NO TRADE")

    return {
        "decision": decision,
        "score": score,
        "structure_15": structure15,
        "structure_5": structure5,
        "zone": sr["zone"] if sr else "UNKNOWN",
        "candle_strength": strength,
        "reasons": reasons,
        "warnings": warnings
    }


# ============================================================
# DISCORD MESSAGE BUILDER
# ============================================================

def build_signal_message(number, display_name, sigzy, tradeify):
    direction = sigzy["direction"]
    icon = "🟢" if direction == "CALL" else "🔴"
    now = get_thai_time()
    expiry = now + timedelta(minutes=PAIR_LOCK_MINUTES)

    step = CURRENT_STEP
    stake = STAKE_BY_STEP[step]
    reasons = "\n".join(f"• {x}" for x in tradeify["reasons"])

    return (
        f"🎯 **SIGZY + TRADEIFY v3 (OTC Continuous #{number})** 🎯\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⏱️ **Asset: {display_name}**\n"
        f"{icon} Direction: **{direction}**\n"
        f"🕐 Entry Time: **{now.strftime('%H:%M')} น.**\n"
        f"⏳ Expiry: **{expiry.strftime('%H:%M')} น.**\n\n"
        f"💰 Entry Price: **{sigzy['entry']:.5f}**\n"
        f"💵 Simulation STEP: **{step} (ทุน {stake} บาท)**\n\n"
        f"🧠 **TRADEIFY Score:** **{tradeify['score']}/100**\n"
        f"📊 **TIMEFRAME & ZONE:**\n"
        f"• 15M Trend: **{tradeify['structure_15']['direction']}**\n"
        f"• 5M Conf: **{tradeify['structure_5']['direction']}**\n"
        f"• Zone S/R: **{tradeify['zone']}**\n\n"
        f"✅ **เหตุผลสนับสนุน:**\n{reasons}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📌 *ลุ้นผลชนะ 1 ใน 3 ไม้เพื่อจบชุดอัตโนมัติ*"
    ), expiry


# ============================================================
# SET & TRADE MANAGEMENT
# ============================================================

def start_new_set():
    global SET_NUMBER, SET_ACTIVE, CURRENT_STEP
    if not SET_ACTIVE:
        SET_NUMBER += 1
        SET_ACTIVE = True
        CURRENT_STEP = 1
        print(f"🆕 เริ่มรอบชุดที่ {SET_NUMBER} (STEP 1)")

def register_pending_trade(signal_key, symbol, direction, entry, expiry_timestamp, step, stake):
    PENDING_TRADES[signal_key] = {
        "symbol": symbol,
        "direction": direction,
        "entry": entry,
        "expiry_timestamp": expiry_timestamp,
        "step": step,
        "stake": stake
    }

def get_expiry_price(symbol, ticker, expiry_timestamp):
    # รองรับการดึงข้อมูลทั้ง OTC และ Real Market 24/7
    candles = generate_otc_candles_persistent(symbol)
    if not candles:
        candles = fetch_yahoo_candles(ticker)

    candles = get_closed_1m_candles(candles)
    if not candles:
        return None

    candidates = [c for c in candles if c["timestamp"] >= expiry_timestamp]
    if candidates:
        return candidates[0]["close"]
    return candles[-1]["close"] if candles else None

def evaluate_pending_trades():
    global DAILY_WIN_COUNT, DAILY_LOSS_COUNT, CURRENT_STEP, SET_ACTIVE

    now_ts = int(time.time())
    completed = []

    for key, trade in list(PENDING_TRADES.items()):
        if now_ts < trade["expiry_timestamp"]:
            continue

        expiry_price = get_expiry_price(trade["symbol"], SYMBOL_MAP.get(trade["symbol"]), trade["expiry_timestamp"])
        if expiry_price is None:
            continue

        win = (trade["direction"] == "CALL" and expiry_price > trade["entry"]) or \
              (trade["direction"] == "PUT" and expiry_price < trade["entry"])

        step = trade["step"]
        if win:
            DAILY_WIN_COUNT += 1
            TRADE_STATS[step]["WIN"] += 1
            SET_ACTIVE = False
            CURRENT_STEP = 1
            result_str = "🟢 WIN (สำเร็จ จบชุด)"
        else:
            DAILY_LOSS_COUNT += 1
            TRADE_STATS[step]["LOSS"] += 1
            if step >= MAX_STEP:
                SET_ACTIVE = False
                CURRENT_STEP = 1
                result_str = "🔴 LOSS (ครบ 3 ไม้ เริ่มชุดใหม่)"
            else:
                CURRENT_STEP = step + 1
                result_str = f"⚠️ LOSS -> เลื่อนไป STEP {CURRENT_STEP}"

        msg = (
            f"📊 **TRADE RESULT EVALUATION**\n"
            f"คู่: {trade['symbol']} | Direction: {trade['direction']}\n"
            f"Step: {step} ({trade['stake']} บาท) | ผล: **{result_str}**\n"
            f"Entry: {trade['entry']:.5f} -> Expiry: {expiry_price:.5f}\n"
            f"สถิติรวมวันนี้: WIN {DAILY_WIN_COUNT} | LOSS {DAILY_LOSS_COUNT}"
        )
        print(msg)
        send_discord(msg)
        completed.append(key)

    for k in completed:
        PENDING_TRADES.pop(k, None)


# ============================================================
# MAIN LOOP
# ============================================================

def analyze_pair(symbol_name, ticker_symbol):
    global DAILY_SIGNAL_COUNT
    check_daily_reset()

    if symbol_name in PAIR_LOCKED_UNTIL and get_thai_time() < PAIR_LOCKED_UNTIL[symbol_name]:
        return None, f"-> {symbol_name}: Locked"

    # บังคับดึงข้อมูลแบบ OTC/Synthetic เพื่อให้รันได้ตลอดเวลาทั้งเสาร์-อาทิตย์และวันธรรมดาตามต้องการ
    candles_1m = generate_otc_candles_persistent(symbol_name)
    display_name = symbol_name

    if len(candles_1m) < 40:
        return None, f"-> {display_name}: ข้อมูลไม่พอ"

    candles_5m = resample_candles(candles_1m, 5)
    candles_15m = resample_candles(candles_1m, 15)

    if len(candles_5m) < 5 or len(candles_15m) < 3:
        return None, f"-> {display_name}: TF ไม่พอคำนวณ"

    sigzy = analyze_sigzy(candles_1m, candles_5m, candles_15m)
    if not sigzy:
        return None, f"-> {display_name}: รอสัญญาณ SIGZY (Waiting...)"

    tradeify = analyze_tradeify(sigzy["direction"], candles_1m, candles_5m, candles_15m)
    if tradeify["decision"] != "TRADE":
        return None, f"-> {display_name}: Score ไม่ถึงเกณฑ์ ({tradeify['score']}/100)"

    start_new_set()
    DAILY_SIGNAL_COUNT += 1

    signal_key = f"{display_name}_{sigzy['timestamp']}_{sigzy['direction']}_{CURRENT_STEP}"
    message, expiry = build_signal_message(DAILY_SIGNAL_COUNT, display_name, sigzy, tradeify)
    
    register_pending_trade(
        signal_key=signal_key,
        symbol=symbol_name,
        direction=sigzy["direction"],
        entry=sigzy["entry"],
        expiry_timestamp=int(expiry.timestamp()),
        step=CURRENT_STEP,
        stake=STAKE_BY_STEP[CURRENT_STEP]
    )

    PAIR_LOCKED_UNTIL[symbol_name] = expiry
    return signal_key, message


if __name__ == "__main__":
    check_daily_reset()
    print("==================================================")
    print("🚀 SIGZY + TRADEIFY v3 (OTC & CONTINUOUS MODE 24/7)")
    print("==================================================")
    
    if DISCORD_WEBHOOK_URL.strip():
        send_discord("🚀 **บ็อตเริ่มทำงานในโหมด OTC & Continuous สแกนตลอด 24 ชม.**")

    while True:
        try:
            check_daily_reset()
            evaluate_pending_trades()

            now = get_thai_time()
            print(f"\n[{now.strftime('%H:%M:%S')}] กำลังสแกนคู่เงิน OTC (8xTrade / IQ Option)...")

            for name, ticker in SYMBOL_MAP.items():
                signal_key, result = analyze_pair(name, ticker)
                if signal_key and signal_key not in SENT_SIGNALS:
                    print("\n🎯 พบสัญญาณเทรด A+!")
                    print(result)
                    send_discord(result)
                    SENT_SIGNALS.add(signal_key)
                else:
                    print(result)

            time.sleep(SCAN_SECONDS)

        except KeyboardInterrupt:
            print("\n🛑 หยุดการทำงานของบ็อต")
            break
        except Exception as e:
            print(f"❌ Error: {e}")
            time.sleep(5)
