# -*- coding: utf-8 -*-

import os
import json
import time
import requests
from datetime import datetime, timezone, timedelta
import yfinance as yf

# ============================================================
# CONFIGURATIONS & GLOBAL VARIABLES
# ============================================================

RAW_WEBHOOK = os.getenv(
    "DISCORD_WEBHOOK_URL",
    "https://discord.com/api/webhooks/1537208534058405918/555sHE5Z09zHOD8xtv7Q-fBj5NP4bUE4nkeIFz6ugqsWxEIVmEi2PX0Wxx36ZCLXlKpR"
)
DISCORD_WEBHOOK_URL = RAW_WEBHOOK.strip()
if DISCORD_WEBHOOK_URL.startswith("Https://"):
    DISCORD_WEBHOOK_URL = "https://" + DISCORD_WEBHOOK_URL[8:]

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
MEMORY_FILE = "v13_memory.json"

# ตัวแปรสำหรับระบบที่ 1
SENT_SIGNALS = set()
PENDING_TRADES = []
TRADE_HISTORY = []

# ตัวแปรสำหรับระบบที่ 2 (Sigzy Tracker)
HISTORICAL_MEMORY = []
ACTIVE_TRACKERS = []


def now_text():
    utc_now = datetime.now(timezone.utc)
    thai = utc_now + timedelta(hours=7)
    return thai.strftime("%Y-%m-%d %H:%M:%S")


def log(msg):
    print(f"[{now_text()}] {msg}")


def send_discord(message):
    """ฟังก์ชันกลางสำหรับส่งข้อความแจ้งเตือนเข้า Discord"""
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


# ============================================================
# [ส่วนที่ 1] สคริปต์: บอกจุดเข้า 15 นาที (15M Signal Scanner)
# ============================================================

def get_market_data_15m(symbol):
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
        print(f"[15M {symbol}] yfinance Error: {e}")
        return []


def atr_15m(candles, period=14):
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


def calculate_ema_15m(candles, period=50):
    if len(candles) < period:
        return None
    closes = [c["close"] for c in candles]
    multiplier = 2 / (period + 1)
    ema = sum(closes[:period]) / period
    for price in closes[period:]:
        ema = (price - ema) * multiplier + ema
    return ema


def analyze_15m_opportunity(symbol, candles):
    if len(candles) < 50:
        return {"decision": "WAIT", "score": 0}

    c0 = candles[-1]
    c1 = candles[-2]
    price = c0["close"]
    current_atr = atr_15m(candles, 14)
    ema50 = calculate_ema_15m(candles, 50)

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

    b0 = abs(c0["close"] - c0["open"])
    r0 = max(c0["high"] - c0["low"], 0.00000001)
    upper0 = c0["high"] - max(c0["open"], c0["close"])
    lower0 = min(c0["open"], c0["close"]) - c0["low"]
    ratio0 = b0 / r0

    is_strong_bull = (c0["close"] > c0["open"]) and ratio0 >= 0.70
    is_strong_bear = (c0["close"] < c0["open"]) and ratio0 >= 0.70
    is_hammer = lower0 >= b0 * 2.5 and upper0 <= r0 * 0.20 and ratio0 <= 0.40
    is_shooting_star = upper0 >= b0 * 2.5 and lower0 <= r0 * 0.20 and ratio0 <= 0.40

    is_bull_engulfing = (
        (c0["close"] > c0["open"]) and 
        (c1["close"] < c1["open"]) and 
        c0["open"] <= c1["close"] and 
        c0["close"] >= c1["open"] and 
        abs(c0["close"] - c0["open"]) > abs(c1["close"] - c1["open"])
    )

    is_bear_engulfing = (
        (c0["close"] < c0["open"]) and 
        (c1["close"] > c1["open"]) and 
        c0["open"] >= c1["close"] and 
        c0["close"] <= c1["open"] and 
        abs(c0["close"] - c0["open"]) > abs(c1["close"] - c1["open"])
    )

    if is_strong_bull or is_hammer or is_bull_engulfing:
        confirmations_call += 1
        reasons.append("Bullish Pattern")

    if is_strong_bear or is_shooting_star or is_bear_engulfing:
        confirmations_put += 1
        reasons.append("Bearish Pattern")

    if confirmations_call > confirmations_put and confirmations_call >= 3 and above_ema:
        direction = "CALL"
        score = min(50 + (confirmations_call * 12), 99)
    elif confirmations_put > confirmations_call and confirmations_put >= 3 and below_ema:
        direction = "PUT"
        score = min(50 + (confirmations_put * 12), 99)
    else:
        return {"decision": "WAIT", "score": 0}

    if score < 70:
        return {"decision": "WATCH", "score": score}

    tp = price + current_atr * 0.50 if direction == "CALL" else price - current_atr * 0.50
    sl = price - current_atr * 0.50 if direction == "CALL" else price + current_atr * 0.50

    return {
        "decision": direction,
        "score": score,
        "symbol": symbol,
        "price": price,
        "atr": current_atr,
        "tp": tp,
        "sl": sl,
        "reasons": " | ".join(reasons),
        "candle_time": c0["datetime"],
        "setup_name": "15M_Strategy"
    }


def run_script_1_scanner():
    """การทำงานส่วนที่ 1: สแกนและส่งสัญญาณจุดเข้า 15 นาที"""
    global SENT_SIGNALS, PENDING_TRADES, ACTIVE_TRACKERS
    print(f"\n[{now_text()}] 🔍 [ระบบ 1] สแกนจุดเข้า 15 นาที...")

    for symbol in SYMBOLS:
        try:
            candles = get_market_data_15m(symbol)
            if not candles:
                continue

            res = analyze_15m_opportunity(symbol, candles)
            if res["decision"] in ["CALL", "PUT"]:
                signal_key = (res["symbol"], res["candle_time"], res["decision"])
                if signal_key not in SENT_SIGNALS:
                    SENT_SIGNALS.add(signal_key)
                    PENDING_TRADES.append(res)
                    
                    # ส่งเข้า Tracker (ระบบที่ 2) อัตโนมัติทันทีที่พบสัญญาณ
                    ACTIVE_TRACKERS.append({
                        "symbol": res["symbol"],
                        "decision": res["decision"],
                        "score": res["score"],
                        "setup_name": res["setup_name"],
                        "signal_time": res["candle_time"],
                        "entry_price": res["price"],
                        "atr": res["atr"],
                        "step": 1,
                        "max_mfe": 0,
                        "max_mae": 0
                    })

                    icon = "🟢" if res['decision'] == "CALL" else "🔴"
                    msg = (
                        f"🚨 **[NEW SIGNAL - 15M] สัญญาณเข้าเทรดใหม่** {icon}\n\n"
                        f"💱 คู่เงิน: **{res['symbol']}**\n"
                        f"📌 ทิศทาง: **{res['decision']}**\n"
                        f"🏆 คะแนน: **{res['score']}/100**\n"
                        f"💰 Entry: **{res['price']:.5f}**\n"
                        f"🎯 TP: **{res['tp']:.5f}** | 🛑 SL: **{res['sl']:.5f}**\n\n"
                        f"🔎 เหตุผล: {res['reasons']}\n"
                        f"🕐 เวลา: {now_text()}"
                    )
                    send_discord(msg)
                    print(f"🚨 [ระบบ 1] ส่งสัญญาณ {res['symbol']} สำเร็จ!")
        except Exception as e:
            print(f"[ระบบ 1 Error] {symbol}: {e}")


# ============================================================
# [ส่วนที่ 2] สคริปต์: Sigzy Tracker (ติดตามผลหลายไม้)
# ============================================================

def load_memory_from_file():
    global HISTORICAL_MEMORY
    if os.path.exists(MEMORY_FILE):
        try:
            with open(MEMORY_FILE, "r", encoding="utf-8") as f:
                HISTORICAL_MEMORY = json.load(f)
            log(f"📂 [ระบบ 2] โหลดประวัติสำเร็จ! ทั้งหมด {len(HISTORICAL_MEMORY)} รายการ")
        except Exception as e:
            log(f"⚠️ [ระบบ 2] โหลด Memory ล้มเหลว: {e}")
            HISTORICAL_MEMORY = []
    else:
        log("📄 [ระบบ 2] ไม่พบไฟล์ประวัติ สร้างฐานข้อมูลใหม่...")


def save_memory_to_file():
    try:
        with open(MEMORY_FILE, "w", encoding="utf-8") as f:
            json.dump(HISTORICAL_MEMORY, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log(f"⚠️ [ระบบ 2] บันทึกไฟล์ล้มเหลว: {e}")


def get_closed_candles_tracker(symbol):
    yf_symbol = SYMBOL_MAP.get(symbol, symbol)
    try:
        ticker = yf.Ticker(yf_symbol)
        df = ticker.history(period="3d", interval=INTERVAL)
        if df.empty or len(df) < 50:
            return []
        closed_df = df.iloc[:-1]
        candles = []
        for idx, row in closed_df.iterrows():
            candles.append({
                "datetime": idx.strftime("%Y-%m-%d %H:%M:%S"),
                "open": float(row["Open"]),
                "high": float(row["High"]),
                "low": float(row["Low"]),
                "close": float(row["Close"])
            })
        return candles
    except Exception as e:
        log(f"[Tracker {symbol}] Error: {e}")
        return []


def record_history(tracker, win_at_step, status, step_entry):
    record = {
        "symbol": tracker["symbol"],
        "decision": tracker["decision"],
        "score": tracker["score"],
        "setup_name": tracker["setup_name"],
        "signal_time": tracker["signal_time"],
        "status": status,
        "win_at_step": win_at_step,
        "step_entry": step_entry,
        "max_mfe": tracker.get("max_mfe", 0),
        "max_mae": tracker.get("max_mae", 0),
        "recorded_at": now_text()
    }
    HISTORICAL_MEMORY.append(record)
    save_memory_to_file()


def run_script_2_tracker():
    """การทำงานส่วนที่ 2: ติดตามผลลัพธ์แบบหลายไม้ (Sigzy Tracker)"""
    global ACTIVE_TRACKERS
    if not ACTIVE_TRACKERS:
        return

    log("📈 [ระบบ 2] กำลังประเมินผล Active Trackers...")
    remaining_trackers = []

    for tracker in ACTIVE_TRACKERS:
        candles = get_closed_candles_tracker(tracker["symbol"])
        if not candles:
            remaining_trackers.append(tracker)
            continue

        future_candles = [c for c in candles if c["datetime"] > tracker["signal_time"]]
        current_step = tracker["step"]

        if len(future_candles) >= current_step:
            target_candle = future_candles[current_step - 1]
            step_entry = tracker["entry_price"] if current_step == 1 else target_candle["open"]

            atr = tracker["atr"]
            direction = tracker["decision"]
            tp_dist, sl_dist = atr * 0.6, atr * 0.6

            if direction == "CALL":
                tp_price, sl_price = step_entry + tp_dist, step_entry - sl_dist
                mfe = target_candle["high"] - step_entry
                mae = step_entry - target_candle["low"]
                tp_hit = target_candle["high"] >= tp_price
                sl_hit = target_candle["low"] <= sl_price
                is_win = (target_candle["close"] > step_entry) if not (tp_hit and sl_hit) else (tp_hit and not sl_hit)
            else:
                tp_price, sl_price = step_entry - tp_dist, step_entry + sl_dist
                mfe = step_entry - target_candle["low"]
                mae = target_candle["high"] - step_entry
                tp_hit = target_candle["low"] <= tp_price
                sl_hit = target_candle["high"] >= sl_price
                is_win = (target_candle["close"] < step_entry) if not (tp_hit and sl_hit) else (tp_hit and not sl_hit)

            tracker["max_mfe"] = max(tracker.get("max_mfe", 0), mfe)
            tracker["max_mae"] = max(tracker.get("max_mae", 0), mae)

            if is_win:
                record_history(tracker, win_at_step=current_step, status="WIN", step_entry=step_entry)
                send_discord(
                    f"🎯 **[SIGZY TRACKER OUTCOME]**\n"
                    f"💱 คู่เงิน: **{tracker['symbol']}** ({direction})\n"
                    f"🏁 ผลลัพธ์: **WIN 🟢 (ชนะในไม้ที่ {current_step})**\n"
                    f"📍 ราคาเข้าไม้ {current_step}: **{step_entry:.5f}**\n"
                    f"📈 Max MFE: **{tracker['max_mfe']:.5f}** | 📉 Max MAE: **{tracker['max_mae']:.5f}**\n"
                    f"🕐 สัญญาณเมื่อ: {tracker['signal_time']}"
                )
            else:
                if current_step < 3:
                    tracker["step"] += 1
                    remaining_trackers.append(tracker)
                else:
                    record_history(tracker, win_at_step=0, status="FULL_LOSS", step_entry=step_entry)
                    send_discord(
                        f"🛑 **[SIGZY TRACKER OUTCOME]**\n"
                        f"💱 คู่เงิน: **{tracker['symbol']}** ({direction})\n"
                        f"🏁 ผลลัพธ์: **FULL LOSS 🔴 (แพ้ครบ 3 ไม้)**\n"
                        f"📈 Max MFE: **{tracker['max_mfe']:.5f}** | 📉 Max MAE: **{tracker['max_mae']:.5f}**\n"
                        f"🕐 สัญญาณเมื่อ: {tracker['signal_time']}"
                    )
        else:
            remaining_trackers.append(tracker)

    ACTIVE_TRACKERS = remaining_trackers


# ============================================================
# MAIN LOOP (รันพร้อมกันในเซิร์ฟเวอร์เดียว)
# ============================================================

def main():
    log("🚀 รวมสคริปต์ทำงานพร้อมกัน: [ระบบ 1: สัญญาณ 15M] + [ระบบ 2: Sigzy Tracker]")
    send_discord("🤖 **SIGZY BOT COMBINED ONLINE**\nบอทสแกนสัญญาณและระบบติดตามผล (Tracker) เริ่มทำงานพร้อมกันแล้วครับ!")
    
    load_memory_from_file()

    while True:
        try:
            # รันระบบที่ 2 เช็คผลลัพธ์เก่าก่อน
            run_script_2_tracker()
            
            # รันระบบที่ 1 หาจุดเข้าใหม่
            run_script_1_scanner()

        except Exception as e:
            log(f"⚠️ MAIN ERROR: {e}")

        log("⏳ พักรอบการทำงาน 3 นาที...\n" + "="*50)
        time.sleep(180)
from threading import Thread
from http.server import HTTPServer, BaseHTTPRequestHandler

# สร้างเว็บเซิร์ฟเวอร์จำลองเพื่อให้ Railway มองว่าแอปทำงานอยู่ตลอดเวลา
class DummyHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is running!")

def run_server():
    server = HTTPServer(('0.0.0.0', 8080), DummyHandler)
    server.serve_forever()

# รันเซิร์ฟเวอร์จำลองไว้ในเบื้องหลัง (Background Thread)
server_thread = Thread(target=run_server, daemon=True)
server_thread.start()


if __name__ == "__main__":
    main()
