# -*- coding: utf-8 -*-

import os
import time
import json
import requests
from datetime import datetime, timezone, timedelta

# ============================================================
# SIGZY AI 15M - API SAFE & ACCURATE ENGINE
# ============================================================

SYMBOLS = [
    "EUR/USD",
    "GBP/USD",
    "USD/JPY",
    "AUD/USD",
    "USD/CHF",
    "USD/CAD",
    "NZD/USD",
    "EUR/JPY"
]

INTERVAL = "15min"
OUTPUT_SIZE = 100
TIMEOUT = 10

TP_ATR = 0.50
SL_ATR = 0.50

# ระบบสำรอง API Key
API_KEYS = [
    "77aef7a76c9b45e68d72394940bc0e77",
    # หากสมัคร Key ฟรีเพิ่ม นำมาวางเพิ่มตรงนี้ได้ครับ เช่น:
    # "YOUR_SECOND_API_KEY",
]
current_key_index = 0

HISTORY_FILE = "trade_history.json"

RAW_WEBHOOK = os.getenv(
    "DISCORD_WEBHOOK_URL",
    "https://discord.com/api/webhooks/1537208534058405918/555sHE5Z09zHOD8xtv7Q-fBj5NP4bUE4nkeIFz6ugqsWxEIVmEi2PX0Wxx36ZCLXlKpR"
)
DISCORD_WEBHOOK_URL = RAW_WEBHOOK.strip()
if DISCORD_WEBHOOK_URL.startswith("Https://"):
    DISCORD_WEBHOOK_URL = "https://" + DISCORD_WEBHOOK_URL[8:]

STATS_WEBHOOK_URL = os.getenv("STATS_WEBHOOK_URL", "")

SENT_SIGNALS = set()
PROCESSED_KEYS = set()
PENDING_TRADES = []

TRADE_HISTORY = []
CURRENT_SERIES_TRADES = []
SERIES_HISTORY = []


def load_history():
    global TRADE_HISTORY, SERIES_HISTORY
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                TRADE_HISTORY = data.get("trade_history", [])
                SERIES_HISTORY = data.get("series_history", [])
        except Exception:
            pass


def save_history():
    try:
        data = {
            "trade_history": TRADE_HISTORY,
            "series_history": SERIES_HISTORY
        }
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def now_text():
    utc_now = datetime.now(timezone.utc)
    thai = utc_now + timedelta(hours=7)
    return thai.strftime("%Y-%m-%d %H:%M:%S")


def format_candle_time(utc_time_str):
    try:
        dt = datetime.strptime(utc_time_str, "%Y-%m-%d %H:%M:%S")
        thai_dt = dt + timedelta(hours=7)
        return thai_dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return utc_time_str


def send_discord(message):
    if not DISCORD_WEBHOOK_URL:
        return
    try:
        requests.post(
            DISCORD_WEBHOOK_URL,
            json={"content": message},
            timeout=5
        )
    except Exception as e:
        print("Discord Error:", e)


def get_time_session(thai_hour):
    if 0 <= thai_hour < 4:
        return "00-04"
    elif 4 <= thai_hour < 5:
        return "04-05"
    elif 5 <= thai_hour < 6:
        return "05-06"
    elif 6 <= thai_hour < 12:
        return "06-12"
    elif 12 <= thai_hour < 18:
        return "12-18"
    else:
        return "18-24"


def get_market_data(symbol):
    global current_key_index

    api_key = API_KEYS[current_key_index]
    url = f"https://api.twelvedata.com/time_series?symbol={symbol}&interval={INTERVAL}&outputsize={OUTPUT_SIZE}&timezone=UTC&apikey={api_key}"

    try:
        response = requests.get(url, timeout=TIMEOUT)
        data = response.json()

        if data.get("status") == "error":
            print(f"{symbol} API Limit/Error: {data.get('message')}")
            # สลับไปใช้ Key ถัดไปหากมี
            if len(API_KEYS) > 1:
                current_key_index = (current_key_index + 1) % len(API_KEYS)
            return []

        values = data.get("values", [])
        if not values:
            return []

        candles = []
        for item in reversed(values):
            try:
                candles.append({
                    "datetime": item["datetime"],
                    "open": float(item["open"]),
                    "high": float(item["high"]),
                    "low": float(item["low"]),
                    "close": float(item["close"])
                })
            except Exception:
                continue
        return candles

    except Exception:
        return []


def atr(candles, period=14):
    if len(candles) < period + 1:
        return None
    trs = []
    for i in range(1, len(candles)):
        c = candles[i]
        p = candles[i - 1]
        tr = max(
            c["high"] - c["low"],
            abs(c["high"] - p["close"]),
            abs(c["low"] - p["close"])
        )
        trs.append(tr)
    return sum(trs[-period:]) / period


def calculate_ema(candles, period=50):
    if len(candles) < period:
        return None
    closes = [c["close"] for c in candles]
    multiplier = 2 / (period + 1)
    ema = sum(closes[:period]) / period
    for price in closes[period:]:
        ema = (price - ema) * multiplier + ema
    return ema


def body(c):
    return abs(c["close"] - c["open"])


def candle_range(c):
    return max(c["high"] - c["low"], 0.00000001)


def upper_wick(c):
    return c["high"] - max(c["open"], c["close"])


def lower_wick(c):
    return min(c["open"], c["close"]) - c["low"]


def bullish(c):
    return c["close"] > c["open"]


def bearish(c):
    return c["close"] < c["open"]


def market_regime(candles):
    current_atr = atr(candles, 14)
    if not current_atr:
        return "NORMAL"

    ranges = [c["high"] - c["low"] for c in candles[-20:]]
    avg_range = sum(ranges) / len(ranges)

    if avg_range < current_atr * 0.75:
        return "QUIET"
    elif avg_range > current_atr * 1.40:
        return "FAST"
    return "NORMAL"


def get_threshold(regime, thai_hour):
    base_threshold = 70

    if regime == "QUIET":
        base_threshold += 5
    elif regime == "FAST":
        base_threshold += 8

    if 6 <= thai_hour < 12:
        base_threshold += 8
    elif 4 <= thai_hour < 6:
        base_threshold += 5

    return base_threshold


def analyze_15m_opportunity(symbol, candles):
    if len(candles) < 50:
        return {"decision": "WAIT", "score": 0}

    c0 = candles[-1]
    c1 = candles[-2]
    price = c0["close"]
    current_atr = atr(candles, 14)
    ema50 = calculate_ema(candles, 50)

    if not current_atr or not ema50:
        return {"decision": "WAIT", "score": 0}

    thai_now = datetime.now(timezone.utc) + timedelta(hours=7)
    thai_hour = thai_now.hour

    b0 = body(c0)
    r0 = candle_range(c0)
    ratio0 = b0 / r0 if r0 > 0 else 0

    if ratio0 < 0.45:
        return {"decision": "WAIT", "score": 0}

    reasons = []
    confirmations_call = 0
    confirmations_put = 0

    above_ema = price > ema50
    below_ema = price < ema50

    if above_ema:
        confirmations_call += 1
        reasons.append("Above EMA 50")
    elif below_ema:
        confirmations_put += 1
        reasons.append("Below EMA 50")

    upper0 = upper_wick(c0)
    lower0 = lower_wick(c0)

    is_strong_bull = bullish(c0) and ratio0 >= 0.70
    is_strong_bear = bearish(c0) and ratio0 >= 0.70
    is_hammer = lower0 >= b0 * 2.5 and upper0 <= r0 * 0.20 and ratio0 <= 0.40
    is_shooting_star = upper0 >= b0 * 2.5 and lower0 <= r0 * 0.20 and ratio0 <= 0.40
    
    is_bull_engulfing = (bullish(c0) and bearish(c1) and c0["open"] <= c1["close"] and c0["close"] >= c1["open"] and body(c0) > body(c1))
    is_bear_engulfing = (bearish(c0) and bullish(c1) and c0["open"] >= c1["close"] and c0["close"] <= c1["open"] and body(c0) > body(c1))

    if is_strong_bull or is_hammer or is_bull_engulfing:
        confirmations_call += 1
        if is_bull_engulfing:
            reasons.append("Bullish Engulfing")
        elif is_hammer:
            reasons.append("Bullish Hammer")
        else:
            reasons.append("Strong Bullish Candle")

    if is_strong_bear or is_shooting_star or is_bear_engulfing:
        confirmations_put += 1
        if is_bear_engulfing:
            reasons.append("Bearish Engulfing")
        elif is_shooting_star:
            reasons.append("Shooting Star")
        else:
            reasons.append("Strong Bearish Candle")

    history_zone = candles[-100:] if len(candles) >= 100 else candles[-50:]
    support_level = min(c["low"] for c in history_zone)
    resistance_level = max(c["high"] for c in history_zone)

    at_support = abs(price - support_level) <= (support_level * 0.0008)
    at_resistance = abs(price - resistance_level) <= (resistance_level * 0.0008)

    if at_support and above_ema:
        confirmations_call += 1
        reasons.append("Support Confluence")

    if at_resistance and below_ema:
        confirmations_put += 1
        reasons.append("Resistance Confluence")

    recent_3 = candles[-3:]
    bull_momentum = sum(1 for c in recent_3 if bullish(c)) >= 2
    bear_momentum = sum(1 for c in recent_3 if bearish(c)) >= 2

    if bull_momentum:
        confirmations_call += 1
        reasons.append("Bullish Momentum")

    if bear_momentum:
        confirmations_put += 1
        reasons.append("Bearish Momentum")

    regime = market_regime(candles)
    threshold = get_threshold(regime, thai_hour)

    if confirmations_call > confirmations_put and confirmations_call >= 3 and above_ema:
        direction = "CALL"
        score = 50 + (confirmations_call * 12)
    elif confirmations_put > confirmations_call and confirmations_put >= 3 and below_ema:
        direction = "PUT"
        score = 50 + (confirmations_put * 12)
    else:
        return {"decision": "WAIT", "score": 0}

    score = min(score, 99)

    if score < threshold:
        return {"decision": "WATCH", "score": score, "symbol": symbol, "reasons": f"Score {score} < Threshold {threshold}"}

    if direction == "CALL":
        tp = price + current_atr * TP_ATR
        sl = price - current_atr * SL_ATR
    else:
        tp = price - current_atr * TP_ATR
        sl = price + current_atr * SL_ATR

    return {
        "decision": direction,
        "score": score,
        "symbol": symbol,
        "price": price,
        "atr": current_atr,
        "tp": tp,
        "sl": sl,
        "regime": regime,
        "threshold": threshold,
        "reasons": " | ".join(reasons),
        "candle_time": c0["datetime"],
        "session": get_time_session(thai_hour)
    }


def verify_pending_trades():
    global PENDING_TRADES, PROCESSED_KEYS, TRADE_HISTORY, CURRENT_SERIES_TRADES, SERIES_HISTORY

    if not PENDING_TRADES:
        return

    remaining_trades = []

    for trade in PENDING_TRADES:
        trade_key = (trade["symbol"], trade["candle_time"], trade["decision"])
        
        if trade_key in PROCESSED_KEYS:
            continue

        candles = get_market_data(trade["symbol"])
        if not candles:
            remaining_trades.append(trade)
            continue

        target_candle = None
        for c in candles:
            if c["datetime"] > trade["candle_time"]:
                target_candle = c
                break

        if target_candle:
            PROCESSED_KEYS.add(trade_key)
            direction = trade["decision"]
            entry_price = trade["price"]
            tp_price = trade["tp"]
            sl_price = trade["sl"]

            candle_high = target_candle["high"]
            candle_low = target_candle["low"]
            close_price = target_candle["close"]

            exit_reason = "Close Price"

            if direction == "CALL":
                if candle_high >= tp_price:
                    result = "WIN 🟢"
                    exit_reason = "TP Hit (High)"
                elif candle_low <= sl_price:
                    result = "LOSS 🔴"
                    exit_reason = "SL Hit (Low)"
                else:
                    result = "WIN 🟢" if close_price > entry_price else "LOSS 🔴"
            else:
                if candle_low <= tp_price:
                    result = "WIN 🟢"
                    exit_reason = "TP Hit (Low)"
                elif candle_high >= sl_price:
                    result = "LOSS 🔴"
                    exit_reason = "SL Hit (High)"
                else:
                    result = "WIN 🟢" if close_price < entry_price else "LOSS 🔴"

            trade_record = {
                "symbol": trade["symbol"],
                "decision": direction,
                "score": trade["score"],
                "entry_price": entry_price,
                "close_price": close_price,
                "tp": tp_price,
                "sl": sl_price,
                "result": result,
                "exit_reason": exit_reason,
                "session": trade.get("session", "N/A"),
                "timestamp": now_text()
            }

            TRADE_HISTORY.append(trade_record)
            CURRENT_SERIES_TRADES.append(trade_record)
            trade_index = len(CURRENT_SERIES_TRADES)

            total_trades = len(TRADE_HISTORY)
            wins_trades = sum(1 for t in TRADE_HISTORY if "WIN" in t["result"])
            trade_wr = (wins_trades / total_trades) * 100 if total_trades > 0 else 0

            has_win = any("WIN" in t["result"] for t in CURRENT_SERIES_TRADES)
            is_series_complete = False

            if has_win:
                is_series_complete = True
                series_status = "SERIES WIN 🟢 (ชนะ 1 ใน 3)"
            elif trade_index >= 3:
                is_series_complete = True
                series_status = "SERIES LOSS 🔴 (แพ้ติดกัน 3 ไม้)"
            else:
                series_status = f"IN PROGRESS ⏳ (ไม้ที่ {trade_index}/3)"

            if is_series_complete:
                series_record = {
                    "result": "SERIES WIN 🟢" if has_win else "SERIES LOSS 🔴",
                    "trades": list(CURRENT_SERIES_TRADES),
                    "session": trade_record["session"],
                    "timestamp": now_text()
                }
                SERIES_HISTORY.append(series_record)
                CURRENT_SERIES_TRADES = []

            total_series = len(SERIES_HISTORY)
            wins_series = sum(1 for sr in SERIES_HISTORY if sr["result"] == "SERIES WIN 🟢")
            series_wr = (wins_series / total_series) * 100 if total_series > 0 else 0

            save_history()

            msg = (
                f"📊 **SIGZY EVALUATION REPORT**\n\n"
                f"💱 คู่เงิน: **{trade_record['symbol']}** ({trade_record['decision']})\n"
                f"📍 ไม้ที่: **{trade_index}/3** | Session: **{trade_record['session']} น.**\n"
                f"🎯 Entry: **{trade_record['entry_price']:.5f}** -> Close: **{trade_record['close_price']:.5f}**\n"
                f"🏁 ผลออเดอร์: **{trade_record['result']}** ({trade_record['exit_reason']})\n\n"
                f"🏆 **สถานะ Series:** **{series_status}**\n"
                f"----------------------------------------\n"
                f"🎯 **SINGLE TRADE WR:** {wins_trades}/{total_trades} ไม้ (**{trade_wr:.2f}%**)\n"
                f"🏆 **SERIES (1 ใน 3) WR:** {wins_series}/{total_series} รอบ (**{series_wr:.2f}%**)\n"
                f"🕐 {now_text()}"
            )
            send_discord(msg)

            if STATS_WEBHOOK_URL and is_series_complete:
                try:
                    requests.post(STATS_WEBHOOK_URL, json=series_record, timeout=5)
                except Exception:
                    pass
        else:
            remaining_trades.append(trade)

    PENDING_TRADES = remaining_trades


def scan_all_symbols():
    signals = []
    watchlist = []

    print(f"[{now_text()}] 🔍 สแกน 8 คู่เงิน...")

    for symbol in SYMBOLS:
        try:
            candles = get_market_data(symbol)
            if not candles:
                continue

            res = analyze_15m_opportunity(symbol, candles)
            if res["decision"] == "WATCH":
                watchlist.append(res)
            elif res["decision"] != "WAIT":
                signals.append(res)
        except Exception as e:
            print(f"{symbol}: ERROR {e}")

    return signals, watchlist


def send_update(signals, watchlist):
    global SENT_SIGNALS, PENDING_TRADES

    if signals:
        signals.sort(key=lambda x: x["score"], reverse=True)

        for sig in signals:
            signal_key = (sig["symbol"], sig["candle_time"], sig["decision"])
            if signal_key not in SENT_SIGNALS:
                SENT_SIGNALS.add(signal_key)
                PENDING_TRADES.append(sig)

                thai_candle_time = format_candle_time(sig['candle_time'])

                msg = (
                    f"🎯 **SIGZY AI 15M**\n\n"
                    f"💱 คู่เงิน: **{sig['symbol']}**\n"
                    f"📌 ทิศทาง: **{sig['decision']}**\n"
                    f"🏆 Score: **{sig['score']}/100** (Threshold: {sig['threshold']})\n"
                    f"🌡️ Market: **{sig['regime']}** | Session: **{sig['session']} น.**\n"
                    f"💰 Entry: **{sig['price']:.5f}**\n"
                    f"🎯 TP: **{sig['tp']:.5f}**\n"
                    f"🛑 SL: **{sig['sl']:.5f}**\n\n"
                    f"🔎 ปัจจัยสนับสนุน: {sig['reasons']}\n\n"
                    f"⏱️ Candle: {thai_candle_time}\n"
                    f"🕐 {now_text()}"
                )
                send_discord(msg)


def main():
    print("SIGZY AI 15M - API SAFE ENGINE STARTED")
    load_history()
    send_discord("🤖 **SIGZY ONLINE**\nปรับรอบการสแกนเพื่อประหยัด API + พร้อมระบบตรวจผล TP/SL เรียบร้อย!")

    last_scan_time = 0

    while True:
        try:
            now_ts = time.time()

            # วนสแกนและตรวจผลทุก 3 นาที (180 วินาที) เพื่อประหยัด API
            if now_ts - last_scan_time >= 180:
                verify_pending_trades()
                signals, watchlist = scan_all_symbols()
                send_update(signals, watchlist)
                last_scan_time = now_ts

        except Exception as e:
            print(f"MAIN ERROR: {e}")

        time.sleep(30)


if __name__ == "__main__":
    main()
