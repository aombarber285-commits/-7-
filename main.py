# -*- coding: utf-8 -*-

import os
import time
import json
import logging
import threading

from datetime import datetime, timezone, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer

import requests
import pandas as pd
import numpy as np


# =========================================================
# TRADEIFY V8.5
# 8X TRADE / 5M DATA
#
# 5M = DATA SOURCE + ENTRY
# 15M = CREATED FROM 3 x 5M CANDLES
# DISCORD = SIGNAL
# =========================================================


APP_NAME = "TRADEIFY V8.5 8X"

PORT = int(
    os.getenv("PORT", "8080")
)

SCAN_SECONDS = int(
    os.getenv("SCAN_SECONDS", "20")
)

DISCORD_WEBHOOK_URL = os.getenv(
    "DISCORD_WEBHOOK_URL",
    ""
).strip()


# =========================================================
# 8 PAIRS
# =========================================================

SYMBOLS = [
    "EURUSD",
    "GBPUSD",
    "USDJPY",
    "AUDUSD",
    "EURJPY",
    "GBPJPY",
    "EURGBP",
    "AUDJPY",
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
# STORAGE
# =========================================================

MARKET_DATA = {}

DATA_LOCK = threading.Lock()

LAST_SIGNAL = set()

LAST_CANDLE = {}

LAST_DISCORD = 0

DISCORD_LOCK = threading.Lock()


# =========================================================
# LOG
# =========================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger(APP_NAME)


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
# NORMALIZE CANDLE
# =========================================================

def normalize_candles(candles):

    rows = []

    for x in candles:

        try:

            ts = x.get(
                "timestamp",
                x.get("time")
            )

            if ts is None:
                continue

            ts = float(ts)

            if ts > 10_000_000_000:
                ts /= 1000


            rows.append({

                "datetime":
                    datetime.fromtimestamp(
                        ts,
                        timezone.utc
                    ).astimezone(
                        THAI_TZ
                    ),

                "open":
                    float(x["open"]),

                "high":
                    float(x["high"]),

                "low":
                    float(x["low"]),

                "close":
                    float(x["close"])

            })

        except Exception:

            continue


    if not rows:

        return pd.DataFrame()


    df = pd.DataFrame(rows)

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
# 5M -> 15M
#
# 3 CLOSED 5M CANDLES
# = 1 CLOSED 15M CANDLE
# =========================================================

def make_15m_from_5m(df5):

    if df5 is None:
        return pd.DataFrame()


    if len(df5) < 3:
        return pd.DataFrame()


    df = df5.copy()

    df["datetime"] = pd.to_datetime(
        df["datetime"]
    )


    df = df.sort_values(
        "datetime"
    )


    df = df.set_index(
        "datetime"
    )


    df15 = df.resample(
        "15min",
        origin="epoch",
        label="left",
        closed="left"
    ).agg({

        "open": "first",

        "high": "max",

        "low": "min",

        "close": "last"

    })


    df15 = df15.dropna()

    df15 = df15.reset_index()


    return df15


# =========================================================
# BRIDGE
# =========================================================

class BridgeHandler(
    BaseHTTPRequestHandler
):


    def reply(
        self,
        status,
        data
    ):

        body = json.dumps(
            data,
            ensure_ascii=False
        ).encode("utf-8")


        self.send_response(
            status
        )

        self.send_header(
            "Content-Type",
            "application/json"
        )

        self.send_header(
            "Content-Length",
            str(len(body))
        )

        self.end_headers()

        self.wfile.write(
            body
        )


    def do_GET(self):

        if self.path == "/":

            self.reply(
                200,
                {
                    "status": "online",
                    "service": APP_NAME,
                    "mode": "8X_5M",
                    "pairs": len(SYMBOLS)
                }
            )

            return


        if self.path == "/health":

            self.reply(
                200,
                {
                    "status": "ok"
                }
            )

            return


        if self.path == "/market":

            with DATA_LOCK:

                result = {
                    k: v
                    for k, v
                    in MARKET_DATA.items()
                }


            self.reply(
                200,
                result
            )

            return


        self.reply(
            404,
            {
                "error": "not found"
            }
        )


    def do_POST(self):

        if self.path != "/market":

            self.reply(
                404,
                {
                    "error": "not found"
                }
            )

            return


        try:

            length = int(
                self.headers.get(
                    "Content-Length",
                    "0"
                )
            )


            raw = self.rfile.read(
                length
            )


            payload = json.loads(
                raw.decode("utf-8")
            )


            symbol = str(
                payload.get(
                    "symbol",
                    ""
                )
            ).upper()


            candles = payload.get(
                "candles",
                []
            )


            if symbol not in SYMBOLS:

                self.reply(
                    400,
                    {
                        "error":
                            "invalid symbol"
                    }
                )

                return


            df5 = normalize_candles(
                candles
            )


            if len(df5) < 100:

                self.reply(
                    400,
                    {
                        "error":
                            "need at least 100 candles"
                    }
                )

                return


            with DATA_LOCK:

                MARKET_DATA[
                    symbol + "_5m"
                ] = df5.to_dict(
                    orient="records"
                )


            logger.info(
                "8X 5M RECEIVED | %s | %d candles",
                symbol,
                len(df5)
            )


            self.reply(
                200,
                {
                    "ok": True,
                    "symbol": symbol,
                    "timeframe": "5m",
                    "candles": len(df5)
                }
            )


        except Exception as e:

            logger.exception(
                "BRIDGE ERROR"
            )

            self.reply(
                500,
                {
                    "error": str(e)
                }
            )


# =========================================================
# SERVER
# =========================================================

def start_server():

    server = HTTPServer(
        ("0.0.0.0", PORT),
        BridgeHandler
    )

    logger.info(
        "8X BRIDGE LISTENING :%s",
        PORT
    )

    server.serve_forever()


# =========================================================
# GET 5M
# =========================================================

def get_5m(
    symbol
):

    key = (
        symbol +
        "_5m"
    )


    with DATA_LOCK:

        raw = MARKET_DATA.get(
            key
        )


    if not raw:

        raise RuntimeError(
            f"ยังไม่มีข้อมูล 8X: "
            f"{symbol} 5m"
        )


    df = pd.DataFrame(
        raw
    )


    df["datetime"] = pd.to_datetime(
        df["datetime"]
    )


    df = df.sort_values(
        "datetime"
    )


    return df.reset_index(
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
    period
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

def indicators(df):

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
# TF ANALYSIS
# =========================================================

def analyze_tf(df):

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


    upper = (
        h -
        max(o, c)
    )


    lower = (
        min(o, c) -
        l
    )


    upper_ratio = (
        upper /
        candle_range
    )


    lower_ratio = (
        lower /
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


    flow_up = (
        df["close"].iloc[-1]
        >=
        df["close"].iloc[-2]
        >=
        df["close"].iloc[-3]
    )


    flow_down = (
        df["close"].iloc[-1]
        <=
        df["close"].iloc[-2]
        <=
        df["close"].iloc[-3]
    )


    recent = df.iloc[:-1].tail(
        SR_PERIOD
    )


    support = float(
        recent["low"].min()
    )


    resistance = float(
        recent["high"].max()
    )


    sr_range = max(
        resistance - support,
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

        "direction": direction,

        "call": call_score,

        "put": put_score,

        "gap": gap,

        "rsi": rv,

        "ema_call": ema_call,

        "ema_put": ema_put,

        "flow_up": flow_up,

        "flow_down": flow_down,

        "bull_rejection":
            bull_rejection,

        "bear_rejection":
            bear_rejection,

        "pullback_call":
            pullback_call,

        "pullback_put":
            pullback_put,

        "price": c

    }


# =========================================================
# V8 MASTER
# =========================================================

def analyze_v85(
    df5
):

    df5 = indicators(
        df5
    )


    # -----------------------------------------------------
    # IMPORTANT:
    # ใช้เฉพาะแท่ง 5M ที่ปิดแล้ว
    # -----------------------------------------------------

    df5_closed = df5.iloc[:-1].copy()


    if len(df5_closed) < 100:

        return None


    # -----------------------------------------------------
    # CREATE 15M
    # -----------------------------------------------------

    df15 = make_15m_from_5m(
        df5_closed
    )


    if len(df15) < 85:

        return None


    df15 = indicators(
        df15
    )


    master = analyze_tf(
        df15
    )


    entry = analyze_tf(
        df5_closed
    )


    if (
        master is None
        or
        entry is None
    ):

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

        "final": final,

        "master": master,

        "entry": entry,

        "candle":
            str(
                df5_closed[
                    "datetime"
                ].iloc[-1]
            )

    }


# =========================================================
# DISCORD
# =========================================================

def send_discord(
    message
):

    global LAST_DISCORD


    if not DISCORD_WEBHOOK_URL:

        logger.error(
            "DISCORD_WEBHOOK_URL NOT SET"
        )

        return False


    with DISCORD_LOCK:

        wait = (
            1.5 -
            (
                time.time()
                -
                LAST_DISCORD
            )
        )


        if wait > 0:

            time.sleep(
                wait
            )


        for _ in range(5):

            try:

                r = requests.post(

                    DISCORD_WEBHOOK_URL,

                    json={
                        "content":
                            message
                    },

                    timeout=15

                )


                if r.status_code in (
                    200,
                    204
                ):

                    LAST_DISCORD = (
                        time.time()
                    )

                    return True


                if r.status_code == 429:

                    try:

                        retry = float(
                            r.json().get(
                                "retry_after",
                                3
                            )
                        )

                    except Exception:

                        retry = 3


                    time.sleep(
                        min(
                            max(
                                retry,
                                2
                            ),
                            30
                        )
                    )

                    continue


                logger.error(
                    "DISCORD ERROR %s | %s",
                    r.status_code,
                    r.text[:200]
                )

                return False


            except Exception as e:

                logger.error(
                    "DISCORD ERROR: %s",
                    e
                )

                time.sleep(2)


    return False


# =========================================================
# MESSAGE
# =========================================================

def make_message(
    symbol,
    result
):

    m = result["master"]

    e = result["entry"]

    final = result["final"]


    icon = {

        "CALL": "🟢",

        "PUT": "🔴",

        "PRE CALL": "🟡",

        "PRE PUT": "🟡"

    }.get(
        final,
        "⚪"
    )


    return (

        f"{icon} **TRADEIFY V8.5**\n\n"

        f"PAIR: `{symbol}`\n"

        f"SIGNAL: **{final}**\n\n"

        "━━━━━━━━━━━━━━\n"

        "15M MASTER\n"

        f"Direction: {m['direction']}\n"

        f"CALL Score: {m['call']}\n"

        f"PUT Score: {m['put']}\n"

        f"GAP: {m['gap']:+d}\n"

        f"RSI: {m['rsi']:.1f}\n\n"

        "━━━━━━━━━━━━━━\n"

        "5M ENTRY\n"

        f"Direction: {e['direction']}\n"

        f"CALL Score: {e['call']}\n"

        f"PUT Score: {e['put']}\n"

        f"GAP: {e['gap']:+d}\n"

        f"RSI: {e['rsi']:.1f}\n\n"

        f"TIME: "
        f"{thai_now().strftime('%H:%M:%S')}"

    )


# =========================================================
# PROCESS
# =========================================================

def process_symbol(
    symbol
):

    try:

        df5 = get_5m(
            symbol
        )


        result = analyze_v85(
            df5
        )


        if result is None:

            logger.info(
                "%s | WAIT DATA",
                symbol
            )

            return


        m = result["master"]

        e = result["entry"]

        final = result["final"]

        candle = result["candle"]


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
            candle,
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

            LAST_SIGNAL.add(
                signal_id
            )


    except Exception as e:

        logger.error(
            "%s | %s",
            symbol,
            e
        )


# =========================================================
# SCAN
# =========================================================

def process_market():

    logger.info(
        "===== 8X V8.5 SCAN START ====="
    )


    active = 0


    with DATA_LOCK:

        for symbol in SYMBOLS:

            if (
                symbol + "_5m"
                in MARKET_DATA
            ):

                active += 1


    logger.info(
        "8X 5M DATA ACTIVE: %d/%d",
        active,
        len(SYMBOLS)
    )


    for symbol in SYMBOLS:

        process_symbol(
            symbol
        )

        time.sleep(
            0.25
        )


    logger.info(
        "===== 8X V8.5 SCAN END ====="
    )


# =========================================================
# LOOP
# =========================================================

def scanner_loop():

    logger.info(
        "TRADEIFY V8.5 START"
    )

    logger.info(
        "MODE: 8X 5M"
    )

    logger.info(
        "15M CREATED FROM 3 x 5M"
    )

    logger.info(
        "PAIRS: %d",
        len(SYMBOLS)
    )


    while True:

        started = time.time()


        try:

            process_market()

        except Exception as e:

            logger.exception(
                "SCAN ERROR: %s",
                e
            )


        elapsed = (
            time.time()
            -
            started
        )


        wait = max(
            SCAN_SECONDS -
            elapsed,
            5
        )


        logger.info(
            "NEXT SCAN IN %.1f SEC",
            wait
        )


        time.sleep(
            wait
        )


# =========================================================
# MAIN
# =========================================================

def main():

    logger.info(
        "================================"
    )

    logger.info(
        "TRADEIFY V8.5 8X"
    )

    logger.info(
        "DISCORD: %s",
        "SET"
        if DISCORD_WEBHOOK_URL
        else "NOT SET"
    )

    logger.info(
        "================================"
    )


    threading.Thread(
        target=start_server,
        daemon=True
    ).start()


    scanner_loop()


if __name__ == "__main__":

    main()
