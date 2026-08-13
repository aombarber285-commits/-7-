# -*- coding: utf-8 -*-
import os
import time
import requests
from datetime import datetime
import yfinance as yf

# ===============================================
# SIGZY AI / FOREX BOT - YFINANCE VERSION
# ===============================================

# แมปคู่เงินสัญลักษณ์มาตรฐานเป็นฟอร์แมตของ Yahoo Finance (=X)
SYMBOL_MAP = {
    "EUR/USD": "EURUSD=X",
    "GBP/USD": "GBPUSD=X",
    "USD/JPY": "JPY=X",
    "AUD/USD": "AUDUSD=X",
    "USD/CHF": "CHF=X",
    "USD/CAD": "CAD=X",
}

def check_signals():
    """ฟังก์ชันตรวจสอบสัญญาณเทรดจาก Candlestick Pattern"""
    current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f"\n[{current_time}] --- กำลังตรวจสอบสัญญาณตลาด ---")
    
    for symbol_name, yf_symbol in SYMBOL_MAP.items():
        try:
            # ดึงข้อมูลแท่งเทียนย้อนหลัง (ช่วงเวลา 15 นาที)
            ticker = yf.Ticker(yf_symbol)
            df = ticker.history(period="2d", interval="15m")
            
            # ตรวจสอบว่ามีข้อมูลเพียงพออย่างน้อย 2 แท่งหรือไม่
            if df is None or len(df) < 2:
                print(f"[{symbol_name}] ข้อมูลไม่เพียงพอ")
                continue
                
            # แท่งปัจจุบัน (c0) และแท่งก่อนหน้า (c1)
            c0 = df.iloc[-1]
            c1 = df.iloc[-2]
            
            # เช็กเงื่อนไข Bullish Engulfing (กลืนกินฝั่งซื้อ)
            is_bull_engulfing = (
                (c0['Close'] > c0['Open']) and 
                (c1['Close'] < c1['Open']) and 
                (c0['Close'] >= c1['Open']) and 
                (c0['Open'] <= c1['Close'])
            )
            
            # เช็กเงื่อนไข Bearish Engulfing (กลืนกินฝั่งขาย)
            is_bear_engulfing = (
                (c0['Close'] < c0['Open']) and 
                (c1['Close'] > c1['Open']) and 
                (c0['Close'] <= c1['Open']) and 
                (c0['Open'] >= c1['Close'])
            )
            
            # แสดงผลลัพธ์การวิเคราะห์
            if is_bull_engulfing:
                print(f"🟢 [SIGNAL BUY] -> คู่เงิน: {symbol_name} | ราคาปิด: {c0['Close']:.5f}")
            elif is_bear_engulfing:
                print(f"🔴 [SIGNAL SELL] -> คู่เงิน: {symbol_name} | ราคาปิด: {c0['Close']:.5f}")
            else:
                print(f"⚪ [{symbol_name}] ไม่มีสัญญาณเทรด (ปกติ)")
                
        except Exception as e:
            print(f"❌ เกิดข้อผิดพลาดกับ {symbol_name}: {e}")

def main():
    """ฟังก์ชันหลัก รันระบบแบบตลอด 24 ชั่วโมง"""
    print("🚀 เริ่มต้นการทำงานของบอทบน Railway...")
    
    while True:
        try:
            check_signals()
        except Exception as e:
            print(f"⚠️ เกิดข้อผิดพลาดในระบบหลัก: {e}")
        
        # หน่วงเวลา 15 นาที (900 วินาที) แล้ววนกลับมาเช็กใหม่
        print("⏳ รอตรวจสอบรอบถัดไปในอีก 15 นาที...")
        time.sleep(900)

if __name__ == "__main__":
    main()
