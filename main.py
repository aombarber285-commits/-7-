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
# TRADEIFY V8.2 OTC
# =========================================================
#
# OTC 8 PAIRS
#
# 15M = MASTER
# 5M  = ENTRY
#
# Score / GAP
# PRE / CALL / PUT
#
# NO TWELVE DATA
#
# Railway Ready
# Discord Rate Limit Protection
# =========================================================


APP_NAME = "TRADEIFY V8.2 OTC"


# =========================================================
# ENV
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

OTC_API_URL = os.getenv(
    "OTC_API_URL",
    ""
).strip()

OTC_API_KEY = os.getenv(
    "OTC_API_KEY",
    ""
).strip()


# =========================================================
# OTC 8 PAIRS
# =========================================================

SYMBOLS = [

    "EURUSD_OTC",

    "GBPUSD_OTC",

    "USDJPY_OTC",

    "AUDUSD_OTC",

    "EURJPY_OTC",

    "GBPJPY_OTC",

    "EURGBP_OTC",

    "AUDJPY_OTC",

]


# =========================================================
# SETTINGS
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
# LOG
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
# TIME
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

API_LOCK = threading.Lock()


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
            "TRADEIFY V8.2 OTC ONLINE"
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
# NORMALIZE DATA
# =========================================================

def normalize_dataframe(
    data
):

    if isinstance(
        data,
        list
    ):

        df = pd.DataFrame(
            data
        )

    elif isinstance(
        data,
        dict
    ):

        if "data" in data:

            data = data["data"]

        elif "candles" in data:

            data = data["candles"]

        elif "values" in data:

            data = data["values"]

        elif "result" in data:

            data = data["result"]

        if isinstance(
            data,
            list
        ):

            df = pd.DataFrame(
                data
            )

        elif isinstance(
            data,
            dict
        ):

            df = pd.DataFrame(
                data
            )

        else:

            return None

    else:

        return None

    # -----------------------------------------------------
    # Rename common API fields
    # -----------------------------------------------------

    rename = {

        "time": "datetime",

        "timestamp": "datetime",

        "date": "datetime",

        "o": "open",

        "h": "high",

        "l": "low",

        "c": "close",

    }

    df = df.rename(
        columns=rename
    )

    required = [
        "open",
        "high",
        "low",
        "close"
    ]

    for col in required:

        if col not in df.columns:

            return None

    if "datetime" not in df.columns:

        if "timestamp" in df.columns:

            df["datetime"] = (
                df["timestamp"]
            )

        else:

            df["datetime"] = range(
                len(df)
            )

    for col in required:

        df[col] = pd.to_numeric(
            df[col],
            errors="coerce"
        )

    df["datetime"] = pd.to_datetime(
        df["datetime"],
        errors="coerce"
    )

    df = df.dropna(
        subset=[
            "open",
            "high",
            "low",
            "close"
        ]
    )

    df = df.sort_values(
        "datetime"
    )

    df = df.drop_duplicates(
        subset=[
            "datetime"
        ]
    )

    df = df.reset_index(
        drop=True
    )

    if len(df) < 100:

        return None

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
# REMOVE INCOMPLETE 5M
# =========================================================

def remove_incomplete_5m(
    df
):

    if df is None or df.empty:

        return df

    df = df.copy()

    now = thai_now()

    minute = (
        now.minute // 5
    ) * 5

    current = now.replace(
        minute=minute,
        second=0,
        microsecond=0
    )

    dt = pd.to_datetime(
        df["datetime"],
        errors="coerce"
    )

    if dt.dt.tz is None:

        dt = dt.dt.tz_localize(
            THAI_TZ
        )

    else:

        dt = dt.dt.tz_convert(
            THAI_TZ
        )

    df["datetime"] = dt

    return df[
        df["datetime"] <
        current
    ].reset_index(
        drop=True
    )


# =========================================================
# BUILD 15M
# =========================================================

def build_15m(
    df5
):

    df = df5.copy()

    df = df.set_index(
        "datetime"
    )

    df15 = df.resample(
        "15min",
        label="left",
        closed="left"
    ).agg({

        "open":
            "first",

        "high":
            "max",

        "low":
            "min",

        "close":
            "last"

    })

    df15 = df15.dropna()

    df15 = df15.reset_index()

    return df15


# =========================================================
# ADD INDICATORS
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
# ANALYZE TF
# =========================================================

def analyze_tf(
    df
):

    if len(df) < 85:

        return None

    i = -1

    p = -2

    # -----------------------------------------------------
    # Candle
    # -----------------------------------------------------

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

    po = float(
        df["open"].iloc[p]
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

    # -----------------------------------------------------
    # Structure
    # -----------------------------------------------------

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

    # -----------------------------------------------------
    # EMA
    # -----------------------------------------------------

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

    # -----------------------------------------------------
    # RSI
    # -----------------------------------------------------

    rsi_value = float(
        df["rsi"].iloc[i]
    )

    # -----------------------------------------------------
    # Candle anatomy
    # -----------------------------------------------------

    candle_range = max(
        h - l,
        0.00000001
    )

    upper_wick = (
        h -
        max(
            o,
            c
        )
    )

    lower_wick = (
        min(
            o,
            c
        ) -
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

    # -----------------------------------------------------
    # FLOW
    # -----------------------------------------------------

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

    # -----------------------------------------------------
    # S/R
    # -----------------------------------------------------

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
        0.00000001
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
        resistance -
        c
    ) / sr_range

    room_put = (
        c -
        support
    ) / sr_range

    enough_room_call = (
        room_call >= 0.15
    )

    enough_room_put = (
        room_put >= 0.15
    )

    # -----------------------------------------------------
    # Pullback
    # -----------------------------------------------------

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

    # -----------------------------------------------------
    # Score
    # -----------------------------------------------------

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

    if rsi_value > RSI_MID:

        call_score += 5

    if rsi_value < RSI_MID:

        put_score += 5

    if pullback_call:

        call_score += 8

    if pullback_put:

        put_score += 8

    if enough_room_call:

        call_score += 5

    if enough_room_put:

        put_score += 5

    if near_support:

        call_score += 5

    if near_resistance:

        put_score += 5

    # -----------------------------------------------------
    # Penalty
    # -----------------------------------------------------

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

    # -----------------------------------------------------
    # Direction
    # -----------------------------------------------------

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
            rsi_value,

        "trend_call":
            trend_call,

        "trend_put":
            trend_put,

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

        "support":
            support,

        "resistance":
            resistance,

        "price":
            c

    }


# =========================================================
# V8 MASTER / ENTRY
# =========================================================

def analyze_v82(
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

    if (
        master_call
        and
        entry_call
    ):

        final = "CALL"

    elif (
        master_put
        and
        entry_put
    ):

        final = "PUT"

    elif (
        master_call
        and
        entry["call"]
        >= PRE_SCORE
        and
        entry["gap"]
        >= MIN_GAP
    ):

        final = "PRE CALL"

    elif (
        master_put
        and
        entry["put"]
        >= PRE_SCORE
        and
        entry["gap"]
        <= -MIN_GAP
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
# OTC API
# =========================================================

def get_market_data(
    symbol,
    timeframe="5m",
    limit=220
):

    if not OTC_API_URL:

        raise RuntimeError(
            "OTC_API_URL ยังไม่ได้ตั้งค่า "
            "ใน Railway Variables"
        )

    params = {

        "symbol":
            symbol,

        "timeframe":
            timeframe,

        "limit":
            limit

    }

    headers = {}

    if OTC_API_KEY:

        headers[
            "Authorization"
        ] = (
            "Bearer "
            +
            OTC_API_KEY
        )

        headers[
            "X-API-Key"
        ] = OTC_API_KEY

    with API_LOCK:

        response = requests.get(

            OTC_API_URL,

            params=params,

            headers=headers,

            timeout=20

        )

    if response.status_code != 200:

        raise RuntimeError(
            "OTC API HTTP "
            +
            str(
                response.status_code
            )
            +
            " | "
            +
            response.text[:500]
        )

    try:

        payload = response.json()

    except Exception:

        raise RuntimeError(
            "OTC API ไม่ได้ส่ง JSON"
        )

    df = normalize_dataframe(
        payload
    )

    if df is None:

        raise RuntimeError(
            "OTC API format ไม่ตรงกับ "
            "open/high/low/close/datetime"
        )

    return df


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

        # -------------------------------------------------
        # Basic spacing
        # -------------------------------------------------

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

        for attempt in range(5):

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

                    logger.info(
                        "DISCORD SENT"
                    )

                    return True

                # -----------------------------------------
                # Discord 429
                # -----------------------------------------

                if response.status_code == 429:

                    retry_after = 5

                    try:

                        payload = (
                            response.json()
                        )

                        retry_after = float(
                            payload.get(
                                "retry_after",
                                5
                            )
                        )

                    except Exception:

                        pass

                    retry_after = min(
                        max(
                            retry_after,
                            2
                        ),
                        60
                    )

                    logger.warning(
                        "DISCORD 429 "
                        "WAIT %.2fs",
                        retry_after
                    )

                    time.sleep(
                        retry_after
                    )

                    continue

                logger.error(
                    "DISCORD ERROR %s | %s",
                    response.status_code,
                    response.text[:500]
                )

                return False

            except requests.RequestException as e:

                logger.error(
                    "DISCORD CONNECTION ERROR %s",
                    e
                )

                time.sleep(
                    3
                )

        return False


# =========================================================
# DISCORD MESSAGE
# =========================================================

def make_message(
    symbol,
    result,
    candle_time
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

    if final == "CALL":

        emoji = "🟢"

    elif final == "PUT":

        emoji = "🔴"

    elif "PRE" in final:

        emoji = "🟡"

    else:

        emoji = "⚪"

    return (

        f"{emoji} **TRADEIFY V8.2 OTC**\n\n"

        f"💱 `{symbol}`\n"

        f"🕐 `{candle_time}`\n\n"

        "━━━━━━━━━━━━━━━━━━\n"

        "📊 **15M MASTER**\n"

        f"Direction: **{m['direction']}**\n"

        f"CALL Score: `{m['call']}`\n"

        f"PUT Score: `{m['put']}`\n"

        f"GAP: `{m['gap']:+d}`\n"

        f"RSI: `{m['rsi']:.1f}`\n\n"

        "━━━━━━━━━━━━━━━━━━\n"

        "🎯 **5M ENTRY**\n"

        f"Direction: **{e['direction']}**\n"

        f"CALL Score: `{e['call']}`\n"

        f"PUT Score: `{e['put']}`\n"

        f"GAP: `{e['gap']:+d}`\n"

        f"RSI: `{e['rsi']:.1f}`\n\n"

        "━━━━━━━━━━━━━━━━━━\n"

        f"🚨 **FINAL: {final}**"

    )


# =========================================================
# PROCESS SYMBOL
# =========================================================

def process_symbol(
    symbol
):

    try:

        # =================================================
        # 5M
        # =================================================

        df5 = get_market_data(

            symbol,

            "5m",

            220

        )

        df5 = remove_incomplete_5m(
            df5
        )

        if df5 is None:

            return

        if len(df5) < 100:

            logger.warning(
                "%s | 5M DATA < 100",
                symbol
            )

            return

        # =================================================
        # 15M
        # =================================================

        df15 = build_15m(
            df5
        )

        if len(df15) < 85:

            logger.warning(
                "%s | 15M DATA < 85",
                symbol
            )

            return

        # =================================================
        # Indicators
        # =================================================

        df5 = add_indicators(
            df5
        )

        df15 = add_indicators(
            df15
        )

        # =================================================
        # Candle ID
        # =================================================

        candle_time = (
            df5[
                "datetime"
            ].iloc[-1]
        )

        candle_id = str(
            candle_time
        )

        if (
            LAST_CANDLE.get(
                symbol
            )
            ==
            candle_id
        ):

            return

        LAST_CANDLE[
            symbol
        ] = candle_id

        # =================================================
        # Analyze
        # =================================================

        result = analyze_v82(

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
            "15M=%s S=%d/%d G=%+d | "
            "5M=%s S=%d/%d G=%+d | "
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

        # =================================================
        # SEND SIGNAL ONLY
        # =================================================

        if final not in (
            "CALL",
            "PUT",
            "PRE CALL",
            "PRE PUT"
        ):

            return

        signal_id = (
            symbol,
            candle_id,
            final
        )

        if signal_id in LAST_SIGNAL:

            return

        message = make_message(

            symbol,

            result,

            candle_time

        )

        if send_discord(
            message
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
# PROCESS MARKET
# =========================================================

def process_market():

    logger.info(
        "===== OTC SCAN START ====="
    )

    for symbol in SYMBOLS:

        process_symbol(
            symbol
        )

        # กัน API / Discord burst
        time.sleep(
            0.5
        )

    logger.info(
        "===== OTC SCAN END ====="
    )


# =========================================================
# STARTUP DISCORD
# =========================================================

def startup():

    message = (

        "🟢 **TRADEIFY V8.2 OTC ONLINE**\n\n"

        "ตลาด: **OTC**\n"

        "คู่: **8 PAIRS**\n"

        "15M: **MASTER**\n"

        "5M: **ENTRY**\n"

        "Score/GAP: **ON**\n"

        "Twelve Data: **OFF**\n"

        "Discord 429 Guard: **ON**\n"

        "Railway: **ONLINE**"

    )

    send_discord(
        message
    )


# =========================================================
# SCANNER
# =========================================================

def scanner_loop():

    logger.info(
        "================================"
    )

    logger.info(
        "TRADEIFY V8.2 OTC START"
    )

    logger.info(
        "PAIRS = %d",
        len(SYMBOLS)
    )

    logger.info(
        "Twelve Data = OFF"
    )

    logger.info(
        "================================"
    )

    startup()

    while True:

        start = time.time()

        try:

            process_market()

        except Exception as e:

            logger.exception(
                "PROCESS MARKET ERROR: %s",
                e
            )

        elapsed = (
            time.time()
            -
            start
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
        "TRADEIFY V8.2 OTC"
    )

    logger.info(
        "API: %s",
        "SET"
        if OTC_API_URL
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
        "SCAN: %s",
        SCAN_SECONDS
    )

    logger.info(
        "PAIRS: %s",
        len(SYMBOLS)
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


# =========================================================
# RUN
# =========================================================

if __name__ == "__main__":

    main()
