# -*- coding: utf-8 -*-

import os
import time
import logging
import threading
from datetime import datetime, timezone, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer

import numpy as np
import pandas as pd
import requests


# =========================================================
# TRADEIFY V8.1 REAL FOREX
# 20 PAIRS
# 5M DATA -> BUILD 15M
# DISCORD RATE LIMIT PROTECTION
# RAILWAY READY
# =========================================================


APP_NAME = "TRADEIFY V8.1"


# =========================================================
# ENV
# =========================================================

TWELVE_DATA_API_KEY = os.getenv(
    "TWELVE_DATA_API_KEY",
    ""
).strip()

DISCORD_WEBHOOK_URL = os.getenv(
    "DISCORD_WEBHOOK_URL",
    ""
).strip()

PORT = int(
    os.getenv(
        "PORT",
        "8080"
    )
)

SCAN_SECONDS = int(
    os.getenv(
        "SCAN_SECONDS",
        "30"
    )
)

# ส่งเฉพาะสัญญาณใหม่
SEND_PRE = os.getenv(
    "SEND_PRE",
    "true"
).lower() == "true"


# =========================================================
# 20 REAL FOREX PAIRS
# =========================================================

SYMBOLS = [
    "EURUSD",
    "GBPUSD",
    "USDJPY",
    "USDCHF",
    "AUDUSD",
    "USDCAD",
    "NZDUSD",
    "EURGBP",
    "EURJPY",
    "EURCHF",
    "EURAUD",
    "EURCAD",
    "EURNZD",
    "GBPJPY",
    "GBPCHF",
    "GBPAUD",
    "AUDJPY",
    "AUDCAD",
    "AUDNZD",
    "CADJPY",
]


# =========================================================
# V8 SETTINGS
# =========================================================

MIN_SCORE = 68
MIN_GAP = 8

PRE_SCORE = MIN_SCORE - 10

EMA_FAST_LEN = 9
EMA_SLOW_LEN = 21
EMA_TREND_LEN = 50

RSI_PERIOD = 14
RSI_MID = 50

SR_PERIOD = 80

STRICT_MODE = False


# =========================================================
# TWELVE DATA
# =========================================================

TWELVE_DATA_URL = (
    "https://api.twelvedata.com/time_series"
)


# =========================================================
# TIME
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
# MEMORY
# =========================================================

MARKET_CACHE = {}

LAST_SIGNAL = {}

LAST_5M_CANDLE = None

LAST_API_CALL = 0

LAST_HEARTBEAT = 0

DISCORD_LOCK = threading.Lock()

API_LOCK = threading.Lock()


# =========================================================
# SYMBOL
# =========================================================

def normalize_symbol(
    symbol
):

    symbol = str(
        symbol
    ).strip().upper()

    if symbol.endswith(
        "_OTC"
    ):

        symbol = symbol[:-4]

    if (
        "/" not in symbol
        and
        len(symbol) == 6
    ):

        return (
            symbol[:3]
            + "/"
            + symbol[3:]
        )

    return symbol


# =========================================================
# INTERVAL
# =========================================================

def normalize_interval(
    timeframe
):

    mapping = {

        "1m": "1min",

        "5m": "5min",

        "15m": "15min",

        "30m": "30min",

        "1h": "1h",

        "4h": "4h",

        "1d": "1day",

    }

    return mapping.get(
        str(
            timeframe
        ).lower(),
        timeframe
    )


# =========================================================
# API RETRY
# =========================================================

def api_get(
    params,
    retries=4
):

    global LAST_API_CALL

    with API_LOCK:

        # กันยิงติดกันเกินไป
        now = time.time()

        wait = (
            2.0 -
            (
                now -
                LAST_API_CALL
            )
        )

        if wait > 0:

            time.sleep(
                wait
            )

        for attempt in range(
            retries
        ):

            try:

                response = requests.get(
                    TWELVE_DATA_URL,
                    params=params,
                    timeout=30
                )

                LAST_API_CALL = (
                    time.time()
                )

                if response.status_code == 429:

                    retry_after = (
                        5 *
                        (
                            attempt + 1
                        )
                    )

                    logger.warning(
                        "Twelve Data 429 "
                        "retry in %ss",
                        retry_after
                    )

                    time.sleep(
                        retry_after
                    )

                    continue

                if response.status_code != 200:

                    raise RuntimeError(
                        "HTTP "
                        +
                        str(
                            response.status_code
                        )
                        +
                        " "
                        +
                        response.text[
                            :300
                        ]
                    )

                return response.json()

            except requests.RequestException as e:

                logger.warning(
                    "API connection error: %s",
                    e
                )

                time.sleep(
                    2 *
                    (
                        attempt + 1
                    )
                )

        raise RuntimeError(
            "Twelve Data request failed"
        )


# =========================================================
# FETCH BATCH 5M
# =========================================================

def get_market_data_batch():

    if not TWELVE_DATA_API_KEY:

        raise RuntimeError(
            "ไม่พบ TWELVE_DATA_API_KEY "
            "กรุณาเพิ่มใน Railway Variables"
        )

    symbols = ",".join(
        normalize_symbol(
            s
        )
        for s in SYMBOLS
    )

    params = {

        "symbol":
            symbols,

        "interval":
            "5min",

        "outputsize":
            220,

        "timezone":
            "Asia/Bangkok",

        "apikey":
            TWELVE_DATA_API_KEY,
    }

    data = api_get(
        params
    )

    if (
        isinstance(
            data,
            dict
        )
        and
        data.get(
            "status"
        ) == "error"
    ):

        raise RuntimeError(
            "Twelve Data ERROR: "
            +
            str(
                data.get(
                    "message",
                    "Unknown error"
                )
            )
        )

    result = {}

    # =====================================================
    # Batch response
    # =====================================================

    if all(
        isinstance(
            v,
            dict
        )
        for v in data.values()
    ):

        for symbol_key, payload in data.items():

            if not isinstance(
                payload,
                dict
            ):

                continue

            values = payload.get(
                "values"
            )

            if not values:

                continue

            df = build_dataframe(
                values
            )

            if df is not None:

                clean_name = (
                    symbol_key
                    .replace(
                        "/",
                        ""
                    )
                    .upper()
                )

                result[
                    clean_name
                ] = df

    # =====================================================
    # Single response fallback
    # =====================================================

    elif data.get(
        "values"
    ):

        df = build_dataframe(
            data[
                "values"
            ]
        )

        if df is not None:

            for symbol in SYMBOLS:

                result[
                    symbol
                ] = df

    if not result:

        raise RuntimeError(
            "Twelve Data ไม่ส่งข้อมูล "
            "20 คู่กลับมา"
        )

    return result


# =========================================================
# DATAFRAME
# =========================================================

def build_dataframe(
    values
):

    required = [
        "datetime",
        "open",
        "high",
        "low",
        "close",
    ]

    df = pd.DataFrame(
        values
    )

    for col in required:

        if col not in df.columns:

            return None

    df["datetime"] = pd.to_datetime(
        df["datetime"],
        errors="coerce"
    )

    for col in [
        "open",
        "high",
        "low",
        "close",
    ]:

        df[col] = pd.to_numeric(
            df[col],
            errors="coerce"
        )

    df = df.dropna(
        subset=required
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
        required
    ]


# =========================================================
# BUILD 15M FROM 5M
# =========================================================

def build_15m_from_5m(
    df5
):

    df = df5.copy()

    df["datetime"] = pd.to_datetime(
        df["datetime"]
    )

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
            "last",

    })

    df15 = df15.dropna()

    df15 = df15.reset_index()

    return df15


# =========================================================
# REMOVE INCOMPLETE CANDLE
# =========================================================

def remove_incomplete_candle(
    df,
    minutes
):

    if df.empty:

        return df

    now = thai_now()

    current_bucket = (
        now.replace(
            minute=(
                now.minute //
                minutes
            ) *
            minutes,
            second=0,
            microsecond=0
        )
    )

    df = df.copy()

    df["datetime"] = pd.to_datetime(
        df["datetime"]
    )

    # datetime จาก Twelve Data
    # ไม่มี timezone -> ตีความเป็น Bangkok
    if df["datetime"].dt.tz is None:

        df["datetime"] = (
            df["datetime"]
            .dt.tz_localize(
                THAI_TZ
            )
        )

    else:

        df["datetime"] = (
            df["datetime"]
            .dt.tz_convert(
                THAI_TZ
            )
        )

    return df[
        df["datetime"] <
        current_bucket
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

    value = (
        100 -
        (
            100 /
            (
                1 +
                rs
            )
        )
    )

    return value.fillna(
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
        EMA_FAST_LEN
    )

    df["ema_slow"] = ema(
        df["close"],
        EMA_SLOW_LEN
    )

    df["ema_trend"] = ema(
        df["close"],
        EMA_TREND_LEN
    )

    df["rsi"] = rsi(
        df["close"],
        RSI_PERIOD
    )

    return df


# =========================================================
# SCORE
# =========================================================

def calculate_score(
    df
):

    if len(df) < 85:

        raise ValueError(
            "ข้อมูลไม่พอสำหรับ V8"
        )

    # ปิดแล้ว
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

    # =====================================================
    # STRUCTURE
    # =====================================================

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

    # =====================================================
    # EMA
    # =====================================================

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

    # =====================================================
    # RSI
    # =====================================================

    rsi_value = float(
        df["rsi"].iloc[i]
    )

    # =====================================================
    # CANDLE
    # =====================================================

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

    # =====================================================
    # FLOW
    # =====================================================

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

    # =====================================================
    # SR
    # =====================================================

    history = df.iloc[
        :-1
    ]

    recent = history.tail(
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

    # =====================================================
    # PULLBACK
    # =====================================================

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

    # =====================================================
    # SCORE
    # =====================================================

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

    # =====================================================
    # PENALTY
    # =====================================================

    if near_resistance:
        call_score -= 8

    if near_support:
        put_score -= 8

    # =====================================================
    # LIMIT
    # =====================================================

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

    # =====================================================
    # DIRECTION
    # =====================================================

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

        "call_score":
            call_score,

        "put_score":
            put_score,

        "gap":
            gap,

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

        "near_support":
            near_support,

        "near_resistance":
            near_resistance,

        "rsi":
            rsi_value,

        "price":
            c,

        "support":
            support,

        "resistance":
            resistance,
    }


# =========================================================
# V8 MASTER + ENTRY
# =========================================================

def analyze_v8(
    df15,
    df5
):

    master = calculate_score(
        df15
    )

    entry = calculate_score(
        df5
    )

    master_call = (
        master["direction"]
        == "CALL"
    )

    master_put = (
        master["direction"]
        == "PUT"
    )

    entry_call = (
        entry["call_score"]
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
        entry["put_score"]
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

    reason = (
        "ยังไม่ครบเงื่อนไข"
    )

    if (
        master_call
        and
        entry_call
    ):

        final = "CALL"

        reason = (
            "15M MASTER CALL + "
            "5M ENTRY CALL"
        )

    elif (
        master_put
        and
        entry_put
    ):

        final = "PUT"

        reason = (
            "15M MASTER PUT + "
            "5M ENTRY PUT"
        )

    elif (
        master_call
        and
        entry["direction"]
        in (
            "PRE CALL",
            "CALL"
        )
    ):

        final = "PRE CALL"

        reason = (
            "15M CALL + "
            "5M กำลังยืนยัน"
        )

    elif (
        master_put
        and
        entry["direction"]
        in (
            "PRE PUT",
            "PUT"
        )
    ):

        final = "PRE PUT"

        reason = (
            "15M PUT + "
            "5M กำลังยืนยัน"
        )

    return {

        "direction":
            final,

        "reason":
            reason,

        "master":
            master,

        "entry":
            entry,
    }


# =========================================================
# DISCORD SAFE SEND
# =========================================================

def send_discord(
    message
):

    if not DISCORD_WEBHOOK_URL:

        logger.warning(
            "Discord webhook not set"
        )

        return False

    with DISCORD_LOCK:

        for attempt in range(
            4
        ):

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

                    logger.info(
                        "DISCORD SENT"
                    )

                    return True

                if response.status_code == 429:

                    try:

                        payload = (
                            response.json()
                        )

                        retry_after = float(
                            payload.get(
                                "retry_after",
                                2
                            )
                        )

                    except Exception:

                        retry_after = 2

                    retry_after = min(
                        max(
                            retry_after,
                            1
                        ),
                        30
                    )

                    logger.warning(
                        "DISCORD 429 | "
                        "sleep %.2fs",
                        retry_after
                    )

                    time.sleep(
                        retry_after
                    )

                    continue

                logger.error(
                    "DISCORD ERROR %s | %s",
                    response.status_code,
                    response.text[:300]
                )

                return False

            except requests.RequestException as e:

                logger.error(
                    "DISCORD CONNECTION ERROR: %s",
                    e
                )

                time.sleep(
                    2
                )

        return False


# =========================================================
# DISCORD FORMAT
# =========================================================

def format_signal(
    symbol,
    result,
    candle_time
):

    master = result[
        "master"
    ]

    entry = result[
        "entry"
    ]

    return (
        "🔥 **TRADEIFY V8.1 REAL FOREX**\n"
        f"💱 `{symbol}`\n"
        f"🕐 `{candle_time}`\n\n"

        "━━━━━━━━━━━━━━━━━━\n"

        "📊 **15M MASTER**\n"
        f"Direction: **"
        f"{master['direction']}**\n"
        f"CALL Score: `{master['call_score']}`\n"
        f"PUT Score: `{master['put_score']}`\n"
        f"GAP: `{master['gap']:+d}`\n"
        f"RSI: `{master['rsi']:.2f}`\n\n"

        "━━━━━━━━━━━━━━━━━━\n"

        "🎯 **5M ENTRY**\n"
        f"Direction: **"
        f"{entry['direction']}**\n"
        f"CALL Score: `{entry['call_score']}`\n"
        f"PUT Score: `{entry['put_score']}`\n"
        f"GAP: `{entry['gap']:+d}`\n"
        f"RSI: `{entry['rsi']:.2f}`\n\n"

        "━━━━━━━━━━━━━━━━━━\n"

        f"🚀 **FINAL: "
        f"{result['direction']}**\n"
        f"📝 `{result['reason']}`"
    )


# =========================================================
# STARTUP
# =========================================================

def startup_message():

    return (
        "🟢 **TRADEIFY V8.1 ONLINE**\n\n"
        "ตลาด: **REAL FOREX**\n"
        "Pairs: **20**\n"
        "Data: **Twelve Data Batch**\n"
        "5M: **ENTRY**\n"
        "15M: **BUILT FROM 5M**\n"
        "Score/GAP: **ON**\n"
        "Discord Rate Limit Guard: **ON**\n"
        "Railway: **ONLINE**"
    )


# =========================================================
# HEARTBEAT
# =========================================================

def heartbeat():

    global LAST_HEARTBEAT

    now = time.time()

    if (
        now -
        LAST_HEARTBEAT
        < 900
    ):

        return

    if send_discord(
        "💓 **TRADEIFY V8.1 HEARTBEAT**\n"
        f"เวลา `{thai_now().strftime('%Y-%m-%d %H:%M:%S')}`\n"
        "20 REAL FOREX PAIRS\n"
        "5M ENTRY + 15M MASTER\n"
        "ระบบกำลังทำงาน"
    ):

        LAST_HEARTBEAT = now


# =========================================================
# PROCESS ALL
# =========================================================

def process_market():

    global LAST_5M_CANDLE

    # =====================================================
    # API BATCH
    # =====================================================

    batch = get_market_data_batch()

    if not batch:

        raise RuntimeError(
            "Batch data empty"
        )

    # =====================================================
    # หาแท่ง 5M ล่าสุดร่วม
    # =====================================================

    newest_times = []

    for symbol in SYMBOLS:

        df = batch.get(
            symbol
        )

        if df is None:

            continue

        df = remove_incomplete_candle(
            df,
            5
        )

        if df.empty:

            continue

        newest_times.append(
            df[
                "datetime"
            ].iloc[-1]
        )

    if not newest_times:

        return

    current_candle = max(
        newest_times
    )

    # =====================================================
    # ไม่ใช่แท่งใหม่
    # =====================================================

    if (
        LAST_5M_CANDLE is not None
        and
        current_candle ==
        LAST_5M_CANDLE
    ):

        logger.info(
            "NO NEW 5M CANDLE | "
            "API DATA CACHED"
        )

        return

    LAST_5M_CANDLE = (
        current_candle
    )

    logger.info(
        "NEW 5M CANDLE | %s",
        current_candle
    )

    # =====================================================
    # PROCESS 20 PAIRS
    # =====================================================

    signals = []

    for symbol in SYMBOLS:

        try:

            df5 = batch.get(
                symbol
            )

            if df5 is None:

                logger.warning(
                    "%s | NO DATA",
                    symbol
                )

                continue

            df5 = remove_incomplete_candle(
                df5,
                5
            )

            if len(df5) < 100:

                logger.warning(
                    "%s | DATA < 100",
                    symbol
                )

                continue

            # =================================================
            # 15M
            # =================================================

            df15 = build_15m_from_5m(
                df5
            )

            df15 = remove_incomplete_candle(
                df15,
                15
            )

            if len(df15) < 85:

                logger.warning(
                    "%s | 15M DATA < 85",
                    symbol
                )

                continue

            # =================================================
            # INDICATORS
            # =================================================

            df5 = add_indicators(
                df5
            )

            df15 = add_indicators(
                df15
            )

            # =================================================
            # ANALYZE
            # =================================================

            result = analyze_v8(
                df15,
                df5
            )

            master = result[
                "master"
            ]

            entry = result[
                "entry"
            ]

            logger.info(
                (
                    "V8.1 | %s | "
                    "15M=%s "
                    "S=%d/%d "
                    "G=%+d | "
                    "5M=%s "
                    "S=%d/%d "
                    "G=%+d | "
                    "FINAL=%s"
                ),
                symbol,

                master[
                    "direction"
                ],

                master[
                    "call_score"
                ],

                master[
                    "put_score"
                ],

                master[
                    "gap"
                ],

                entry[
                    "direction"
                ],

                entry[
                    "call_score"
                ],

                entry[
                    "put_score"
                ],

                entry[
                    "gap"
                ],

                result[
                    "direction"
                ]
            )

            final = result[
                "direction"
            ]

            if final in (
                "CALL",
                "PUT"
            ) or (
                SEND_PRE
                and
                final in (
                    "PRE CALL",
                    "PRE PUT"
                )
            ):

                candle_time = (
                    df5[
                        "datetime"
                    ].iloc[-1]
                )

                signal_id = (
                    symbol,
                    str(
                        candle_time
                    ),
                    final
                )

                if signal_id not in LAST_SIGNAL:

                    signals.append(
                        (
                            symbol,
                            result,
                            candle_time,
                            signal_id
                        )
                    )

        except Exception as e:

            logger.exception(
                "%s ANALYZE ERROR: %s",
                symbol,
                e
            )

    # =====================================================
    # DISCORD
    # =====================================================

    for (
        symbol,
        result,
        candle_time,
        signal_id
    ) in signals:

        message = format_signal(
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

            # กัน Discord 429
            time.sleep(
                1.2
            )


# =========================================================
# SCANNER
# =========================================================

def scanner_loop():

    logger.info(
        "========================================"
    )

    logger.info(
        "TRADEIFY V8.1 START"
    )

    logger.info(
        "MARKET: REAL FOREX"
    )

    logger.info(
        "PAIRS: %d",
        len(SYMBOLS)
    )

    logger.info(
        "5M DATA -> 15M AGGREGATION"
    )

    logger.info(
        "DISCORD RATE LIMIT GUARD: ON"
    )

    logger.info(
        "========================================"
    )

    send_discord(
        startup_message()
    )

    while True:

        started = time.time()

        try:

            process_market()

            heartbeat()

        except Exception as e:

            logger.exception(
                "MARKET DATA ERROR: %s",
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

        time.sleep(
            sleep_time
        )


# =========================================================
# RAILWAY HEALTH
# =========================================================

class HealthHandler(
    BaseHTTPRequestHandler
):

    def do_GET(
        self
    ):

        body = (
            "TRADEIFY V8.1 ONLINE"
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
        "RAILWAY HEALTH PORT: %s",
        PORT
    )

    server.serve_forever()


# =========================================================
# MAIN
# =========================================================

def main():

    logger.info(
        "========================================"
    )

    logger.info(
        "TRADEIFY V8.1 START"
    )

    logger.info(
        "API KEY: %s",
        "SET"
        if TWELVE_DATA_API_KEY
        else "NOT SET"
    )

    logger.info(
        "Discord: %s",
        "SET"
        if DISCORD_WEBHOOK_URL
        else "NOT SET"
    )

    logger.info(
        "Port: %s",
        PORT
    )

    logger.info(
        "Scan: %s seconds",
        SCAN_SECONDS
    )

    logger.info(
        "Pairs: %s",
        len(SYMBOLS)
    )

    logger.info(
        "========================================"
    )

    if not TWELVE_DATA_API_KEY:

        logger.error(
            "MARKET DATA DISABLED: "
            "TWELVE_DATA_API_KEY NOT SET"
        )

    if not DISCORD_WEBHOOK_URL:

        logger.warning(
            "DISCORD_WEBHOOK_URL NOT SET"
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
