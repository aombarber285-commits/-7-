# -*- coding: utf-8 -*-
import time
from config import SYMBOLS, log
from tracker import load_memory_from_file, update_3_opportunity_trackers, print_memory_stats, send_discord, ACTIVE_TRACKERS, get_closed_candles
from signal_engine import analyze_v13_signal

def scan_and_process():
    log("🔍 [V13 ULTIMATE] สแกนแท่งเทียน 15M...")
    for symbol in SYMBOLS:
        candles = get_closed_candles(symbol)
        if not candles:
            continue

        sig = analyze_v13_signal(symbol, candles)
        if sig:
            tracker = {
                "symbol": sig["symbol"],
                "decision": sig["decision"],
                "score": sig["score"],
                "entry_price": sig["entry_price"],
                "atr": sig["atr"],
                "signal_time": sig["signal_time"],
                "setup_name": sig["setup_name"],
                "step": 1,
                "max_mfe": 0.0,
                "max_mae": 0.0
            }
            ACTIVE_TRACKERS.append(tracker)

            icon = "🟢" if sig['decision'] == "CALL" else "🔴"
            msg = (
                f"🚨 **[SIGZY V13 ULTIMATE SIGNAL] เกิดสัญญาณใหม่** {icon}\n\n"
                f"💱 คู่เงิน: **{sig['symbol']}** ({sig['decision']})\n"
                f"🏆 AI Score: **{sig['score']}** | Setup: **{sig['setup_name']}**\n"
                f"💰 ราคาเข้าไม้ 1: **{sig['entry_price']:.5f}**\n\n"
                f"🔎 **เหตุผล:** {sig['reasons']}\n"
                f"📌 *ระบบเริ่มติดตาม 3 โอกาส (เข้าใหม่ทุกไม้) + บันทึก MFE/MAE อัตโนมัติ*\n"
                f"🕐 แท่งเทียนเวลา: {sig['signal_time']}"
            )
            send_discord(msg)
            log(f"🎯 New Signal Issued: {sig['symbol']} {sig['decision']} (Score: {sig['score']})")

def main():
    log("🚀 SIGZY AI V13 ULTIMATE ONLINE...")
    load_memory_from_file()
    send_discord("🤖 **SIGZY AI V13 ULTIMATE ONLINE**\nระบบแยกไฟล์รัน Modular Engine พร้อมทำงานแล้วครับ!")

    while True:
        try:
            scan_and_process()
            update_3_opportunity_trackers()
            print_memory_stats()
        except Exception as e:
            log(f"CRITICAL ERROR: {e}")

        log("⏳ พักสแกน 3 นาที...\n")
        time.sleep(180)

if __name__ == "__main__":
    main()
