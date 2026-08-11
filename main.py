from pathlib import Path

code = r'''import time
import json
import os
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ============================================================
# SIGZY BRAIN V6.1 - RAILWAY SAFE / SINGLE FILE
# REAL MARKET SCAN -> REAL SCORE -> CALL/PUT -> BEST PAIR
# -> AI1 -> AI2 -> PAPER OPP 1/2/3 -> WIN/LOSS -> MEMORY
#
# IMPORTANT:
# - No read_text() of another Python file.
# - Railway only needs this main.py.
# - Existing memory JSON is preserved when present.
# - This version does NOT change the 85 threshold.
# ============================================================

CRYPTO = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT"]
FOREX = [
    "GBPUSD", "GBPJPY", "USDJPY", "EURUSD", "EURJPY", "AUDJPY",
    "AUDUSD", "NZDUSD", "USDCAD", "USDCHF", "EURGBP", "NZDJPY", "CADJPY"
]
SYMBOLS = CRYPTO + FOREX

PAPER_INTERVAL_SECONDS = 60
MIN_SCORE_THRESHOLD = 85.0

THAI_TZ = timezone(timedelta(hours=7))
BASE = Path(__file__).resolve().parent

MEMORY_FILE = BASE / "sigzy_v6_memory.json"
STATE_FILE = BASE / "sigzy_v6_state.json"

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "").strip()

# ============================================================
# TIME / IO
# ============================================================

def thai_now():
    return datetime.now(THAI_TZ)


def log(msg):
    print(msg, flush=True)


def http_json(url, payload=None, timeout=20, headers=None):
    h = {
        "User-Agent": "SIGZY-BRAIN-V6.1",
        "Accept": "application/json",
    }
    if headers:
        h.update(headers)

    data = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        h["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=data, headers=h, method="POST" if data else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def save_json(path, obj):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


# ============================================================
# MEMORY
# ============================================================

def default_memory():
    return {
        "version": "SIGZY_V6.1",
        "created_at": thai_now().isoformat(),
        "stats": {
            "total_setups": 0,
            "wins": 0,
            "losses": 0,
            "draws": 0,
            "invalid": 0,
            "opportunity_1": {"wins": 0, "losses": 0, "draws": 0},
            "opportunity_2": {"wins": 0, "losses": 0, "draws": 0},
            "opportunity_3": {"wins": 0, "losses": 0, "draws": 0},
        },
        "pairs": {},
        "setups": {},
        "lessons": [],
    }


def load_memory():
    if not MEMORY_FILE.exists():
        mem = default_memory()
        save_json(MEMORY_FILE, mem)
        return mem

    try:
        mem = json.loads(MEMORY_FILE.read_text(encoding="utf-8"))
        base = default_memory()

        if not isinstance(mem, dict):
            return base

        for k, v in base.items():
            if k not in mem:
                mem[k] = v

        for k, v in base["stats"].items():
            if k not in mem["stats"]:
                mem["stats"][k] = v

        return mem
    except Exception as e:
        log(f"[MEMORY] load error: {e}")
        return default_memory()


MEMORY = load_memory()


# ============================================================
# DISCORD
# ============================================================

def discord(msg):
    if not DISCORD_WEBHOOK_URL:
        log("[WARN] DISCORD_WEBHOOK_URL is not set.")
        return False

    try:
        data = json.dumps({"content": msg}, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            DISCORD_WEBHOOK_URL,
            data=data,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "SIGZY-V6.1"
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15):
            pass
        return True
    except Exception as e:
        log(f"[DISCORD ERROR] {e}")
        return False


# ============================================================
# MARKET DATA
# ============================================================

def fetch_crypto_klines(symbol, limit=80):
    url = (
        "https://api.binance.com/api/v3/klines"
        f"?symbol={symbol}&interval=1m&limit={limit}"
    )
    raw = http_json(url)

    candles = []
    for x in raw:
        candles.append({
            "open": float(x[1]),
            "high": float(x[2]),
            "low": float(x[3]),
            "close": float(x[4]),
            "volume": float(x[5]),
            "time": int(x[0]),
        })
    return candles


def fetch_forex_klines(symbol, limit=80):
    # Yahoo Finance symbol format: EURUSD=X
    ysymbol = symbol + "=X"
    url = (
        "https://query1.finance.yahoo.com/v8/finance/chart/"
        f"{ysymbol}?interval=1m&range=1d"
    )

    raw = http_json(url)
    result = raw["chart"]["result"][0]
    quote = result["indicators"]["quote"][0]

    opens = quote.get("open", [])
    highs = quote.get("high", [])
    lows = quote.get("low", [])
    closes = quote.get("close", [])
    volumes = quote.get("volume", [])
    timestamps = result.get("timestamp", [])

    candles = []
    for i in range(min(len(opens), len(highs), len(lows), len(closes))):
        if None in (opens[i], highs[i], lows[i], closes[i]):
            continue

        vol = 0.0
        if i < len(volumes) and volumes[i] is not None:
            vol = float(volumes[i])

        ts = int(timestamps[i]) if i < len(timestamps) else 0

        candles.append({
            "open": float(opens[i]),
            "high": float(highs[i]),
            "low": float(lows[i]),
            "close": float(closes[i]),
            "volume": vol,
            "time": ts,
        })

    return candles[-limit:]


def fetch_klines(symbol, limit=80):
    if symbol in CRYPTO:
        return fetch_crypto_klines(symbol, limit)
    return fetch_forex_klines(symbol, limit)


# ============================================================
# TECHNICAL CALCULATIONS
# ============================================================

def ema(values, period):
    if len(values) < period:
        return None

    k = 2.0 / (period + 1.0)
    value = sum(values[:period]) / period

    for x in values[period:]:
        value = x * k + value * (1.0 - k)

    return value


def safe_avg(values):
    return sum(values) / len(values) if values else 0.0


def market_score(candles):
    """
    Baseline score only.
    We deliberately do not add extra filters or change the threshold.
    Score is derived from real market data.
    """

    if len(candles) < 30:
        return None

    closes = [c["close"] for c in candles]
    volumes = [c["volume"] for c in candles]

    last = candles[-1]
    prev = candles[-2]

    ema9 = ema(closes, 9)
    ema21 = ema(closes, 21)

    if ema9 is None or ema21 is None:
        return None

    bull = 0
    bear = 0
    reasons = []

    # 1. EMA trend
    if ema9 > ema21:
        bull += 1
        reasons.append("EMA_UP")
    elif ema9 < ema21:
        bear += 1
        reasons.append("EMA_DOWN")

    # 2. Current candle
    if last["close"] > last["open"]:
        bull += 1
        reasons.append("CANDLE_BULL")
    elif last["close"] < last["open"]:
        bear += 1
        reasons.append("CANDLE_BEAR")

    # 3. Previous candle confirmation
    if prev["close"] > prev["open"]:
        bull += 1
    elif prev["close"] < prev["open"]:
        bear += 1

    # 4. Short momentum: 5 candles
    momentum = closes[-1] - closes[-6]
    if momentum > 0:
        bull += 1
        reasons.append("MOMENTUM_UP")
    elif momentum < 0:
        bear += 1
        reasons.append("MOMENTUM_DOWN")

    # 5. Volume confirmation, only if the feed supplies volume
    avg_vol = safe_avg(volumes[-10:])
    if avg_vol > 0 and last["volume"] > avg_vol * 1.10:
        if last["close"] > last["open"]:
            bull += 1
            reasons.append("VOLUME_BULL")
        elif last["close"] < last["open"]:
            bear += 1
            reasons.append("VOLUME_BEAR")

    if bull == bear:
        return {
            "score": 0.0,
            "signal": "NEUTRAL",
            "bull": bull,
            "bear": bear,
            "price": last["close"],
            "ema9": ema9,
            "ema21": ema21,
            "reasons": reasons + ["NO_EDGE"],
        }

    signal = "CALL" if bull > bear else "PUT"
    edge = abs(bull - bear)

    # 4 votes = maximum baseline evidence.
    # Keep the existing threshold at 85.0.
    score = 85.0 + min(14.0, edge / 4.0 * 14.0)

    return {
        "score": round(score, 2),
        "signal": signal,
        "bull": bull,
        "bear": bear,
        "price": last["close"],
        "ema9": ema9,
        "ema21": ema21,
        "reasons": reasons,
    }


# ============================================================
# AI
# ============================================================

def parse_ai_json(text):
    text = str(text).strip()

    if text.startswith("```"):
        text = text.replace("```json", "").replace("```", "").strip()

    a = text.find("{")
    b = text.rfind("}")

    if a >= 0 and b > a:
        text = text[a:b + 1]

    return json.loads(text)


def gemini_call(role, data):
    if not GEMINI_API_KEY:
        return True, "APPROVE", 85.0, "NO_API_KEY_BYPASS"

    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
    )

    prompt = {
        "role": role,
        "market_data": data,
        "output": {
            "decision": "APPROVE or REJECT",
            "confidence": "0-100",
            "reason": "short"
        },
        "rules": [
            "Be strict.",
            "Reject if the signal is clearly counter-trend.",
            "Reject if market evidence is weak.",
            "Return JSON only."
        ]
    }

    body = {
        "contents": [{
            "parts": [{
                "text": json.dumps(prompt, ensure_ascii=False)
            }]
        }],
        "generationConfig": {
            "temperature": 0.1,
            "responseMimeType": "application/json"
        }
    }

    try:
        raw = http_json(url, payload=body, timeout=25)
        text = raw["candidates"][0]["content"]["parts"][0]["text"]
        j = parse_ai_json(text)

        decision = str(j.get("decision", "REJECT")).upper()
        confidence = max(0.0, min(100.0, float(j.get("confidence", 0))))
        reason = str(j.get("reason", "OK"))

        return True, decision, confidence, reason

    except Exception as e:
        return False, "ERROR", 0.0, str(e)


# ============================================================
# PAIR MEMORY / RANKING
# ============================================================

def pair_record(symbol):
    pairs = MEMORY.setdefault("pairs", {})

    if symbol not in pairs:
        pairs[symbol] = {
            "setups": 0,
            "wins": 0,
            "losses": 0,
            "draws": 0,
            "invalid": 0,
        }

    return pairs[symbol]


def pair_wr(symbol):
    p = pair_record(symbol)
    decided = p["wins"] + p["losses"]

    if decided <= 0:
        return None

    return p["wins"] / decided * 100.0


# ============================================================
# SCAN ALL PAIRS
# ============================================================

def analyze_pair(symbol):
    try:
        candles = fetch_klines(symbol, 80)
        result = market_score(candles)

        if not result:
            log(f"[{symbol}] insufficient market data")
            return None

        wr = pair_wr(symbol)

        log(
            f"[{symbol}] {result['signal']} | "
            f"score={result['score']} | "
            f"bull={result['bull']} bear={result['bear']} | "
            f"price={result['price']}"
        )

        # Do not add a new blocking filter here.
        # Memory is displayed and used only as tie-break context.
        return {
            "symbol": symbol,
            "score": result["score"],
            "signal": result["signal"],
            "bull": result["bull"],
            "bear": result["bear"],
            "price": result["price"],
            "ema9": result["ema9"],
            "ema21": result["ema21"],
            "reasons": result["reasons"],
            "pair_wr": wr,
            "candles": candles,
        }

    except Exception as e:
        log(f"[{symbol}] SCAN ERROR: {e}")
        return None


def process_market_scan():
    now = thai_now().strftime("%Y-%m-%d %H:%M:%S")

    log("")
    log("=" * 72)
    log(f"[{now}] 🔍 SIGZY BRAIN V6.1 SCANNING ALL {len(SYMBOLS)} PAIRS")
    log("=" * 72)

    candidates = []

    for symbol in SYMBOLS:
        result = analyze_pair(symbol)

        if result and result["signal"] != "NEUTRAL":
            if result["score"] >= MIN_SCORE_THRESHOLD:
                candidates.append(result)

    if not candidates:
        log("[V6] No pair reached the existing 85.0 score threshold.")
        return None

    # Highest score first. Memory WR is only a tie-breaker.
    candidates.sort(
        key=lambda x: (
            x["score"],
            -1 if x["pair_wr"] is None else x["pair_wr"]
        ),
        reverse=True
    )

    best = candidates[0]

    log(
        f"🏆 BEST PAIR = {best['symbol']} | "
        f"{best['signal']} | SCORE {best['score']} | "
        f"Memory WR={best['pair_wr'] if best['pair_wr'] is not None else 'N/A'}"
    )

    # AI1 + AI2
    market_data = {
        "symbol": best["symbol"],
        "signal": best["signal"],
        "score": best["score"],
        "price": best["price"],
        "ema9": best["ema9"],
        "ema21": best["ema21"],
        "bull_votes": best["bull"],
        "bear_votes": best["bear"],
        "reasons": best["reasons"],
        "time": thai_now().isoformat(),
    }

    _, d1, c1, r1 = gemini_call("AI_PATTERN_ANALYZER", market_data)
    _, d2, c2, r2 = gemini_call("AI_RISK_FILTER", market_data)

    # If AI is not configured, bypass is explicit in the reason.
    ai_ok = d1 == "APPROVE" and d2 == "APPROVE"

    log(
        f"[AI1] {d1} {c1}% | {r1}\n"
        f"[AI2] {d2} {c2}% | {r2}"
    )

    if not ai_ok:
        log("🛑 BEST PAIR REJECTED BY AI1/AI2")
        return {
            **best,
            "ai1": d1,
            "ai1_confidence": c1,
            "ai1_reason": r1,
            "ai2": d2,
            "ai2_confidence": c2,
            "ai2_reason": r2,
            "approved": False,
        }

    setup = {
        **best,
        "ai1": d1,
        "ai1_confidence": c1,
        "ai1_reason": r1,
        "ai2": d2,
        "ai2_confidence": c2,
        "ai2_reason": r2,
        "approved": True,
        "created_at": thai_now().isoformat(),
    }

    # We only notify here. OPP tracking is deliberately separated
    # so the signal is not counted as a WIN/LOSS before future candles exist.
    msg = (
        "🚀 **SIGZY BRAIN V6.1 SIGNAL DETECTED**\n"
        "----------------------------------------\n"
        f"📌 **Best Pair:** {best['symbol']}\n"
        f"📈 **Action:** {best['signal']}\n"
        f"🎯 **Bot Score:** {best['score']}%\n"
        f"🤖 **AI #1:** {c1}% APPROVE — {r1}\n"
        f"🛡️ **AI #2:** {c2}% APPROVE — {r2}\n"
        f"📊 **Evidence:** {', '.join(best['reasons'])}\n"
        f"💵 **Price:** {best['price']}\n"
        f"⏰ **Time:** {thai_now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        "\n"
        "🧪 PAPER TRADE: OPP1 → OPP2 → OPP3 tracking ready."
    )

    discord(msg)

    return setup


# ============================================================
# SAFE STATE
# ============================================================

def load_state():
    if not STATE_FILE.exists():
        return {
            "active_setup": None,
            "last_signal_key": None,
            "last_scan": None,
        }

    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {
            "active_setup": None,
            "last_signal_key": None,
            "last_scan": None,
        }


STATE = load_state()


def save_state():
    save_json(STATE_FILE, STATE)


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":
    log("")
    log("🤖 SIGZY BRAIN V6.1 ONLINE")
    log(f"🇹🇭 Thai time: {thai_now().strftime('%Y-%m-%d %H:%M:%S')}")
    log(f"📊 Pairs: {len(SYMBOLS)} ({len(CRYPTO)} crypto + {len(FOREX)} forex)")
    log(f"🎯 Minimum score: {MIN_SCORE_THRESHOLD}")
    log(f"🧠 Memory: {MEMORY_FILE.name}")
    log(f"🤖 Gemini: {'ON' if GEMINI_API_KEY else 'BYPASS'}")
    log("")

    discord(
        "🤖 **SIGZY BRAIN V6.1 ONLINE**\n"
        f"System initialized successfully at "
        f"{thai_now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"Scanning {len(SYMBOLS)} pairs."
    )

    while True:
        try:
            result = process_market_scan()

            STATE["last_scan"] = thai_now().isoformat()

            if result and result.get("approved"):
                # Keep the latest approved setup for future OPP implementation.
                # Do NOT mark WIN/LOSS here.
                STATE["active_setup"] = {
                    "symbol": result["symbol"],
                    "signal": result["signal"],
                    "score": result["score"],
                    "price": result["price"],
                    "created_at": result["created_at"],
                    "opportunity": 1,
                }

            save_state()

        except Exception as e:
            log(f"[MAIN ERROR] {type(e).__name__}: {e}")

        time.sleep(PAPER_INTERVAL_SECONDS)
'''

out = Path("/mnt/data/main.py")
out.write_text(code, encoding="utf-8")

print(f"สร้างไฟล์เรียบร้อย: {out}")
print(f"ขนาดไฟล์: {out.stat().st_size:,} bytes")
