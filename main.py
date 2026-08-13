# -*- coding: utf-8 -*-
import os
import time
import requests
from datetime import datetime, timezone, timedelta
import yfinance as yf

# ===============================================
# SIGZY AI - YFINANCE VERSION
# ===============================================

# แมปคู่เงินสัญลักษณ์มาตรฐานเป็นฟอร์แมตของ Yahoo Finance
SYMBOL_MAP = {
    "EUR/USD": "EURUSD=X",
    "GBP/USD": "GBPUSD=X",
    "USD/JPY": "JPY=X",
    "AUD/USD": "AUDUSD=X",
    "USD/CHF": "CHF=X",
    "USD/CAD": "CAD=X",
}

def check_signals():
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] กำลังตรวจสอบสัญญาณ...")
    
    for symbol_name, yf_symbol in SYMBOL_MAP.items():
        try:
            # ดึงข้อมูลแท่งเทียน 15 นาทีล่าสุด
            ticker = yf.Ticker(yf_symbol)
            df = ticker.history(period="2d", interval="15m")
            
            if len(df) < 2:
                continue
                
            # แท่งปัจจุบัน (c0) และแท่งก่อนหน้า (c1)
            c0 = df.iloc[-1]
            c1 = df.iloc[-2]
            
            # เช็กเงื่อนไข Bullish Engulfing
            is_bull_engulfing = (c0['Close'] > c0['Open']) and (c1['Close'] < c1['Open']) and (c0['Close'] >= c1['Open']) and (c0['Open'] <= c1['Close'])
            
            # เช็กเงื่อนไข Bearish Engulfing
            is_bear_engulfing = (c0['Close'] < c0['Open']) and (c1['Close'] > c1['Open']) and (c0['Close'] <= c1['Open']) and (c0['Open'] >= c1['Close'])
            
            if is_bull_engulfing:
                print(f"--> สัญญาณ BUY: {symbol_name}")
            elif is_bear_engulfing:
                print(f"--> สัญญาณ SELL: {symbol_name}")
                
        except Exception as e:
            print(f"เกิดข้อผิดพลาดกับ {symbol_name}: {e}")

def main():
    print("เริ่มต้นทำงานบน Railway...")
    while True:
        try:
            check_signals()
        except Exception as e:
            print(f"MAIN ERROR: {e}")
        
        # รอ 15 นาที (900 วินาที) ก่อนตรวจสอบรอบถัดไป
        time.sleep(900)

if __name__ == "__main__":
    main()
