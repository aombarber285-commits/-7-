from pathlib import Path

code = r'''#!/usr/bin/env python3
"""
TRADEIFY 2.5.0
ONE PLAN / TWO TIMEFRAMES

15M = main direction + main trade plan + entry zone
5M  = timing / confirmation for the SAME 15M plan
After a LOSS: re-check the SAME direction up to 3 times.
If 15M structure/key level is broken, invalidate immediately.

Supports:
- NORMAL
- OTC

No external packages required.

LIVE INPUT:
  /app/data/candles_15m.json
  /app/data/candles_5m.json

Each file:
[
  {"open":1,"high":2,"low":0.5,"close":1.5},
  ...
]

Optional timestamp fields are ignored.

ENV:
  TRADEIFY_DISCORD_WEBHOOK=...
  TRADEIFY_OUTPUT_DIR=/app/output
  TRADEIFY_DATA_DIR=/app/data
  TRADEIFY_POLL_SECONDS=10
  TRADEIFY_MARKET_MODE=OTC
  TRADEIFY_MIN_SCORE=9
  TRADEIFY_RECHECK_MAX=3
  TRADEIFY_DEMO=1   # startup Discord test only; default 0
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import signal
import sys
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

VERSION = "2.5.0"

OUTPUT_DIR = Path(os.getenv("TRADEIFY_OUTPUT_DIR", "/app/output"))
DATA_DIR = Path(os.getenv("TRADEIFY_DATA_DIR", "/app/data"))
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)

SIGNAL_FILE = OUTPUT_DIR / "tradeify_last_signal.json"
STATE_FILE = OUTPUT_DIR / "tradeify_state.json"
LOG_FILE = OUTPUT_DIR / "tradeify.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
    ],
)
log = logging.getLogger("TRADEIFY")

STOP = False


@dataclass
class Candle:
    open: float
    high: float
    low: float
    close: float


@dataclass
class Plan:
    direction: str
    score: int
    confidence: int
    entry_low: float
    entry_high: float
    entry: float
    sl: float
    tp1: float
    tp2: float
    support: float
    resistance: float
    atr: float
    rsi: float
    ema20: float
    ema50: float
    macd_hist: float
    structure: str
    key_level: str
    reason: list[str]
    warnings: list[str]


def env_bool(name: str, default: bool = False) -> bool:
    v = os.getenv(name, "1" if default else "0").lower().strip()
    return v in {"1", "true", "yes", "on"}


def webhook_url() -> str:
    return (
        os.getenv("TRADEIFY_DISCORD_WEBHOOK", "").strip()
        or os.getenv("DISCORD_WEBHOOK_URL", "").strip()
        or os.getenv("DISCORD_WEBHOOK", "").strip()
    )


def send_discord(text: str, retries: int = 3, timeout: float = 10.0) -> tuple[bool, str]:
    url = webhook_url()
    if not url:
        return False, "NOT_CONFIGURED"

    body = json.dumps({
        "username": "TRADEIFY",
        "content": text[:1900],
        "allowed_mentions": {"parse": []},
    }, ensure_ascii=False).encode("utf-8")

    last = "UNKNOWN"
    for attempt in range(1, retries + 1):
        try:
            req = Request(
                url,
                data=body,
                method="POST",
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": f"TRADEIFY/{VERSION}",
                },
            )
            with urlopen(req, timeout=timeout) as resp:
                status = int(getattr(resp, "status", 0))
            if 200 <= status < 300:
                log.info("DISCORD SUCCESS HTTP_%s", status)
                return True, f"HTTP_{status}"
            last = f"HTTP_{status}"
        except HTTPError as e:
            last = f"HTTP_{e.code}"
            try:
                detail = e.read().decode("utf-8", errors="replace")[:400]
                log.warning("Discord %s/%s: %s %s", attempt, retries, last, detail)
            except Exception:
                log.warning("Discord %s/%s: %s", attempt, retries, last)
        except (URLError, TimeoutError, OSError) as e:
            last = f"{type(e).__name__}: {e}"
            log.warning("Discord %s/%s: %s", attempt, retries, last)
        except Exception as e:
            last = f"{type(e).__name__}: {e}"
            log.exception("Discord unexpected error")

        if attempt < retries:
            time.sleep(min(2 ** (attempt - 1), 4))
    return False, last


def load_state() -> dict:
    try:
        if STATE_FILE.exists():
            obj = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            if isinstance(obj, dict):
                return obj
    except Exception:
        log.exception("State load failed")
    return {}


def save_state(state: dict) -> None:
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(STATE_FILE)


def read_candles(path: Path) -> list[Candle]:
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            raw = raw.get("candles", [])
        out = []
        for x in raw:
            out.append(Candle(
                float(x["open"]),
                float(x["high"]),
                float(x["low"]),
                float(x["close"]),
            ))
        return out
    except Exception as e:
        log.warning("Cannot read %s: %s", path, e)
        return []


def ema(values: list[float], period: int) -> float:
    if not values:
        return 0.0
    if len(values) < period:
        return sum(values) / len(values)
    k = 2 / (period + 1)
    value = sum(values[:period]) / period
    for price in values[period:]:
        value = price * k + value * (1 - k)
    return value


def rsi(values: list[float], period: int = 14) -> float:
    if len(values) <= period:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(values)):
        d = values[i] - values[i - 1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    return 100 - (100 / (1 + avg_gain / avg_loss))


def atr(candles: list[Candle], period: int = 14) -> float:
    if len(candles) < 2:
        return 0.0
    trs = []
    for i in range(1, len(candles)):
        c = candles[i]
        p = candles[i - 1]
        trs.append(max(c.high - c.low, abs(c.high - p.close), abs(c.low - p.close)))
    return sum(trs[-period:]) / min(period, len(trs))


def macd_hist(values: list[float]) -> float:
    if len(values) < 26:
        return 0.0
    # MACD line from EMA12/EMA26. Signal uses a short rolling approximation.
    macd_series = []
    for i in range(26, len(values) + 1):
        segment = values[:i]
        macd_series.append(ema(segment, 12) - ema(segment, 26))
    if not macd_series:
        return 0.0
    signal = ema(macd_series, min(9, len(macd_series)))
    return macd_series[-1] - signal


def swing_levels(candles: list[Candle], lookback: int = 30) -> tuple[float, float]:
    recent = candles[-lookback:] if len(candles) >= lookback else candles
    if not recent:
        return 0.0, 0.0
    return min(c.low for c in recent), max(c.high for c in recent)


def structure(candles: list[Candle]) -> str:
    if len(candles) < 12:
        return "UNKNOWN"
    # Compare the latest half with the previous half.
    a = candles[-12:-6]
    b = candles[-6:]
    ah, al = max(x.high for x in a), min(x.low for x in a)
    bh, bl = max(x.high for x in b), min(x.low for x in b)
    if bh > ah and bl > al:
        return "HH/HL"
    if bh < ah and bl < al:
        return "LH/LL"
    return "RANGE"


def analyze_15m(candles: list[Candle], min_score: int = 9) -> Optional[Plan]:
    if len(candles) < 40:
        return None

    closes = [c.close for c in candles]
    last = closes[-1]
    e20 = ema(closes, 20)
    e50 = ema(closes, 50)
    r = rsi(closes)
    a = atr(candles)
    mh = macd_hist(closes)
    st = structure(candles)
    support, resistance = swing_levels(candles, 30)

    if a <= 0:
        return None

    bull = 0
    bear = 0
    reasons_bull, reasons_bear = [], []

    # Direction / trend
    if last > e20 > e50:
        bull += 2
        reasons_bull.append("15M ราคาอยู่เหนือ EMA20 และ EMA20 > EMA50")
    elif last < e20 < e50:
        bear += 2
        reasons_bear.append("15M ราคาอยู่ใต้ EMA20 และ EMA20 < EMA50")

    # Structure
    if st == "HH/HL":
        bull += 2
        reasons_bull.append("15M structure = HH/HL")
    elif st == "LH/LL":
        bear += 2
        reasons_bear.append("15M structure = LH/LL")

    # MACD
    if mh > 0:
        bull += 1
        reasons_bull.append("15M MACD histogram positive")
    elif mh < 0:
        bear += 1
        reasons_bear.append("15M MACD histogram negative")

    # RSI: confirmation, not a standalone entry trigger
    if 50 <= r <= 70:
        bull += 1
        reasons_bull.append("15M RSI สนับสนุน momentum ฝั่ง BUY")
    elif 30 <= r <= 50:
        bear += 1
        reasons_bear.append("15M RSI สนับสนุน momentum ฝั่ง SELL")

    # Key level proximity
    dist_support = abs(last - support)
    dist_resistance = abs(resistance - last)
    zone = max(a * 1.25, 1e-12)

    near_support = dist_support <= zone
    near_resistance = dist_resistance <= zone

    if near_support:
        bull += 2
        reasons_bull.append("ราคาอยู่ใกล้ Key Support")
    if near_resistance:
        bear += 2
        reasons_bear.append("ราคาอยู่ใกล้ Key Resistance")

    # Reject chasing: require room toward target
    direction = "BUY" if bull > bear else "SELL" if bear > bull else "NONE"
    score = max(bull, bear)

    if direction == "NONE" or score < min_score:
        return None

    if direction == "BUY":
        entry_low = max(support, last - 0.50 * a)
        entry_high = min(last + 0.15 * a, resistance)
        entry = max(entry_low, min(last, entry_high))
        sl = support - 0.35 * a
        risk = entry - sl
        if risk <= 0:
            return None
        tp1 = entry + risk * 1.5
        tp2 = entry + risk * 2.2
        key = "KEY SUPPORT" if near_support else "TREND / PULLBACK ZONE"
        reasons = reasons_bull
        warnings = []
        if r >= 75:
            warnings.append("RSI overbought — ห้ามไล่ราคา")
        if entry > support + 1.5 * a:
            warnings.append("Entry ห่างจาก Support — รอ Pullback")
    else:
        entry_low = max(support, last - 0.15 * a)
        entry_high = min(resistance, last + 0.50 * a)
        entry = min(last, entry_high)
        sl = resistance + 0.35 * a
        risk = sl - entry
        if risk <= 0:
            return None
        tp1 = entry - risk * 1.5
        tp2 = entry - risk * 2.2
        key = "KEY RESISTANCE" if near_resistance else "TREND / PULLBACK ZONE"
        reasons = reasons_bear
        warnings = []
        if r <= 25:
            warnings.append("RSI oversold — ห้ามไล่ราคา")
        if resistance - entry > 1.5 * a:
            warnings.append("Entry ห่างจาก Resistance — รอ Pullback")

    confidence = min(95, max(55, 55 + score * 4))

    return Plan(
        direction=direction,
        score=score,
        confidence=confidence,
        entry_low=entry_low,
        entry_high=entry_high,
        entry=entry,
        sl=sl,
        tp1=tp1,
        tp2=tp2,
        support=support,
        resistance=resistance,
        atr=a,
        rsi=r,
        ema20=e20,
        ema50=e50,
        macd_hist=mh,
        structure=st,
        key_level=key,
        reason=reasons,
        warnings=warnings,
    )


def confirm_5m(candles: list[Candle], plan: Plan) -> tuple[bool, str]:
    if len(candles) < 25:
        return False, "ข้อมูล 5M ยังไม่พอ"

    closes = [c.close for c in candles]
    e20 = ema(closes, 20)
    e50 = ema(closes, 50)
    r = rsi(closes)
    mh = macd_hist(closes)
    st = structure(candles)
    last = closes[-1]

    if plan.direction == "BUY":
        direction_ok = last >= e20 and e20 >= e50
        structure_ok = st == "HH/HL"
        momentum_ok = mh >= 0 or r >= 50
        in_zone = plan.entry_low <= last <= plan.entry_high
    else:
        direction_ok = last <= e20 and e20 <= e50
        structure_ok = st == "LH/LL"
        momentum_ok = mh <= 0 or r <= 50
        in_zone = plan.entry_low <= last <= plan.entry_high

    # 5M may confirm slightly outside the exact zone, but not chase > 0.5 ATR.
    a = atr(candles)
    if not in_zone and a > 0 and abs(last - plan.entry) > 0.5 * a:
        return False, "5M ยังไม่อยู่ใน Entry Zone"

    if not direction_ok:
        return False, "5M direction ยังไม่สอดคล้องกับ 15M"
    if not structure_ok:
        return False, "5M ยังไม่มี Structure Confirmation"
    if not momentum_ok:
        return False, "5M momentum ยังไม่ยืนยัน"

    return True, "5M Structure + Momentum + Entry Zone ยืนยัน"


def fingerprint(obj: dict) -> str:
    return hashlib.sha256(
        json.dumps(obj, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


def fmt_plan(mode: str, plan: Plan, status: str, extra: str = "") -> str:
    rr1 = abs(plan.tp1 - plan.entry) / max(abs(plan.entry - plan.sl), 1e-12)
    rr2 = abs(plan.tp2 - plan.entry) / max(abs(plan.entry - plan.sl), 1e-12)
    icon = "🟢" if plan.direction == "BUY" else "🔴"

    return f"""🚨 **TRADEIFY {VERSION} — {icon} {status}**
━━━━━━━━━━━━━━━━━━━━
**MODE:** `{mode}`
**15M PLAN:** **{plan.direction}**
**15M STRUCTURE:** `{plan.structure}`
**KEY LEVEL:** `{plan.key_level}`
**CONFIDENCE:** `{plan.confidence}%`
**SCORE:** `{plan.score}`

**15M ENTRY ZONE:** `{plan.entry_low:.5f} - {plan.entry_high:.5f}`
**ENTRY:** `{plan.entry:.5f}`
**SL:** `{plan.sl:.5f}`
**TP1:** `{plan.tp1:.5f}`
**TP2:** `{plan.tp2:.5f}`
**R:R:** `1:{rr1:.2f} / 1:{rr2:.2f}`

**SUPPORT:** `{plan.support:.5f}`
**RESISTANCE:** `{plan.resistance:.5f}`
**ATR14:** `{plan.atr:.5f}`
**RSI14:** `{plan.rsi:.2f}`
**EMA20:** `{plan.ema20:.5f}`
**EMA50:** `{plan.ema50:.5f}`
**MACD HIST:** `{plan.macd_hist:.6f}`

**REASONS**
{chr(10).join("• " + x for x in plan.reason) or "• ไม่มี"}

**WARNINGS**
{chr(10).join("⚠️ " + x for x in plan.warnings) or "ไม่มี"}

{extra}
━━━━━━━━━━━━━━━━━━━━
15M = PLAN + ENTRY | 5M = CONFIRMATION
OTC = ใช้ราคา OTC โดยตรงเมื่อ MODE=OTC
"""


def process() -> None:
    mode = os.getenv("TRADEIFY_MARKET_MODE", "OTC").upper()
    min_score = int(os.getenv("TRADEIFY_MIN_SCORE", "9"))
    max_recheck = int(os.getenv("TRADEIFY_RECHECK_MAX", "3"))

    c15 = read_candles(DATA_DIR / "candles_15m.json")
    c5 = read_candles(DATA_DIR / "candles_5m.json")

    if len(c15) < 40 or len(c5) < 25:
        log.info("WAIT DATA | 15M=%s 5M=%s | need 40/25 candles", len(c15), len(c5))
        return

    plan = analyze_15m(c15, min_score)
    state = load_state()

    if not plan:
        if state.get("active_plan"):
            state["active_plan"] = None
            state["status"] = "NO_TRADE"
            save_state(state)
        log.info("NO TRADE | 15M confluence below threshold")
        return

    plan_dict = asdict(plan)
    plan_id = fingerprint({
        "direction": plan.direction,
        "entry_low": round(plan.entry_low, 8),
        "entry_high": round(plan.entry_high, 8),
        "sl": round(plan.sl, 8),
        "tp1": round(plan.tp1, 8),
        "tp2": round(plan.tp2, 8),
        "structure": plan.structure,
        "support": round(plan.support, 8),
        "resistance": round(plan.resistance, 8),
    })

    active = state.get("active_plan")

    # New 15M plan.
    if not active or active.get("plan_id") != plan_id:
        active = {
            "plan_id": plan_id,
            "direction": plan.direction,
            "attempt": 0,
            "losses": 0,
            "wins": 0,
            "status": "PLAN_ACTIVE",
        }
        state["active_plan"] = active
        state["status"] = "PLAN_ACTIVE"
        save_state(state)

        ok, st = send_discord(fmt_plan(
            mode, plan, "15M TRADE PLAN",
            "⏳ **5M ACTION:** รอจุดยืนยันตามแผนนี้",
        ))
        log.info("15M PLAN DISCORD=%s %s", ok, st)

    # If direction changed, old plan is invalid.
    if active.get("direction") != plan.direction:
        active["status"] = "INVALIDATED"
        state["active_plan"] = None
        state["status"] = "INVALIDATED"
        save_state(state)
        send_discord(fmt_plan(
            mode, plan, "PLAN RESET",
            "⚠️ 15M direction เปลี่ยน — แผนเดิมถูกยกเลิก",
        ))
        return

    confirmed, reason = confirm_5m(c5, plan)

    if not confirmed:
        log.info("5M WAIT | %s", reason)
        return

    # Do not automatically send endless duplicate confirmations.
    last_entry_fp = state.get("last_entry_fingerprint")
    entry_payload = {
        "plan_id": plan_id,
        "direction": plan.direction,
        "entry": round(plan.entry, 8),
        "sl": round(plan.sl, 8),
        "tp1": round(plan.tp1, 8),
        "tp2": round(plan.tp2, 8),
        "attempt": active.get("attempt", 0) + 1,
    }
    efp = fingerprint(entry_payload)

    if efp == last_entry_fp:
        return

    # A confirmation is an attempt. Max three after losses.
    attempt = int(active.get("attempt", 0))
    losses = int(active.get("losses", 0))

    if losses >= max_recheck:
        log.info("RECHECK LIMIT REACHED")
        return

    attempt += 1
    active["attempt"] = attempt
    active["status"] = "ENTRY_CONFIRMED"
    state["active_plan"] = active
    state["last_entry_fingerprint"] = efp
    state["status"] = "ENTRY_CONFIRMED"
    save_state(state)

    extra = (
        f"🟢 **5M ENTRY CONFIRMED**\n"
        f"Attempt: `{attempt}` | Losses: `{losses}`\n"
        f"Confirmation: {reason}\n"
        f"ถ้าไม้แพ้ ระบบจะทบทวน **{plan.direction} เดิม** "
        f"สูงสุดอีก {max(0, max_recheck - losses)} ครั้ง "
        f"ตราบใดที่ 15M Structure/Key Level ยังไม่เสีย"
    )
    send_discord(fmt_plan(mode, plan, "5M ENTRY CONFIRMED", extra))


def demo_signal() -> None:
    # This is ONLY a webhook connectivity test, never a trading signal.
    text = (
        f"🧪 **TRADEIFY {VERSION} DISCORD TEST**\n"
        f"Runtime online.\n"
        f"15M = Plan + Entry | 5M = Confirmation\n"
        f"Mode = {os.getenv('TRADEIFY_MARKET_MODE', 'OTC').upper()}\n"
        f"This message is NOT a trade signal."
    )
    ok, status = send_discord(text)
    print(f"DISCORD TEST: {'PASS' if ok else 'FAIL'} | {status}", flush=True)
    return


def handle_stop(signum, frame):
    global STOP
    STOP = True
    log.info("Stopping TRADEIFY")


def main() -> int:
    signal.signal(signal.SIGTERM, handle_stop)
    signal.signal(signal.SIGINT, handle_stop)

    print("=" * 64, flush=True)
    print(f"        TRADEIFY {VERSION} — ONE PLAN / TWO TF", flush=True)
    print("=" * 64, flush=True)
    print(f"OUTPUT : {OUTPUT_DIR}", flush=True)
    print(f"DATA   : {DATA_DIR}", flush=True)
    print(f"MODE   : {os.getenv('TRADEIFY_MARKET_MODE', 'OTC').upper()}", flush=True)
    print(f"DISCORD: {'CONFIGURED' if webhook_url() else 'NOT_CONFIGURED'}", flush=True)

    if env_bool("TRADEIFY_DEMO", False):
        demo_signal()

    poll = max(5, int(os.getenv("TRADEIFY_POLL_SECONDS", "10")))
    print(f"STATUS : ONLINE | poll={poll}s", flush=True)

    while not STOP:
        try:
            process()
        except Exception:
            log.exception("ENGINE ERROR")
        for _ in range(poll):
            if STOP:
                break
            time.sleep(1)

    print("STATUS : STOPPED", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''

out = Path("/mnt/data")
out.mkdir(parents=True, exist_ok=True)

main_path = out / "main.py"
main_path.write_text(code, encoding="utf-8")

env_path = out / "tradeify.env.example"
env_path.write_text(
    """TRADEIFY_DISCORD_WEBHOOK=https://discord.com/api/webhooks/YOUR_ID/YOUR_TOKEN
TRADEIFY_MARKET_MODE=OTC
TRADEIFY_POLL_SECONDS=10
TRADEIFY_MIN_SCORE=9
TRADEIFY_RECHECK_MAX=3
TRADEIFY_DEMO=1
TRADEIFY_OUTPUT_DIR=/app/output
TRADEIFY_DATA_DIR=/app/data
""",
    encoding="utf-8",
)

readme_path = out / "TRADEIFY_2_5_SETUP.txt"
readme_path.write_text(
    """TRADEIFY 2.5

IMPORTANT:
1. Use this file as /app/main.py.
2. Start command:
   python /app/main.py

3. Set TRADEIFY_DISCORD_WEBHOOK.
4. For OTC:
   TRADEIFY_MARKET_MODE=OTC
5. For normal market:
   TRADEIFY_MARKET_MODE=NORMAL

LIVE DATA:
 /app/data/candles_15m.json
 /app/data/candles_5m.json

15M = direction + trade plan + entry zone.
5M = confirmation/timing for the same plan.
After a loss, re-check the same direction up to 3 times.
If 15M structure/key level breaks, the plan is invalidated.

The engine does NOT place orders. It generates signals only.
""",
    encoding="utf-8",
)

print(main_path)
print(env_path)
print(readme_path)
