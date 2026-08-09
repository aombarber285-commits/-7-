from pathlib import Path
import zipfile

project = Path("/mnt/data/railway_bot_v24_fixed")
project.mkdir(parents=True, exist_ok=True)

main_code = r'''import time
import json
import os
import urllib.request
from datetime import datetime

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

        return [
            {
                'timestamp': row[0],
                'open': float(row[1]),
                'high': float(row[2]),
                'low': float(row[3]),
                'close': float(row[4]),
                'volume': float(row[5])
            }
            for row in data
        ]
    except Exception as e:
        print(f"[BINANCE ERROR] {symbol}: {e}", flush=True)
        return None


def save_memory(memory):
    with open(MEMORY_FILE, 'w', encoding='utf-8') as f:
        json.dump(memory, f, indent=4, ensure_ascii=False)


def init_strict_memory():
    if os.path.exists(MEMORY_FILE):
        try:
            with open(MEMORY_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"[MEMORY] โหลดไฟล์เดิมไม่ได้: {e}", flush=True)

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
    print(">> 🧠 เริ่มต้นระบบหน่วยความจำ 3 แท่งเทียน / 3 โอกาส", flush=True)
    return memory


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
    print(f"📊 {symbol} | {action} => {result}", flush=True)
    print(f"ยอดรวม: {s['total_trades']} | ชนะ: {s['wins']} | แพ้: {s['losses']}", flush=True)
    print(f"Win Rate: {wr:.2f}% | Win Streak: {s['win_streak']} | Loss Streak: {s['loss_streak']}", flush=True)
    print("==================================================", flush=True)


def calculate_indicators(df):
    if len(df) < 50:
        return None

    closes = [x['close'] for x in df]
    highs = [x['high'] for x in df]
    lows = [x['low'] for x in df]
    volumes = [x['volume'] for x in df]

    def calc_ema(data, span):
        multiplier = 2 / (span + 1)
        ema = data[0]
        for price in data[1:]:
            ema = price * multiplier + ema * (1 - multiplier)
        return ema

    ema20 = calc_ema(closes[-20:], 20)
    ema50 = calc_ema(closes[-50:], 50)
    ema200 = calc_ema(closes, min(len(closes), 200))

    gains = losses = 0.0
    for i in range(-14, 0):
        diff = closes[i] - closes[i - 1]
        if diff > 0:
            gains += diff
        else:
            losses -= diff

    avg_gain = gains / 14
    avg_loss = losses / 14
    rs = avg_gain / avg_loss if avg_loss > 0 else 100
    rsi = 100 - (100 / (1 + rs))

    macd_val = closes[-1] - closes[-12]
    macd_sig = macd_val * 0.8

    atr = highs[-1] - lows[-1]
    momentum = closes[-1] - closes[-5]
    vol_mean = sum(volumes[-10:]) / 10
    volume_ratio = volumes[-1] / vol_mean if vol_mean > 0 else 1.0

    hh = highs[-1] > highs[-2] and highs[-2] > highs[-3]
    hl = lows[-1] > lows[-2] and lows[-2] > lows[-3]
    lh = highs[-1] < highs[-2] and highs[-2] < highs[-3]
    ll = lows[-1] < lows[-2] and lows[-2] < lows[-3]

    return {
        "price": closes[-1], "ema20": ema20, "ema50": ema50, "ema200": ema200,
        "rsi": rsi, "macd_val": macd_val, "macd_sig": macd_sig,
        "atr": atr, "momentum": momentum, "volume_ratio": volume_ratio,
        "hh": hh, "hl": hl, "lh": lh, "ll": ll
    }


def evaluate_99_strict_brain(ind, symbol, memory):
    call_scores, put_scores = {}, {}

    trend_score = (
        90 if ind["ema20"] > ind["ema50"] > ind["ema200"]
        else (-90 if ind["ema20"] < ind["ema50"] < ind["ema200"] else 0)
    )

    call_scores["TREND"] = max(0, min(100, (trend_score + 100) / 2))
    put_scores["TREND"] = max(0, min(100, ((-trend_score) + 100) / 2))

    call_scores["EMA"] = 90 if ind["price"] > ind["ema20"] else 30
    put_scores["EMA"] = 90 if ind["price"] < ind["ema20"] else 30

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

    call_den = c_hist.get("WIN", 0) + c_hist.get("LOSS", 0)
    put_den = p_hist.get("WIN", 0) + p_hist.get("LOSS", 0)

    call_scores["MEMORY"] = c_hist.get("WIN", 0) / call_den * 100 if call_den else 50
    put_scores["MEMORY"] = p_hist.get("WIN", 0) / put_den * 100 if put_den else 50

    if ind["rsi"] < 35:
        call_scores["PATTERN"], put_scores["PATTERN"] = 85, 30
    elif ind["rsi"] > 65:
        call_scores["PATTERN"], put_scores["PATTERN"] = 30, 85
    else:
        call_scores["PATTERN"], put_scores["PATTERN"] = 50, 50

    call_total = sum(call_scores.values()) / len(call_scores)
    put_total = sum(put_scores.values()) / len(put_scores)

    return call_scores, put_scores, round(call_total, 2), round(put_total, 2)


def analyze_market_strict(symbol, memory):
    df = fetch_binance_klines(symbol, 100)
    if not df:
        return None

    ind = calculate_indicators(df)
    if not ind:
        return None

    c_scores, p_scores, call_final, put_final = evaluate_99_strict_brain(
        ind, symbol, memory
    )

    action = "WAIT"
    chosen_score = 0

    if call_final >= MIN_SCORE_THRESHOLD and call_final - put_final >= MIN_SCORE_GAP:
        action, chosen_score = "CALL", call_final
    elif put_final >= MIN_SCORE_THRESHOLD and put_final - call_final >= MIN_SCORE_GAP:
        action, chosen_score = "PUT", put_final

    trend_key = "BULL" if ind["ema20"] > ind["ema50"] else "BEAR"

    return {
        "symbol": symbol, "action": action, "score": chosen_score,
        "call_score": call_final, "put_score": put_final,
        "trend_key": trend_key, "price": ind["price"]
    }


def scan_all_symbols(memory):
    results = []
    for sym in SYMBOLS:
        res = analyze_market_strict(sym, memory)
        if res:
            results.append(res)
    return results


def run_bot():
    print("==================================================", flush=True)
    print("🤖 V24.2 MULTI-SYMBOL 3-CANDLE / 3-CHANCE SYSTEM", flush=True)
    print("==================================================", flush=True)

    memory = init_strict_memory()
    active_trades = {}

    while True:
        current_time = datetime.now().strftime("%H:%M:%S")
        print(f"\n[🔍 {current_time}] กำลังสแกนทุกคู่เงิน...", flush=True)

        symbols_to_remove = []

        for sym, trade in list(active_trades.items()):
            trade['current_candle'] += 1
            df_check = fetch_binance_klines(sym, 5)

            if df_check:
                current_price = df_check[-1]['close']
                entry_price = trade['price']
                action = trade['action']

                candle_win = (
                    current_price > entry_price
                    if action == "CALL"
                    else current_price < entry_price
                )

                if candle_win:
                    trade['success_count'] += 1
                    print(f"🟢 [{sym}] แท่ง {trade['current_candle']}/3 ชนะ", flush=True)
                else:
                    trade['fail_count'] += 1
                    print(f"🔴 [{sym}] แท่ง {trade['current_candle']}/3 แพ้", flush=True)

                if trade['current_candle'] >= 3:
                    final_result = "WIN" if trade['success_count'] >= 1 else "LOSS"
                    record_trade_result(
                        memory, sym, action, trade['trend_key'], final_result
                    )
                    symbols_to_remove.append(sym)

        for sym in symbols_to_remove:
            del active_trades[sym]

        for res in scan_all_symbols(memory):
            sym = res['symbol']
            if (
                res['action'] != "WAIT"
                and res['score'] >= MIN_SCORE_THRESHOLD
                and sym not in active_trades
            ):
                print("\n🚨 [HIGH-SCORE SIGNAL]", flush=True)
                print(
                    f"📌 {sym} | {res['action']} | SCORE {res['score']} "
                    f"(CALL {res['call_score']} / PUT {res['put_score']})",
                    flush=True
                )

                active_trades[sym] = {
                    'symbol': sym, 'action': res['action'], 'price': res['price'],
                    'trend_key': res['trend_key'], 'current_candle': 0,
                    'success_count': 0, 'fail_count': 0
                }

        time.sleep(PAPER_INTERVAL_SECONDS)


if __name__ == "__main__":
    run_bot()
'''

dockerfile = '''FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY main_v15.py /app/main_v15.py
COPY requirements.txt /app/requirements.txt

RUN python -m pip install --no-cache-dir -r /app/requirements.txt

CMD ["python", "-u", "/app/main_v15.py"]
'''

requirements = '# Standard library only\n'
dockerignore = '__pycache__/\n*.pyc\n.git/\n.env\n'

(project / "main_v15.py").write_text(main_code, encoding="utf-8")
(project / "Dockerfile").write_text(dockerfile, encoding="utf-8")
(project / "requirements.txt").write_text(requirements, encoding="utf-8")
(project / ".dockerignore").write_text(dockerignore, encoding="utf-8")

zip_path = Path("/mnt/data/railway_bot_v24_fixed.zip")
with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
    for p in project.iterdir():
        z.write(p, p.name)

print(f"สร้างไฟล์แก้ไขแล้ว: {zip_path}")
print("ภายในมี main_v15.py + Dockerfile + requirements.txt + .dockerignore")
