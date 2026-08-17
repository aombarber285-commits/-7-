# -*- coding: utf-8 -*-

import os
import json
import time
import requests
from datetime import datetime, timezone, timedelta
import yfinance as yf
from threading import Thread
from http.server import HTTPServer, BaseHTTPRequestHandler
from google import genai

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

# ตั้งค่า Client สำหรับ Google GenAI
ai_client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
GEMINI_MODEL = "gemini-2.5-flash"

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
MEMORY_FILE = "v13_memory_5m.json"

SENT_SIGNALS = set()
PENDING_TRADES = []
last_signal_time = time.time()

HISTORICAL_MEMORY = []
ACTIVE_TRACKERS = []


def now_text():
    utc_now = datetime.now(timezone.utc)
    thai = utc_now + timedelta(hours=7)
    return thai.strftime("%Y-%m-%d %H:%M:%S")


def log(msg):
    print(f"[{now_text()}] {msg}")


def send_discord(message):
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
# [AI Chat Integration with AFC Best Practices] รายงานตลาดทุก 5 นาที
# ============================================================

def ai_market_trend_report(symbol="EUR/USD"):
    """ใช้ Chat Session ตามคำแนะนำของ SDK เพื่อป้องกัน Error เกี่ยวกับ generate_content ตรงๆ"""
    try:
        chat = ai_client.chats.create(
            model=GEMINI_MODEL,
            config={
                "tools": []  # สามารถใส่ฟังก์ชันเสริมในนี้ได้หากต้องการใช้งาน Tool/Function Calling
            }
        )
        prompt = (
            f"ช่วยวิเคราะห์แนวโน้มสภาวะตลาด forex คู่เงิน {symbol} ในกรอบเวลา 5 นาที ตอนนี้ให้หน่อยครับ "
            f"ขอแบบกระชับสั้นๆ 2-3 บรรทัด ว่าตลาดกำลังอยู่ในเทรนด์ขาขึ้น ขาลง หรือไซด์เวย์"
        )
        response = chat.send_message(prompt)
        return response.text
    except Exception as e:
        return f"AI Analysis Error: {e}"


def market_reporter_loop():
    while True:
        try:
            log("🤖 กำลังให้ AI วิเคราะห์แนวโน้มตลาดรอบ 5 นาที...")
            send_discord("⏳ **[แจ้งเตือนระบบ]** AI กำลังวิเคราะห์แนวโน้มตลาด 5 นาที โปรดรอสักครู่... 🔄")
            time.sleep(3)
            analysis = ai_market_trend_report("EUR/USD")
            message = (
                f"📊 **[รายงานตลาด 5M]** 🤖\n"
                f"----------------------------------\n"
                f"{analysis}\n"
                f"----------------------------------"
            )
            send_discord(message)
        except Exception as e:
            print(f"Market Reporter Error: {e}")
        time.sleep(297)


# ============================================================
# [ส่วนที่ 1] สคริปต์สแกนสัญญาณ: เน้น 5M เป็นหลัก + กรองด้วย 1M
# ============================================================

def get_market_data(symbol, interval):
    yf_symbol = SYMBOL_MAP.get(symbol, symbol)
    try:
        ticker = yf.Ticker(yf_symbol)
        period = "5d" if interval == "1m" else "10d"
        df = ticker.history(period=period, interval=interval)
        if df.empty or len(df) < 30:
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
        print(f"[{interval} {symbol}] yfinance Error: {e}")
        return []


def calculate_atr(candles, period=14):
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


def analyze_5m_strategy(symbol):
    candles_5m = get_market_data(symbol, "5m")
    candles_1m = get_market_data(symbol, "1m")

    if len(candles_5m) < 30 or len(candles_1m) < 30:
        return {"decision": "WAIT", "score": 0}

    c0_5m = candles_5m[-1]
    price = c0_5m["close"]
    atr_5m = calculate_atr(candles_5m, 14)
    ema_5m = calculate_ema(candles_5m, 50)
    ema_1m = calculate_ema(candles_1m, 50)

    if not atr_5m or not ema_5m or not ema_1m:
        return {"decision": "WAIT", "score": 0}

    is_bullish_5m = c0_5m["close"] > ema_5m and c0_5m["close"] > c0_5m["open"]
    is_bearish_5m = c0_5m["close"] < ema_5m and c0_5m["close"] < c0_5m["open"]
    
    c0_1m = candles_1m[-1]
    is_bullish_1m = c0_1m["close"] > ema_1m
    is_bearish_1m = c0_1m["close"] < ema_1m

    if is_bullish_5m and is_bullish_1m:
        direction = "CALL"
        score = 85
        reasons = "5M Trend Bullish + 1M Momentum Confirm"
    elif is_bearish_5m and is_bearish_1m:
        direction = "PUT"
        score = 85
        reasons = "5M Trend Bearish + 1M Momentum Confirm"
    else:
        return {"decision": "WAIT", "score": 0}

    tp = price + atr_5m * 0.50 if direction == "CALL" else price - atr_5m * 0.50
    sl = price - atr_5m * 0.50 if direction == "CALL" else price + atr_5m * 0.50

    return {
        "decision": direction,
        "score": score,
        "symbol": symbol,
        "price": price,
        "atr": atr_5m,
        "tp": tp,
        "sl": sl,
        "reasons": reasons,
        "candle_time": c0_5m["datetime"],
        "setup_name": "5M_Strategy_1M_Filter"
    }


def run_script_1_scanner():
    global SENT_SIGNALS, PENDING_TRADES, ACTIVE_TRACKERS, last_signal_time
    print(f"\n[{now_text()}] 🔍 [ระบบ 1] สแกนจุดเข้ากรอบเวลา 5M (กรองด้วย 1M)...")

    signal_found = False

    for symbol in SYMBOLS:
        try:
            res = analyze_5m_strategy(symbol)
            if res["decision"] in ["CALL", "PUT"]:
                signal_key = (res["symbol"], res["candle_time"], res["decision"])
                if signal_key not in SENT_SIGNALS:
                    SENT_SIGNALS.add(signal_key)
                    PENDING_TRADES.append(res)
                    signal_found = True
                    last_signal_time = time.time()
                    
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
                        f"🚨 **[NEW SIGNAL - 5M] สัญญาณเทรด (กรอง 1M)** {icon}\n\n"
                        f"💱 คู่เงิน: **{res['symbol']}**\n"
                        f"📌 ทิศทาง: **{res['decision']}**\n"
                        f"🏆 คะแนน: **{res['score']}/100**\n"
                        f"💰 Entry: **{res['price']:.5f}**\n"
                        f"🎯 TP: **{res['tp']:.5f}** | 🛑 SL: **{res['sl']:.5f}**\n\n"
                        f"🔎 เงื่อนไข: {res['reasons']}\n"
                        f"🕐 เวลา: {now_text()}"
                    )
                    send_discord(msg)
                    print(f"🚨 [ระบบ 1] ส่งสัญญาณ 5M คู่ {res['symbol']} สำเร็จ!")
        except Exception as e:
            print(f"[ระบบ 1 Error] {symbol}: {e}")

    if not signal_found:
        elapsed_time = time.time() - last_signal_time
        if elapsed_time >= 600:
            send_discord(
                f"⏱️ **[แจ้งเตือนสถานะตลาด 5M]**\n"
                f"ไม่มีสัญญาณเทรด 5M เข้ามาเลยเป็นเวลา **10 นาทีแล้ว** 🧊\n"
                f"ระบบกำลังเฝ้าระวังแท่งเทียนถัดไป..."
            )
            last_signal_time = time.time()


# ============================================================
# [ส่วนที่ 2] Sigzy Tracker: ติดตามผล 5M ทบ 3 ไม้ (ชนะ 1 ใน 3)
# ============================================================

def load_memory_from_file():
    global HISTORICAL_MEMORY
    if os.path.exists(MEMORY_FILE):
        try:
            with open(MEMORY_FILE, "r", encoding="utf-8") as f:
                HISTORICAL_MEMORY = json.load(f)
            log(f"📂 [ระบบ 2] โหลดประวัติสำเร็จ! {len(HISTORICAL_MEMORY)} รายการ")
        except Exception as e:
            log(f"⚠️ [ระบบ 2] โหลด Memory ล้มเหลว: {e}")
            HISTORICAL_MEMORY = []
    else:
        log("📄 [ระบบ 2] สร้างฐานข้อมูลประวัติใหม่สำหรับ 5M...")


def save_memory_to_file():
    try:
        with open(MEMORY_FILE, "w", encoding="utf-8") as f:
            json.dump(HISTORICAL_MEMORY, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log(f"⚠️ [ระบบ 2] บันทึกไฟล์ล้มเหลว: {e}")


def get_closed_candles_5m(symbol):
    yf_symbol = SYMBOL_MAP.get(symbol, symbol)
    try:
        ticker = yf.Ticker(yf_symbol)
        df = ticker.history(period="2d", interval="5m")
        if df.empty or len(df) < 30:
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
        log(f"[Tracker 5M {symbol}] Error: {e}")
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
    global ACTIVE_TRACKERS
    if not ACTIVE_TRACKERS:
        return

    log("📈 [ระบบ 2] ติดตามผล Active Trackers (กรอบเวลา 5M)...")
    remaining_trackers = []

    for tracker in ACTIVE_TRACKERS:
        candles = get_closed_candles_5m(tracker["symbol"])
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
            tp_dist, sl_dist = atr * 0.5, atr * 0.5

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
                    f"🎯 **[SIGZY TRACKER 5M OUTCOME]**\n"
                    f"💱 คู่เงิน: **{tracker['symbol']}** ({direction})\n"
                    f"🏁 ผลลัพธ์: **WIN 🟢 (ชนะในไม้ที่ {current_step} ของ 5M)**\n"
                    f"📍 ราคาเข้าไม้ {current_step}: **{step_entry:.5f}**\n"
                    f"📈 MFE: **{tracker['max_mfe']:.5f}** | 📉 MAE: **{tracker['max_mae']:.5f}**\n"
                    f"🕐 สัญญาณเมื่อ: {tracker['signal_time']}"
                )
            else:
                if current_step < 3:
                    tracker["step"] += 1
                    remaining_trackers.append(tracker)
                else:
                    record_history(tracker, win_at_step=0, status="FULL_LOSS", step_entry=step_entry)
                    send_discord(
                        f"🛑 **[SIGZY TRACKER 5M OUTCOME]**\n"
                        f"💱 คู่เงิน: **{tracker['symbol']}** ({direction})\n"
                        f"🏁 ผลลัพธ์: **FULL LOSS 🔴 (แพ้ครบ 3 ไม้ 5M)**\n"
                        f"📈 MFE: **{tracker['max_mfe']:.5f}** | 📉 MAE: **{tracker['max_mae']:.5f}**\n"
                        f"🕐 สัญญาณเมื่อ: {tracker['signal_time']}"
                    )
        else:
            remaining_trackers.append(tracker)

    ACTIVE_TRACKERS = remaining_trackers


# ============================================================
# MAIN LOOP & BACKGROUND THREADS
# ============================================================

def main():
    log("🚀 เริ่มระบบใหม่: [สแกน 5M + กรอง 1M] + [Sigzy Tracker 3 ไม้] + [AI 5M Report]")
    load_memory_from_file()

    reporter_thread = Thread(target=market_reporter_loop, daemon=True)
    reporter_thread.start()

    while True:
        try:
            run_script_2_tracker()
            run_script_1_scanner()
        except Exception as e:
            log(f"⚠️ MAIN ERROR: {e}")

        log("⏳ พักรอบการทำงาน 3 นาที...\n" + "="*50)
        time.sleep(180)


class DummyHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot 5M is running!")

def run_server():
    server = HTTPServer(('0.0.0.0', 8080), DummyHandler)
    server.serve_forever()

server_thread = Thread(target=run_server, daemon=True)
server_thread.start()


if __name__ == "__main__":
    main()
