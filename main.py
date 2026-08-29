# -*- coding: utf-8 -*-
import os
import time
import logging
from datetime import datetime, timezone, timedelta
import numpy as np
import pandas as pd
import requests
# =========================================================
# TRADEIFY V8 CONFIG
# =========================================================
APP_NAME = "SIGZY TRADEIFY V8 SYNC"
TWELVE_DATA_API_KEY = os.getenv(
    "TWELVE_DATA_API_KEY",
    ""
)
TWELVE_DATA_URL = (
    "https://api.twelvedata.com/time_series"
)
DISCORD_WEBHOOK_URL = os.getenv(
    "DISCORD_WEBHOOK_URL",
    ""
)
PORT = int(
    os.getenv(
        "PORT",
        "8080"
    )
)
SCAN_SECONDS = int(
    os.getenv(
        "SCAN_SECONDS",
        "10"
    )
)
# =========================================================
# V8 SETTINGS
# =========================================================
EMA_FAST_LEN = 9
EMA_SLOW_LEN = 21
EMA_TREND_LEN = 50
RSI_PERIOD = 14
RSI_MID = 50
SR_PERIOD = 80
MIN_SCORE = 68
MIN_GAP = 8
PRE_SCORE = MIN_SCORE - 10
STRICT_MODE = False
# =========================================================
# TIMEZONE
# =========================================================
THAI_TZ = timezone(
    timedelta(hours=7)
)
def thai_now():
    return datetime.now(
        THAI_TZ
    )
# =========================================================
# LOGGING
# =========================================================
logging.basicConfig(
    level=logging.INFO,
    format=(
        "%(asctime)s | "
        "%(levelname)s | "
        "%(message)s"
    )
)
logger = logging.getLogger(
    APP_NAME
)
# =========================================================
# TWELVE DATA SYMBOL
# =========================================================
def normalize_twelve_symbol(symbol):
    symbol = str(
        symbol
    ).strip().upper()
    if symbol.endswith("_OTC"):
        symbol = symbol[:-4]
    if "/" in symbol:
        return symbol
    if len(symbol) == 6:
        return (
            symbol[:3]
            + "/"
            + symbol[3:]
        )
    return symbol
# =========================================================
# TIMEFRAME
# =========================================================
def normalize_interval(timeframe):
    mapping = {
        "1m": "1min",
        "5m": "5min",
        "15m": "15min",
        "30m": "30min",
        "1h": "1h",
        "4h": "4h",
        "1d": "1day"
    }
    tf = str(
        timeframe
    ).lower().strip()
    return mapping.get(
        tf,
        tf
    )
# =========================================================
# GET MARKET DATA
# =========================================================
def get_market_data(
    symbol,
    timeframe,
    limit=200
):
    if not TWELVE_DATA_API_KEY:
        raise RuntimeError(
            "ไม่พบ TWELVE_DATA_API_KEY "
            "กรุณาเพิ่มใน Railway Variables"
        )
    td_symbol = normalize_twelve_symbol(
        symbol
    )
    interval = normalize_interval(
        timeframe
    )
    limit = max(
        100,
        min(
            int(limit),
            5000
        )
    )
    params = {
        "symbol": td_symbol,
        "interval": interval,
        "outputsize": limit,
        "timezone": "Asia/Bangkok",
        "apikey": TWELVE_DATA_API_KEY
    }
    logger.info(
        "Request Twelve Data: %s %s",
        td_symbol,
        interval
    )
    try:
        response = requests.get(
            TWELVE_DATA_URL,
            params=params,
            timeout=15
        )
        response.raise_for_status()
    except requests.RequestException as e:
        raise RuntimeError(
            f"Twelve Data connection error: {e}"
        )
    try:
        data = response.json()
    except ValueError:
        raise RuntimeError(
            "Twelve Data ส่งข้อมูล JSON ไม่ถูกต้อง"
        )
    if data.get("status") == "error":
        raise RuntimeError(
            "Twelve Data API Error: "
            + str(
                data.get(
                    "message",
                    "Unknown error"
                )
            )
        )
    values = data.get(
        "values"
    )
    if not values:
        raise RuntimeError(
            f"ไม่มีข้อมูลจาก Twelve Data: "
            f"{td_symbol} {interval}"
        )
    df = pd.DataFrame(
        values
    )
    required = [
        "datetime",
        "open",
        "high",
        "low",
        "close"
    ]
    missing = [
        x for x in required
        if x not in df.columns
    ]
    if missing:
        raise RuntimeError(
            "ข้อมูลขาด column: "
            + ", ".join(missing)
        )
    for column in [
        "open",
        "high",
        "low",
        "close"
    ]:
        df[column] = pd.to_numeric(
            df[column],
            errors="coerce"
        )
    df["datetime"] = pd.to_datetime(
        df["datetime"],
        errors="coerce"
    )
    df = df.dropna(
        subset=[
            "datetime",
            "open",
            "high",
            "low",
            "close"
        ]
    )
    df = df.sort_values(
        "datetime"
    )
    df = df.reset_index(
        drop=True
    )
    if len(df) < 100:
        raise RuntimeError(
            f"ข้อมูลไม่พอ: "
            f"{td_symbol} {interval} "
            f"มีเพียง {len(df)} candles"
        )
    logger.info(
        "Twelve Data OK | %s | %s | %s candles | latest=%s",
        td_symbol,
        interval,
        len(df),
        df["datetime"].iloc[-1]
    )
    return df[
        [
            "datetime",
            "open",
            "high",
            "low",
            "close"
        ]
    ]
# =========================================================
# TEST
# =========================================================
if __name__ == "__main__":
    logger.info(
        "TRADEIFY V8 START"
    )
    logger.info(
        "API KEY: %s",
        "SET" if TWELVE_DATA_API_KEY else "NOT SET"
    )
    logger.info(
        "Discord: %s",
        "SET" if DISCORD_WEBHOOK_URL else "NOT SET"
    )
    logger.info(
        "Port: %s",
        PORT
    )
    logger.info(
        "Scan: %s seconds",
        SCAN_SECONDS
    )
    # ทดสอบดึงข้อมูล 5M
    try:
        test_df = get_market_data(
            "EURUSD_OTC",
            "5m",
            200
        )
        logger.info(
            "TEST SUCCESS: %s candles",
            len(test_df)
        )
        logger.info(
            "LATEST CLOSE: %s",
            test_df["close"].iloc[-1]
        )
    except Exception as e:
        logger.error(
            "MARKET DATA ERROR: %s",
            e
        )
