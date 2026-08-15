# ============================================================
# TRADEIFY V2.2 CORE PATCH
# Fixes:
# - Strict OTC source handling
# - Data freshness validation
# - Exact 15M -> 5M synchronization
# - Persistent ACTIVE_SERIES
# - Persistent SENT_SIGNALS
# - Rebuild statistics from memory
# - Deterministic signal IDs
# ============================================================

MEMORY_SCHEMA_VERSION = 2

DATA_SOURCE_REAL_OTC = "OTC_REAL"
DATA_SOURCE_YAHOO = "YAHOO_PROXY"


# ---------------- DATA SOURCE ----------------

def get_data_source_mode():
    """
    Returns:
        OTC_REAL     = configured OTC provider
        YAHOO_PROXY  = public Yahoo FX proxy
    """
    if mode_now() == "OTC":
        return DATA_SOURCE_REAL_OTC if OTC_API_URL else DATA_SOURCE_YAHOO
    return DATA_SOURCE_YAHOO


def get_candles(symbol, interval, period="5d", limit=300):
    """
    V2.2:
    Never silently fall back from REAL OTC to Yahoo.

    If OTC_API_URL is configured and OTC fails:
        return []

    This prevents accidental REAL-OTC signals from public proxy data.
    """

    if mode_now() == "OTC" and OTC_API_URL:
        otc = get_otc_candles(symbol, interval, limit)

        if otc:
            return otc

        log(
            f"{symbol} {interval}: "
            f"REAL OTC unavailable -> PAUSE"
        )
        return []

    return get_yahoo_candles(symbol, interval, period)


# ---------------- FRESHNESS ----------------

def candle_age_seconds(candle, interval):
    if candle is None:
        return float("inf")

    close_ts = candle["timestamp"] + interval_seconds(interval)
    return max(0.0, time.time() - close_ts)


def fresh_candle(candle, interval, max_age=None):
    if candle is None:
        return False

    if max_age is None:
        max_age = MAX_DATA_AGE_SECONDS

    if max_age <= 0:
        return True

    return candle_age_seconds(candle, interval) <= max_age


# ---------------- EXACT 15M / 5M SYNC ----------------

def expected_5m_timestamp_for_15m(master_candle):
    """
    A 15M candle ending at T must use the final 5M candle
    inside that 15M block.

    Example:
        15M open 12:45
        15M close 13:00

        Expected 5M:
        12:55 -> 13:00
    """
    return master_candle["timestamp"] + TF15 - TF5


def exact_candle_by_timestamp(candles, timestamp):
    for candle in candles:
        if abs(candle["timestamp"] - timestamp) < 1:
            return candle
    return None


def validate_master_entry_sync(master_candle, entry_candle):
    if master_candle is None or entry_candle is None:
        return False

    expected_ts = expected_5m_timestamp_for_15m(master_candle)

    if abs(entry_candle["timestamp"] - expected_ts) >= 1:
        log(
            "SYNC_MISMATCH "
            f"15M={master_candle['datetime']} "
            f"expected_5M_ts={expected_ts} "
            f"actual_5M={entry_candle['datetime']}"
        )
        return False

    return True


# ---------------- SIGNAL ID ----------------

def make_signal_id(
    symbol,
    mode,
    direction,
    master_candle_ts
):
    return (
        f"{symbol.replace('/', '')}_"
        f"{mode}_"
        f"{direction}_"
        f"{int(master_candle_ts)}"
    )


# ---------------- MEMORY SCHEMA ----------------

def normalize_memory_payload(data):
    """
    V2.2 supports:
        old V2.1 list
        new V2.2 dict
    """

    if isinstance(data, list):
        return {
            "schema_version": 1,
            "history": data,
            "active_series": [],
            "sent_signals": {},
        }

    if isinstance(data, dict):
        return {
            "schema_version": data.get(
                "schema_version",
                MEMORY_SCHEMA_VERSION
            ),
            "history": (
                data.get("history", [])
                if isinstance(data.get("history", []), list)
                else []
            ),
            "active_series": (
                data.get("active_series", [])
                if isinstance(data.get("active_series", []), list)
                else []
            ),
            "sent_signals": (
                data.get("sent_signals", {})
                if isinstance(data.get("sent_signals", {}), dict)
                else {}
            ),
        }

    return {
        "schema_version": MEMORY_SCHEMA_VERSION,
        "history": [],
        "active_series": [],
        "sent_signals": {},
    }


def load_memory():
    global HISTORICAL_MEMORY
    global ACTIVE_SERIES
    global SENT_SIGNALS

    dirname = os.path.dirname(os.path.abspath(MEMORY_FILE))
    os.makedirs(dirname, exist_ok=True)

    if not os.path.exists(MEMORY_FILE):
        HISTORICAL_MEMORY = []
        ACTIVE_SERIES = []
        SENT_SIGNALS = {}
        log(f"Memory new: {MEMORY_FILE}")
        return

    try:
        with open(MEMORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        payload = normalize_memory_payload(data)

        HISTORICAL_MEMORY = payload["history"]
        ACTIVE_SERIES = payload["active_series"]
        SENT_SIGNALS = payload["sent_signals"]

        log(
            f"Memory loaded: "
            f"history={len(HISTORICAL_MEMORY)} "
            f"active={len(ACTIVE_SERIES)} "
            f"signals={len(SENT_SIGNALS)}"
        )

    except Exception as e:
        log(f"Memory load error: {e}")

        try:
            backup = (
                f"{MEMORY_FILE}.corrupt."
                f"{int(time.time())}"
            )
            os.replace(MEMORY_FILE, backup)
            log(f"Corrupt memory backed up: {backup}")
        except Exception:
            pass

        HISTORICAL_MEMORY = []
        ACTIVE_SERIES = []
        SENT_SIGNALS = {}


def save_memory():
    """
    Atomic V2.2 state persistence.
    """

    try:
        dirname = os.path.dirname(
            os.path.abspath(MEMORY_FILE)
        )
        os.makedirs(dirname, exist_ok=True)

        with LOCK:
            payload = {
                "schema_version": MEMORY_SCHEMA_VERSION,
                "history": HISTORICAL_MEMORY,
                "active_series": ACTIVE_SERIES,
                "sent_signals": SENT_SIGNALS,
                "saved_at": thai_text(),
            }

        tmp = MEMORY_FILE + ".tmp"

        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(
                payload,
                f,
                ensure_ascii=False,
                indent=2
            )
            f.flush()
            os.fsync(f.fileno())

        os.replace(tmp, MEMORY_FILE)

    except Exception as e:
        log(f"Memory save error: {e}")


# ---------------- STATS REBUILD ----------------

def rebuild_stats_from_memory():
    global STATS

    with LOCK:
        STATS = {
            "signals": 0,
            "wins": 0,
            "losses": 0,
            "draws": 0,
            "series_completed": 0,
            "series_wins": 0,
            "series_full_loss": 0,
        }

        for record in HISTORICAL_MEMORY:

            if record.get("type") != "SERIES":
                continue

            STATS["signals"] += 1

            STATS["wins"] += int(
                record.get("wins", 0) or 0
            )

            STATS["losses"] += int(
                record.get("losses", 0) or 0
            )

            STATS["draws"] += int(
                record.get("draws", 0) or 0
            )

            STATS["series_completed"] += 1

            if record.get("status") == "SERIES_WIN":
                STATS["series_wins"] += 1

            if record.get("status") == "FULL_LOSS":
                STATS["series_full_loss"] += 1

    log(
        "Stats rebuilt: "
        f"signals={STATS['signals']} "
        f"W={STATS['wins']} "
        f"L={STATS['losses']} "
        f"D={STATS['draws']}"
    )


# ---------------- PERSISTENT SERIES ----------------

def persist_state():
    save_memory()


def create_series(signal):

    signal_id = make_signal_id(
        signal["symbol"],
        signal["mode"],
        signal["decision"],
        signal["master_candle_ts"],
    )

    with LOCK:

        if (
            len(ACTIVE_SERIES)
            >= MAX_ACTIVE_SERIES
        ):
            return None

        if any(
            s["symbol"] == signal["symbol"]
            for s in ACTIVE_SERIES
        ):
            return None

        tracker = {
            "type": "ACTIVE_SERIES",

            "series_id": signal_id,
            "signal_id": signal_id,

            "schema_version":
                MEMORY_SCHEMA_VERSION,

            "symbol": signal["symbol"],
            "mode": signal["mode"],
            "data_source": signal["data_source"],

            "master_direction":
                signal["decision"],

            "setup_strength":
                signal["setup_strength"],

            "entry_context":
                signal["entry_context"],

            "zone_state":
                signal["zone_state"],

            "zone_level":
                signal["zone_level"],

            "master_candle_ts":
                signal["master_candle_ts"],

            "signal_time":
                signal["created_at"],

            "signal_ts":
                signal["signal_ts"],

            "state":
                "WAITING_RESULT",

            "opportunity": 1,

            "next_entry_ts":
                signal["next_open_ts"],

            "next_close_ts":
                signal["next_close_ts"],

            "wins": 0,
            "losses": 0,
            "draws": 0,

            "first_opportunity_result":
                None,

            "processed_5m": [],

            "max_mfe": 0.0,
            "max_mae": 0.0,
        }

        ACTIVE_SERIES.append(tracker)

        STATS["signals"] += 1

    persist_state()

    return tracker


# ---------------- V2.2 SCAN CORE ----------------

def scan_symbol(symbol):

    source = get_data_source_mode()

    c15 = closed_only(
        get_candles(
            symbol,
            "15m",
            "5d",
            300
        ),
        "15m"
    )

    if len(c15) < 80:
        return None

    master_candle = c15[-1]

    if not fresh_candle(
        master_candle,
        "15m"
    ):
        stale_log(
            symbol,
            "15m",
            master_candle
        )
        return None

    if not is_new_closed(
        symbol,
        "15m",
        master_candle
    ):
        return None

    c5 = closed_only(
        get_candles(
            symbol,
            "5m",
            "5d",
            500
        ),
        "5m"
    )

    if len(c5) < 70:
        return None

    expected_5m_ts = (
        expected_5m_timestamp_for_15m(
            master_candle
        )
    )

    latest5 = exact_candle_by_timestamp(
        c5,
        expected_5m_ts
    )

    if latest5 is None:
        log(
            f"{symbol}: expected 5M candle missing "
            f"for 15M {master_candle['datetime']}"
        )
        return None

    if not fresh_candle(
        latest5,
        "5m"
    ):
        stale_log(
            symbol,
            "5m",
            latest5
        )
        return None

    if not validate_master_entry_sync(
        master_candle,
        latest5
    ):
        return None

    master = analyze_15m(c15)

    if not master:
        return None

    timing = analyze_5m(
        c5,
        master["decision"]
    )

    if not timing:
        return None

    context = (
        "5M_CONFIRM"
        if timing["decision"] ==
           master["decision"]
        else
        "5M_PULLBACK"
        if timing["decision"] != "UNKNOWN"
        else
        "5M_UNKNOWN"
    )

    if master["setup_strength"] < 62:
        return None

    mode = mode_now()

    hist = historical_stats(
        setup_signature(
            symbol,
            mode,
            master["decision"],
            master["zone_state"]
        )
    )

    next_open_ts = (
        latest5["timestamp"] + TF5
    )

    next_close_ts = (
        next_open_ts + TF5
    )

    mark_closed(
        symbol,
        "15m",
        master_candle
    )

    mark_closed(
        symbol,
        "5m",
        latest5
    )

    signal_id = make_signal_id(
        symbol,
        mode,
        master["decision"],
        master_candle["timestamp"]
    )

    return {
        "signal_id": signal_id,

        "symbol": symbol,
        "mode": mode,
        "data_source": source,

        "decision":
            master["decision"],

        "setup_strength":
            round(
                master["setup_strength"],
                1
            ),

        "rsi15": master["rsi"],
        "rsi5": timing["rsi"],

        "entry_score":
            timing["score"],

        "entry_context": context,

        "zone_state":
            master["zone_state"],

        "zone_level":
            master["zone_level"],

        "price": master["price"],
        "atr": master["atr"],

        "reasons15":
            master["reasons"],

        "reasons5":
            timing["reasons"],

        "signal_candle15":
            master["candle_time"],

        "master_candle_ts":
            master_candle["timestamp"],

        "last_closed_5m":
            latest5["datetime"],

        "last_closed_5m_ts":
            latest5["timestamp"],

        "signal_ts":
            time.time(),

        "next_open_ts":
            next_open_ts,

        "next_close_ts":
            next_close_ts,

        "history": hist,

        "created_at":
            thai_text(),
    }


# ---------------- TRACKER RESULT ----------------

def record_opportunity(tracker, outcome):

    tracker["processed_5m"].append(
        outcome["candle"]["datetime"]
    )

    tracker["max_mfe"] = max(
        tracker["max_mfe"],
        outcome["mfe"]
    )

    tracker["max_mae"] = max(
        tracker["max_mae"],
        outcome["mae"]
    )

    result = outcome["result"]

    with LOCK:

        if result == "WIN":
            tracker["wins"] += 1
            STATS["wins"] += 1

        elif result == "LOSS":
            tracker["losses"] += 1
            STATS["losses"] += 1

        else:
            tracker["draws"] += 1
            STATS["draws"] += 1

        if tracker["opportunity"] == 1:
            tracker["first_opportunity_result"] = result

    persist_state()

    return result


# ---------------- STARTUP ----------------

def startup_restore():

    load_memory()

    rebuild_stats_from_memory()

    now = time.time()

    with LOCK:

        valid = []

        for tracker in ACTIVE_SERIES:

            if tracker.get(
                "type"
            ) != "ACTIVE_SERIES":
                continue

            # Never restore a malformed tracker.
            required = (
                "series_id",
                "symbol",
                "master_direction",
                "opportunity",
                "next_entry_ts",
                "next_close_ts",
            )

            if not all(
                key in tracker
                for key in required
            ):
                log(
                    f"Discard malformed series: "
                    f"{tracker.get('series_id')}"
                )
                continue

            # Do not allow impossible opportunity numbers.
            if not 1 <= int(
                tracker["opportunity"]
            ) <= MAX_OPPORTUNITIES:
                log(
                    f"Discard invalid opportunity: "
                    f"{tracker.get('series_id')}"
                )
                continue

            valid.append(tracker)

        ACTIVE_SERIES[:] = valid

    persist_state()

    log(
        f"Restored active series: "
        f"{len(ACTIVE_SERIES)}"
    )
