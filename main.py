from pathlib import Path

# Create the REAL executable main.py from the bot source the user supplied earlier.
# The GitHub file must contain the bot itself, not a Python string/wrapper that writes another file.
bot_code = r'''import os
import json
import time
import sqlite3
import threading
import urllib.request
import urllib.parse
from datetime import datetime
from zoneinfo import ZoneInfo

try:
    import numpy as np
except Exception as e:
    print("[FATAL] numpy import failed:", e)
    raise

REAL_ORDER_ENABLED = False
PAPER_TRAINING_ENABLED = True
PAPER_INTERVAL_SECONDS = 30 * 60
PAPER_HORIZON_SECONDS = 2 * 60
FORCE_PAPER_TEST = True
AVOID_DUPLICATE_ACTIVE_PAIR = True
HEARTBEAT_INTERVAL_SECONDS = 5 * 60

THAI_TZ = ZoneInfo("Asia/Bangkok")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "").strip()

PAIRS = [
    ("BTC/USDT", "BTCUSDT"),
    ("ETH/USDT", "ETHUSDT"),
    ("SOL/USDT", "SOLUSDT"),
    ("XRP/USDT", "XRPUSDT"),
    ("BNB/USDT", "BNBUSDT"),
    ("DOGE/USDT", "DOGEUSDT"),
]


def thai_text():
    return datetime.now(THAI_TZ).strftime("%Y-%m-%d %H:%M:%S")


def discord_enabled():
    u = DISCORD_WEBHOOK_URL
    return (
        u.startswith("https://discord.com/api/webhooks/")
        or u.startswith("https://discordapp.com/api/webhooks/")
    )


def discord(msg):
    if not discord_enabled():
        print("[DISCORD OFF] DISCORD_WEBHOOK_URL is missing/invalid")
        print(msg)
        return False

    try:
        payload = json.dumps({"content": str(msg)[:1900]}).encode("utf-8")
        req = urllib.request.Request(
            DISCORD_WEBHOOK_URL,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "V15-Paper-Trainer/Railway-Fixed",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            ok = r.status in (200, 204)
            print(f"[DISCORD] HTTP {r.status} OK={ok}")
            return ok
    except Exception as e:
        print("[DISCORD ERROR]", repr(e))
        return False


def fetch(symbol, interval="1m", limit=250):
    q = urllib.parse.urlencode({
        "symbol": symbol,
        "interval": interval,
        "limit": limit,
    })
    url = "https://api.binance.com/api/v3/klines?" + q

    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "V15-Paper-Trainer/Railway-Fixed"},
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read().decode("utf-8"))

        return [
            [
                int(x[0]),
                float(x[1]),
                float(x[2]),
                float(x[3]),
                float(x[4]),
                float(x[5]),
            ]
            for x in data
        ]
    except Exception as e:
        print(f"[BINANCE ERROR] {symbol}/{interval}: {e}")
        return []


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
        "red": c < o,
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
        tr.append(max(h - l, abs(h - pc), abs(l - pc)))

    return np.mean(tr[-n:]) / closed[-1]["c"] * 100


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
            "rsi": 50,
            "flags": ["DATA<220"],
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

    if cur["body_pct"] >= 40:
        score += 10

    vols = [x["v"] for x in p[-31:-2]]
    if vols:
        z = (cur["v"] - np.mean(vols)) / max(np.std(vols), 1e-12)
        if z >= 2:
            score += 15
        elif z >= 1:
            score += 10
        elif z >= 0.5:
            score += 5

    if action == "CALL" and resistance > price:
        if (resistance - price) / price * 100 < atr * 0.45:
            trap += 65
            flags.append("CALL near resistance")

    if action == "PUT" and support < price:
        if (price - support) / price * 100 < atr * 0.45:
            trap += 65
            flags.append("PUT near support")

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
        "flags": flags,
    }


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
                (ts,pair,action,entry,entry_time,score,grade,trap,source)
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
                source,
            ))
            return cur.lastrowid

    def finish(self, sid, result, exit_price, pnl):
        with self.lock, sqlite3.connect(self.path) as c:
            c.execute("""
                UPDATE paper
                SET result=?, exit=?, pnl=?
                WHERE id=?
            """, (result, exit_price, pnl, sid))


journal = Journal()


class Paper:
    def __init__(self):
        self.lock = threading.RLock()
        self.active = []
        self.last_cycle = -1
        self.last_heartbeat = 0
        self.n = 0
        self.session = {"WIN": 0, "LOSS": 0, "DRAW": 0}

    def active_symbol(self, symbol):
        with self.lock:
            return any(x["symbol"] == symbol for x in self.active)

    def best(self):
        rows = []

        for name, symbol in PAIRS:
            if AVOID_DUPLICATE_ACTIVE_PAIR and self.active_symbol(symbol):
                continue

            try:
                r = analyze(symbol, name)
                real = r["decision"] in ("CALL", "PUT")

                if real:
                    direction = r["decision"]
                else:
                    h, m15, m5 = r["mtf"]
                    if h == "BULLISH" or m15 == "BULLISH":
                        direction = "CALL"
                    elif h == "BEARISH" or m15 == "BEARISH":
                        direction = "PUT"
                    else:
                        direction = "CALL" if r.get("rsi", 50) >= 50 else "PUT"

                if r["regime"] in ("HIGH_VOLATILITY", "LOW_VOLATILITY", "UNKNOWN"):
                    print(f"[FILTER] {name} BLOCKED regime={r['regime']}")
                    continue

                rank = r["score"] + (15 if real else 0) - r["trap"] * 0.25
                rows.append((rank, r, direction, real))

                print(
                    f"[CANDIDATE] {name} {direction} "
                    f"grade={r['grade']} score={r['score']:.1f} "
                    f"trap={r['trap']:.1f} rank={rank:.1f}"
                )

            except Exception as e:
                print("[ANALYZE ERROR]", name, repr(e))

        if not rows:
            return None

        return max(rows, key=lambda x: x[0])

    def open_cycle(self):
        now = time.time()

        if now - self.last_cycle < PAPER_INTERVAL_SECONDS:
            return

        self.last_cycle = now

        print("=" * 60)
        print("[PAPER CYCLE]", thai_text())
        print("[PAPER CYCLE] Searching candidates...")

        selected = self.best()

        if not selected:
            msg = (
                "⚠️ **V15 PAPER CYCLE**\n"
                "ไม่พบ Candidate ที่เปิด PAPER ได้ในรอบนี้\n"
                f"🇹🇭 `{thai_text()}`"
            )
            print(msg)
            discord(msg)
            return

        rank, r, direction, real = selected

        k = fetch(r["symbol"], "1m", 5)
        if len(k) < 2:
            msg = (
                f"⚠️ **V15 ENTRY DATA ERROR** `{r['name']}`\n"
                "ดึง 1m ไม่พอ"
            )
            print(msg)
            discord(msg)
            return

        entry = k[-2]
        price = entry[4]
        entry_time = entry[0]

        self.n += 1
        pid = f"PAPER-{self.n:06d}"

        source = "PAPER_V15_SIGNAL" if real else "PAPER_FORCED_TEST"

        r["price"] = price
        r["entry_time"] = entry_time

        sid = journal.add(r, direction, source)

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
            "expire": time.time() + PAPER_HORIZON_SECONDS,
        }

        with self.lock:
            self.active.append(order)

        mode = "🟢 V15 SIGNAL" if real else "🟡 FORCED TEST"

        msg = (
            "🧠 **V15 PAPER TRAINING OPEN**\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"🆔 `{pid}`\n"
            f"📌 `{r['name']}`\n"
            f"🎯 **{direction}**\n"
            f"🧪 {mode}\n"
            f"💵 Entry `{price:.8f}`\n"
            f"📊 Score `{r['score']:.1f}`\n"
            f"🛡️ Trap `{r['trap']:.1f}`\n"
            f"🏷️ Grade `{r['grade']}`\n"
            f"🌐 `{r['regime']}`\n"
            f"📈 MTF `{r['mtf'][0]} / {r['mtf'][1]} / {r['mtf'][2]}`\n"
            f"🇹🇭 `{thai_text()}`\n\n"
            "⏱️ **วัดผลหลัง 2 นาที**\n"
            "❌ PAPER ONLY — REAL ORDER OFF"
        )

        print("[PAPER OPEN]", pid, r["name"], direction)
        discord(msg)

    def resolve(self):
        with self.lock:
            orders = list(self.active)

        remain = []

        for o in orders:
            if time.time() < o["expire"]:
                remain.append(o)
                continue

            k = fetch(o["symbol"], "1m", 10)

            if len(k) < 3:
                remain.append(o)
                continue

            target_time = o["entry_time"] + 2 * 60 * 1000
            target = None

            for x in k:
                if x[0] == target_time and int(time.time() * 1000) >= x[0] + 60000:
                    target = x
                    break

            if target is None:
                remain.append(o)
                continue

            exit_price = target[4]
            entry = o["entry"]

            if o["action"] == "CALL":
                pnl = (exit_price - entry) / entry * 100
            else:
                pnl = (entry - exit_price) / entry * 100

            if pnl > 0:
                result = "WIN"
            elif pnl < 0:
                result = "LOSS"
            else:
                result = "DRAW"

            journal.finish(o["sid"], result, exit_price, pnl)
            self.session[result] += 1

            total = sum(self.session.values())
            wr = self.session["WIN"] / total * 100 if total else 0

            emoji = "✅" if result == "WIN" else "❌" if result == "LOSS" else "➖"

            msg = (
                f"{emoji} **V15 PAPER RESULT**\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                f"🆔 `{o['id']}`\n"
                f"📌 `{o['name']}`\n"
                f"🎯 `{o['action']}`\n"
                f"💵 Entry `{entry:.8f}`\n"
                f"💵 Exit `{exit_price:.8f}`\n"
                f"📈 **{result}**\n"
                f"💰 P/L `{pnl:+.4f}%`\n"
                f"📚 SESSION W `{self.session['WIN']}` / "
                f"L `{self.session['LOSS']}` / "
                f"D `{self.session['DRAW']}`\n"
                f"🎯 Session WR `{wr:.1f}%`\n"
                f"🇹🇭 `{thai_text()}`"
            )

            print("[PAPER RESULT]", o["id"], result, f"{pnl:+.4f}%")
            discord(msg)

        with self.lock:
            self.active = remain

    def loop(self):
        print("[PAPER LOOP] RUNNING")

        while True:
            try:
                self.resolve()
                self.open_cycle()

                now = time.time()

                if now - self.last_heartbeat >= HEARTBEAT_INTERVAL_SECONDS:
                    self.last_heartbeat = now

                    msg = (
                        "💓 **V15 SYSTEM HEARTBEAT**\n"
                        "━━━━━━━━━━━━━━━━━━━━\n"
                        "🟢 Process = `RUNNING`\n"
                        "🧪 Paper = `ON`\n"
                        "🔒 Real Order = `OFF`\n"
                        f"📦 Active Paper = `{len(self.active)}`\n"
                        f"🇹🇭 `{thai_text()}`"
                    )

                    print(msg)
                    discord(msg)

            except Exception as e:
                print("[PAPER LOOP ERROR]", repr(e))
                discord(
                    "🚨 **V15 LOOP ERROR**\n"
                    f"`{str(e)[:500]}`\n"
                    f"🇹🇭 `{thai_text()}`"
                )

            time.sleep(2)


paper = Paper()


def worker(name, symbol):
    print(f"[WORKER ONLINE] {name}")
    last = None

    while True:
        try:
            k = fetch(symbol, "1m", 5)

            if not k:
                time.sleep(2)
                continue

            current = k[-1][0]

            if current != last:
                last = current

                r = analyze(symbol, name)

                print(
                    f"[MASTER] {name} {r['decision']} "
                    f"grade={r['grade']} score={r['score']:.1f} "
                    f"trap={r['trap']:.1f}"
                )

                if (
                    r["decision"] in ("CALL", "PUT")
                    and r["grade"] in ("A", "A+")
                    and r["trap"] < 30
                ):
                    discord(
                        "🏆 **V15 MASTER SIGNAL**\n"
                        "━━━━━━━━━━━━━━━━━━━━\n"
                        f"📌 `{name}`\n"
                        f"🎯 **{r['decision']}**\n"
                        f"📊 Score `{r['score']:.1f}`\n"
                        f"🛡️ Trap `{r['trap']:.1f}`\n"
                        f"🌐 `{r['regime']}`\n"
                        f"📈 MTF `{r['mtf'][0]} / {r['mtf'][1]} / {r['mtf'][2]}`\n"
                        f"🇹🇭 `{thai_text()}`\n\n"
                        "🔒 **REAL ORDER = OFF**"
                    )

        except Exception as e:
            print(f"[WORKER ERROR] {name}:", repr(e))

        time.sleep(1.5)


def start():
    print("=" * 60)
    print("🚀 V15 ADAPTIVE PAPER TRAINING - RAILWAY FIX")
    print("=" * 60)
    print("REAL ORDER = OFF")
    print("PAPER = ON")
    print("PAPER INTERVAL =", PAPER_INTERVAL_SECONDS, "seconds")
    print("PAPER HORIZON =", PAPER_HORIZON_SECONDS, "seconds")
    print("FORCE PAPER TEST =", FORCE_PAPER_TEST)
    print("THAI TIME =", thai_text())

    print("[DISCORD CONFIGURED] =", discord_enabled())

    if not discord_enabled():
        print("⚠️ DISCORD_WEBHOOK_URL NOT CONFIGURED")
        print("Set Railway Variable: DISCORD_WEBHOOK_URL")
    else:
        ok = discord(
            "🚀 **V15 SYSTEM ONLINE**\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "🟢 Process = `RUNNING`\n"
            "🧪 PAPER = `ON`\n"
            "🧪 Every **30 minutes**\n"
            "⏱️ Result target **2 minutes**\n"
            f"🟡 FORCE PAPER TEST = `{FORCE_PAPER_TEST}`\n"
            "🔒 REAL ORDER = `OFF`\n"
            "📨 Discord = `CONNECTED`\n"
            f"🇹🇭 `{thai_text()}`"
        )
        print("[DISCORD START TEST] OK =", ok)

    threading.Thread(
        target=paper.loop,
        name="paper-loop",
        daemon=True,
    ).start()

    for name, symbol in PAIRS:
        threading.Thread(
            target=worker,
            args=(name, symbol),
            name=f"worker-{symbol}",
            daemon=True,
        ).start()
        time.sleep(0.5)


if __name__ == "__main__":
    start()

    while True:
        time.sleep(3600)
'''

main_path = Path("/mnt/data/main.py")
requirements_path = Path("/mnt/data/requirements.txt")
main_path.write_text(bot_code, encoding="utf-8")
requirements_path.write_text("numpy>=2.0,<3\n", encoding="utf-8")

print(f"สร้างไฟล์จริงแล้ว: {main_path}")
print(f"สร้าง dependencies แล้ว: {requirements_path}")
print(f"จำนวนบรรทัด main.py: {len(bot_code.splitlines())}")
