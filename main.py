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
# TRADEIFY V8 REAL FOREX
# 20 PAIRS
# 15M MASTER + 5M ENTRY
# TWELVE DATA + DISCORD + RAILWAY
# =========================================================


APP_NAME = "TRADEIFY V8 REAL FOREX"


# =========================================================
# RAILWAY VARIABLES
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
        "15"
    )
)


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
    "CADJPY"
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
# CACHE
# =========================================================

MARKET_CACHE = {}

LAST_CLOSED_CANDLE = {}

LAST_SIGNAL = {}

LAST_CHECK = {}

LAST_HEARTBEAT = 0


# =========================================================
# SYMBOL CONVERTER
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
# INTERVAL CONVERTER
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

        "1d": "1day"

    }

    return mapping.get(
        str(
            timeframe
        ).lower().strip(),
        timeframe
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
            "ใน Railway Variables"
        )

    td_symbol = normalize_symbol(
        symbol
    )

    interval = normalize_interval(
        timeframe
    )

    params = {

        "symbol":
            td_symbol,

        "interval":
            interval,

        "outputsize":
            int(limit),

        "timezone":
            "Asia/Bangkok",

        "apikey":
            TWELVE_DATA_API_KEY
    }

    try:

        response = requests.get(
            TWELVE_DATA_URL,
            params=params,
            timeout=20
        )

    except requests.RequestException as e:

        raise RuntimeError(
            f"Twelve Data connection error: {e}"
        )

    if response.status_code != 200:

        raise RuntimeError(
            f"Twelve Data HTTP "
            f"{response.status_code}"
        )

    try:

        data = response.json()

    except Exception:

        raise RuntimeError(
            "Twelve Data JSON ERROR"
        )

    if data.get(
        "status"
    ) == "error":

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

    values = data.get(
        "values"
    )

    if not values:

        raise RuntimeError(
            f"ไม่มีข้อมูล {td_symbol}"
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

    for column in required:

        if column not in df.columns:

            raise RuntimeError(
                f"ขาด column {column}"
            )

    df["datetime"] = pd.to_datetime(
        df["datetime"],
        errors="coerce"
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

    df = df.dropna(
        subset=required
    )

    df = df.sort_values(
        "datetime"
    )

    df = df.reset_index(
        drop=True
    )

    if len(df) < 85:

        raise RuntimeError(
            f"{td_symbol} {interval} "
            f"ข้อมูลไม่พอ: {len(df)}"
        )

    return df[
        required
    ]


# =========================================================
# CLOSED CANDLE
# =========================================================

def get_closed_candle(
    df
):

    if len(df) < 2:

        return None

    return df[
        "datetime"
    ].iloc[-2]


# =========================================================
# FETCH / CACHE
# =========================================================

def refresh_market(
    symbol,
    timeframe
):

    key = (
        symbol,
        timeframe
    )

    old_df = MARKET_CACHE.get(
        key
    )

    df = get_market_data(
        symbol,
        timeframe,
        200
    )

    MARKET_CACHE[key] = (
        df.copy()
    )

    old_candle = None

    if old_df is not None:

        old_candle = (
            get_closed_candle(
                old_df
            )
        )

    new_candle = (
        get_closed_candle(
            df
        )
    )

    changed = (
        old_candle !=
        new_candle
    )

    LAST_CLOSED_CANDLE[key] = (
        new_candle
    )

    return df, changed


# =========================================================
# EMA
# =========================================================

def calculate_ema(
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

def calculate_rsi(
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
            (1 + rs)
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

    df["ema_fast"] = (
        calculate_ema(
            df["close"],
            EMA_FAST_LEN
        )
    )

    df["ema_slow"] = (
        calculate_ema(
            df["close"],
            EMA_SLOW_LEN
        )
    )

    df["ema_trend"] = (
        calculate_ema(
            df["close"],
            EMA_TREND_LEN
        )
    )

    df["rsi"] = (
        calculate_rsi(
            df["close"],
            RSI_PERIOD
        )
    )

    return df


# =========================================================
# V8 SCORE ENGINE
# =========================================================

def calculate_tf_score(
    df
):

    if len(df) < 85:

        raise ValueError(
            "V8 DATA NOT ENOUGH"
        )

    i = -2
    p = -3

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

    body = abs(
        c - o
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
        df["close"].iloc[-2]
    )

    c2 = float(
        df["close"].iloc[-3]
    )

    c3 = float(
        df["close"].iloc[-4]
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
    # SUPPORT RESISTANCE
    # =====================================================

    history = df.iloc[:-1]

    support = float(
        history[
            "low"
        ]
        .tail(
            SR_PERIOD
        )
        .min()
    )

    resistance = float(
        history[
            "high"
        ]
        .tail(
            SR_PERIOD
        )
        .max()
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

    # =====================================================
    # GAP
    # =====================================================

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
        -gap >= MIN_GAP
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
        -gap >= MIN_GAP
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

        "room_call":
            room_call,

        "room_put":
            room_put,

        "rsi":
            rsi_value,

        "price":
            c,

        "support":
            support,

        "resistance":
            resistance
    }


# =========================================================
# MASTER + ENTRY
# =========================================================

def calculate_v8_score(
    df15,
    df5
):

    master = calculate_tf_score(
        df15
    )

    entry = calculate_tf_score(
        df5
    )

    master_call = (
        master["direction"] == "CALL"
    )

    master_put = (
        master["direction"] == "PUT"
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
        -entry["gap"]
        >= MIN_GAP
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

    # =====================================================
    # FINAL
    # =====================================================

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

    elif (
        master_call
        and
        entry["direction"] == "PUT"
    ):

        final = "WAIT"

        reason = (
            "15M CALL / "
            "5M PUT BLOCK"
        )

    elif (
        master_put
        and
        entry["direction"] == "CALL"
    ):

        final = "WAIT"

        reason = (
            "15M PUT / "
            "5M CALL BLOCK"
        )

    return {

        "direction":
            final,

        "reason":
            reason,

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

    if not DISCORD_WEBHOOK_URL:

        logger.error(
            "DISCORD_WEBHOOK_URL NOT SET"
        )

        return False

    try:

        response = requests.post(
            DISCORD_WEBHOOK_URL,
            json={
                "content": message
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

        logger.error(
            "DISCORD ERROR %s | %s",
            response.status_code,
            response.text[:300]
        )

    except Exception as e:

        logger.error(
            "DISCORD ERROR: %s",
            e
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

    final = result[
        "direction"
    ]

    gap15 = master[
        "gap"
    ]

    gap5 = entry[
        "gap"
    ]

    return (
        "🔥 **TRADEIFY V8 REAL FOREX**\n"
        f"💱 `{symbol}`\n"
        f"🕐 `{candle_time}`\n\n"

        "━━━━━━━━━━━━━━━━━━\n"

        "📊 **15M MASTER**\n"
        f"Direction: **{master['direction']}**\n"
        f"CALL Score: `{master['call_score']}`\n"
        f"PUT Score: `{master['put_score']}`\n"
        f"GAP: `{gap15:+d}`\n"
        f"RSI: `{master['rsi']:.2f}`\n\n"

        "━━━━━━━━━━━━━━━━━━\n"

        "🎯 **5M ENTRY**\n"
        f"Direction: **{entry['direction']}**\n"
        f"CALL Score: `{entry['call_score']}`\n"
        f"PUT Score: `{entry['put_score']}`\n"
        f"GAP: `{gap5:+d}`\n"
        f"RSI: `{entry['rsi']:.2f}`\n"
        f"EMA CALL: `{entry['ema_call']}`\n"
        f"EMA PUT: `{entry['ema_put']}`\n"
        f"Rejection CALL: "
        f"`{entry['bull_rejection']}`\n"
        f"Rejection PUT: "
        f"`{entry['bear_rejection']}`\n\n"

        "━━━━━━━━━━━━━━━━━━\n"

        f"🚀 **FINAL: {final}**\n"
        f"📝 `{result['reason']}`\n\n"

        f"Minimum Score: `{MIN_SCORE}`\n"
        f"Minimum GAP: `{MIN_GAP}`"
    )


# =========================================================
# STARTUP
# =========================================================

def send_startup():

    message = (
        "🟢 **TRADEIFY V8 ONLINE**\n\n"
        "ตลาด: **REAL FOREX**\n"
        "Pairs: **20**\n"
        "15M: MASTER\n"
        "5M: ENTRY\n"
        "Score/GAP: ON\n"
        "Twelve Data: ON\n"
        "Discord: ON\n"
        "Railway: ONLINE"
    )

    send_discord(
        message
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

    message = (
        "💓 **TRADEIFY V8 HEARTBEAT**\n"
        f"เวลา `{thai_now().strftime('%H:%M:%S')}`\n"
        "20 REAL FOREX PAIRS\n"
        "15M MASTER + 5M ENTRY\n"
        "ระบบกำลังทำงาน"
    )

    if send_discord(
        message
    ):

        LAST_HEARTBEAT = now


# =========================================================
# PROCESS SYMBOL
# =========================================================

def process_symbol(
    symbol
):

    try:

        now = thai_now()

        # =================================================
        # 5M REFRESH
        # =================================================

        df5, new5 = refresh_market(
            symbol,
            "5m"
        )

        # =================================================
        # 15M REFRESH
        #
        # refresh ทุก 15 นาที
        # =================================================

        df15_cache = MARKET_CACHE.get(
            (
                symbol,
                "15m"
            )
        )

        new15 = False

        if df15_cache is None:

            df15, new15 = refresh_market(
                symbol,
                "15m"
            )

        else:

            if (
                now.minute % 15 == 0
                and
                now.second >= 5
            ):

                df15, new15 = refresh_market(
                    symbol,
                    "15m"
                )

            else:

                df15 = df15_cache

        # =================================================
        # ONLY NEW 5M CANDLE
        # =================================================

        if not new5:

            return

        logger.info(
            "NEW 5M CANDLE | %s",
            symbol
        )

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
        # V8
        # =================================================

        result = calculate_v8_score(
            df15,
            df5
        )

        candle_time = (
            get_closed_candle(
                df5
            )
        )

        # =================================================
        # LOG
        # =================================================

        logger.info(
            "V8 | %s | "
            "15M=%s "
            "CALL=%d "
            "PUT=%d "
            "GAP=%d | "
            "5M=%s "
            "CALL=%d "
            "PUT=%d "
            "GAP=%d | "
            "FINAL=%s",

            symbol,

            result[
                "master"
            ][
                "direction"
            ],

            result[
                "master"
            ][
                "call_score"
            ],

            result[
                "master"
            ][
                "put_score"
            ],

            result[
                "master"
            ][
                "gap"
            ],

            result[
                "entry"
            ][
                "direction"
            ],

            result[
                "entry"
            ][
                "call_score"
            ],

            result[
                "entry"
            ][
                "put_score"
            ],

            result[
                "entry"
            ][
                "gap"
            ],

            result[
                "direction"
            ]
        )

        # =================================================
        # DISCORD
        #
        # ส่งเฉพาะ PRE/CALL/PUT
        # =================================================

        final = result[
            "direction"
        ]

        if final in (
            "PRE CALL",
            "PRE PUT",
            "CALL",
            "PUT"
        ):

            signal_key = (
                symbol,
                str(
                    candle_time
                ),
                final
            )

            if signal_key not in LAST_SIGNAL:

                message = format_signal(
                    symbol,
                    result,
                    candle_time
                )

                if send_discord(
                    message
                ):

                    LAST_SIGNAL[
                        signal_key
                    ] = time.time()

        # =================================================
        # CHECK LOG
        # =================================================

        LAST_CHECK[
            symbol
        ] = time.time()

    except Exception as e:

        logger.exception(
            "SYMBOL ERROR | %s",
            symbol
        )

        send_discord(
            "⚠️ **TRADEIFY ERROR**\n"
            f"Pair: `{symbol}`\n"
            f"`{str(e)[:500]}`"
        )


# =========================================================
# SCANNER
# =========================================================

def scanner_loop():

    logger.info(
        "========================================"
    )

    logger.info(
        "TRADEIFY V8 REAL FOREX START"
    )

    logger.info(
        "20 PAIRS"
    )

    logger.info(
        "15M MASTER + 5M ENTRY"
    )

    logger.info(
        "========================================"
    )

    send_startup()

    while True:

        start = time.time()

        try:

            for symbol in SYMBOLS:

                process_symbol(
                    symbol
                )

                # เว้นเล็กน้อย
                # ลดการยิง API รวดเดียว
                time.sleep(
                    0.5
                )

            heartbeat()

        except Exception as e:

            logger.exception(
                "SCANNER ERROR: %s",
                e
            )

        elapsed = (
            time.time()
            -
            start
        )

        sleep_time = max(
            1,
            SCAN_SECONDS -
            elapsed
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
            "TRADEIFY V8 ONLINE"
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
        "RAILWAY PORT %s",
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
        "TRADEIFY V8 START"
    )

    logger.info(
        "MARKET: REAL FOREX"
    )

    logger.info(
        "PAIRS: %d",
        len(SYMBOLS)
    )

    logger.info(
        "TWELVE DATA API: %s",
        "SET"
        if TWELVE_DATA_API_KEY
        else "NOT SET"
    )

    logger.info(
        "DISCORD: %s",
        "SET"
        if DISCORD_WEBHOOK_URL
        else "NOT SET"
    )

    logger.info(
        "PORT: %d",
        PORT
    )

    logger.info(
        "SCAN: %d seconds",
        SCAN_SECONDS
    )

    logger.info(
        "========================================"
    )

    if not TWELVE_DATA_API_KEY:

        logger.error(
            "TWELVE_DATA_API_KEY NOT SET"
        )

    if not DISCORD_WEBHOOK_URL:

        logger.error(
            "DISCORD_WEBHOOK_URL NOT SET"
        )

    # =====================================================
    # HEALTH
    # =====================================================

    thread = threading.Thread(
        target=health_server,
        daemon=True
    )

    thread.start()

    # =====================================================
    # SCANNER
    # =====================================================

    scanner_loop()


# =========================================================
# RUN
# =========================================================

if __name__ == "__main__":

    main()
