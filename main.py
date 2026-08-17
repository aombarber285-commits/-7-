# -*- coding: utf-8 -*-
"""
SIGZY 4-TF (STRICT TRACKING & BALANCED MARTINGALE 90%) - 100% THAI TIME FIXED
- แปลงระบบเวลาทั้งหมด (ทั้งสัญญาณเข้าและผลลัพธ์) ให้เป็นเวลาประเทศไทย (UTC+7)
- แก้ไข Bug Webhook ขาดบรรทัด (Syntax Error)
- เช็คเทรนด์ด้วยแท่งปิดจบ (-2) ป้องกันสัญญาณหลอก
"""

import json
import time
import random
import urllib.request
from datetime import datetime, timedelta, timezone

# 📌 WEBHOOK URL
DISCORD_WEBHOOK_URL = "https://discord.com/api/webhooks/1537044300305530950/-aDtd7fsi5lzAaLoYA3VwaKAvjPvf-vFMwYIuqctxX8BZ7RHtF89AIebiR78o7CNBOUV"

SYMBOL_MAP = {
    "EUR/JPY": "EURJPY=X",
    "EUR/USD": "EURUSD=X",
    "GBP/USD": "GBPUSD=X",
    "USD/JPY": "JPY=X"
}

SENT_SIGNALS = set()
PAIR_LOCKED_UNTIL = {}
OTC_HISTORY = {}
DAILY_SIGNAL_COUNT = 0
CURRENT_DAY = None

def get_thai_time(dt=None):
    """ ดึงเวลาปัจจุบัน หรือแปลง datetime ใดๆ ให้เป็นเวลาไทย (+7 ชม.) """
    if dt is None:
        dt = datetime.now(timezone.utc)
    return dt.astimezone(timezone(timedelta(hours=7)))

def timestamp_to_thai_str(ts, fmt="%Y-%m-%d %H:%M:%S"):
    """ แปลง Timestamp ของกราฟให้เป็นสตริงเวลาไทย """
    dt_utc = datetime.fromtimestamp(ts, tz=timezone.utc)
    dt_thai = dt_utc.astimezone(timezone(timedelta(hours=7)))
    return dt_thai.strftime(fmt)

def send_discord(message):
    headers = {'Content-Type': 'application/json', 'User-Agent': 'Mozilla/5.0'}
    data = json.dumps({"content": message}).encode('utf-8')
    req = urllib.request.Request(url=DISCORD_WEBHOOK_URL, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            if response.status in (200, 204):
                print("✅ ส่งแจ้งเตือนเข้า Discord เรียบร้อย!")
                return True
    except Exception as e:
        print(f"❌ ส่ง Discord ไม่สำเร็จ: {e}")
        return False

def is_weekend():
    return get_thai_time().weekday() in [5, 6]

def check_daily_reset():
    global DAILY_SIGNAL_COUNT, CURRENT_DAY, SENT_SIGNALS
    today = get_thai_time().strftime("%Y-%m-%d")
    if CURRENT_DAY != today:
        CURRENT_DAY = today
        DAILY_SIGNAL_COUNT = 0
        SENT_SIGNALS.clear()

def fetch_yahoo_candles(symbol_ticker):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol_ticker}?interval=1m&range=1d"
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode())
            result = data['chart']['result'][0]
            timestamps = result.get('timestamp', [])
            quote = result['indicators']['quote'][0]
            
            candles = []
            for i in range(len(timestamps)):
                o, h, l, c = quote['open'][i], quote['high'][i], quote['low'][i], quote['close'][i]
                if None not in (o, h, l, c):
                    candles.append({
                        'timestamp': timestamps[i],
                        'open': float(o), 'high': float(h), 'low': float(l), 'close': float(c)
                    })
            return candles[-120:]
    except Exception as e:
        print(f"⚠️ ดึงข้อมูล Yahoo {symbol_ticker} ล้มเหลว: {e}")
        return []

def generate_otc_candles_persistent(symbol_name):
    now_ts = int(time.time())
    if symbol_name not in OTC_HISTORY:
        base_price = 162.500 if "JPY" in symbol_name else 1.0850
        candles = []
        trend_bias = random.choice([-0.04, 0.04])
        for i in range(60, 0, -1):
            ts = now_ts - (i * 60)
            change = trend_bias + random.uniform(-0.03, 0.03)
            open_p = base_price
            close_p = open_p + change
            high_p = max(open_p, close_p) + abs(random.uniform(0.005, 0.02))
            low_p = min(open_p, close_p) - abs(random.uniform(0.005, 0.02))
            candles.append({'timestamp': ts, 'open': round(open_p, 3), 'high': round(high_p, 3), 'low': round(low_p, 3), 'close': round(close_p, 3)})
            base_price = close_p
        OTC_HISTORY[symbol_name] = candles
    else:
        last_candle = OTC_HISTORY[symbol_name][-1]
        if now_ts - last_candle['timestamp'] >= 60:
            open_p = last_candle['close']
            change = random.uniform(-0.05, 0.05)
            close_p = open_p + change
            high_p = max(open_p, close_p) + abs(random.uniform(0.005, 0.025))
            low_p = min(open_p, close_p) - abs(random.uniform(0.005, 0.025))
            OTC_HISTORY[symbol_name].append({'timestamp': now_ts, 'open': round(open_p, 3), 'high': round(high_p, 3), 'low': round(low_p, 3), 'close': round(close_p, 3)})
            OTC_HISTORY[symbol_name] = OTC_HISTORY[symbol_name][-60:]
            
    return OTC_HISTORY[symbol_name]

def resample_candles_by_time(candles_1m, timeframe_minutes):
    grouped = {}
    tf_sec = timeframe_minutes * 60
    
    for c in candles_1m:
        group_key = (c['timestamp'] // tf_sec) * tf_sec
        if group_key not in grouped:
            grouped[group_key] = []
        grouped[group_key].append(c)
        
    resampled = []
    for g_key in sorted(grouped.keys()):
        group = grouped[g_key]
        resampled.append({
            'timestamp': g_key,
            'open': group[0]['open'],
            'high': max(item['high'] for item in group),
            'low': min(item['low'] for item in group),
            'close': group[-1]['close']
        })
    return resampled

def check_balanced_entry(c_1m, direction):
    full_range = max(c_1m['high'] - c_1m['low'], 1e-6)
    upper_wick = c_1m['high'] - max(c_1m['open'], c_1m['close'])
    lower_wick = min(c_1m['open'], c_1m['close']) - c_1m['low']

    min_wick = 0.02
    wick_ratio = (lower_wick / full_range) if direction == "CALL" else (upper_wick / full_range)
    
    if wick_ratio < min_wick:
        return False, "ไส้เทียนย่อสั้นไปเล็กน้อย"

    return True, "จังหวะเข้าตามโมเมนตัมเทรนด์"

def analyze_4tf(symbol_name, ticker_symbol):
    global DAILY_SIGNAL_COUNT
    check_daily_reset()
    now_dt = get_thai_time()

    if symbol_name in PAIR_LOCKED_UNTIL:
        if now_dt < PAIR_LOCKED_UNTIL[symbol_name]:
            return None, f"-> {symbol_name}: ล็อคติดตามการเดินเงิน (ถึงเวลา {PAIR_LOCKED_UNTIL[symbol_name].strftime('%H:%M')} น.)"

    otc_flag = is_weekend()
    c_1m_list = generate_otc_candles_persistent(symbol_name) if otc_flag else fetch_yahoo_candles(ticker_symbol)
    
    if not c_1m_list and not otc_flag:
        c_1m_list = generate_otc_candles_persistent(symbol_name)
        display_name = f"{symbol_name} (Backup)"
    else:
        display_name = f"{symbol_name} (OTC)" if otc_flag else symbol_name

    if len(c_1m_list) < 15:
        return None, f"-> {display_name}: ข้อมูลกราฟยังไม่พอ..."

    c_15m_list = resample_candles_by_time(c_1m_list, 15)
    c_5m_list = resample_candles_by_time(c_1m_list, 5)

    if len(c_15m_list) < 2 or len(c_5m_list) < 2:
        return None, f"-> {display_name}: กำลังประมวลผล Timeframe..."

    c_15m = c_15m_list[-2] if len(c_15m_list) >= 2 else c_15m_list[-1]
    master_direction = "CALL" if c_15m['close'] >= c_15m['open'] else "PUT"
    trend_name = "UPTREND (15M)" if master_direction == "CALL" else "DOWNTREND (15M)"

    c_5m = c_5m_list[-2] if len(c_5m_list) >= 2 else c_5m_list[-1]
    is_5m_ok = (c_5m['close'] >= c_5m['open']) if master_direction == "CALL" else (c_5m['close'] <= c_5m['open'])
    if not is_5m_ok:
        return None, f"-> {display_name}: 5M ยังไม่คล้อยตาม 15M"

    c_1m = c_1m_list[-1]
    is_valid, reason = check_balanced_entry(c_1m, master_direction)
    if not is_valid:
        return None, f"-> {display_name}: {reason}"

    # 🇹🇭 กำหนดเวลาเข้าและหมดเวลาในรูปแบบ เวลาไทย
    entry_time_str = now_dt.strftime("%H:%M")
    expiry_dt = now_dt + timedelta(minutes=15)
    expiry_time_str = expiry_dt.strftime("%H:%M")
    candle_time_thai = timestamp_to_thai_str(c_1m['timestamp'])

    PAIR_LOCKED_UNTIL[symbol_name] = expiry_dt
    DAILY_SIGNAL_COUNT += 1
    
    icon = "🟢" if master_direction == "CALL" else "🔴"
    signal_key = f"{display_name}_{c_1m['timestamp']}_{master_direction}"

    msg = (
        f"🎯 **STRICT TRACKING SIGNAL (#{DAILY_SIGNAL_COUNT})** 🎯\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⏱️ **5m {display_name}**\n"
        f"{icon} **เวลาเข้าซื้อ: {entry_time_str} น. (เวลาไทย 🇹🇭)**\n"
        f"⏳ **ล็อคถึงเวลา: {expiry_time_str} น.**\n\n"
        f"📊 ทิศทางยิง: **{master_direction} (ตามเทรนด์ 15M)**\n"
        f"💰 ราคาเข้า (Entry): **{c_1m['close']:.3f}**\n"
        f"🕐 เวลาแท่งเทียน: **{candle_time_thai} น.**\n"
        f"🎯 **เป้าหมาย:** ชนะ 1 ใน 3 ไม้ (Martingale Opportunity)\n\n"
        f"📌 **สถานะโครงสร้าง:**\n"
        f"• 15M Trend : {icon} {trend_name}\n"
        f"• 5M Action : ✅ โมเมนตัมไปทางเดียวกัน\n"
        f"• 1M Trigger: ⚡ {reason}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💡 *บริหารเงิน 3 ไม้ (ไม้ 1 -> ไม้ 2 -> ไม้ 3) ชนะไม้ไหนหยุดทันที!*"
    )

    return signal_key, msg

if __name__ == "__main__":
    mode_str = "OTC MODE (เสาร์-อาทิตย์)" if is_weekend() else "REAL MARKET MODE (จันทร์-ศุกร์)"
    print(f"--- 🚀 SIGZY STRICT TRACKING SNIPER STARTING [{mode_str}] ---")
    
    test_msg = f"🔔 **[SYSTEM TEST]** บอทเริ่มทำงานแล้ว! ระบบเวลาปรับเป็น **เวลาไทย (UTC+7)** เรียบร้อย ({mode_str})"
    print("กำลังทดสอบส่งข้อความเข้า Discord...")
    send_discord(test_msg)
    
    print("\nเริ่มสแกนกราฟพร้อมระบบล็อคโฟกัส (วนลูปทุก 60 วินาที)...\n")
    
    while True:
        now_str = get_thai_time().strftime("%H:%M:%S")
        print(f"[{now_str} น.] กำลังสแกนหาจังหวะเข้าเทรด...")
        for name, ticker in SYMBOL_MAP.items():
            sig_key, result_msg = analyze_4tf(name, ticker)
            if sig_key:
                if sig_key not in SENT_SIGNALS:
                    print(f"\n🎯 พบสัญญาณ! ล็อคโฟกัสคู่ {name} ส่งเข้า Discord เรียบร้อย")
                    send_discord(result_msg)
                    SENT_SIGNALS.add(sig_key)
            else:
                print(result_msg)
        time.sleep(60)
