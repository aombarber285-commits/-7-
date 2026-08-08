import os
import urllib.request
import json
import time
from datetime import datetime
import threading
import sqlite3
import numpy as np

# ==============================================================================
# 🟩 SCRIPT 1: V15 GRANULAR WALK-FORWARD EXPECTANCY & MFE/MAE ENGINE
# ==============================================================================
class ExpectancyJournalV15:
    def __init__(self, db_path=None):
        if db_path is None:
            import tempfile
            self.db_path = os.path.join(tempfile.gettempdir(), "v15_sniper_journal.db")
        else:
            self.db_path = db_path
            
        self.lock = threading.Lock()
        self.init_db()

    def init_db(self):
        with self.lock:
            conn = sqlite3.connect(self.db_path, check_same_thread=False)
            cursor = conn.cursor()
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS journal (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT, pair TEXT, setup_key TEXT, regime TEXT, granular_key TEXT, action TEXT, grade TEXT,
                    confidence_score REAL, trap_risk REAL, h_bias TEXT, mtf_15m TEXT, mtf_5m TEXT,
                    target_candle_time INTEGER, signal_price REAL, target_open REAL, actual_open REAL,
                    slippage REAL, exit_close_price REAL, mfe REAL, mae REAL, pnl_pct REAL, result TEXT DEFAULT 'PENDING'
                )
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS granular_adaptive_state (
                    granular_key TEXT PRIMARY KEY,
                    cooldown_until_candle INTEGER DEFAULT 0,
                    consecutive_losses INTEGER DEFAULT 0,
                    is_circuit_active INTEGER DEFAULT 0
                )
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS rate_limit_tracker (
                    pair TEXT PRIMARY KEY,
                    last_signal_timestamps TEXT
                )
            ''')
            conn.commit()
            conn.close()

    def log_signal(self, pair, setup_key, regime, granular_key, action, grade, confidence_score, trap_risk, h_bias, mtf_15m, mtf_5m, target_candle_time, signal_price, target_open, actual_open, slippage):
        with self.lock:
            conn = sqlite3.connect(self.db_path, check_same_thread=False)
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO journal (
                    timestamp, pair, setup_key, regime, granular_key, action, grade, confidence_score, trap_risk,
                    h_bias, mtf_15m, mtf_5m, target_candle_time, signal_price, target_open, actual_open, slippage
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), pair, setup_key, regime, granular_key, action, grade, confidence_score, trap_risk, h_bias, mtf_15m, mtf_5m, target_candle_time, signal_price, target_open, actual_open, slippage))
            signal_id = cursor.lastrowid
            conn.commit()
            conn.close()
            return signal_id

    def update_forward_test_result(self, signal_id, granular_key, target_kline, action, current_candle_time):
        actual_open_price = target_kline[1] 
        high = target_kline[2]
        low = target_kline[3]
        exit_close_price = target_kline[4]
        
        if action == "CALL":
            mfe = ((high - actual_open_price) / actual_open_price) * 100
            mae = ((actual_open_price - low) / actual_open_price) * 100
            pnl_pct = ((exit_close_price - actual_open_price) / actual_open_price) * 100
            result = "WIN" if exit_close_price > actual_open_price else ("LOSS" if exit_close_price < actual_open_price else "DRAW")
        else:
            mfe = ((actual_open_price - low) / actual_open_price) * 100
            mae = ((high - actual_open_price) / actual_open_price) * 100
            pnl_pct = ((actual_open_price - exit_close_price) / actual_open_price) * 100
            result = "WIN" if exit_close_price < actual_open_price else ("LOSS" if exit_close_price > actual_open_price else "DRAW")

        with self.lock:
            conn = sqlite3.connect(self.db_path, check_same_thread=False)
            cursor = conn.cursor()
            
            cursor.execute('''
                UPDATE journal 
                SET exit_close_price = ?, mfe = ?, mae = ?, pnl_pct = ?, result = ? 
                WHERE id = ?
            ''', (exit_close_price, mfe, mae, pnl_pct, result, signal_id))
            
            cursor.execute('SELECT consecutive_losses FROM granular_adaptive_state WHERE granular_key = ?', (granular_key,))
            row = cursor.fetchone()
            consec_losses = row[0] if row else 0

            if result == "LOSS":
                consec_losses += 1
                cooldown_bars = 3 if consec_losses == 1 else (6 if consec_losses == 2 else 15)
                target_cooldown_time = current_candle_time + (cooldown_bars * 60000)
                circuit_active = 1
            elif result == "WIN":
                consec_losses = 0
                target_cooldown_time = 0
                circuit_active = 0
            else:
                target_cooldown_time = 0
                circuit_active = 0

            cursor.execute('''
                INSERT INTO granular_adaptive_state (granular_key, cooldown_until_candle, consecutive_losses, is_circuit_active)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(granular_key) DO UPDATE SET
                    cooldown_until_candle = excluded.cooldown_until_candle,
                    consecutive_losses = excluded.consecutive_losses,
                    is_circuit_active = excluded.is_circuit_active
            ''', (granular_key, target_cooldown_time, consec_losses, circuit_active))

            conn.commit()
            conn.close()

    def check_granular_adaptive_guards(self, granular_key, current_candle_time):
        with self.lock:
            conn = sqlite3.connect(self.db_path, check_same_thread=False)
            cursor = conn.cursor()
            cursor.execute('SELECT cooldown_until_candle, consecutive_losses, is_circuit_active FROM granular_adaptive_state WHERE granular_key = ?', (granular_key,))
            row = cursor.fetchone()

            if not row:
                conn.close()
                return False, "NORMAL"
            
            cooldown_until, consec_losses, circuit_active = row[0], row[1], row[2]
            
            if current_candle_time >= cooldown_until and circuit_active == 1:
                cursor.execute('''
                    UPDATE granular_adaptive_state 
                    SET is_circuit_active = 0, cooldown_until_candle = 0, consecutive_losses = 0 
                    WHERE granular_key = ?
                ''', (granular_key,))
                conn.commit()
                conn.close()
                return False, "COOLDOWN_EXPIRED_RESET"
                
            if current_candle_time < cooldown_until:
                conn.close()
                return True, f"GRANULAR_COOLDOWN_ACTIVE (Locked for {int((cooldown_until - current_candle_time)/60000)}m, Losses: {consec_losses})"
                
            conn.close()
            return False, "PASSED"

    def record_confirmed_rate_limit(self, pair, current_time_ms):
        with self.lock:
            conn = sqlite3.connect(self.db_path, check_same_thread=False)
            cursor = conn.cursor()
            cursor.execute('SELECT last_signal_timestamps FROM rate_limit_tracker WHERE pair = ?', (pair,))
            row = cursor.fetchone()
            
            timestamps = json.loads(row[0]) if row and row[0] else []
            fifteen_mins_ago = current_time_ms - (15 * 60 * 1000)
            
            timestamps = [t for t in timestamps if t > fifteen_mins_ago]
            timestamps.append(current_time_ms)
            cursor.execute('''
                INSERT INTO rate_limit_tracker (pair, last_signal_timestamps)
                VALUES (?, ?)
                ON CONFLICT(pair) DO UPDATE SET last_signal_timestamps = excluded.last_signal_timestamps
            ''', (pair, json.dumps(timestamps)))
            conn.commit()
            conn.close()

    def check_rate_limit(self, pair, current_time_ms):
        with self.lock:
            conn = sqlite3.connect(self.db_path, check_same_thread=False)
            cursor = conn.cursor()
            cursor.execute('SELECT last_signal_timestamps FROM rate_limit_tracker WHERE pair = ?', (pair,))
            row = cursor.fetchone()
            conn.close()
            
            timestamps = json.loads(row[0]) if row and row[0] else []
            fifteen_mins_ago = current_time_ms - (15 * 60 * 1000)
            timestamps = [t for t in timestamps if t > fifteen_mins_ago]
            
            if len(timestamps) >= 3:
                return True, f"Rate Limit Exceeded: {len(timestamps)} confirmed signals in last 15 mins"
            return False, "PASSED"

    def get_granular_expectancy_and_learning(self, granular_key, raw_score):
        with self.lock:
            conn = sqlite3.connect(self.db_path, check_same_thread=False)
            cursor = conn.cursor()
            cursor.execute('''
                SELECT result, pnl_pct, confidence_score, mfe, mae FROM journal WHERE granular_key = ? AND result IN ('WIN', 'LOSS') ORDER BY id ASC
            ''', (granular_key,))
            rows = cursor.fetchall()
            conn.close()

            total_samples = len(rows)
            if total_samples < 10:
                return {"tier": "LEARNING", "status": "LEARNING", "samples": total_samples, "winrate": 50.0, "expectancy": 0.0, "calibrated_winrate": 50.0, "avg_mfe": 0.0, "avg_mae": 0.0, "mae_penalty": 0.0}

            wins = [r[1] for r in rows if r[0] == 'WIN']
            losses = [abs(r[1]) for r in rows if r[0] == 'LOSS']
            winrate = (len(wins) / total_samples) * 100.0 if total_samples > 0 else 0.0
            avg_win = np.mean(wins) if wins else 0.0
            avg_loss = np.mean(losses) if losses else 0.0
            expectancy = ((winrate / 100.0) * avg_win) - (((100.0 - winrate) / 100.0) * avg_loss)

            mfes = [r[3] for r in rows if r[3] is not None]
            maes = [r[4] for r in rows if r[4] is not None]
            avg_mfe = np.mean(mfes) if mfes else 0.0
            avg_mae = np.mean(maes) if maes else 0.0

            mae_penalty = 0.0
            if avg_mae > (avg_mfe * 1.4):
                mae_penalty = 15.0

            bracket_min = (raw_score // 5) * 5
            bracket_rows = [r for r in rows if bracket_min <= r[2] <= bracket_min + 4]
            
            if len(bracket_rows) >= 4:
                bracket_wins = len([r for r in bracket_rows if r[0] == 'WIN'])
                calibrated_winrate = (bracket_wins / len(bracket_rows)) * 100.0
            else:
                calibrated_winrate = winrate

            tier = "HIGH_CONFIDENCE" if total_samples >= 30 else ("VALIDATED" if total_samples >= 15 else "CAUTION")
            status = "ADAPTIVE_BLOCKED" if (winrate < 50.0 or expectancy <= 0 or avg_mae > 0.38) else "ACTIVE"
            return {"tier": tier, "status": status, "samples": total_samples, "winrate": winrate, "expectancy": expectancy, "calibrated_winrate": calibrated_winrate, "avg_mfe": avg_mfe, "avg_mae": avg_mae, "mae_penalty": mae_penalty}

journal_engine = ExpectancyJournalV15()

# ==============================================================================
# 🟦 SCRIPT 2: CANDS-VISION S+ V15 (REAL SWING / BOS / CHoCH + STRICT NO-LOOKAHEAD)
# ==============================================================================
PAIRS_S2 = [
    {"name": "BTC/USDT", "symbol": "BTCUSDT"},
    {"name": "ETH/USDT", "symbol": "ETHUSDT"},
    {"name": "SOL/USDT", "symbol": "SOLUSDT"},
    {"name": "XRP/USDT", "symbol": "XRPUSDT"},
    {"name": "BNB/USDT", "symbol": "BNBUSDT"},
    {"name": "DOGE/USDT", "symbol": "DOGEUSDT"}
]

WEBHOOK_URL_S2 = os.getenv("DISCORD_WEBHOOK_URL", "")

pair_states = {}
for p in PAIRS_S2:
    pair_states[p["symbol"]] = {
        "is_busy": False,
        "last_signal_time": 0,
        "pending_pre_alert": None,
        "lock": threading.Lock()
    }

def send_discord_signal_s2(message):
    if not WEBHOOK_URL_S2: return False
    try:
        data = json.dumps({"content": message}).encode("utf-8")
        req = urllib.request.Request(WEBHOOK_URL_S2, data=data, headers={'Content-Type': 'application/json', 'User-Agent': 'Mozilla/5.0'}, method="POST")
        with urllib.request.urlopen(req, timeout=5) as response:
            return response.status == 204
    except Exception as e:
        print(f"⚠️ Discord Alert Error: {e}")

def fetch_klines(symbol, interval, limit=250):
    url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode())
            return [[int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5])] for k in data]
    except Exception:
        return []

def parse_kline(k):
    o, h, l, c, vol = k[1], k[2], k[3], k[4], k[5]
    total_len = h - l if h - l > 0 else 0.00001
    body = abs(c - o)
    midpoint = (o + c) / 2
    return {
        "o": o, "h": h, "l": l, "c": c, "vol": vol,
        "total_len": total_len, "body": body, "midpoint": midpoint,
        "body_pct": (body / total_len) * 100,
        "upper_wick_pct": ((h - max(o, c)) / total_len) * 100,
        "lower_wick_pct": ((min(o, c) - l) / total_len) * 100,
        "is_green": c > o, "is_red": c < o
    }

def calculate_wilder_rsi(closes, period=14):
    if len(closes) < period + 1: return 50.0
    deltas = np.diff(closes)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    
    avg_gain = np.mean(gains[:period])
    avg_loss = np.mean(losses[:period])
    
    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        
    if avg_loss == 0: return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def calculate_ema(closes, period):
    if len(closes) < period: return closes[-1] if closes else 0.0
    multiplier = 2 / (period + 1)
    ema = closes[0]
    for price in closes[1:]:
        ema = (price - ema) * multiplier + ema
    return ema

def calculate_atr_pct(parsed_klines, period=14):
    tr_list = []
    for i in range(1, len(parsed_klines)):
        h, l, prev_c = parsed_klines[i]["h"], parsed_klines[i]["l"], parsed_klines[i-1]["c"]
        tr_list.append(max(h - l, abs(h - prev_c), abs(l - prev_c)))
    atr_val = np.mean(tr_list[-period:]) if len(tr_list) >= period else 0.0001
    current_close = parsed_klines[-1]["c"]
    return (atr_val / current_close) * 100.0

def detect_market_regime_atr_pct(parsed_klines, atr_pct):
    closes = [p["c"] for p in parsed_klines[-15:-1]]
    linear_slope = closes[-1] - closes[0]
    
    if atr_pct > 1.2: return "HIGH_VOLATILITY"
    if atr_pct < 0.25: return "LOW_VOLATILITY"
    if linear_slope > (closes[-1] * 0.0025): return "TREND_UP"
    if linear_slope < -(closes[-1] * 0.0025): return "TREND_DOWN"
    return "RANGE"

def calculate_no_lookahead_sr(parsed_klines):
    history = parsed_klines[-32:-2] if len(parsed_klines) >= 32 else parsed_klines[:-2]
    if not history: return 0.0, 0.0
    highs = [p["h"] for p in history]
    lows = [p["l"] for p in history]
    return max(highs), min(lows)

def evaluate_real_swing_structure(parsed_klines):
    history = parsed_klines[:-1]
    if len(history) < 20: return {"bos_bullish": False, "bos_bearish": False, "choch_bullish": False, "choch_bearish": False, "score": 0}
    
    swing_highs = []
    swing_lows = []
    
    for i in range(2, len(history) - 2):
        if history[i]["h"] >= history[i-1]["h"] and history[i]["h"] >= history[i-2]["h"] and \
           history[i]["h"] >= history[i+1]["h"] and history[i]["h"] >= history[i+2]["h"]:
            swing_highs.append((i, history[i]["h"]))
            
        if history[i]["l"] <= history[i-1]["l"] and history[i]["l"] <= history[i-2]["l"] and \
           history[i]["l"] <= history[i+1]["l"] and history[i]["l"] <= history[i+2]["l"]:
            swing_lows.append((i, history[i]["l"]))

    bos_bullish = False
    bos_bearish = False
    choch_bullish = False
    choch_bearish = False
    score = 0

    curr_close = history[-1]["c"]

    if len(swing_highs) >= 2 and len(swing_lows) >= 2:
        last_sh = swing_highs[-1][1]
        prev_sh = swing_highs[-2][1]
        last_sl = swing_lows[-1][1]
        prev_sl = swing_lows[-2][1]

        if curr_close > last_sh:
            bos_bullish = True
            score += 20
        elif curr_close < last_sl:
            bos_bearish = True
            score += 20

        if prev_sh > last_sh and curr_close > prev_sh:
            choch_bullish = True
            score += 15
        elif prev_sl < last_sl and curr_close < prev_sl:
            choch_bearish = True
            score += 15

    return {
        "bos_bullish": bos_bullish,
        "bos_bearish": bos_bearish,
        "choch_bullish": choch_bullish,
        "choch_bearish": choch_bearish,
        "score": min(score, 25)
    }

def evaluate_displacement_and_volume(parsed_klines):
    curr = parsed_klines[-2]
    vols = [p["vol"] for p in parsed_klines[-31:-2]]
    mean_vol = np.mean(vols)
    std_vol = np.std(vols) if np.std(vols) > 0 else 1.0
    vol_zscore = (curr["vol"] - mean_vol) / std_vol
    
    score = 0
    if vol_zscore >= 2.0: score += 15
    elif vol_zscore >= 1.0: score += 10
    elif vol_zscore >= 0.5: score += 5
    
    if curr["body_pct"] >= 40.0 and curr["total_len"] > 0:
        score += 10
        
    return score, vol_zscore

def evaluate_strict_mtf_bias(symbol):
    def get_tf_closed_structure(interval):
        klines = fetch_klines(symbol, interval, 250)
        if len(klines) < 200: return "NEUTRAL"
        parsed = [parse_kline(k) for k in klines]
        closes = [p["c"] for p in parsed[:-1]]
        
        ema_20 = calculate_ema(closes, 20)
        ema_50 = calculate_ema(closes, 50)
        ema_200 = calculate_ema(closes, 200)
        curr_c = parsed[-2]["c"]
        
        if curr_c > ema_20 and ema_20 > ema_50 and ema_50 > ema_200: return "BULLISH"
        elif curr_c < ema_20 and ema_20 < ema_50 and ema_50 < ema_200: return "BEARISH"
        return "NEUTRAL"

    return get_tf_closed_structure("1h"), get_tf_closed_structure("15m"), get_tf_closed_structure("5m")

def check_distance_to_sr(parsed_klines, proposed_action, atr_pct):
    res_level, sup_level = calculate_no_lookahead_sr(parsed_klines)
    curr_c = parsed_klines[-2]["c"]
    
    dist_res_pct = ((res_level - curr_c) / curr_c) * 100
    dist_sup_pct = ((curr_c - sup_level) / curr_c) * 100
    min_space = atr_pct * 0.45
    
    if proposed_action == "CALL" and dist_res_pct < min_space:
        return True, f"CALL Blocked: Resistance distance ({dist_res_pct:.3f}%) < Min Space"
    if proposed_action == "PUT" and dist_sup_pct < min_space:
        return True, f"PUT Blocked: Support distance ({dist_sup_pct:.3f}%) < Min Space"
        
    return False, "PASSED"

def eval_liquidity_sweep_and_confirmation(parsed_klines):
    res_level, sup_level = calculate_no_lookahead_sr(parsed_klines)
    curr = parsed_klines[-2]
    
    eql_swept = curr["l"] < sup_level and curr["c"] > sup_level
    eqh_swept = curr["h"] > res_level and curr["c"] < res_level
    
    return {
        "valid_call": eql_swept and curr["lower_wick_pct"] >= 28.0 and curr["is_green"],
        "valid_put": eqh_swept and curr["upper_wick_pct"] >= 28.0 and curr["is_red"]
    }

def analyze_candle_vision_v15(symbol, name):
    klines_1m = fetch_klines(symbol, "1m", 250)
    if len(klines_1m) < 220:
        return "C", "NO TRADE", "UNKNOWN", "UNKNOWN", 0, 100, {}, ["ข้อมูลไม่พอสำหรับ V15"], "RANGE", "NEUTRAL", "NEUTRAL", "NEUTRAL", 0

    parsed = [parse_kline(k) for k in klines_1m]
    target_candle_time = klines_1m[-1][0] 

    closes = [p["c"] for p in parsed[:-1]]
    rsi = calculate_wilder_rsi(closes, 14)
    prev_rsi = calculate_wilder_rsi(closes[:-1], 14)
    atr_pct = calculate_atr_pct(parsed, 14)
    regime = detect_market_regime_atr_pct(parsed, atr_pct)

    trap_risk = 0
    trap_flags = []

    if regime in ["HIGH_VOLATILITY", "LOW_VOLATILITY"]:
        return "C", "NO TRADE", "UNKNOWN", "UNKNOWN", 0, 100, {}, [f"Hard Gate Blocked: Regime {regime}"], regime, "NEUTRAL", "NEUTRAL", "NEUTRAL", target_candle_time

    rejection_map = eval_liquidity_sweep_and_confirmation(parsed)
    h_bias, mtf_15m, mtf_5m = evaluate_strict_mtf_bias(symbol)
    struct_eval = evaluate_real_swing_structure(parsed)
    disp_score, vol_zscore = evaluate_displacement_and_volume(parsed)

    liquidity_score = 0
    mtf_score = 0
    momentum_score = 0
    structure_score = struct_eval["score"]

    proposed_action = "NONE"
    base_setup_id = "UNKNOWN"

    if rejection_map["valid_call"] or struct_eval["bos_bullish"] or struct_eval["choch_bullish"]:
        base_setup_id = "V15_BOS_SWEEP_CALL"
        proposed_action = "CALL"
        liquidity_score += 25
    elif rejection_map["valid_put"] or struct_eval["bos_bearish"] or struct_eval["choch_bearish"]:
        base_setup_id = "V15_BOS_SWEEP_PUT"
        proposed_action = "PUT"
        liquidity_score += 25

    if proposed_action == "CALL":
        if rsi < 48.0 and rsi > prev_rsi: momentum_score += 15
        elif rsi > 72.0: trap_risk += 30; trap_flags.append("RSI Overbought")
    elif proposed_action == "PUT":
        if rsi > 52.0 and rsi < prev_rsi: momentum_score += 15
        elif rsi < 28.0: trap_risk += 30; trap_flags.append("RSI Oversold")

    if proposed_action == "CALL":
        if h_bias == "BULLISH": mtf_score += 10
        if mtf_15m == "BULLISH": mtf_score += 10
        if mtf_5m == "BULLISH": mtf_score += 5
        if h_bias == "BEARISH": trap_risk += 45; trap_flags.append("1H Macro Bias BEARISH for CALL")
    elif proposed_action == "PUT":
        if h_bias == "BEARISH": mtf_score += 10
        if mtf_15m == "BEARISH": mtf_score += 10
        if mtf_5m == "BEARISH": mtf_score += 5
        if h_bias == "BULLISH": trap_risk += 45; trap_flags.append("1H Macro Bias BULLISH for PUT")

    if proposed_action != "NONE":
        is_spatial_blocked, spatial_reason = check_distance_to_sr(parsed, proposed_action, atr_pct)
        if is_spatial_blocked:
            trap_risk += 65; trap_flags.append(spatial_reason)

    total_confidence = structure_score + liquidity_score + mtf_score + momentum_score + disp_score
    setup_key = f"{base_setup_id}|{proposed_action}"
    granular_key = f"{name}|{setup_key}|{regime}"

    is_blocked, block_reason = journal_engine.check_granular_adaptive_guards(granular_key, target_candle_time)
    if is_blocked:
        trap_risk += 90; trap_flags.append(f"Granular Guard: {block_reason}")

    is_rate_limited, rate_reason = journal_engine.check_rate_limit(symbol, target_candle_time)
    if is_rate_limited:
        trap_risk += 80; trap_flags.append(f"Rate Limit: {rate_reason}")

    stats = journal_engine.get_granular_expectancy_and_learning(granular_key, total_confidence)
    if stats["status"] == "ADAPTIVE_BLOCKED":
        trap_risk += 85; trap_flags.append(f"Granular Walk-Forward Blocked [{name} + {regime}] -> WinRate: {stats['winrate']:.1f}%")
    
    total_confidence -= stats["mae_penalty"]

    grade = "C"
    decision = "NO TRADE"
    mtf_fully_aligned = (proposed_action == "CALL" and h_bias == "BULLISH" and mtf_15m == "BULLISH") or \
                        (proposed_action == "PUT" and h_bias == "BEARISH" and mtf_15m == "BEARISH")

    if stats["tier"] == "LEARNING":
        trap_flags.append("Granular Samples < 10 (Learning Mode)")
        if trap_risk <= 5 and total_confidence >= 82.0 and mtf_fully_aligned and not is_blocked:
            grade = "A"; decision = proposed_action
        elif trap_risk <= 15 and total_confidence >= 72.0:
            grade = "B"; decision = "WAIT"
    else:
        if trap_risk <= 5 and total_confidence >= 88.0 and mtf_fully_aligned and not is_blocked:
            grade = "A+"; decision = proposed_action
        elif trap_risk <= 15 and total_confidence >= 80.0 and mtf_fully_aligned and not is_blocked:
            grade = "A"; decision = proposed_action
        elif trap_risk <= 25 and total_confidence >= 70.0:
            grade = "B"; decision = "WAIT"

    score_breakdown = {
        "Grade": grade,
        "Adjusted Score": f"{max(total_confidence, 0):.1f}/100",
        "Granular Winrate": f"{stats['calibrated_winrate']:.1f}% ({stats['samples']} samples)",
        "Expectancy (EV)": f"{stats['expectancy']:.4f}%",
        "Avg MFE / MAE": f"+{stats['avg_mfe']:.3f}% / -{stats['avg_mae']:.3f}%",
        "Market Regime": regime,
        "1H / 15m / 5m Bias": f"{h_bias} / {mtf_15m} / {mtf_5m}",
        "Granular Key": granular_key
    }

    return grade, decision, setup_key, granular_key, max(total_confidence, 0), min(trap_risk, 100), score_breakdown, trap_flags, regime, h_bias, mtf_15m, mtf_5m, target_candle_time

def exact_forward_test_tracker_v15(symbol, signal_id, granular_key, action, target_candle_time):
    while True:
        time.sleep(1)
        klines = fetch_klines(symbol, "1m", 10)
        if not klines: continue
        
        for k in klines:
            if k[0] == target_candle_time:
                while True:
                    time.sleep(1.5)
                    check_klines = fetch_klines(symbol, "1m", 5)
                    if check_klines and check_klines[-1][0] > target_candle_time:
                        for ck in check_klines:
                            if ck[0] == target_candle_time:
                                current_c_time = check_klines[-1][0]
                                journal_engine.update_forward_test_result(signal_id, granular_key, ck, action, current_c_time)
                                print(f"📌 [V15 SNIPER CLOSED] ID: {signal_id} | GranularKey: {granular_key} | Result Recorded")
                                return
        
        if klines[-1][0] > target_candle_time + 120000:
            return

def pair_worker_s2(pair):
    symbol, name = pair["symbol"], pair["name"]
    state = pair_states[symbol]
    last_candle_time = None
    
    print(f"[{datetime.now().strftime('%H:%M:%S')}] 🎯 [S+ V15 Granular Sniper] Active: {name}")
    
    while True:
        try:
            klines = fetch_klines(symbol, "1m", 5)
            if not klines:
                time.sleep(2)
                continue
                
            curr_candle_time = klines[-1][0]
            
            if last_candle_time is not None and last_candle_time != curr_candle_time:
                if state["pending_pre_alert"]:
                    grade, decision, setup_key, granular_key, score, trap_risk, breakdown, trap_flags, regime, h_bias, mtf_15m, mtf_5m, target_time = analyze_candle_vision_v15(symbol, name)
                    p_type = state["pending_pre_alert"]["type"]

                    if decision == p_type and grade in ["A+", "A"]:
                        target_open = klines[-1][1] 
                        signal_price = klines[-2][4] 
                        actual_open = klines[-1][1]
                        slippage = abs(actual_open - target_open)

                        journal_engine.record_confirmed_rate_limit(symbol, target_time)

                        sig_id = journal_engine.log_signal(name, setup_key, regime, granular_key, decision, grade, score, trap_risk, h_bias, mtf_15m, mtf_5m, target_time, signal_price, target_open, actual_open, slippage)
                        threading.Thread(target=exact_forward_test_tracker_v15, args=(symbol, sig_id, granular_key, decision, target_time), daemon=True).start()

                        details_str = "\n".join([f"• {k}: {v}" for k, v in breakdown.items()])
                        confirm_msg = (
                            f"🏆 **[S+ V15 MASTER SIGNAL — GRADE {grade}] — {name}**\n"
                            f"🎯 **คำสั่งซื้อ:** `{decision}` | **Regime:** `{regime}`\n"
                            f"🌐 **Granular Key:** `{granular_key}`\n"
                            f"💯 **Score & Granular Validation:**\n{details_str}"
                        )
                        print(f"\n--------------------------------------------------\n{confirm_msg}\n--------------------------------------------------")
                        send_discord_signal_s2(confirm_msg)
                        state["last_signal_time"] = time.time()

                    elif decision in ["NO TRADE", "WAIT"] or trap_risk >= 30:
                        trap_str = "\n".join([f"• {f}" for f in trap_flags])
                        no_trade_msg = (
                            f"🚨 **[S+ V15 QUALITY GATE: {decision} / GRADE {grade}] — {name}**\n"
                            f"❌ **บล็อกตามเงื่อนไข Granular & Regime:**\n{trap_str}\n"
                            f"📊 **Adjusted Score:** `{score:.1f}/100` | 🔴 **Trap Risk:** `{trap_risk}/100`"
                        )
                        print(f"\n--------------------------------------------------\n{no_trade_msg}\n--------------------------------------------------")
                        send_discord_signal_s2(no_trade_msg)

                    with state["lock"]:
                        state["is_busy"] = False
                        state["pending_pre_alert"] = None

            last_candle_time = curr_candle_time

            if not state["is_busy"] and (time.time() - state["last_signal_time"]) >= 60:
                grade, decision, setup_key, granular_key, score, trap_risk, breakdown, trap_flags, regime, h_bias, mtf_15m, mtf_5m, target_time = analyze_candle_vision_v15(symbol, name)
                if decision in ["CALL", "PUT"] and grade in ["A+", "A"]:
                    with state["lock"]:
                        state["is_busy"] = True
                        state["pending_pre_alert"] = {"type": decision, "time": curr_candle_time}

                    pre_msg = (
                        f"⚠️ **[PRE-ALERT - S+ V15 SNIPER GRADE {grade}]**\n"
                        f"📌 **คู่เงิน:** `{name}` | **Direction:** `{decision}` | **Regime:** `{regime}`\n"
                        f"🌐 **Granular Key:** `{granular_key}`\n"
                        f"💯 **Adjusted Score:** `{score:.1f}/100` | 🛡️ **Trap Risk:** `{trap_risk}/100`"
                    )
                    print(f"\n--------------------------------------------------\n{pre_msg}\n--------------------------------------------------")
                    send_discord_signal_s2(pre_msg)

        except Exception as e:
            print(f"⚠️ Worker Error ({name}): {e}")
            
        time.sleep(1.5)

def start_script_2_system():
    print("🚀 [System 2] เริ่มทำงานระบบ S+ V15 Granular Sniper Engine...")
    send_discord_signal_s2("🏆 **[SYSTEM V15 GRANULAR ARCHITECTURE ONLINE]** Real Swing BOS/CHoCH + Strict No-Lookahead MTF + Granular Walk-Forward Validation พร้อมรบแล้ว!")
    for pair in PAIRS_S2:
        threading.Thread(target=pair_worker_s2, args=(pair,), daemon=True).start()
        time.sleep(0.3)

if __name__ == "__main__":
    print("==================================================")
    print("⚡ MAIN ENGINE: STARTING S+ V15 ARCHITECTURE")
    print("==================================================")
    
    threading.Thread(target=start_script_2_system, daemon=True).start()
    
    while True:
        time.sleep(3600)

    
    while True:
        time.sleep(3600)
