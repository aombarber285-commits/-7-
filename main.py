# -*- coding: utf-8 -*-

import os
import time
import requests
from datetime import datetime, timezone, timedelta
import yfinance as yf

# ============================================================
# SIGZY AI 15M - YFINANCE VERSION (NO API LIMIT / FREE FOREX)
# ============================================================

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
TP_ATR = 0.50
SL_ATR = 0.50

RAW_WEBHOOK = os.getenv(
    "DISCORD_WEBHOOK_URL",
    "https://discord.com/api/webhooks/1537208534058405918/555sHE5Z09zHOD8xtv7Q-fBj5NP4bUE4nkeIFz6ugqsWxEIVmEi2PX0Wxx36ZCLXlKpR"
)
DISCORD_WEBHOOK_URL = RAW_WEBHOOK.strip()
if DISCORD_WEBHOOK_URL.startswith("Https://"):
    DISCORD_WEBHOOK_URL = "https://" + DISCORD_WEBHOOK_URL[8:]

SENT_SIGNALS = set()
PENDING_TRADES = []
TRADE_HISTORY = []


def now_text():
    utc_now = datetime.now(timezone.utc)
    thai = utc_now + timedelta(hours=7)
    return thai.strftime("%Y-%m-%d %H:%M:%S")


def send_discord(message):
    if not DISCORD_WEBHOOK_URL:
        return
    try:
        requests.post(
            DISCORD_WEBHOOK_URL,
            json={"content": message},
            timeout=5
        )
    except Exception as e:
        print("Discord Error:", e)


def get_market_data(symbol):
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
        print(f"[{symbol}] yfinance Error: {e}")
        return []


def atr(candles, period=14):
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


def body(c):
    return abs(c["close"] - c["open"])


def candle_range(c):
    return max(c["high"] - c["low"], 0.00000001)


def upper_wick(c):
    return c["high"] - max(c["open"], c["close"])


def lower_wick(c):
    return min(c["open"], c["close"]) - c["low"]


def bullish(c):
    return c["close"] > c["open"]


def bearish(c):
    return c["close"] < c["open"]


def analyze_15m_opportunity(symbol, candles):
    if len(candles) < 50:
        return {"decision": "WAIT", "score": 0}

    c0 = candles[-1]
    c1 = candles[-2]
    price = c0["close"]
    current_atr = atr(candles, 14)
    ema50 = calculate_ema(candles, 50)

    if not current_atr or not ema50:
        return {"decision": "WAIT", "score": 0}

    above_ema = price > ema50
    below_ema = price < ema50

    reasons = []
    confirmations_call = 0
    confirmations_put = 0

    if above_ema:
        confirmations_call += 1
        reasons.append("Above EMA 50")
    elif below_ema:
        confirmations_put += 1
        reasons.append("Below EMA 50")

    b0 = body(c0)
    r0 = candle_range(c0)
    upper0 = upper_wick(c0)
    lower0 = lower_wick(c0)
    ratio0 = b0 / r0 if r0 > 0 else 0

    is_strong_bull = bullish(c0) and ratio0 >= 0.70
    is_strong_bear = bearish(c0) and ratio0 >= 0.70
