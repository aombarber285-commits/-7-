from pathlib import Path
import zipfile

out = Path("/mnt/data/v15_railway_discord_fixed")
out.mkdir(exist_ok=True)

main_code = r'''import os
import json
import time
import sqlite3
import threading
import urllib.request
import urllib.parse
from datetime import datetime
from zoneinfo import ZoneInfo

# ============================================================
# V15 ADAPTIVE PAPER TRAINER - RAILWAY / DISCORD FIX
# IMPORTANT:
# - This file is the ACTUAL bot.
# - Do NOT wrap this file inside: code = '''...'''
# - REAL ORDERS ARE ALWAYS OFF.
# ============================================================

REAL_ORDER_ENABLED = False
PAPER_TRAINING_ENABLED = True

# Test paper every 30 minutes.
PAPER_INTERVAL_SECONDS = 30 * 60

# Resolve after 2 minutes.
PAPER_HORIZON_SECONDS = 2 * 60

FORCE_PAPER_TEST = True
AVOID_DUPLICATE_ACTIVE_PAIR = True
HEARTBEAT_INTERVAL_SECONDS = 5 * 60

THAI_TZ = ZoneInfo("Asia/Bangkok")

# Railway -> Variables -> DISCORD_WEBHOOK_URL
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


# ============================================================
# DISCORD
# ============================================================

def discord_enabled():
    u = DISCORD_WEBHOOK_URL
    return (
        u.startswith("https://discord.com/api/webhooks/")
        or u.startswith("https://discordapp.com/api/webhooks/")
    )


def discord(msg):
    """Send Discord webhook and never crash the main process."""
    if not discord_enabled():
        print("[DISCORD OFF] DISCORD_WEBHOOK_URL is missing/invalid.")
        print("[DISCORD MESSAGE]", msg)
        return False

    try:
        payload = json.dumps({
            "content": str(msg)[:1900],
            "allowed_mentions": {"parse": []},
        }).encode("utf-8")

        req = urllib.request.Request(
            DISCORD_WEBHOOK_URL,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "V15-Paper-Trainer/2.0",
            },
            method="POST",
        )

        with urllib.request.urlopen(req, timeout=15) as response:
            body = response.read().decode("utf-8", errors="ignore")
            print(f"[DISCORD] HTTP {response.status} body={body[:200]}")
            return response.status in (200, 204)

    except Exception as exc:
        print("[DISCORD ERROR]", repr(exc))
        return False


def discord_startup_test():
    """Visible test so Railway logs immediately show Discord status."""
    if not discord_enabled():
        print("============================================================")
        print("DISCORD ERROR: DISCORD_WEBHOOK_URL IS NOT CONFIGURED")
        print("Railway -> Variables -> add DISCORD_WEBHOOK_URL")
        print("============================================================")
        return False

    ok = discord(
        "🚀 **V15 SYSTEM ONLINE — DISCORD TEST**\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "🟢 Process = `RUNNING`\n"
        "🧪 PAPER = `ON`\n"
        "🔒 REAL ORDER = `OFF`\n"
        "🧪 Forced paper test = `ON`\n"
        "⏱️ Paper cycle = `30 minutes`\n"
        "⏱️ Result horizon = `2 minutes`\n"
        f"🇹🇭 `{thai_text()}`"
    )
    print("[DISCORD STARTUP TEST]", "SUCCESS" if ok else "FAILED")
    return ok


# ============================================================
# BINANCE
# ============================================================

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
            headers={"User-Agent": "V15-Paper-Trainer/2.0"},
        )
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode("utf-8"))

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

    except Exception as exc:
        print(f"[BINANCE ERROR] {symbol}/{interval}: {exc}")
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

    for value in values[1:]:
        x = (value - x) * a + x

    return x


def rsi(values, n=14):
    if len(values) < n + 1:
        return 50.0

    gains = []
    losses = []

    for i in range(1, len(values)):
        d = values[i] - values[i - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))

    avg_gain = sum(gains[:n]) / n
    avg_loss = sum(losses[:n]) / n

    for i in range(n, len(gains)):
        avg_gain = (avg_gain * (n - 1) + gains[i]) / n
        avg_loss = (avg_loss * (n - 1) + losses[i]) / n

    if avg_loss == 0:
        return 100.0

    return 100 - 100 / (1 + avg_gain / avg_loss)


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

    return sum(tr[-n:]) / n / closed[-1]["c"] * 100


def mtf(symbol):
    result = []

    for tf in ("1h", "15m", "5m"):
        k = fetch(symbol, tf, 250)

        if len(k) < 200:
            result.append("NEUTRAL")
            continue

        p = [candle(x) for x in k]
        closes = [x["c"] for x in p[:-1]]
        c = p[-2]["c"]

        e20 = ema(closes, 20)
        e50 = ema(closes, 50)
        e200 = ema(closes, 200)

        if c > e20 > e50 > e200:
            result.append("BULLISH")
        elif c < e20 < e50 < e200:
            result.append("BEARISH")
        else:
            result.append("NEUTRAL")

    return tuple(result)


def analyze(symbol, name):
    k = fetch(symbol, "1m", 250)

    if len(k) < 220:
        return {
            "name": name,
            "symbol": symbol,
            "decision": "NO TRADE",
            "grade": "C",
            "score": 0.0,
            "trap": 100.0,
            "regime": "UNKNOWN",
            "mtf": ("NEUTRAL", "NEUTRAL", "NEUTRAL"),
            "entry_time": 0,
            "price": 0.0,
            "rsi": 50.0,
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
        mean_v = sum(vols) / len(vols)
        variance = sum((v - mean_v) ** 2 for v in vols) / len(vols)
        std_v = variance ** 0.5
        z = (cur["v"] - mean_v) / max(std_v, 1e-12)

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


# ============================================================
# SQLITE
# ============================================================

class Journal:
    def __init__(self):
        os.makedirs("./data", exist_ok=True)
        self.path = "./data/v15_sniper_journal.db"
        self.lock = threading.RLock()

        with sqlite3.connect(self.path) as c:
            c.execute(
                """
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
                """
            )

    def add(self, r, action, source):
        with self.lock, sqlite3.connect(self.path) as c:
            cur = c.execute(
                """
                INSERT INTO paper
                (ts,pair,action,entry,entry_time,score,grade,trap,source)
                VALUES(?,?,?,?,?,?,?,?,?)
                """,
                (
                    thai_text(),
                    r["name"],
                    action,
                    r["price"],
                    r["entry_time"],
                    r["score"],
                    r["grade"],
                    r["trap"],
                    source,
                ),
            )
            return cur.lastrowid

    def finish(self, sid, result, exit_price, pnl):
        with self.lock, sqlite3.connect(self.path) as c:
            c.execute(
                """
                UPDATE paper
                SET result=?, exit=?, pnl=?
                WHERE id=?
                """,
                (result, exit_price, pnl, sid),
            )


journal = Journal()


# ============================================================
# PAPER ENGINE
# ============================================================

class Paper:
    def __init__(self):
        self.lock = threading.RLock()
        self.active = []
        self.last_cycle = 0.0
        self.last_heartbeat = 0.0
        self.n = 0
        self.session = {"WIN": 0, "LOSS": 0, "DRAW": 0}

    def active_symbol(self, symbol):
        with self.lock:
            return any(x["symbol"] == symbol for x in self.active)

    def best(self):
        rows = []

        for name, symbol in PAIRS:
            if (
                AVOID_DUPLICATE_ACTIVE_PAIR
                and self.active_symbol(symbol)
            ):
                continue

            try:
                r = analyze(symbol, name)

                real = r["decision"] in ("CALL", "PUT")

                if real:
                    direction = r["decision"]
                else:
                    # Forced PAPER is allowed to choose a test direction.
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

                if r["regime"] in (
                    "HIGH_VOLATILITY",
                    "LOW_VOLATILITY",
                    "UNKNOWN",
                ):
                    print(
                        f"[SKIP REGIME] {name} {r['regime']}"
                    )
                    continue

                rank = (
                    r["score"]
                    + (15 if real else 0)
                    - r["trap"] * 0.25
                )

                rows.append((rank, r, direction, real))

                print(
                    f"[CANDIDATE] {name} {direction} "
                    f"grade={r['grade']} "
                    f"score={r['score']:.1f} "
                    f"trap={r['trap']:.1f} "
                    f"rank={rank:.1f}"
                )

            except Exception as exc:
                print("[ANALYZE ERROR]", name, repr(exc))

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

        selected = self.best()

        if not selected:
            discord(
                "⚠️ **V15 PAPER CYCLE**\n"
                "ไม่พบ Candidate ที่เปิด PAPER ได้ในรอบนี้\n"
                f"🇹🇭 `{thai_text()}`"
            )
            return

        rank, r, direction, real = selected

        k = fetch(r["symbol"], "1m", 5)

        if len(k) < 2:
            discord(
                f"⚠️ **V15 ENTRY DATA ERROR** `{r['name']}`\n"
                "ดึง 1m ไม่พอ"
            )
            return

        # Last CLOSED 1m candle.
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

        mode = (
            "🟢 V15 SIGNAL"
            if real
            else "🟡 FORCED TEST"
        )

        discord(
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
            f"📈 MTF `{r['mtf'][0]} / "
            f"{r['mtf'][1]} / {r['mtf'][2]}`\n"
            f"🇹🇭 `{thai_text()}`\n\n"
            "⏱️ **วัดผลหลัง 2 นาที**\n"
            "❌ PAPER ONLY — REAL ORDER OFF"
        )

        print(
            "[PAPER OPEN]",
            pid,
            r["name"],
            direction,
            "rank=",
            rank,
        )

    def resolve(self):
        with self.lock:
            orders = list(self.active)

        remain = []

        for order in orders:
            if time.time() < order["expire"]:
                remain.append(order)
                continue

            k = fetch(order["symbol"], "1m", 10)

            if len(k) < 3:
                remain.append(order)
                continue

            # We use the candle exactly +2 minutes after entry.
            target_time = (
                order["entry_time"]
                + PAPER_HORIZON_SECONDS * 1000
            )

            target = None
            now_ms = int(time.time() * 1000)

            for x in k:
                if (
                    x[0] == target_time
                    and now_ms >= x[0] + 60000
                ):
                    target = x
                    break

            if target is None:
                remain.append(order)
                continue

            exit_price = target[4]
            entry = order["entry"]

            if order["action"] == "CALL":
                pnl = (exit_price - entry) / entry * 100
            else:
                pnl = (entry - exit_price) / entry * 100

            if pnl > 0:
                result = "WIN"
            elif pnl < 0:
                result = "LOSS"
            else:
                result = "DRAW"

            journal.finish(
                order["sid"],
                result,
                exit_price,
                pnl,
            )

            self.session[result] += 1

            total = sum(self.session.values())
            wr = (
                self.session["WIN"] / total * 100
                if total
                else 0
            )

            emoji = (
                "✅" if result == "WIN"
                else "❌" if result == "LOSS"
                else "➖"
            )

            discord(
                f"{emoji} **V15 PAPER RESULT**\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                f"🆔 `{order['id']}`\n"
                f"📌 `{order['name']}`\n"
                f"🎯 `{order['action']}`\n"
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

            print(
                "[PAPER RESULT]",
                order["id"],
                result,
                f"{pnl:+.4f}%",
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
                        "💓 **V15 SYSTEM HEARTBEAT**\n"
                        "━━━━━━━━━━━━━━━━━━━━\n"
                        "🟢 Process = `RUNNING`\n"
                        "🧪 Paper = `ON`\n"
                        "🔒 Real Order = `OFF`\n"
                        f"📦 Active Paper = `{len(self.active)}`\n"
                        f"🇹🇭 `{thai_text()}`"
                    )

            except Exception as exc:
                print(
                    "[PAPER LOOP ERROR]",
                    repr(exc),
                )
                discord(
                    "🚨 **V15 LOOP ERROR**\n"
                    f"`{str(exc)[:500]}`\n"
                    f"🇹🇭 `{thai_text()}`"
                )

            time.sleep(2)


paper = Paper()


# ============================================================
# MASTER SIGNAL WORKERS
# ============================================================

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
                    f"[MASTER] {name} "
                    f"{r['decision']} "
                    f"grade={r['grade']} "
                    f"score={r['score']:.1f} "
                    f"trap={r['trap']:.1f}"
                )

                if (
                    r["decision"] in ("CALL", "PUT")
                    and r["grade"] in ("A", "A+")
                    and r["trap"] < 30
                ):
                    discord(
                        f"🏆 **V15 MASTER SIGNAL — "
                        f"{r['grade']}**\n"
                        "━━━━━━━━━━━━━━━━━━━━\n"
                        f"📌 `{name}`\n"
                        f"🎯 **{r['decision']}**\n"
                        f"📊 Score `{r['score']:.1f}`\n"
                        f"🛡️ Trap `{r['trap']:.1f}`\n"
                        f"🌐 `{r['regime']}`\n"
                        f"📈 MTF `{r['mtf'][0]} / "
                        f"{r['mtf'][1]} / {r['mtf'][2]}`\n"
                        f"🇹🇭 `{thai_text()}`\n\n"
                        "🔒 **REAL ORDER = OFF**"
                    )

        except Exception as exc:
            print(
                f"[WORKER ERROR] {name}: {repr(exc)}"
            )

        time.sleep(1.5)


# ============================================================
# START
# ============================================================

def start():
    print("=" * 60)
    print("🚀 V15 ADAPTIVE PAPER TRAINING - RAILWAY FIX")
    print("=" * 60)
    print("REAL ORDER =", REAL_ORDER_ENABLED)
    print("PAPER =", PAPER_TRAINING_ENABLED)
    print(
        "PAPER INTERVAL =",
        PAPER_INTERVAL_SECONDS,
        "seconds",
    )
    print(
        "PAPER HORIZON =",
        PAPER_HORIZON_SECONDS,
        "seconds",
    )
    print("FORCE PAPER TEST =", FORCE_PAPER_TEST)
    print("THAI TIME =", thai_text())
    print(
        "DISCORD CONFIGURED =",
        discord_enabled(),
    )

    discord_startup_test()

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

    print("[START] All workers started.")


if __name__ == "__main__":
    start()

    # Keep Railway process alive forever.
    while True:
        time.sleep(3600)
'''

requirements = """# V15 Railway dependencies
# Python 3.13 compatible
numpy>=2.1,<3
"""

readme = """V15 Railway Discord Fixed

IMPORTANT:
1. Replace the repository's main.py with main.py from this folder.
2. Do NOT paste the previous generator script containing:
       from pathlib import Path
       code = ''' ... '''
   into main.py.
   main.py must start directly with imports such as "import os".
3. In Railway, add a Variable:
       DISCORD_WEBHOOK_URL = <your Discord webhook URL>
4. Deploy/redeploy.
5. Open Railway Logs.
6. You MUST see:
       [DISCORD STARTUP TEST] SUCCESS
   and Discord should immediately receive:
       V15 SYSTEM ONLINE — DISCORD TEST

The bot keeps REAL_ORDER_ENABLED=False.
Paper training runs every 30 minutes.
Result is evaluated after 2 minutes.

If Discord still does not receive the startup message, check Railway Variables
and make sure the webhook is copied exactly from Discord.
"""

(out / "main.py").write_text(main_code, encoding="utf-8")
(out / "requirements.txt").write_text(requirements, encoding="utf-8")
(out / "README_FIX.txt").write_text(readme, encoding="utf-8")

zip_path = Path("/mnt/data/V15_Railway_Discord_FIXED.zip")
with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
    for p in out.iterdir():
        z.write(p, arcname=p.name)

print("สร้างไฟล์แก้ไขแล้ว")
print(zip_path)
print("ไฟล์เดี่ยว:", out / "main.py")
print("requirements:", out / "requirements.txt")
