# -*- coding: utf-8 -*-
from datetime import datetime
from tracker import ACTIVE_TRACKERS, get_closed_candles

PROCESSED_CANDLES = set()
LAST_SIGNAL_TIME = {}

def calculate_atr(candles, period=14):
    if len(candles) < period + 1:
        return 0.0010
    trs = []
    for i in range(1, len(candles)):
        c, p = candles[i], candles[i - 1]
        tr = max(c["high"] - c["low"], abs(c["high"] - p["close"]), abs(c["low"] - p["close"]))
        trs.append(tr)
    return sum(trs[-period:]) / period

def analyze_reversal_zone_and_touches(candles, lookback=35):
    if len(candles) < lookback:
        return None, None, 0, 0, False, False

    zone_candles = candles[-lookback:-1]
    c0 = candles[-1]

    swing_high = max(c["high"] for c in zone_candles)
    swing_low = min(c["low"] for c in zone_candles)

    high_touches = sum(1 for c in zone_candles if c["high"] >= swing_high * 0.9995)
    low_touches = sum(1 for c in zone_candles if c["low"] <= swing_low * 1.0005)

    at_resistance = (c0["high"] >= swing_high * 0.9995)
    at_support = (c0["low"] <= swing_low * 1.0005)

    return swing_high, swing_low, high_touches, low_touches, at_resistance, at_support

def calculate_reversal_strength(candle, is_resistance):
    c_open, c_close = candle["open"], candle["close"]
    c_high, c_low = candle["high"], candle["low"]
    total_range = max(c_high - c_low, 0.00001)

    strength = 0
    if is_resistance:
        upper_wick = c_high - max(c_open, c_close)
        if upper_wick / total_range >= 0.4: strength += 30
        if c_close < c_open: strength += 20
    else:
        lower_wick = min(c_open, c_close) - c_low
        if lower_wick / total_range >= 0.4: strength += 30
        if c_close > c_open: strength += 20

    return strength

def analyze_v13_signal(symbol, candles):
    if len(candles) < 50:
        return None

    c0 = candles[-1]
    candle_time = c0["datetime"]
    unique_key = f"{symbol}_{candle_time}"

    if unique_key in PROCESSED_CANDLES:
        return None

    if symbol in LAST_SIGNAL_TIME:
        last_time = datetime.strptime(LAST_SIGNAL_TIME[symbol], "%Y-%m-%d %H:%M:%S")
        curr_time = datetime.strptime(candle_time, "%Y-%m-%d %H:%M:%S")
        if (curr_time - last_time).total_seconds() < 1800:
            return None

    for tracker in ACTIVE_TRACKERS:
        if tracker["symbol"] == symbol:
            return None

    swing_high, swing_low, high_touches, low_touches, at_res, at_sup = analyze_reversal_zone_and_touches(candles)
    atr_val = calculate_atr(candles, 14)

    score_call, score_put = 0, 0
    reasons = []

    if at_res:
        rev_strength = calculate_reversal_strength(c0, is_resistance=True)
        score_put += 30 + rev_strength
        reasons.append(f"Resistance Touch ({high_touches}x, Strength: {rev_strength})")
        if high_touches > 3: score_put -= 15

    if at_sup:
        rev_strength = calculate_reversal_strength(c0, is_resistance=False)
        score_call += 30 + rev_strength
        reasons.append(f"Support Touch ({low_touches}x, Strength: {rev_strength})")
        if low_touches > 3: score_call -= 15

    THRESHOLD = 50

    if score_put >= THRESHOLD and score_put > score_call:
        PROCESSED_CANDLES.add(unique_key)
        LAST_SIGNAL_TIME[symbol] = candle_time
        return {
            "symbol": symbol, "decision": "PUT", "score": score_put,
            "entry_price": c0["close"], "atr": atr_val, "signal_time": candle_time,
            "setup_name": f"Resistance Touch ({high_touches}x)", "reasons": " | ".join(reasons)
        }

    if score_call >= THRESHOLD and score_call > score_put:
        PROCESSED_CANDLES.add(unique_key)
        LAST_SIGNAL_TIME[symbol] = candle_time
        return {
            "symbol": symbol, "decision": "CALL", "score": score_call,
            "entry_price": c0["close"], "atr": atr_val, "signal_time": candle_time,
            "setup_name": f"Support Touch ({low_touches}x)", "reasons": " | ".join(reasons)
        }

    return None
