from pathlib import Path
import zipfile

out = Path("/mnt/data/railway_bot_v24_deploy")
out.mkdir(parents=True, exist_ok=True)

main_code = r'''import time
import json
import os
import urllib.request
from datetime import datetime

# ============================================================
# RAILWAY DEPLOY VERSION
# IMPORTANT:
# - ห้ามใช้ /mnt/data ใน Railway
# - ไฟล์นี้คือ BOT โดยตรง ไม่ใช่สคริปต์สร้าง ZIP
# ============================================================

SYMBOLS = [
    "BTCUSDT",
    "ETHUSDT",
    "SOLUSDT",
    "XRPUSDT",
    "BNBUSDT",
]

PAPER_INTERVAL_SECONDS = 180
MIN_SCORE_THRESHOLD = 80.0
MIN_SCORE_GAP = 15.0
MEMORY_FILE = "bot_memory_v24_3candles.json"


def fetch_binance_klines(symbol, limit=100):
    try:
        url = (
            "https://api.binance.com/api/v3/klines"
            f"?symbol={symbol}&interval=1m&limit={limit}"
        )
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0"},
        )

        with urllib.request.urlopen(req, timeout=15) as response:
            data = json.loads(response.read().decode("utf-8"))

        return [
            {
                "timestamp": row[0],
                "open": float(row[1]),
                "high": float(row[2]),
                "low": float(row[3]),
                "close": float(row[4]),
                "volume": float(row[5]),
            }
            for row in data
        ]

    except Exception as e:
        print(f"[BINANCE ERROR] {symbol}: {e}", flush=True)
        return None


def save_memory(memory):
    try:
        with open(MEMORY_FILE, "w", encoding="utf-8") as f:
            json.dump(memory, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"[MEMORY SAVE ERROR] {e}", flush=True)


def init_memory():
    if os.path.exists(MEMORY_FILE):
        try:
            with open(MEMORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"[MEMORY] โหลดไฟล์เดิมไม่ได้: {e}", flush=True)

    memory = {
        "stats": {
            "total_trades": 0,
            "wins": 0,
            "losses": 0,
            "draws": 0,
            "win_streak": 0,
            "loss_streak": 0,
            "best_win_streak": 0,
            "best_loss_streak": 0,
        },
        "direction_memory": {},
        "recent_logs": [],
    }

    save_memory(memory)
    print(">> 🧠 เริ่มต้น Memory 3 แท่ง / 3 โอกาส", flush=True)
    return memory


def record_trade_result(memory, symbol, action, trend_key, result):
    s = memory["stats"]
    s["total_trades"] += 1

    if result == "WIN":
        s["wins"] += 1
        s["win_streak"] += 1
        s["loss_streak"] = 0
        s["best_win_streak"] = max(
            s["best_win_streak"], s["win_streak"]
        )

    elif result == "LOSS":
        s["losses"] += 1
        s["loss_streak"] += 1
        s["win_streak"] = 0
        s["best_loss_streak"] = max(
            s["best_loss_streak"], s["loss_streak"]
        )

    else:
        s["draws"] += 1

    key = f"{symbol}|{action}|{trend_key}"

    row = memory["direction_memory"].setdefault(
        key,
        {"WIN": 0, "LOSS": 0, "DRAW": 0},
    )

    row[result] = int(row.get(result, 0)) + 1

    save_memory(memory)

    decided = s["wins"] + s["losses"]
    win_rate = (
        s["wins"] / decided * 100.0
        if decided
        else 0.0
    )

    print("\n==================================================", flush=True)
    print(f"📊 {symbol} | {action} => {result}", flush=True)
    print(
        f"ยอดรวม: {s['total_trades']} | "
        f"ชนะ: {s['wins']} | แพ้: {s['losses']}",
        flush=True,
    )
    print(
        f"Win Rate: {win_rate:.2f}% | "
        f"Win Streak: {s['win_streak']} | "
        f"Loss Streak: {s['loss_streak']}",
        flush=True,
    )
    print("==================================================", flush=True)


def ema(data, period):
    if not data:
        return 0.0

    multiplier = 2.0 / (period + 1.0)
    value = data[0]

    for price in data[1:]:
        value = price * multiplier + value * (1.0 - multiplier)

    return value


def calculate_indicators(candles):
    # ใช้เฉพาะ CLOSED candles
    # Binance คืนแท่งล่าสุดที่กำลังวิ่งอยู่เป็นตัวท้าย
    if len(candles) < 55:
        return None

    closed = candles[:-1]

    closes = [x["close"] for x in closed]
    highs = [x["high"] for x in closed]
    lows = [x["low"] for x in closed]
    volumes = [x["volume"] for x in closed]

    ema20 = ema(closes[-20:], 20)
    ema50 = ema(closes[-50:], 50)
    ema200 = ema(closes[-min(len(closes), 200):], min(len(closes), 200))

    gains = 0.0
    losses = 0.0

    for i in range(-14, 0):
        diff = closes[i] - closes[i - 1]

        if diff > 0:
            gains += diff
        else:
            losses -= diff

    avg_gain = gains / 14.0
    avg_loss = losses / 14.0

    if avg_loss > 0:
        rs = avg_gain / avg_loss
        rsi = 100.0 - (100.0 / (1.0 + rs))
    else:
        rsi = 100.0

    # MACD แบบ lightweight สำหรับระบบนี้
    fast_ema = ema(closes[-12:], 12)
    slow_ema = ema(closes[-26:], 26)
    macd_val = fast_ema - slow_ema

    prev_fast = ema(closes[-13:-1], 12)
    prev_slow = ema(closes[-27:-1], 26)
    prev_macd = prev_fast - prev_slow

    macd_sig = prev_macd

    atr = highs[-1] - lows[-1]
    momentum = closes[-1] - closes[-5]

    vol_mean = sum(volumes[-10:]) / 10.0
    volume_ratio = (
        volumes[-1] / vol_mean
        if vol_mean > 0
        else 1.0
    )

    hh = (
        highs[-1] > highs[-2]
        and highs[-2] > highs[-3]
    )

    hl = (
        lows[-1] > lows[-2]
        and lows[-2] > lows[-3]
    )

    lh = (
        highs[-1] < highs[-2]
        and highs[-2] < highs[-3]
    )

    ll = (
        lows[-1] < lows[-2]
        and lows[-2] < lows[-3]
    )

    return {
        "price": closes[-1],
        "ema20": ema20,
        "ema50": ema50,
        "ema200": ema200,
        "rsi": rsi,
        "macd_val": macd_val,
        "macd_sig": macd_sig,
        "atr": atr,
        "momentum": momentum,
        "volume_ratio": volume_ratio,
        "hh": hh,
        "hl": hl,
        "lh": lh,
        "ll": ll,
        "closed_timestamp": closed[-1]["timestamp"],
    }


def evaluate_brain(ind, symbol, memory):
    call = {}
    put = {}

    # Trend
    if ind["ema20"] > ind["ema50"] > ind["ema200"]:
        call["TREND"] = 95
        put["TREND"] = 10
    elif ind["ema20"] < ind["ema50"] < ind["ema200"]:
        call["TREND"] = 10
        put["TREND"] = 95
    else:
        call["TREND"] = 50
        put["TREND"] = 50

    # EMA
    call["EMA"] = 90 if ind["price"] > ind["ema20"] else 25
    put["EMA"] = 90 if ind["price"] < ind["ema20"] else 25

    # MACD
    call["MACD"] = 88 if ind["macd_val"] > ind["macd_sig"] else 30
    put["MACD"] = 88 if ind["macd_val"] < ind["macd_sig"] else 30

    # Momentum
    call["MOMENTUM"] = 88 if ind["momentum"] > 0 else 25
    put["MOMENTUM"] = 88 if ind["momentum"] < 0 else 25

    # Volume
    volume_score = max(20.0, min(100.0, ind["volume_ratio"] * 50.0))
    call["VOLUME"] = volume_score
    put["VOLUME"] = volume_score

    # Structure
    if ind["hh"] and ind["hl"]:
        call["STRUCTURE"] = 92
        put["STRUCTURE"] = 15
    elif ind["lh"] and ind["ll"]:
        call["STRUCTURE"] = 15
        put["STRUCTURE"] = 92
    else:
        call["STRUCTURE"] = 50
        put["STRUCTURE"] = 50

    # RSI / pattern
    if ind["rsi"] < 35:
        call["PATTERN"] = 88
        put["PATTERN"] = 30
    elif ind["rsi"] > 65:
        call["PATTERN"] = 30
        put["PATTERN"] = 88
    else:
        call["PATTERN"] = 50
        put["PATTERN"] = 50

    # Memory
    trend_key = (
        "BULL"
        if ind["ema20"] > ind["ema50"]
        else "BEAR"
    )

    c_hist = memory["direction_memory"].get(
        f"{symbol}|CALL|{trend_key}",
        {"WIN": 1, "LOSS": 1},
    )

    p_hist = memory["direction_memory"].get(
        f"{symbol}|PUT|{trend_key}",
        {"WIN": 1, "LOSS": 1},
    )

    c_total = c_hist.get("WIN", 0) + c_hist.get("LOSS", 0)
    p_total = p_hist.get("WIN", 0) + p_hist.get("LOSS", 0)

    call["MEMORY"] = (
        c_hist.get("WIN", 0) / c_total * 100
        if c_total
        else 50
    )

    put["MEMORY"] = (
        p_hist.get("WIN", 0) / p_total * 100
        if p_total
        else 50
    )

    call_final = sum(call.values()) / len(call)
    put_final = sum(put.values()) / len(put)

    return (
        round(call_final, 2),
        round(put_final, 2),
        trend_key,
    )


def analyze_market(symbol, memory):
    candles = fetch_binance_klines(symbol, 100)

    if not candles:
        return None

    ind = calculate_indicators(candles)

    if not ind:
        return None

    call_score, put_score, trend_key = evaluate_brain(
        ind,
        symbol,
        memory,
    )

    action = "WAIT"
    score = 0.0

    if (
        call_score >= MIN_SCORE_THRESHOLD
        and call_score - put_score >= MIN_SCORE_GAP
    ):
        action = "CALL"
        score = call_score

    elif (
        put_score >= MIN_SCORE_THRESHOLD
        and put_score - call_score >= MIN_SCORE_GAP
    ):
        action = "PUT"
        score = put_score

    return {
        "symbol": symbol,
        "action": action,
        "score": round(score, 2),
        "call_score": call_score,
        "put_score": put_score,
        "trend_key": trend_key,
        "price": ind["price"],
        "closed_timestamp": ind["closed_timestamp"],
    }


def scan_all_symbols(memory):
    results = []

    for symbol in SYMBOLS:
        result = analyze_market(symbol, memory)

        if result:
            results.append(result)

    return results


def run_bot():
    print("==================================================", flush=True)
    print("🤖 V24.2 RAILWAY DEPLOY VERSION", flush=True)
    print("🧠 CLOSED-CANDLE / 3-CANDLE / PAPER MODE", flush=True)
    print("==================================================", flush=True)
    print(f"Pairs: {', '.join(SYMBOLS)}", flush=True)
    print(
        f"Threshold: {MIN_SCORE_THRESHOLD} | "
        f"Gap: {MIN_SCORE_GAP}",
        flush=True,
    )
    print(
        f"Scan interval: {PAPER_INTERVAL_SECONDS}s",
        flush=True,
    )
    print("==================================================", flush=True)

    memory = init_memory()
    active_trades = {}

    while True:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        print(f"\n[🔍 {now}] SCAN START", flush=True)

        # ----------------------------------------------------
        # 1. ตรวจผลไม้ที่กำลังติดตาม
        # ----------------------------------------------------
        finished = []

        for symbol, trade in list(active_trades.items()):
            candles = fetch_binance_klines(symbol, 5)

            if not candles:
                continue

            # ใช้แท่ง CLOSED ล่าสุด
            closed = candles[:-1]

            if not closed:
                continue

            current = closed[-1]
            current_price = current["close"]
            current_ts = current["timestamp"]

            # ป้องกันการนับแท่งเดิมซ้ำ
            if current_ts == trade.get("last_checked_timestamp"):
                continue

            trade["last_checked_timestamp"] = current_ts
            trade["current_candle"] += 1

            entry_price = trade["price"]

            if trade["action"] == "CALL":
                candle_win = current_price > entry_price
            else:
                candle_win = current_price < entry_price

            if candle_win:
                trade["success_count"] += 1
                print(
                    f"🟢 [{symbol}] "
                    f"แท่ง {trade['current_candle']}/3 WIN "
                    f"price={current_price}",
                    flush=True,
                )
            else:
                trade["fail_count"] += 1
                print(
                    f"🔴 [{symbol}] "
                    f"แท่ง {trade['current_candle']}/3 LOSS "
                    f"price={current_price}",
                    flush=True,
                )

            # ชนะทันที = ปิดไม้
            if candle_win:
                record_trade_result(
                    memory,
                    symbol,
                    trade["action"],
                    trade["trend_key"],
                    "WIN",
                )
                finished.append(symbol)

            # ครบ 3 แท่งแล้วยังไม่ชนะ = LOSS
            elif trade["current_candle"] >= 3:
                record_trade_result(
                    memory,
                    symbol,
                    trade["action"],
                    trade["trend_key"],
                    "LOSS",
                )
                finished.append(symbol)

        for symbol in finished:
            active_trades.pop(symbol, None)

        # ----------------------------------------------------
        # 2. สแกนหา SIGNAL ใหม่
        # ----------------------------------------------------
        for result in scan_all_symbols(memory):
            symbol = result["symbol"]

            print(
                f"📡 {symbol} | "
                f"CALL={result['call_score']} | "
                f"PUT={result['put_score']} | "
                f"FINAL={result['score'] or 'WAIT'}",
                flush=True,
            )

            if (
                result["action"] != "WAIT"
                and symbol not in active_trades
            ):
                print("\n🚨 HIGH-SCORE SIGNAL", flush=True)
                print(
                    f"📌 {symbol} | "
                    f"{result['action']} | "
                    f"SCORE {result['score']} | "
                    f"ENTRY {result['price']}",
                    flush=True,
                )

                active_trades[symbol] = {
                    "symbol": symbol,
                    "action": result["action"],
                    "price": result["price"],
                    "trend_key": result["trend_key"],
                    "current_candle": 0,
                    "success_count": 0,
                    "fail_count": 0,
                    "last_checked_timestamp": result["closed_timestamp"],
                }

        print(
            f"⏳ รอรอบถัดไป {PAPER_INTERVAL_SECONDS} วินาที...",
            flush=True,
        )

        time.sleep(PAPER_INTERVAL_SECONDS)


if __name__ == "__main__":
    run_bot()
'''

dockerfile = """FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY main_v15.py /app/main_v15.py

CMD ["python", "-u", "/app/main_v15.py"]
"""

requirements = "# ไม่มี external package — ใช้ Python standard library ทั้งหมด\n"
dockerignore = """__pycache__/
*.pyc
.git/
.env
"""

readme = """# Railway Bot V24.2

ไฟล์นี้เป็นเวอร์ชันสำหรับ Deploy บน Railway โดยตรง

## สำคัญ
ห้ามนำสคริปต์ที่มี `Path("/mnt/data/...")` ไปตั้งเป็น `main_v15.py`
เพราะ `/mnt/data` เป็น path ของ environment ที่สร้างไฟล์ ZIP ไม่ใช่ path ที่ Railway ต้องใช้

## ไฟล์
- main_v15.py
- Dockerfile
- requirements.txt
- .dockerignore

## Start command
Dockerfile กำหนดไว้แล้ว:
`python -u /app/main_v15.py`

## โหมด
Paper analysis เท่านั้น ไม่ส่งคำสั่งซื้อขายจริง

## Logic
- Binance 1m candles
- ใช้ CLOSED candle สำหรับการวิเคราะห์
- Threshold 80
- Score gap 15
- ติดตามผลสูงสุด 3 closed candles
- ชนะใน candle ใด candle หนึ่ง = WIN
- ครบ 3 candles แล้วยังไม่ชนะ = LOSS

หมายเหตุ: ระบบนี้ไม่ได้รับประกันความแม่นยำ 99%
"""

(out / "main_v15.py").write_text(main_code, encoding="utf-8")
(out / "Dockerfile").write_text(dockerfile, encoding="utf-8")
(out / "requirements.txt").write_text(requirements, encoding="utf-8")
(out / ".dockerignore").write_text(dockerignore, encoding="utf-8")
(out / "README.txt").write_text(readme, encoding="utf-8")

zip_path = Path("/mnt/data/railway_bot_v24_2_DEPLOY_FIXED.zip")
with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
    for p in sorted(out.iterdir()):
        z.write(p, p.name)

print(f"สร้างแพ็กเกจใหม่เรียบร้อย: {zip_path}")
print("แก้ปัญหา /mnt/data ใน Railway แล้ว")
print("main_v15.py เป็น BOT จริง ไม่ใช่สคริปต์สร้าง ZIP")
