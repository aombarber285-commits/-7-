from pathlib import Path

code = r'''import time
import json
import os
import urllib.request
import urllib.parse
from datetime import datetime
from pathlib import Path

# ============================================================
# SIGZY BRAIN V5.2 - RAILWAY SAFE
# MEMORY + RULE ENGINE + OPTIONAL GEMINI CONFIRMATION
# PAPER TRADE | CRYPTO + FOREX | DISCORD ALERT
#
# IMPORTANT:
# - Railway Variables:
#   DISCORD_WEBHOOK_URL = your Discord webhook
#   GEMINI_API_KEY      = optional Gemini API key
#   GEMINI_MODEL        = optional, default gemini-2.0-flash
# - This version is designed to avoid FileNotFoundError from
#   missing memory files and keeps running if AI/API fails.
# ============================================================

CRYPTO = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT"]

FOREX = [
    "GBPUSD", "GBPJPY", "USDJPY", "EURUSD", "EURJPY", "AUDJPY",
    "AUDUSD", "NZDUSD", "USDCAD", "USDCHF", "EURGBP", "NZDJPY",
    "CADJPY"
]

SYMBOLS = CRYPTO + FOREX
YAHOO = {s: s + "=X" for s in FOREX}

PAPER_INTERVAL_SECONDS = 60
MIN_SCORE_THRESHOLD = 88.0
MAX_NEW_ALERTS_PER_SCAN = 1
SYMBOL_COOLDOWN_SECONDS = 300

PAIR_MIN_HISTORY = 10
SETUP_MIN_HISTORY = 10
PAIR_BLOCK_WR = 45.0
PAIR_WEAK_WR = 55.0
PAIR_FAVOR_WR = 65.0
PAIR_STRONG_WR = 70.0

AI_CONFIDENCE_THRESHOLD = 75.0
AI_TIMEOUT_SECONDS = 12
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash").strip()

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()

BASE = Path(__file__).resolve().parent

# Railway /app should normally exist.
# /tmp is used only as a final fallback for memory persistence.
MEMORY_FILE = BASE / "bot_memory_sigzy_brain_v5.json"
FALLBACK_MEMORY_FILE = Path("/tmp/bot_memory_sigzy_brain_v5.json")


TUNING = {
    "DEFAULT": {"sr": 0.008, "zone": 0.015, "vol": 1.40}
}

for s in CRYPTO:
    TUNING[s] = {
        "sr": {
            "BTCUSDT": 0.006,
            "ETHUSDT": 0.007,
            "SOLUSDT": 0.010,
            "XRPUSDT": 0.010,
            "BNBUSDT": 0.008,
        }[s],
        "zone": {
            "BTCUSDT": 0.012,
            "ETHUSDT": 0.013,
            "SOLUSDT": 0.018,
            "XRPUSDT": 0.018,
            "BNBUSDT": 0.015,
        }[s],
        "vol": {
            "BTCUSDT": 1.35,
            "ETHUSDT": 1.35,
            "SOLUSDT": 1.45,
            "XRPUSDT": 1.45,
            "BNBUSDT": 1.40,
        }[s],
    }

for s in FOREX:
    TUNING[s] = {
        "sr": 0.005 if s in (
            "GBPUSD", "USDJPY", "EURUSD", "AUDUSD",
            "NZDUSD", "USDCAD", "USDCHF", "EURGBP"
        ) else 0.006,
        "zone": 0.012 if s in (
            "GBPUSD", "USDJPY", "EURUSD", "AUDUSD",
            "NZDUSD", "USDCAD", "USDCHF", "EURGBP"
        ) else 0.013,
        "vol": 1.20,
    }


# ============================================================
# HTTP HELPERS
# ============================================================

def http_json(url, payload=None, headers=None, timeout=15):
    headers = headers or {}

    if payload is None:
        req = urllib.request.Request(
            url,
            headers=headers,
            method="GET"
        )
    else:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                **headers
            },
            method="POST"
        )

    with urllib.request.urlopen(req, timeout=timeout) as response:
        raw = response.read().decode("utf-8")
        return json.loads(raw)


# ============================================================
# DISCORD
# ============================================================

def discord(msg):
    if not DISCORD_WEBHOOK_URL:
        print("[DISCORD] Webhook not configured.", flush=True)
        return False

    try:
        data = json.dumps({"content": msg}).encode("utf-8")

        req = urllib.request.Request(
            DISCORD_WEBHOOK_URL,
            data=data,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "SIGZY-BRAIN-V5.2"
            },
            method="POST"
        )

        with urllib.request.urlopen(req, timeout=15):
            pass

        return True

    except Exception as e:
        print("[DISCORD ERROR]", repr(e), flush=True)
        return False


# ============================================================
# GEMINI AI
# ============================================================

def ai_final_check(symbol, action, setup, trend, candles):
    """
    AI is an optional confirmation layer.

    IMPORTANT:
    - Missing API key => AI is skipped, rule engine remains active.
    - AI/network error => AI is skipped, bot does NOT crash.
    - AI rejection => signal is rejected.
    """

    if not GEMINI_API_KEY:
        print(
            "[AI] GEMINI_API_KEY not set -> AI confirmation skipped.",
            flush=True
        )
        return True, "AI_SKIPPED_NO_KEY", 0.0

    recent_candles = []

    for c in candles[-10:]:
        recent_candles.append({
            "open": round(float(c["open"]), 10),
            "high": round(float(c["high"]), 10),
            "low": round(float(c["low"]), 10),
            "close": round(float(c["close"]), 10),
            "volume": round(float(c["volume"]), 4),
        })

    prompt = {
        "task": "Confirm a rule-based short-term CALL/PUT paper-trade signal.",
        "symbol": symbol,
        "proposed_action": action,
        "setup_type": setup,
        "market_trend": trend,
        "candles": recent_candles,
        "rules": [
            "Analyze candle body, upper/lower wick, momentum and recent direction.",
            "Do not invent missing market data.",
            "Return JSON only.",
            "decision must be APPROVE or REJECT.",
            "confidence must be a number from 0 to 100.",
            "reason must be short."
        ]
    }

    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        + urllib.parse.quote(GEMINI_MODEL, safe="")
        + ":generateContent?key="
        + urllib.parse.quote(GEMINI_API_KEY, safe="")
    )

    payload = {
        "contents": [
            {
                "parts": [
                    {
                        "text": json.dumps(prompt, ensure_ascii=False)
                    }
                ]
            }
        ],
        "generationConfig": {
            "response_mime_type": "application/json"
        }
    }

    try:
        result = http_json(
            url,
            payload=payload,
            headers={"User-Agent": "SIGZY-BRAIN-V5.2"},
            timeout=AI_TIMEOUT_SECONDS
        )

        candidates = result.get("candidates", [])
        if not candidates:
            raise RuntimeError("Gemini returned no candidates")

        parts = (
            candidates[0]
            .get("content", {})
            .get("parts", [])
        )

        if not parts:
            raise RuntimeError("Gemini returned no content parts")

        text = parts[0].get("text", "").strip()

        # Remove accidental markdown fences if the model sends them.
        if text.startswith("```"):
            text = text.replace("```json", "", 1)
            text = text.replace("```", "")
            text = text.strip()

        ai = json.loads(text)

        decision = str(ai.get("decision", "REJECT")).upper()
        confidence = float(ai.get("confidence", 0))
        reason = str(ai.get("reason", "No reason"))

        confidence = max(0.0, min(100.0, confidence))

        print(
            f"[AI] {symbol} | {decision} | "
            f"{confidence:.1f}% | {reason}",
            flush=True
        )

        approved = (
            decision == "APPROVE"
            and confidence >= AI_CONFIDENCE_THRESHOLD
        )

        return approved, reason, confidence

    except Exception as e:
        # Do NOT crash Railway because Gemini is unavailable.
        print(
            f"[AI ERROR] {symbol}: {repr(e)} -> "
            f"AI skipped, rule signal continues.",
            flush=True
        )
        return True, "AI_ERROR_RULE_ENGINE_CONTINUES", 0.0


# ============================================================
# MARKET DATA
# ============================================================

def fetch(symbol, limit=200):
    try:
        if symbol in CRYPTO:
            url = (
                "https://api.binance.com/api/v3/klines"
                f"?symbol={symbol}&interval=1m&limit={limit}"
            )

            data = http_json(
                url,
                headers={"User-Agent": "SIGZY-BRAIN-V5.2"},
                timeout=15
            )

            return [
                {
                    "timestamp": int(x[0]),
                    "open": float(x[1]),
                    "high": float(x[2]),
                    "low": float(x[3]),
                    "close": float(x[4]),
                    "volume": float(x[5]),
                }
                for x in data
            ]

        # Yahoo 1m data can be limited/unavailable depending on symbol/time.
        url = (
            f"https://query1.finance.yahoo.com/v8/finance/chart/"
            f"{urllib.parse.quote(YAHOO[symbol], safe='')}"
            "?interval=1m&range=1d"
        )

        data = http_json(
            url,
            headers={"User-Agent": "Mozilla/5.0 SIGZY-BRAIN-V5.2"},
            timeout=15
        )

        results = data.get("chart", {}).get("result")
        if not results:
            return None

        res = results[0]
        ts = res.get("timestamp", [])
        quote = res.get("indicators", {}).get("quote", [{}])[0]

        opens = quote.get("open", [])
        highs = quote.get("high", [])
        lows = quote.get("low", [])
        closes = quote.get("close", [])
        volumes = quote.get("volume", [])

        out = []

        for i, t in enumerate(ts):
            try:
                o = opens[i]
                h = highs[i]
                l = lows[i]
                c = closes[i]

                if None in (o, h, l, c):
                    continue

                v = volumes[i] if i < len(volumes) else 0
                v = v or 0

                out.append({
                    "timestamp": int(t) * 1000,
                    "open": float(o),
                    "high": float(h),
                    "low": float(l),
                    "close": float(c),
                    "volume": float(v),
                })

            except Exception:
                continue

        return out[-limit:]

    except Exception as e:
        print(
            f"[DATA ERROR] {symbol}: {repr(e)}",
            flush=True
        )
        return None


# ============================================================
# MEMORY
# ============================================================

def default_setup(symbol="", action="", setup=""):
    return {
        "symbol": symbol,
        "action": action,
        "setup": setup,
        "total": 0,
        "wins": 0,
        "losses": 0,
        "draws": 0,
        "invalid": 0,
        "opportunity_1": {
            "wins": 0,
            "losses": 0,
            "draws": 0
        },
        "opportunity_2": {
            "wins": 0,
            "losses": 0,
            "draws": 0
        },
        "opportunity_3": {
            "wins": 0,
            "losses": 0,
            "draws": 0
        },
    }


def default_memory():
    return {
        "version": "V5.2",
        "created_at": datetime.now().isoformat(),
        "stats": {
            "total_setups": 0,
            "wins": 0,
            "losses": 0,
            "draws": 0,
            "invalid": 0,
        },
        "score_bands": {
            "PREMIUM_95_100": {
                "signals": 0,
                "wins": 0,
                "losses": 0,
                "draws": 0
            },
            "HIGH_QUALITY_88_94": {
                "signals": 0,
                "wins": 0,
                "losses": 0,
                "draws": 0
            },
            "WATCH_82_87": {
                "signals": 0,
                "wins": 0,
                "losses": 0,
                "draws": 0
            }
        },
        "setups": {}
    }


def choose_memory_file():
    # Prefer /app/project file when writable.
    try:
        MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        test = MEMORY_FILE.with_name(".memory_write_test")
        with open(test, "w", encoding="utf-8") as f:
            f.write("ok")
        try:
            test.unlink()
        except Exception:
            pass
        return MEMORY_FILE
    except Exception:
        print(
            "[MEMORY] /app is not writable -> using /tmp memory.",
            flush=True
        )
        return FALLBACK_MEMORY_FILE


def save(mem):
    global MEMORY_FILE

    try:
        MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)

        tmp = MEMORY_FILE.with_name(
            MEMORY_FILE.name + ".tmp"
        )

        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(
                mem,
                f,
                indent=2,
                ensure_ascii=False
            )

        os.replace(tmp, MEMORY_FILE)

    except Exception as e:
        print(
            "[MEMORY SAVE ERROR]",
            repr(e),
            flush=True
        )

        # Last-resort fallback.
        try:
            with open(
                FALLBACK_MEMORY_FILE,
                "w",
                encoding="utf-8"
            ) as f:
                json.dump(
                    mem,
                    f,
                    indent=2,
                    ensure_ascii=False
                )

            MEMORY_FILE = FALLBACK_MEMORY_FILE

            print(
                f"[MEMORY] Saved fallback: {MEMORY_FILE}",
                flush=True
            )

        except Exception as e2:
            print(
                "[MEMORY FALLBACK ERROR]",
                repr(e2),
                flush=True
            )


def init_memory():
    global MEMORY_FILE

    MEMORY_FILE = choose_memory_file()

    # If current memory doesn't exist, create a fresh one.
    if not MEMORY_FILE.exists():
        mem = default_memory()
        save(mem)
        print(
            f"[MEMORY] Created: {MEMORY_FILE}",
            flush=True
        )
        return mem

    try:
        with open(
            MEMORY_FILE,
            "r",
            encoding="utf-8"
        ) as f:
            m = json.load(f)

        base = default_memory()

        m.setdefault("version", "V5.2")
        m.setdefault("stats", {})
        m.setdefault("score_bands", {})
        m.setdefault("setups", {})

        for k, v in base["stats"].items():
            m["stats"].setdefault(k, v)

        for k, v in base["score_bands"].items():
            m["score_bands"].setdefault(k, v.copy())

        # Fix old typo "losss".
        if "losss" in m["stats"]:
            m["stats"]["losses"] += m["stats"].pop("losss")

        for s in m["setups"].values():
            if not isinstance(s, dict):
                continue

            for k in (
                "total",
                "wins",
                "losses",
                "draws",
                "invalid"
            ):
                s.setdefault(k, 0)

            for i in (1, 2, 3):
                s.setdefault(
                    f"opportunity_{i}",
                    {
                        "wins": 0,
                        "losses": 0,
                        "draws": 0
                    }
                )

        save(m)

        print(
            f"[MEMORY] Loaded: {MEMORY_FILE}",
            flush=True
        )

        return m

    except Exception as e:
        print(
            "[MEMORY LOAD ERROR]",
            repr(e),
            flush=True
        )

        # Do not crash. Start a clean memory.
        m = default_memory()
        save(m)

        print(
            "[MEMORY] Started clean memory after load failure.",
            flush=True
        )

        return m


# ============================================================
# ANALYSIS / LEARNING
# ============================================================

def quality(m, symbol, action, setup):
    s = m["setups"].get(
        f"{symbol}_{action}_{setup}"
    )

    if not s:
        return {
            "wr": None,
            "decided": 0
        }

    wins = int(s.get("wins", 0))
    losses = int(s.get("losses", 0))
    decided = wins + losses

    wr = (
        wins / decided * 100
        if decided
        else None
    )

    return {
        "wr": wr,
        "decided": decided
    }


def pair_stats(m, symbol):
    w = l = d = t = 0

    for k, s in m.get("setups", {}).items():
        if not isinstance(s, dict):
            continue

        if (
            s.get("symbol") == symbol
            or k.startswith(symbol + "_")
        ):
            w += int(s.get("wins", 0))
            l += int(s.get("losses", 0))
            d += int(s.get("draws", 0))
            t += int(s.get("total", 0))

    decided = w + l

    return {
        "wins": w,
        "losses": l,
        "draws": d,
        "total": t,
        "decided": decided,
        "wr": w / decided * 100 if decided else None
    }


def pair_status(s):
    if s["decided"] < PAIR_MIN_HISTORY:
        return "LEARNING"

    if s["wr"] < PAIR_BLOCK_WR:
        return "BLOCK"

    if s["wr"] >= PAIR_STRONG_WR:
        return "STRONG"

    if s["wr"] >= PAIR_FAVOR_WR:
        return "FAVOR"

    if s["wr"] >= PAIR_WEAK_WR:
        return "NORMAL"

    return "WEAK"


def ema(values, n):
    if not values:
        return 0.0

    n = min(n, len(values))
    e = values[0]
    k = 2 / (n + 1)

    for p in values[1:]:
        e = p * k + e * (1 - k)

    return e


def band(score):
    if score >= 95:
        return "PREMIUM_95_100"

    if score >= 88:
        return "HIGH_QUALITY_88_94"

    if score >= 82:
        return "WATCH_82_87"

    return "BELOW_WATCH"


def update_band(m, score, result=None):
    b = m["score_bands"].get(band(score))

    if not b:
        return

    if result is None:
        b["signals"] += 1

    elif result in ("WIN", "LOSS", "DRAW"):
        b[result.lower() + "s"] += 1


def historical_bonus(m, symbol, action, setup):
    q = quality(
        m,
        symbol,
        action,
        setup
    )

    if (
        q["decided"] < SETUP_MIN_HISTORY
        or q["wr"] is None
    ):
        return 0.0, "LEARNING"

    if q["wr"] >= 75:
        return 10.0, f"REAL_WR_{q['wr']:.1f}"

    if q["wr"] >= 70:
        return 6.0, f"REAL_WR_{q['wr']:.1f}"

    if q["wr"] >= 65:
        return 2.0, f"REAL_WR_{q['wr']:.1f}"

    if q["wr"] < 55:
        return -12.0, f"REAL_WR_{q['wr']:.1f}"

    return 0.0, f"REAL_WR_{q['wr']:.1f}"


def analyze(c, m, symbol):
    if not c or len(c) < 150:
        return {
            "action": "WAIT",
            "score": 0,
            "setup": "NEUTRAL",
            "call_score": 0,
            "put_score": 0,
            "trend": "N/A",
            "real_wr": None,
            "reason": "INSUFFICIENT_DATA"
        }

    # Only closed candles.
    x = c[:-1]

    if len(x) < 110:
        return {
            "action": "WAIT",
            "score": 0,
            "setup": "NEUTRAL",
            "call_score": 0,
            "put_score": 0,
            "trend": "N/A",
            "real_wr": None,
            "reason": "INSUFFICIENT_CLOSED_CANDLES"
        }

    cur = x[-1]
    pr = x[-2]
    pr2 = x[-3]

    closes = [z["close"] for z in x]

    e20 = ema(closes, 20)
    e50 = ema(closes, 50)
    e100 = ema(closes, 100)

    if cur["close"] > e20 > e50 > e100:
        trend = "BULL_STRONG"
    elif cur["close"] < e20 < e50 < e100:
        trend = "BEAR_STRONG"
    elif cur["close"] > e50:
        trend = "BULL"
    elif cur["close"] < e50:
        trend = "BEAR"
    else:
        trend = "RANGE"

    z = x[-101:-1]

    sup = min(q["low"] for q in z)
    res = max(q["high"] for q in z)

    p = cur["close"]
    t = TUNING.get(
        symbol,
        TUNING["DEFAULT"]
    )

    ns = (
        abs(p - sup)
        / max(abs(sup), 1e-12)
        <= t["sr"]
    )

    nr = (
        abs(p - res)
        / max(abs(res), 1e-12)
        <= t["sr"]
    )

    ins = (
        abs(p - sup)
        / max(abs(sup), 1e-12)
        <= t["zone"]
    )

    inr = (
        abs(p - res)
        / max(abs(res), 1e-12)
        <= t["zone"]
    )

    vv = [
        q["volume"]
        for q in x[-16:-1]
        if q["volume"] > 0
    ]

    avg = sum(vv) / len(vv) if vv else 0

    vr = (
        cur["volume"] / avg
        if avg and cur["volume"]
        else 1
    )

    hv = vr >= t["vol"]

    body = abs(
        cur["close"] - cur["open"]
    )

    rng = max(
        cur["high"] - cur["low"],
        1e-12
    )

    up = (
        cur["high"]
        - max(cur["open"], cur["close"])
    )

    lo = (
        min(cur["open"], cur["close"])
        - cur["low"]
    )

    bull = cur["close"] > cur["open"]
    bear = cur["close"] < cur["open"]

    bp = (
        bull
        and lo >= max(
            body * 1.8,
            rng * 0.3
        )
    )

    sp = (
        bear
        and up >= max(
            body * 1.8,
            rng * 0.3
        )
    )

    be = (
        pr["close"] < pr["open"]
        and bull
        and cur["open"] <= pr["close"]
        and cur["close"] >= pr["open"]
    )

    se = (
        pr["close"] > pr["open"]
        and bear
        and cur["open"] >= pr["close"]
        and cur["close"] <= pr["open"]
    )

    bf = bull and (
        pr["close"] > pr["open"]
        or pr2["close"] > pr2["open"]
        or be
        or bp
    )

    sf = bear and (
        pr["close"] < pr["open"]
        or pr2["close"] < pr2["open"]
        or se
        or sp
    )

    cs = 0.0
    ps = 0.0

    cs += (
        20 if trend == "BULL_STRONG"
        else 15 if trend == "BULL"
        else 5 if trend == "RANGE"
        else 0
    )

    ps += (
        20 if trend == "BEAR_STRONG"
        else 15 if trend == "BEAR"
        else 5 if trend == "RANGE"
        else 0
    )

    cs += 25 if ns else 10 if ins else 0
    ps += 25 if nr else 10 if inr else 0

    cs += (
        20 if bp or be
        else 7 if bull
        else 0
    )

    ps += (
        20 if sp or se
        else 7 if bear
        else 0
    )

    cs += (
        15 if hv and bull
        else 4 if bull
        else 0
    )

    ps += (
        15 if hv and bear
        else 4 if bear
        else 0
    )

    cs += 10 if bf else 0
    ps += 10 if sf else 0

    call_setup = (
        "SUPPORT_REVERSAL_CALL"
        if ns
        else "MOMENTUM_CALL"
    )

    put_setup = (
        "RESISTANCE_REVERSAL_PUT"
        if nr
        else "MOMENTUM_PUT"
    )

    cm = quality(
        m,
        symbol,
        "CALL",
        call_setup
    )

    pm = quality(
        m,
        symbol,
        "PUT",
        put_setup
    )

    cbonus, ctag = historical_bonus(
        m,
        symbol,
        "CALL",
        call_setup
    )

    pbonus, ptag = historical_bonus(
        m,
        symbol,
        "PUT",
        put_setup
    )

    cs += cbonus
    ps += pbonus

    cv = (
        (
            ns
            and bull
            and (bp or be)
            and bf
            and trend != "BEAR_STRONG"
        )
        or (
            trend in ("BULL", "BULL_STRONG")
            and bull
            and bf
            and hv
            and p > e20
            and not nr
        )
    )

    pv = (
        (
            nr
            and bear
            and (sp or se)
            and sf
            and trend != "BULL_STRONG"
        )
        or (
            trend in ("BEAR", "BEAR_STRONG")
            and bear
            and sf
            and hv
            and p < e20
            and not ns
        )
    )

    # Historical setup gate.
    if (
        cm["decided"] >= SETUP_MIN_HISTORY
        and cm["wr"] < 55.0
    ):
        cv = False

    if (
        pm["decided"] >= SETUP_MIN_HISTORY
        and pm["wr"] < 55.0
    ):
        pv = False

    if ns and bull and (bp or be):
        cs += 10

    if nr and bear and (sp or se):
        ps += 10

    cs = max(0, min(100, cs))
    ps = max(0, min(100, ps))

    action = "WAIT"
    score = 0.0
    setup = "NEUTRAL"
    wr = None
    memory_tag = ""

    if (
        cv
        and cs >= MIN_SCORE_THRESHOLD
        and cs > ps
    ):
        action = "CALL"
        score = cs
        setup = call_setup
        wr = cm["wr"]
        memory_tag = ctag

    elif (
        pv
        and ps >= MIN_SCORE_THRESHOLD
        and ps > cs
    ):
        action = "PUT"
        score = ps
        setup = put_setup
        wr = pm["wr"]
        memory_tag = ptag

    return {
        "action": action,
        "score": round(score, 2),
        "setup": setup,
        "real_wr": wr,
        "call_score": round(cs, 2),
        "put_score": round(ps, 2),
        "trend": trend,
        "vol_ratio": round(vr, 2),
        "memory_tag": memory_tag,
        "call_history": cm,
        "put_history": pm,
    }


# ============================================================
# RANKING
# ============================================================

def rank_candidates(candidates, m):
    def key(x):
        a = x["analysis"]
        symbol = x["symbol"]

        ps = pair_stats(m, symbol)
        qs = quality(
            m,
            symbol,
            a["action"],
            a["setup"]
        )

        learned = (
            ps["wr"]
            if ps["decided"] >= PAIR_MIN_HISTORY
            and ps["wr"] is not None
            else 50.0
        )

        setup_wr = (
            qs["wr"]
            if qs["decided"] >= SETUP_MIN_HISTORY
            and qs["wr"] is not None
            else 50.0
        )

        return (
            a["score"],
            learned,
            setup_wr,
            min(ps["decided"], 50),
            min(qs["decided"], 50),
        )

    return sorted(
        candidates,
        key=key,
        reverse=True
    )


# ============================================================
# RECORD RESULT
# ============================================================

def record(m, tr, result):
    st = m["stats"]

    st["total_setups"] += 1

    if result == "WIN":
        st["wins"] += 1
    elif result == "LOSS":
        st["losses"] += 1
    elif result == "DRAW":
        st["draws"] += 1

    key = (
        f"{tr['symbol']}_"
        f"{tr['action']}_"
        f"{tr['setup']}"
    )

    s = m["setups"].setdefault(
        key,
        default_setup(
            tr["symbol"],
            tr["action"],
            tr["setup"]
        )
    )

    s["total"] += 1

    if result in ("WIN", "LOSS", "DRAW"):
        s[result.lower() + "s"] += 1

    for i in (1, 2, 3):
        r = (
            tr.get("opportunity_results", {})
            .get(str(i))
        )

        if r in ("WIN", "LOSS", "DRAW"):
            s[f"opportunity_{i}"][
                r.lower() + "s"
            ] += 1

    update_band(
        m,
        tr.get("entry_score", 0),
        result
    )

    save(m)

    decided = (
        s["wins"]
        + s["losses"]
    )

    wr = (
        s["wins"] / decided * 100
        if decided
        else 0
    )

    msg = (
        "📊 PAPER RESULT V5.2\n"
        f"ตลาด: {tr['symbol']}\n"
        f"ผลล่าสุด: {result}\n"
        f"Action: {tr['action']}\n"
        f"Setup: {tr['setup']}\n"
        f"Entry Score: {tr['entry_score']:.1f}\n"
        "ครบ Opportunity: 3/3\n"
        f"Setup WR: {wr:.1f}%\n"
        f"ระบบรวม: ชนะ {st['wins']} | "
        f"เสมอ {st['draws']} | "
        f"แพ้ {st['losses']}\n"
        f"🧠 Memory updated: {key}"
    )

    print(msg, flush=True)
    discord(msg)


# ============================================================
# MAIN LOOP
# ============================================================

def run():
    print("=" * 72)
    print("🤖 SIGZY BRAIN V5.2 - RAILWAY SAFE")
    print("MEMORY + REAL LEARNING + OPTIONAL AI CONFIRMATION")
    print("CRYPTO + FOREX | PAPER TRADE")
    print("=" * 72, flush=True)

    print(
        f"[CONFIG] Score >= {MIN_SCORE_THRESHOLD}",
        flush=True
    )

    print(
        f"[CONFIG] AI model: {GEMINI_MODEL}",
        flush=True
    )

    if DISCORD_WEBHOOK_URL:
        print(
            "✅ Discord webhook loaded.",
            flush=True
        )
        discord(
            "🟢 SIGZY BRAIN V5.2 ONLINE\n"
            "Railway Safe + Memory + Paper Trade"
        )
    else:
        print(
            "⚠️ DISCORD_WEBHOOK_URL is not set.",
            flush=True
        )

    if GEMINI_API_KEY:
        print(
            "✅ Gemini API key loaded.",
            flush=True
        )
    else:
        print(
            "⚠️ GEMINI_API_KEY not set. "
            "AI layer will be skipped.",
            flush=True
        )

    m = init_memory()

    active = {}
    last_alert = {}
    last_signal = datetime.now()

    while True:
        try:
            now = datetime.now()

            print(
                "\n" + "=" * 72,
                flush=True
            )

            print(
                "🧠 SCAN",
                now.strftime("%Y-%m-%d %H:%M:%S"),
                flush=True
            )

            done = []

            # ------------------------------------------------
            # Track active 3 opportunities.
            # ------------------------------------------------
            for symbol, tr in list(active.items()):
                c = fetch(symbol, 200)

                if not c or len(c) < 3:
                    continue

                cur = c[-2]

                if cur["timestamp"] == tr.get("last_ts"):
                    continue

                tr["last_ts"] = cur["timestamp"]
                tr["opportunity"] += 1

                opp = tr["opportunity"]

                green = (
                    cur["close"] > cur["open"]
                )

                red = (
                    cur["close"] < cur["open"]
                )

                if tr["action"] == "CALL":
                    result = (
                        "WIN"
                        if green
                        else "LOSS"
                        if red
                        else "DRAW"
                    )
                else:
                    result = (
                        "WIN"
                        if red
                        else "LOSS"
                        if green
                        else "DRAW"
                    )

                tr["opportunity_results"][
                    str(opp)
                ] = result

                print(
                    f"📍 {symbol} | "
                    f"OPP {opp}/3 | "
                    f"{tr['action']} | {result}",
                    flush=True
                )

                if opp >= 3:
                    record(
                        m,
                        tr,
                        tr["opportunity_results"]["3"]
                    )

                    done.append(symbol)

            for symbol in done:
                active.pop(symbol, None)

            # ------------------------------------------------
            # Find new candidates.
            # ------------------------------------------------
            candidates = []

            for symbol in SYMBOLS:
                ps = pair_stats(
                    m,
                    symbol
                )

                status = pair_status(ps)

                if status == "BLOCK":
                    continue

                if symbol in active:
                    continue

                if symbol in last_alert:
                    elapsed = (
                        now
                        - last_alert[symbol]
                    ).total_seconds()

                    if elapsed < SYMBOL_COOLDOWN_SECONDS:
                        continue

                c = fetch(symbol, 200)

                if not c or len(c) < 150:
                    continue

                a = analyze(
                    c,
                    m,
                    symbol
                )

                if (
                    a["action"] != "WAIT"
                    and a["score"] >= MIN_SCORE_THRESHOLD
                ):
                    closed = c[:-1]

                    candidates.append({
                        "symbol": symbol,
                        "analysis": a,
                        "price": closed[-1]["close"],
                        "timestamp": closed[-1]["timestamp"],
                        "candles": c,
                    })

                    pair_wr = (
                        f"{ps['wr']:.1f}%"
                        if ps["wr"] is not None
                        else "N/A"
                    )

                    print(
                        f"🎯 CANDIDATE {symbol} "
                        f"{a['action']} "
                        f"{a['score']:.1f} "
                        f"{a['setup']} | "
                        f"Pair={status} | "
                        f"PairWR={pair_wr}",
                        flush=True
                    )

            ranked = rank_candidates(
                candidates,
                m
            )

            selected = ranked[
                :MAX_NEW_ALERTS_PER_SCAN
            ]

            # ------------------------------------------------
            # Alert selected signal.
            # ------------------------------------------------
            for cand in selected:
                a = cand["analysis"]
                symbol = cand["symbol"]

                ai_approved, ai_reason, ai_conf = (
                    ai_final_check(
                        symbol,
                        a["action"],
                        a["setup"],
                        a["trend"],
                        cand["candles"]
                    )
                )

                if not ai_approved:
                    print(
                        f"⛔ AI REJECTED {symbol} | "
                        f"{ai_conf:.1f}% | "
                        f"{ai_reason}",
                        flush=True
                    )
                    continue

                last_signal = now

                tr = {
                    "symbol": symbol,
                    "action": a["action"],
                    "price": cand["price"],
                    "entry_timestamp": cand["timestamp"],
                    "entry_score": a["score"],
                    "setup": a["setup"],
                    "opportunity": 0,
                    "last_ts": cand["timestamp"],
                    "opportunity_results": {},
                }

                active[symbol] = tr
                last_alert[symbol] = now

                update_band(
                    m,
                    a["score"]
                )

                save(m)

                ps = pair_stats(
                    m,
                    symbol
                )

                wr = (
                    f"{a['real_wr']:.1f}%"
                    if a["real_wr"] is not None
                    else "กำลังเก็บสถิติ"
                )

                pair_wr_str = (
                    f"{ps['wr']:.1f}% "
                    f"({ps['decided']} decided)"
                    if ps["wr"] is not None
                    else "กำลังเรียนรู้"
                )

                ai_text = (
                    f"{ai_conf:.1f}%"
                    if ai_conf > 0
                    else "SKIPPED"
                )

                msg = (
                    f"🚨 SIGZY V5.2 AI — "
                    f"{band(a['score'])}\n"
                    f"ตลาด: {symbol}\n"
                    f"เวลา: {now.strftime('%H:%M:%S')}\n"
                    f"ราคา: {cand['price']}\n"
                    f"ทิศทาง: "
                    f"{'CALL 🟢' if a['action'] == 'CALL' else 'PUT 🔴'}\n"
                    f"Score: {a['score']:.1f}/100\n"
                    f"Setup: {a['setup']}\n"
                    f"Trend: {a['trend']}\n"
                    f"Historical Setup WR: {wr}\n"
                    f"Pair WR: {pair_wr_str}\n"
                    f"🤖 AI Confidence: {ai_text}\n"
                    f"💬 AI Reason: {ai_reason}\n"
                    f"🧠 Memory: "
                    f"{a.get('memory_tag', 'LEARNING')}\n"
                    "🏆 Best opportunity selected\n"
                    "📊 Paper Track: "
                    "Opportunity 1 ➔ 2 ➔ 3\n"
                    "⚠️ Paper Trade เท่านั้น"
                )

                print(
                    msg,
                    flush=True
                )

                discord(msg)

            total = m["stats"]["total_setups"]
            wins = m["stats"]["wins"]
            losses = m["stats"]["losses"]
            draws = m["stats"]["draws"]

            decided = wins + losses

            overall_wr = (
                wins / decided * 100
                if decided
                else 0
            )

            print(
                f"📊 Memory: {total} setups | "
                f"WIN={wins} "
                f"LOSS={losses} "
                f"DRAW={draws} | "
                f"WR={overall_wr:.2f}% | "
                f"Active={len(active)} | "
                f"Last signal="
                f"{(now-last_signal).total_seconds()/60:.0f} min ago",
                flush=True
            )

            print(
                f"⏳ Wait {PAPER_INTERVAL_SECONDS}s...",
                flush=True
            )

            time.sleep(
                PAPER_INTERVAL_SECONDS
            )

        except Exception as e:
            # Critical: never let one bad symbol/API response
            # kill the Railway process.
            print(
                "[LOOP ERROR] Bot recovered:",
                repr(e),
                flush=True
            )

            try:
                discord(
                    "⚠️ SIGZY V5.2 LOOP ERROR\n"
                    f"{repr(e)}\n"
                    "ระบบกำลังพยายามรันต่อ"
                )
            except Exception:
                pass

            time.sleep(10)


# ============================================================
# START
# ============================================================

if __name__ == "__main__":
    try:
        run()

    except KeyboardInterrupt:
        print(
            "🛑 SIGZY V5.2 stopped.",
            flush=True
        )

    except Exception as e:
        # Last-resort guard.
        print(
            "[FATAL ERROR]",
            repr(e),
            flush=True
        )

        time.sleep(5)
'''
path = Path("/mnt/data/main.py")
path.write_text(code, encoding="utf-8")
print(path)
