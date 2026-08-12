# -*- coding: utf-8 -*-
"""
SIGZY BRAIN V6.2 - SINGLE FILE FOR RAILWAY
PRESERVES: Memory Structure / 85.0 Threshold
"""

import time
import json
import os
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ============================================================
# CONFIGURATION
# ============================================================

DISCORD_WEBHOOK_URL = os.environ.get(
    "DISCORD_WEBHOOK_URL",
    "https://discord.com/api/webhooks/1537074805818269746/jDuxNjMLZhmnb_BqysF5SiH9m97GDLPSYPZ1RUxziQY7QBdY2zP_YwBHMvjWwOhx_1Ir"
)
TWELVE_DATA_API_KEY = os.environ.get(
    "TWELVE_DATA_API_KEY",
    "af2b5d958f0f4691907e77742f5462ee"
)

CRYPTO = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
FOREX = [
    "GBPUSD", "GBPJPY", "USDJPY", "EURUSD",
    "AUDUSD", "NZDUSD", "USDCAD", "USDCHF"
]
ALL_SYMBOLS = CRYPTO + FOREX

TWELVE_INTERVAL = "5min"
OUTPUT_SIZE = 1000
SCAN_INTERVAL = 600
TIMEOUT = 30

MIN_SCORE = 85.0
TP_ATR = 0.80
SL_ATR = 0.60

BASE_DIR = Path(__file__).resolve().parent
MEMORY_FILE = BASE_DIR / "sigzy_memory.json"

THAI_TZ = timezone(timedelta(hours=7))


# ============================================================
# TIME
# ============================================================

def now():
    return datetime.now(THAI_TZ).strftime("%Y-%m-%d %H:%M:%S")


# ============================================================
# MEMORY
# ============================================================

def default_memory():
    return {
        "sets": [],
        "total_sets": 0,
        "sets_with_win": 0,
        "sets_loss_0_of_3": 0,
        "opp_wins": 0,
        "opp_losses": 0,
        "opp_ambiguous": 0,
        "active_sets": {},
        "last_closed_candle": {}
    }


def load_memory():
    if not MEMORY_FILE.exists():
        return default_memory()

    try:
        with open(MEMORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        memory = default_memory()
        if isinstance(data, dict):
            memory.update(data)

        return memory

    except Exception as e:
        print("Memory load error:", e)
        return default_memory()


MEMORY = load_memory()


def save_memory():
    try:
        with open(MEMORY_FILE, "w", encoding="utf-8") as f:
            json.dump(
                MEMORY,
                f,
                ensure_ascii=False,
                indent=2
            )
    except Exception as e:
        print("Memory save error:", e)


# ============================================================
# DISCORD
# ============================================================

def send_discord(message):
    if not DISCORD_WEBHOOK_URL:
        print("Discord: MISSING")
        return False

    try:
        payload = json.dumps({"content": message}).encode("utf-8")
        req = urllib.request.Request(
            DISCORD_WEBHOOK_URL,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "Mozilla/5.0"
            },
            method="POST"
        )

        with urllib.request.urlopen(req, timeout=TIMEOUT) as response:
            if response.status >= 300:
                print("Discord Error status:", response.status)
                return False

        return True

    except Exception as e:
        print("Discord Error:", e)
        return False


# ============================================================
# TWELVE DATA
# ============================================================

def format_symbol_for_twelve(symbol):
    if symbol in CRYPTO:
        base = symbol[:-4]
        quote = symbol[-4:]
        return f"{base}/{quote}"
    elif symbol in FOREX:
        return f"{symbol[:3]}/{symbol[3:]}"
    return symbol


def aggregate_5m_to_10m(candles):
    result = []
    for i in range(0, len(candles) - 1, 2):
        a = candles[i]
        b = candles[i + 1]
        result.append({
            "datetime": a["datetime"],
            "open": a["open"],
            "high": max(a["high"], b["high"]),
            "low": min(a["low"], b["low"]),
            "close": b["close"]
        })
    return result


def get_market_data(symbol):
    if not TWELVE_DATA_API_KEY:
        raise RuntimeError("TWELVE_DATA_API_KEY ยังไม่ได้ตั้งค่า")

    formatted_symbol = format_symbol_for_twelve(symbol)
    url = (
        f"https://api.twelvedata.com/time_series"
        f"?symbol={formatted_symbol}"
        f"&interval={TWELVE_INTERVAL}"
        f"&outputsize={OUTPUT_SIZE + 20}"
        f"&timezone=UTC"
        f"&apikey={TWELVE_DATA_API_KEY}"
    )

    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0"}
    )

    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"HTTPError {e.code}: {error_body}")
    except Exception as e:
        raise RuntimeError(f"Network error: {e}")

    if data.get("status") == "error":
        raise RuntimeError(data.get("message", "Twelve Data API Error"))

    values = data.get("values", [])
    if len(values) < 100:
        raise RuntimeError(f"ข้อมูล 5M ไม่พอ: {len(values)} candles")

    candles = []
    for x in reversed(values):
        try:
            candles.append({
                "datetime": x["datetime"],
                "open": float(x["open"]),
                "high": float(x["high"]),
                "low": float(x["low"]),
                "close": float(x["close"])
            })
        except Exception:
            continue

    if len(candles) < 100:
        raise RuntimeError("OHLC data ไม่เพียงพอ")

    closed_5m = candles[:-1]
    ten_minute = aggregate_5m_to_10m(closed_5m)

    if len(ten_minute) < 100:
        raise RuntimeError("แท่ง 10M ไม่เพียงพอ")

    return ten_minute[-500:]


# ============================================================
# INDICATORS
# ============================================================

def sma(values, period):
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


def atr(candles, period=14):
    if len(candles) < period + 1:
        return None

    trs = []
    for i in range(1, len(candles)):
        current = candles[i]
        previous = candles[i - 1]
        tr = max(
            current["high"] - current["low"],
            abs(current["high"] - previous["close"]),
            abs(current["low"] - previous["close"])
        )
        trs.append(tr)

    return sum(trs[-period:]) / period


def support_resistance(candles, lookback=100):
    data = candles[-lookback:]
    support = min(x["low"] for x in data)
    resistance = max(x["high"] for x in data)
    return support, resistance


# ============================================================
# CANDLE & MOMENTUM
# ============================================================

def candle_structure(candle):
    o = candle["open"]
    h = candle["high"]
    l = candle["low"]
    c = candle["close"]

    total_range = h - l
    body = abs(c - o)

    if total_range <= 0:
        return 0, 0

    upper_wick = h - max(o, c)
    lower_wick = min(o, c) - l

    bull = 0
    bear = 0

    if c > o:
        bull += 1
    elif c < o:
        bear += 1

    if body / total_range >= 0.60:
        if c > o:
            bull += 2
        elif c < o:
            bear += 2

    if lower_wick > body * 1.5:
        bull += 1
    if upper_wick > body * 1.5:
        bear += 1

    return bull, bear


def momentum_score(candles):
    closes = [x["close"] for x in candles]
    if len(closes) < 10:
        return 0

    change = closes[-1] - closes[-6]
    if change > 0:
        return 10
    if change < 0:
        return -10
    return 0


# ============================================================
# ANALYSIS
# ============================================================

def analyze_market(symbol, candles):
    closes = [x["close"] for x in candles]
    price = closes[-1]

    ma3 = sma(closes, 3)
    ma7 = sma(closes, 7)
    ma15 = sma(closes, 15)

    current_atr = atr(candles, 14)
    support, resistance = support_resistance(candles, 100)

    bull_candle, bear_candle = candle_structure(candles[-1])
    momentum = momentum_score(candles)

    call_score = 0
    put_score = 0

    call_reasons = []
    put_reasons = []

    if ma3 and ma7 and ma15:
        if ma3 > ma7 and ma7 > ma15 and price > ma3:
            call_score += 30
            call_reasons.append("MA bullish")
        elif ma3 < ma7 and ma7 < ma15 and price < ma3:
            put_score += 30
            put_reasons.append("MA bearish")

    if momentum > 0:
        call_score += 10
        call_reasons.append("Momentum bullish")
    elif momentum < 0:
        put_score += 10
        put_reasons.append("Momentum bearish")

    if bull_candle >= 2:
        call_score += 15
        call_reasons.append("Bullish candle")
    if bear_candle >= 2:
        put_score += 15
        put_reasons.append("Bearish candle")

    if current_atr:
        distance_support = abs(price - support)
        distance_resistance = abs(resistance - price)

        if distance_support <= current_atr * 0.8:
            call_score += 15
            call_reasons.append("Near support")
        if distance_resistance <= current_atr * 0.8:
            put_score += 15
            put_reasons.append("Near resistance")

    if call_score >= MIN_SCORE and call_score > put_score:
        decision = "CALL"
        score = call_score
        reasons = call_reasons
    elif put_score >= MIN_SCORE and put_score > call_score:
        decision = "PUT"
        score = put_score
        reasons = put_reasons
    else:
        decision = "WAIT"
        score = max(call_score, put_score)
        reasons = ["ไม่มี setup ผ่านเกณฑ์ 85.0"]

    return {
        "symbol": symbol,
        "price": price,
        "decision": decision,
        "score": score,
        "call_score": call_score,
        "put_score": put_score,
        "atr": current_atr,
        "support": support,
        "resistance": resistance,
        "reasons": reasons,
        "candle_time": candles[-1]["datetime"]
    }


# ============================================================
# OPPORTUNITY
# ============================================================

def create_opportunity(signal, number):
    if signal["decision"] == "WAIT":
        return None
    if not signal["atr"] or signal["atr"] <= 0:
        return None

    entry = signal["price"]
    direction = signal["decision"]

    if direction == "CALL":
        tp = entry + signal["atr"] * TP_ATR
        sl = entry - signal["atr"] * SL_ATR
    else:
        tp = entry - signal["atr"] * TP_ATR
        sl = entry + signal["atr"] * SL_ATR

    return {
        "opp": number,
        "direction": direction,
        "entry": entry,
        "entry_time": now(),
        "entry_candle": signal["candle_time"],
        "tp": tp,
        "sl": sl,
        "status": "ACTIVE"
    }


def statistics_text():
    total = MEMORY["total_sets"]
    wins = MEMORY["sets_with_win"]
    losses = MEMORY["sets_loss_0_of_3"]

    set_rate = (wins / total * 100) if total > 0 else 0
    total_opp = MEMORY["opp_wins"] + MEMORY["opp_losses"]
    opp_rate = (MEMORY["opp_wins"] / total_opp * 100) if total_opp > 0 else 0

    return (
        f"Sets: {total}\n"
        f">=1 WIN: {wins}\n"
        f"0/3 LOSS: {losses}\n"
        f"Set WIN Rate: {set_rate:.2f}%\n"
        f"Opportunity WIN Rate: {opp_rate:.2f}%"
    )


def send_signal(signal, opportunity):
    icon = "🟢" if opportunity["direction"] == "CALL" else "🔴"
    message = (
        f"{icon} **SIGZY V6.2 SIGNAL**\n"
        f"คู่เงิน: **{signal['symbol']}**\n"
        f"OPP: **{opportunity['opp']}**\n"
        f"Signal: **{opportunity['direction']}**\n"
        f"เวลาเข้า: **{opportunity['entry_time']}**\n"
        f"Entry: **{opportunity['entry']:.5f}**\n"
        f"TP: **{opportunity['tp']:.5f}**\n"
        f"SL: **{opportunity['sl']:.5f}**\n"
        f"Score: **{signal['score']}**\n"
        f"CALL: {signal['call_score']} | PUT: {signal['put_score']}\n"
        f"แท่ง 10M: **{signal['candle_time']}**\n"
        f"เหตุผล: {', '.join(signal['reasons'])}"
    )
    send_discord(message)


# ============================================================
# EVALUATE
# ============================================================

def check_opportunity(opportunity, candle):
    high = candle["high"]
    low = candle["low"]

    if opportunity["direction"] == "CALL":
        hit_tp = high >= opportunity["tp"]
        hit_sl = low <= opportunity["sl"]
    else:
        hit_tp = low <= opportunity["tp"]
        hit_sl = high >= opportunity["sl"]

    if hit_tp and hit_sl:
        return "AMBIGUOUS"
    if hit_tp:
        return "WIN"
    if hit_sl:
        return "LOSS"
    return "PENDING"


def finish_set_as_win(symbol, active, opportunity):
    opportunity["status"] = "WIN"
    MEMORY["opp_wins"] += 1
    MEMORY["total_sets"] += 1
    MEMORY["sets_with_win"] += 1

    MEMORY["sets"].append(active)
    MEMORY["sets"] = MEMORY["sets"][-1000:]
    del MEMORY["active_sets"][symbol]
    save_memory()

    send_discord(
        f"🟢 **SIGZY WIN**\n"
        f"คู่เงิน: {symbol}\n"
        f"OPP{opportunity['opp']} WIN\n\n"
        f"{statistics_text()}"
    )


def finish_set_zero_three(symbol, active):
    MEMORY["total_sets"] += 1
    MEMORY["sets_loss_0_of_3"] += 1

    MEMORY["sets"].append(active)
    MEMORY["sets"] = MEMORY["sets"][-1000:]
    del MEMORY["active_sets"][symbol]
    save_memory()

    send_discord(
        f"🔴 **SIGZY 0/3 LOSS**\n"
        f"คู่เงิน: {symbol}\n"
        f"ครบ 3 Opportunity แล้วไม่มี WIN\n\n"
        f"{statistics_text()}"
    )


def process_active_set(symbol, candles):
    active = MEMORY["active_sets"].get(symbol)
    if not active:
        return False

    opportunity = active["opportunities"][-1]
    candle = candles[-1]

    if candle["datetime"] == opportunity["entry_candle"]:
        return True

    result = check_opportunity(opportunity, candle)
    if result == "PENDING":
        return True

    if result == "AMBIGUOUS":
        opportunity["status"] = "AMBIGUOUS"
        MEMORY["opp_ambiguous"] += 1
        save_memory()
        send_discord(
            f"🟡 **SIGZY AMBIGUOUS**\n"
            f"คู่เงิน: {symbol}\n"
            f"OPP{opportunity['opp']} ชน TP และ SL ในแท่งเดียวกัน"
        )
        return True

    if result == "WIN":
        finish_set_as_win(symbol, active, opportunity)
        return False

    opportunity["status"] = "LOSS"
    MEMORY["opp_losses"] += 1

    if opportunity["opp"] < 3:
        signal = analyze_market(symbol, candles)
        if signal["decision"] != "WAIT":
            next_opp = create_opportunity(signal, opportunity["opp"] + 1)
            if next_opp:
                active["opportunities"].append(next_opp)
                save_memory()
                send_signal(signal, next_opp)
                send_discord(
                    f"🔄 **RE-ANALYSIS**\n"
                    f"คู่เงิน: {symbol}\n"
                    f"OPP{opportunity['opp']} = LOSS → วิเคราะห์ใหม่ OPP{next_opp['opp']}"
                )
                return True

        save_memory()
        send_discord(
            f"⚪ **WAIT**\n"
            f"คู่เงิน: {symbol}\n"
            f"OPP{opportunity['opp']} LOSS (ยังไม่มี setup ผ่านเกณฑ์ 85.0)"
        )
        return True

    finish_set_zero_three(symbol, active)
    return False


# ============================================================
# PROCESS SYMBOL
# ============================================================

def process_symbol(symbol):
    try:
        candles = get_market_data(symbol)
    except Exception as e:
        print(f"[{now()}] {symbol} DATA ERROR: {e}")
        return

    latest = candles[-1]
    latest_time = latest["datetime"]

    previous = MEMORY["last_closed_candle"].get(symbol)
    if previous == latest_time:
        return

    MEMORY["last_closed_candle"][symbol] = latest_time
    save_memory()

    print(f"[{now()}] NEW CLOSED 10M [{symbol}]: {latest_time}")

    if symbol in MEMORY["active_sets"]:
        still_active = process_active_set(symbol, candles)
        if still_active:
            return

    signal = analyze_market(symbol, candles)
    print(f"[{now()}] {symbol} -> {signal['decision']} Score={signal['score']}")

    if signal["decision"] == "WAIT":
        return

    opportunity = create_opportunity(signal, 1)
    if opportunity:
        MEMORY["active_sets"][symbol] = {
            "symbol": symbol,
            "created_at": now(),
            "opportunities": [opportunity]
        }
        save_memory()
        send_signal(signal, opportunity)


# ============================================================
# STARTUP & MAIN
# ============================================================

def startup():
    print("=" * 70)
    print("SIGZY BRAIN V6.2 - MULTI SYMBOL SCANNER")
    print("Threshold: 85.0 | Railway Ready")
    print("=" * 70)
    print(f"Total Symbols: {len(ALL_SYMBOLS)}")
    print(f"Memory Path: {MEMORY_FILE}")
    print("=" * 70)

    if not TWELVE_DATA_API_KEY:
        print("ERROR: TWELVE_DATA_API_KEY missing")
        return False
    if not DISCORD_WEBHOOK_URL:
        print("ERROR: DISCORD_WEBHOOK_URL missing")
        return False
    return True


def main():
    if not startup():
        return

    send_discord("🚀 **SIGZY BRAIN V6.2 STARTED ON RAILWAY** (Multi-Symbol Scanner)")

    while True:
        cycle_start = time.time()

        for symbol in ALL_SYMBOLS:
            process_symbol(symbol)
            time.sleep(1)  # ป้องกันยิง API ถี่เกินไป

        elapsed = time.time() - cycle_start
        sleep_time = max(5, SCAN_INTERVAL - elapsed)

        print(f"[{now()}] ครบรอบสแกน รออีก {sleep_time:.1f} วินาที...")
        try:
            time.sleep(sleep_time)
        except KeyboardInterrupt:
            print("SIGZY STOPPED")
            break


if __name__ == "__main__":
    main()
