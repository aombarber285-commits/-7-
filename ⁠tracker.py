# -*- coding: utf-8 -*-
import os
import json
import requests
from config import DISCORD_WEBHOOK_URL, MEMORY_FILE, SYMBOL_MAP, INTERVAL, log, now_text
import yfinance as yf

HISTORICAL_MEMORY = []
ACTIVE_TRACKERS = []

def send_discord(message):
    if not DISCORD_WEBHOOK_URL:
        return
    try:
        payload = {"content": message}
        response = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=5)
        if response.status_code == 204:
            log("  [Discord] ส่งข้อความสำเร็จ!")
        else:
            log(f"  [Discord Error] Status Code: {response.status_code}")
    except Exception as e:
        log(f"  [Discord Exception] {e}")

def load_memory_from_file():
    global HISTORICAL_MEMORY
    if os.path.exists(MEMORY_FILE):
        try:
            with open(MEMORY_FILE, "r", encoding="utf-8") as f:
                HISTORICAL_MEMORY = json.load(f)
            log(f"📂 โหลดประวัติเก่าสำเร็จ! ข้อมูลทั้งหมด {len(HISTORICAL_MEMORY)} รายการ")
        except Exception as e:
            log(f"⚠️ โหลดไฟล์ Memory ล้มเหลว: {e}")
            HISTORICAL_MEMORY = []
    else:
        log("📄 ไม่พบไฟล์ประวัติเก่า เริ่มสร้างฐานข้อมูลใหม่...")

def save_memory_to_file():
    try:
        with open(MEMORY_FILE, "w", encoding="utf-8") as f:
            json.dump(HISTORICAL_MEMORY, f, ensure_ascii=False, indent=2)
        log("💾 บันทึกลงไฟล์ v13_memory.json สำเร็จ!")
    except Exception as e:
        log(f"⚠️ บันทึกไฟล์ Memory ล้มเหลว: {e}")

def get_closed_candles(symbol):
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
        log(f"[{symbol}] Data Fetch Error: {e}")
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

def update_3_opportunity_trackers():
    global ACTIVE_TRACKERS
    if not ACTIVE_TRACKERS:
        return

    remaining_trackers = []

    for tracker in ACTIVE_TRACKERS:
        candles = get_closed_candles(tracker["symbol"])
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
            else:
                tp_price, sl_price = step_entry - tp_dist, step_entry + sl_dist
                mfe = step_entry - target_candle["low"]
                mae = target_candle["high"] - step_entry
                tp_hit = target_candle["low"] <= tp_price
                sl_hit = target_candle["high"] >= sl_price

            tracker["max_mfe"] = max(tracker.get("max_mfe", 0), mfe)
            tracker["max_mae"] = max(tracker.get("max_mae", 0), mae)

            if tp_hit and sl_hit:
                is_win = (target_candle["close"] > step_entry) if direction == "CALL" else (target_candle["close"] < step_entry)
            elif tp_hit:
                is_win = True
            elif sl_hit:
                is_win = False
            else:
                is_win = (target_candle["close"] > step_entry) if direction == "CALL" else (target_candle["close"] < step_entry)

            if is_win:
                record_history(tracker, win_at_step=current_step, status="WIN", step_entry=step_entry)
                send_discord(
                    f"🎯 **[V13 OUTCOME UPDATE]**\n"
                    f"💱 คู่เงิน: **{tracker['symbol']}** ({direction})\n"
                    f"🏁 ผลลัพธ์: **WIN 🟢 (ชนะในไม้ที่ {current_step})**\n"
                    f"📍 ราคาเข้าจริงไม้ {current_step}: **{step_entry:.5f}**\n"
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
                        f"🛑 **[V13 OUTCOME UPDATE]**\n"
                        f"💱 คู่เงิน: **{tracker['symbol']}** ({direction})\n"
                        f"🏁 ผลลัพธ์: **FULL LOSS 🔴 (แพ้ครบ 3 ไม้)**\n"
                        f"📈 Max MFE: **{tracker['max_mfe']:.5f}** | 📉 Max MAE: **{tracker['max_mae']:.5f}**\n"
                        f"🕐 สัญญาณเมื่อ: {tracker['signal_time']}"
                    )
        else:
            remaining_trackers.append(tracker)

    ACTIVE_TRACKERS = remaining_trackers

def print_memory_stats():
    if not HISTORICAL_MEMORY:
        return
    total = len(HISTORICAL_MEMORY)
    wins = sum(1 for r in HISTORICAL_MEMORY if r["status"] == "WIN")
    s1 = sum(1 for r in HISTORICAL_MEMORY if r["win_at_step"] == 1)
    s2 = sum(1 for r in HISTORICAL_MEMORY if r["win_at_step"] == 2)
    s3 = sum(1 for r in HISTORICAL_MEMORY if r["win_at_step"] == 3)
    losses = sum(1 for r in HISTORICAL_MEMORY if r["status"] == "FULL_LOSS")
    avg_mfe = sum(r.get("max_mfe", 0) for r in HISTORICAL_MEMORY) / total
    avg_mae = sum(r.get("max_mae", 0) for r in HISTORICAL_MEMORY) / total

    log(f"\n==================================================")
    log(f"📊 SIGZY V13 MEMORY STATS (Total: {total})")
    log(f"• Win Rate รวม (3 ไม้): {(wins/total)*100:.2f}%")
    log(f"  - ชนะไม้ 1: {s1} | ชนะไม้ 2: {s2} | ชนะไม้ 3: {s3} | แพ้ครบ: {losses}")
    log(f"• MFE เฉลี่ย: {avg_mfe:.5f} | MAE เฉลี่ย: {avg_mae:.5f}")
    log(f"==================================================\n")
