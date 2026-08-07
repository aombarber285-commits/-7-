import urllib.request
import json
import random
import time
from datetime import datetime, timedelta
import threading
import math
import sqlite3
import pandas as pd
import numpy as np

# ==========================================
# ⚙️ CONFIGURATION & SETTINGS
# ==========================================
MODE = "LIVE"  # เลือกโหมด: "LIVE" หรือ "BACKTEST"
TRADE_SESSIONS = ["LONDON", "NEW_YORK"] 
USE_NEWS_FILTER = True 

# คู่เงินหลักสำหรับสูตร 1, 2, 3, 6 (เพิ่ม GBP/JPY เป็นคู่ที่ 6)
PAIRS = [
    {"name": "EUR/USD", "symbol": "EURUSDT"},
    {"name": "GBP/USD", "symbol": "GBPUSDT"},
    {"name": "AUD/USD", "symbol": "AUDUSDT"},
    {"name": "USD/JPY", "symbol": "USDJPY"},
    {"name": "EUR/GBP", "symbol": "EURGBP"},
    {"name": "GBP/JPY", "symbol": "GBPJPY=X"}  # คู่เงินที่ 6
]

# คู่เงินเฉพาะสำหรับสูตรที่ 4 (Sniper Pro SOL/USD)
SOL_PAIRS = [
    {"name": "SOL/USD", "symbol": "SOLUSDT", "color": "\033[95m"}
]

# คู่เงินเฉพาะสำหรับสูตรที่ 7 A+
STRATEGY7_PAIRS = [
    {"name": "EUR/JPY", "symbol": "EURJPY=X"}
]

# 🛡️ เพิ่ม CAD/JPY เข้าไปในระบบ Sniper Pro
SNIPER_PRO_CADJPY_PAIR = {"name": "CAD/JPY", "symbol": "CADJPY=X"}

INTERVAL = "1m"
stakes = {1: 30, 2: 60, 3: 120}
INITIAL_CAPITAL = 10000.0
BASE_STAKE = 30.0

# สถิติสำหรับสูตรต่างๆ
stats = {"win": 0, "draw": 0, "loss": 0}
stats_cad_jpy = {"win": 0, "draw": 0, "loss": 0}  # สถิติเฉพาะ CAD/JPY Sniper Pro
stats_gbp = {"win": 0, "draw": 0, "loss": 0}
stats_strat7 = {"win": 0, "draw": 0, "loss": 0}
stats_lock = threading.Lock()

# ตัวแปรสถานะแยกแต่ละสูตร
sniper_traders = {
    p["symbol"]: {"name": p["name"], "step": 1, "rest_until": 0, "active_action": "PUT", "in_progress": False}
    for p in PAIRS
}
strategy3_traders = {
    p["symbol"]: {"name": p["name"], "step": 1, "rest_until": 0, "active_action": None, "in_progress": False}
    for p in PAIRS
}
strategy6_traders = {
    p["symbol"]: {"name": p["name"], "step": 1, "rest_until": 0, "active_action": None, "in_progress": False}
    for p in PAIRS
}

# ==========================================
# 🗄️ DATABASE & METRICS (PERMANENT SYSTEM STATS)
# ==========================================
def init_database():
    conn = sqlite3.connect("trading_journal.db", check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            pair TEXT,
            strategy TEXT,
            action TEXT,
            stake REAL,
            result TEXT,
            profit_loss REAL
        )
    ''')
    conn.commit()
    return conn

db_conn = init_database()
db_lock = threading.Lock()

def log_trade_to_db(timestamp, pair, strategy, action, stake, result, pl):
    with db_lock:
        cursor = db_conn.cursor()
        cursor.execute('''
            INSERT INTO trades (timestamp, pair, strategy, action, stake, result, profit_loss)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (timestamp, pair, strategy, action, stake, result, pl))
        db_conn.commit()

def get_total_system_stats():
    """ ดึงสถิติรวมของทุกสูตรแบบสะสมถาวรจากฐานข้อมูล SQLite """
    with db_lock:
        cursor = db_conn.cursor()
        cursor.execute("SELECT result, COUNT(*) FROM trades GROUP BY result")
        data = dict(cursor.fetchall())
        return data.get("WIN", 0), data.get("DRAW", 0), data.get("LOSS", 0)

def print_total_system_stats_summary():
    """ พิมพ์สถิติรวมสะสมถาวรทุกสูตร """
    wins, draws, losses = get_total_system_stats()
    print(f"\n==========================================")
    print(f"       🏆 สถิติรวมทุกสูตร (สะสมถาวรใน DB)")
    print(f"             ชนะ{wins}  เสมอ{draws}   แพ้{losses}")
    print(f"==========================================\n")

def print_stats_summary():
    with stats_lock:
        print(f"\n==========================================")
        print(f"       📊 สถิติผลงานรวม Sniper Pro (SOL/USD)")
        print(f"   ชนะ: {stats['win']}  |  แพ้ (รวมเสมอ): {stats['loss']}")
        print(f"==========================================\n")
    print_total_system_stats_summary()

def print_cad_jpy_stats_summary():
    with stats_lock:
        print(f"\n==========================================")
        print(f"       📊 สถิติผลงานรวม Sniper Pro (CAD/JPY)")
        print(f"   ชนะ: {stats_cad_jpy['win']}  |  เสมอ: {stats_cad_jpy['draw']}  |  แพ้: {stats_cad_jpy['loss']}")
        print(f"==========================================\n")
    print_total_system_stats_summary()

def print_gbp_stats_summary():
    with stats_lock:
        print(f"\n==========================================")
        print(f"       📊 สถิติผลงานรวม GBP/JPY (GJ)")
        print(f"   ชนะ: {stats_gbp['win']}  |  เสมอ: {stats_gbp['draw']}  |  แพ้: {stats_gbp['loss']}")
        print(f"==========================================\n")
    print_total_system_stats_summary()

def print_strat7_stats_summary():
    with stats_lock:
        print(f"\n==========================================")
        print(f"       📊 สถิติผลงานรวม สูตรที่7 A+ (EUR/JPY)")
        print(f"   ชนะ: {stats_strat7['win']}  |  เสมอ: {stats_strat7['draw']}  |  แพ้: {stats_strat7['loss']}")
        print(f"==========================================\n")
    print_total_system_stats_summary()

# ==========================================
# 🔔 DISCORD ALERT FUNCTION (ใช้ร่วมกันทุกสูตร)
# ==========================================
def send_discord_alert(message):
    WEBHOOK_URL = "https://discord.com/api/webhooks/1535170560789717044/79qFAb9xBXFAWSQqP3BXpxKI65KnMH3CQ6YxaQFPoeQH1zG45xqkRD0yUrw0SFeZqMsB"
    
    # แนบสถิติรวมสะสมถาวรต่อท้ายข้อความเสมอ
    wins, draws, losses = get_total_system_stats()
    stats_msg = f"\n\n📊 **สถิติรวมทุกสูตร (สะสมถาวร):** ชนะ {wins} | เสมอ {draws} | แพ้ {losses}"
    
    payload = {"content": message + stats_msg}
    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(WEBHOOK_URL, data=data, headers={'Content-Type': 'application/json', 'User-Agent': 'Mozilla/5.0'}, method="POST")
        with urllib.request.urlopen(req, timeout=5) as response:
            return response.status == 204
    except Exception as e:
        print(f"⚠️ Discord Alert Error: {e}")

# ==========================================
# 🛠️ UNIVERSAL DATA FETCH & CALCULATIONS
# ==========================================
def get_market_data(symbol, interval="1m", limit=300):
    if "=X" in symbol:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval={interval}&range=1d"
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        for _ in range(3):
            try:
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=5) as response:
                    res = json.loads(response.read().decode())
                    chart_data = res['chart']['result'][0]
                    timestamps = chart_data['timestamp']
                    quote = chart_data['indicators']['quote'][0]
                    opens, highs, lows, closes = quote['open'], quote['high'], quote['low'], quote['close']
                    volumes = quote.get('volume', [1]*len(closes))
                    klines = []
                    for i in range(len(timestamps)):
                        if None in (opens[i], highs[i], lows[i], closes[i]): continue
                        v = float(volumes[i]) if volumes[i] is not None else 1.0
                        klines.append([timestamps[i] * 1000, float(opens[i]), float(highs[i]), float(lows[i]), float(closes[i]), v])
                    if klines:
                        df = pd.DataFrame(klines[-limit:], columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
                        return df
            except Exception:
                time.sleep(0.3)
        return None

    url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
    for _ in range(3):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=5) as response:
                raw_data = json.loads(response.read().decode())
                df = pd.DataFrame(raw_data, columns=[
                    'timestamp', 'open', 'high', 'low', 'close', 'volume',
                    'close_time', 'quote_asset_volume', 'number_of_trades',
                    'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore'
                ])
                for col in ['open', 'high', 'low', 'close', 'volume']:
                    df[col] = df[col].astype(float)
                return df
        except Exception:
            time.sleep(0.3 if symbol == "SOLUSDT" else 1)
    return None

def calculate_all_indicators(df):
    df['EMA20'] = df['close'].ewm(span=20, adjust=False).mean()
    df['EMA50'] = df['close'].ewm(span=50, adjust=False).mean()
    
    delta = df['close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss
    df['RSI'] = 100 - (100 / (1 + rs))
    
    exp1 = df['close'].ewm(span=12, adjust=False).mean()
    exp2 = df['close'].ewm(span=26, adjust=False).mean()
    df['MACD'] = exp1 - exp2
    df['MACD_Signal'] = df['MACD'].ewm(span=9, adjust=False).mean()
    
    high_diff = df['high'].diff()
    low_diff = df['low'].diff()
    df['+DM'] = np.where((high_diff > low_diff) & (high_diff > 0), high_diff, 0.0)
    df['-DM'] = np.where((low_diff > high_diff) & (low_diff > 0), low_diff, 0.0)
    tr1 = df['high'] - df['low']
    tr2 = abs(df['high'] - df['close'].shift(1))
    tr3 = abs(df['low'] - df['close'].shift(1))
    df['TR'] = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr_val = df['TR'].ewm(alpha=1/14, adjust=False).mean()
    plus_di = 100 * (df['+DM'].ewm(alpha=1/14, adjust=False).mean() / atr_val)
    minus_di = 100 * (df['-DM'].ewm(alpha=1/14, adjust=False).mean() / atr_val)
    dx = (abs(plus_di - minus_di) / (plus_di + minus_di)) * 100
    df['ADX'] = dx.ewm(alpha=1/14, adjust=False).mean()
    
    df['BB_Middle'] = df['close'].rolling(window=20).mean()
    bb_std = df['close'].rolling(window=20).std()
    df['BB_Upper'] = df['BB_Middle'] + (bb_std * 2)
    df['BB_Lower'] = df['BB_Middle'] - (bb_std * 2)
    
    df['Vol_Avg'] = df['volume'].rolling(window=20).mean()
    df['ATR'] = atr_val
    df['Resistance'] = df['high'].rolling(window=20).max()
    df['Support'] = df['low'].rolling(window=20).min()
    return df.fillna(0)

# ==========================================
# 🛡️ ระบบ Sniper Pro CAD/JPY
# ==========================================
def get_market_data_cadjpy(symbol):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=2d&interval=1m"
    headers = {'User-Agent': 'Mozilla/5.0'}
    for _ in range(5):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=10) as response:
                data = json.loads(response.read().decode('utf-8'))
                result = data.get('chart', {}).get('result', [])
                if not result: return None
                timestamps = result[0].get('timestamp', [])
                quote = result[0].get('indicators', {}).get('quote', [{}])[0]
                opens, highs, lows, closes = quote.get('open', []), quote.get('high', []), quote.get('low', []), quote.get('close', [])
                klines = []
                for i in range(len(timestamps)):
                    if opens[i] is not None and closes[i] is not None:
                        klines.append([timestamps[i] * 1000, str(opens[i]), str(highs[i]), str(lows[i]), str(closes[i]), "0"])
                return klines[-250:]
        except Exception:
            time.sleep(2)
    return None

def calculate_ema(closes, period):
    if len(closes) < period: return sum(closes)/len(closes) if closes else 0
    k = 2 / (period + 1)
    ema = sum(closes[:period]) / period  
    for close in closes[period:]:
        ema = (close - ema) * k + ema
    return ema

def calculate_adx_atr_real(klines, period=14):
    if len(klines) < period * 2: return 15.0, 0.001
    
    highs = [float(k[2]) for k in klines]
    lows = [float(k[3]) for k in klines]
    closes = [float(k[4]) for k in klines]
    
    tr, plus_dm, minus_dm = [], [], []
    for i in range(1, len(klines)):
        tr.append(max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1])))
        up_move = highs[i] - highs[i-1]
        down_move = lows[i-1] - lows[i]
        plus_dm.append(up_move if up_move > down_move and up_move > 0 else 0)
        minus_dm.append(down_move if down_move > up_move and down_move > 0 else 0)

    smoothed_tr = sum(tr[:period])
    smoothed_plus = sum(plus_dm[:period])
    smoothed_minus = sum(minus_dm[:period])
    dx = []
    
    for i in range(period, len(tr)):
        smoothed_tr = smoothed_tr - (smoothed_tr/period) + tr[i]
        smoothed_plus = smoothed_plus - (smoothed_plus/period) + plus_dm[i]
        smoothed_minus = smoothed_minus - (smoothed_minus/period) + minus_dm[i]
        
        di_plus = 100 * (smoothed_plus / smoothed_tr) if smoothed_tr > 0 else 0
        di_minus = 100 * (smoothed_minus / smoothed_tr) if smoothed_tr > 0 else 0
        dx_val = 100 * abs(di_plus - di_minus) / (di_plus + di_minus) if (di_plus + di_minus) > 0 else 0
        dx.append(dx_val)
        
    adx = sum(dx[:period]) / period
    for i in range(period, len(dx)):
        adx = ((adx * (period - 1)) + dx[i]) / period
        
    atr = smoothed_tr / period
    return adx, atr

def calculate_rsi(closes, period=14):
    if len(closes) < period + 1: return 50
    gains, losses = 0, 0
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i-1]
        if diff > 0: gains += diff
        else: losses -= diff
    avg_gain = gains / period
    avg_loss = abs(losses) / period
    if avg_loss == 0: return 100
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def sniper_pro_score_analyze(klines):
    if not klines or len(klines) < 100:
        return 0, "WAIT", "ข้อมูลไม่พอ"
    
    closes = [float(k[4]) for k in klines]
    highs = [float(k[2]) for k in klines]
    lows = [float(k[3]) for k in klines]
    opens = [float(k[1]) for k in klines]
    
    score = 0
    reasons = []
    
    ema_20 = calculate_ema(closes, 20)
    ema_50 = calculate_ema(closes, 50)
    
    m5_closes = closes[4::5] 
    m5_ema_9 = calculate_ema(m5_closes, 9) if len(m5_closes) >= 9 else closes[-1]
    m5_ema_21 = calculate_ema(m5_closes, 21) if len(m5_closes) >= 21 else closes[-1]
    
    trend = "CALL" if ema_20 > ema_50 else "PUT"
    m5_trend = "CALL" if m5_ema_9 > m5_ema_21 else "PUT"
    
    if trend == m5_trend:
        score += 30
        reasons.append(f"EMA(M1) สอดคล้องกับเทรนด์ M5 ({trend})")
    else:
        score -= 10
        reasons.append(f"ระวังเทรนด์ M1 ขัดแย้งกับ M5")

    adx, atr = calculate_adx_atr_real(klines)
    if adx >= 25:
        score += 15
        reasons.append(f"ADX ทรงพลัง ({adx:.1f})")
    else:
        score -= 10
        reasons.append(f"ตลาดอ่อนแรง ADX={adx:.1f}")
        
    if atr > 0.005:  
        score += 5
        reasons.append("Volatility (ATR) ผ่านเกณฑ์")

    rsi = calculate_rsi(closes)
    momentum = closes[-1] - closes[-10] 
    
    if trend == "CALL" and rsi > 55 and momentum > 0:
        score += 20
        reasons.append(f"RSI({rsi:.1f}) & Momentum ยืนยันขาขึ้น")
    elif trend == "PUT" and rsi < 45 and momentum < 0:
        score += 20
        reasons.append(f"RSI({rsi:.1f}) & Momentum ยืนยันขาลง")

    body = abs(closes[-1] - opens[-1])
    total_range = highs[-1] - lows[-1]
    if total_range > 0 and (body / total_range) > 0.35:
        score += 15
        reasons.append("แท่งเทียนมีมวล (ไม่ใช่ Doji)")
    else:
        score -= 15
        reasons.append("เจอแท่ง Doji/ไส้เทียนยาว เสี่ยงโดนหลอก")

    if trend == "CALL" and closes[-1] > max(highs[-21:-1]):
        score += 15
        reasons.append("Breakout ทะลุแนวต้าน")
    elif trend == "PUT" and closes[-1] < min(lows[-21:-1]):
        score += 15
        reasons.append("Breakout ทะลุแนวรับ")

    return score, trend, " | ".join(reasons)

def run_single_pair(pair):
    global stats_cad_jpy
    symbol = pair["symbol"]
    name = pair["name"]
    step = 1
    consecutive_losses = 0
    
    print(f"[{datetime.now().strftime('%H:%M:%S')}] 🛡️ Sniper Pro (CAD/JPY) เริ่มทำงาน: {name}")
    
    while True:
        try:
            if consecutive_losses >= 2:
                print(f"\n⚠️ พลาดติดต่อกัน {consecutive_losses} ไม้! ระบบบังคับพัก 5 นาที...")
                time.sleep(300)
                consecutive_losses = 0
                
            current_time = datetime.now()
            seconds_to_wait = 60 - current_time.second
            if seconds_to_wait > 2: time.sleep(seconds_to_wait)
                
            klines = get_market_data_cadjpy(symbol)
            if not klines:
                time.sleep(5)
                continue
                
            score, action, reason = sniper_pro_score_analyze(klines)
            now_str = datetime.now().strftime('%H:%M:%S')
            
            if score >= 75:
                target_minute = datetime.now() + timedelta(minutes=1)
                prep_target_time = target_minute.replace(second=0, microsecond=0)
                action_icon = "🟢 ซื้อขึ้น (CALL)" if action == "CALL" else "🔴 ซื้อลง (PUT)"
                
                print(f"\n🔔 [{now_str}] ⚠️ แจ้งเตือน! [{name}] Score: {score}/100")
                print(f"🎯 ทิศทาง: {action} | รอเข้าซื้อที่เวลา {prep_target_time.strftime('%H:%M:%S')}")
                print(f"📋 ลอจิก: {reason}")
                
                discord_msg = (
                    f"🇨🇦🇯🇵 **[แจ้งเตือน Sniper Pro CAD/JPY]**\n"
                    f"⏰ **เวลาเข้าซื้อจริง:** `{prep_target_time.strftime('%H:%M:%S')}` น.\n"
                    f"👉 **ทิศทาง:** **{action_icon}** | ไม้ที่ {step}/3 ({stakes[step]} บ.)\n"
                    f"🎯 **Score:** {score}/100 | ลอจิก: {reason}"
                )
                send_discord_alert(discord_msg)
                
                while datetime.now() < prep_target_time:
                    time.sleep(0.01)
                    
                print(f"🔄 กำลังดึงข้อมูลล่าสุดเพื่อยืนยันออเดอร์...")
                klines_new = get_market_data_cadjpy(symbol)
                
                if klines_new:
                    new_score, new_action, _ = sniper_pro_score_analyze(klines_new)
                    last_close = float(klines_new[-1][4])
                    
                    if new_action == action and new_score >= 50:
                        print(f"--------------------------------------------------")
                        print(f"🚀 [ยิงออเดอร์จริง!] [{name}] Score ปัจจุบัน: {new_score}")
                        print(f"⏰ เวลา: {datetime.now().strftime('%H:%M:%S')} | ไม้ที่ {step}/3")
                        print(f"ทิศทาง: {action_icon} (ยอด {stakes[step]} บาท)")
                        print(f"ราคา API ล่าสุด: {last_close:.4f}")
                        print(f"--------------------------------------------------\n")
                        
                        send_discord_alert(f"🚀 **[CAD/JPY EXECUTE ENTRY]** เปิดออร์เดอร์จริง `{action_icon}` ไม้ที่ {step}/3 ({stakes[step]} บ.)")
                        
                        time.sleep(58) 
                        
                        result_klines = get_market_data_cadjpy(symbol)
                        if result_klines:
                            p_open = float(result_klines[-1][1])
                            p_close = float(result_klines[-1][4])
                            
                            if p_close == p_open:
                                stats_cad_jpy["draw"] += 1
                                log_trade_to_db(str(datetime.now()), name, "Sniper Pro CAD/JPY", action, stakes[step], "DRAW", 0.0)
                                send_discord_alert(f"🟡 **[CAD/JPY RESULT]** เสมอ! 🟡")
                            else:
                                is_win = (p_close > p_open) if action == "CALL" else (p_close < p_open)
                                pl = stakes[step] * 0.85 if is_win else -stakes[step]
                                result_type = "WIN" if is_win else "LOSS"
                                log_trade_to_db(str(datetime.now()), name, "Sniper Pro CAD/JPY", action, stakes[step], result_type, pl)

                                if is_win:
                                    stats_cad_jpy["win"] += 1
                                    consecutive_losses = 0
                                    print(f"[{datetime.now().strftime('%H:%M:%S')}] 🟢 วินไม้ที่ {step} สำเร็จ! ✨")
                                    send_discord_alert(f"✅ **[CAD/JPY WIN]** ชนะไม้ที่ {step} 🎉 (+{pl} บาท)")
                                    print_cad_jpy_stats_summary()
                                    step = 1
                                else:
                                    stats_cad_jpy["loss"] += 1
                                    consecutive_losses += 1
                                    send_discord_alert(f"⚠️ **[CAD/JPY LOSS]** พลาดไม้ที่ {step} ({pl} บาท)")
                                    if step < 3:
                                        print(f"[{datetime.now().strftime('%H:%M:%S')}] 🔴 พลาดไม้ที่ {step} ➔ ทบเงินไม้ที่ {step + 1} ({stakes[step + 1]} บาท)")
                                        step += 1
                                    else:
                                        print(f"[{datetime.now().strftime('%H:%M:%S')}] 🔴 พลาดครบ 3 ไม้ ล้างกระดานเริ่มต้นใหม่")
                                        print_cad_jpy_stats_summary()
                                        step = 1
                    else:
                        print(f"❌ [{datetime.now().strftime('%H:%M:%S')}] ยกเลิกการเข้าเทรด! ตลาดกลับตัวหลังผ่านไป 2 นาที (Score ร่วงเหลือ {new_score})")
            else:
                print(f"[{now_str}] 🔎 {name} | Score: {score}/100 ➔ รอก่อน | {reason}")
                time.sleep(1)
        except Exception as e:
            print(f"Error in CAD/JPY Thread: {e}")
            time.sleep(5)

# ==========================================
# 🇬🇧/🇯🇵 ระบบเฉพาะคู่เงิน GBP/JPY (8 Indicators + Discord)
# ==========================================
def calculate_gbp_advanced_indicators(df):
    if df is None or len(df) < 50: return None
    klines = df.values.tolist()
    closes = [float(k[4]) for k in klines]
    highs = [float(k[2]) for k in klines]
    lows = [float(k[3]) for k in klines]
    volumes = [float(k[5]) for k in klines]

    def ema(data, period):
        alpha = 2 / (period + 1)
        val = sum(data[:period]) / period
        res = [val]
        for x in data[period:]:
            val = (x * alpha) + (val * (1 - alpha))
            res.append(val)
        return res

    ema20 = ema(closes, 20)[-1]
    ema50 = ema(closes, 50)[-1]

    tr_list = []
    for i in range(1, len(klines)):
        tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
        tr_list.append(tr)
    atr = sum(tr_list[-14:]) / 14 if len(tr_list) >= 14 else 0.01

    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i-1]
        gains.append(diff if diff > 0 else 0)
        losses.append(abs(diff) if diff < 0 else 0)
    avg_gain = sum(gains[-14:]) / 14
    avg_loss = sum(losses[-14:]) / 14
    rs = (avg_gain / avg_loss) if avg_loss != 0 else 100
    rsi = 100 - (100 / (1 + rs))

    ema12_vals = ema(closes, 12)
    ema26_vals = ema(closes, 26)
    macd_line = [e12 - e26 for e12, e26 in zip(ema12_vals[-len(ema26_vals):], ema26_vals)]
    macd_signal = ema(macd_line, 9)[-1]
    curr_macd = macd_line[-1]

    cum_vol_price = sum(((h + l + c) / 3) * v for h, l, c, v in zip(highs[-20:], lows[-20:], closes[-20:], volumes[-20:]))
    cum_vol = sum(volumes[-20:])
    vwap = (cum_vol_price / cum_vol) if cum_vol != 0 else closes[-1]

    plus_dm, minus_dm = [], []
    for i in range(1, len(klines)):
        up_move = highs[i] - highs[i-1]
        down_move = lows[i-1] - lows[i]
        plus_dm.append(up_move if (up_move > down_move and up_move > 0) else 0)
        minus_dm.append(down_move if (down_move > up_move and down_move > 0) else 0)
    
    smooth_tr = sum(tr_list[-14:])
    smooth_pdm = sum(plus_dm[-14:])
    smooth_mdm = sum(minus_dm[-14:])
    
    p_di = (smooth_pdm / smooth_tr * 100) if smooth_tr != 0 else 0
    m_di = (smooth_mdm / smooth_tr * 100) if smooth_tr != 0 else 0
    dx = (abs(p_di - m_di) / (p_di + m_di) * 100) if (p_di + m_di) != 0 else 0
    adx = dx

    support = min(lows[-20:])
    resistance = max(highs[-20:])

    return {
        "ema20": ema20, "ema50": ema50, "atr": atr, "rsi": rsi,
        "macd": curr_macd, "macd_signal": macd_signal, "vwap": vwap,
        "adx": adx, "support": support, "resistance": resistance
    }

def analyze_gbp_jpy_signal(df, pair_name="GBP/JPY"):
    ind = calculate_gbp_advanced_indicators(df)
    if not ind:
        return "CALL", 85.0, f"[{pair_name}] ข้อมูลกำลังโหลด ใช้ค่าเริ่มต้น"

    p_close = float(df.iloc[-1]['close'])
    call_score, put_score = 0, 0

    if ind["ema20"] > ind["ema50"]: call_score += 1
    else: put_score += 1

    if p_close > ind["vwap"]: call_score += 1
    else: put_score += 1

    if ind["rsi"] > 50: call_score += 1
    else: put_score += 1

    if ind["macd"] > ind["macd_signal"]: call_score += 1
    else: put_score += 1

    if ind["adx"] > 20:
        if call_score > put_score: call_score += 1
        else: put_score += 1

    dist_to_sup = abs(p_close - ind["support"])
    dist_to_res = abs(p_close - ind["resistance"])
    if dist_to_sup < dist_to_res: call_score += 1
    else: put_score += 1

    if call_score >= put_score:
        confidence = 85.0 + (call_score * 2.0)
        reason = f"EMA/VWAP ขาขึ้น | RSI:{ind['rsi']:.1f} | ADX:{ind['adx']:.1f}"
        return "CALL", confidence, reason
    else:
        confidence = 85.0 + (put_score * 2.0)
        reason = f"EMA/VWAP ขาลง | RSI:{ind['rsi']:.1f} | ADX:{ind['adx']:.1f}"
        return "PUT", confidence, reason

def run_strategy_gbp_jpy():
    global stats_gbp
    name = "GBP/JPY"
    symbol = "GBPJPY=X"
    step = 1
    
    print(f"[{datetime.now().strftime('%H:%M:%S')}] ⚡ เริ่มระบบวิเคราะห์ (8 Indicators + แจ้งเตือน Discord ตรงเวลา): {name}\n")
    last_processed_minute = -1
    
    while True:
        try:
            now = datetime.now()
            if now.minute != last_processed_minute:
                df = get_market_data(symbol, "1m", 100)
                if df is not None:
                    action, win_prob, reason = analyze_gbp_jpy_signal(df, name)
                    entry_time = (now + timedelta(minutes=1)).replace(second=0, microsecond=0)
                    action_icon = "🟢 ซื้อขึ้น (CALL)" if action == "CALL" else "🔴 ซื้อลง (PUT)"
                    
                    print(f"\n==================================================")
                    print(f"[{now.strftime('%H:%M:%S')}] ⚠️ [แจ้งเตรียมตัวเข้าซื้อ] {name}")
                    print(f"⏰ เวลาเข้าซื้อจริง: {entry_time.strftime('%H:%M:%S')} น.")
                    print(f"👉 ทิศทางเข้าซื้อ: {action_icon} | ไม้ที่ {step}/3 ({stakes[step]} บาท)")
                    print(f"📊 ความมั่นใจ: {win_prob:.1f}% | เหตุผล (8 เครื่องมือ): {reason}")
                    print(f"==================================================")
                    
                    alert_msg = (
                        f"🇬🇧🇯🇵 **[แจ้งเตือนวิเคราะห์ GBP/JPY (8 Indicators)]**\n"
                        f"⏰ **เวลาเข้าซื้อจริง:** `{entry_time.strftime('%H:%M:%S')}` น.\n"
                        f"👉 **ทิศทางเข้าซื้อ:** **{action_icon}** | ไม้ที่ {step}/3 ({stakes[step]} บ.)\n"
                        f"📊 **ความมั่นใจ:** {win_prob:.1f}% | {reason}"
                    )
                    send_discord_alert(alert_msg)
                    
                    while datetime.now() < entry_time:
                        time.sleep(0.01)
                        
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] 🚀 [EXECUTE ENTRY] เปิดออร์เดอร์เรียบร้อย: {action_icon}")
                    last_processed_minute = entry_time.minute
                    
                    time.sleep(58)
                    result_df = get_market_data(symbol, "1m", 2)
                    
                    if result_df is not None and len(result_df) >= 2:
                        p_open, p_close = float(result_df.iloc[-1]['open']), float(result_df.iloc[-1]['close'])
                        
                        if p_close == p_open:
                            result_type = "DRAW"
                            with stats_lock: stats_gbp["draw"] += 1
                            log_trade_to_db(str(datetime.now()), name, "GBP/JPY 8 Indicators", action, stakes[step], "DRAW", 0.0)
                            print(f"[{datetime.now().strftime('%H:%M:%S')}] 🟡 [{name}] เสมอ!")
                            send_discord_alert(f"🟡 **[GBP/JPY RESULT]** เสมอ! 🟡")
                        else:
                            is_win = (p_close > p_open) if action == "CALL" else (p_close < p_open)
                            pl = stakes[step] * 0.85 if is_win else -stakes[step]
                            result_type = "WIN" if is_win else "LOSS"
                            log_trade_to_db(str(datetime.now()), name, "GBP/JPY 8 Indicators", action, stakes[step], result_type, pl)
                            
                            if is_win:
                                with stats_lock: stats_gbp["win"] += 1  
                                print(f"[{datetime.now().strftime('%H:%M:%S')}] 🟢 [{name}] วินไม้ที่ {step}!")
                                send_discord_alert(f"✅ **[GBP/JPY WIN]** ชนะไม้ที่ {step} 🎉 (+{pl} บาท)")
                            else:
                                with stats_lock: stats_gbp["loss"] += 1
                                print(f"[{datetime.now().strftime('%H:%M:%S')}] 🔴 [{name}] พลาดไม้ที่ {step}")
                                send_discord_alert(f"⚠️ **[GBP/JPY LOSS]** พลาดไม้ที่ {step} ({pl} บาท)")

                        print_gbp_stats_summary()
                        
                        if result_type in ["WIN", "DRAW"]:
                            step = 1
                        else:
                            step = step + 1 if step < 3 else 1
            time.sleep(0.2)
        except Exception as e:
            print(f"Error in GBP/JPY Thread: {e}")
            time.sleep(5)

# ==========================================
# 💎 HELPER & CORE LOGIC สำหรับสูตรที่7 A+
# ==========================================
def calculate_ema_s7(data, period):
    if len(data) < period: return [data[-1]] * len(data)
    k = 2 / (period + 1)
    ema = [sum(data[:period]) / period]
    for price in data[period:]:
        ema.append((price * k) + (ema[-1] * (1 - k)))
    return ema

def calculate_rsi_s7(closes, period=14):
    if len(closes) < period + 1: return 50.0
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i-1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0: return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def calculate_atr_s7(klines, period=14):
    if len(klines) < period + 1: return 0.0, 0.0
    tr_list = []
    for i in range(1, len(klines)):
        h, l, prev_c = float(klines[i][2]), float(klines[i][3]), float(klines[i-1][4])
        tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
        tr_list.append(tr)
    atr_current = sum(tr_list[-period:]) / period
    atr_avg_20 = sum(tr_list[-20:]) / len(tr_list[-20:]) if len(tr_list) >= 20 else atr_current
    return atr_current, atr_avg_20

def calculate_adx_s7(klines, period=14):
    if len(klines) < period * 2: return 25.0
    tr_l, pdm_l, mdm_l = [], [], []
    for i in range(1, len(klines)):
        h, l, p_h, p_l, p_c = float(klines[i][2]), float(klines[i][3]), float(klines[i-1][2]), float(klines[i-1][3]), float(klines[i-1][4])
        tr = max(h - l, abs(h - p_c), abs(l - p_c))
        up_move = h - p_h
        down_move = p_l - l
        pdm = up_move if (up_move > down_move and up_move > 0) else 0
        mdm = down_move if (down_move > up_move and down_move > 0) else 0
        tr_l.append(tr); pdm_l.append(pdm); mdm_l.append(mdm)
    
    str_val = sum(tr_l[:period])
    spdm = sum(pdm_l[:period])
    smdm = sum(mdm_l[:period])
    
    dx_list = []
    for i in range(period, len(tr_l)):
        str_val = str_val - (str_val / period) + tr_l[i]
        spdm = spdm - (spdm / period) + pdm_l[i]
        smdm = smdm - (smdm / period) + mdm_l[i]
        pdi = (spdm / str_val) * 100 if str_val > 0 else 0
        mdi = (smdm / str_val) * 100 if str_val > 0 else 0
        di_diff = abs(pdi - mdi)
        di_sum = pdi + mdi
        dx = (di_diff / di_sum) * 100 if di_sum > 0 else 0
        dx_list.append(dx)
    
    return sum(dx_list[-period:]) / period if dx_list else 25.0

def get_market_data_s7(symbol):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval={INTERVAL}&range=1d"
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    for _ in range(3):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=5) as response:
                res = json.loads(response.read().decode())
                chart_data = res['chart']['result'][0]
                timestamps = chart_data['timestamp']
                quote = chart_data['indicators']['quote'][0]
                opens, highs, lows, closes = quote['open'], quote['high'], quote['low'], quote['close']
                
                klines = []
                for i in range(len(timestamps)):
                    if None in (opens[i], highs[i], lows[i], closes[i]): continue
                    klines.append([timestamps[i] * 1000, opens[i], highs[i], lows[i], closes[i]])
                return klines[-220:]
        except Exception:
            time.sleep(0.5)
    return None

def analyze_signal_s7(klines, pair_name):
    if not klines or len(klines) < 200:
        return None, 0, "รอโหลดข้อมูลกราฟให้ครบสำหรับ EMA200..."

    now_time = datetime.now().time()
    if datetime.strptime("00:00", "%H:%M").time() <= now_time <= datetime.strptime("06:00", "%H:%M").time():
        return None, 0, "นอกเวลาสแกนหลัก (Session Filter: งดช่วง 00:00 - 06:00 น.)"

    closes = [float(k[4]) for k in klines]
    ema20 = calculate_ema_s7(closes, 20)[-1]
    ema50 = calculate_ema_s7(closes, 50)[-1]
    ema200 = calculate_ema_s7(closes, 200)[-1]
    
    trend = None
    if ema20 > ema50 > ema200:
        trend = "CALL"
    elif ema20 < ema50 < ema200:
        trend = "PUT"
    else:
        return None, 0, f"EMA ไม่เรียงตัวสมบูรณ์ (E20: {ema20:.2f}, E50: {ema50:.2f}, E200: {ema200:.2f})"

    adx = calculate_adx_s7(klines)
    if adx < 20:
        return None, 0, f"ADX ต่ำกว่า 20 ตลาดชะลอตัว (ADX: {adx:.1f})"

    atr_curr, atr_avg = calculate_atr_s7(klines)
    if atr_curr < atr_avg:
        return None, 0, f"ความผันผวนต่ำกว่าค่าเฉลี่ย (ATR: {atr_curr:.4f} < Avg: {atr_avg:.4f})"

    score = 0
    reasons = []

    if adx >= 25:
        score += 25
        reasons.append(f"ADX แข็งแกร่ง ({adx:.1f})")
    else:
        score += 15
        reasons.append(f"ADX ปานกลาง ({adx:.1f})")

    score += 25
    reasons.append(f"เทรนด์ชัดเจน ({trend})")

    rsi = calculate_rsi_s7(closes)
    if trend == "CALL" and 55 <= rsi <= 70:
        score += 15
        reasons.append(f"RSI ขาขึ้นสวย ({rsi:.1f})")
    elif trend == "PUT" and 30 <= rsi <= 45:
        score += 15
        reasons.append(f"RSI ขาลงสวย ({rsi:.1f})")
    elif abs(rsi - 50) < 3:
        return None, 0, f"RSI ใกล้ 50 ตลาดไม่มีทิศทาง ({rsi:.1f})"

    prev_k = klines[-1]
    p_open, p_high, p_low, p_close = float(prev_k[1]), float(prev_k[2]), float(prev_k[3]), float(prev_k[4])
    body = abs(p_close - p_open)
    total_range = p_high - p_low
    
    if total_range == 0 or body / total_range < 0.2:
        return None, 0, "พบแท่ง Doji/Range แคบผิดปกติ"
        
    is_bull = p_close > p_open
    if (trend == "CALL" and is_bull and body/total_range >= 0.55) or \
       (trend == "PUT" and not is_bull and body/total_range >= 0.55):
        score += 15
        reasons.append(f"PA แท่งเต็มเนื้อแน่น ({body/total_range*100:.0f}%)")

    highs_3_5 = [float(k[2]) for k in klines[-6:-1]]
    lows_3_5 = [float(k[3]) for k in klines[-6:-1]]
    
    if trend == "CALL" and p_close > max(highs_3_5):
        score += 20
        reasons.append("Breakout ทะลุ High 5 แท่งก่อนหน้า")
    elif trend == "PUT" and p_close < min(lows_3_5):
        score += 20
        reasons.append("Breakout ทะลุ Low 5 แท่งก่อนหน้า")

    if score >= 85:
        return trend, score, f"[{pair_name}] " + " | ".join(reasons)
    else:
        return None, score, f"คะแนนความมั่นใจไม่ถึงเกณฑ์ ({score}/100)"

def pair_worker_strategy7(pair):
    global stats_strat7
    symbol = pair["symbol"]
    name = pair["name"]
    
    step = 1
    last_checked_minute = -1
    print(f"[{datetime.now().strftime('%H:%M:%S')}] ⚡ เริ่มระบบเข้าซื้อ [สูตรที่7 A+] {name} (โหมดแจ้งเตือนตรงเวลา)...\n")
    
    while True:
        try:
            now = datetime.now()
            
            if now.minute != last_checked_minute:
                last_checked_minute = now.minute
                klines = get_market_data_s7(symbol)
                
                curr_price = float(klines[-1][4]) if klines else 0.0
                action, win_prob, reason = analyze_signal_s7(klines, name)
                
                print(f"--------------------------------------------------")
                print(f"⏱️ [{now.strftime('%H:%M:%S')}] สแกนกราฟ [สูตรที่7 A+]: {name} | ราคาปิดแท่งล่าสุด: {curr_price:.3f}")
                
                if action:
                    entry_time = (now + timedelta(minutes=1)).replace(second=0, microsecond=0)
                    target_str = entry_time.strftime('%H:%M')
                    action_text = "ซื้อขึ้น (CALL)" if action == "CALL" else "ซื้อลง (PUT)"
                    action_icon = "🟢" if action == "CALL" else "🔴"
                    
                    print(f"\n==========================================")
                    print(f"📢 แจ้งเตือนออร์เดอร์ใหม่ [สูตรที่7 A+] [{name}]")
                    print(f"⏰ เวลาเข้าซื้อ: {target_str} น. {action_text}")
                    print(f"👉 สัญญาณ: {action_icon} {action_text}")
                    print(f"💰 การลงทุน: ไม้ที่ {step}/3 (ยอด {stakes[step]} บาท)")
                    print(f"🎯 คะแนนความมั่นใจ: {win_prob}/100")
                    print(f"💡 เหตุผล: {reason}")
                    print(f"==========================================\n")
                    
                    alert_msg = (
                        f"🔥 **[แจ้งเตือนสัญญาณ สูตรที่7 A+]**\n"
                        f"🪙 **คู่เงิน:** `{name}`\n"
                        f"⏰ **เวลาเข้าซื้อ:** `{target_str}` น.\n"
                        f"👉 **สัญญาณ:** {action_icon} **{action_text}** (ไม้ที่ {step}/3 - {stakes[step]} บ.)\n"
                        f"🎯 **ความมั่นใจ:** {win_prob}/100\n"
                        f"💡 **เหตุผล:** {reason}"
                    )
                    send_discord_alert(alert_msg)
                    
                    while datetime.now() < entry_time:
                        time.sleep(0.01)
                    
                    print(f"\n🚀 [{datetime.now().strftime('%H:%M:%S')}] [EXECUTE ENTRY] เปิดออร์เดอร์เวลา {target_str} น. ไม้ที่ {step}/3 [{name}]: {action_text} ({stakes[step]} บาท)")
                    
                    time.sleep(58)
                    result_klines = get_market_data_s7(symbol)
                    
                    if result_klines:
                        completed_k = result_klines[-1]
                        p_open = float(completed_k[1])
                        p_close = float(completed_k[4])
                        
                        if p_close == p_open:
                            result_type = "DRAW"
                            with stats_lock: stats_strat7["draw"] += 1
                            log_trade_to_db(str(datetime.now()), name, "สูตรที่7 A+", action, stakes[step], "DRAW", 0.0)
                            print(f"[{datetime.now().strftime('%H:%M:%S')}] 🟡 [สูตรที่7 A+] [{name}] เสมอ!")
                            send_discord_alert(f"🟡 **[สูตรที่7 A+ RESULT]** `{name}` เสมอ! 🟡")
                        else:
                            is_win = (p_close > p_open) if action == "CALL" else (p_close < p_open)
                            pl = stakes[step] * 0.85 if is_win else -stakes[step]
                            result_type = "WIN" if is_win else "LOSS"
                            log_trade_to_db(str(datetime.now()), name, "สูตรที่7 A+", action, stakes[step], result_type, pl)
                            
                            if is_win:
                                with stats_lock: stats_strat7["win"] += 1
                                print(f"[{datetime.now().strftime('%H:%M:%S')}] 🟢 [สูตรที่7 A+] [{name}] วินไม้ที่ {step}!")
                                send_discord_alert(f"✅ **[สูตรที่7 A+ WIN]** `{name}` ชนะไม้ที่ {step} 🎉 (+{pl} บาท)")
                            else:
                                with stats_lock: stats_strat7["loss"] += 1
                                print(f"[{datetime.now().strftime('%H:%M:%S')}] 🔴 [สูตรที่7 A+] [{name}] พลาดไม้ที่ {step}")
                                send_discord_alert(f"⚠️ **[สูตรที่7 A+ LOSS]** `{name}` พลาดไม้ที่ {step} ({pl} บาท)")

                        print_strat7_stats_summary()
                        
                        if result_type in ["WIN", "DRAW"]:
                            step = 1
                        else:
                            if step < 3:
                                print(f"[{datetime.now().strftime('%H:%M:%S')}] 🔍 [Re-entry Protection] ตรวจสอบเงื่อนไขใหม่ก่อนทบไม้ที่ {step+1}...")
                                check_klines = get_market_data_s7(symbol)
                                re_action, re_score, re_reason = analyze_signal_s7(check_klines, name)
                                
                                if re_action == action and re_score >= 85:
                                    step += 1
                                    print(f"[{datetime.now().strftime('%H:%M:%S')}] ✅ เงื่อนไขยังสมบูรณ์ เตรียมทบไม้ที่ {step}/3 (ยอด {stakes[step]} บาท)")
                                else:
                                    print(f"[{datetime.now().strftime('%H:%M:%S')}] ⚠️ แนวโน้มเปลี่ยน/ความมั่นใจลดลง ({re_score}/100) -> ยกเลิกการทบ รีเซ็ตกลับไม้ 1")
                                    step = 1
                            else:
                                print(f"[{datetime.now().strftime('%H:%M:%S')}] 🔴 [{name}] ครบ 3 ไม้ รีเซ็ตเริ่มไม้ 1 ใหม่...")
                                step = 1
                else:
                    print(f"📊 สถานะ: {reason}")
                    print(f"--------------------------------------------------")

            time.sleep(0.2)
        except Exception as e:
            print(f"Error in Strategy 7 Worker: {e}")
            time.sleep(5)

# ==========================================
# 🎯 สูตรที่ 1: สูตร 6 คู่เงิน 1m
# ==========================================
def analyze_advanced_filters(df):
    if df is None or len(df) < 50: return None, "ข้อมูลกราฟไม่พอ"
    df = calculate_all_indicators(df)
    latest, prev = df.iloc[-1], df.iloc[-2]
    
    body = abs(latest['close'] - latest['open'])
    is_bullish_engulfing = (latest['close'] > latest['open']) and (prev['close'] < prev['open']) and (latest['close'] >= prev['open']) and (latest['open'] <= prev['close'])
    is_bearish_engulfing = (latest['close'] < latest['open']) and (prev['close'] > prev['open']) and (latest['close'] <= prev['open']) and (latest['close'] >= prev['close'])
    
    trend_up = latest['EMA20'] > latest['EMA50']
    trend_down = latest['EMA20'] < latest['EMA50']
    rsi_ok_buy = 50 < latest['RSI'] < 75
    rsi_ok_sell = 25 < latest['RSI'] < 50
    macd_buy = latest['MACD'] > latest['MACD_Signal']
    macd_sell = latest['MACD'] < latest['MACD_Signal']
    adx_ok = latest['ADX'] > 20
    vol_ok = latest['volume'] > latest['Vol_Avg']
    atr_ok = latest['ATR'] > 0.00001
    
    distance_to_res = latest['Resistance'] - latest['close']
    distance_to_sup = latest['close'] - latest['Support']
    
    if trend_up and rsi_ok_buy and macd_buy and adx_ok and vol_ok and atr_ok and distance_to_res > (body * 1.2):
        if is_bullish_engulfing or body > (latest['ATR'] * 0.8):
            return "CALL", f"RSI:{latest['RSI']:.1f} | ADX:{latest['ADX']:.1f} | ผ่าน 8 ข้อกรอง"
            
    if trend_down and rsi_ok_sell and macd_sell and adx_ok and vol_ok and atr_ok and distance_to_sup > (body * 1.2):
        if is_bearish_engulfing or body > (latest['ATR'] * 0.8):
            return "PUT", f"RSI:{latest['RSI']:.1f} | ADX:{latest['ADX']:.1f} | ผ่าน 8 ข้อกรอง"
            
    return None, "รอจังหวะตลาดฟอร์มตัว"

def run_strategy_sniper_5m():
    print("🎯 [สูตรที่ 1: สูตร 6 คู่เงิน 1m] เริ่มทำงานสแกน 6 คู่เงินหลักเรียบร้อย")
    while True:
        try:
            now = datetime.now()
            target_time = (now + timedelta(minutes=1)).replace(second=0, microsecond=0)
            warning_time = target_time - timedelta(seconds=15)
            
            if warning_time <= now < warning_time + timedelta(seconds=2):
                next_target_str = target_time.strftime("%H:%M:%S")
                signals_found = 0
                alert_msg = f"🎯 **[สูตร 6 คู่เงิน 1m - สัญญาณใกล้เข้าซื้อ]**\n⏰ เวลาเข้าซื้อจริง: `{next_target_str}` น.\n"
                
                valid_signals = []
                for pair in PAIRS:
                    symbol, name = pair["symbol"], pair["name"]
                    if time.time() < sniper_traders[symbol]["rest_until"]: continue
                    df = get_market_data(symbol, "1m", 100)
                    action, reason = analyze_advanced_filters(df)
                    
                    st = sniper_traders[symbol]
                    if not st["in_progress"] and action is None: continue
                    if st["in_progress"] and action is None: action, reason = st["active_action"], "ไม้ทบต่อเนื่องตามแผน"
                    
                    if action is not None:
                        signals_found += 1
                        st["in_progress"] = True
                        if st["step"] == 1: st["active_action"] = action
                        icon = "🟢 CALL" if st["active_action"] == "CALL" else "🔴 PUT"
                        alert_msg += f"- `{name}` [ไม้ {st['step']}/3] ➔ **{icon}** ({stakes[st['step']]} บ.) | {reason}\n"
                        valid_signals.append((name, symbol, st["active_action"], st["step"]))
                
                if signals_found > 0:
                    send_discord_alert(alert_msg)
                    while datetime.now() < target_time: time.sleep(0.01)
                    time.sleep(2)
                    
                    for name, symbol, action, step in valid_signals:
                        res_df = get_market_data(symbol, "1m", 2)
                        st = sniper_traders[symbol]
                        if res_df is not None and len(res_df) >= 2:
                            p_o, p_c = float(res_df.iloc[-1]['open']), float(res_df.iloc[-1]['close'])
                            
                            if p_c == p_o:
                                log_trade_to_db(str(datetime.now()), name, "สูตร 6 คู่เงิน 1m", action, stakes[step], "DRAW", 0.0)
                                send_discord_alert(f"🟡 **[สูตร 6 คู่เงิน 1m RESULT]** `{name}` เสมอ! 🟡")
                                st["step"], st["rest_until"], st["in_progress"] = 1, time.time() + 60, False
                            else:
                                is_win = (p_c > p_o) if action == "CALL" else (p_c < p_o)
                                pl = stakes[step] * 0.85 if is_win else -stakes[step]
                                log_trade_to_db(str(datetime.now()), name, "สูตร 6 คู่เงิน 1m", action, stakes[step], "WIN" if is_win else "LOSS", pl)
                                
                                if is_win:
                                    send_discord_alert(f"✅ **[สูตร 6 คู่เงิน 1m WIN]** `{name}` ชนะไม้ {step} 🎉 (+{pl} บ.)")
                                    st["step"], st["rest_until"], st["in_progress"] = 1, time.time() + 180, False
                                else:
                                    if st["step"] < 3:
                                        st["step"] += 1
                                        send_discord_alert(f"⚠️ **[สูตร 6 คู่เงิน 1m LOSS]** `{name}` พลาดไม้ {step} ➔ เตรียมทบไม้ที่ {st['step']}")
                                    else:
                                        send_discord_alert(f"🛑 **[สูตร 6 คู่เงิน 1m CUT]** `{name}` แพ้ครบ 3 ไม้ พักชั่วคราว")
                                        st["step"], st["rest_until"], st["in_progress"] = 1, time.time() + 300, False
                            print_total_system_stats_summary()
            time.sleep(0.5)
        except Exception as e:
            print(f"Error in Strategy 1 Thread: {e}")
            time.sleep(5)

# ==========================================
# 🧠 สูตรที่ 2: ULTIMATE SMC 1-MINUTE STRATEGY
# ==========================================
def run_strategy_smc_1m():
    print("🧠 [สูตรที่ 2] Ultimate SMC 1 นาที เริ่มทำงาน")
    while True:
        try:
            now = datetime.now()
            target_time = (now + timedelta(minutes=1)).replace(second=0, microsecond=0)
            warning_time = target_time - timedelta(seconds=15)
            
            if warning_time <= now < warning_time + timedelta(seconds=2):
                next_target_str = target_time.strftime("%H:%M:%S")
                signals_found = 0
                alert_msg = f"🧠 **[สูตร 2: SMC 1M - สัญญาณใกล้เข้าซื้อ]**\n⏰ เวลาเข้าซื้อจริง: `{next_target_str}` น.\n"
                
                valid_signals = []
                for pair in PAIRS:
                    symbol, name = pair["symbol"], pair["name"]
                    k1 = get_market_data(symbol, "1m", 150)
                    k5 = get_market_data(symbol, "5m", 30)
                    if k1 is None or k5 is None: continue
                    
                    c_close, c_open = float(k1.iloc[-1]['close']), float(k1.iloc[-1]['open'])
                    htf_bull = float(k5.iloc[-1]['close']) > float(k5.iloc[-1]['open'])
                    
                    action = "CALL" if (c_close > c_open and htf_bull) else ("PUT" if (c_close < c_open and not htf_bull) else None)
                    if action:
                        signals_found += 1
                        icon = "🟢 CALL" if action == "CALL" else "🔴 PUT"
                        alert_msg += f"- `{name}` ➔ **{icon}** (ยอด {BASE_STAKE} บ.)\n"
                        valid_signals.append((name, symbol, action))
                
                if signals_found > 0:
                    send_discord_alert(alert_msg)
                    while datetime.now() < target_time: time.sleep(0.01)
                    time.sleep(2)
                    
                    for name, symbol, action in valid_signals:
                        res = get_market_data(symbol, "1m", 2)
                        if res is not None and len(res) >= 2:
                            p_open, p_close = float(res.iloc[-1]['open']), float(res.iloc[-1]['close'])
                            
                            if p_close == p_open:
                                log_trade_to_db(str(datetime.now()), name, "SMC 1M", action, BASE_STAKE, "DRAW", 0.0)
                                send_discord_alert(f"🟡 **[SMC 1M RESULT]** `{name}` เสมอ! 🟡")
                            else:
                                is_win = (p_close > p_open) if action == "CALL" else (p_close < p_open)
                                pl = BASE_STAKE * 0.85 if is_win else -BASE_STAKE
                                log_trade_to_db(str(datetime.now()), name, "SMC 1M", action, BASE_STAKE, "WIN" if is_win else "LOSS", pl)
                                
                                if is_win:
                                    send_discord_alert(f"✅ **[SMC 1M WIN]** `{name}` ชนะ 🎉 (+{pl} บ.)")
                                else:
                                    send_discord_alert(f"❌ **[SMC 1M LOSS]** `{name}` พลาด (-{BASE_STAKE} บ.)")
                            print_total_system_stats_summary()
            time.sleep(0.5)
        except Exception as e:
            print(f"Error in SMC Thread: {e}")
            time.sleep(5)

# ==========================================
# 📐 สูตรที่ 3: INSTITUTIONAL SMC + ATR SIZING
# ==========================================
def calculate_atr_position_size(df, base_stake=30.0, period=14):
    if df is None or len(df) < period: return base_stake
    curr_atr = df['ATR'].iloc[-1]
    avg_atr = df['ATR'].rolling(window=50).mean().iloc[-1] if len(df) >= 50 else curr_atr
    if pd.isna(curr_atr) or pd.isna(avg_atr) or curr_atr == 0: return base_stake
    return round(base_stake * min(max(avg_atr / curr_atr, 0.5), 1.5), 2)

def get_current_session(now_local):
    h = now_local.hour
    if 14 <= h < 23: return "LONDON"       
    if 19 <= h or h < 4: return "NEW_YORK"  
    return "ASIAN"

def check_time_filters(now_local):
    session = get_current_session(now_local)
    if session not in TRADE_SESSIONS:
        return False, f"นอกเซสชัน ({session})"
    if USE_NEWS_FILTER and now_local.hour in [19, 20, 21] and now_local.minute in [0, 15, 30, 45]:
        return False, "ติด News Filter"
    return True, "OK"

def analyze_strategy3(df_1m, df_5m, df_15m, now_local):
    valid, _ = check_time_filters(now_local)
    if not valid: return None, "นอกเงื่อนไขเวลา"
    if df_1m is None or df_5m is None or df_15m is None: return None, "ข้อมูลไม่พอ"
    
    local_high = df_1m['high'].iloc[-20:-1].max()
    local_low = df_1m['low'].iloc[-20:-1].min()
    c_close = df_1m['close'].iloc[-1]
    
    htf_bull = (df_5m['close'].iloc[-1] > df_5m['open'].iloc[-1]) and (df_15m['close'].iloc[-1] > df_15m['open'].iloc[-1])
    htf_bear = (df_5m['close'].iloc[-1] < df_5m['open'].iloc[-1]) and (df_15m['close'].iloc[-1] < df_15m['open'].iloc[-1])
    
    if htf_bull and c_close > local_high: return "CALL", "HTF Bull + Breakout BOS"
    if htf_bear and c_close < local_low: return "PUT", "HTF Bear + Breakdown BOS"
    return None, "รอโครงสร้างราคา"

def run_strategy_3_multitimeframe():
    print("📐 [สูตรที่ 3] Institutional SMC + ATR เริ่มทำงาน")
    while True:
        try:
            now = datetime.now()
            current_min = now.minute
            
            min_to_next = 5 - (current_min % 5)
            target_time = (now + timedelta(minutes=min_to_next)).replace(second=0, microsecond=0)
            warning_time = target_time - timedelta(seconds=15)
            
            if warning_time <= now < warning_time + timedelta(seconds=2):
                next_target_str = target_time.strftime("%H:%M:%S")
                signals_found = 0
                alert_msg = f"📐 **[สูตร 3: Institutional Pro - เตือนก่อนเข้าซื้อ]**\n⏰ เวลาเข้าซื้อจริง: `{next_target_str}` น.\n"
                
                valid_signals = []
                for pair in PAIRS:
                    symbol, name = pair["symbol"], pair["name"]
                    k1 = get_market_data(symbol, "1m", 150)
                    k5 = get_market_data(symbol, "5m", 30)
                    k15 = get_market_data(symbol, "15m", 30)
                    if k1 is None: continue
                    k1 = calculate_all_indicators(k1)
                    
                    action, reason = analyze_strategy3(k1, k5, k15, now)
                    if action:
                        signals_found += 1
                        stake = calculate_atr_position_size(k1, BASE_STAKE)
                        icon = "🟢 CALL" if action == "CALL" else "🔴 PUT"
                        alert_msg += f"- `{name}` ➔ **{icon}** (ขนาดไม้ ATR: {stake} บ.) | {reason}\n"
                        valid_signals.append((name, symbol, action, k1))
                
                if signals_found > 0:
                    send_discord_alert(alert_msg)
                    while datetime.now() < target_time: time.sleep(0.01)
                    time.sleep(2)
                    
                    for name, symbol, action, k1 in valid_signals:
                        res_df = get_market_data(symbol, "1m", 2)
                        if res_df is not None and len(res_df) >= 2:
                            p_open, p_close = float(res_df.iloc[-1]['open']), float(res_df.iloc[-1]['close'])
                            stake = calculate_atr_position_size(k1, BASE_STAKE)
                            
                            if p_close == p_open:
                                log_trade_to_db(str(datetime.now()), name, "Strategy 3 (MTF)", action, stake, "DRAW", 0.0)
                                send_discord_alert(f"🟡 **[สูตร 3 RESULT]** `{name}` เสมอ! 🟡")
                            else:
                                is_win = (p_close > p_open) if action == "CALL" else (p_close < p_open)
                                pl = stake * 0.85 if is_win else -stake
                                log_trade_to_db(str(datetime.now()), name, "Strategy 3 (MTF)", action, stake, "WIN" if is_win else "LOSS", pl)
                                
                                if is_win:
                                    send_discord_alert(f"✅ **[สูตร 3 WIN]** `{name}` ชนะ 🎉 (+{pl} บ.)")
                                else:
                                    send_discord_alert(f"❌ **[สูตร 3 LOSS]** `{name}` พลาด (-{stake} บ.)")
                            print_total_system_stats_summary()
            time.sleep(0.5)
        except Exception as e:
            print(f"Error in Strategy 3 Thread: {e}")
            time.sleep(5)

# ==========================================
# ⚡ สูตรที่ 4: SNIPER PRO SOL/USD
# ==========================================
def sniper_pro_analyze(df, mtf_df, pair_name):
    if df is None or len(df) < 100 or mtf_df is None or len(mtf_df) < 50: return "C", None, 0.0, "ข้อมูลไม่พอ"
    
    df = calculate_all_indicators(df)
    mtf_df = calculate_all_indicators(mtf_df)
    
    latest = df.iloc[-1]
    mtf_latest = mtf_df.iloc[-1]
    
    ema_9 = df['close'].ewm(span=9, adjust=False).mean().iloc[-1]
    ema_20 = latest['EMA20']
    ema_50 = latest['EMA50']
    ema_200 = df['close'].ewm(span=200, adjust=False).mean().iloc[-1]
    
    mtf_trend_bull = mtf_latest['EMA20'] > mtf_latest['EMA50']
    mtf_trend_bear = mtf_latest['EMA20'] < mtf_latest['EMA50']
    
    adx, atr = latest['ADX'], latest['ATR']
    rsi = latest['RSI']
    is_volume_spike = latest['volume'] >= (latest['Vol_Avg'] * 1.3)
    sup, res = latest['Support'], latest['Resistance']
    current_price = latest['close']
    
    score, action = 0, None
    if ema_9 > ema_20 and ema_20 > ema_50 and current_price > ema_200:
        action = "CALL"
        score += 25
        if mtf_trend_bull: score += 20
        if 55 < rsi < 75: score += 15
        if adx >= 22: score += 15
        if is_volume_spike: score += 10
        if abs(current_price - sup) <= (atr * 0.8): score += 15
    elif ema_9 < ema_20 and ema_20 < ema_50 and current_price < ema_200:
        action = "PUT"
        score += 25
        if mtf_trend_bear: score += 20
        if 25 < rsi < 45: score += 15
        if adx >= 22: score += 15
        if is_volume_spike: score += 10
        if abs(current_price - res) <= (atr * 0.8): score += 15
        
    if adx < 20: return "C", None, atr, "ADX ต่ำกว่า 20"
    grade = "S+" if score >= 90 else ("S" if score >= 80 else ("A+" if score >= 70 else "C"))
    if grade == "C": action = None
    return grade, action, atr, f"[{pair_name}] เกรด {grade} (Score: {score}/100) | ADX:{adx:.1f} | RSI:{rsi:.1f}"

def pair_worker_sol(pair):
    global stats
    symbol, name, color = pair["symbol"], pair["name"], pair["color"]
    step = 1
    print(f"[{datetime.now().strftime('%H:%M:%S')}] 🛡️ [สูตรที่ 4] Sniper Pro ({name}) เริ่มสแกนตลาด...")
    
    while True:
        try:
            df = get_market_data(symbol, INTERVAL, 250)
            mtf_df = get_market_data(symbol, "5m", 100)
            grade, action, atr, reason = sniper_pro_analyze(df, mtf_df, name)
            
            if grade == "C" or not action or grade not in ["A+", "S", "S+"]:
                time.sleep(10)
                continue
                
            now = datetime.now()
            entry_time = (now + timedelta(minutes=1)).replace(second=0, microsecond=0)
            action_icon = "🟢 ซื้อขึ้น (CALL)" if action == "CALL" else "🔴 ซื้อลง (PUT)"
            
            print(f"\n--------------------------------------------------")
            print(f"{color}🎯 [SNIPER PRO SOL/USD] พบสัญญาณเกรด [{grade}] ทรงพลัง! [{name}]{color}")
            print(f"⏰ จุดเข้าซื้อเวลา: {entry_time.strftime('%H:%M:%S')} น. | ไม้ที่ {step}/3")
            print(f"--------------------------------------------------")
            
            discord_msg = (
                f"⚡ **[สูตร 4: Sniper Pro SOL/USD - เตือนเตรียมตัวเข้าซื้อ]**\n"
                f"🪙 **สินทรัพย์:** `{name}` (เกรด {grade})\n"
                f"⏰ **เวลาเข้าซื้อจริง:** `{entry_time.strftime('%H:%M:%S')}` น. (ไม้ที่ {step}/3)\n"
                f"🎯 **ทิศทาง:** **{action_icon}** (จำนวน {stakes[step]} บาท)\n"
                f"📊 **รายละเอียด:** {reason}"
            )
            send_discord_alert(discord_msg)
            
            while datetime.now() < entry_time:
                time.sleep(0.01)
                
            time.sleep(58)
            
            result_df = get_market_data(symbol, INTERVAL, 250)
            if result_df is not None and len(result_df) >= 2:
                p_open, p_close = float(result_df.iloc[-1]['open']), float(result_df.iloc[-1]['close'])
                
                if p_close == p_open:
                    log_trade_to_db(str(datetime.now()), name, "Sniper Pro SOL", action, stakes[step], "DRAW", 0.0)
                    send_discord_alert(f"🟡 **[สูตร 4 SOL/USD RESULT]** `{name}` เสมอ! 🟡")
                    step = 1
                else:
                    is_win = (p_close > p_open) if action == "CALL" else (p_close < p_open)
                    pl = stakes[step] * 0.85 if is_win else -stakes[step]
                    log_trade_to_db(str(datetime.now()), name, "Sniper Pro SOL", action, stakes[step], "WIN" if is_win else "LOSS", pl)

                    if is_win:
                        with stats_lock: stats["win"] += 1
                        send_discord_alert(f"✅ **[สูตร 4 SOL/USD WIN]** `{name}` ชนะไม้ที่ {step} สำเร็จ! 🎉 (+{pl} บาท)")
                        print_stats_summary()
                        step = 1
                    else:
                        with stats_lock: stats["loss"] += 1
                        if step < 3:
                            send_discord_alert(f"⚠️ **[สูตร 4 SOL/USD LOSS]** `{name}` พลาดไม้ที่ {step} ➔ เตรียมทบไม้ที่ {step + 1}")
                            step += 1
                        else:
                            send_discord_alert(f"🛑 **[สูตร 4 SOL/USD CUT]** `{name}` พลาดครบ 3 ไม้ พักระบบชั่วคราว!")
                            print_stats_summary()
                            step = 1
                            time.sleep(120)
        except Exception as e:
            print(f"⚠️ Error in SOL Worker: {e}")
            time.sleep(5)

# ==========================================
# 🚀 สูตรที่ 6: MOMENTUM & VOLATILITY PRO
# ==========================================
def analyze_strategy_6(df):
    if df is None or len(df) < 50: return None, "ข้อมูลกราฟไม่พอ"
    df = calculate_all_indicators(df)
    latest, prev = df.iloc[-1], df.iloc[-2]
    
    volume_spike = latest['volume'] > (latest['Vol_Avg'] * 1.5)
    bb_break_high = latest['close'] > latest['BB_Upper']
    bb_break_low = latest['close'] < latest['BB_Lower']
    
    if bb_break_high and volume_spike and latest['RSI'] > 55 and latest['ADX'] > 22:
        return "CALL", f"BB High Breakout | Vol Spike ({latest['volume']:.0f}) | ADX:{latest['ADX']:.1f}"
        
    if bb_break_low and volume_spike and latest['RSI'] < 45 and latest['ADX'] > 22:
        return "PUT", f"BB Low Breakdown | Vol Spike ({latest['volume']:.0f}) | ADX:{latest['ADX']:.1f}"
        
    return None, "รอจังหวะตลาดฟอร์มตัว"

def run_strategy_6_momentum():
    print("🚀 [สูตรที่ 6: Momentum & Volatility Pro 1m] เริ่มสแกนสัญญาณเรียบร้อย")
    while True:
        try:
            now = datetime.now()
            target_time = (now + timedelta(minutes=1)).replace(second=0, microsecond=0)
            warning_time = target_time - timedelta(seconds=15)
            
            if warning_time <= now < warning_time + timedelta(seconds=2):
                next_target_str = target_time.strftime("%H:%M:%S")
                signals_found = 0
                alert_msg = f"🚀 **[สูตร 6: Momentum Pro - สัญญาณใกล้เข้าซื้อ]**\n⏰ เวลาเข้าซื้อจริง: `{next_target_str}` น.\n"
                
                valid_signals = []
                for pair in PAIRS:
                    symbol, name = pair["symbol"], pair["name"]
                    if time.time() < strategy6_traders[symbol]["rest_until"]: continue
                    df = get_market_data(symbol, "1m", 100)
                    action, reason = analyze_strategy_6(df)
                    
                    st = strategy6_traders[symbol]
                    if not st["in_progress"] and action is None: continue
                    if st["in_progress"] and action is None: action, reason = st["active_action"], "ไม้ทบต่อเนื่องตามแผน"
                    
                    if action is not None:
                        signals_found += 1
                        st["in_progress"] = True
                        if st["step"] == 1: st["active_action"] = action
                        icon = "🟢 CALL" if st["active_action"] == "CALL" else "🔴 PUT"
                        alert_msg += f"- `{name}` [ไม้ {st['step']}/3] ➔ **{icon}** ({stakes[st['step']]} บ.) | {reason}\n"
                        valid_signals.append((name, symbol, st["active_action"], st["step"]))
                
                if signals_found > 0:
                    send_discord_alert(alert_msg)
                    while datetime.now() < target_time: time.sleep(0.01)
                    time.sleep(2)
                    
                    for name, symbol, action, step in valid_signals:
                        res_df = get_market_data(symbol, "1m", 2)
                        st = strategy6_traders[symbol]
                        if res_df is not None and len(res_df) >= 2:
                            p_o, p_c = float(res_df.iloc[-1]['open']), float(res_df.iloc[-1]['close'])
                            
                            if p_c == p_o:
                                log_trade_to_db(str(datetime.now()), name, "สูตร 6 Momentum Pro", action, stakes[step], "DRAW", 0.0)
                                send_discord_alert(f"🟡 **[สูตร 6 RESULT]** `{name}` เสมอ! 🟡")
                                st["step"], st["rest_until"], st["in_progress"] = 1, time.time() + 60, False
                            else:
                                is_win = (p_c > p_o) if action == "CALL" else (p_c < p_o)
                                pl = stakes[step] * 0.85 if is_win else -stakes[step]
                                log_trade_to_db(str(datetime.now()), name, "สูตร 6 Momentum Pro", action, stakes[step], "WIN" if is_win else "LOSS", pl)
                                
                                if is_win:
                                    send_discord_alert(f"✅ **[สูตร 6 WIN]** `{name}` ชนะไม้ {step} 🎉 (+{pl} บ.)")
                                    st["step"], st["rest_until"], st["in_progress"] = 1, time.time() + 180, False
                                else:
                                    if st["step"] < 3:
                                        st["step"] += 1
                                        send_discord_alert(f"⚠️ **[สูตร 6 LOSS]** `{name}` พลาดไม้ {step} ➔ เตรียมทบไม้ที่ {st['step']}")
                                    else:
                                        send_discord_alert(f"🛑 **[สูตร 6 CUT]** `{name}` แพ้ครบ 3 ไม้ พักชั่วคราว")
                                        st["step"], st["rest_until"], st["in_progress"] = 1, time.time() + 300, False
                            print_total_system_stats_summary()
            time.sleep(0.5)
        except Exception as e:
            print(f"Error in Strategy 6 Thread: {e}")
            time.sleep(5)

# ==========================================
# 🚀 MAIN EXECUTION (Keep-Alive 24/7)
# ==========================================
def start_bot_system():
    print("==================================================")
    print("🚀 ระบบ Multi-Strategy Bot รวม Sniper Pro CAD/JPY พร้อมใช้งานเต็มรูปแบบ")
    print("==================================================")
    
    print_total_system_stats_summary()
    
    send_discord_alert(
        "🚀 **Bot Online (24/7 Mode)**: เปิดใช้งานระบบเทรดอัตโนมัติครบถ้วนเรียบร้อยแล้ว!\n"
        "- 🛡️ **Sniper Pro CAD/JPY**: ระบบล็อกเป้าหมาย Yahoo Finance + เตือน Discord ตรงเวลา\n"
        "- 🎯 **สูตร 1**: สูตร 6 คู่เงิน 1m\n"
        "- 🧠 **สูตร 2**: SMC 1M\n"
        "- 📐 **สูตร 3**: Institutional MTF\n"
        "- ⚡ **สูตร 4**: Sniper Pro SOL/USD\n"
        "- 🇬🇧🇯🇵 **สูตร GBP/JPY**: 8 Indicators + เตือน Discord ตรงเวลา\n"
        "- 🚀 **สูตร 6**: Momentum & Volatility Pro 1M\n"
        "- 🔥 **สูตรที่7 A+**: EUR/JPY Confidence Score System"
    )
    
    threading.Thread(target=run_strategy_sniper_5m, daemon=True).start()
    threading.Thread(target=run_strategy_smc_1m, daemon=True).start()
    threading.Thread(target=run_strategy_3_multitimeframe, daemon=True).start()
    threading.Thread(target=run_strategy_6_momentum, daemon=True).start()
    threading.Thread(target=run_strategy_gbp_jpy, daemon=True).start()
    
    threading.Thread(target=run_single_pair, args=(SNIPER_PRO_CADJPY_PAIR,), daemon=True).start()
    
    for pair in STRATEGY7_PAIRS:
        threading.Thread(target=pair_worker_strategy7, args=(pair,), daemon=True).start()
        time.sleep(0.3)
    
    for pair in SOL_PAIRS:
        threading.Thread(target=pair_worker_sol, args=(pair,), daemon=True).start()
        time.sleep(0.3)

if __name__ == "__main__":
    while True:
        try:
            start_bot_system()
            while True:
                time.sleep(3600)
        except Exception as e:
            print(f"🔥 Critical System Restart due to: {e}")
            send_discord_alert(f"🔥 **Bot System Auto-Restart**: ระบบรีสตาร์ทตัวเองอัตโนมัติเนื่องจาก: `{e}`")
            time.sleep(5)
