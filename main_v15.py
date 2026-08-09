from pathlib import Path
import zipfile

project = Path("/mnt/data/railway_bot_v24")
project.mkdir(exist_ok=True)

main_code = r'''import time
import json
import os
import urllib.request
from datetime import datetime

# ==========================================
# ⚙️ CONFIGURATION
# ==========================================
SYMBOLS = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'XRPUSDT', 'BNBUSDT', 'EURUSDT']
PAPER_INTERVAL_SECONDS = 180
MIN_SCORE_THRESHOLD = 80.0
MIN_SCORE_GAP = 15.0
MEMORY_FILE = 'bot_memory_v24_3candles.json'


def fetch_binance_klines(symbol, limit=100):
    try:
        url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval=1m&limit={limit}"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=15) as response:
            data = json.loads(response.read().decode())
            df = []
            for row in data:
                df.append({
                    'timestamp': row[0],
                    'open': float(row[1]),
                    'high': float(row[2]),
                    'low': float(row[3]),
                    'close': float(row[4]),
                    'volume': float(row[5])
                })
            return df
    except Exception as e:
        print(f"[BINANCE ERROR] {symbol}: {e}", flush=True)
        return None


# ==========================================
# 🧠 STRICT BRAIN & 3-CANDLE SYSTEM
# ==========================================
def init_strict_memory():
    if os.path.exists(MEMORY_FILE):
        try:
            with open(MEMORY_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"[MEMORY] โหลดไฟล์เดิมไม่ได้: {e}", flush=True)

    print(">> 🧠 กำลังเริ่มต้นระบบหน่วยความจำแบบ 3 แท่งเทียน / 3 โอกาส...", flush=True)
    memory = {
        "stats": {
            "total_trades": 0, "wins": 0, "losses": 0, "draws": 0,
            "win_streak": 0, "loss_streak": 0,
            "best_win_streak": 0, "best_loss_streak": 0
        },
        "direction_memory": {},
        "recent_logs": []
    }
    save_memory(memory)
    return memory


def save_memory(memory):
    with open(MEMORY_FILE, 'w', encoding='utf-8') as f:
        json.dump(memory, f, indent=4, ensure_ascii=False)


def record_trade_result(memory, symbol, action, trend_key, result):
    s = memory["stats"]
    s["total_trades"] += 1

    if result == "WIN":
        s["wins"] += 1
        s["win_streak"] += 1
        s["loss_streak"] = 0
        s["best_win_streak"] = max(s["best_win_streak"], s["win_streak"])
    elif result == "LOSS":
        s["losses"] += 1
        s["loss_streak"] += 1
        s["win_streak"] = 0
        s["best_loss_streak"] = max(s["best_loss_streak"], s["loss_streak"])
    else:
        s["draws"] += 1

    key = f"{symbol}|{action}|{trend_key}"
    row = memory["direction_memory"].setdefault(
        key, {"WIN": 0, "LOSS": 0, "DRAW": 0}
    )
    row[result] = int(row.get(result, 0)) + 1

    save_memory(memory)

    decided = s["wins"] + s["losses"]
    wr = (s["wins"] / decided * 100.0) if decided else 0.0

    print("\n==================================================", flush=True)
    print(f"📊 [สถิติรวมรอบ 3 แท่งเทียน] {symbol} | {action} => {result}", flush=True)
    print(f"ยอดรวมทั้งหมด: {s['total_trades']} | ชนะ: {s['wins']} | แพ้: {s['losses']}", flush=True)
    print(f"Win Rate: {wr:.2f}% | Win Streak: {s['win_streak']} | Loss Streak: {s['loss_streak']}", flush=True)
    print("==================================================", flush=True)


# ==========================================
# 📊 PURE PYTHON TECHNICAL CALCULATIONS
# ==========================================
def calculate_indicators(df):
    closes = [row['close'] for row in df]
    highs = [row['high'] for row in df]
    lows = [row['low'] for row in df]
    volumes = [row['volume'] for row in df]

    def calc_ema(data, span):
        multiplier = 2 / (span + 1)
        ema = data[0]
        for price in data[1:]:
            ema = (price * multiplier) + (ema * (1 - multiplier))
        return ema

    ema20 = calc_ema(closes[-20:], 20)
    ema50 = calc_ema(closes[-50:], 50)
    ema200 = calc_ema(closes, min(len(closes), 200))

    gains, losses = 0, 0
    for i in range(-14, 0):
        diff = closes[i] - closes[i - 1]
        if diff > 0:
            gains += diff
        else:
            losses -= diff

    avg_gain = gains / 14
    avg_loss = losses / 14
    rs = (avg_gain / avg_loss) if avg_loss > 0 else 100
    rsi = 100 - (100 / (1 + rs))

    macd_val = closes[-1] - closes[-12]
    macd_sig = macd_val * 0.8

    atr = highs[-1] - lows[-1]
    momentum = closes[-1] - closes[-5]
    vol_mean = sum(volumes[-10:]) / 10
    volume_ratio = (volumes[-1] / vol_mean) if vol_mean > 0 else 1.0

    hh = highs[-1] > highs[-2] and highs[-2] > highs[-3]
    hl = lows[-1] > lows[-2] and lows[-2] > lows[-3]
    lh = highs[-1] < highs[-2] and highs[-2] < highs[-3]
    ll = lows[-1] < lows[-2] and lows[-2] < lows[-3]

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
        "ll": ll
    }


def evaluate_99_strict_brain(ind, symbol, memory):
    call_scores, put_scores = {}, {}
    price = ind["price"]

    trend_score = (
        90 if ind["ema20"] > ind["ema50"] > ind["ema200"]
        else (-90 if ind["ema20"] < ind["ema50"] < ind["ema200"] else 0)
    )

    call_scores["TREND"] = max(0, min(100, (trend_score + 100) / 2))
    put_scores["TREND"] = max(0, min(100, ((-trend_score) + 100) / 2))

    call_scores["EMA"] = 90 if price > ind["ema20"] else 30
    put_scores["EMA"] = 90 if price < ind["ema20"] else 30

    call_scores["MACD"] = 85 if ind["macd_val"] > ind["macd_sig"] else 35
    put_scores["MACD"] = 85 if ind["macd_val"] < ind["macd_sig"] else 35

    call_scores["ADX"] = 75 if ind["atr"] > 0 else 50
    put_scores["ADX"] = 75 if ind["atr"] > 0 else 50

    call_scores["MOMENTUM"] = 85 if ind["momentum"] > 0 else 30
    put_scores["MOMENTUM"] = 85 if ind["momentum"] < 0 else 30

    vol_score = min(100, ind["volume_ratio"] * 50)
    call_scores["VOLUME"] = vol_score
    put_scores["VOLUME"] = vol_score

    if ind["hh"] and ind["hl"]:
        call_scores["MTF"], put_scores["MTF"] = 90, 20
    elif ind["lh"] and ind["ll"]:
        call_scores["MTF"], put_scores["MTF"] = 20, 90
    else:
        call_scores["MTF"], put_scores["MTF"] = 50, 50

    c_hist = memory["direction_memory"].get(
        f"{symbol}|CALL|BULL", {"WIN": 1, "LOSS": 1}
    )
    p_hist = memory["direction_memory"].get(
        f"{symbol}|PUT|BEAR", {"WIN": 1, "LOSS": 1}
    )

    call_den = c_hist["WIN"] + c_hist["LOSS"]
    put_den = p_hist["WIN"] + p_hist["LOSS"]

    call_scores["MEMORY"] = c_hist["WIN"] / call_den * 100
    put_scores["MEMORY"] = p_hist["WIN"] / put_den * 100

    if ind["rsi"] < 35:
        call_scores["PATTERN"], put_scores["PATTERN"] = 85, 30
    elif ind["rsi"] > 65:
        call_scores["PATTERN"], put_scores["PATTERN"] = 30, 85
    else:
        call_scores["PATTERN"], put_scores["PATTERN"] = 50, 50

    call_total = sum(call_scores.values()) / len(call_scores)
    put_total = sum(put_scores.values()) / len(put_scores)

    return (
        call_scores,
        put_scores,
        round(call_total, 2),
        round(put_total, 2)
    )


def analyze_market_strict(symbol, memory):
    df = fetch_binance_klines(symbol, 100)
    if not df:
        return None

    ind = calculate_indicators(df)
    c_scores, p_scores, call_final, put_final = evaluate_99_strict_brain(
        ind, symbol, memory
    )

    action = "WAIT"
    chosen_score = 0

    if call_final >= MIN_SCORE_THRESHOLD and call_final - put_final >= MIN_SCORE_GAP:
        action = "CALL"
        chosen_score = call_final
    elif put_final >= MIN_SCORE_THRESHOLD and put_final - call_final >= MIN_SCORE_GAP:
        action = "PUT"
        chosen_score = put_final

    trend_key = "BULL" if ind["ema20"] > ind["ema50"] else "BEAR"

    return {
        "symbol": symbol,
        "action": action,
        "score": chosen_score,
        "call_score": call_final,
        "put_score": put_final,
        "trend_key": trend_key,
        "price": ind["price"]
    }


def scan_all_symbols(memory):
    results = []
    for sym in SYMBOLS:
        res = analyze_market_strict(symbol=sym, memory=memory)
        if res:
            results.append(res)
    return results


# ==========================================
# 🚀 MAIN LOOP
# ==========================================
def run_bot():
    print("==================================================", flush=True)
    print("🤖 V24.2 MULTI-SYMBOL 3-CANDLE / 3-CHANCE SYSTEM", flush=True)
    print("ประเมินครบทุกคู่เงิน | วัดผล 3 แท่งเทียน | 1 ใน 3 ชนะนับชนะ", flush=True)
    print("==================================================", flush=True)

    memory = init_strict_memory()
    active_trades = {}

    while True:
        current_time = datetime.now().strftime("%H:%M:%S")
        print(
            f"\n[🔍 {current_time}] กำลังสแกนและประเมินทุกคู่เงิน "
            f"(ครบทุก 3 นาที)...",
            flush=True
        )

        symbols_to_remove = []

        for sym, trade in active_trades.items():
            trade['current_candle'] += 1
            df_check = fetch_binance_klines(sym, 5)

            if df_check:
                current_price = df_check[-1]['close']
                entry_price = trade['price']
                action = trade['action']

                if action == "CALL":
                    candle_win = current_price > entry_price
                else:
                    candle_win = current_price < entry_price

                if candle_win:
                    trade['success_count'] += 1
                    print(
                        f"   🟢 [{sym}] แท่งที่ {trade['current_candle']}/3 ชนะ! "
                        f"(ราคาปิด {current_price} vs เข้า {entry_price})",
                        flush=True
                    )
                else:
                    trade['fail_count'] += 1
                    print(
                        f"   🔴 [{sym}] แท่งที่ {trade['current_candle']}/3 แพ้ "
                        f"(ราคาปิด {current_price} vs เข้า {entry_price})",
                        flush=True
                    )

                if trade['current_candle'] >= 3:
                    print(
                        f"🏁 สิ้นสุดการประเมิน 3 แท่งเทียนของคู่ {sym} ({action})",
                        flush=True
                    )
                    print(
                        f"   - ชนะสะสม: {trade['success_count']} ครั้ง | "
                        f"แพ้สะสม: {trade['fail_count']} ครั้ง",
                        flush=True
                    )

                    if trade['success_count'] >= 1:
                        final_result = "WIN"
                        print(
                            "   👉 ผลลัพธ์สรุป: WIN (เพราะมีชนะอย่างน้อย 1 แท่ง)",
                            flush=True
                        )
                    else:
                        final_result = "LOSS"
                        print(
                            "   👉 ผลลัพธ์สรุป: LOSS (เพราะแพ้ครบทั้ง 3 แท่ง)",
                            flush=True
                        )

                    record_trade_result(
                        memory,
                        sym,
                        action,
                        trade['trend_key'],
                        final_result
                    )
                    symbols_to_remove.append(sym)

        for sym in symbols_to_remove:
            del active_trades[sym]

        market_evaluations = scan_all_symbols(memory)

        for res in market_evaluations:
            sym = res['symbol']
            action = res['action']
            score = res['score']

            if (
                action != "WAIT"
                and score >= MIN_SCORE_THRESHOLD
                and sym not in active_trades
            ):
                print(
                    "\n🚨 [แจ้งเตือนสัญญาณด่วน!] "
                    "พบโอกาสความแม่นยำสูง (ใกล้เคียง 90%)",
                    flush=True
                )
                print(
                    f"   📌 คู่เงิน: {sym} | สัญญาณ: {action} | "
                    f"คะแนน: {score} "
                    f"(CALL: {res['call_score']}, PUT: {res['put_score']})",
                    flush=True
                )
                print(
                    "   ⏳ เริ่มต้นนับถอยหลังติดตาม 3 แท่งเทียน (3 โอกาส)...",
                    flush=True
                )

                active_trades[sym] = {
                    'symbol': sym,
                    'action': action,
                    'price': res['price'],
                    'trend_key': res['trend_key'],
                    'current_candle': 0,
                    'success_count': 0,
                    'fail_count': 0
                }

        time.sleep(PAPER_INTERVAL_SECONDS)


if __name__ == "__main__":
    run_bot()
'''

dockerfile = r'''FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY main_v15.py /app/main_v15.py

CMD ["python", "-u", "main_v15.py"]
'''

requirements = r'''# This bot uses only Python standard-library modules.
# No third-party packages are required.
'''

dockerignore = r'''__pycache__
*.pyc
*.pyo
.git
.gitignore
.env
'''

(project / "main_v15.py").write_text(main_code, encoding="utf-8")
(project / "Dockerfile").write_text(dockerfile, encoding="utf-8")
(project / "requirements.txt").write_text(requirements, encoding="utf-8")
(project / ".dockerignore").write_text(dockerignore, encoding="utf-8")

zip_path = Path("/mnt/data/railway_bot_v24_ready.zip")
with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
    for p in project.iterdir():
        z.write(p, p.name)

print(f"สร้างชุดไฟล์พร้อม Railway แล้ว: {zip_path}")
print("ไฟล์: main_v15.py, Dockerfile, requirements.txt, .dockerignore")
