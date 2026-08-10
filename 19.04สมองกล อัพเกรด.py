from pathlib import Path

code = r'''import time
import json
import os
import urllib.request
from datetime import datetime, timedelta

# ============================================================
# SIGZY BRAIN V4.1 - CRYPTO + FOREX
# ORIGINAL BRAIN + EURUSD + GROUP WR 70%
#
# IMPORTANT:
# - ไม่ล้าง Memory เดิม
# - ไม่เปลี่ยนแกนการคิดหลัก
# - EURUSD มี tuning เฉพาะคู่
# - Group WR 70% เป็นตัวกรองแยก
# - Opportunity 1/2/3 คงโครงสร้างเดิม
# ============================================================

# ============================================================
# SYMBOL CONFIGURATION
# ============================================================

CRYPTO_SYMBOLS = [
    "BTCUSDT",
    "ETHUSDT",
    "SOLUSDT",
    "XRPUSDT",
    "BNBUSDT",
]

FOREX_SYMBOLS = [
    "GBPUSD",
    "GBPJPY",
    "USDJPY",
    "EURUSD",
]

SYMBOLS = CRYPTO_SYMBOLS + FOREX_SYMBOLS


# ============================================================
# FOREX SYMBOL MAPPING
# ============================================================

FOREX_YAHOO_SYMBOLS = {
    "GBPUSD": "GBPUSD=X",
    "GBPJPY": "GBPJPY=X",
    "USDJPY": "JPY=X",
    "EURUSD": "EURUSD=X",
}


# ============================================================
# SETTINGS
# ============================================================

PAPER_INTERVAL_SECONDS = 60

# แกนสมองเดิม: ยัง 75
MIN_SCORE_THRESHOLD = 75.0

PREMIUM_SCORE = 88.0

# Group filter ใหม่
GROUP_WR_THRESHOLD = 70.0

MAX_NEW_ALERTS_PER_SCAN = 3
MAX_SAME_SETUP_PER_SCAN = 2
SYMBOL_COOLDOWN_SECONDS = 180

MAX_OPPORTUNITIES = 3

MIN_HISTORY_REQUIRED = 10

MEMORY_FILE = "bot_memory_sigzy_brain_v4_crypto_forex.json"

# ============================================================
# PAIR TUNING
# ไม่เปลี่ยนสมองกลาง
# EURUSD จูนแยกเท่านั้น
# ============================================================

PAIR_TUNING = {
    "DEFAULT": {
        "support_resistance_distance": 0.008,
        "volume_threshold": 1.40,
    },

    "EURUSD": {
        # จูนเฉพาะ EURUSD
        "support_resistance_distance": 0.006,
        "volume_threshold": 1.40,
    },
}

# ============================================================
# DISCORD
# ใส่ Webhook ใหม่ของคุณเอง
# ไม่ควรใช้ Webhook ที่เคยเปิดเผยในแชต
# ============================================================

DISCORD_WEBHOOK_URL = ""


# ============================================================
# DISCORD
# ============================================================

def send_discord_notification(message):
    if not DISCORD_WEBHOOK_URL:
        return

    try:
        payload = json.dumps({
            "content": message
        }).encode("utf-8")

        req = urllib.request.Request(
            DISCORD_WEBHOOK_URL,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "Mozilla/5.0"
            },
            method="POST"
        )

        with urllib.request.urlopen(req, timeout=10):
            pass

    except Exception as e:
        print(f"[DISCORD ERROR] {e}", flush=True)


# ============================================================
# PAIR TUNING HELPER
# ============================================================

def get_pair_tuning(symbol):
    tuning = PAIR_TUNING["DEFAULT"].copy()
    tuning.update(PAIR_TUNING.get(symbol, {}))
    return tuning


# ============================================================
# BINANCE CRYPTO DATA
# ============================================================

def fetch_binance_klines(symbol, limit=200):
    try:
        url = (
            f"https://api.binance.com/api/v3/klines"
            f"?symbol={symbol}"
            f"&interval=1m"
            f"&limit={limit}"
        )

        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0"}
        )

        with urllib.request.urlopen(req, timeout=15) as response:
            data = json.loads(response.read().decode("utf-8"))

        candles = []

        for row in data:
            candles.append({
                "timestamp": int(row[0]),
                "open": float(row[1]),
                "high": float(row[2]),
                "low": float(row[3]),
                "close": float(row[4]),
                "volume": float(row[5]),
            })

        return candles

    except Exception as e:
        print(f"[BINANCE ERROR] {symbol}: {e}", flush=True)
        return None


# ============================================================
# YAHOO FOREX DATA
# ============================================================

def fetch_forex_klines(symbol, limit=200):
    yahoo_symbol = FOREX_YAHOO_SYMBOLS.get(symbol)

    if not yahoo_symbol:
        return None

    try:
        url = (
            "https://query1.finance.yahoo.com/v8/finance/chart/"
            + yahoo_symbol
            + "?interval=1m&range=1d"
        )

        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0"}
        )

        with urllib.request.urlopen(req, timeout=15) as response:
            data = json.loads(response.read().decode("utf-8"))

        result = data["chart"]["result"]

        if not result:
            return None

        result = result[0]
        timestamps = result.get("timestamp", [])

        indicators = result.get("indicators", {})
        quote_list = indicators.get("quote", [])

        if not quote_list:
            return None

        quote = quote_list[0]

        opens = quote.get("open", [])
        highs = quote.get("high", [])
        lows = quote.get("low", [])
        closes = quote.get("close", [])
        volumes = quote.get("volume", [])

        candles = []

        for i in range(len(timestamps)):
            try:
                o = opens[i]
                h = highs[i]
                l = lows[i]
                c = closes[i]

                if o is None or h is None or l is None or c is None:
                    continue

                volume = 0.0

                if i < len(volumes) and volumes[i] is not None:
                    volume = float(volumes[i])

                candles.append({
                    "timestamp": int(timestamps[i]) * 1000,
                    "open": float(o),
                    "high": float(h),
                    "low": float(l),
                    "close": float(c),
                    "volume": volume,
                })

            except Exception:
                continue

        if len(candles) > limit:
            candles = candles[-limit:]

        return candles

    except Exception as e:
        print(f"[FOREX ERROR] {symbol}: {e}", flush=True)
        return None


# ============================================================
# UNIVERSAL DATA FETCHER
# ============================================================

def fetch_market_data(symbol, limit=200):
    if symbol in CRYPTO_SYMBOLS:
        return fetch_binance_klines(symbol, limit)

    if symbol in FOREX_SYMBOLS:
        return fetch_forex_klines(symbol, limit)

    return None


# ============================================================
# MEMORY
# ============================================================

def default_memory():
    return {
        "stats": {
            "total_setups": 0,
            "wins": 0,
            "losses": 0,
            "draws": 0,
            "invalid": 0
        },
        "setups": {}
    }


def save_memory(memory):
    try:
        with open(MEMORY_FILE, "w", encoding="utf-8") as f:
            json.dump(
                memory,
                f,
                indent=2,
                ensure_ascii=False
            )

    except Exception as e:
        print(f"[MEMORY SAVE ERROR] {e}", flush=True)


def init_memory():
    if os.path.exists(MEMORY_FILE):
        try:
            with open(MEMORY_FILE, "r", encoding="utf-8") as f:
                memory = json.load(f)

            base = default_memory()

            if "stats" not in memory:
                memory["stats"] = base["stats"]

            if "setups" not in memory:
                memory["setups"] = {}

            print(
                "🧠 โหลด Memory เดิมสำเร็จ — ไม่รีเซ็ตคลังสมอง",
                flush=True
            )

            return memory

        except Exception as e:
            print(f"[MEMORY LOAD ERROR] {e}", flush=True)

    memory = default_memory()
    save_memory(memory)

    return memory


# ============================================================
# HISTORICAL WIN RATE
# ============================================================

def get_setup_win_rate(memory, symbol, action, setup):
    key = f"{symbol}_{action}_{setup}"

    setup_stats = memory["setups"].get(key, {})

    wins = setup_stats.get("wins", 0)
    losses = setup_stats.get("losses", 0)

    total = wins + losses

    if total < MIN_HISTORY_REQUIRED:
        return None

    return (wins / total) * 100.0


# ============================================================
# GROUP WIN RATE
# ใช้ข้อมูลของคู่ + action + setup เดียวกัน
# ไม่ปนคู่
# ============================================================

def get_group_win_rate(memory, symbol, action, setup):
    key = f"{symbol}_{action}_{setup}"
    s = memory["setups"].get(key)

    if not s:
        return None

    wins = s.get("wins", 0)
    losses = s.get("losses", 0)

    decided = wins + losses

    if decided < MIN_HISTORY_REQUIRED:
        return None

    return (wins / decided) * 100.0


def passes_group_filter(memory, symbol, action, setup):
    wr = get_group_win_rate(
        memory,
        symbol,
        action,
        setup
    )

    # คู่/Setup ที่ยังไม่มีข้อมูล 10 ครั้ง
    # ยังไม่ถูกตัดทิ้ง เพื่อให้สามารถเรียนรู้ได้
    if wr is None:
        return True

    return wr >= GROUP_WR_THRESHOLD


# ============================================================
# RECORD RESULT
# ============================================================

def record_trade_result(memory, trade, result, opportunity):
    stats = memory["stats"]

    stats["total_setups"] = stats.get("total_setups", 0) + 1

    if result == "WIN":
        stats["wins"] = stats.get("wins", 0) + 1

    elif result == "LOSS":
        stats["losses"] = stats.get("losses", 0) + 1

    elif result == "DRAW":
        stats["draws"] = stats.get("draws", 0) + 1

    elif result == "INVALID":
        stats["invalid"] = stats.get("invalid", 0) + 1

    key = (
        f"{trade['symbol']}_"
        f"{trade['action']}_"
        f"{trade['setup']}"
    )

    if key not in memory["setups"]:
        memory["setups"][key] = {
            "symbol": trade["symbol"],
            "action": trade["action"],
            "setup": trade["setup"],
            "total": 0,
            "wins": 0,
            "losses": 0,
            "draws": 0,
            "invalid": 0,
            "opportunity_1": {
                "wins": 0,
                "losses": 0
            },
            "opportunity_2": {
                "wins": 0,
                "losses": 0
            },
            "opportunity_3": {
                "wins": 0,
                "losses": 0
            }
        }

    setup_stats = memory["setups"][key]

    setup_stats["total"] = setup_stats.get("total", 0) + 1

    if result == "WIN":
        setup_stats["wins"] += 1

    elif result == "LOSS":
        setup_stats["losses"] += 1

    elif result == "DRAW":
        setup_stats["draws"] += 1

    elif result == "INVALID":
        setup_stats["invalid"] += 1

    if opportunity in [1, 2, 3]:
        opp_key = f"opportunity_{opportunity}"

        if result == "WIN":
            setup_stats[opp_key]["wins"] += 1

        elif result == "LOSS":
            setup_stats[opp_key]["losses"] += 1

    wins = setup_stats.get("wins", 0)
    losses = setup_stats.get("losses", 0)

    decided = wins + losses

    real_wr = None

    if decided > 0:
        real_wr = (wins / decided) * 100.0

    save_memory(memory)

    action_text = (
        "ซื้อขึ้น 🟢"
        if trade["action"] == "CALL"
        else "ซื้อลง 🔴"
    )

    total_wins = stats.get("wins", 0)
    total_draws = stats.get("draws", 0)
    total_losses = stats.get("losses", 0)

    if real_wr is None:
        wr_text = "ยังไม่มีสถิติ"
    else:
        wr_text = f"{real_wr:.1f}% (จาก {decided} ครั้ง)"

    msg = (
        f"📊 ผลลัพธ์ {trade['symbol']}\n"
        f"ผล: {result} ({action_text})\n"
        f"Setup: {trade['setup']}\n"
        f"Opportunity: {opportunity}/3\n"
        f"Win Rate Setup: {wr_text}\n"
        f"รวมระบบ: "
        f"ชนะ {total_wins} | "
        f"เสมอ {total_draws} | "
        f"แพ้ {total_losses}"
    )

    print(f"\n{msg}", flush=True)

    send_discord_notification(msg)


# ============================================================
# EMA
# ============================================================

def calculate_ema(closes, period):
    if not closes:
        return 0.0

    period = min(period, len(closes))

    multiplier = 2.0 / (period + 1.0)

    ema = closes[0]

    for price in closes[1:]:
        ema = (
            price * multiplier
            +
            ema * (1.0 - multiplier)
        )

    return ema


# ============================================================
# SIGZY ANALYSIS
# แกนสมองเดิม
# ============================================================

def evaluate_sigzy(candles, memory, symbol):
    if len(candles) < 150:
        return {
            "action": "WAIT",
            "score": 0.0,
            "setup": "NEUTRAL",
            "real_wr": None,
            "group_wr": None,
            "reason": "INSUFFICIENT_DATA"
        }

    # --------------------------------------------------------
    # CLOSED CANDLE ONLY
    # --------------------------------------------------------

    closed = candles[:-1]

    if len(closed) < 100:
        return {
            "action": "WAIT",
            "score": 0.0,
            "setup": "NEUTRAL",
            "real_wr": None,
            "group_wr": None
        }

    curr = closed[-1]
    prev = closed[-2]

    closes = [x["close"] for x in closed]

    ema50 = calculate_ema(closes, 50)
    ema200 = calculate_ema(closes, 200)

    # --------------------------------------------------------
    # TREND
    # --------------------------------------------------------

    if curr["close"] > ema50 and ema50 >= ema200:
        trend = "BULL"

    elif curr["close"] < ema50 and ema50 <= ema200:
        trend = "BEAR"

    else:
        trend = "RANGE"

    # --------------------------------------------------------
    # SUPPORT / RESISTANCE
    # --------------------------------------------------------

    zone_data = closed[-101:-1]

    resistance = max(x["high"] for x in zone_data)
    support = min(x["low"] for x in zone_data)

    price = curr["close"]

    support_dist = (
        abs(price - support)
        /
        max(abs(support), 0.00000001)
    )

    resistance_dist = (
        abs(price - resistance)
        /
        max(abs(resistance), 0.00000001)
    )

    # --------------------------------------------------------
    # PAIR TUNING
    # Default เหมือนเดิม
    # EURUSD จูนแยก
    # --------------------------------------------------------

    tuning = get_pair_tuning(symbol)

    sr_distance = tuning["support_resistance_distance"]

    near_support = support_dist <= sr_distance
    near_resistance = resistance_dist <= sr_distance

    # --------------------------------------------------------
    # VOLUME
    # --------------------------------------------------------

    volumes = [
        x["volume"]
        for x in closed[-16:-1]
    ]

    valid_volumes = [
        v for v in volumes if v > 0
    ]

    if valid_volumes:
        avg_vol = (
            sum(valid_volumes)
            /
            len(valid_volumes)
        )

        if curr["volume"] > 0:
            vol_ratio = (
                curr["volume"]
                /
                max(avg_vol, 0.00000001)
            )
        else:
            vol_ratio = 1.0
    else:
        vol_ratio = 1.0

    volume_threshold = tuning["volume_threshold"]

    high_volume = vol_ratio >= volume_threshold

    # --------------------------------------------------------
    # CANDLE
    # --------------------------------------------------------

    body = abs(
        curr["close"] -
        curr["open"]
    )

    candle_range = max(
        curr["high"] -
        curr["low"],
        0.00000001
    )

    upper_wick = (
        curr["high"] -
        max(curr["open"], curr["close"])
    )

    lower_wick = (
        min(curr["open"], curr["close"]) -
        curr["low"]
    )

    bullish = curr["close"] > curr["open"]
    bearish = curr["close"] < curr["open"]

    bull_pin = (
        bullish
        and
        lower_wick >= max(
            body * 2.0,
            candle_range * 0.35
        )
    )

    bear_pin = (
        bearish
        and
        upper_wick >= max(
            body * 2.0,
            candle_range * 0.35
        )
    )

    bull_eng = (
        prev["close"] < prev["open"]
        and
        bullish
        and
        curr["open"] <= prev["close"]
        and
        curr["close"] >= prev["open"]
    )

    bear_eng = (
        prev["close"] > prev["open"]
        and
        bearish
        and
        curr["open"] >= prev["close"]
        and
        curr["close"] <= prev["open"]
    )

    # ========================================================
    # SCORE — โครงสร้างเดิม
    # ========================================================

    call_struct = (
        25 if trend == "BULL"
        else 15 if trend == "RANGE"
        else 5
    )

    put_struct = (
        25 if trend == "BEAR"
        else 15 if trend == "RANGE"
        else 5
    )

    call_level = (
        20 if near_support
        else 5 if support_dist <= 0.015
        else 0
    )

    put_level = (
        20 if near_resistance
        else 5 if resistance_dist <= 0.015
        else 0
    )

    call_vol = (
        15
        if high_volume and bullish
        else 5
        if high_volume
        else 8
    )

    put_vol = (
        15
        if high_volume and bearish
        else 5
        if high_volume
        else 8
    )

    call_candle = (
        20
        if bull_pin or bull_eng
        else 10
        if bullish
        else 0
    )

    put_candle = (
        20
        if bear_pin or bear_eng
        else 10
        if bearish
        else 0
    )

    call_setup = (
        "SUPPORT_REVERSAL_CALL"
        if near_support
        else
        "MOMENTUM_CALL"
    )

    put_setup = (
        "RESISTANCE_REVERSAL_PUT"
        if near_resistance
        else
        "MOMENTUM_PUT"
    )

    # --------------------------------------------------------
    # MEMORY
    # --------------------------------------------------------

    call_wr = get_setup_win_rate(
        memory,
        symbol,
        "CALL",
        call_setup
    )

    put_wr = get_setup_win_rate(
        memory,
        symbol,
        "PUT",
        put_setup
    )

    if call_wr is None:
        call_hist = 10
        real_call_wr = None
    else:
        if call_wr >= 70:
            call_hist = 20
        elif call_wr >= 55:
            call_hist = 15
        elif call_wr < 40:
            call_hist = 0
        else:
            call_hist = 5

        real_call_wr = call_wr

    if put_wr is None:
        put_hist = 10
        real_put_wr = None
    else:
        if put_wr >= 70:
            put_hist = 20
        elif put_wr >= 55:
            put_hist = 15
        elif put_wr < 40:
            put_hist = 0
        else:
            put_hist = 5

        real_put_wr = put_wr

    # --------------------------------------------------------
    # TOTAL
    # --------------------------------------------------------

    call_total = (
        call_struct
        +
        call_level
        +
        call_vol
        +
        call_candle
        +
        call_hist
    )

    put_total = (
        put_struct
        +
        put_level
        +
        put_vol
        +
        put_candle
        +
        put_hist
    )

    action = "WAIT"
    final_score = 0
    best_setup = "NEUTRAL"
    real_wr = None
    group_wr = None

    if (
        call_total >= MIN_SCORE_THRESHOLD
        and
        call_total > put_total
    ):
        action = "CALL"
        final_score = call_total
        best_setup = call_setup
        real_wr = real_call_wr
        group_wr = call_wr

    elif (
        put_total >= MIN_SCORE_THRESHOLD
        and
        put_total > call_total
    ):
        action = "PUT"
        final_score = put_total
        best_setup = put_setup
        real_wr = real_put_wr
        group_wr = put_wr

    return {
        "action": action,
        "score": final_score,
        "setup": best_setup,
        "real_wr": real_wr,
        "group_wr": group_wr
    }


# ============================================================
# MARKET STATUS
# ============================================================

def market_name(symbol):
    names = {
        "BTCUSDT": "Bitcoin",
        "ETHUSDT": "Ethereum",
        "SOLUSDT": "Solana",
        "XRPUSDT": "XRP",
        "BNBUSDT": "BNB",

        "GBPUSD": "GBP/USD",
        "GBPJPY": "GBP/JPY",
        "USDJPY": "USD/JPY",
        "EURUSD": "EUR/USD",
    }

    return names.get(symbol, symbol)


# ============================================================
# MAIN LOOP
# ============================================================

def run_bot():
    print("=" * 70)
    print("🤖 SIGZY BRAIN V4.1")
    print("CRYPTO + FOREX | EURUSD + GROUP WR 70%")
    print("🧠 ORIGINAL BRAIN PRESERVED")
    print("=" * 70)

    print("\n📡 ตลาดที่กำลังสแกน:")

    for symbol in SYMBOLS:
        print(
            f"   • {symbol} "
            f"({market_name(symbol)})"
        )

    print()

    memory = init_memory()

    active_trades = {}
    last_alert_time = {}

    while True:
        now_time = datetime.now()
        now_str = now_time.strftime("%Y-%m-%d %H:%M:%S")

        print("\n" + "=" * 70, flush=True)
        print("🧠 SIGZY BRAIN SCAN", flush=True)
        print(f"⏰ {now_str}", flush=True)

        print(
            "📊 Crypto: "
            + ", ".join(CRYPTO_SYMBOLS),
            flush=True
        )

        print(
            "💱 Forex: "
            + ", ".join(FOREX_SYMBOLS),
            flush=True
        )

        print(
            f"🎯 Brain Score: {MIN_SCORE_THRESHOLD}+"
            f" | Group WR: {GROUP_WR_THRESHOLD}%",
            flush=True
        )

        print("=" * 70, flush=True)

        # ====================================================
        # 1. TRACK ACTIVE TRADES
        # ====================================================

        finished = []

        for symbol, trade in list(active_trades.items()):
            candles = fetch_market_data(symbol, 200)

            if not candles:
                continue

            if len(candles) < 3:
                continue

            current = candles[-2]
            current_ts = current["timestamp"]

            if current_ts == trade.get(
                "last_checked_timestamp"
            ):
                continue

            trade["last_checked_timestamp"] = current_ts

            trade["opportunity"] += 1

            is_green = current["close"] > current["open"]
            is_red = current["close"] < current["open"]

            analysis = evaluate_sigzy(
                candles,
                memory,
                symbol
            )

            result = "PENDING"

            if trade["action"] == "CALL":
                if is_green:
                    result = "WIN"
                elif is_red:
                    result = "LOSS"
                else:
                    result = "DRAW"

            else:
                if is_red:
                    result = "WIN"
                elif is_green:
                    result = "LOSS"
                else:
                    result = "DRAW"

            # ------------------------------------------------
            # RESULT
            # ------------------------------------------------

            if result in ["WIN", "LOSS", "DRAW"]:
                record_trade_result(
                    memory,
                    trade,
                    result,
                    trade["opportunity"]
                )

                finished.append(symbol)
                continue

            # ------------------------------------------------
            # OPPOSITE SIGNAL
            # ------------------------------------------------

            opposite = (
                trade["action"] == "CALL"
                and
                analysis["action"] == "PUT"
            ) or (
                trade["action"] == "PUT"
                and
                analysis["action"] == "CALL"
            )

            if (
                opposite
                and
                analysis["score"] >= MIN_SCORE_THRESHOLD
            ):
                record_trade_result(
                    memory,
                    trade,
                    "INVALID",
                    trade["opportunity"]
                )

                finished.append(symbol)
                continue

            # ------------------------------------------------
            # MAX OPPORTUNITY
            # ------------------------------------------------

            if trade["opportunity"] >= MAX_OPPORTUNITIES:
                record_trade_result(
                    memory,
                    trade,
                    "LOSS",
                    trade["opportunity"]
                )

                finished.append(symbol)

        for symbol in finished:
            active_trades.pop(symbol, None)

        # ====================================================
        # 2. SCAN NEW SIGNALS
        # ====================================================

        candidates = []

        for symbol in SYMBOLS:
            if symbol in active_trades:
                continue

            if symbol in last_alert_time:
                elapsed = (
                    now_time -
                    last_alert_time[symbol]
                ).total_seconds()

                if elapsed < SYMBOL_COOLDOWN_SECONDS:
                    continue

            print(
                f"🔎 Scanning {symbol}...",
                flush=True
            )

            candles = fetch_market_data(symbol, 200)

            if not candles:
                print(
                    f"   ⚠️ ไม่มีข้อมูล {symbol}",
                    flush=True
                )
                continue

            if len(candles) < 150:
                print(
                    f"   ⚠️ ข้อมูลไม่พอ "
                    f"{len(candles)}/150",
                    flush=True
                )
                continue

            analysis = evaluate_sigzy(
                candles,
                memory,
                symbol
            )

            closed = candles[:-1]

            if not closed:
                continue

            if (
                analysis["action"] != "WAIT"
                and
                analysis["score"] >= MIN_SCORE_THRESHOLD
            ):
                # ------------------------------------------------
                # GROUP WR 70%
                # ถ้ามีสถิติ >= 10 ครั้งแล้วต่ำกว่า 70%
                # จะไม่ปล่อย Signal กลุ่มนั้น
                # แต่ถ้ายังไม่ถึง 10 ครั้ง ให้เรียนรู้ต่อ
                # ------------------------------------------------

                group_pass = passes_group_filter(
                    memory,
                    symbol,
                    analysis["action"],
                    analysis["setup"]
                )

                group_wr = analysis.get("group_wr")

                if not group_pass:
                    wr_text = (
                        f"{group_wr:.1f}%"
                        if group_wr is not None
                        else "N/A"
                    )

                    print(
                        f"   🚫 GROUP BLOCK "
                        f"WR={wr_text} < "
                        f"{GROUP_WR_THRESHOLD}%",
                        flush=True
                    )

                    continue

                candidates.append({
                    "symbol": symbol,
                    "analysis": analysis,
                    "price": closed[-1]["close"],
                    "timestamp": closed[-1]["timestamp"]
                })

                group_text = (
                    f"{group_wr:.1f}%"
                    if group_wr is not None
                    else "LEARNING"
                )

                print(
                    f"   ✅ CANDIDATE "
                    f"{analysis['action']} "
                    f"Score={analysis['score']} "
                    f"Setup={analysis['setup']} "
                    f"GroupWR={group_text}",
                    flush=True
                )

            else:
                print(
                    f"   ⏸ WAIT "
                    f"Score={analysis['score']}",
                    flush=True
                )

        # ====================================================
        # 3. RANKING
        # ====================================================

        candidates.sort(
            key=lambda x:
                x["analysis"]["score"],
            reverse=True
        )

        selected = []
        setup_count = {}

        for cand in candidates:
            analysis = cand["analysis"]

            setup = analysis["setup"]
            score = analysis["score"]

            is_premium = score >= PREMIUM_SCORE

            if not is_premium:
                if (
                    setup_count.get(setup, 0)
                    >= MAX_SAME_SETUP_PER_SCAN
                ):
                    continue

                if len(selected) >= MAX_NEW_ALERTS_PER_SCAN:
                    break

            selected.append(cand)

            setup_count[setup] = (
                setup_count.get(setup, 0)
                + 1
            )

        # ====================================================
        # 4. SEND SIGNAL
        # ====================================================

        if not selected:
            print(
                "\n❌ รอบนี้ยังไม่มี "
                "Signal ที่ผ่านเกณฑ์",
                flush=True
            )

        for cand in selected:
            symbol = cand["symbol"]
            analysis = cand["analysis"]

            target_time = (
                now_time +
                timedelta(minutes=3)
            )

            trade = {
                "symbol": symbol,
                "action": analysis["action"],
                "price": cand["price"],
                "entry_timestamp": cand["timestamp"],
                "opportunity": 0,
                "last_checked_timestamp": cand["timestamp"],
                "setup": analysis["setup"],
                "real_wr": analysis["real_wr"],
                "group_wr": analysis.get("group_wr")
            }

            active_trades[symbol] = trade
            last_alert_time[symbol] = now_time

            if analysis["action"] == "CALL":
                emoji_action = "ซื้อขึ้น 🟢"
            else:
                emoji_action = "ซื้อลง 🔴"

            if analysis["real_wr"] is not None:
                wr_text = (
                    f"{analysis['real_wr']:.1f}% "
                    f"จากผลจริง"
                )
            else:
                wr_text = "กำลังเก็บสถิติ"

            if analysis.get("group_wr") is not None:
                group_text = (
                    f"{analysis['group_wr']:.1f}%"
                )
            else:
                group_text = "กำลังเรียนรู้"

            alert = (
                f"🔥 SIGZY SIGNAL\n"
                f"ตลาด: {market_name(symbol)}\n"
                f"คู่: {symbol}\n"
                f"เวลา: {now_time.strftime('%H:%M:%S')}\n"
                f"ราคา: {cand['price']}\n"
                f"Action: {emoji_action}\n"
                f"Score: {analysis['score']}/100\n"
                f"Setup: {analysis['setup']}\n"
                f"Historical WR: {wr_text}\n"
                f"Group WR: {group_text}\n"
                f"Group Gate: {GROUP_WR_THRESHOLD}%\n"
                f"เตรียมตัวเวลา: "
                f"{target_time.strftime('%H:%M:%S')}"
            )

            print("\n" + "🚨 " * 10, flush=True)
            print(alert, flush=True)
            print("🚨 " * 10, flush=True)

            send_discord_notification(alert)

        # ====================================================
        # STATUS
        # ====================================================

        print("\n📊 ACTIVE TRADES:", flush=True)

        if active_trades:
            for symbol, trade in active_trades.items():
                group_wr = trade.get("group_wr")

                group_text = (
                    f"{group_wr:.1f}%"
                    if group_wr is not None
                    else "LEARNING"
                )

                print(
                    f"   {symbol} "
                    f"{trade['action']} "
                    f"OPP "
                    f"{trade['opportunity']}/3 "
                    f"Score memory="
                    f"{trade.get('real_wr')} "
                    f"GroupWR={group_text}",
                    flush=True
                )
        else:
            print("   ไม่มี", flush=True)

        print(
            "\n⏳ รอ "
            f"{PAPER_INTERVAL_SECONDS} "
            "วินาที...",
            flush=True
        )

        time.sleep(PAPER_INTERVAL_SECONDS)


# ============================================================
# START
# ============================================================

if __name__ == "__main__":
    try:
        run_bot()

    except KeyboardInterrupt:
        print(
            "\n🛑 หยุด SIGZY BRAIN",
            flush=True
        )

    except Exception as e:
        print(
            "\n[FATAL ERROR]",
            repr(e),
            flush=True
        )
'''

path = Path("/mnt/data/SIGZY_BRAIN_V4_1_CRYPTO_FOREX_EURUSD_70.py")
path.write_text(code, encoding="utf-8")
print(f"สร้างไฟล์เรียบร้อย: {path}")
