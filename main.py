from pathlib import Path

code = r'''# ============================================================
# SIGZY BRAIN V6.0 - PAPER TRADE / RAILWAY
# BOT + AI #1 PATTERN + AI #2 CONFIRM
# REAL MEMORY + BEST PAIR SELECTION
# THAI TIMEZONE UTC+7
# ============================================================
# IMPORTANT:
# - V6 นี้เป็น PAPER TRADE เท่านั้น ไม่ส่งคำสั่งซื้อขายจริง
# - ใช้ Yahoo Finance สำหรับราคา
# - เก็บ Memory / Stats ใน sigzy_memory_v6.json
# - Telegram เป็น optional: ตั้ง TELEGRAM_BOT_TOKEN และ TELEGRAM_CHAT_ID
# ============================================================

import os
import json
import time
import urllib.request
import urllib.parse
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ---------------- CONFIG ----------------
PAPER_INTERVAL_SECONDS = 60
CANDLE_INTERVAL = "1m"
CANDLE_RANGE = "1d"

PATTERN_THRESHOLD = 88.0
PAIR_SCORE_THRESHOLD = 80.0
AI_CONFIDENCE_THRESHOLD = 75.0

# ไม่ใช้ OTC
CRYPTO = [
    "BTCUSDT", "ETHUSDT", "BNBUSDT",
    "SOLUSDT", "XRPUSDT", "ADAUSDT"
]

FOREX = [
    "GBPUSD", "GBPJPY", "EURUSD",
    "USDJPY", "AUDUSD", "NZDUSD"
]

SYMBOLS = CRYPTO + FOREX
YAHOO = {s: s + "=X" for s in FOREX}
for s in CRYPTO:
    YAHOO[s] = s

MEMORY_FILE = Path("sigzy_memory_v6.json")
TZ = timezone(timedelta(hours=7))

# ---------------- MEMORY ----------------
DEFAULT_MEMORY = {
    "version": "V6.0",
    "stats": {
        "total_setups": 0,
        "wins": 0,
        "losses": 0,
        "draws": 0,
        "invalid": 0
    },
    "setups": {},
    "pattern_memory": {},
    "pair_memory": {},
    "last_signals": {}
}

def load_memory():
    if not MEMORY_FILE.exists():
        return json.loads(json.dumps(DEFAULT_MEMORY))
    try:
        with open(MEMORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        # เติม key ที่ขาดโดยไม่ล้าง memory เดิม
        for k, v in DEFAULT_MEMORY.items():
            if k not in data:
                data[k] = json.loads(json.dumps(v))
        return data
    except Exception:
        return json.loads(json.dumps(DEFAULT_MEMORY))

def save_memory(memory):
    tmp = MEMORY_FILE.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(memory, f, ensure_ascii=False, indent=2)
    tmp.replace(MEMORY_FILE)

memory = load_memory()

# ---------------- UTILS ----------------
def now_th():
    return datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")

def clamp(x, lo=0.0, hi=100.0):
    return max(lo, min(hi, float(x)))

def pct(a, b):
    if b == 0:
        return 0.0
    return (a / b) * 100.0

def log(msg):
    print(f"[{now_th()}] {msg}", flush=True)

# ---------------- TELEGRAM ----------------
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

def telegram(text):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        data = urllib.parse.urlencode({
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text
        }).encode()
        urllib.request.urlopen(
            urllib.request.Request(url, data=data),
            timeout=10
        ).read()
    except Exception as e:
        log(f"Telegram error: {e}")

# ---------------- MARKET DATA ----------------
def yahoo_url(symbol):
    y = YAHOO[symbol]
    return (
        "https://query1.finance.yahoo.com/v8/finance/chart/"
        + urllib.parse.quote(y)
        + f"?interval={CANDLE_INTERVAL}&range={CANDLE_RANGE}"
    )

def get_candles(symbol):
    try:
        req = urllib.request.Request(
            yahoo_url(symbol),
            headers={"User-Agent": "Mozilla/5.0"}
        )
        raw = urllib.request.urlopen(req, timeout=12).read()
        obj = json.loads(raw.decode())
        result = obj["chart"]["result"][0]

        q = result["indicators"]["quote"][0]
        closes = q.get("close", [])
        opens = q.get("open", [])
        highs = q.get("high", [])
        lows = q.get("low", [])

        rows = []
        for i in range(len(closes)):
            if None in (closes[i], opens[i], highs[i], lows[i]):
                continue
            rows.append({
                "o": float(opens[i]),
                "h": float(highs[i]),
                "l": float(lows[i]),
                "c": float(closes[i])
            })

        return rows[-80:]
    except Exception as e:
        log(f"{symbol}: data error -> {e}")
        return []

# ---------------- INDICATORS ----------------
def sma(values, n):
    if len(values) < n:
        return None
    return sum(values[-n:]) / n

def rsi(closes, period=14):
    if len(closes) < period + 1:
        return 50.0
    gains = []
    losses = []
    for i in range(-period, 0):
        d = closes[i] - closes[i-1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    ag = sum(gains) / period
    al = sum(losses) / period
    if al == 0:
        return 100.0
    return 100.0 - (100.0 / (1.0 + ag / al))

def candle_features(rows):
    c = rows[-1]
    body = abs(c["c"] - c["o"])
    rng = max(c["h"] - c["l"], 1e-12)
    upper = c["h"] - max(c["o"], c["c"])
    lower = min(c["o"], c["c"]) - c["l"]
    return {
        "bull": c["c"] > c["o"],
        "bear": c["c"] < c["o"],
        "body_ratio": body / rng,
        "upper_ratio": upper / rng,
        "lower_ratio": lower / rng
    }

# ---------------- AI #1: PATTERN ----------------
def pattern_engine(rows):
    closes = [x["c"] for x in rows]
    f = candle_features(rows)

    score_call = 50.0
    score_put = 50.0
    reasons_call = []
    reasons_put = []

    ma5 = sma(closes, 5)
    ma10 = sma(closes, 10)
    ma20 = sma(closes, 20)
    rv = rsi(closes)

    if ma5 and ma10:
        if ma5 > ma10:
            score_call += 12
            score_put -= 12
            reasons_call.append("MA5>MA10")
        elif ma5 < ma10:
            score_put += 12
            score_call -= 12
            reasons_put.append("MA5<MA10")

    if ma10 and ma20:
        if ma10 > ma20:
            score_call += 10
            score_put -= 10
            reasons_call.append("MA10>MA20")
        elif ma10 < ma20:
            score_put += 10
            score_call -= 10
            reasons_put.append("MA10<MA20")

    if rv < 30:
        score_call += 10
        reasons_call.append("RSI oversold")
    elif rv > 70:
        score_put += 10
        reasons_put.append("RSI overbought")

    if f["bull"] and f["body_ratio"] >= 0.55:
        score_call += 10
        reasons_call.append("strong bull candle")
    if f["bear"] and f["body_ratio"] >= 0.55:
        score_put += 10
        reasons_put.append("strong bear candle")

    if f["lower_ratio"] > 0.45:
        score_call += 8
        reasons_call.append("lower rejection")
    if f["upper_ratio"] > 0.45:
        score_put += 8
        reasons_put.append("upper rejection")

    # Momentum from recent closes
    if len(closes) >= 6:
        momentum = closes[-1] - closes[-6]
        if momentum > 0:
            score_call += 8
            reasons_call.append("positive momentum")
        elif momentum < 0:
            score_put += 8
            reasons_put.append("negative momentum")

    score_call = clamp(score_call)
    score_put = clamp(score_put)

    if score_call >= score_put:
        action = "CALL"
        score = score_call
        reasons = reasons_call
    else:
        action = "PUT"
        score = score_put
        reasons = reasons_put

    return {
        "action": action,
        "score": round(score, 2),
        "call": round(score_call, 2),
        "put": round(score_put, 2),
        "rsi": round(rv, 2),
        "reasons": reasons
    }

# ---------------- AI #2: CONFIRM ----------------
def confirm_engine(rows, pattern):
    closes = [x["c"] for x in rows]
    if len(closes) < 20:
        return {"confidence": 0.0, "agree": False, "reason": "not enough candles"}

    action = pattern["action"]
    confidence = 50.0
    reasons = []

    ma5 = sma(closes, 5)
    ma20 = sma(closes, 20)
    rv = rsi(closes)

    if action == "CALL":
        if ma5 > ma20:
            confidence += 15
            reasons.append("trend confirm")
        if rv < 68:
            confidence += 8
            reasons.append("RSI not extreme")
        if closes[-1] > closes[-2]:
            confidence += 7
            reasons.append("last candle agrees")
    else:
        if ma5 < ma20:
            confidence += 15
            reasons.append("trend confirm")
        if rv > 32:
            confidence += 8
            reasons.append("RSI not extreme")
        if closes[-1] < closes[-2]:
            confidence += 7
            reasons.append("last candle agrees")

    confidence = clamp(confidence)
    return {
        "confidence": round(confidence, 2),
        "agree": confidence >= AI_CONFIDENCE_THRESHOLD,
        "reason": ", ".join(reasons) if reasons else "weak confirmation"
    }

# ---------------- MEMORY SCORE ----------------
def memory_score(symbol, action):
    key = f"{symbol}_{action}"
    item = memory["pair_memory"].get(key)
    if not item:
        return 50.0

    w = int(item.get("wins", 0))
    l = int(item.get("losses", 0))
    total = w + l
    if total < 5:
        return 50.0

    return clamp((w / total) * 100.0)

# ---------------- SETUP ----------------
def analyze(symbol, rows):
    p = pattern_engine(rows)
    c = confirm_engine(rows, p)
    mem = memory_score(symbol, p["action"])

    # weighted final score
    final_score = (
        p["score"] * 0.50 +
        c["confidence"] * 0.30 +
        mem * 0.20
    )

    valid = (
        p["score"] >= PATTERN_THRESHOLD and
        c["confidence"] >= AI_CONFIDENCE_THRESHOLD and
        final_score >= PAIR_SCORE_THRESHOLD
    )

    return {
        "symbol": symbol,
        "action": p["action"],
        "pattern_score": p["score"],
        "ai_confidence": c["confidence"],
        "memory_score": round(mem, 2),
        "final_score": round(final_score, 2),
        "valid": valid,
        "rsi": p["rsi"],
        "reasons": p["reasons"],
        "confirm": c["reason"],
        "price": rows[-1]["c"]
    }

# ---------------- PAPER TRADE ----------------
def register_setup(signal):
    key = f'{signal["symbol"]}_{signal["action"]}'
    memory["setups"].setdefault(key, {
        "symbol": signal["symbol"],
        "action": signal["action"],
        "wins": 0,
        "losses": 0,
        "draws": 0,
        "signals": 0
    })
    memory["setups"][key]["signals"] += 1

def record_result(signal, result):
    key = f'{signal["symbol"]}_{signal["action"]}'
    item = memory["setups"].setdefault(key, {
        "symbol": signal["symbol"],
        "action": signal["action"],
        "wins": 0,
        "losses": 0,
        "draws": 0,
        "signals": 0
    })

    if result == "WIN":
        item["wins"] += 1
        memory["stats"]["wins"] += 1
    elif result == "LOSS":
        item["losses"] += 1
        memory["stats"]["losses"] += 1
    else:
        item["draws"] += 1
        memory["stats"]["draws"] += 1

    memory["stats"]["total_setups"] += 1

    pm = memory["pair_memory"].setdefault(key, {
        "wins": 0, "losses": 0, "draws": 0
    })
    if result == "WIN":
        pm["wins"] += 1
    elif result == "LOSS":
        pm["losses"] += 1
    else:
        pm["draws"] += 1

    save_memory(memory)

def evaluate_old_signals():
    """
    ตรวจ signal ที่ผ่านไป 3 รอบ (ประมาณ 3 นาที)
    """
    now = time.time()
    changed = False

    for key, trade in list(memory["last_signals"].items()):
        if trade.get("result") is not None:
            continue

        if now - trade["created_ts"] < PAPER_INTERVAL_SECONDS * 3:
            continue

        rows = get_candles(trade["symbol"])
        if not rows:
            continue

        current = rows[-1]["c"]
        entry = trade["entry"]
        action = trade["action"]

        if action == "CALL":
            if current > entry:
                result = "WIN"
            elif current < entry:
                result = "LOSS"
            else:
                result = "DRAW"
        else:
            if current < entry:
                result = "WIN"
            elif current > entry:
                result = "LOSS"
            else:
                result = "DRAW"

        trade["exit"] = current
        trade["result"] = result
        trade["evaluated_at"] = now_th()
        record_result(trade, result)
        changed = True

        log(
            f"RESULT {trade['symbol']} {action} "
            f"{result} | entry={entry:.6f} exit={current:.6f}"
        )
        telegram(
            f"SIGZY V6 PAPER RESULT\n"
            f"{trade['symbol']} {action}\n"
            f"Result: {result}\n"
            f"Entry: {entry:.6f}\n"
            f"Exit: {current:.6f}"
        )

    if changed:
        save_memory(memory)

# ---------------- BEST PAIR ----------------
def choose_best(signals):
    valid = [s for s in signals if s["valid"]]
    if not valid:
        return None
    valid.sort(
        key=lambda x: (
            x["final_score"],
            x["ai_confidence"],
            x["pattern_score"]
        ),
        reverse=True
    )
    return valid[0]

# ---------------- MAIN ----------------
def main():
    log("============================================================")
    log("SIGZY BRAIN V6.0 STARTED - PAPER TRADE ONLY")
    log("BOT + AI #1 PATTERN + AI #2 CONFIRM + REAL MEMORY")
    log("Timezone: UTC+7 | OTC: DISABLED")
    log("============================================================")

    telegram("🧠 SIGZY BRAIN V6.0 STARTED\nPaper Trade Only\nUTC+7")

    while True:
        try:
            evaluate_old_signals()

            signals = []

            for symbol in SYMBOLS:
                rows = get_candles(symbol)
                if len(rows) < 25:
                    continue

                s = analyze(symbol, rows)
                signals.append(s)

                log(
                    f"{symbol}: {s['action']} | "
                    f"PAT={s['pattern_score']} "
                    f"AI={s['ai_confidence']} "
                    f"MEM={s['memory_score']} "
                    f"FINAL={s['final_score']} "
                    f"{'PASS' if s['valid'] else 'BLOCK'}"
                )

            best = choose_best(signals)

            if best:
                key = f"{best['symbol']}_{best['action']}"
                old = memory["last_signals"].get(key)

                # ป้องกันการออก signal ซ้ำคู่/ทิศทางเดิมภายใน 3 นาที
                duplicate = (
                    old and
                    old.get("result") is None and
                    time.time() - old.get("created_ts", 0)
                    < PAPER_INTERVAL_SECONDS * 3
                )

                if not duplicate:
                    register_setup(best)

                    trade = {
                        "symbol": best["symbol"],
                        "action": best["action"],
                        "entry": best["price"],
                        "created_at": now_th(),
                        "created_ts": time.time(),
                        "pattern_score": best["pattern_score"],
                        "ai_confidence": best["ai_confidence"],
                        "memory_score": best["memory_score"],
                        "final_score": best["final_score"],
                        "result": None
                    }

                    memory["last_signals"][key] = trade
                    save_memory(memory)

                    msg = (
                        f"🚨 SIGZY V6 PAPER SIGNAL\n"
                        f"คู่: {best['symbol']}\n"
                        f"ทิศทาง: {best['action']}\n"
                        f"Entry: {best['price']:.6f}\n"
                        f"Pattern: {best['pattern_score']:.1f}\n"
                        f"AI Confirm: {best['ai_confidence']:.1f}\n"
                        f"Memory: {best['memory_score']:.1f}\n"
                        f"Final: {best['final_score']:.1f}\n"
                        f"เหตุผล: {', '.join(best['reasons'])}\n"
                        f"ตรวจผลหลัง 3 นาที\n"
                        f"เวลาไทย: {now_th()}"
                    )

                    log(msg)
                    telegram(msg)
            else:
                log("NO HIGH-QUALITY SETUP -> WAIT")

            time.sleep(PAPER_INTERVAL_SECONDS)

        except KeyboardInterrupt:
            log("STOPPED")
            break
        except Exception as e:
            log(f"MAIN ERROR: {e}")
            time.sleep(10)

if __name__ == "__main__":
    main()
'''

path = Path("/mnt/data/SIGZY_BRAIN_V6_0_TH_TIME.py")
path.write_text(code, encoding="utf-8")
print(f"สร้างไฟล์เรียบร้อย: {path}")
print(f"ขนาด: {path.stat().st_size/1024:.1f} KB")
