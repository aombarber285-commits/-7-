import os
import urllib.request
import json
import time
from datetime import datetime
import threading
import sqlite3
import tempfile
import numpy as np


# ================================================================
# S+ V10.1 + V15
# CLOSED-CANDLE / LIQUIDITY / TIMING UPDATE
#
# PATCH CONTINUOUS UPDATE
# 1) Latest CLOSED candle ใช้ [-1]
# 2) Liquidity reference ไม่รวม current candle
# 3) Signal action ต้องเป็น CALL/PUT ก่อน journal
# 4) Exact T+1 target tracking
# 5) Duplicate target protection
# 6) Signal snapshot protection
# 7) MFE / MAE / underlying-candle result
# 8) V15 baseline แยกจาก expectancy และยังไม่ auto-adaptive
# 9) FIX: Confirmation transition ใช้แท่งที่เพิ่งปิดเป็น confirmation
#    และ Target T+1 คือแท่งที่กำลังก่อตัวถัดจาก confirmation
# 10) FIX: ห้าม reject target เพียงเพราะ target เป็น current forming
#     เนื่องจาก current forming คือ T+1 ที่ถูกต้อง ณ เวลา confirmation
#
# IMPORTANT:
# Forward Test นี้วัด underlying candle directional result
# ไม่ใช่ Option P&L และยังไม่รวม fee/slippage
# ================================================================


# ================================================================
# EXPECTANCY JOURNAL
# ================================================================

class ExpectancyJournalV10:
    def __init__(self, db_path=None):
        if db_path is None:
            import tempfile
            self.db_path = os.path.join(
                tempfile.gettempdir(),
                "v10_smc_journal.db"
            )
        else:
            self.db_path = db_path

        self.lock = threading.Lock()
        self.init_db()

    def _connect(self):
        return sqlite3.connect(
            self.db_path,
            check_same_thread=False,
            timeout=10
        )

    def init_db(self):
        with self.lock:
            conn = self._connect()
            cursor = conn.cursor()

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS journal (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT,
                    pair TEXT,
                    setup_key TEXT,
                    action TEXT,
                    grade TEXT,
                    confidence_score REAL,
                    trap_risk REAL,
                    mtf_15m TEXT,
                    mtf_5m TEXT,
                    target_candle_time INTEGER,
                    signal_candle_time INTEGER,
                    confirmation_candle_time INTEGER,
                    entry_open_price REAL,
                    exit_close_price REAL,
                    mfe REAL,
                    mae REAL,
                    pnl_pct REAL,
                    result TEXT DEFAULT 'PENDING',
                    forward_test_type TEXT DEFAULT
                        'UNDERLYING_CANDLE'
                )
            """)

            existing_columns = {
                row[1]
                for row in cursor.execute(
                    "PRAGMA table_info(journal)"
                ).fetchall()
            }

            migrations = {
                "signal_candle_time":
                    "ALTER TABLE journal ADD COLUMN "
                    "signal_candle_time INTEGER",

                "confirmation_candle_time":
                    "ALTER TABLE journal ADD COLUMN "
                    "confirmation_candle_time INTEGER",

                "forward_test_type":
                    "ALTER TABLE journal ADD COLUMN "
                    "forward_test_type TEXT DEFAULT "
                    "'UNDERLYING_CANDLE'"
            }

            for column, statement in migrations.items():
                if column not in existing_columns:
                    try:
                        cursor.execute(statement)
                    except sqlite3.OperationalError:
                        pass

            conn.commit()
            conn.close()

    def log_signal(
        self,
        pair,
        setup_key,
        action,
        grade,
        confidence_score,
        trap_risk,
        mtf_15m,
        mtf_5m,
        target_candle_time,
        signal_candle_time=None,
        confirmation_candle_time=None
    ):
        if action not in ["CALL", "PUT"]:
            raise ValueError(
                f"Invalid journal action: {action}"
            )

        with self.lock:
            conn = self._connect()
            cursor = conn.cursor()

            cursor.execute("""
                SELECT id
                FROM journal
                WHERE pair = ?
                  AND setup_key = ?
                  AND action = ?
                  AND target_candle_time = ?
                LIMIT 1
            """, (
                pair,
                setup_key,
                action,
                target_candle_time
            ))

            duplicate = cursor.fetchone()

            if duplicate:
                conn.close()
                return None

            cursor.execute("""
                INSERT INTO journal (
                    timestamp,
                    pair,
                    setup_key,
                    action,
                    grade,
                    confidence_score,
                    trap_risk,
                    mtf_15m,
                    mtf_5m,
                    target_candle_time,
                    signal_candle_time,
                    confirmation_candle_time,
                    result,
                    forward_test_type
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                datetime.now().strftime(
                    "%Y-%m-%d %H:%M:%S"
                ),
                pair,
                setup_key,
                action,
                grade,
                confidence_score,
                trap_risk,
                mtf_15m,
                mtf_5m,
                target_candle_time,
                signal_candle_time,
                confirmation_candle_time,
                "FORWARD_TEST",
                "UNDERLYING_CANDLE"
            ))

            signal_id = cursor.lastrowid

            conn.commit()
            conn.close()

            return signal_id

    def update_forward_test_result(
        self,
        signal_id,
        target_kline,
        action
    ):
        if action not in ["CALL", "PUT"]:
            return

        entry_open_price = float(target_kline[1])
        high = float(target_kline[2])
        low = float(target_kline[3])
        exit_close_price = float(target_kline[4])

        if entry_open_price <= 0:
            return

        if action == "CALL":
            mfe = (
                (high - entry_open_price)
                / entry_open_price
            ) * 100

            mae = (
                (entry_open_price - low)
                / entry_open_price
            ) * 100

            pnl_pct = (
                (exit_close_price - entry_open_price)
                / entry_open_price
            ) * 100
        else:
            mfe = (
                (entry_open_price - low)
                / entry_open_price
            ) * 100

            mae = (
                (high - entry_open_price)
                / entry_open_price
            ) * 100

            pnl_pct = (
                (entry_open_price - exit_close_price)
                / entry_open_price
            ) * 100

        if pnl_pct > 0:
            result = "WIN"
        elif pnl_pct < 0:
            result = "LOSS"
        else:
            result = "DRAW"

        with self.lock:
            conn = self._connect()
            cursor = conn.cursor()

            cursor.execute("""
                UPDATE journal
                SET
                    entry_open_price = ?,
                    exit_close_price = ?,
                    mfe = ?,
                    mae = ?,
                    pnl_pct = ?,
                    result = ?
                WHERE id = ?
                  AND result = 'FORWARD_TEST'
            """, (
                entry_open_price,
                exit_close_price,
                mfe,
                mae,
                pnl_pct,
                result,
                signal_id
            ))

            conn.commit()
            conn.close()

    def get_setup_expectancy(self, setup_key):
        with self.lock:
            conn = self._connect()
            cursor = conn.cursor()

            cursor.execute("""
                SELECT result, pnl_pct
                FROM journal
                WHERE setup_key = ?
                  AND result IN ('WIN', 'LOSS')
            """, (setup_key,))

            rows = cursor.fetchall()
            conn.close()

        total_samples = len(rows)

        if total_samples < 30:
            tier = "LEARNING"
        elif total_samples < 50:
            tier = "CAUTION"
        elif total_samples < 100:
            tier = "VALIDATED"
        else:
            tier = "HIGH_CONFIDENCE"

        if total_samples < 30:
            return {
                "tier": tier,
                "status": "FORWARD_TEST_ONLY",
                "samples": total_samples,
                "winrate": 0.0,
                "expectancy": 0.0,
                "avg_win": 0.0,
                "avg_loss": 0.0,
                "profit_factor": 0.0
            }

        wins = [
            float(r[1])
            for r in rows
            if r[0] == "WIN"
            and r[1] is not None
            and float(r[1]) > 0
        ]

        losses = [
            abs(float(r[1]))
            for r in rows
            if r[0] == "LOSS"
            and r[1] is not None
            and float(r[1]) < 0
        ]

        winrate = (
            len(wins) / total_samples
        ) * 100.0

        avg_win = np.mean(wins) if wins else 0.0
        avg_loss = np.mean(losses) if losses else 0.0

        expectancy = (
            (winrate / 100.0) * avg_win
        ) - (
            ((100.0 - winrate) / 100.0)
            * avg_loss
        )

        gross_profit = sum(wins)
        gross_loss = sum(losses)

        if gross_loss > 0:
            profit_factor = (
                gross_profit / gross_loss
            )
        else:
            profit_factor = 0.0

        if tier in [
            "LEARNING",
            "CAUTION"
        ]:
            status = "FORWARD_TEST_ONLY"
        elif (
            winrate >= 55.0
            and expectancy > 0
        ):
            status = "CANDIDATE_ACTIVE"
        else:
            status = "ADAPTIVE_BLOCKED"

        return {
            "tier": tier,
            "status": status,
            "samples": total_samples,
            "winrate": winrate,
            "expectancy": expectancy,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "profit_factor": profit_factor
        }


journal_engine = ExpectancyJournalV10()


# ================================================================
# V15 META-BRAIN
# ================================================================

# FIX V15 DB:
# 1) ใช้ absolute path แทน relative path
# 2) สร้าง directory ก่อนเปิด SQLite
# 3) ถ้าโฟลเดอร์ของไฟล์ .py เขียนไม่ได้ ให้ fallback ไป temp
# 4) ใช้ connection helper + lock เดียวกัน
# 5) เปิด WAL/busy_timeout เพื่อลดปัญหา database locked
#
# สามารถกำหนด path เองได้ด้วย:
#   V15_DB_PATH=/path/to/v15_sniper_journal.db

def _get_script_dir():
    try:
        return os.path.dirname(
            os.path.abspath(__file__)
        )
    except Exception:
        return os.getcwd()


def _is_writable_dir(directory):
    test_file = None

    try:
        os.makedirs(
            directory,
            exist_ok=True
        )

        test_file = os.path.join(
            directory,
            ".v15_write_test.tmp"
        )

        with open(
            test_file,
            "a",
            encoding="utf-8"
        ):
            pass

        os.remove(test_file)
        return True

    except Exception:
        if test_file:
            try:
                if os.path.exists(test_file):
                    os.remove(test_file)
            except Exception:
                pass

        return False


def _resolve_v15_db_path():
    env_path = os.getenv(
        "V15_DB_PATH",
        ""
    ).strip()

    if env_path:
        env_path = os.path.abspath(
            os.path.expanduser(env_path)
        )

        env_dir = os.path.dirname(
            env_path
        ) or os.getcwd()

        if _is_writable_dir(env_dir):
            return env_path

        print(
            "⚠️ V15_DB_PATH ใช้งานไม่ได้ "
            f"({env_dir}) → fallback"
        )

    script_dir = _get_script_dir()
    cwd = os.getcwd()
    temp_dir = tempfile.gettempdir()

    # Prefer existing DB so old V15 data is preserved.
    existing_candidates = [
        os.path.join(
            script_dir,
            "v15_sniper_journal.db"
        ),
        os.path.join(
            cwd,
            "v15_sniper_journal.db"
        ),
        os.path.join(
            temp_dir,
            "v15_sniper_journal.db"
        )
    ]

    for db_path in existing_candidates:
        if (
            os.path.isfile(db_path)
            and
            _is_writable_dir(
                os.path.dirname(db_path)
            )
        ):
            return db_path

    # New DB: prefer the script directory.
    for directory in (
        script_dir,
        cwd,
        temp_dir
    ):
        if _is_writable_dir(directory):
            return os.path.join(
                directory,
                "v15_sniper_journal.db"
            )

    # Last-resort fallback.
    return os.path.join(
        tempfile.gettempdir(),
        "v15_sniper_journal.db"
    )


DB_FILE = _resolve_v15_db_path()

_V15_DB_LOCK = threading.RLock()

print(
    f"🗄️ V15 DB: {DB_FILE}"
)


def _v15_connect():
    """
    Centralized V15 SQLite connection.
    """
    db_dir = os.path.dirname(
        os.path.abspath(DB_FILE)
    )

    os.makedirs(
        db_dir,
        exist_ok=True
    )

    conn = sqlite3.connect(
        DB_FILE,
        check_same_thread=False,
        timeout=30
    )

    conn.execute(
        "PRAGMA busy_timeout = 30000"
    )

    try:
        conn.execute(
            "PRAGMA journal_mode=WAL"
        )
    except Exception:
        # Restricted filesystems may not support WAL.
        pass

    return conn


def initialize_seed_database():
    try:
        with _V15_DB_LOCK:
            conn = _v15_connect()

            try:
                cursor = conn.cursor()

                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS journal (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp TEXT,
                        confidence_score REAL,
                        atr_multiplier REAL,
                        weight_threshold REAL,
                        status TEXT
                    )
                """)

                cursor.execute(
                    "SELECT COUNT(*) FROM journal"
                )

                if cursor.fetchone()[0] == 0:
                    seed_data = [
                        (
                            "2026-08-09 05:00:00",
                            0.65,
                            1.5,
                            75.0,
                            "INITIAL_BASELINE"
                        ),
                        (
                            "2026-08-09 05:01:00",
                            0.70,
                            1.5,
                            75.0,
                            "CALIBRATED"
                        ),
                        (
                            "2026-08-09 05:02:00",
                            0.75,
                            2.0,
                            80.0,
                            "GRADE_A_LOCKED"
                        )
                    ]

                    cursor.executemany("""
                        INSERT INTO journal (
                            timestamp,
                            confidence_score,
                            atr_multiplier,
                            weight_threshold,
                            status
                        )
                        VALUES (?, ?, ?, ?, ?)
                    """, seed_data)

                    conn.commit()

            finally:
                conn.close()

        print(
            "✅ V15 DB initialized successfully"
        )
        return True

    except Exception as e:
        print(
            f"⚠️ V15 DB Init Error: {e}"
        )
        return False


def get_v15_baseline():
    try:
        with _V15_DB_LOCK:
            conn = _v15_connect()

            try:
                cursor = conn.cursor()

                cursor.execute("""
                    SELECT
                        confidence_score,
                        atr_multiplier,
                        weight_threshold,
                        status
                    FROM journal
                    ORDER BY id DESC
                    LIMIT 1
                """)

                row = cursor.fetchone()

            finally:
                conn.close()

        if not row:
            return {
                "confidence_score": 0.75,
                "atr_multiplier": 2.0,
                "weight_threshold": 80.0,
                "status": "DEFAULT"
            }

        return {
            "confidence_score": float(row[0]),
            "atr_multiplier": float(row[1]),
            "weight_threshold": float(row[2]),
            "status": row[3]
        }

    except Exception as e:
        print(
            f"⚠️ V15 DB Error: {e}"
        )

        return {
            "confidence_score": 0.75,
            "atr_multiplier": 2.0,
            "weight_threshold": 80.0,
            "status": "FALLBACK"
        }


def update_v15_baseline(
    confidence_score,
    atr_multiplier,
    weight_threshold,
    status="BATCH_CALIBRATED"
):
    """
    V15 calibration storage only.

    IMPORTANT:
    This function is intentionally NOT called after every WIN/LOSS.
    It is a controlled write point for a future batch calibration engine.
    """

    confidence_score = min(
        max(float(confidence_score), 0.60),
        0.90
    )

    atr_multiplier = min(
        max(float(atr_multiplier), 1.0),
        3.0
    )

    weight_threshold = min(
        max(float(weight_threshold), 70.0),
        95.0
    )

    try:
        with _V15_DB_LOCK:
            conn = _v15_connect()

            try:
                cursor = conn.cursor()

                cursor.execute("""
                    INSERT INTO journal (
                        timestamp,
                        confidence_score,
                        atr_multiplier,
                        weight_threshold,
                        status
                    )
                    VALUES (?, ?, ?, ?, ?)
                """, (
                    datetime.now().strftime(
                        "%Y-%m-%d %H:%M:%S"
                    ),
                    confidence_score,
                    atr_multiplier,
                    weight_threshold,
                    status
                ))

                conn.commit()

            finally:
                conn.close()

        return True

    except Exception as e:
        print(
            f"⚠️ V15 DB Write Error: {e}"
        )
        return False


initialize_seed_database()


# ================================================================
# MARKET CONFIG
# ================================================================

PAIRS_S2 = [
    {"name": "BTC/USDT", "symbol": "BTCUSDT"},
    {"name": "ETH/USDT", "symbol": "ETHUSDT"},
    {"name": "SOL/USDT", "symbol": "SOLUSDT"},
    {"name": "XRP/USDT", "symbol": "XRPUSDT"},
    {"name": "BNB/USDT", "symbol": "BNBUSDT"},
    {"name": "DOGE/USDT", "symbol": "DOGEUSDT"}
]


WEBHOOK_URL_S2 = os.getenv(
    "DISCORD_WEBHOOK_URL",
    ""
)


pair_states = {}

for p in PAIRS_S2:
    pair_states[p["symbol"]] = {
        "is_busy": False,
        "last_signal_time": 0,
        "pending_pre_alert": None,
        "last_forward_test_target": None,
        "lock": threading.Lock()
    }


# ================================================================
# DISCORD
# ================================================================

def send_discord_signal_s2(message):
    if not WEBHOOK_URL_S2:
        return False

    try:
        data = json.dumps({
            "content": message
        }).encode("utf-8")

        req = urllib.request.Request(
            WEBHOOK_URL_S2,
            data=data,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "Mozilla/5.0"
            },
            method="POST"
        )

        with urllib.request.urlopen(
            req,
            timeout=5
        ) as response:
            return response.status == 204

    except Exception as e:
        print(
            f"⚠️ Discord Alert Error: {e}"
        )
        return False


# ================================================================
# BINANCE DATA
# ================================================================

def fetch_klines(
    symbol,
    interval,
    limit=60,
    retries=3
):
    """
    Robust Binance klines fetch.

    FIX:
    - retry on transient SSL/connection failures
    - short exponential backoff
    - rotate Binance API endpoints
    - return [] only after all attempts fail
    """

    endpoints = (
        "https://api.binance.com/api/v3/klines",
        "https://api1.binance.com/api/v3/klines",
        "https://api2.binance.com/api/v3/klines",
        "https://api3.binance.com/api/v3/klines"
    )

    last_error = None

    for attempt in range(
        max(1, int(retries))
    ):
        endpoint = endpoints[
            attempt % len(endpoints)
        ]

        url = (
            f"{endpoint}"
            f"?symbol={symbol}"
            f"&interval={interval}"
            f"&limit={limit}"
        )

        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 "
                        "(Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 "
                        "Chrome/120 Safari/537.36"
                    ),
                    "Accept": "application/json",
                    "Connection": "close"
                },
                method="GET"
            )

            with urllib.request.urlopen(
                req,
                timeout=8
            ) as response:

                raw = response.read().decode(
                    "utf-8"
                )

                data = json.loads(raw)

                if not isinstance(data, list):
                    raise ValueError(
                        "Binance returned non-list data"
                    )

                result = [
                    [
                        int(k[0]),
                        float(k[1]),
                        float(k[2]),
                        float(k[3]),
                        float(k[4]),
                        float(k[5])
                    ]
                    for k in data
                ]

                if result:
                    return result

                raise ValueError(
                    "Binance returned empty klines"
                )

        except Exception as e:
            last_error = e

            if attempt < max(1, int(retries)) - 1:
                delay = 0.75 * (
                    2 ** attempt
                )

                time.sleep(delay)

    print(
        f"⚠️ Binance Fetch Failed "
        f"{symbol} {interval} "
        f"after {max(1, int(retries))} attempts: "
        f"{last_error}"
    )

    return []


# ================================================================
# CLOSED CANDLE UTILITIES
# ================================================================

def get_closed_klines(
    symbol,
    interval,
    limit=60
):
    """
    Binance returns the current/forming candle.
    The final candle is removed.
    """

    raw = fetch_klines(
        symbol,
        interval,
        limit + 1
    )

    if len(raw) < 2:
        return []

    return raw[:-1]


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
        "time": k[0],
        "o": o,
        "h": h,
        "l": l,
        "c": c,
        "vol": vol,
        "total_len": total_len,
        "body": body,
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
        "is_red": c < o
    }


# ================================================================
# ATR
# ================================================================

def calculate_atr(
    parsed_klines,
    period=14
):
    if len(parsed_klines) < period + 1:
        return 0.0001

    tr_list = []

    for i in range(1, len(parsed_klines)):
        h = parsed_klines[i]["h"]
        l = parsed_klines[i]["l"]
        prev_c = parsed_klines[i - 1]["c"]

        tr_list.append(
            max(
                h - l,
                abs(h - prev_c),
                abs(l - prev_c)
            )
        )

    return np.mean(
        tr_list[-period:]
    )


# ================================================================
# MARKET REGIME
# ================================================================

def detect_market_regime(
    parsed_klines,
    atr
):
    if len(parsed_klines) < 15:
        return "RANGE"

    recent = parsed_klines[-15:]

    closes = [
        p["c"]
        for p in recent
    ]

    avg_volatility = np.mean([
        p["total_len"]
        for p in recent
    ])

    range_span = (
        max(p["h"] for p in recent)
        -
        min(p["l"] for p in recent)
    )

    linear_slope = (
        closes[-1] -
        closes[0]
    )

    if avg_volatility > (
        atr * 1.8
    ):
        return "HIGH_VOLATILITY"

    if range_span < (
        atr * 2.2
    ):
        return "RANGE"

    if linear_slope > (
        atr * 1.2
    ):
        return "TREND_UP"

    if linear_slope < (
        -atr * 1.2
    ):
        return "TREND_DOWN"

    return "LOW_VOLATILITY"


# ================================================================
# SMC STRUCTURE
# ================================================================

def detect_smc_structure(
    parsed_klines
):
    if len(parsed_klines) < 20:
        return "RANGE"

    recent = parsed_klines[-15:]

    highs = [
        p["h"]
        for p in recent
    ]

    lows = [
        p["l"]
        for p in recent
    ]

    curr = recent[-1]

    prev_high = max(
        highs[:-2]
    )

    prev_low = min(
        lows[:-2]
    )

    if (
        curr["c"] > prev_high
        and
        recent[-3]["c"]
        <
        recent[-5]["c"]
    ):
        return "BULLISH_CHoCH"

    elif (
        curr["c"] < prev_low
        and
        recent[-3]["c"]
        >
        recent[-5]["c"]
    ):
        return "BEARISH_CHoCH"

    if (
        curr["c"] > prev_high
        and
        curr["body_pct"] > 55
    ):
        return "STRONG_BULL_BOS"

    elif (
        curr["c"] < prev_low
        and
        curr["body_pct"] > 55
    ):
        return "STRONG_BEAR_BOS"

    elif curr["h"] > prev_high:
        return "WEAK_BULL_BOS"

    elif curr["l"] < prev_low:
        return "WEAK_BEAR_BOS"

    return "RANGE"


# ================================================================
# LIQUIDITY SWEEP / REJECTION
# ================================================================

def eval_liquidity_rejection_quality(
    parsed_klines
):
    if len(parsed_klines) < 25:
        return {
            "valid_eql_call": False,
            "valid_eqh_put": False,
            "raw_sweep_eql": False,
            "raw_sweep_eqh": False
        }

    # Latest CLOSED candle = [-1]
    curr = parsed_klines[-1]

    # Current candle is excluded from liquidity reference
    reference = parsed_klines[-25:-1]

    res_level = max(
        p["h"]
        for p in reference
    )

    sup_level = min(
        p["l"]
        for p in reference
    )

    eql_swept = (
        curr["l"] < sup_level
    )

    eql_closed_inside = (
        curr["c"] > sup_level
    )

    eql_strong_wick = (
        curr["lower_wick_pct"] >= 35.0
    )

    eqh_swept = (
        curr["h"] > res_level
    )

    eqh_closed_inside = (
        curr["c"] < res_level
    )

    eqh_strong_wick = (
        curr["upper_wick_pct"] >= 35.0
    )

    return {
        "valid_eql_call": (
            eql_swept
            and eql_closed_inside
            and eql_strong_wick
        ),

        "valid_eqh_put": (
            eqh_swept
            and eqh_closed_inside
            and eqh_strong_wick
        ),

        "raw_sweep_eql": eql_swept,
        "raw_sweep_eqh": eqh_swept
    }


# ================================================================
# REAL MTF SMC
# ================================================================

def eval_real_mtf_smc(symbol):
    k15_raw = get_closed_klines(
        symbol,
        "15m",
        25
    )

    k5_raw = get_closed_klines(
        symbol,
        "5m",
        25
    )

    if (
        len(k15_raw) < 20
        or
        len(k5_raw) < 20
    ):
        return "RANGE", "RANGE"

    k15 = [
        parse_kline(k)
        for k in k15_raw
    ]

    k5 = [
        parse_kline(k)
        for k in k5_raw
    ]

    s15 = detect_smc_structure(k15)
    s5 = detect_smc_structure(k5)

    return s15, s5


# ================================================================
# V10.1 DECISION ENGINE
# ================================================================

def analyze_candle_vision_v10_1(symbol):
    raw_1m = get_closed_klines(
        symbol,
        "1m",
        60
    )

    if len(raw_1m) < 40:
        return (
            "C",
            "NO TRADE",
            "UNKNOWN",
            0,
            100,
            {},
            ["ข้อมูลไม่พอ"],
            "RANGE",
            "RANGE",
            "RANGE",
            0
        )

    parsed = [
        parse_kline(k)
        for k in raw_1m[-40:]
    ]

    # Latest CLOSED candle
    curr = parsed[-1]

    # T+1 relative to latest CLOSED candle
    target_candle_time = (
        curr["time"] + 60_000
    )

    atr = calculate_atr(
        parsed,
        14
    )

    avg_vol = np.mean([
        p["vol"]
        for p in parsed[-15:-1]
    ])

    regime = detect_market_regime(
        parsed,
        atr
    )

    rejection_map = (
        eval_liquidity_rejection_quality(
            parsed
        )
    )

    mtf_15m, mtf_5m = (
        eval_real_mtf_smc(symbol)
    )

    v15 = get_v15_baseline()

    v15_weight_threshold = (
        v15["weight_threshold"]
    )

    structure_score = 0
    liquidity_score = 0
    mtf_score = 0
    volume_score = 0
    regime_score = 0

    trap_penalty = 0
    trap_flags = []

    proposed_action = "NONE"
    base_setup_id = "UNKNOWN"

    # ------------------------------------------------------------
    # LIQUIDITY
    # ------------------------------------------------------------

    if rejection_map["valid_eql_call"]:
        base_setup_id = (
            "REVERSAL_EQL_CONFIRMED"
        )
        proposed_action = "CALL"
        liquidity_score += 25

    elif rejection_map["valid_eqh_put"]:
        base_setup_id = (
            "REVERSAL_EQH_CONFIRMED"
        )
        proposed_action = "PUT"
        liquidity_score += 25

    elif (
        rejection_map["raw_sweep_eql"]
        or
        rejection_map["raw_sweep_eqh"]
    ):
        trap_penalty += 40

        trap_flags.append(
            "Weak Sweep Rejection "
            "(Missing Wick/Close Inside)"
        )

    # ------------------------------------------------------------
    # 1m SMC
    # ------------------------------------------------------------

    smc_1m = detect_smc_structure(
        parsed
    )

    if proposed_action == "NONE":
        if (
            "BULL" in smc_1m
            and
            curr["is_green"]
        ):
            base_setup_id = (
                f"SMC_{smc_1m}"
            )
            proposed_action = "CALL"
            structure_score += 30

        elif (
            "BEAR" in smc_1m
            and
            curr["is_red"]
        ):
            base_setup_id = (
                f"SMC_{smc_1m}"
            )
            proposed_action = "PUT"
            structure_score += 30

    else:
        if (
            (
                "BULL" in smc_1m
                and
                proposed_action == "CALL"
            )
            or
            (
                "BEAR" in smc_1m
                and
                proposed_action == "PUT"
            )
        ):
            structure_score += 35

    # ------------------------------------------------------------
    # MTF ALIGNMENT
    # ------------------------------------------------------------

    if (
        (
            proposed_action == "CALL"
            and
            "BULL" in mtf_15m
        )
        or
        (
            proposed_action == "PUT"
            and
            "BEAR" in mtf_15m
        )
    ):
        mtf_score += 12

    if (
        (
            proposed_action == "CALL"
            and
            "BULL" in mtf_5m
        )
        or
        (
            proposed_action == "PUT"
            and
            "BEAR" in mtf_5m
        )
    ):
        mtf_score += 8

    # ------------------------------------------------------------
    # VOLUME
    # ------------------------------------------------------------

    vol_ratio = (
        curr["vol"]
        /
        (avg_vol + 1e-9)
    )

    if vol_ratio >= 1.3:
        volume_score += 10

    # ------------------------------------------------------------
    # REGIME
    # ------------------------------------------------------------

    if regime in [
        "TREND_UP",
        "TREND_DOWN",
        "RANGE"
    ]:
        regime_score += 10

    # ------------------------------------------------------------
    # TOTAL SCORE
    # ------------------------------------------------------------

    total_confidence = (
        structure_score
        +
        liquidity_score
        +
        mtf_score
        +
        volume_score
        +
        regime_score
    )

    setup_key = (
        f"{symbol}|"
        f"{base_setup_id}|"
        f"{regime}|"
        f"{proposed_action}"
    )

    # ------------------------------------------------------------
    # HIGH VOLATILITY FILTER
    # ------------------------------------------------------------

    if regime == "HIGH_VOLATILITY":
        if (
            total_confidence < 90.0
            or
            mtf_score < 20
        ):
            trap_penalty += 60

            trap_flags.append(
                "High Volatility Filter Failed "
                "(Score < 90 or MTF Disaligned)"
            )

    # ------------------------------------------------------------
    # EXPECTANCY
    # ------------------------------------------------------------

    stats = (
        journal_engine
        .get_setup_expectancy(
            setup_key
        )
    )

    if (
        stats["tier"] not in [
            "LEARNING",
            "CAUTION"
        ]
        and
        stats["status"] == "ADAPTIVE_BLOCKED"
    ):
        trap_penalty += 80

        trap_flags.append(
            f"Adaptive Blocked -> "
            f"WinRate: {stats['winrate']:.1f}%, "
            f"EV: {stats['expectancy']:.3f}"
        )

    # ------------------------------------------------------------
    # V15 THRESHOLD
    # ------------------------------------------------------------

    if (
        total_confidence
        <
        v15_weight_threshold
    ):
        trap_flags.append(
            f"V15 Threshold Not Met "
            f"({total_confidence:.1f} < "
            f"{v15_weight_threshold:.1f})"
        )

    # ------------------------------------------------------------
    # TRAP RISK
    # ------------------------------------------------------------

    trap_risk = min(
        trap_penalty,
        100
    )

    # ------------------------------------------------------------
    # GRADE / DECISION
    # ------------------------------------------------------------

    grade = "C"
    decision = "NO TRADE"

    if stats["tier"] in [
        "LEARNING",
        "CAUTION"
    ]:
        trap_flags.append(
            f"{stats['tier']} "
            f"(Forward-Test Only)"
        )

        if (
            proposed_action in [
                "CALL",
                "PUT"
            ]
            and
            trap_risk <= 20
            and
            total_confidence >= 80.0
            and
            total_confidence >= v15_weight_threshold
        ):
            grade = "B"
            decision = proposed_action
        else:
            grade = "C"
            decision = "NO TRADE"

    else:
        if (
            trap_risk <= 10
            and
            total_confidence >= 90.0
            and
            mtf_score >= 12
            and
            total_confidence >= v15_weight_threshold
        ):
            grade = "A+"
            decision = proposed_action

        elif (
            trap_risk <= 15
            and
            total_confidence >= 85.0
            and
            total_confidence >= v15_weight_threshold
        ):
            grade = "A"
            decision = proposed_action

        elif (
            trap_risk <= 30
            and
            total_confidence >= 75.0
        ):
            grade = "B"
            decision = "WAIT"

        else:
            grade = "C"
            decision = "NO TRADE"

    # Explicit action protection
    if decision not in [
        "CALL",
        "PUT",
        "WAIT",
        "NO TRADE"
    ]:
        decision = "NO TRADE"

    score_breakdown = {
        "Grade": grade,
        "Total Confidence":
            f"{total_confidence:.1f}/100",
        "Scores":
            f"Struct:{structure_score} "
            f"Liq:{liquidity_score} "
            f"MTF:{mtf_score} "
            f"Vol:{volume_score} "
            f"Reg:{regime_score}",
        "Setup Key":
            setup_key,
        "Sample Tier":
            f"{stats['tier']} "
            f"(Samples: {stats['samples']})",
        "Expectancy":
            f"{stats['expectancy']:.3f}%",
        "Winrate":
            f"{stats['winrate']:.1f}%",
        "Profit Factor":
            f"{stats['profit_factor']:.2f}",
        "V15 Threshold":
            f"{v15_weight_threshold:.1f}",
        "V15 Status":
            v15["status"],
        "SMC 1m":
            smc_1m,
        "MTF 5m":
            mtf_5m,
        "MTF 15m":
            mtf_15m
    }

    return (
        grade,
        decision,
        setup_key,
        total_confidence,
        trap_risk,
        score_breakdown,
        trap_flags,
        regime,
        mtf_15m,
        mtf_5m,
        target_candle_time
    )


# Backward-compatible name
analyze_candle_vision_v9 = (
    analyze_candle_vision_v10_1
)


# ================================================================
# EXACT T+1 FORWARD TEST
# ================================================================

def exact_forward_test_tracker(
    symbol,
    signal_id,
    action,
    target_candle_time
):
    deadline = (
        target_candle_time
        +
        180_000
    )

    while True:
        time.sleep(3)

        klines = fetch_klines(
            symbol,
            "1m",
            5
        )

        now_ms = int(
            time.time() * 1000
        )

        if not klines:
            if now_ms > deadline:
                print(
                    f"⚠️ Forward Test Timeout "
                    f"for Signal ID: {signal_id}"
                )
                return
            continue

        for kline in klines:
            candle_time = kline[0]

            if candle_time != target_candle_time:
                continue

            # Target candle must be fully CLOSED
            if (
                now_ms
                <
                target_candle_time + 60_000
            ):
                continue

            journal_engine.update_forward_test_result(
                signal_id,
                kline,
                action
            )

            print(
                f"📌 [EXACT T+1 FORWARD TEST CLOSED] "
                f"Signal ID: {signal_id} | "
                f"Open: {kline[1]} -> "
                f"Close: {kline[4]} | "
                f"Action: {action}"
            )

            return

        if now_ms > deadline:
            print(
                f"⚠️ Forward Test Timeout "
                f"for Signal ID: {signal_id}"
            )
            return


# ================================================================
# SNAPSHOT HELPERS
# ================================================================

def create_signal_snapshot(
    decision_result,
    signal_candle_time,
    target_time
):
    (
        grade,
        decision,
        setup_key,
        score,
        trap_risk,
        breakdown,
        trap_flags,
        regime,
        mtf_15m,
        mtf_5m,
        analyzed_target_time
    ) = decision_result

    return {
        "grade": grade,
        "decision": decision,
        "setup_key": setup_key,
        "score": score,
        "trap_risk": trap_risk,
        "breakdown": dict(breakdown),
        "trap_flags": list(trap_flags),
        "regime": regime,
        "mtf_15m": mtf_15m,
        "mtf_5m": mtf_5m,
        "signal_candle_time": signal_candle_time,
        "target_time": target_time,
        "analyzed_target_time": analyzed_target_time,
        "created_at": int(
            time.time() * 1000
        )
    }


# ================================================================
# WORKER ENGINE
# ================================================================

def pair_worker_s2(pair):
    symbol = pair["symbol"]
    name = pair["name"]

    state = pair_states[symbol]

    last_candle_time = None

    print(
        f"[{datetime.now().strftime('%H:%M:%S')}] "
        f"👁️ [S+ V10.1 + V15] "
        f"Active: {name}"
    )

    while True:
        try:
            # Raw 1m is used only to detect candle transition
            klines = fetch_klines(
                symbol,
                "1m",
                5
            )

            if not klines:
                time.sleep(2)
                continue

            current_forming_candle_time = (
                klines[-1][0]
            )

            # ====================================================
            # NEW 1m CANDLE DETECTED
            # The previous forming candle is now CLOSED.
            # It is used as the confirmation candle.
            # The current forming candle is the exact T+1 target.
            # ====================================================

            if (
                last_candle_time is not None
                and
                last_candle_time
                !=
                current_forming_candle_time
            ):
                pending = (
                    state["pending_pre_alert"]
                )

                if pending:
                    (
                        grade,
                        decision,
                        setup_key,
                        score,
                        trap_risk,
                        breakdown,
                        trap_flags,
                        regime,
                        mtf_15m,
                        mtf_5m,
                        recalculated_target
                    ) = analyze_candle_vision_v10_1(
                        symbol
                    )

                    pending_type = (
                        pending["type"]
                    )

                    # The candle that just closed is:
                    # current_forming_candle_time - 60 seconds
                    confirmation_candle_time = (
                        current_forming_candle_time
                        -
                        60_000
                    )

                    signal_candle_time = (
                        pending["snapshot"][
                            "signal_candle_time"
                        ]
                    )

                    # ====================================================
                    # CONFIRMATION
                    # ====================================================

                    confirmed = (
                        decision == pending_type
                        and
                        decision in [
                            "CALL",
                            "PUT"
                        ]
                        and
                        grade in [
                            "A+",
                            "A",
                            "B"
                        ]
                    )

                    if confirmed:
                        snapshot = pending[
                            "snapshot"
                        ]

                        snapshot_grade = (
                            snapshot["grade"]
                        )

                        snapshot_decision = (
                            snapshot["decision"]
                        )

                        snapshot_setup_key = (
                            snapshot["setup_key"]
                        )

                        snapshot_score = (
                            snapshot["score"]
                        )

                        snapshot_trap_risk = (
                            snapshot["trap_risk"]
                        )

                        snapshot_mtf_15m = (
                            snapshot["mtf_15m"]
                        )

                        snapshot_mtf_5m = (
                            snapshot["mtf_5m"]
                        )

                        # ====================================================
                        # EXACT TARGET TIMING FIX
                        #
                        # At confirmation:
                        # - confirmation candle = previous candle, now closed
                        # - current_forming_candle_time = next candle
                        # - therefore current_forming_candle_time IS the exact
                        #   T+1 target after confirmation.
                        #
                        # Do NOT reject it for being current-forming.
                        # The forward tracker waits until it closes.
                        # ====================================================

                        target_time = (
                            confirmation_candle_time
                            +
                            60_000
                        )

                        if target_time != current_forming_candle_time:
                            print(
                                f"⚠️ Target timing mismatch "
                                f"for {name}: "
                                f"expected={current_forming_candle_time}, "
                                f"calculated={target_time}"
                            )
                            confirmed = False

                        # Target must be after confirmation.
                        if target_time <= confirmation_candle_time:
                            print(
                                f"⚠️ Invalid T+1 target "
                                f"for {name}: "
                                f"target={target_time}, "
                                f"confirmation={confirmation_candle_time}"
                            )
                            confirmed = False

                        if confirmed:
                            # Duplicate protection
                            if (
                                state[
                                    "last_forward_test_target"
                                ]
                                ==
                                target_time
                            ):
                                print(
                                    f"⚠️ Duplicate target blocked: "
                                    f"{name} {target_time}"
                                )
                            else:
                                sig_id = (
                                    journal_engine
                                    .log_signal(
                                        name,
                                        snapshot_setup_key,
                                        snapshot_decision,
                                        snapshot_grade,
                                        snapshot_score,
                                        snapshot_trap_risk,
                                        snapshot_mtf_15m,
                                        snapshot_mtf_5m,
                                        target_time,
                                        signal_candle_time,
                                        confirmation_candle_time
                                    )
                                )

                                if sig_id is not None:
                                    threading.Thread(
                                        target=(
                                            exact_forward_test_tracker
                                        ),
                                        args=(
                                            symbol,
                                            sig_id,
                                            snapshot_decision,
                                            target_time
                                        ),
                                        daemon=True
                                    ).start()

                                    state[
                                        "last_forward_test_target"
                                    ] = target_time

                                    details_str = (
                                        "\n".join(
                                            [
                                                f"• {k}: {v}"
                                                for k, v in snapshot[
                                                    "breakdown"
                                                ].items()
                                            ]
                                        )
                                    )

                                    entry_time = (
                                        datetime.now()
                                        .strftime(
                                            "%H:%M:%S"
                                        )
                                    )

                                    title = (
                                        "🧪 "
                                        "[S+ V10.1 + V15 "
                                        "FORWARD-TEST "
                                        f"CONFIRMED — "
                                        f"GRADE "
                                        f"{snapshot_grade}]"
                                    )

                                    confirm_msg = (
                                        f"{title} — {name}\n"
                                        f"🎯 **คำสั่ง:** "
                                        f"`{snapshot_decision}`\n"
                                        f"🎯 **Key:** "
                                        f"`{snapshot_setup_key}`\n"
                                        f"🌐 **Regime:** "
                                        f"`{snapshot['regime']}`\n"
                                        f"🕯️ **Signal Candle:** "
                                        f"`{signal_candle_time}`\n"
                                        f"🕯️ **Confirmation Candle:** "
                                        f"`{confirmation_candle_time}`\n"
                                        f"⏰ **Target T+1:** "
                                        f"`{target_time}`\n"
                                        f"💯 **Snapshot Confidence:** "
                                        f"`{snapshot_score:.1f}/100`\n"
                                        f"🛡️ **Snapshot Trap Risk:** "
                                        f"`{snapshot_trap_risk}/100`\n\n"
                                        f"📊 **Snapshot Breakdown:**\n"
                                        f"{details_str}\n\n"
                                        f"⏱️ **Entry:** "
                                        f"OPEN ของ Target Candle\n"
                                        f"📈 **Result Type:** "
                                        f"`UNDERLYING_CANDLE`\n"
                                        f"🕐 **Confirm Time:** "
                                        f"`{entry_time}`"
                                    )

                                    print(
                                        "\n"
                                        "--------------------------------------------------\n"
                                        f"{confirm_msg}"
                                        "\n"
                                        "--------------------------------------------------"
                                    )

                                    send_discord_signal_s2(
                                        confirm_msg
                                    )

                                    state[
                                        "last_signal_time"
                                    ] = time.time()

                    # ====================================================
                    # REJECTED
                    # ====================================================

                    if not confirmed:
                        trap_str = (
                            "\n".join(
                                [
                                    f"• {f}"
                                    for f in (
                                        trap_flags
                                        or
                                        [
                                            "Confirmation "
                                            "conditions not satisfied"
                                        ]
                                    )
                                ]
                            )
                        )

                        no_trade_msg = (
                            f"🚨 **[S+ V10.1 DECISION: "
                            f"{decision} / GRADE {grade}] "
                            f"— {name}**\n"
                            f"❌ **ไม่ผ่าน Confirmation**\n"
                            f"{trap_str}\n\n"
                            f"📊 **Recheck Confidence:** "
                            f"`{score:.1f}/100`\n"
                            f"🔴 **Recheck Trap Risk:** "
                            f"`{trap_risk}/100`"
                        )

                        print(
                            "\n"
                            "--------------------------------------------------\n"
                            f"{no_trade_msg}"
                            "\n"
                            "--------------------------------------------------"
                        )

                        send_discord_signal_s2(
                            no_trade_msg
                        )

                    # Clear pending state
                    with state["lock"]:
                        state["is_busy"] = False
                        state["pending_pre_alert"] = None

            # Store current forming candle
            last_candle_time = (
                current_forming_candle_time
            )

            # ====================================================
            # NEW SIGNAL SCAN
            # ====================================================

            if (
                not state["is_busy"]
                and
                (
                    time.time()
                    -
                    state["last_signal_time"]
                ) >= 60
            ):
                decision_result = (
                    analyze_candle_vision_v10_1(
                        symbol
                    )
                )

                (
                    grade,
                    decision,
                    setup_key,
                    score,
                    trap_risk,
                    breakdown,
                    trap_flags,
                    regime,
                    mtf_15m,
                    mtf_5m,
                    target_time
                ) = decision_result

                # The analysis is based on latest CLOSED candle.
                # Its immediate next candle is the pre-alert target.
                if (
                    target_time
                    <=
                    current_forming_candle_time
                ):
                    target_time = (
                        current_forming_candle_time
                        +
                        60_000
                    )

                # Only CALL/PUT can become a pending signal.
                if (
                    decision in [
                        "CALL",
                        "PUT"
                    ]
                    and
                    grade in [
                        "A+",
                        "A",
                        "B"
                    ]
                ):
                    signal_candle_time = (
                        current_forming_candle_time
                        -
                        60_000
                    )

                    snapshot = create_signal_snapshot(
                        decision_result,
                        signal_candle_time,
                        target_time
                    )

                    # Make snapshot target authoritative
                    snapshot[
                        "target_time"
                    ] = target_time

                    with state["lock"]:
                        if (
                            state[
                                "pending_pre_alert"
                            ]
                            is None
                        ):
                            state["is_busy"] = True

                            state[
                                "pending_pre_alert"
                            ] = {
                                "type": decision,
                                "signal_candle_time":
                                    signal_candle_time,
                                "target_time":
                                    target_time,
                                "snapshot":
                                    snapshot
                            }

                            mode_text = (
                                "FORWARD-TEST"
                            )

                            pre_msg = (
                                f"⚠️ **[PRE-ALERT — "
                                f"S+ V10.1 GRADE "
                                f"{grade}]**\n"
                                f"📌 **คู่:** `{name}`\n"
                                f"🎯 **Direction:** "
                                f"`{decision}`\n"
                                f"🧠 **Mode:** "
                                f"`{mode_text}`\n"
                                f"🎯 **Setup:** "
                                f"`{setup_key}`\n"
                                f"🌐 **Regime:** "
                                f"`{regime}`\n"
                                f"💯 **Confidence:** "
                                f"`{score:.1f}/100`\n"
                                f"🛡️ **Trap Risk:** "
                                f"`{trap_risk}/100`\n"
                                f"🕯️ **Signal Candle:** "
                                f"`{signal_candle_time}`\n"
                                f"⏰ **Initial Target:** "
                                f"`{target_time}`\n"
                                f"⏱️ **รอแท่งถัดไป "
                                f"เพื่อยืนยัน...**"
                            )

                            print(
                                "\n"
                                "--------------------------------------------------\n"
                                f"{pre_msg}"
                                "\n"
                                "--------------------------------------------------"
                            )

                            send_discord_signal_s2(
                                pre_msg
                            )

        except Exception as e:
            print(
                f"⚠️ Worker Error ({name}): {e}"
            )

        time.sleep(1.5)


# ================================================================
# START SYSTEM
# ================================================================

def start_script_2_system():
    print(
        "🚀 [System 2] "
        "เริ่มทำงาน S+ V10.1 + V15 "
        "Closed-Candle Architecture..."
    )

    send_discord_signal_s2(
        "🏆 **[SYSTEM V10.1 + V15 ONLINE]**\n"
        "Closed Candle + MTF SMC + "
        "Liquidity + Expectancy + "
        "Exact T+1 Forward Test + "
        "Signal Snapshot พร้อมทำงาน!\n"
        "⚠️ Forward-Test Only — "
        "ไม่ใช่ Option P&L"
    )

    for pair in PAIRS_S2:
        threading.Thread(
            target=pair_worker_s2,
            args=(pair,),
            daemon=True
        ).start()

        time.sleep(0.3)


# ================================================================
# MAIN
# ================================================================

if __name__ == "__main__":
    print(
        "=================================================="
    )

    print(
        "⚡ MAIN ENGINE: "
        "STARTING V10.1 + V15 "
        "CLOSED-CANDLE ARCHITECTURE"
    )

    print(
        "=================================================="
    )

    threading.Thread(
        target=start_script_2_system,
        daemon=True
    ).start()

    while True:
        time.sleep(3600)
