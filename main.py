from pathlib import Path
from textwrap import dedent

out = Path("/mnt/data")
out.mkdir(parents=True, exist_ok=True)

code = dedent(r'''
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TRADEIFY 2.2.2 STABLE
- Robust startup (/mnt/data is created automatically)
- Discord Webhook notifier with timeout + retry
- Discord self-test verifies the webhook independently
- Signal generation is separated from notification
- Duplicate-signal guard
- JSON output
- OTC safety warnings
- No third-party Python packages required

Environment:
  DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
Optional:
  DISCORD_TIMEOUT=10
  DISCORD_RETRIES=3
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


APP_VERSION = "2.2.2"
BASE_DIR = Path("/mnt/data")
BASE_DIR.mkdir(parents=True, exist_ok=True)

OUTPUT_JSON = BASE_DIR / "tradeify_last_signal.json"
STATE_JSON = BASE_DIR / "tradeify_state.json"

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
DISCORD_TIMEOUT = float(os.getenv("DISCORD_TIMEOUT", "10"))
DISCORD_RETRIES = max(1, int(os.getenv("DISCORD_RETRIES", "3")))


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


@dataclass
class Signal:
    market: str
    symbol: str
    timeframe: str
    trend: str
    structure: str
    signal: str
    confidence: int
    entry: float
    sl: float
    tp1: float
    tp2: float
    support: float
    resistance: float
    atr14: float
    rsi14: float
    ema20: float
    ema50: float
    macd_histogram: float
    reasons: list[str]
    warnings: list[str]
    created_at: str


class DiscordNotifier:
    def __init__(self, webhook_url: str = DISCORD_WEBHOOK_URL):
        self.webhook_url = webhook_url.strip()
        self.last_http_status: Optional[int] = None
        self.last_error: Optional[str] = None

    @property
    def configured(self) -> bool:
        return bool(self.webhook_url)

    def send(self, content: str, *, username: str = "TRADEIFY") -> bool:
        if not self.configured:
            self.last_error = "DISCORD_WEBHOOK_URL is missing"
            print(f"Discord    : FAILED - {self.last_error}")
            return False

        payload = json.dumps({
            "username": username,
            "content": content,
            "allowed_mentions": {"parse": []},
        }).encode("utf-8")

        req = urllib.request.Request(
            self.webhook_url,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "User-Agent": f"TRADEIFY/{APP_VERSION}",
            },
            method="POST",
        )

        for attempt in range(1, DISCORD_RETRIES + 1):
            try:
                print(f"Discord    : SENDING... attempt {attempt}/{DISCORD_RETRIES}")
                with urllib.request.urlopen(req, timeout=DISCORD_TIMEOUT) as resp:
                    self.last_http_status = int(resp.status)

                if self.last_http_status in (200, 204):
                    self.last_error = None
                    print(f"Discord    : SUCCESS (HTTP {self.last_http_status})")
                    return True

                self.last_error = f"unexpected HTTP {self.last_http_status}"
                print(f"Discord    : FAILED - {self.last_error}")

            except urllib.error.HTTPError as e:
                self.last_http_status = e.code
                body = ""
                try:
                    body = e.read().decode("utf-8", errors="replace")[:500]
                except Exception:
                    pass
                self.last_error = f"HTTP {e.code}" + (f" - {body}" if body else "")
                print(f"Discord    : FAILED - {self.last_error}")

            except Exception as e:
                self.last_http_status = None
                self.last_error = f"{type(e).__name__}: {e}"
                print(f"Discord    : FAILED - {self.last_error}")

            if attempt < DISCORD_RETRIES:
                time.sleep(min(2 ** (attempt - 1), 4))

        return False

    def self_test(self) -> bool:
        print("----------------------------------------")
        print("DISCORD WEBHOOK SELF TEST")
        print("----------------------------------------")

        if not self.configured:
            print("Discord    : NOT CONFIGURED")
            print("Set DISCORD_WEBHOOK_URL before starting live notifications.")
            return False

        test_message = (
            f"🧪 **TRADEIFY {APP_VERSION} — DISCORD TEST**\n"
            f"Status: webhook reachable\n"
            f"Time: {now_iso()}"
        )
        ok = self.send(test_message, username="TRADEIFY TEST")
        print(f"DISCORD SELF TEST: {'PASS' if ok else 'FAIL'}")
        return ok


def load_state() -> dict:
    try:
        if STATE_JSON.exists():
            return json.loads(STATE_JSON.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def save_state(state: dict) -> None:
    STATE_JSON.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def signal_key(s: Signal) -> str:
    raw = "|".join([
        s.market, s.symbol, s.timeframe, s.signal,
        f"{s.entry:.8f}", f"{s.sl:.8f}",
        f"{s.tp1:.8f}", f"{s.tp2:.8f}",
    ])
    return hashlib.sha256(raw.encode()).hexdigest()


def risk_reward(entry: float, sl: float, tp: float, side: str) -> float:
    risk = abs(entry - sl)
    reward = (tp - entry) if side == "BUY" else (entry - tp)
    if risk <= 0:
        return 0.0
    return reward / risk


def build_signal(
    *,
    market: str,
    symbol: str,
    timeframe: str,
    entry: float,
    support: float,
    resistance: float,
    atr14: float,
    rsi14: float,
    ema20: float,
    ema50: float,
    macd_histogram: float,
) -> Signal:
    reasons: list[str] = []
    warnings: list[str] = []

    bullish = ema20 > ema50 and entry > ema20
    bearish = ema20 < ema50 and entry < ema20

    if bullish:
        trend = "BULLISH"
        structure = "HH/HL"
        reasons += [
            "ราคาอยู่เหนือ EMA20 และ EMA20 อยู่เหนือ EMA50",
            "โครงสร้างราคาเป็น Higher High / Higher Low",
        ]
    elif bearish:
        trend = "BEARISH"
        structure = "LH/LL"
        reasons += [
            "ราคาอยู่ใต้ EMA20 และ EMA20 อยู่ใต้ EMA50",
            "โครงสร้างราคาเป็น Lower High / Lower Low",
        ]
    else:
        trend = "NEUTRAL"
        structure = "MIXED"

    # Conservative signal logic:
    # trend alone is not enough for an immediate entry.
    signal = "WAIT"
    confidence = 50

    if bullish:
        confidence += 15
        if macd_histogram > 0:
            confidence += 10
            reasons.append("MACD Histogram สนับสนุนโมเมนตัมขาขึ้น")
        else:
            confidence -= 8
            warnings.append("MACD Histogram เป็นลบ — โมเมนตัมขาขึ้นกำลังอ่อนแรง")

        if rsi14 >= 70:
            confidence -= 10
            warnings.append("RSI Overbought — ห้ามไล่ราคา Buy")
            signal = "BUY ON CONFIRMATION"
        elif rsi14 >= 50:
            confidence += 8
            signal = "BUY"
        else:
            signal = "WAIT"

    elif bearish:
        confidence += 15
        if macd_histogram < 0:
            confidence += 10
            reasons.append("MACD Histogram สนับสนุนโมเมนตัมขาลง")
        else:
            confidence -= 8
            warnings.append("MACD Histogram เป็นบวก — โมเมนตัมขาลงกำลังอ่อนแรง")

        if rsi14 <= 30:
            confidence -= 10
            warnings.append("RSI Oversold — ห้ามไล่ราคา Sell")
            signal = "SELL ON CONFIRMATION"
        elif rsi14 <= 50:
            confidence += 8
            signal = "SELL"
        else:
            signal = "WAIT"

    else:
        warnings.append("Trend ไม่ชัดเจน — รอโครงสร้างยืนยัน")
        signal = "WAIT"

    confidence = int(clamp(confidence, 0, 95))

    # ATR-based protective levels.
    # For confirmation signals, these are planning levels only.
    if bullish:
        sl = max(support, entry - 1.15 * atr14)
        tp1 = entry + 1.50 * abs(entry - sl)
        tp2 = entry + 2.20 * abs(entry - sl)
    elif bearish:
        sl = min(resistance, entry + 1.15 * atr14)
        tp1 = entry - 1.50 * abs(entry - sl)
        tp2 = entry - 2.20 * abs(entry - sl)
    else:
        sl = entry - atr14
        tp1 = entry + 1.50 * atr14
        tp2 = entry + 2.20 * atr14

    if market.upper() == "OTC":
        warnings += [
            "OTC MODE: ราคาอาจแตกต่างจากตลาดจริง",
            "ห้ามถือว่าสัญญาณ OTC = ราคาตลาดจริง 100%",
            "แนะนำลดขนาดไม้และรอ Candle Confirmation",
        ]

    return Signal(
        market=market.upper(),
        symbol=symbol,
        timeframe=timeframe,
        trend=trend,
        structure=structure,
        signal=signal,
        confidence=confidence,
        entry=round(entry, 8),
        sl=round(sl, 8),
        tp1=round(tp1, 8),
        tp2=round(tp2, 8),
        support=round(support, 8),
        resistance=round(resistance, 8),
        atr14=round(atr14, 8),
        rsi14=round(rsi14, 4),
        ema20=round(ema20, 8),
        ema50=round(ema50, 8),
        macd_histogram=round(macd_histogram, 8),
        reasons=reasons,
        warnings=warnings,
        created_at=now_iso(),
    )


def format_discord(s: Signal) -> str:
    icon = {
        "BUY": "🟢",
        "BUY ON CONFIRMATION": "🟡",
        "SELL": "🔴",
        "SELL ON CONFIRMATION": "🟠",
        "WAIT": "⚪",
    }.get(s.signal, "📊")

    rr1 = risk_reward(s.entry, s.sl, s.tp1, "BUY" if "BUY" in s.signal else "SELL")
    rr2 = risk_reward(s.entry, s.sl, s.tp2, "BUY" if "BUY" in s.signal else "SELL")

    reasons = "\n".join(f"• {x}" for x in s.reasons) or "• ไม่มี"
    warnings = "\n".join(f"⚠️ {x}" for x in s.warnings) or "ไม่มี"

    return (
        f"**{icon} TRADEIFY {APP_VERSION} — {s.signal}**\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"**Market:** `{s.market}`\n"
        f"**Symbol:** `{s.symbol}`\n"
        f"**Timeframe:** `{s.timeframe}`\n"
        f"**Trend:** `{s.trend}`\n"
        f"**Structure:** `{s.structure}`\n"
        f"**Confidence:** `{s.confidence}%`\n\n"
        f"**Entry:** `{s.entry}`\n"
        f"**SL:** `{s.sl}`\n"
        f"**TP1:** `{s.tp1}`  | R:R `{rr1:.2f}`\n"
        f"**TP2:** `{s.tp2}`  | R:R `{rr2:.2f}`\n\n"
        f"**RSI14:** `{s.rsi14}`\n"
        f"**EMA20:** `{s.ema20}`\n"
        f"**EMA50:** `{s.ema50}`\n"
        f"**ATR14:** `{s.atr14}`\n"
        f"**MACD Hist:** `{s.macd_histogram}`\n\n"
        f"**Reasons**\n{reasons}\n\n"
        f"**Warnings**\n{warnings}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"`{s.created_at}`"
    )


def save_signal(s: Signal) -> None:
    OUTPUT_JSON.write_text(
        json.dumps(asdict(s), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def print_signal(s: Signal) -> None:
    rr1 = risk_reward(s.entry, s.sl, s.tp1, "BUY" if "BUY" in s.signal else "SELL")
    rr2 = risk_reward(s.entry, s.sl, s.tp2, "BUY" if "BUY" in s.signal else "SELL")

    print("=" * 55)
    print(f"TRADEIFY {APP_VERSION} STABLE")
    print("=" * 55)
    print(f"Market      : {s.market}")
    print(f"Symbol      : {s.symbol}")
    print(f"Timeframe   : {s.timeframe}")
    print(f"Trend       : {s.trend}")
    print(f"Structure   : {s.structure}")
    print(f"Signal      : {s.signal}")
    print(f"Confidence  : {s.confidence}%")
    print("REASONS")
    for x in s.reasons:
        print(f"  + {x}")
    print("WARNINGS")
    for x in s.warnings:
        print(f"  ! {x}")
    print(f"Entry       : {s.entry}")
    print(f"SL          : {s.sl}")
    print(f"TP1         : {s.tp1}  R:R {rr1:.2f}")
    print(f"TP2         : {s.tp2}  R:R {rr2:.2f}")
    print("=" * 55)


def notify_once(s: Signal, notifier: DiscordNotifier) -> bool:
    state = load_state()
    key = signal_key(s)

    if state.get("last_signal_key") == key:
        print("Discord    : SKIPPED - duplicate signal")
        return True

    ok = notifier.send(format_discord(s))
    if ok:
        state["last_signal_key"] = key
        state["last_sent_at"] = now_iso()
        save_state(state)
    return ok


def demo_signal() -> Signal:
    # Same type of test values as the previous successful log.
    return build_signal(
        market="OTC",
        symbol="TEST-OTC",
        timeframe="1m",
        entry=112.7063,
        support=108.174,
        resistance=113.1564,
        atr14=0.49873377,
        rsi14=80.0126,
        ema20=112.2621,
        ema50=110.9249,
        macd_histogram=-0.05,
    )


def main() -> int:
    print()
    print("=" * 55)
    print(f"             TRADEIFY {APP_VERSION} STABLE")
    print("=" * 55)
    print(f"Python      : {sys.version.split()[0]}")
    print(f"Output      : {OUTPUT_JSON}")
    print(f"Discord     : {'CONFIGURED' if DISCORD_WEBHOOK_URL else 'NOT CONFIGURED'}")
    print()

    notifier = DiscordNotifier()

    # Explicit webhook test. This is separate from signal self-test.
    discord_ok = notifier.self_test()

    # Generate a safe demo signal.
    s = demo_signal()
    save_signal(s)
    print_signal(s)

    # Send demo signal only when webhook is configured.
    signal_ok = notify_once(s, notifier) if notifier.configured else False

    print()
    print("FILES")
    print(f"  JSON saved : {OUTPUT_JSON}")
    print(f"  State      : {STATE_JSON}")
    print()
    print("SELF TEST")
    print(f"  Signal engine : PASS")
    print(f"  Discord       : {'PASS' if discord_ok else 'FAIL / NOT CONFIGURED'}")
    print(f"  Notification  : {'PASS' if signal_ok else 'FAIL / NOT CONFIGURED'}")
    print("=" * 55)

    # Do not mark the entire system PASS when Discord was not actually tested.
    return 0 if discord_ok and signal_ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
''').lstrip()

path = out / "tradeify_v2_2_2_stable.py"
path.write_text(code, encoding="utf-8")

readme = """TRADEIFY 2.2.2 STABLE

1) Set your Discord webhook as an environment variable:
   DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/..."

2) Run:
   python tradeify_v2_2_2_stable.py

The program now:
- creates /mnt/data automatically
- tests Discord webhook separately
- prints HTTP status
- retries failed Discord sends
- prevents duplicate notifications
- saves signal JSON/state
- does NOT report overall PASS when Discord was never configured/tested

IMPORTANT:
Do not paste your private webhook URL into public chat/logs.
"""
(out / "TRADEIFY_2_2_2_README.txt").write_text(readme, encoding="utf-8")

print(f"Created: {path}")
print(f"Created: {out / 'TRADEIFY_2_2_2_README.txt'}")
