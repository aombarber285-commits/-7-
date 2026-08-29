TRADEIFY V8.3 — IQ OPTION OTC 8 PAIRS

# -*- coding: utf-8 -*-
import os
import time
import logging
import threading
from datetime import datetime, timezone, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
import requests
import pandas as pd
import numpy as np
# =========================================================
# TRADEIFY V8.3 IQ OPTION OTC
# =========================================================
#
# 15M = MASTER
# 5M  = ENTRY
#
# 8 OTC PAIRS
#
# NO TWELVE DATA
# NO OTC_API_URL
#
# IMPORTANT:
# get_market_data() ต้องเชื่อมกับ IQ Option
# ก่อนจึงจะมีราคาจริง
# =========================================================
APP_NAME = "TRADEIFY V8.3 IQ OPTION OTC"
# =========================================================
# RAILWAY
# =========================================================
PORT = int(
    os.getenv(
        "PORT",
        "8080"
    )
)
SCAN_SECONDS = int(
    os.getenv(
        "SCAN_SECONDS",
        "20"
    )
)
DISCORD_WEBHOOK_URL = os.getenv(
    "DISCORD_WEBHOOK_URL",
    ""
).strip()
# =========================================================
# IQ OPTION LOGIN
# =========================================================
IQ_EMAIL = os.getenv(
    "IQ_EMAIL",
    ""
).strip()
IQ_PASSWORD = os.getenv(
    "IQ_PASSWORD",
    ""
).strip()
# =========================================================
# 8 OTC PAIRS
# =========================================================
SYMBOLS = [
    "EURUSD-OTC",
    "GBPUSD-OTC",
    "USDJPY-OTC",
    "AUDUSD-OTC",
    "EURJPY-OTC",
    "GBPJPY-OTC",
    "EURGBP-OTC",
    "AUDJPY-OTC",
]
# =========================================================
# STRATEGY
# =========================================================
MIN_SCORE = 68
MIN_GAP = 8
PRE_SCORE = 58
EMA_FAST = 9
EMA_SLOW = 21
EMA_TREND = 50
RSI_PERIOD = 14
RSI_MID = 50
SR_PERIOD = 80
STRICT_MODE = False
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
# TIMEZONE
# =========================================================
THAI_TZ = timezone(
    timedelta(
        hours=7
    )
)
def thai_now():
    return datetime.now(
        THAI_TZ
    )
# =========================================================
# MEMORY
# =========================================================
LAST_SIGNAL = {}
LAST_CANDLE = {}
LAST_DISCORD_SEND = 0
DISCORD_LOCK = threading.Lock()
# =========================================================
# HEALTH SERVER
# =========================================================
class HealthHandler(
    BaseHTTPRequestHandler
):
    def do_GET(
        self
    ):
        body = (
            "TRADEIFY V8.3 IQ OPTION ONLINE"
        ).encode(
            "utf-8"
        )
        self.send_response(
            200
        )
        self.send_header(
            "Content-Type",
            "text/plain; charset=utf-8"
        )
        self.send_header(
            "Content-Length",
            str(
                len(body)
            )
        )
        self.end_headers()
        self.wfile.write(
            body
        )
    def log_message(
        self,
        format,
        *args
    ):
        return
def health_server():
    server = HTTPServer(
        (
            "0.0.0.0",
            PORT
        ),
        HealthHandler
    )
    logger.info(
        "HEALTH SERVER :%s",
        PORT
    )
    server.serve_forever()
# =========================================================
# IQ OPTION CONNECTOR
# =========================================================
IQ_API = None
def connect_iq_option():
    global IQ_API
    if not IQ_EMAIL or not IQ_PASSWORD:
        logger.error(
            "IQ_EMAIL / IQ_PASSWORD NOT SET"
        )
        return False
    try:
        from iqoptionapi.stable_api import (
            IQ_Option
        )
    except ImportError:
        logger.error(
            "iqoptionapi ยังไม่ได้ติดตั้ง"
        )
        logger.error(
            "เพิ่ม dependency:"
        )
        logger.error(
            "iqoptionapi"
        )
        return False
    try:
        IQ_API = IQ_Option(
            IQ_EMAIL,
            IQ_PASSWORD
        )
        check, reason = IQ_API.connect()
        if not check:
            logger.error(
                "IQ OPTION CONNECT FAILED: %s",
                reason
            )
            IQ_API = None
            return False
        logger.info(
            "IQ OPTION CONNECTED"
        )
        return True
    except Exception as e:
        logger.exception(
            "IQ OPTION CONNECT ERROR: %s",
            e
        )
        IQ_API = None
        return False
# =========================================================
# IQ OPTION CANDLES
# =========================================================
def get_market_data(
    symbol,
    timeframe="5m",
    limit=220
):
    global IQ_API
    if IQ_API is None:
        if not connect_iq_option():
            raise RuntimeError(
                "IQ Option ยังไม่ได้เชื่อมต่อ"
            )
    if timeframe == "5m":
        seconds = 300
    elif timeframe == "15m":
        seconds = 900
    else:
        raise ValueError(
            "รองรับเฉพาะ 5m / 15m"
        )
    try:
        candles = IQ_API.get_candles(
            symbol,
            seconds,
            limit,
            time.time()
        )
    except Exception as e:
        IQ_API = None
        raise RuntimeError(
            "IQ Option get_candles failed: "
            +
            str(e)
        )
    if not candles:
        raise RuntimeError(
            "IQ Option ไม่ส่ง candle "
            +
            symbol
        )
    rows = []
    for candle in candles:
        rows.append({
            "datetime":
                datetime.fromtimestamp(
                    float(
                        candle["from"]
                    ),
                    timezone.utc
                ).astimezone(
                    THAI_TZ
                ),
            "open":
                float(
                    candle["open"]
                ),
            "high":
                float(
                    candle["max"]
                ),
            "low":
                float(
                    candle["min"]
                ),
            "close":
                float(
                    candle["close"]
                )
        })
    df = pd.DataFrame(
        rows
    )
    df = df.sort_values(
        "datetime"
    )
    df = df.drop_duplicates(
        "datetime"
    )
    return df.reset_index(
        drop=True
    )
# =========================================================
# REMOVE CURRENT CANDLE
# =========================================================
def remove_incomplete(
    df,
    timeframe
):
    if df is None or df.empty:
        return df
    df = df.copy()
    now = thai_now()
    if timeframe == "5m":
        seconds = 300
    else:
        seconds = 900
    epoch = now.timestamp()
    closed_epoch = (
        int(epoch // seconds)
        *
        seconds
    )
    cutoff = datetime.fromtimestamp(
        closed_epoch,
        THAI_TZ
    )
    return df[
        df["datetime"] <
        cutoff
    ].reset_index(
        drop=True
    )
# =========================================================
# EMA
# =========================================================
def ema(
    series,
    period
):
    return series.ewm(
        span=period,
        adjust=False
    ).mean()
# =========================================================
# RSI
# =========================================================
def rsi(
    series,
    period=14
):
    delta = series.diff()
    gain = delta.clip(
        lower=0
    )
    loss = -delta.clip(
        upper=0
    )
    avg_gain = gain.ewm(
        alpha=1 / period,
        adjust=False
    ).mean()
    avg_loss = loss.ewm(
        alpha=1 / period,
        adjust=False
    ).mean()
    rs = (
        avg_gain /
        avg_loss.replace(
            0,
            np.nan
        )
    )
    result = (
        100 -
        (
            100 /
            (
                1 + rs
            )
        )
    )
    return result.fillna(
        50
    )
# =========================================================
# INDICATORS
# =========================================================
def add_indicators(
    df
):
    df = df.copy()
    df["ema_fast"] = ema(
        df["close"],
        EMA_FAST
    )
    df["ema_slow"] = ema(
        df["close"],
        EMA_SLOW
    )
    df["ema_trend"] = ema(
        df["close"],
        EMA_TREND
    )
    df["rsi"] = rsi(
        df["close"],
        RSI_PERIOD
    )
    return df
# =========================================================
# ANALYZE
# =========================================================
def analyze_tf(
    df
):
    if df is None:
        return None
    if len(df) < 85:
        return None
    i = -1
    p = -2
    o = float(
        df["open"].iloc[i]
    )
    h = float(
        df["high"].iloc[i]
    )
    l = float(
        df["low"].iloc[i]
    )
    c = float(
        df["close"].iloc[i]
    )
    ph = float(
        df["high"].iloc[p]
    )
    pl = float(
        df["low"].iloc[p]
    )
    pc = float(
        df["close"].iloc[p]
    )
    bull = c > o
    bear = c < o
    structure_up = (
        h >= ph
        or
        l >= pl
    )
    structure_down = (
        h <= ph
        or
        l <= pl
    )
    trend_call = (
        bull
        and
        structure_up
        and
        c >= pc
    )
    trend_put = (
        bear
        and
        structure_down
        and
        c <= pc
    )
    ef = float(
        df["ema_fast"].iloc[i]
    )
    es = float(
        df["ema_slow"].iloc[i]
    )
    et = float(
        df["ema_trend"].iloc[i]
    )
    ema_call = (
        ef > es
        and
        es > et
    )
    ema_put = (
        ef < es
        and
        es < et
    )
    rv = float(
        df["rsi"].iloc[i]
    )
    candle_range = max(
        h - l,
        1e-10
    )
    upper_wick = (
        h -
        max(o, c)
    )
    lower_wick = (
        min(o, c) -
        l
    )
    upper_ratio = (
        upper_wick /
        candle_range
    )
    lower_ratio = (
        lower_wick /
        candle_range
    )
    bull_rejection = (
        bull
        and
        lower_ratio >= 0.18
    )
    bear_rejection = (
        bear
        and
        upper_ratio >= 0.18
    )
    c1 = float(
        df["close"].iloc[-1]
    )
    c2 = float(
        df["close"].iloc[-2]
    )
    c3 = float(
        df["close"].iloc[-3]
    )
    flow_up = (
        c1 >= c2
        and
        c2 >= c3
    )
    flow_down = (
        c1 <= c2
        and
        c2 <= c3
    )
    recent = df.iloc[
        :-1
    ].tail(
        SR_PERIOD
    )
    support = float(
        recent["low"].min()
    )
    resistance = float(
        recent["high"].max()
    )
    sr_range = max(
        resistance -
        support,
        1e-10
    )
    near_support = (
        c <=
        support +
        sr_range * 0.22
    )
    near_resistance = (
        c >=
        resistance -
        sr_range * 0.22
    )
    room_call = (
        resistance - c
    ) / sr_range
    room_put = (
        c - support
    ) / sr_range
    pullback_call = (
        (
            l <= ef
            or
            l <= es
            or
            near_support
        )
        and
        c >= ef
    )
    pullback_put = (
        (
            h >= ef
            or
            h >= es
            or
            near_resistance
        )
        and
        c <= ef
    )
    call_score = 0
    put_score = 0
    if trend_call:
        call_score += 30
    if trend_put:
        put_score += 30
    if ema_call:
        call_score += 12
    if ema_put:
        put_score += 12
    if flow_up:
        call_score += 8
    if flow_down:
        put_score += 8
    if bull_rejection:
        call_score += 12
    if bear_rejection:
        put_score += 12
    if rv > RSI_MID:
        call_score += 5
    if rv < RSI_MID:
        put_score += 5
    if pullback_call:
        call_score += 8
    if pullback_put:
        put_score += 8
    if room_call >= 0.15:
        call_score += 5
    if room_put >= 0.15:
        put_score += 5
    if near_support:
        call_score += 5
    if near_resistance:
        put_score += 5
    if near_resistance:
        call_score -= 8
    if near_support:
        put_score -= 8
    call_score = int(
        max(
            0,
            min(
                call_score,
                100
            )
        )
    )
    put_score = int(
        max(
            0,
            min(
                put_score,
                100
            )
        )
    )
    gap = (
        call_score -
        put_score
    )
    if (
        call_score >= MIN_SCORE
        and
        gap >= MIN_GAP
    ):
        direction = "CALL"
    elif (
        put_score >= MIN_SCORE
        and
        gap <= -MIN_GAP
    ):
        direction = "PUT"
    elif (
        call_score >= PRE_SCORE
        and
        gap >= MIN_GAP
    ):
        direction = "PRE CALL"
    elif (
        put_score >= PRE_SCORE
        and
        gap <= -MIN_GAP
    ):
        direction = "PRE PUT"
    else:
        direction = "WAIT"
    return {
        "direction":
            direction,
        "call":
            call_score,
        "put":
            put_score,
        "gap":
            gap,
        "rsi":
            rv,
        "ema_call":
            ema_call,
        "ema_put":
            ema_put,
        "flow_up":
            flow_up,
        "flow_down":
            flow_down,
        "bull_rejection":
            bull_rejection,
        "bear_rejection":
            bear_rejection,
        "pullback_call":
            pullback_call,
        "pullback_put":
            pullback_put,
        "price":
            c,
        "support":
            support,
        "resistance":
            resistance
    }
# =========================================================
# V8.3 ANALYSIS
# =========================================================
def analyze_v83(
    df15,
    df5
):
    master = analyze_tf(
        df15
    )
    entry = analyze_tf(
        df5
    )
    if master is None or entry is None:
        return None
    master_call = (
        master["direction"]
        == "CALL"
    )
    master_put = (
        master["direction"]
        == "PUT"
    )
    entry_call = (
        entry["call"]
        >= MIN_SCORE
        and
        entry["gap"]
        >= MIN_GAP
        and
        entry["ema_call"]
        and
        entry["bull_rejection"]
        and
        entry["pullback_call"]
    )
    entry_put = (
        entry["put"]
        >= MIN_SCORE
        and
        entry["gap"]
        <= -MIN_GAP
        and
        entry["ema_put"]
        and
        entry["bear_rejection"]
        and
        entry["pullback_put"]
    )
    if STRICT_MODE:
        entry_call = (
            entry_call
            and
            entry["flow_up"]
        )
        entry_put = (
            entry_put
            and
            entry["flow_down"]
        )
    final = "WAIT"
    if master_call and entry_call:
        final = "CALL"
    elif master_put and entry_put:
        final = "PUT"
    elif (
        master_call
        and
        entry["call"] >= PRE_SCORE
        and
        entry["gap"] >= MIN_GAP
    ):
        final = "PRE CALL"
    elif (
        master_put
        and
        entry["put"] >= PRE_SCORE
        and
        entry["gap"] <= -MIN_GAP
    ):
        final = "PRE PUT"
    return {
        "final":
            final,
        "master":
            master,
        "entry":
            entry
    }
# =========================================================
# DISCORD
# =========================================================
def send_discord(
    message
):
    global LAST_DISCORD_SEND
    if not DISCORD_WEBHOOK_URL:
        logger.warning(
            "DISCORD_WEBHOOK_URL NOT SET"
        )
        return False
    with DISCORD_LOCK:
        wait = (
            1.5 -
            (
                time.time()
                -
                LAST_DISCORD_SEND
            )
        )
        if wait > 0:
            time.sleep(
                wait
            )
        for _ in range(5):
            try:
                response = requests.post(
                    DISCORD_WEBHOOK_URL,
                    json={
                        "content":
                            message
                    },
                    timeout=15
                )
                if response.status_code in (
                    200,
                    204
                ):
                    LAST_DISCORD_SEND = (
                        time.time()
                    )
                    return True
                if response.status_code == 429:
                    try:
                        retry = float(
                            response.json().get(
                                "retry_after",
                                5
                            )
                        )
                    except Exception:
                        retry = 5
                    time.sleep(
                        min(
                            max(
                                retry,
                                2
                            ),
                            60
                        )
                    )
                    continue
                logger.error(
                    "DISCORD ERROR %s | %s",
                    response.status_code,
                    response.text[:500]
                )
                return False
            except Exception as e:
                logger.error(
                    "DISCORD ERROR: %s",
                    e
                )
                time.sleep(
                    3
                )
        return False
# =========================================================
# MESSAGE
# =========================================================
def make_message(
    symbol,
    result
):
    m = result[
        "master"
    ]
    e = result[
        "entry"
    ]
    final = result[
        "final"
    ]
    emoji = {
        "CALL":
            "🟢",
        "PUT":
            "🔴",
        "PRE CALL":
            "🟡",
        "PRE PUT":
            "🟡",
    }.get(
        final,
        "⚪"
    )
    return (
        f"{emoji} **TRADEIFY V8.3 IQ OTC**\n\n"
        f"💱 `{symbol}`\n"
        f"🚨 **{final}**\n\n"
        "━━━━━━━━━━━━━━\n"
        "📊 **15M MASTER**\n"
        f"Direction: `{m['direction']}`\n"
        f"CALL: `{m['call']}`\n"
        f"PUT: `{m['put']}`\n"
        f"GAP: `{m['gap']:+d}`\n"
        f"RSI: `{m['rsi']:.1f}`\n\n"
        "━━━━━━━━━━━━━━\n"
        "🎯 **5M ENTRY**\n"
        f"Direction: `{e['direction']}`\n"
        f"CALL: `{e['call']}`\n"
        f"PUT: `{e['put']}`\n"
        f"GAP: `{e['gap']:+d}`\n"
        f"RSI: `{e['rsi']:.1f}`\n\n"
        "━━━━━━━━━━━━━━\n"
        "⚠️ ใช้เป็นสัญญาณวิเคราะห์ "
        "ไม่ใช่การรับประกันผลลัพธ์"
    )
# =========================================================
# PROCESS
# =========================================================
def process_symbol(
    symbol
):
    try:
        df5 = get_market_data(
            symbol,
            "5m",
            220
        )
        df5 = remove_incomplete(
            df5,
            "5m"
        )
        if len(df5) < 100:
            logger.warning(
                "%s | 5M insufficient",
                symbol
            )
            return
        df15 = get_market_data(
            symbol,
            "15m",
            120
        )
        df15 = remove_incomplete(
            df15,
            "15m"
        )
        if len(df15) < 85:
            logger.warning(
                "%s | 15M insufficient",
                symbol
            )
            return
        df5 = add_indicators(
            df5
        )
        df15 = add_indicators(
            df15
        )
        candle_time = str(
            df5[
                "datetime"
            ].iloc[-1]
        )
        if (
            LAST_CANDLE.get(
                symbol
            )
            ==
            candle_time
        ):
            return
        LAST_CANDLE[
            symbol
        ] = candle_time
        result = analyze_v83(
            df15,
            df5
        )
        if result is None:
            return
        m = result[
            "master"
        ]
        e = result[
            "entry"
        ]
        final = result[
            "final"
        ]
        logger.info(
            "%s | "
            "15M=%s %d/%d GAP=%+d | "
            "5M=%s %d/%d GAP=%+d | "
            "FINAL=%s",
            symbol,
            m["direction"],
            m["call"],
            m["put"],
            m["gap"],
            e["direction"],
            e["call"],
            e["put"],
            e["gap"],
            final
        )
        if final not in (
            "CALL",
            "PUT",
            "PRE CALL",
            "PRE PUT"
        ):
            return
        signal_id = (
            symbol,
            candle_time,
            final
        )
        if signal_id in LAST_SIGNAL:
            return
        if send_discord(
            make_message(
                symbol,
                result
            )
        ):
            LAST_SIGNAL[
                signal_id
            ] = time.time()
    except Exception as e:
        logger.exception(
            "%s ERROR: %s",
            symbol,
            e
        )
# =========================================================
# MARKET LOOP
# =========================================================
def process_market():
    logger.info(
        "===== IQ OTC SCAN START ====="
    )
    for symbol in SYMBOLS:
        process_symbol(
            symbol
        )
        time.sleep(
            0.5
        )
    logger.info(
        "===== IQ OTC SCAN END ====="
    )
# =========================================================
# STARTUP
# =========================================================
def scanner_loop():
    logger.info(
        "================================"
    )
    logger.info(
        "TRADEIFY V8.3 IQ OPTION OTC"
    )
    logger.info(
        "PAIRS = %d",
        len(SYMBOLS)
    )
    logger.info(
        "15M = MASTER"
    )
    logger.info(
        "5M = ENTRY"
    )
    logger.info(
        "Twelve Data = OFF"
    )
    logger.info(
        "================================"
    )
    if not connect_iq_option():
        logger.error(
            "IQ OPTION NOT CONNECTED"
        )
        logger.error(
            "Scanner will stay alive "
            "without generating fake signals"
        )
    while True:
        started = time.time()
        try:
            process_market()
        except Exception as e:
            logger.exception(
                "MARKET LOOP ERROR: %s",
                e
            )
        elapsed = (
            time.time()
            -
            started
        )
        sleep_time = max(
            SCAN_SECONDS -
            elapsed,
            5
        )
        logger.info(
            "NEXT SCAN IN %.1f SEC",
            sleep_time
        )
        time.sleep(
            sleep_time
        )
# =========================================================
# MAIN
# =========================================================
def main():
    logger.info(
        "================================"
    )
    logger.info(
        "TRADEIFY V8.3 START"
    )
    logger.info(
        "IQ EMAIL: %s",
        "SET"
        if IQ_EMAIL
        else "NOT SET"
    )
    logger.info(
        "DISCORD: %s",
        "SET"
        if DISCORD_WEBHOOK_URL
        else "NOT SET"
    )
    logger.info(
        "PORT: %s",
        PORT
    )
    logger.info(
        "SCAN: %s SEC",
        SCAN_SECONDS
    )
    logger.info(
        "================================"
    )
    thread = threading.Thread(
        target=health_server,
        daemon=True
    )
    thread.start()
    scanner_loop()
if __name__ == "__main__":
    main()
