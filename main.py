# -*- coding: utf-8 -*-

import os
import time
import requests
from datetime import datetime, timezone, timedelta
import yfinance as yf

# ============================================================
# SIGZY AI 15M - YFINANCE + DISCORD NOTIFICATION
# ============================================================

# ลิงก์ Discord Webhook
RAW_WEBHOOK = os.getenv(
    "DISCORD_WEBHOOK_URL",
    "https://discord.com/api/webhooks/1537208534058405918/555sHE5Z09zHOD8xtv7Q-fBj5NP4bUE4nkeIFz6ugqsWxEIVmEi2PX0Wxx36ZCLXlKpR"
)
DISCORD_WEBHOOK_URL = RAW_WEBHOOK.strip()
if DISCORD_WEBHOOK_URL.startswith("Https://"):
    DISCORD_WEBHOOK_URL = "https://" + DISCORD_WEBHOOK_URL[8:]

# รายชื่อคู่เงิน
SYMBOL_MAP = {
    "EUR/USD": "EURUSD=X",
    "GBP/USD": "GBPUSD=X",
    "USD/JPY": "JPY=X",
    "AUD/USD": "AUDUSD=X",
    "USD/CHF": "CHF=X",
    "USD/CAD": "CAD=X",
    "NZD/USD": "NZDUSD=X",
    "EUR/JPY": "EURJPY=X"
}

SYMBOLS = list(SYMBOL_MAP.keys())
INTERVAL = "15m"
TP_ATR = 0.50
SL_ATR = 0.50

SENT_SIGNALS = set()
PENDING_TRADES = []
TRADE_HISTORY = []


def now_text():
    utc_now = datetime.now(timezone.utc)
    thai = utc_now + timedelta(hours=7)
    return thai.strftime("%Y-%m-%d %H:%M:%S")


def send_discord(message):
    """ฟังก์ชันสำหรับส่งข้อความแจ้งเตือนเข้า Discord"""
    if not DISCORD_WEBHOOK_URL:
        return
    try:
        payload = {"content": message}
        response = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=5)
        if response.status_code == 204:
            print("  [Discord] ส่งการแจ้งเตือนสำเร็จ!")
        else:
            print(f"  [Discord Error] Status Code: {response.status_code}")
    except Exception as e:
        print(f"  [Discord Exception] {e}")


def get_market_data(symbol):
    yf_symbol = SYMBOL_MAP.get(symbol, symbol)
    try:
        ticker = yf.Ticker(yf_symbol)
        df = ticker.history(period="2d", interval=INTERVAL)

        if df.empty or len(df) < 50:
            return []

        candles = []
        for idx, row in df.iterrows():
            candles.append({
                "datetime": idx.strftime("%Y-%m-%d %H:%M:%S"),
                "open": float(row["Open"]),
                "high": float(row["High"]),
                "low": float(row["Low"]),
                "close": float(row["Close"])
            })
        return candles
    except Exception as e:
        print(f"[{symbol}] yfinance Error: {e}")
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

    above_ema = price > ema50
    below_ema = price < ema50

    reasons = []
    confirmations_call = 0
    confirmations_put = 0

    if above_ema:
        confirmations_call += 1
        reasons.append("ราคาอยู่เหนือ EMA 50")
    elif below_ema:
        confirmations_put += 1
        reasons.append("ราคาอยู่ใต้ EMA 50")

    b0 = body(c0)
    r0 = candle_range(c0)
    upper0 = upper_wick(c0)
    lower0 = lower_wick(c0)
    ratio0 = b0 / r0 if r0 > 0 else 0

    is_strong_bull = bullish(c0) and ratio0 >= 0.70
    is_strong_bear = bearish(c0) and ratio0 >= 0.70
    is_hammer = lower0 >= b0 * 2.5 and upper0 <= r0 * 0.20 and ratio0 <= 0.40
    is_shooting_star = upper0 >= b0 * 2.5 and lower0 <= r0 * 0.20 and ratio0 <= 0.40

    is_bull_engulfing = (
        bullish(c0) and 
        bearish(c1) and 
        c0["open"] <= c1["close"] and 
        c0["close"] >= c1["open"] and 
        body(c0) > body(c1)
    )

    is_bear_engulfing = (
        bearish(c0) and 
        bullish(c1) and 
        c0["open"] >= c1["close"] and 
        c0["close"] <= c1["open"] and 
        body(c0) > body(c1)
    )

    if is_strong_bull or is_hammer or is_bull_engulfing:
        confirmations_call += 1
        if is_bull_engulfing:
            reasons.append("Bullish Engulfing")
        elif is_hammer:
            reasons.append("Hammer PinBar")
        else:
            reasons.append("Strong Bull Candle")

    if is_strong_bear or is_shooting_star or is_bear_engulfing:
        confirmations_put += 1
        if is_bear_engulfing:
            reasons.append("Bearish Engulfing")
        elif is_shooting_star:
            reasons.append("Shooting Star")
        else:
            reasons.append("Strong Bear Candle")

    history_zone = candles[-100:]
    support_level = min(c["low"] for c in history_zone)
    resistance_level = max(c["high"] for c in history_zone)

    if abs(price - support_level) <= (support_level * 0.0008) and above_ema:
        confirmations_call += 1
        reasons.append("แนวรับ Support Zone")

    if abs(price - resistance_level) <= (resistance_level * 0.0008) and below_ema:
        confirmations_put += 1
        reasons.append("แนวต้าน Resistance Zone")

    recent_3 = candles[-3:]
    if sum(1 for c in recent_3 if bullish(c)) >= 2:
        confirmations_call += 1
        reasons.append("แรงซื้อ Bullish Momentum")

    if sum(1 for c in recent_3 if bearish(c)) >= 2:
        confirmations_put += 1
        reasons.append("แรงขาย Bearish Momentum")

    threshold = 70

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
        "reasons": " | ".join(reasons),
        "candle_time": c0["datetime"]
    }


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

        if latest_candle["datetime"] != trade["candle_time"]:
            direction = trade["decision"]
            entry_price = trade["price"]
            tp_price = trade["tp"]
            sl_price = trade["sl"]

            candle_high = latest_candle["high"]
            candle_low = latest_candle["low"]
            close_price = latest_candle["close"]

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

            record = {
                "symbol": trade["symbol"],
                "decision": direction,
                "score": trade["score"],
                "entry_price": entry_price,
                "close_price": close_price,
                "result": result,
                "exit_reason": exit_reason,
                "timestamp": now_text()
            }

            TRADE_HISTORY.append(record)

            total_trades = len(TRADE_HISTORY)
            wins = sum(1 for t in TRADE_HISTORY if "WIN" in t["result"])
            win_rate = (wins / total_trades) * 100 if total_trades > 0 else 0

            # ข้อความสรุปผลไม้เดิมส่งเข้า Discord
            msg = (
                f"📊 **[RESULT UPDATE] ผลสรุปไม้ออเดอร์**\n\n"
                f"💱 คู่เงิน: **{record['symbol']}** ({record['decision']})\n"
                f"🎯 Entry: **{record['entry_price']:.5f}** ➡️ Close: **{record['close_price']:.5f}**\n"
                f"🏁 ผลลัพธ์: **{record['result']}** ({exit_reason})\n\n"
                f"📈 **TOTAL WIN RATE:** {wins}/{total_trades} ไม้ (**{win_rate:.2f}%**)\n"
                f"🕐 {now_text()}"
            )
            send_discord(msg)
            print(f"\n[RESULT] {record['symbol']} -> {record['result']} ({exit_reason})")
        else:
            remaining_trades.append(trade)

    PENDING_TRADES = remaining_trades


def scan_all_symbols():
    signals = []
    watchlist = []

    print(f"\n[{now_text()}] 🔍 สแกน 8 คู่เงิน...")

    for symbol in SYMBOLS:
        try:
            candles = get_market_data(symbol)
            if not candles:
                continue

            res = analyze_15m_opportunity(symbol, candles)
            if res["decision"] == "WATCH":
                watchlist.append(res)
                print(f"  • {symbol}: WATCH (Score: {res['score']})")
            elif res["decision"] != "WAIT":
                signals.append(res)
                print(f"  • {symbol}: 🎯 {res['decision']} | Score: {res['score']}")
            else:
                print(f"  • {symbol}: WAIT")
        except Exception as e:
            print(f"  • {symbol}: ERROR {e}")

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

                # ข้อความแจ้งเตือนสัญญาณใหม่เข้า Discord
                icon = "🟢" if sig['decision'] == "CALL" else "🔴"
                msg = (
                    f"🚨 **[NEW SIGNAL] สัญญาณเข้าเทรดใหม่** {icon}\n\n"
                    f"💱 คู่เงิน: **{sig['symbol']}**\n"
                    f"📌 ทิศทาง: **{sig['decision']}**\n"
                    f"🏆 คะแนนความมั่นใจ: **{sig['score']}/100**\n"
                    f"💰 ราคาเข้า (Entry): **{sig['price']:.5f}**\n"
                    f"🎯 เป้าหมาย (TP): **{sig['tp']:.5f}** | 🛑 ตัดขาดทุน (SL): **{sig['sl']:.5f}**\n\n"
                    f"🔎 เหตุผลการวิเคราะห์: {sig['reasons']}\n"
                    f"🕐 เวลา: {now_text()}"
                )
                send_discord(msg)
                print(f"\n🚨 ส่งสัญญาณ {sig['symbol']} เข้า Discord เรียบร้อย!")


def main():
    print("SIGZY AI 15M - DISCORD NOTIFIER RUNNING")
    
    # ส่งข้อความทดสอบสถานะระบบเมื่อเริ่มรัน
    send_discord("🤖 **SIGZY BOT ONLINE**\nบอทสแกนสัญญาณเทรดและแจ้งเตือนผ่าน Discord เริ่มทำงานแล้วครับ!")

    while True:
        try:
            verify_pending_trades()
            signals, watchlist = scan_all_symbols()
            send_update(signals, watchlist)
        except Exception as e:
            print(f"MAIN ERROR: {e}")

        print("\n⏳ พักสแกน 3 นาที...")
        time.sleep(180)


if __name__ == "__main__":
    main()
