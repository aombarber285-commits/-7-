from pathlib import Path

code = '''import os
import json
import time
import sqlite3
import threading
import urllib.request
import urllib.parse
from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np


# ============================================================
# V15 ADAPTIVE PAPER TRAINER - FIXED V2
# REAL ORDER IS ALWAYS OFF
#
# PAPER:
# - เปิด PAPER ทุก 30 นาที
# - วัดผลหลัง 2 นาที
# - ถ้ามี V15 SIGNAL จะใช้ SIGNAL ก่อน
# - ถ้าไม่มี SIGNAL และ FORCE_PAPER_TEST=True
#   จะบังคับสร้าง PAPER TEST เพื่อทดสอบระบบ
# ============================================================

REAL_ORDER_ENABLED = False
PAPER_TRAINING_ENABLED = True

PAPER_INTERVAL_SECONDS = 30 * 60       # ทุก 30 นาที
PAPER_HORIZON_SECONDS = 2 * 60         # เป้าหมายวัดผล 2 นาที

FORCE_PAPER_TEST = True
AVOID_DUPLICATE_ACTIVE_PAIR = True
HEARTBEAT_INTERVAL_SECONDS = 5 * 60

THAI_TZ = ZoneInfo("Asia/Bangkok")

DISCORD_WEBHOOK_URL = os.getenv(
    "DISCORD_WEBHOOK_URL",
    "PASTE_YOUR_DISCORD_WEBHOOK_HERE"
)

PAIRS = [
    ("BTC/USDT", "BTCUSDT"),
    ("ETH/USDT", "ETHUSDT"),
    ("SOL/USDT", "SOLUSDT"),
    ("XRP/USDT", "XRPUSDT"),
    ("BNB/USDT", "BNBUSDT"),
    ("DOGE/USDT", "DOGEUSDT"),
]


# ============================================================
# TIME
# ============================================================

def thai_text():
    return datetime.now(THAI_TZ).strftime("%Y-%m-%d %H:%M:%S")


# ============================================================
# DISCORD
# ============================================================

def discord_enabled():
    u = (DISCORD_WEBHOOK_URL or "").strip()
    return (
        u.startswith("https://discord.com/api/webhooks/")
        or u.startswith("https://discordapp.com/api/webhooks/")
    )


def discord(msg):
    if not discord_enabled():
        print("[DISCORD OFF] Webhook URL is not configured.")
        print(msg)
        return False

    try:
        data = json.dumps({
            "content": str(msg)[:1900]
        }).encode("utf-8")

        req = urllib.request.Request(
            DISCORD_WEBHOOK_URL,
            data=data,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "V15-Paper-Trainer/Fixed-V2"
            },
            method="POST"
        )

        with urllib.request.urlopen(req, timeout=10) as r:
            ok = r.status in (200, 204)
            print(f"[DISCORD] HTTP {r.status}")
            return ok

    except Exception as e:
        print("[DISCORD ERROR]", e)
        return False


# ============================================================
# BINANCE DATA
# ============================================================

def fetch(symbol, interval="1m", limit=250):
    q = urllib.parse.urlencode({
        "symbol": symbol,
        "interval": interval,
        "limit": limit
    })

    url = "https://api.binance.com/api/v3/klines?" + q

    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "V15-Paper-Trainer/Fixed-V2"
            }
        )

        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read().decode("utf-8"))

        return [[
            int(x[0]),
            float(x[1]),
            float(x[2]),
            float(x[3]),
            float(x[4]),
            float(x[5])
        ] for x in data]

    except Exception as e:
        print(f"[BINANCE ERROR] {symbol}/{interval}: {e}")
        return []


# ============================================================
# CANDLE / INDICATORS
# ============================================================

def candle(k):
    o, h, l, c, v = k[1], k[2], k[3], k[4], k[5]
    rng = max(h - l, 1e-12)

    return {
        "o": o,
        "h": h,
        "l": l,
        "c": c,
        "v": v,
        "body_pct": abs(c - o) / rng * 100,
        "up_wick": (h - max(o, c)) / rng * 100,
        "dn_wick": (min(o, c) - l) / rng * 100,
        "green": c > o,
        "red": c < o
    }


def ema(values, n):
    if not values:
        return 0.0

    if len(values) < n:
        return values[-1]

    a = 2 / (n + 1)
    x = values[0]

    for v in values[1:]:
        x = (v - x) * a + x

    return x


def rsi(values, n=14):
    if len(values) < n + 1:
        return 50.0

    d = np.diff(values)

    g = np.where(d > 0, d, 0.0)
    l = np.where(d < 0, -d, 0.0)

    ag = np.mean(g[:n])
    al = np.mean(l[:n])

    for i in range(n, len(d)):
        ag = (ag * (n - 1) + g[i]) / n
        al = (al * (n - 1) + l[i]) / n

    if al == 0:
        return 100.0

    return 100 - 100 / (1 + ag / al)


def atr_pct(p, n=14):
    closed = p[:-1]

    if len(closed) < n + 2:
        return 0.0

    tr = []

    for i in range(1, len(closed)):
        h = closed[i]["h"]
        l = closed[i]["l"]
        pc = closed[i - 1]["c"]

        tr.append(
            max(
                h - l,
                abs(h - pc),
                abs(l - pc)
            )
        )

    return np.mean(tr[-n:]) / closed[-1]["c"] * 100


# ============================================================
# MULTI TIMEFRAME
# ============================================================

def mtf(symbol):
    out = []

    for tf in ("1h", "15m", "5m"):
        k = fetch(symbol, tf, 250)

        if len(k) < 200:
            out.append("NEUTRAL")
            continue

        p = [candle(x) for x in k]
        closes = [x["c"] for x in p[:-1]]

        c = p[-2]["c"]

        e20 = ema(closes, 20)
        e50 = ema(closes, 50)
        e200 = ema(closes, 200)

        if c > e20 > e50 > e200:
            out.append("BULLISH")

        elif c < e20 < e50 < e200:
            out.append("BEARISH")

        else:
            out.append("NEUTRAL")

    return tuple(out)


# ============================================================
# ANALYSIS
# ============================================================

def analyze(symbol, name):
    k = fetch(symbol, "1m", 250)

    if len(k) < 220:
        return {
            "name": name,
            "symbol": symbol,
            "decision": "NO TRADE",
            "grade": "C",
            "score": 0,
            "trap": 100,
            "regime": "UNKNOWN",
            "mtf": ("NEUTRAL",) * 3,
            "entry_time": 0,
            "price": 0,
            "rsi": 50.0,
            "flags": ["DATA<220"]
        }

    p = [candle(x) for x in k]
    closed = p[:-1]

    price = closed[-1]["c"]
    entry_time = k[-2][0]

    rr = rsi([x["c"] for x in closed])
    atr = atr_pct(p)

    if atr > 1.2:
        regime = "HIGH_VOLATILITY"

    elif atr < 0.25:
        regime = "LOW_VOLATILITY"

    else:
        regime = "NORMAL"

    bias = mtf(symbol)
    h, m15, m5 = bias

    score = 0.0
    trap = 0.0
    flags = []

    recent = closed[-32:-2]

    resistance = max(x["h"] for x in recent)
    support = min(x["l"] for x in recent)

    cur = closed[-1]
    action = None

    # Liquidity sweep
    sweep_call = (
        cur["l"] < support
        and cur["c"] > support
        and cur["dn_wick"] >= 28
        and cur["green"]
    )

    sweep_put = (
        cur["h"] > resistance
        and cur["c"] < resistance
        and cur["up_wick"] >= 28
        and cur["red"]
    )

    # Break of structure
    highs = [x["h"] for x in closed[-8:-1]]
    lows = [x["l"] for x in closed[-8:-1]]

    bos_call = cur["c"] > max(highs)
    bos_put = cur["c"] < min(lows)

    if sweep_call or bos_call:
        action = "CALL"
        score += 25

    elif sweep_put or bos_put:
        action = "PUT"
        score += 25

    # MTF confirmation
    if action == "CALL":
        if h == "BULLISH":
            score += 10

        if m15 == "BULLISH":
            score += 10

        if m5 == "BULLISH":
            score += 5

        if h == "BEARISH":
            trap += 45
            flags.append("1H BEARISH vs CALL")

        if rr > 72:
            trap += 30
            flags.append("RSI Overbought")

    elif action == "PUT":
        if h == "BEARISH":
            score += 10

        if m15 == "BEARISH":
            score += 10

        if m5 == "BEARISH":
            score += 5

        if h == "BULLISH":
            trap += 45
            flags.append("1H BULLISH vs PUT")

        if rr < 28:
            trap += 30
            flags.append("RSI Oversold")

    # Candle strength
    if cur["body_pct"] >= 40:
        score += 10

    # Volume
    vols = [x["v"] for x in p[-31:-2]]

    if vols:
        z = (
            cur["v"] - np.mean(vols)
        ) / max(np.std(vols), 1e-12)

        if z >= 2:
            score += 15

        elif z >= 1:
            score += 10

        elif z >= 0.5:
            score += 5

    # Trap near levels
    if action == "CALL" and resistance > price:
        distance = (resistance - price) / price * 100

        if distance < atr * 0.45:
            trap += 65
            flags.append("CALL near resistance")

    if action == "PUT" and support < price:
        distance = (price - support) / price * 100

        if distance < atr * 0.45:
            trap += 65
            flags.append("PUT near support")

    # Alignment
    aligned = (
        action == "CALL"
        and h == "BULLISH"
        and m15 == "BULLISH"
    ) or (
        action == "PUT"
        and h == "BEARISH"
        and m15 == "BEARISH"
    )

    if action and aligned and trap < 30 and score >= 70:
        grade = "A"
        decision = action

    elif action and score >= 55 and trap < 45:
        grade = "B"
        decision = "WAIT"

    else:
        grade = "C"
        decision = "NO TRADE"

    # Normal V15 signal blocks unusual volatility.
    if regime != "NORMAL":
        decision = "NO TRADE"
        grade = "C"
        trap = 100
        flags.append("REGIME BLOCK")

    return {
        "name": name,
        "symbol": symbol,
        "decision": decision,
        "grade": grade,
        "score": score,
        "trap": min(trap, 100),
        "regime": regime,
        "mtf": bias,
        "entry_time": entry_time,
        "price": price,
        "rsi": rr,
        "flags": flags
    }


# ============================================================
# SQLITE JOURNAL
# ============================================================

class Journal:
    def __init__(self):
        os.makedirs("./data", exist_ok=True)

        self.path = "./data/v15_sniper_journal.db"
        self.lock = threading.RLock()

        with sqlite3.connect(self.path) as c:
            c.execute("""
                CREATE TABLE IF NOT EXISTS paper(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT,
                    pair TEXT,
                    action TEXT,
                    entry REAL,
                    entry_time INTEGER,
                    score REAL,
                    grade TEXT,
                    trap REAL,
                    source TEXT,
                    result TEXT DEFAULT 'PENDING',
                    exit REAL,
                    pnl REAL
                )
            """)

    def add(self, r, action, source):
        with self.lock, sqlite3.connect(self.path) as c:
            cur = c.execute("""
                INSERT INTO paper
                (
                    ts,
                    pair,
                    action,
                    entry,
                    entry_time,
                    score,
                    grade,
                    trap,
                    source
                )
                VALUES(?,?,?,?,?,?,?,?,?)
            """, (
                thai_text(),
                r["name"],
                action,
                r["price"],
                r["entry_time"],
                r["score"],
                r["grade"],
                r["trap"],
                source
            ))

            return cur.lastrowid

    def finish(self, sid, result, exit_price, pnl):
        with self.lock, sqlite3.connect(self.path) as c:
            c.execute("""
                UPDATE paper
                SET result=?, exit=?, pnl=?
                WHERE id=?
            """, (
                result,
                exit_price,
                pnl,
                sid
            ))


journal = Journal()


# ============================================================
# PAPER ENGINE
# ============================================================

class Paper:
    def __init__(self):
        self.lock = threading.RLock()

        self.active = []

        # 0 = first cycle runs immediately
        self.last_cycle = 0

        self.last_heartbeat = 0
        self.n = 0

        self.session = {
            "WIN": 0,
            "LOSS": 0,
            "DRAW": 0
        }

    def active_symbol(self, symbol):
        with self.lock:
            return any(
                x["symbol"] == symbol
                for x in self.active
            )

    def best(self):
        rows = []
        all_rows = []

        for name, symbol in PAIRS:

            if (
                AVOID_DUPLICATE_ACTIVE_PAIR
                and self.active_symbol(symbol)
            ):
                continue

            try:
                r = analyze(symbol, name)

                real = r["decision"] in ("CALL", "PUT")

                # ------------------------------------------------
                # REAL V15 SIGNAL
                # ------------------------------------------------
                if real:
                    direction = r["decision"]

                # ------------------------------------------------
                # FORCE PAPER TEST
                #
                # สำคัญ:
                # เดิม FORCE_PAPER_TEST=True แต่ logic ไม่ได้
                # บังคับจริง เพราะมีการ skip regime ก่อนเลือก
                #
                # เวอร์ชันนี้เก็บ candidate ทุกตัวไว้ก่อน
                # แล้วค่อยเลือก เพื่อให้ FORCE PAPER ทำงานจริง
                # ------------------------------------------------
                else:
                    h, m15, m5 = r["mtf"]

                    if h == "BULLISH" or m15 == "BULLISH":
                        direction = "CALL"

                    elif h == "BEARISH" or m15 == "BEARISH":
                        direction = "PUT"

                    else:
                        direction = (
                            "CALL"
                            if r.get("rsi", 50) >= 50
                            else "PUT"
                        )

                rank = (
                    r["score"]
                    + (15 if real else 0)
                    - r["trap"] * 0.25
                )

                item = (
                    rank,
                    r,
                    direction,
                    real
                )

                all_rows.append(item)

                # Candidate สำหรับ NORMAL regime
                if r["regime"] == "NORMAL":
                    rows.append(item)

                print(
                    f"[CANDIDATE] {name} "
                    f"{direction} "
                    f"grade={r['grade']} "
                    f"score={r['score']:.1f} "
                    f"trap={r['trap']:.1f} "
                    f"regime={r['regime']} "
                    f"rank={rank:.1f}"
                )

            except Exception as e:
                print(
                    "[ANALYZE ERROR]",
                    name,
                    e
                )

        # --------------------------------------------------------
        # Priority 1:
        # ใช้ candidate NORMAL ก่อน
        # --------------------------------------------------------
        if rows:
            return max(
                rows,
                key=lambda x: x[0]
            )

        # --------------------------------------------------------
        # Priority 2:
        # ถ้าไม่มี NORMAL และ FORCE_PAPER_TEST=True
        # ให้บังคับเลือก candidate ที่ดีที่สุด
        # เพื่อให้ระบบทดสอบทุก 30 นาทีจริง
        # --------------------------------------------------------
        if FORCE_PAPER_TEST and all_rows:
            forced = max(
                all_rows,
                key=lambda x: x[0]
            )

            rank, r, direction, real = forced

            print(
                "[FORCE PAPER TEST] "
                f"{r['name']} "
                f"{direction} "
                f"regime={r['regime']}"
            )

            return forced

        return None

    def open_cycle(self):
        if not PAPER_TRAINING_ENABLED:
            return

        now = time.time()

        if now - self.last_cycle < PAPER_INTERVAL_SECONDS:
            return

        self.last_cycle = now

        print("=" * 60)
        print(
            "[PAPER CYCLE]",
            thai_text()
        )

        selected = self.best()

        if not selected:
            discord(
                "⚠️ **V15 PAPER CYCLE**\\n"
                "ไม่พบ Candidate สำหรับ PAPER\\n"
                f"🇹🇭 `{thai_text()}`"
            )
            return

        rank, r, direction, real = selected

        # ใช้ closed 1m candle เป็น entry
        k = fetch(
            r["symbol"],
            "1m",
            5
        )

        if len(k) < 2:
            discord(
                f"⚠️ **V15 ENTRY DATA ERROR** "
                f"`{r['name']}`\\n"
                "ดึง 1m candle ไม่พอ"
            )
            return

        entry = k[-2]

        price = entry[4]
        entry_time = entry[0]

        self.n += 1

        pid = f"PAPER-{self.n:06d}"

        source = (
            "PAPER_V15_SIGNAL"
            if real
            else "PAPER_FORCED_TEST"
        )

        sid = journal.add(
            r,
            direction,
            source
        )

        order = {
            "id": pid,
            "sid": sid,
            "name": r["name"],
            "symbol": r["symbol"],
            "action": direction,
            "entry": price,
            "entry_time": entry_time,
            "score": r["score"],
            "trap": r["trap"],
            "real": real,
            "source": source,

            # เก็บเวลาเพื่อควบคุม paper lifecycle
            "open_wall_time": time.time(),
            "expire": (
                time.time()
                + PAPER_HORIZON_SECONDS
            )
        }

        with self.lock:
            self.active.append(order)

        if real:
            mode = "🟢 V15 SIGNAL"
        else:
            mode = "🟡 FORCED TEST"

        discord(
            "🧠 **V15 PAPER TRAINING OPEN**\\n"
            "━━━━━━━━━━━━━━━━━━━━\\n"
            f"🆔 `{pid}`\\n"
            f"📌 `{r['name']}`\\n"
            f"🎯 **{direction}**\\n"
            f"🧪 {mode}\\n"
            f"💵 Entry `{price:.8f}`\\n"
            f"📊 Score `{r['score']:.1f}`\\n"
            f"🛡️ Trap `{r['trap']:.1f}`\\n"
            f"🏷️ Grade `{r['grade']}`\\n"
            f"🌐 `{r['regime']}`\\n"
            f"📈 MTF "
            f"`{r['mtf'][0]} / "
            f"{r['mtf'][1]} / "
            f"{r['mtf'][2]}`\\n"
            f"🇹🇭 `{thai_text()}`\\n\\n"
            "⏱️ **วัดผลเป้าหมาย 2 นาที**\\n"
            "❌ PAPER ONLY — REAL ORDER OFF"
        )

        print(
            "[PAPER OPEN]",
            pid,
            r["name"],
            direction
        )

    def resolve(self):
        with self.lock:
            orders = list(self.active)

        remain = []

        for o in orders:

            if time.time() < o["expire"]:
                remain.append(o)
                continue

            k = fetch(
                o["symbol"],
                "1m",
                10
            )

            if len(k) < 3:
                remain.append(o)
                continue

            # ----------------------------------------------------
            # Entry = closed candle
            #
            # Entry candle timestamp = T
            # T + 1m = candle หลัง entry
            # T + 2m = candle ที่ต้องการใช้วัดผล
            #
            # ต้องรอ candle T+2m ปิดก่อน
            # ดังนั้น wall-clock ต้อง >= T+3m
            # ----------------------------------------------------
            target_time = (
                o["entry_time"]
                + 2 * 60 * 1000
            )

            target = None

            for x in k:
                if (
                    x[0] == target_time
                    and int(time.time() * 1000)
                    >= x[0] + 60 * 1000
                ):
                    target = x
                    break

            if target is None:
                remain.append(o)
                continue

            exit_price = target[4]
            entry_price = o["entry"]

            if o["action"] == "CALL":
                pnl = (
                    (exit_price - entry_price)
                    / entry_price
                    * 100
                )
            else:
                pnl = (
                    (entry_price - exit_price)
                    / entry_price
                    * 100
                )

            if pnl > 0:
                result = "WIN"

            elif pnl < 0:
                result = "LOSS"

            else:
                result = "DRAW"

            journal.finish(
                o["sid"],
                result,
                exit_price,
                pnl
            )

            self.session[result] += 1

            total = sum(
                self.session.values()
            )

            wr = (
                self.session["WIN"]
                / total
                * 100
                if total
                else 0
            )

            if result == "WIN":
                emoji = "✅"

            elif result == "LOSS":
                emoji = "❌"

            else:
                emoji = "➖"

            discord(
                f"{emoji} **V15 PAPER RESULT**\\n"
                "━━━━━━━━━━━━━━━━━━━━\\n"
                f"🆔 `{o['id']}`\\n"
                f"📌 `{o['name']}`\\n"
                f"🎯 `{o['action']}`\\n"
                f"💵 Entry `{entry_price:.8f}`\\n"
                f"💵 Exit `{exit_price:.8f}`\\n"
                f"📈 **{result}**\\n"
                f"💰 P/L `{pnl:+.4f}%`\\n"
                f"📚 SESSION "
                f"W `{self.session['WIN']}` / "
                f"L `{self.session['LOSS']}` / "
                f"D `{self.session['DRAW']}`\\n"
                f"🎯 Session WR `{wr:.1f}%`\\n"
                f"🇹🇭 `{thai_text()}`"
            )

            print(
                "[PAPER RESULT]",
                o["id"],
                result,
                f"{pnl:+.4f}%"
            )

        with self.lock:
            self.active = remain

    def loop(self):
        print("[PAPER LOOP] RUNNING")

        while True:
            try:
                self.resolve()
                self.open_cycle()

                now = time.time()

                if (
                    now - self.last_heartbeat
                    >= HEARTBEAT_INTERVAL_SECONDS
                ):
                    self.last_heartbeat = now

                    discord(
                        "💓 **V15 SYSTEM HEARTBEAT**\\n"
                        "━━━━━━━━━━━━━━━━━━━━\\n"
                        "🟢 Process = `RUNNING`\\n"
                        "🧪 Paper = `ON`\\n"
                        "🔒 Real Order = `OFF`\\n"
                        f"📦 Active Paper = "
                        f"`{len(self.active)}`\\n"
                        f"⏱️ Paper Interval = "
                        f"`{PAPER_INTERVAL_SECONDS // 60} min`\\n"
                        f"⏱️ Paper Horizon = "
                        f"`{PAPER_HORIZON_SECONDS // 60} min`\\n"
                        f"🧪 Force Test = "
                        f"`{FORCE_PAPER_TEST}`\\n"
                        f"🇹🇭 `{thai_text()}`"
                    )

            except Exception as e:
                print(
                    "[PAPER LOOP ERROR]",
                    e
                )

                discord(
                    "🚨 **V15 LOOP ERROR**\\n"
                    f"`{str(e)[:500]}`\\n"
                    f"🇹🇭 `{thai_text()}`"
                )

            time.sleep(2)


paper = Paper()


# ============================================================
# MASTER SIGNAL WORKERS
# ============================================================

def worker(name, symbol):
    print(
        f"[WORKER ONLINE] {name}"
    )

    last = None

    while True:
        try:
            k = fetch(
                symbol,
                "1m",
                5
            )

            if not k:
                time.sleep(2)
                continue

            current = k[-1][0]

            if current != last:
                last = current

                r = analyze(
                    symbol,
                    name
                )

                print(
                    f"[MASTER] {name} "
                    f"{r['decision']} "
                    f"grade={r['grade']} "
                    f"score={r['score']:.1f} "
                    f"trap={r['trap']:.1f}"
                )

                # เฉพาะ MASTER SIGNAL คุณภาพสูง
                if (
                    r["decision"] in ("CALL", "PUT")
                    and r["grade"] in ("A", "A+")
                    and r["trap"] < 30
                ):
                    discord(
                        f"🏆 **V15 MASTER SIGNAL — "
                        f"{r['grade']}**\\n"
                        "━━━━━━━━━━━━━━━━━━━━\\n"
                        f"📌 `{name}`\\n"
                        f"🎯 **{r['decision']}**\\n"
                        f"📊 Score `{r['score']:.1f}`\\n"
                        f"🛡️ Trap `{r['trap']:.1f}`\\n"
                        f"🌐 `{r['regime']}`\\n"
                        f"📈 MTF "
                        f"`{r['mtf'][0]} / "
                        f"{r['mtf'][1]} / "
                        f"{r['mtf'][2]}`\\n"
                        f"🇹🇭 `{thai_text()}`\\n\\n"
                        "🔒 **REAL ORDER = OFF**"
                    )

        except Exception as e:
            print(
                f"[WORKER ERROR] "
                f"{name}: {e}"
            )

        time.sleep(1.5)


# ============================================================
# START
# ============================================================

def start():
    print("=" * 60)
    print(
        "🚀 V15 ADAPTIVE PAPER TRAINING - FIXED V2"
    )
    print("=" * 60)

    # Safety lock
    print(
        "REAL ORDER =",
        "OFF" if not REAL_ORDER_ENABLED else "ON"
    )

    print(
        "PAPER =",
        "ON" if PAPER_TRAINING_ENABLED else "OFF"
    )

    print(
        "PAPER INTERVAL =",
        PAPER_INTERVAL_SECONDS,
        "seconds"
    )

    print(
        "PAPER HORIZON =",
        PAPER_HORIZON_SECONDS,
        "seconds"
    )

    print(
        "FORCE PAPER TEST =",
        FORCE_PAPER_TEST
    )

    print(
        "DISCORD =",
        "CONNECTED" if discord_enabled()
        else "NOT CONFIGURED"
    )

    print(
        "THAI TIME =",
        thai_text()
    )

    # --------------------------------------------------------
    # Safety check:
    # Real order must remain OFF.
    # --------------------------------------------------------
    if REAL_ORDER_ENABLED:
        print(
            "🚨 WARNING: REAL_ORDER_ENABLED=True"
        )
    else:
        print(
            "🔒 SAFETY: REAL ORDER DISABLED"
        )

    if discord_enabled():
        ok = discord(
            "🚀 **V15 SYSTEM ONLINE — FIXED V2**\\n"
            "━━━━━━━━━━━━━━━━━━━━\\n"
            "🟢 Process = `RUNNING`\\n"
            "🧪 PAPER = `ON`\\n"
            "🧪 Every **30 minutes**\\n"
            "⏱️ Result target **2 minutes**\\n"
            f"🟡 FORCE PAPER TEST = "
            f"`{FORCE_PAPER_TEST}`\\n"
            "🔒 REAL ORDER = `OFF`\\n"
            "📨 Discord = `CONNECTED`\\n"
            f"🇹🇭 `{thai_text()}`"
        )

        print(
            "[DISCORD START]",
            ok
        )

    else:
        print(
            "⚠️ DISCORD WEBHOOK NOT CONFIGURED"
        )
        print(
            "Set DISCORD_WEBHOOK_URL "
            "in Railway Variables."
        )

    # Paper engine
    threading.Thread(
        target=paper.loop,
        daemon=True,
        name="paper-engine"
    ).start()

    # Master workers
    for name, symbol in PAIRS:
        threading.Thread(
            target=worker,
            args=(name, symbol),
            daemon=True,
            name=f"worker-{symbol}"
        ).start()

        time.sleep(0.3)


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":
    start()

    while True:
        time.sleep(3600)
'''

path = Path("/mnt/data/v15_adaptive_paper_training_FIXED_V2.py")
path.write_text(code, encoding="utf-8")

print(f"สร้างไฟล์สำเร็จ: {path}")
print("แก้หลัก: FORCE_PAPER_TEST ทำงานจริง, ถ้าไม่มี NORMAL candidate จะเลือก candidate ที่ดีที่สุดเพื่อทดสอบ PAPER, REAL ORDER ยัง OFF")
