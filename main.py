import os
import urllib.request
import urllib.parse
import json
import time
from datetime import datetime
from zoneinfo import ZoneInfo
import threading
import sqlite3
import numpy as np


# ============================================================
# V15 ADAPTIVE PAPER TRAINING + DISCORD
# ============================================================
#
# REAL ORDER
# ----------------
# OFF
#
# PAPER TRAINING
# ----------------
# เปิด PAPER ทุก 30 นาที
# ผล PAPER หลัง 2 นาที
#
# ถ้ามี V15 Signal จริง
# -> ใช้ Candidate ที่ดีที่สุด
#
# ถ้าไม่มี V15 Signal
# -> FORCE PAPER TEST
# -> ใช้ Candidate ที่ดีที่สุดจากตลาด
#
# PAPER RESULT
# ----------------
# WIN / LOSS / DRAW
#       ↓
# JOURNAL
#       ↓
# ADAPTIVE LEARNING
#
# ============================================================


# ============================================================
# CONFIG
# ============================================================

REAL_ORDER_ENABLED = False

PAPER_TRAINING_ENABLED = True

# ทุก 30 นาที
PAPER_INTERVAL_SECONDS = 30 * 60

# วัดผลหลัง 2 นาที
PAPER_HORIZON_SECONDS = 2 * 60

# ถ้า True:
# ทุก 30 นาทีต้องพยายามเปิด PAPER 1 ไม้
FORCE_PAPER_TEST = True

# ถ้า True จะไม่เปิดคู่ที่ยังมี PAPER รอผล
AVOID_DUPLICATE_ACTIVE_PAIR = True

# Timezone ไทย
THAI_TZ = ZoneInfo("Asia/Bangkok")


# ============================================================
# DISCORD WEBHOOK
# ============================================================

DISCORD_WEBHOOK_URL = os.getenv(
    "DISCORD_WEBHOOK_URL",
    "PASTE_YOUR_DISCORD_WEBHOOK_HERE"
)


# ============================================================
# PAIRS
# ============================================================

PAIRS_S2 = [
    {
        "name": "BTC/USDT",
        "symbol": "BTCUSDT"
    },
    {
        "name": "ETH/USDT",
        "symbol": "ETHUSDT"
    },
    {
        "name": "SOL/USDT",
        "symbol": "SOLUSDT"
    },
    {
        "name": "XRP/USDT",
        "symbol": "XRPUSDT"
    },
    {
        "name": "BNB/USDT",
        "symbol": "BNBUSDT"
    },
    {
        "name": "DOGE/USDT",
        "symbol": "DOGEUSDT"
    },
]


# ============================================================
# TIME
# ============================================================

def thai_now():
    return datetime.now(THAI_TZ)


def thai_text():
    return thai_now().strftime(
        "%Y-%m-%d %H:%M:%S"
    )


# ============================================================
# DISCORD
# ============================================================

def discord_enabled():

    return bool(
        DISCORD_WEBHOOK_URL
        and
        DISCORD_WEBHOOK_URL.startswith(
            "https://discord.com/api/webhooks/"
        )
    )


def send_discord_signal_s2(message):

    if not discord_enabled():

        print(
            "[DISCORD DISABLED] "
            "Webhook not configured."
        )

        print(message)

        return False

    try:

        payload = json.dumps(
            {
                "content": message[:1900]
            }
        ).encode("utf-8")

        req = urllib.request.Request(
            DISCORD_WEBHOOK_URL,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "V15-Paper-Trainer/3.0",
            },
            method="POST",
        )

        with urllib.request.urlopen(
            req,
            timeout=10
        ) as response:

            return response.status in (
                200,
                204
            )

    except Exception as e:

        print(
            f"[DISCORD ERROR] {e}"
        )

        return False


# ============================================================
# BINANCE DATA
# ============================================================

def fetch_klines(
    symbol,
    interval,
    limit=250
):

    params = urllib.parse.urlencode(
        {
            "symbol": symbol,
            "interval": interval,
            "limit": limit,
        }
    )

    url = (
        "https://api.binance.com/api/v3/klines?"
        + params
    )

    try:

        req = urllib.request.Request(
            url,
            headers={
                "User-Agent":
                    "V15-Paper-Trainer/3.0"
            }
        )

        with urllib.request.urlopen(
            req,
            timeout=8
        ) as response:

            data = json.loads(
                response.read().decode()
            )

            return [
                [
                    int(k[0]),
                    float(k[1]),
                    float(k[2]),
                    float(k[3]),
                    float(k[4]),
                    float(k[5]),
                ]
                for k in data
            ]

    except Exception as e:

        print(
            f"[BINANCE ERROR] "
            f"{symbol}/{interval}: {e}"
        )

        return []


# ============================================================
# CANDLE PARSER
# ============================================================

def parse_kline(k):

    o = k[1]
    h = k[2]
    l = k[3]
    c = k[4]
    vol = k[5]

    total_len = (
        h - l
        if h - l > 0
        else 0.00001
    )

    body = abs(c - o)

    return {

        "o": o,
        "h": h,
        "l": l,
        "c": c,
        "vol": vol,

        "total_len": total_len,

        "body": body,

        "midpoint": (
            o + c
        ) / 2,

        "body_pct": (
            body / total_len
        ) * 100,

        "upper_wick_pct": (
            (h - max(o, c))
            / total_len
        ) * 100,

        "lower_wick_pct": (
            (min(o, c) - l)
            / total_len
        ) * 100,

        "is_green": c > o,

        "is_red": c < o,
    }


# ============================================================
# RSI
# ============================================================

def calculate_wilder_rsi(
    closes,
    period=14
):

    if len(closes) < period + 1:

        return 50.0

    deltas = np.diff(closes)

    gains = np.where(
        deltas > 0,
        deltas,
        0.0
    )

    losses = np.where(
        deltas < 0,
        -deltas,
        0.0
    )

    avg_gain = np.mean(
        gains[:period]
    )

    avg_loss = np.mean(
        losses[:period]
    )

    for i in range(
        period,
        len(deltas)
    ):

        avg_gain = (
            avg_gain * (period - 1)
            + gains[i]
        ) / period

        avg_loss = (
            avg_loss * (period - 1)
            + losses[i]
        ) / period

    if avg_loss == 0:

        return 100.0

    return (
        100
        -
        (
            100
            /
            (
                1
                +
                avg_gain / avg_loss
            )
        )
    )


# ============================================================
# EMA
# ============================================================

def calculate_ema(
    closes,
    period
):

    if not closes:

        return 0.0

    if len(closes) < period:

        return closes[-1]

    multiplier = (
        2 / (period + 1)
    )

    ema = closes[0]

    for price in closes[1:]:

        ema = (
            price - ema
        ) * multiplier + ema

    return ema


# ============================================================
# ATR
# ============================================================

def calculate_atr_pct(
    parsed_klines,
    period=14
):

    # ไม่ใช้ current candle
    closed = parsed_klines[:-1]

    if len(closed) < period + 2:

        return 0.0

    tr_list = []

    for i in range(
        1,
        len(closed)
    ):

        h = closed[i]["h"]
        l = closed[i]["l"]

        prev_c = closed[i - 1]["c"]

        tr = max(
            h - l,
            abs(h - prev_c),
            abs(l - prev_c)
        )

        tr_list.append(tr)

    atr_val = np.mean(
        tr_list[-period:]
    )

    current_close = (
        closed[-1]["c"]
    )

    if current_close <= 0:

        return 0.0

    return (
        atr_val
        /
        current_close
    ) * 100


# ============================================================
# MARKET REGIME
# ============================================================

def detect_market_regime_atr_pct(
    parsed_klines,
    atr_pct
):

    closes = [
        p["c"]
        for p in parsed_klines[-15:-1]
    ]

    if len(closes) < 3:

        return "RANGE"

    linear_slope = (
        closes[-1] - closes[0]
    )

    if atr_pct > 1.2:

        return "HIGH_VOLATILITY"

    if atr_pct < 0.25:

        return "LOW_VOLATILITY"

    if (
        linear_slope
        >
        closes[-1] * 0.0025
    ):

        return "TREND_UP"

    if (
        linear_slope
        <
        -closes[-1] * 0.0025
    ):

        return "TREND_DOWN"

    return "RANGE"


# ============================================================
# SUPPORT / RESISTANCE
# ============================================================

def calculate_no_lookahead_sr(
    parsed_klines
):

    if len(parsed_klines) >= 32:

        history = (
            parsed_klines[-32:-2]
        )

    else:

        history = (
            parsed_klines[:-2]
        )

    if not history:

        return 0.0, 0.0

    resistance = max(
        p["h"]
        for p in history
    )

    support = min(
        p["l"]
        for p in history
    )

    return resistance, support


# ============================================================
# SWING STRUCTURE
# ============================================================

def evaluate_real_swing_structure(
    parsed_klines
):

    history = parsed_klines[:-1]

    if len(history) < 20:

        return {
            "bos_bullish": False,
            "bos_bearish": False,
            "choch_bullish": False,
            "choch_bearish": False,
            "score": 0,
        }

    swing_highs = []
    swing_lows = []

    for i in range(
        2,
        len(history) - 2
    ):

        if (
            history[i]["h"]
            >= history[i - 1]["h"]
            and
            history[i]["h"]
            >= history[i - 2]["h"]
            and
            history[i]["h"]
            >= history[i + 1]["h"]
            and
            history[i]["h"]
            >= history[i + 2]["h"]
        ):

            swing_highs.append(
                (
                    i,
                    history[i]["h"]
                )
            )

        if (
            history[i]["l"]
            <= history[i - 1]["l"]
            and
            history[i]["l"]
            <= history[i - 2]["l"]
            and
            history[i]["l"]
            <= history[i + 1]["l"]
            and
            history[i]["l"]
            <= history[i + 2]["l"]
        ):

            swing_lows.append(
                (
                    i,
                    history[i]["l"]
                )
            )

    result = {

        "bos_bullish": False,

        "bos_bearish": False,

        "choch_bullish": False,

        "choch_bearish": False,

        "score": 0,
    }

    curr_close = history[-1]["c"]

    if (
        len(swing_highs) >= 2
        and
        len(swing_lows) >= 2
    ):

        last_sh = swing_highs[-1][1]

        prev_sh = swing_highs[-2][1]

        last_sl = swing_lows[-1][1]

        prev_sl = swing_lows[-2][1]

        if curr_close > last_sh:

            result[
                "bos_bullish"
            ] = True

            result["score"] += 20

        elif curr_close < last_sl:

            result[
                "bos_bearish"
            ] = True

            result["score"] += 20

        if (
            prev_sh > last_sh
            and
            curr_close > prev_sh
        ):

            result[
                "choch_bullish"
            ] = True

            result["score"] += 15

        elif (
            prev_sl < last_sl
            and
            curr_close < prev_sl
        ):

            result[
                "choch_bearish"
            ] = True

            result["score"] += 15

    result["score"] = min(
        result["score"],
        25
    )

    return result


# ============================================================
# DISPLACEMENT + VOLUME
# ============================================================

def evaluate_displacement_and_volume(
    parsed_klines
):

    curr = parsed_klines[-2]

    vols = [
        p["vol"]
        for p in parsed_klines[-31:-2]
    ]

    if not vols:

        return 0, 0.0

    mean_vol = np.mean(vols)

    std_vol = np.std(vols)

    if std_vol <= 0:

        std_vol = 1.0

    vol_zscore = (
        curr["vol"] - mean_vol
    ) / std_vol

    score = 0

    if vol_zscore >= 2.0:

        score += 15

    elif vol_zscore >= 1.0:

        score += 10

    elif vol_zscore >= 0.5:

        score += 5

    if curr["body_pct"] >= 40.0:

        score += 10

    return score, vol_zscore


# ============================================================
# MTF BIAS
# ============================================================

def evaluate_strict_mtf_bias(
    symbol
):

    def get_tf_closed_structure(
        interval
    ):

        klines = fetch_klines(
            symbol,
            interval,
            250
        )

        if len(klines) < 200:

            return "NEUTRAL"

        parsed = [
            parse_kline(k)
            for k in klines
        ]

        closes = [
            p["c"]
            for p in parsed[:-1]
        ]

        ema_20 = calculate_ema(
            closes,
            20
        )

        ema_50 = calculate_ema(
            closes,
            50
        )

        ema_200 = calculate_ema(
            closes,
            200
        )

        curr_c = parsed[-2]["c"]

        if (
            curr_c
            >
            ema_20
            >
            ema_50
            >
            ema_200
        ):

            return "BULLISH"

        if (
            curr_c
            <
            ema_20
            <
            ema_50
            <
            ema_200
        ):

            return "BEARISH"

        return "NEUTRAL"

    return (
        get_tf_closed_structure("1h"),
        get_tf_closed_structure("15m"),
        get_tf_closed_structure("5m"),
    )


# ============================================================
# DISTANCE TO S/R
# ============================================================

def check_distance_to_sr(
    parsed_klines,
    proposed_action,
    atr_pct
):

    res_level, sup_level = (
        calculate_no_lookahead_sr(
            parsed_klines
        )
    )

    curr_c = parsed_klines[-2]["c"]

    if (
        curr_c <= 0
        or
        atr_pct <= 0
    ):

        return False, "PASSED"

    dist_res_pct = (
        (res_level - curr_c)
        /
        curr_c
    ) * 100

    dist_sup_pct = (
        (curr_c - sup_level)
        /
        curr_c
    ) * 100

    min_space = (
        atr_pct * 0.45
    )

    if (
        proposed_action == "CALL"
        and
        dist_res_pct < min_space
    ):

        return True, (
            "CALL Blocked: "
            "Resistance distance "
            f"({dist_res_pct:.3f}%) "
            "< Min Space"
        )

    if (
        proposed_action == "PUT"
        and
        dist_sup_pct < min_space
    ):

        return True, (
            "PUT Blocked: "
            "Support distance "
            f"({dist_sup_pct:.3f}%) "
            "< Min Space"
        )

    return False, "PASSED"


# ============================================================
# LIQUIDITY SWEEP
# ============================================================

def eval_liquidity_sweep_and_confirmation(
    parsed_klines
):

    res_level, sup_level = (
        calculate_no_lookahead_sr(
            parsed_klines
        )
    )

    curr = parsed_klines[-2]

    eql_swept = (
        curr["l"] < sup_level
        and
        curr["c"] > sup_level
    )

    eqh_swept = (
        curr["h"] > res_level
        and
        curr["c"] < res_level
    )

    return {

        "valid_call": (
            eql_swept
            and
            curr["lower_wick_pct"] >= 28
            and
            curr["is_green"]
        ),

        "valid_put": (
            eqh_swept
            and
            curr["upper_wick_pct"] >= 28
            and
            curr["is_red"]
        ),
    }


# ============================================================
# JOURNAL
# ============================================================

class ExpectancyJournalV15:

    def __init__(
        self,
        db_path=None
    ):

        if db_path is None:

            journal_dir = os.getenv(
                "JOURNAL_DIR",
                "./data"
            )

            os.makedirs(
                journal_dir,
                exist_ok=True
            )

            self.db_path = os.path.join(
                journal_dir,
                "v15_sniper_journal.db"
            )

        else:

            parent = os.path.dirname(
                db_path
            )

            if parent:

                os.makedirs(
                    parent,
                    exist_ok=True
                )

            self.db_path = db_path

        self.lock = threading.RLock()

        self.init_db()


    def _connect(self):

        conn = sqlite3.connect(
            self.db_path,
            check_same_thread=False,
            timeout=30
        )

        conn.execute(
            "PRAGMA journal_mode=WAL"
        )

        conn.execute(
            "PRAGMA busy_timeout=30000"
        )

        return conn


    def init_db(self):

        with self.lock:

            conn = self._connect()

            cur = conn.cursor()

            cur.execute("""
                CREATE TABLE IF NOT EXISTS journal (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT,
                    pair TEXT,
                    setup_key TEXT,
                    regime TEXT,
                    granular_key TEXT,
                    action TEXT,
                    grade TEXT,
                    confidence_score REAL,
                    trap_risk REAL,
                    h_bias TEXT,
                    mtf_15m TEXT,
                    mtf_5m TEXT,
                    target_candle_time INTEGER,
                    signal_price REAL,
                    target_open REAL,
                    actual_open REAL,
                    slippage REAL,
                    exit_close_price REAL,
                    mfe REAL,
                    mae REAL,
                    pnl_pct REAL,
                    result TEXT DEFAULT 'PENDING',
                    source TEXT DEFAULT 'V15'
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS granular_adaptive_state (
                    granular_key TEXT PRIMARY KEY,
                    cooldown_until_candle INTEGER DEFAULT 0,
                    consecutive_losses INTEGER DEFAULT 0,
                    is_circuit_active INTEGER DEFAULT 0
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS rate_limit_tracker (
                    pair TEXT PRIMARY KEY,
                    last_signal_timestamps TEXT
                )
            """)

            conn.commit()

            conn.close()


    # ========================================================
    # LOG SIGNAL
    # ========================================================

    def log_signal(
        self,
        pair,
        setup_key,
        regime,
        granular_key,
        action,
        grade,
        confidence_score,
        trap_risk,
        h_bias,
        mtf_15m,
        mtf_5m,
        target_candle_time,
        signal_price,
        target_open,
        actual_open,
        slippage,
        source="V15",
    ):

        with self.lock:

            conn = self._connect()

            cur = conn.cursor()

            cur.execute("""
                INSERT INTO journal (
                    timestamp,
                    pair,
                    setup_key,
                    regime,
                    granular_key,
                    action,
                    grade,
                    confidence_score,
                    trap_risk,
                    h_bias,
                    mtf_15m,
                    mtf_5m,
                    target_candle_time,
                    signal_price,
                    target_open,
                    actual_open,
                    slippage,
                    source
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (

                thai_text(),

                pair,

                setup_key,

                regime,

                granular_key,

                action,

                grade,

                confidence_score,

                trap_risk,

                h_bias,

                mtf_15m,

                mtf_5m,

                target_candle_time,

                signal_price,

                target_open,

                actual_open,

                slippage,

                source,
            ))

            signal_id = cur.lastrowid

            conn.commit()

            conn.close()

            return signal_id


    # ========================================================
    # UPDATE PAPER RESULT
    # ========================================================

    def update_forward_test_result(
        self,
        signal_id,
        granular_key,
        target_kline,
        action,
        current_candle_time,
    ):

        actual_open_price = target_kline[1]

        high = target_kline[2]

        low = target_kline[3]

        exit_close_price = target_kline[4]

        if actual_open_price <= 0:

            raise ValueError(
                "Invalid target open price"
            )


        if action == "CALL":

            mfe = (
                (
                    high
                    -
                    actual_open_price
                )
                /
                actual_open_price
            ) * 100

            mae = (
                (
                    actual_open_price
                    -
                    low
                )
                /
                actual_open_price
            ) * 100

            pnl_pct = (
                (
                    exit_close_price
                    -
                    actual_open_price
                )
                /
                actual_open_price
            ) * 100

            if (
                exit_close_price
                >
                actual_open_price
            ):

                result = "WIN"

            elif (
                exit_close_price
                <
                actual_open_price
            ):

                result = "LOSS"

            else:

                result = "DRAW"


        else:

            mfe = (
                (
                    actual_open_price
                    -
                    low
                )
                /
                actual_open_price
            ) * 100

            mae = (
                (
                    high
                    -
                    actual_open_price
                )
                /
                actual_open_price
            ) * 100

            pnl_pct = (
                (
                    actual_open_price
                    -
                    exit_close_price
                )
                /
                actual_open_price
            ) * 100

            if (
                exit_close_price
                <
                actual_open_price
            ):

                result = "WIN"

            elif (
                exit_close_price
                >
                actual_open_price
            ):

                result = "LOSS"

            else:

                result = "DRAW"


        with self.lock:

            conn = self._connect()

            cur = conn.cursor()

            cur.execute("""
                UPDATE journal
                SET
                    exit_close_price=?,
                    mfe=?,
                    mae=?,
                    pnl_pct=?,
                    result=?
                WHERE id=?
            """, (

                exit_close_price,

                mfe,

                mae,

                pnl_pct,

                result,

                signal_id,
            ))


            cur.execute("""
                SELECT consecutive_losses
                FROM granular_adaptive_state
                WHERE granular_key=?
            """, (
                granular_key,
            ))

            row = cur.fetchone()

            consec_losses = (
                row[0]
                if row
                else 0
            )


            if result == "LOSS":

                consec_losses += 1

                if consec_losses == 1:

                    cooldown_bars = 3

                elif consec_losses == 2:

                    cooldown_bars = 6

                else:

                    cooldown_bars = 15

                cooldown_until = (
                    current_candle_time
                    +
                    cooldown_bars * 60000
                )

                circuit_active = 1


            elif result == "WIN":

                consec_losses = 0

                cooldown_until = 0

                circuit_active = 0


            else:

                cooldown_until = 0

                circuit_active = 0


            cur.execute("""
                INSERT INTO granular_adaptive_state (
                    granular_key,
                    cooldown_until_candle,
                    consecutive_losses,
                    is_circuit_active
                )
                VALUES (?, ?, ?, ?)

                ON CONFLICT(granular_key)
                DO UPDATE SET
                    cooldown_until_candle=excluded.cooldown_until_candle,
                    consecutive_losses=excluded.consecutive_losses,
                    is_circuit_active=excluded.is_circuit_active
            """, (

                granular_key,

                cooldown_until,

                consec_losses,

                circuit_active,
            ))

            conn.commit()

            conn.close()


        return (
            result,
            pnl_pct,
            mfe,
            mae
        )


    # ========================================================
    # ADAPTIVE GUARD
    # ========================================================

    def check_granular_adaptive_guards(
        self,
        granular_key,
        current_candle_time,
    ):

        with self.lock:

            conn = self._connect()

            cur = conn.cursor()

            cur.execute("""
                SELECT
                    cooldown_until_candle,
                    consecutive_losses,
                    is_circuit_active
                FROM granular_adaptive_state
                WHERE granular_key=?
            """, (
                granular_key,
            ))

            row = cur.fetchone()


            if not row:

                conn.close()

                return (
                    False,
                    "NORMAL"
                )


            cooldown_until = row[0]

            losses = row[1]

            active = row[2]


            if (
                current_candle_time
                >=
                cooldown_until
                and
                active == 1
            ):

                cur.execute("""
                    UPDATE granular_adaptive_state
                    SET
                        is_circuit_active=0,
                        cooldown_until_candle=0,
                        consecutive_losses=0
                    WHERE granular_key=?
                """, (
                    granular_key,
                ))

                conn.commit()

                conn.close()

                return (
                    False,
                    "COOLDOWN_EXPIRED_RESET"
                )


            if (
                current_candle_time
                <
                cooldown_until
            ):

                conn.close()

                mins = max(
                    0,
                    int(
                        (
                            cooldown_until
                            -
                            current_candle_time
                        )
                        /
                        60000
                    )
                )

                return (
                    True,
                    (
                        "GRANULAR_COOLDOWN_ACTIVE "
                        f"({mins}m, Losses: {losses})"
                    )
                )


            conn.close()

            return (
                False,
                "PASSED"
            )


    # ========================================================
    # RATE LIMIT
    # ========================================================

    def record_confirmed_rate_limit(
        self,
        pair,
        current_time_ms,
    ):

        with self.lock:

            conn = self._connect()

            cur = conn.cursor()

            cur.execute("""
                SELECT last_signal_timestamps
                FROM rate_limit_tracker
                WHERE pair=?
            """, (
                pair,
            ))

            row = cur.fetchone()

            timestamps = (
                json.loads(row[0])
                if row and row[0]
                else []
            )

            cutoff = (
                current_time_ms
                -
                15 * 60 * 1000
            )

            timestamps = [
                t
                for t in timestamps
                if t > cutoff
            ]

            timestamps.append(
                current_time_ms
            )

            cur.execute("""
                INSERT INTO rate_limit_tracker (
                    pair,
                    last_signal_timestamps
                )
                VALUES (?, ?)

                ON CONFLICT(pair)
                DO UPDATE SET
                    last_signal_timestamps=excluded.last_signal_timestamps
            """, (

                pair,

                json.dumps(
                    timestamps
                ),
            ))

            conn.commit()

            conn.close()


    def check_rate_limit(
        self,
        pair,
        current_time_ms,
    ):

        with self.lock:

            conn = self._connect()

            cur = conn.cursor()

            cur.execute("""
                SELECT last_signal_timestamps
                FROM rate_limit_tracker
                WHERE pair=?
            """, (
                pair,
            ))

            row = cur.fetchone()

            conn.close()


        timestamps = (
            json.loads(row[0])
            if row and row[0]
            else []
        )

        cutoff = (
            current_time_ms
            -
            15 * 60 * 1000
        )

        timestamps = [
            t
            for t in timestamps
            if t > cutoff
        ]

        if len(timestamps) >= 3:

            return (
                True,
                (
                    f"Rate Limit: "
                    f"{len(timestamps)} "
                    "confirmed / 15m"
                )
            )

        return (
            False,
            "PASSED"
        )


    # ========================================================
    # EXPECTANCY / LEARNING
    # ========================================================

    def get_granular_expectancy_and_learning(
        self,
        granular_key,
        raw_score,
    ):

        with self.lock:

            conn = self._connect()

            cur = conn.cursor()

            cur.execute("""
                SELECT
                    result,
                    pnl_pct,
                    confidence_score,
                    mfe,
                    mae
                FROM journal
                WHERE granular_key=?
                AND result IN ('WIN','LOSS')
                ORDER BY id ASC
            """, (
                granular_key,
            ))

            rows = cur.fetchall()

            conn.close()


        total = len(rows)


        if total < 10:

            return {

                "tier": "LEARNING",

                "status": "LEARNING",

                "samples": total,

                "winrate": 50.0,

                "expectancy": 0.0,

                "calibrated_winrate": 50.0,

                "avg_mfe": 0.0,

                "avg_mae": 0.0,

                "mae_penalty": 0.0,
            }


        wins = [
            r[1]
            for r in rows
            if r[0] == "WIN"
        ]

        losses = [
            abs(r[1])
            for r in rows
            if r[0] == "LOSS"
        ]


        winrate = (
            len(wins)
            /
            total
            *
            100
        )


        avg_win = (
            np.mean(wins)
            if wins
            else 0.0
        )

        avg_loss = (
            np.mean(losses)
            if losses
            else 0.0
        )


        expectancy = (
            winrate / 100
            * avg_win
            -
            (
                (100 - winrate)
                /
                100
            )
            * avg_loss
        )


        mfes = [
            r[3]
            for r in rows
            if r[3] is not None
        ]

        maes = [
            r[4]
            for r in rows
            if r[4] is not None
        ]


        avg_mfe = (
            np.mean(mfes)
            if mfes
            else 0.0
        )

        avg_mae = (
            np.mean(maes)
            if maes
            else 0.0
        )


        mae_penalty = (

            15.0

            if (
                avg_mae
                >
                avg_mfe * 1.4
            )

            else 0.0
        )


        bracket_min = (
            raw_score // 5
        ) * 5


        bracket_rows = [
            r
            for r in rows
            if (
                bracket_min
                <=
                r[2]
                <=
                bracket_min + 4
            )
        ]


        if len(bracket_rows) >= 4:

            calibrated_winrate = (
                len([
                    r
                    for r in bracket_rows
                    if r[0] == "WIN"
                ])
                /
                len(bracket_rows)
                *
                100
            )

        else:

            calibrated_winrate = (
                winrate
            )


        tier = (

            "HIGH_CONFIDENCE"

            if total >= 30

            else "VALIDATED"

            if total >= 15

            else "CAUTION"
        )


        status = (

            "ADAPTIVE_BLOCKED"

            if (
                winrate < 50
                or
                expectancy <= 0
                or
                avg_mae > 0.38
            )

            else "ACTIVE"
        )


        return {

            "tier": tier,

            "status": status,

            "samples": total,

            "winrate": winrate,

            "expectancy": expectancy,

            "calibrated_winrate":
                calibrated_winrate,

            "avg_mfe": avg_mfe,

            "avg_mae": avg_mae,

            "mae_penalty":
                mae_penalty,
        }


# ============================================================
# JOURNAL INSTANCE
# ============================================================

journal_engine = (
    ExpectancyJournalV15()
)


# ============================================================
# ANALYZE V15
# ============================================================

def analyze_candle_vision_v15(
    symbol,
    name
):

    klines = fetch_klines(
        symbol,
        "1m",
        250
    )


    if len(klines) < 220:

        granular_key = (
            f"{name}|UNKNOWN"
        )

        return {

            "grade": "C",

            "decision": "NO TRADE",

            "setup_key": "UNKNOWN",

            "granular_key":
                granular_key,

            "score": 0.0,

            "trap_risk": 100.0,

            "regime": "UNKNOWN",

            "h_bias": "NEUTRAL",

            "mtf_15m": "NEUTRAL",

            "mtf_5m": "NEUTRAL",

            "target_time": 0,

            "rsi": 50.0,

            "flags": [
                "ข้อมูลไม่พอ"
            ],

            "stats":
                journal_engine
                .get_granular_expectancy_and_learning(
                    granular_key,
                    0
                ),
        }


    parsed = [
        parse_kline(k)
        for k in klines
    ]


    # ========================================================
    # CLOSED CANDLE ONLY
    # ========================================================

    target_time = (
        klines[-2][0]
    )


    closes = [
        p["c"]
        for p in parsed[:-1]
    ]


    rsi = calculate_wilder_rsi(
        closes
    )

    prev_rsi = calculate_wilder_rsi(
        closes[:-1]
    )


    atr_pct = calculate_atr_pct(
        parsed
    )


    regime = (
        detect_market_regime_atr_pct(
            parsed,
            atr_pct
        )
    )


    if regime in (
        "HIGH_VOLATILITY",
        "LOW_VOLATILITY",
    ):

        return {

            "grade": "C",

            "decision": "NO TRADE",

            "setup_key":
                "REGIME_BLOCK",

            "granular_key":
                f"{name}|REGIME_BLOCK|{regime}",

            "score": 0.0,

            "trap_risk": 100.0,

            "regime": regime,

            "h_bias": "NEUTRAL",

            "mtf_15m": "NEUTRAL",

            "mtf_5m": "NEUTRAL",

            "target_time":
                target_time,

            "rsi": rsi,

            "flags": [
                f"Hard Gate: {regime}"
            ],

            "stats": {

                "tier": "LEARNING",

                "status": "LEARNING",

                "samples": 0,

                "winrate": 50.0,

                "expectancy": 0.0,

                "calibrated_winrate": 50.0,

                "avg_mfe": 0.0,

                "avg_mae": 0.0,

                "mae_penalty": 0.0,
            },
        }


    h_bias, mtf_15m, mtf_5m = (
        evaluate_strict_mtf_bias(
            symbol
        )
    )


    struct = (
        evaluate_real_swing_structure(
            parsed
        )
    )


    disp_score, vol_z = (
        evaluate_displacement_and_volume(
            parsed
        )
    )


    sweep = (
        eval_liquidity_sweep_and_confirmation(
            parsed
        )
    )


    trap = 0.0

    flags = []


    liquidity_score = 0

    mtf_score = 0

    momentum_score = 0


    action = "NONE"

    setup_id = "UNKNOWN"


    # ========================================================
    # ACTION
    # ========================================================

    if (
        sweep["valid_call"]
        or
        struct["bos_bullish"]
        or
        struct["choch_bullish"]
    ):

        action = "CALL"

        setup_id = (
            "V15_BOS_SWEEP_CALL"
        )

        liquidity_score += 25


    elif (
        sweep["valid_put"]
        or
        struct["bos_bearish"]
        or
        struct["choch_bearish"]
    ):

        action = "PUT"

        setup_id = (
            "V15_BOS_SWEEP_PUT"
        )

        liquidity_score += 25


    # ========================================================
    # CALL
    # ========================================================

    if action == "CALL":

        if (
            rsi < 48
            and
            rsi > prev_rsi
        ):

            momentum_score += 15

        elif rsi > 72:

            trap += 30

            flags.append(
                "RSI Overbought"
            )


        if h_bias == "BULLISH":

            mtf_score += 10


        if mtf_15m == "BULLISH":

            mtf_score += 10


        if mtf_5m == "BULLISH":

            mtf_score += 5


        if h_bias == "BEARISH":

            trap += 45

            flags.append(
                "1H BEARISH vs CALL"
            )


    # ========================================================
    # PUT
    # ========================================================

    elif action == "PUT":

        if (
            rsi > 52
            and
            rsi < prev_rsi
        ):

            momentum_score += 15

        elif rsi < 28:

            trap += 30

            flags.append(
                "RSI Oversold"
            )


        if h_bias == "BEARISH":

            mtf_score += 10


        if mtf_15m == "BEARISH":

            mtf_score += 10


        if mtf_5m == "BEARISH":

            mtf_score += 5


        if h_bias == "BULLISH":

            trap += 45

            flags.append(
                "1H BULLISH vs PUT"
            )


    # ========================================================
    # S/R BLOCK
    # ========================================================

    if action != "NONE":

        blocked_sr, reason_sr = (
            check_distance_to_sr(
                parsed,
                action,
                atr_pct
            )
        )

        if blocked_sr:

            trap += 65

            flags.append(
                reason_sr
            )


    # ========================================================
    # RAW SCORE
    # ========================================================

    raw_score = (

        struct["score"]

        +

        liquidity_score

        +

        mtf_score

        +

        momentum_score

        +

        disp_score
    )


    setup_key = (
        f"{setup_id}|{action}"
    )


    granular_key = (
        f"{name}|"
        f"{setup_key}|"
        f"{regime}"
    )


    # ========================================================
    # ADAPTIVE GUARD
    # ========================================================

    blocked_adaptive, guard_reason = (

        journal_engine
        .check_granular_adaptive_guards(
            granular_key,
            target_time
        )
    )


    if blocked_adaptive:

        trap += 90

        flags.append(
            guard_reason
        )


    # ========================================================
    # RATE LIMIT
    # ========================================================

    rate_limited, rate_reason = (

        journal_engine
        .check_rate_limit(
            symbol,
            target_time
        )
    )


    if rate_limited:

        trap += 80

        flags.append(
            rate_reason
        )


    # ========================================================
    # LEARNING
    # ========================================================

    stats = (

        journal_engine
        .get_granular_expectancy_and_learning(
            granular_key,
            raw_score
        )
    )


    if (
        stats["status"]
        ==
        "ADAPTIVE_BLOCKED"
    ):

        trap += 85

        flags.append(
            "Adaptive blocked: "
            f"WR {stats['winrate']:.1f}%"
        )


    adjusted_score = max(
        0.0,
        raw_score
        -
        stats["mae_penalty"]
    )


    # ========================================================
    # MTF ALIGNMENT
    # ========================================================

    aligned = (

        (
            action == "CALL"
            and
            h_bias == "BULLISH"
            and
            mtf_15m == "BULLISH"
        )

        or

        (
            action == "PUT"
            and
            h_bias == "BEARISH"
            and
            mtf_15m == "BEARISH"
        )
    )


    grade = "C"

    decision = "NO TRADE"


    # ========================================================
    # GRADE
    # ========================================================

    if stats["tier"] == "LEARNING":

        flags.append(
            f"Learning Mode: "
            f"{stats['samples']}/10"
        )


        if (
            trap <= 5
            and
            adjusted_score >= 82
            and
            aligned
            and
            not blocked_adaptive
        ):

            grade = "A"

            decision = action


        elif (
            trap <= 15
            and
            adjusted_score >= 72
        ):

            grade = "B"

            decision = "WAIT"


    else:

        if (
            trap <= 5
            and
            adjusted_score >= 88
            and
            aligned
            and
            not blocked_adaptive
        ):

            grade = "A+"

            decision = action


        elif (
            trap <= 15
            and
            adjusted_score >= 80
            and
            aligned
            and
            not blocked_adaptive
        ):

            grade = "A"

            decision = action


        elif (
            trap <= 25
            and
            adjusted_score >= 70
        ):

            grade = "B"

            decision = "WAIT"


    return {

        "grade": grade,

        "decision": decision,

        "setup_key": setup_key,

        "granular_key":
            granular_key,

        "score": adjusted_score,

        "trap_risk":
            min(100.0, trap),

        "regime": regime,

        "h_bias": h_bias,

        "mtf_15m": mtf_15m,

        "mtf_5m": mtf_5m,

        "target_time": target_time,

        "rsi": rsi,

        "flags": flags,

        "stats": stats,

        "vol_z": vol_z,
    }


# ============================================================
# PAPER TRAINER
# ============================================================

class PaperTrainerV15:

    def __init__(self):

        self.lock = threading.RLock()

        self.active = []

        self.counter = 0

        self.last_cycle = 0.0

        self.stats = {

            "WIN": 0,

            "LOSS": 0,

            "DRAW": 0,
        }


    # ========================================================
    # ACTIVE SYMBOL CHECK
    # ========================================================

    def is_symbol_active(
        self,
        symbol
    ):

        with self.lock:

            for order in self.active:

                if (
                    order["symbol"]
                    ==
                    symbol
                ):

                    return True

        return False


    # ========================================================
    # SELECT BEST CANDIDATE
    # ========================================================

    def _select_best_candidate(
        self
    ):

        rows = []


        for p in PAIRS_S2:

            try:

                if (
                    AVOID_DUPLICATE_ACTIVE_PAIR
                    and
                    self.is_symbol_active(
                        p["symbol"]
                    )
                ):

                    print(
                        f"[PAPER] "
                        f"Skip active: "
                        f"{p['name']}"
                    )

                    continue


                r = analyze_candle_vision_v15(
                    p["symbol"],
                    p["name"]
                )


                # =================================================
                # DETERMINE DIRECTION
                # =================================================

                if r["decision"] in (
                    "CALL",
                    "PUT"
                ):

                    direction = (
                        r["decision"]
                    )

                    real_signal = True

                else:

                    real_signal = False

                    # ---------------------------------------------
                    # Strong MTF direction
                    # ---------------------------------------------

                    if (
                        r["h_bias"]
                        ==
                        "BULLISH"
                        and
                        r["mtf_15m"]
                        ==
                        "BULLISH"
                    ):

                        direction = "CALL"


                    elif (
                        r["h_bias"]
                        ==
                        "BEARISH"
                        and
                        r["mtf_15m"]
                        ==
                        "BEARISH"
                    ):

                        direction = "PUT"


                    # ---------------------------------------------
                    # 1H only
                    # ---------------------------------------------

                    elif (
                        r["h_bias"]
                        ==
                        "BULLISH"
                    ):

                        direction = "CALL"


                    elif (
                        r["h_bias"]
                        ==
                        "BEARISH"
                    ):

                        direction = "PUT"


                    # ---------------------------------------------
                    # 15m only
                    # ---------------------------------------------

                    elif (
                        r["mtf_15m"]
                        ==
                        "BULLISH"
                    ):

                        direction = "CALL"


                    elif (
                        r["mtf_15m"]
                        ==
                        "BEARISH"
                    ):

                        direction = "PUT"


                    # ---------------------------------------------
                    # RSI fallback
                    # ---------------------------------------------

                    else:

                        direction = (
                            "CALL"
                            if r["rsi"] >= 50
                            else "PUT"
                        )


                # =================================================
                # VOLATILITY BLOCK
                # =================================================

                if r["regime"] in (
                    "HIGH_VOLATILITY",
                    "LOW_VOLATILITY",
                    "UNKNOWN"
                ):

                    continue


                # =================================================
                # RANK
                # =================================================

                rank = (

                    r["score"]

                    +

                    (
                        20
                        if r["grade"]
                        ==
                        "A+"
                        else
                        12
                        if r["grade"]
                        ==
                        "A"
                        else
                        0
                    )

                    +

                    (
                        5
                        if r["stats"]["status"]
                        ==
                        "ACTIVE"
                        else
                        0
                    )

                    +

                    (
                        8
                        if real_signal
                        else
                        0
                    )

                    -

                    r["trap_risk"]
                    *
                    0.25
                )


                rows.append(
                    (
                        rank,
                        p,
                        r,
                        direction,
                        real_signal,
                    )
                )


            except Exception as e:

                print(
                    "[PAPER ANALYZE ERROR] "
                    f"{p['name']}: {e}"
                )


        if not rows:

            return None


        rows.sort(
            key=lambda x: x[0],
            reverse=True
        )


        return rows[0]


    # ========================================================
    # OPEN PAPER
    # ========================================================

    def open_cycle(self):

        if not PAPER_TRAINING_ENABLED:

            return False


        now = time.time()


        with self.lock:

            if (
                now - self.last_cycle
                <
                PAPER_INTERVAL_SECONDS
            ):

                return False

            self.last_cycle = now


        selected = (
            self._select_best_candidate()
        )


        if not selected:

            send_discord_signal_s2(
                "⚠️ **V15 PAPER TRAINING**\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "ไม่สามารถหา Candidate "
                "สำหรับรอบ PAPER นี้ได้"
            )

            return False


        (
            rank,
            p,
            r,
            direction,
            real_signal,
        ) = selected


        klines = fetch_klines(
            p["symbol"],
            "1m",
            5
        )


        if len(klines) < 2:

            return False


        # ========================================================
        # ENTRY = CLOSED CANDLE
        # ========================================================

        entry_kline = klines[-2]

        entry_price = entry_kline[4]

        entry_time = entry_kline[0]


        if entry_price <= 0:

            return False


        self.counter += 1


        paper_id = (
            f"PAPER-{self.counter:06d}"
        )


        source = (
            "PAPER_V15_SIGNAL"
            if real_signal
            else
            "PAPER_FORCED_TEST"
        )


        # ========================================================
        # JOURNAL
        # ========================================================

        signal_id = (
            journal_engine.log_signal(

                pair=p["name"],

                setup_key=r["setup_key"],

                regime=r["regime"],

                granular_key=r[
                    "granular_key"
                ],

                action=direction,

                grade="PAPER",

                confidence_score=r[
                    "score"
                ],

                trap_risk=r[
                    "trap_risk"
                ],

                h_bias=r[
                    "h_bias"
                ],

                mtf_15m=r[
                    "mtf_15m"
                ],

                mtf_5m=r[
                    "mtf_5m"
                ],

                target_candle_time=
                    entry_time,

                signal_price=
                    entry_price,

                target_open=
                    entry_price,

                actual_open=
                    entry_price,

                slippage=0.0,

                source=source,
            )
        )


        order = {

            "paper_id":
                paper_id,

            "signal_id":
                signal_id,

            "symbol":
                p["symbol"],

            "name":
                p["name"],

            "action":
                direction,

            "entry":
                entry_price,

            "entry_time":
                entry_time,

            "expire_at":
                (
                    time.time()
                    +
                    PAPER_HORIZON_SECONDS
                ),

            "granular_key":
                r["granular_key"],

            "score":
                r["score"],

            "trap":
                r["trap_risk"],

            "grade":
                r["grade"],

            "regime":
                r["regime"],

            "real_signal":
                real_signal,

            "source":
                source,
        }


        with self.lock:

            self.active.append(
                order
            )


        mode_text = (
            "🟢 V15 SIGNAL"
            if real_signal
            else
            "🟡 FORCED TEST"
        )


        send_discord_signal_s2(

            "🧠 **V15 PAPER TRAINING OPEN**\n"
            "━━━━━━━━━━━━━━━━━━━━\n"

            f"🆔 `{paper_id}`\n"

            f"📌 `{p['name']}`\n"

            f"🎯 **{direction}**\n"

            f"🧪 Mode: **{mode_text}**\n"

            f"💵 Entry `{entry_price:.8f}`\n"

            f"📊 Score `{r['score']:.1f}`\n"

            f"🛡️ Trap `{r['trap_risk']:.1f}`\n"

            f"🏷️ V15 Grade `{r['grade']}`\n"

            f"🌐 Regime `{r['regime']}`\n"

            f"📈 MTF "
            f"`{r['h_bias']} / "
            f"{r['mtf_15m']} / "
            f"{r['mtf_5m']}`\n"

            f"🧠 Learning "
            f"`{r['stats']['tier']}` / "
            f"`{r['stats']['samples']} samples`\n"

            f"🇹🇭 `{thai_text()}`\n\n"

            "⏱️ **วัดผลหลัง 2 นาที**\n"

            "❌ **PAPER ONLY — ไม่มีเงินจริง**"
        )


        print(
            f"[PAPER OPEN] "
            f"{paper_id} "
            f"{p['name']} "
            f"{direction} "
            f"{source}"
        )


        return True


    # ========================================================
    # RESOLVE
    # ========================================================

    def resolve(self):

        with self.lock:

            orders = list(
                self.active
            )


        if not orders:

            return


        remaining = []


        for o in orders:

            if (
                time.time()
                <
                o["expire_at"]
            ):

                remaining.append(o)

                continue


            # ====================================================
            # FETCH 1m DATA
            # ====================================================

            klines = fetch_klines(
                o["symbol"],
                "1m",
                10
            )


            if len(klines) < 3:

                remaining.append(o)

                continue


            # ====================================================
            # ENTRY CANDLE
            #
            # entry_time = timestamp
            # ของ candle ที่ปิดแล้ว
            #
            # 1m candle:
            #
            # entry_time
            #      ↓
            # +60s = target candle
            #      ↓
            # +120s = target candle close
            # ====================================================

            target_start = (
                o["entry_time"]
                +
                60 * 1000
            )


            target = None


            current_ms = int(
                time.time() * 1000
            )


            for k in klines:

                if (
                    k[0]
                    ==
                    target_start
                ):

                    # ต้องปิดแท่งแล้ว

                    if (
                        current_ms
                        >=
                        k[0]
                        +
                        60 * 1000
                    ):

                        target = k

                        break


            if target is None:

                remaining.append(o)

                continue


            # ====================================================
            # UPDATE RESULT
            # ====================================================

            try:

                (
                    result,
                    pnl,
                    mfe,
                    mae
                ) = (

                    journal_engine
                    .update_forward_test_result(

                        o["signal_id"],

                        o["granular_key"],

                        target,

                        o["action"],

                        target[0],
                    )
                )


            except Exception as e:

                print(
                    "[PAPER RESOLVE ERROR] "
                    f"{o['paper_id']}: {e}"
                )

                remaining.append(o)

                continue


            # ====================================================
            # STATS
            # ====================================================

            self.stats[result] = (
                self.stats.get(
                    result,
                    0
                )
                +
                1
            )


            stats = (

                journal_engine
                .get_granular_expectancy_and_learning(

                    o["granular_key"],

                    o["score"]
                )
            )


            total = sum(
                self.stats.values()
            )


            wr = (

                self.stats["WIN"]
                /
                total
                *
                100

                if total

                else 0
            )


            if result == "WIN":

                emoji = "✅"

            elif result == "LOSS":

                emoji = "❌"

            else:

                emoji = "➖"


            mode_text = (
                "V15 SIGNAL"
                if o["real_signal"]
                else "FORCED TEST"
            )


            # ====================================================
            # DISCORD RESULT
            # ====================================================

            send_discord_signal_s2(

                f"{emoji} **V15 PAPER RESULT**\n"
                "━━━━━━━━━━━━━━━━━━━━\n"

                f"🆔 `{o['paper_id']}`\n"

                f"📌 `{o['name']}`\n"

                f"🎯 `{o['action']}`\n"

                f"🧪 `{mode_text}`\n"

                f"💵 Entry "
                f"`{o['entry']:.8f}`\n"

                f"💵 Exit "
                f"`{target[4]:.8f}`\n"

                f"📈 Result "
                f"**{result}**\n"

                f"💰 P/L "
                f"`{pnl:+.4f}%`\n"

                f"📊 MFE "
                f"`{mfe:.4f}%` / "
                f"MAE "
                f"`{mae:.4f}%`\n\n"

                f"🧠 Samples "
                f"`{stats['samples']}`\n"

                f"🎯 Calibrated WR "
                f"`{stats['calibrated_winrate']:.1f}%`\n"

                f"📈 Expectancy "
                f"`{stats['expectancy']:.4f}%`\n"

                f"🏆 Tier "
                f"`{stats['tier']}`\n"

                f"🌐 Status "
                f"`{stats['status']}`\n\n"

                f"📚 SESSION "
                f"W `{self.stats['WIN']}` / "
                f"L `{self.stats['LOSS']}` / "
                f"D `{self.stats['DRAW']}`\n"

                f"🎯 Session WR "
                f"`{wr:.1f}%`\n"

                f"🇹🇭 `{thai_text()}`\n\n"

                "🔄 **บันทึกผลเข้า "
                "V15 Journal แล้ว**"
            )


            print(
                f"[PAPER RESULT] "
                f"{o['paper_id']} "
                f"{o['name']} "
                f"{o['action']} "
                f"{result} "
                f"{pnl:+.4f}%"
            )


        with self.lock:

            self.active = remaining


    # ========================================================
    # LOOP
    # ========================================================

    def loop(self):

        while True:

            try:

                self.resolve()

                self.open_cycle()


            except Exception as e:

                print(
                    f"[PAPER LOOP ERROR] "
                    f"{e}"
                )


            time.sleep(2)


# ============================================================
# PAPER TRAINER INSTANCE
# ============================================================

paper_trainer = (
    PaperTrainerV15()
)


# ============================================================
# PAIR STATES
# ============================================================

pair_states = {

    p["symbol"]: {

        "last_signal_time": 0,

        "lock": threading.Lock(),

    }

    for p in PAIRS_S2
}


# ============================================================
# MASTER SIGNAL WORKER
# ============================================================

def pair_worker_s2(pair):

    symbol = pair["symbol"]

    name = pair["name"]

    state = pair_states[
        symbol
    ]


    print(
        f"[{thai_now().strftime('%H:%M:%S')}] "
        f"🎯 V15 SNIPER Active: "
        f"{name}"
    )


    last_candle_time = None


    while True:

        try:

            klines = fetch_klines(
                symbol,
                "1m",
                5
            )


            if not klines:

                time.sleep(2)

                continue


            curr = klines[-1][0]


            # วิเคราะห์ครั้งเดียวต่อ candle

            if (
                last_candle_time
                !=
                curr
            ):

                last_candle_time = curr


                r = (
                    analyze_candle_vision_v15(
                        symbol,
                        name
                    )
                )


                # =================================================
                # MASTER SIGNAL
                # =================================================

                if (

                    r["decision"]
                    in
                    (
                        "CALL",
                        "PUT"
                    )

                    and

                    r["grade"]
                    in
                    (
                        "A+",
                        "A"
                    )

                    and

                    r["trap_risk"]
                    <
                    30
                ):


                    state[
                        "last_signal_time"
                    ] = time.time()


                    # ---------------------------------------------
                    # Confirmed Master Rate Limit
                    # ---------------------------------------------

                    journal_engine.record_confirmed_rate_limit(

                        symbol,

                        r["target_time"]
                    )


                    msg = (

                        f"🏆 **[V15 MASTER SIGNAL — "
                        f"GRADE {r['grade']}]**\n"

                        f"━━━━━━━━━━━━━━━━━━━━\n"

                        f"📌 `{name}`\n"

                        f"🎯 **{r['decision']}**\n"

                        f"📊 Score "
                        f"`{r['score']:.1f}/100`\n"

                        f"🛡️ Trap "
                        f"`{r['trap_risk']:.1f}/100`\n"

                        f"🌐 `{r['regime']}`\n"

                        f"📈 MTF "
                        f"`{r['h_bias']} / "
                        f"{r['mtf_15m']} / "
                        f"{r['mtf_5m']}`\n"

                        f"🧠 Learning "
                        f"`{r['stats']['tier']}` / "
                        f"`{r['stats']['samples']}`\n"

                        f"🎯 Calibrated WR "
                        f"`{r['stats']['calibrated_winrate']:.1f}%`\n"

                        f"📈 EV "
                        f"`{r['stats']['expectancy']:.4f}%`\n"

                        f"🇹🇭 `{thai_text()}`\n\n"

                        "🟢 **ผ่าน V15 เงื่อนไขแล้ว**\n"

                        "📨 ส่ง Discord แล้ว\n"

                        "🔒 **REAL ORDER = OFF**"
                    )


                    send_discord_signal_s2(
                        msg
                    )


        except Exception as e:

            print(
                f"⚠️ V15 Worker Error "
                f"({name}): {e}"
            )


        time.sleep(1.5)


# ============================================================
# START SYSTEM
# ============================================================

def start_script_2_system():

    print("=" * 60)

    print(
        "🚀 V15 UPGRADED SYSTEM ONLINE"
    )

    print("=" * 60)


    print(
        f"🇹🇭 Thailand time: "
        f"{thai_text()}"
    )


    print(
        f"🧪 PAPER interval: "
        f"{PAPER_INTERVAL_SECONDS}s "
        f"("
        f"{PAPER_INTERVAL_SECONDS // 60}"
        f" minutes)"
    )


    print(
        f"⏱️ PAPER horizon: "
        f"{PAPER_HORIZON_SECONDS}s "
        f"("
        f"{PAPER_HORIZON_SECONDS // 60}"
        f" minutes)"
    )


    print(
        f"🧪 FORCE PAPER TEST: "
        f"{FORCE_PAPER_TEST}"
    )


    print(
        "🔒 REAL ORDER: DISABLED"
    )


    # ========================================================
    # DISCORD START MESSAGE
    # ========================================================

    if discord_enabled():

        send_discord_signal_s2(

            "🚀 **V15 UPGRADED ONLINE**\n"
            "━━━━━━━━━━━━━━━━━━━━\n"

            "🧠 V15\n"
            "→ PAPER\n"
            "→ WIN/LOSS\n"
            "→ JOURNAL\n"
            "→ ADAPTIVE LEARNING\n\n"

            "🧪 PAPER ทุก **30 นาที**\n"

            "⏱️ RESULT หลัง **2 นาที**\n"

            "🟡 FORCE PAPER TEST = "
            f"`{FORCE_PAPER_TEST}`\n"

            "🔒 REAL ORDER = OFF\n"

            "🇹🇭 Asia/Bangkok\n"

            "📨 Discord = CONNECTED"
        )


    else:

        print(
            "⚠️ Discord ยังไม่เชื่อมต่อ"
        )

        print(
            "กรุณาใส่ "
            "DISCORD_WEBHOOK_URL"
        )


    # ========================================================
    # START PAPER TRAINER
    # ========================================================

    threading.Thread(

        target=paper_trainer.loop,

        daemon=True

    ).start()


    # ========================================================
    # START MASTER SIGNAL WORKERS
    # ========================================================

    for pair in PAIRS_S2:

        threading.Thread(

            target=pair_worker_s2,

            args=(pair,),

            daemon=True,

        ).start()


        time.sleep(0.3)


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":

    print("=" * 60)

    print(
        "⚡ MAIN ENGINE: "
        "V15 ADAPTIVE PAPER TRAINING"
    )

    print("=" * 60)


    # --------------------------------------------------------
    # REAL ORDER SAFETY CHECK
    # --------------------------------------------------------

    REAL_ORDER_ENABLED = False


    print(
        "🔒 REAL ORDER = OFF"
    )


    start_script_2_system()


    # --------------------------------------------------------
    # KEEP PROCESS ALIVE
    # --------------------------------------------------------

    while True:

        time.sleep(3600)
