# -*- coding: utf-8 -*-
"""
SIGZY / TRADIFY MULTI-CHART MONITOR (WITH COUNTDOWN & ARROW SIGNALS)
Python 3.10 / Railway Ready
"""

import os
import json
import time
import requests
import gc
from datetime import datetime, timezone, timedelta
from threading import Thread, Lock

import yfinance as yf
from flask import Flask, render_template_string, jsonify

# ============================================================
# CONFIG
# ============================================================

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
PORT = int(os.getenv("PORT", "8080"))
SCAN_SECONDS = int(os.getenv("SCAN_SECONDS", "30"))

SYMBOL_MAP = {
    "EUR/USD": "EURUSD=X",
    "GBP/USD": "GBPUSD=X",
    "USD/JPY": "JPY=X",
    "AUD/USD": "AUDUSD=X",
    "EUR/GBP": "EURGBP=X",
    "GBP/JPY": "GBPJPY=X",
}

SYMBOLS = list(SYMBOL_MAP.keys())
LOCK = Lock()

ACTIVE_SIGNALS = {} # เก็บสัญญาณปัจจุบันเพื่อแสดงบนกราฟ

# ============================================================
# HELPERS & DATA
# ============================================================

def now_utc():
    return datetime.now(timezone.utc)

def send_discord(message):
    if not DISCORD_WEBHOOK_URL: return
    try:
        requests.post(DISCORD_WEBHOOK_URL, json={"content": message[:1900]}, timeout=5)
    except Exception:
        pass

def get_candles(symbol):
    ticker_symbol = SYMBOL_MAP.get(symbol, symbol)
    try:
        ticker = yf.Ticker(ticker_symbol)
        df = ticker.history(period="1d", interval="5m", auto_adjust=False)
        if df.empty: return []

        candles = []
        for idx, row in df.iterrows():
            ts = idx.to_pydatetime()
            if ts.tzinfo is None: ts = ts.replace(tzinfo=timezone.utc)
            candles.append({
                "time": int(ts.timestamp()) + 25200, # Adjust to GMT+7
                "open": float(row["Open"]),
                "high": float(row["High"]),
                "low": float(row["Low"]),
                "close": float(row["Close"]),
            })
        return candles[-25:] # ดึง 25 แท่งล่าสุด
    except Exception:
        return []

# ============================================================
# SCANNER LOGIC (ตัวอย่างการจับจุดเข้า)
# ============================================================

def scan_market():
    while True:
        try:
            for sym in SYMBOLS:
                candles = get_candles(sym)
                if len(candles) < 5: continue
                
                c = candles[-1]
                prev = candles[-2]
                
                # ตัวอย่าง ตรรกะจับจุดเข้า (ปรับแต่งกลยุทธ์ตามต้องการได้)
                direction = None
                if c["close"] > prev["high"]:
                    direction = "CALL"
                elif c["close"] < prev["low"]:
                    direction = "PUT"
                
                if direction:
                    with LOCK:
                        # บันทึกจุดเข้าเพื่อวาดลูกศร
                        ACTIVE_SIGNALS[sym] = {
                            "direction": direction,
                            "price": c["close"],
                            "time": c["time"]
                        }
        except Exception as e:
            print(f"Scanner error: {e}")
        time.sleep(SCAN_SECONDS)

# ============================================================
# DASHBOARD UI (GRID 6 CHARTS LIKE SIGZY / TRADIFY)
# ============================================================

HTML_LAYOUT = """
<!DOCTYPE html>
<html lang="th">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>TRADIFY / SIGZY Signal Monitor</title>
    <script src="https://unpkg.com/lightweight-charts@4.2.0/dist/lightweight-charts.standalone.production.js"></script>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { background: #0a0d14; color: #fff; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; height: 100vh; overflow: hidden; }
        .grid-container {
            display: grid;
            grid-template-columns: repeat(3, 1fr);
            grid-template-rows: repeat(2, 1fr);
            gap: 6px;
            padding: 6px;
            height: 100vh;
        }
        .chart-card {
            background: #111522;
            border: 1px solid #1e2436;
            border-radius: 6px;
            display: flex;
            flex-direction: column;
            position: relative;
            overflow: hidden;
        }
        .chart-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 6px 12px;
            background: #161b2a;
            border-bottom: 1px solid #1e2436;
        }
        .symbol-title { font-weight: bold; font-size: 15px; color: #00e676; }
        .timer-box { font-size: 14px; font-weight: bold; color: #ffeb3b; background: rgba(0,0,0,0.3); padding: 2px 8px; border-radius: 4px; }
        .chart-body { flex: 1; width: 100%; height: 100%; }
        .signal-tag {
            position: absolute;
            top: 45px;
            left: 50%;
            transform: translateX(-50%);
            z-index: 10;
            font-size: 14px;
            font-weight: bold;
            padding: 4px 12px;
            border-radius: 20px;
            display: none;
        }
        .call-tag { background: #00c853; color: #fff; }
        .put-tag { background: #d50000; color: #fff; }
    </style>
</head>
<body>

<div class="grid-container" id="grid"></div>

<script>
    const symbols = ["EUR/USD", "AUD/USD", "EUR/JPY", "EUR/GBP", "GBP/JPY", "GBP/USD"];
    const charts = {};
    const series = {};

    function createGrid() {
        const container = document.getElementById('grid');
        container.innerHTML = symbols.map(s => {
            const id = s.replace('/', '_');
            return `
                <div class="chart-card">
                    <div class="chart-header">
                        <span class="symbol-title">${s}</span>
                        <span class="timer-box" id="timer-${id}">00:00</span>
                    </div>
                    <div class="signal-tag" id="tag-${id}"></div>
                    <div class="chart-body" id="chart-${id}"></div>
                </div>
            `;
        }).join('');

        symbols.forEach(s => {
            const id = s.replace('/', '_');
            const elem = document.getElementById(`chart-${id}`);
            const chart = LightweightCharts.createChart(elem, {
                layout: { background: { color: '#111522' }, textColor: '#d1d4dc' },
                grid: { vertLines: { color: '#1a2030' }, horzLines: { color: '#1a2030' } },
                timeScale: { visible: true, timeVisible: true, secondsVisible: false },
                rightPriceScale: { borderVisible: false }
            });

            const candleSeries = chart.addCandlestickSeries({
                upColor: '#00e676', downColor: '#ff5252',
                borderUpColor: '#00e676', borderDownColor: '#ff5252',
                wickUpColor: '#00e676', wickDownColor: '#ff5252',
            });

            charts[id] = chart;
            series[id] = candleSeries;
        });
    }

    function updateCountdown() {
        const now = new Date();
        const seconds = now.getSeconds();
        const minutes = now.getMinutes();
        
        // นับถอยหลังแท่ง 5 นาที (M5)
        const remMin = 4 - (minutes % 5);
        const remSec = 59 - seconds;

        const mStr = String(remMin).padStart(2, '0');
        const sStr = String(remSec).padStart(2, '0');
        const timeText = `${mStr}:${sStr}`;

        symbols.forEach(s => {
            const id = s.replace('/', '_');
            const el = document.getElementById(`timer-${id}`);
            if (el) el.innerText = timeText;
        });
    }

    async function refreshData() {
        try {
            const res = await fetch('/api/data');
            const json = await res.json();

            symbols.forEach(s => {
                const id = s.replace('/', '_');
                if (json.candles[s] && series[id]) {
                    series[id].setData(json.candles[s]);

                    // เช็คจุดเข้าและวาดลูกศร
                    const sig = json.signals[s];
                    const tagEl = document.getElementById(`tag-${id}`);

                    if (sig) {
                        const isCall = sig.direction === 'CALL';
                        const lastCandle = json.candles[s][json.candles[s].length - 1];

                        series[id].setMarkers([{
                            time: lastCandle.time,
                            position: isCall ? 'belowBar' : 'aboveBar',
                            color: isCall ? '#00e676' : '#ff5252',
                            shape: isCall ? 'arrowUp' : 'arrowDown',
                            text: sig.direction
                        }]);

                        tagEl.className = `signal-tag ${isCall ? 'call-tag' : 'put-tag'}`;
                        tagEl.innerText = `SIGNAL: ${sig.direction}`;
                        tagEl.style.display = 'block';
                    } else {
                        series[id].setMarkers([]);
                        tagEl.style.display = 'none';
                    }

                    charts[id].timeScale().fitContent();
                }
            });
        } catch (e) {
            console.error(e);
        }
    }

    createGrid();
    setInterval(updateCountdown, 1000);
    setInterval(refreshData, 10000);
    refreshData();
</script>

</body>
</html>
"""

# ============================================================
# SERVER ROUTES
# ============================================================

app = Flask(__name__)

@app.route("/")
def index():
    return render_template_string(HTML_LAYOUT)

@app.route("/api/data")
def api_data():
    candles_data = {}
    for sym in SYMBOLS:
        candles_data[sym] = get_candles(sym)

    with LOCK:
        sig_data = dict(ACTIVE_SIGNALS)

    return jsonify({"candles": candles_data, "signals": sig_data})

if __name__ == "__main__":
    Thread(target=scan_market, daemon=True).start()
    app.run(host="0.0.0.0", port=PORT, debug=False)
