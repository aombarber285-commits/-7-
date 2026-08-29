# -*- coding: utf-8 -*-
import os
import time
import logging
import threading
from datetime import datetime, timezone, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import requests
import pandas as pd
import numpy as np
# =========================================================
# TRADEIFY V8.4
# 8X TRADE BRIDGE
#
# 15M = MASTER
# 5M  = ENTRY
# Discord = SIGNAL
#
# NO IQ OPTION
# NO TWELVE DATA
# NO OTC_API_URL
# =========================================================
APP_NAME = "TRADEIFY V8.4 8X BRIDGE"
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
# 8X SYMBOLS
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
# RUNTIME DATA
# =========================================================
MARKET_DATA = {}
DATA_LOCK = threading.Lock()
LAST_SIGNAL = set()
LAST_CANDLE = {}
LAST_DISCORD = 0
DISCORD_LOCK = threading.Lock()
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
# HEALTH + DATA BRIDGE SERVER
# =========================================================
class BridgeHandler(
    BaseHTTPRequestHandler
):
    def send_json(
        self,
        status,
        payload
    ):
        body = json.dumps(
            payload,
            ensure_ascii=False
        ).encode(
            "utf-8"
        )
        self.send_response(
            status
        )
        self.send_header(
            "Content-Type",
            "application/json; charset=utf-8"
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
    def do_GET(
        self
    ):
        if self.path == "/":
            self.send_json(
                200,
                {
                    "status":
                        "online",
                    "service":
                        APP_NAME,
                    "symbols":
                        len(SYMBOLS),
                    "mode":
                        "8X_BRIDGE"
                }
            )
            return
        if self.path == "/health":
            self.send_json(
                200,
                {
                    "status":
                        "ok"
                }
            )
            return
        if self.path == "/market":
            with DATA_LOCK:
                data = MARKET_DATA.copy()
            self.send_json(
                200,
                data
            )
            return
        self.send_json(
            404,
            {
                "error":
                    "not found"
            }
        )
    def do_POST(
        self
    ):
        if self.path != "/market":
            self.send_json(
                404,
                {
                    "error":
                        "not found"
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
                raw.decode(
                    "utf-8"
                )
            )
            symbol = str(
                payload.get(
                    "symbol",
                    ""
                )
            ).upper()
            timeframe = str(
                payload.get(
                    "timeframe",
                    ""
                ).lower()
            )
            candles = payload.get(
                "candles",
                []
            )
            if symbol not in SYMBOLS:
                self.send_json(
                    400,
                    {
                        "error":
                            "invalid symbol"
                    }
                )
                return
            if timeframe not in (
                "5m",
                "15m"
            ):
                self.send_json(
                    400,
                    {
                        "error":
                            "timeframe must be 5m or 15m"
                    }
                )
                return
            if not isinstance(
                candles,
                list
            ):
                self.send_json(
                    400,
                    {
                        "error":
                            "candles must be list"
                    }
                )
                return
            key = (
                symbol
                +
                "_"
                +
                timeframe
            )
            with DATA_LOCK:
                MARKET_DATA[
                    key
                ] = candles
            logger.info(
                "8X DATA RECEIVED | %s | %s | %d candles",
                symbol,
                timeframe,
                len(candles)
            )
            self.send_json(
                200,
                {
                    "ok":
                        True,
                    "symbol":
                        symbol,
                    "timeframe":
                        timeframe,
                    "candles":
                        len(candles)
                }
            )
        except Exception as e:
            logger.exception(
                "BRIDGE ERROR"
            )
            self.send_json(
                500,
                {
                    "error":
                        str(e)
                }
            )
def start_server():
    server = HTTPServer(
        (
            "0.0.0.0",
            PORT
        ),
        BridgeHandler
    )
    logger.info(
        "8X BRIDGE SERVER :%s",
        PORT
    )
    server.serve_forever()
# =========================================================
# GET BRIDGE DATA
# =========================================================
def get_market_data(
    symbol,
    timeframe,
    limit=220
):
    key = (
        symbol
        +
        "_"
        +
        timeframe
    )
    with DATA_LOCK:
        candles = MARKET_DATA.get(
            key,
            []
        )
    if not candles:
        raise RuntimeError(
            f"ยังไม่มีข้อมูล 8X: "
            f"{symbol} {timeframe}"
        )
    rows = []
    for candle in candles[-limit:]:
        if not isinstance(
            candle,
            dict
        ):
            continue
        try:
            timestamp = candle.get(
                "timestamp",
                candle.get(
                    "time"
                )
            )
            if timestamp is None:
                continue
            timestamp = float(
                timestamp
            )
            if timestamp > 10_000_000_000:
                timestamp /= 1000
            rows.append({
                "datetime":
                    datetime.fromtimestamp(
                        timestamp,
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
                        candle["high"]
                    ),
                "low":
                    float(
                        candle["low"]
                    ),
                "close":
                    float(
                        candle["close"]
                    ),
            })
        except Exception:
            continue
    if len(rows) < 10:
        raise RuntimeError(
            f"8X candle ไม่พอ: "
            f"{symbol} {timeframe}"
        )
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
# ANALYSIS
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
    }
# =========================================================
# V8.4 MASTER + ENTRY
# =========================================================
def analyze_v84(
    df15,
    df5
):
    master = analyze_tf(
        df15
    )
    entry = analyze_tf(
        df5
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
    global LAST_DISCORD
    if not DISCORD_WEBHOOK_URL:
        logger.error(
            "DISCORD_WEBHOOK_URL NOT SET"
        )
        return False
    with DISCORD_LOCK:
        elapsed = (
            time.time()
            -
            LAST_DISCORD
        )
        if elapsed < 1.5:
            time.sleep(
                1.5 -
                elapsed
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
                    LAST_DISCORD = (
                        time.time()
                    )
                    return True
                if response.status_code == 429:
                    try:
                        retry = float(
                            response.json().get(
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
                    "DISCORD ERROR %s",
                    response.status_code
                )
                return False
            except Exception as e:
                logger.error(
                    "DISCORD ERROR: %s",
                    e
                )
                time.sleep(
                    2
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
        f"{emoji} TRADEIFY V8.4 8X\n\n"
        f"PAIR: {symbol}\n"
        f"SIGNAL: {final}\n\n"
        "━━━━━━━━━━━━━━\n"
        "15M MASTER\n"
        f"DIR: {m['direction']}\n"
        f"CALL: {m['call']}\n"
        f"PUT: {m['put']}\n"
        f"GAP: {m['gap']:+d}\n"
        f"RSI: {m['rsi']:.1f}\n\n"
        "━━━━━━━━━━━━━━\n"
        "5M ENTRY\n"
        f"DIR: {e['direction']}\n"
        f"CALL: {e['call']}\n"
        f"PUT: {e['put']}\n"
        f"GAP: {e['gap']:+d}\n"
        f"RSI: {e['rsi']:.1f}\n\n"
        f"TIME: "
        f"{thai_now().strftime('%H:%M:%S')}"
    )
# =========================================================
# PROCESS SYMBOL
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
        df15 = get_market_data(
            symbol,
            "15m",
            120
        )
        if len(df5) < 100:
            return
        if len(df15) < 85:
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
        result = analyze_v84(
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
# MARKET LOOP
# =========================================================
def process_market():
    logger.info(
        "===== 8X SCAN START ====="
    )
    active = 0
    with DATA_LOCK:
        for symbol in SYMBOLS:
            if (
                symbol + "_5m"
                in MARKET_DATA
                and
                symbol + "_15m"
                in MARKET_DATA
            ):
                active += 1
    logger.info(
        "8X DATA ACTIVE: %d/%d",
        active,
        len(SYMBOLS)
    )
    for symbol in SYMBOLS:
        process_symbol(
            symbol
        )
        time.sleep(
            0.3
        )
    logger.info(
        "===== 8X SCAN END ====="
    )
# =========================================================
# SCANNER
# =========================================================
def scanner_loop():
    logger.info(
        "TRADEIFY V8.4 START"
    )
    logger.info(
        "MODE: 8X BRIDGE"
    )
    logger.info(
        "15M MASTER + 5M ENTRY"
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
        "TRADEIFY V8.4 8X START"
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
        "================================"
    )
    threading.Thread(
        target=start_server,
        daemon=True
    ).start()
    scanner_loop()
if __name__ == "__main__":
    main()
