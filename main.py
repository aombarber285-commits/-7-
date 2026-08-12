# -*- coding: utf-8 -*-

import os
import time
import requests
from datetime import datetime, timezone, timedelta

# ============================================================
# SIGZY AI 15M - FIXED AUTO-TRACKER & DYNAMIC THRESHOLD
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
TIMEOUT = 15

TP_ATR = 0.50
SL_ATR = 0.50

API_KEY_CORRECT = "77aef7a76c9b45e68d72394940bc0e77"

RAW_WEBHOOK = os.getenv(
    "DISCORD_WEBHOOK_URL",
    "https://discord.com/api/webhooks/1537208534058405918/555sHE5Z09zHOD8xtv7Q-fBj5NP4bUE4nkeIFz6ugqsWxEIVmEi2PX0Wxx36ZCLXlKpR"
)
DISCORD_WEBHOOK_URL = RAW_WEBHOOK.strip()
if DISCORD_WEBHOOK_URL.startswith("Https://"):
    DISCORD_WEBHOOK_URL = "https://" + DISCORD_WEBHOOK_URL[8:]

STATS_WEBHOOK_URL = os.getenv("STATS_WEBHOOK_URL", "")

SENT_SIGNALS = set()
PENDING_TRADES = []
TRADE_HISTORY = []


def now_text():
    utc_now = datetime.now(timezone.utc)
    thai = utc_now + timedelta(hours=7)
    return thai.strftime("%Y-%m-%d %H:%M:%S")


def format_candle_time(utc_time_str):
    """แปลงเวลา Candle จาก UTC ให้เป็นเวลาไทย (GMT+7)"""
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


def is_trading_time():
    return True


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
    url = f"https://api.twelvedata.com/time_series?symbol={symbol}&interval={INTERVAL}&outputsize={OUTPUT_SIZE}&timezone=UTC&apikey={API_KEY_CORRECT}"

    try:
        response = requests.get(url, timeout=TIMEOUT)
        data = response.json()

        if data.get("status") == "error":
            print(f"{symbol} API Error: {data.get('message')}")
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

    except Exception as e:
        print(f"{symbol} Market Error: {e}")
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
    if regime == "QUIET":
        base_threshold = 60
    elif regime == "FAST":
        base_threshold = 70
    else:
        base_threshold = 65

    if 4 <= thai_hour < 5:
        base_threshold += 5
    elif 5 <= thai_hour < 6:
        base_threshold += 10

    return base_threshold


def analyze_15m_opportunity(symbol, candles):
    if len(candles) < 50:
        return {"decision": "WAIT", "score": 0}

    c0 = candles[-1]
    c1 = candles[-2]
    price = c0["close"]
    current_atr = atr(candles, 14)
    if not current_atr:
        return {"decision": "WAIT", "score": 0}

    thai_now = datetime.now(timezone.utc) + timedelta(hours=7)
    thai_hour = thai_now.hour

    call_score = 0
    put_score = 0
    reasons = []
    confirmations_call = 0
    confirmations_put = 0

    b0 = body(c0)
    r0 = candle_range(c0)
    upper0 = upper_wick(c0)
    lower0 = lower_wick(c0)
    ratio0 = b0 / r0 if r0 > 0 else 0

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
            reasons.append("Bullish Pin Bar / Hammer")
        else:
            reasons.append("Strong Bullish Candle")

    if is_strong_bear or is_shooting_star or is_bear_engulfing:
        confirmations_put += 1
        if is_bear_engulfing:
            reasons.append("Bearish Engulfing")
        elif is_shooting_star:
            reasons.append("Bearish Shooting Star")
        else:
            reasons.append("Strong Bearish Candle")

    history_zone = candles[-100:] if len(candles) >= 100 else candles[-50:]
    support_level = min(c["low"] for c in history_zone)
    resistance_level = max(c["high"] for c in history_zone)

    at_support = abs(price - support_level) <= (support_level * 0.0008)
    at_resistance = abs(price - resistance_level) <= (resistance_level * 0.0008)

    if at_support:
        confirmations_call += 1
        reasons.append("At Support Zone")
        if is_hammer:
            call_score += 10
            reasons.append("S/R Rejection Bonus")

    if at_resistance:
        confirmations_put += 1
        reasons.append("At Resistance Zone")
        if is_shooting_star:
            put_score += 10
            reasons.append("S/R Rejection Bonus")

    recent_3 = candles[-3:]
    bull_momentum = sum(1 for c in recent_3 if bullish(c)) >= 2
    bear_momentum = sum(1 for c in recent_3 if bearish(c)) >= 2

    if bull_momentum:
        confirmations_call += 1
        reasons.append("Bullish Momentum")
        if is_strong_bull:
            call_score += 10
            reasons.append("Candle+Momentum Alignment")

    if bear_momentum:
        confirmations_put += 1
        reasons.append("Bearish Momentum")
        if is_strong_bear:
            put_score += 10
            reasons.append("Candle+Momentum Alignment")

    prev_5_high = max(c["high"] for c in candles[-6:-1])
    prev_5_low = min(c["low"] for c in candles[-6:-1])

    if price > prev_5_high and bullish(c0):
        confirmations_call += 1
        reasons.append("Breakout UP")
    elif price < prev_5_low and bearish(c0):
        confirmations_put += 1
        reasons.append("Breakout DOWN")

    regime = market_regime(candles)
    threshold = get_threshold(regime, thai_hour)

    if confirmations_call >= confirmations_put and confirmations_call >= 2:
        direction = "CALL"
        call_score += 50 + (confirmations_call * 12)
        score = call_score
    elif confirmations_put > confirmations_call and confirmations_put >= 2:
        direction = "PUT"
        put_score += 50 + (confirmations_put * 12)
        score = put_score
    else:
        if 55 <= max(call_score, put_score) < threshold:
            return {"decision": "WATCH", "score": max(call_score, put_score), "symbol": symbol, "reasons": "ใกล้เข้าเงื่อนไข"}
        return {"decision": "WAIT", "score": 0}

    score = min(score, 99)

    if score < threshold:
        return {"decision": "WATCH", "score": score, "symbol": symbol, "reasons": f"Score {score} ไม่ถึง Threshold {threshold}"}

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
        "candle_time": c0["datetime"], # เก็บเป็น UTC เพื่อเปรียบเทียบในระบบ
        "session": get_time_session(thai_hour)
    }


def calculate_session_stats():
    sessions = ["00-04", "04-05", "05-06", "06-12", "12-18", "18-24"]
    stats_text = ""

    for s in sessions:
        trades = [t for t in TRADE_HISTORY if t.get("session") == s]
        total = len(trades)
        if total > 0:
            wins = sum(1 for t in trades if "WIN" in t["result"])
            wr = (wins / total) * 100
            stats_text += f"• **{s} น.**: {wins}/{total} ไม้ ({wr:.1f}%)\n"
        else:
            stats_text += f"• **{s} น.**: - (ยังไม่มีข้อมูล)\n"

    return stats_text


def verify_pending_trades():
    global PENDING_TRADES, TRADE_HISTORY

    if not PENDING_TRADES:
        return

    remaining_trades = []

    for trade in PENDING_TRADES:
        candles = get_market_data(trade["symbol"])
        if not candles:
            remaining_trades.append(trade)
            continue

        latest_candle = candles[-1]
        
        # แก้ไขการเปรียบเทียบ: ใช้เวลา UTC เทียบกับ UTC ตรงๆ
        if latest_candle["datetime"] != trade["candle_time"]:
            close_price = latest_candle["close"]
            direction = trade["decision"]
            entry_price = trade["price"]

            if direction == "CALL":
                result = "WIN 🟢" if close_price > entry_price else "LOSS 🔴"
            else:
                result = "WIN 🟢" if close_price < entry_price else "LOSS 🔴"

            record = {
                "symbol": trade["symbol"],
                "decision": direction,
                "score": trade["score"],
                "entry_price": entry_price,
                "close_price": close_price,
                "result": result,
                "reasons": trade["reasons"],
                "candle_time": trade["candle_time"],
                "session": trade.get("session", "N/A"),
                "timestamp": now_text()
            }

            TRADE_HISTORY.append(record)

            total_trades = len(TRADE_HISTORY)
            wins = sum(1 for t in TRADE_HISTORY if "WIN" in t["result"])
            win_rate = (wins / total_trades) * 100 if total_trades > 0 else 0

            session_stats = calculate_session_stats()

            msg = (
                f"📊 **RESULT UPDATE (AUTO-TRACKER)**\n\n"
                f"💱 คู่เงิน: **{record['symbol']}** ({record['decision']})\n"
                f"🏆 Score: **{record['score']}/100** | Session: **{record['session']} น.**\n"
                f"📍 Entry: **{record['entry_price']:.5f}** $\rightarrow$ Close: **{record['close_price']:.5f}**\n"
                f"🏁 ผลลัพธ์: **{record['result']}**\n\n"
                f"📈 **WIN RATE BY SESSION**\n"
                f"{session_stats}\n"
                f"📊 **OVERALL:** {wins}/{total_trades} ไม้ (**{win_rate:.2f}%**)\n"
                f"🕐 {now_text()}"
            )
            send_discord(msg)

            if STATS_WEBHOOK_URL:
                try:
                    requests.post(STATS_WEBHOOK_URL, json=record, timeout=5)
                except Exception as e:
                    print("Stats Export Error:", e)
        else:
            remaining_trades.append(trade)

    PENDING_TRADES = remaining_trades


def scan_all_symbols():
    signals = []
    watchlist = []

    print()
    print("=" * 65)
    print(f"[{now_text()}] 🔍 สแกน 8 คู่เงิน...")
    print("=" * 65)

    for symbol in SYMBOLS:
        try:
            candles = get_market_data(symbol)
            if not candles:
                continue

            res = analyze_15m_opportunity(symbol, candles)
            if res["decision"] == "WATCH":
                watchlist.append(res)
                print(f"{symbol}: WATCH (Score: {res['score']})")
            elif res["decision"] != "WAIT":
                signals.append(res)
                print(f"{symbol}: 🎯 {res['decision']} | Score: {res['score']}")
            else:
                print(f"{symbol}: WAIT")
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
                    f"🎯 **SIGZY AI 15M (OPTIMIZED)**\n\n"
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
                print(f"🚨 ส่งสัญญาณ {sig['symbol']} เข้า Discord เรียบร้อย!")

    if watchlist:
        watch_msg = "👀 **Early Alert (จับตาดู):**\n"
        for w in watchlist[:3]:
            watch_msg += f"- {w['symbol']} (Score: {w['score']})\n"
        send_discord(watch_msg)


def wait_next_15m():
    now = datetime.now(timezone.utc)
    minute = now.minute
    next_block = ((minute // 15) + 1) * 15

    if next_block >= 60:
        target = (now + timedelta(hours=1)).replace(minute=0, second=5, microsecond=0)
    else:
        target = now.replace(minute=next_block, second=5, microsecond=0)

    total_seconds = int((target - now).total_seconds())
    if total_seconds <= 0:
        return

    print(f"\n⏳ รอแท่ง 15M ถัดไป (ประมาณ {total_seconds} วินาที)...")
    time.sleep(total_seconds)


def main():
    print()
    print("=" * 65)
    print("SIGZY AI 15M - FIXED AUTO-TRACKER STARTED")
    print("=" * 65)

    send_discord("🤖 **SIGZY ONLINE (FIXED AUTO-TRACKER)**\nแก้ไขระบบตรวจผลแพ้/ชนะเรียบร้อย บอตพร้อมตรวจผลอัตโนมัติเมื่อจบแท่ง!")

    while True:
        try:
            verify_pending_trades()
            signals, watchlist = scan_all_symbols()
            send_update(signals, watchlist)
        except Exception as e:
            print(f"MAIN ERROR: {e}")

        wait_next_15m()


if __name__ == "__main__":
    main()
