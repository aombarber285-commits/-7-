from pathlib import Path

code = r'''#!/usr/bin/env python3
"""
TRADEIFY 2.2.3 STABLE - RUNNABLE

This file is the actual runtime, not a file-generator.

Features:
- Creates /app/output safely
- Discord webhook with HTTP verification, retries and timeout
- Never reports Discord PASS unless the webhook actually returns 2xx
- Duplicate signal protection
- OTC-aware warnings
- Writes last signal/state/log
- Startup self-test
- Optional continuous heartbeat loop
- No external Python packages required

ENV:
  TRADEIFY_DISCORD_WEBHOOK=...
  TRADEIFY_OUTPUT_DIR=/app/output
  TRADEIFY_RUN_ONCE=1       # optional; default 0 = keep process alive
  TRADEIFY_HEARTBEAT=60     # seconds; default 60
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import signal as os_signal
import sys
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

APP_VERSION = "2.2.3"
OUTPUT_DIR = Path(os.getenv("TRADEIFY_OUTPUT_DIR", "/app/output"))
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

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


@dataclass
class Signal:
    market: str
    symbol: str
    timeframe: str
    trend: str
    structure: str
    signal: str
    confidence: float
    entry: float
    sl: float
    tp1: float
    tp2: float
    rsi14: Optional[float] = None
    macd_histogram: Optional[float] = None
    ema20: Optional[float] = None
    ema50: Optional[float] = None
    support: Optional[float] = None
    resistance: Optional[float] = None
    atr14: Optional[float] = None
    reasons: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


def load_state() -> dict:
    try:
        if STATE_FILE.exists():
            data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
    except Exception:
        log.exception("State read failed")
    return {}


def save_state(state: dict) -> None:
    tmp = STATE_FILE.with_name(STATE_FILE.name + ".tmp")
    tmp.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp.replace(STATE_FILE)


def webhook_url() -> str:
    return (
        os.getenv("TRADEIFY_DISCORD_WEBHOOK", "").strip()
        or os.getenv("DISCORD_WEBHOOK_URL", "").strip()
        or os.getenv("DISCORD_WEBHOOK", "").strip()
    )


def webhook_configured() -> bool:
    return bool(webhook_url())


def signal_fingerprint(s: Signal) -> str:
    payload = json.dumps(
        asdict(s),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def send_discord(
    content: str,
    *,
    retries: int = 3,
    timeout: float = 10.0,
) -> tuple[bool, str]:
    """
    Discord webhook sender.
    Success is only a real HTTP 2xx response.
    Discord commonly returns 204 No Content.
    """
    url = webhook_url()
    if not url:
        return False, "NOT_CONFIGURED"

    body = json.dumps(
        {
            "username": "TRADEIFY",
            "content": content[:1900],
            "allowed_mentions": {"parse": []},
        },
        ensure_ascii=False,
    ).encode("utf-8")

    last_error = "UNKNOWN"

    for attempt in range(1, retries + 1):
        try:
            request = Request(
                url,
                data=body,
                method="POST",
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": f"TRADEIFY/{APP_VERSION}",
                },
            )

            with urlopen(request, timeout=timeout) as response:
                status = int(getattr(response, "status", 0))

            if 200 <= status < 300:
                return True, f"HTTP_{status}"

            last_error = f"HTTP_{status}"

        except HTTPError as exc:
            last_error = f"HTTP_{exc.code}"
            try:
                detail = exc.read().decode("utf-8", errors="replace")[:500]
                if detail:
                    log.warning(
                        "Discord attempt %s/%s failed: %s | %s",
                        attempt, retries, last_error, detail
                    )
                else:
                    log.warning(
                        "Discord attempt %s/%s failed: %s",
                        attempt, retries, last_error
                    )
            except Exception:
                log.warning(
                    "Discord attempt %s/%s failed: %s",
                    attempt, retries, last_error
                )

        except (URLError, TimeoutError, OSError) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            log.warning(
                "Discord attempt %s/%s failed: %s",
                attempt, retries, last_error
            )

        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            log.exception("Unexpected Discord error")

        if attempt < retries:
            time.sleep(min(2 ** (attempt - 1), 4))

    return False, last_error


def format_signal(s: Signal) -> str:
    icon = "🟢" if s.signal.upper() == "BUY" else "🔴" if s.signal.upper() == "SELL" else "🟡"

    reasons = "\n".join(f"• {x}" for x in s.reasons) or "• ไม่มี"
    warnings = "\n".join(f"⚠️ {x}" for x in s.warnings) or "ไม่มี"

    return f"""🚨 **TRADEIFY {APP_VERSION} — {icon} {s.signal.upper()}**
━━━━━━━━━━━━━━━━━━━━
**Market:** {s.market}
**Symbol:** `{s.symbol}`
**Timeframe:** `{s.timeframe}`
**Trend:** {s.trend}
**Structure:** {s.structure}
**Confidence:** `{s.confidence:.0f}%`

**ENTRY:** `{s.entry:.5f}`
**SL:** `{s.sl:.5f}`
**TP1:** `{s.tp1:.5f}`
**TP2:** `{s.tp2:.5f}`

**RSI14:** `{s.rsi14 if s.rsi14 is not None else "-"}`
**MACD Hist:** `{s.macd_histogram if s.macd_histogram is not None else "-"}`
**EMA20:** `{s.ema20 if s.ema20 is not None else "-"}`
**EMA50:** `{s.ema50 if s.ema50 is not None else "-"}`

**REASONS**
{reasons}

**WARNINGS**
{warnings}

━━━━━━━━━━━━━━━━━━━━
⚠️ OTC: ราคาจาก OTC อาจแตกต่างจากตลาดจริง
"""


def notify_signal(s: Signal, force: bool = False) -> tuple[bool, str]:
    fp = signal_fingerprint(s)
    state = load_state()

    if not force and state.get("last_fingerprint") == fp:
        return True, "DUPLICATE_SUPPRESSED"

    ok, status = send_discord(format_signal(s))

    state["last_attempt_at"] = int(time.time())
    state["last_discord_status"] = status

    if ok:
        state["last_fingerprint"] = fp
        state["last_sent_at"] = int(time.time())

    save_state(state)
    return ok, status


def make_test_signal() -> Signal:
    return Signal(
        market="OTC",
        symbol="TEST-OTC",
        timeframe="1m",
        trend="BULLISH",
        structure="HH/HL",
        signal="BUY",
        confidence=72,
        entry=112.7063,
        sl=112.1328,
        tp1=113.5666,
        tp2=113.9681,
        rsi14=80.0126,
        macd_histogram=-0.01,
        ema20=112.2621,
        ema50=110.9249,
        support=108.174,
        resistance=113.1564,
        atr14=0.49873377,
        reasons=(
            "ราคาอยู่เหนือ EMA20 และ EMA20 อยู่เหนือ EMA50",
            "โครงสร้างราคาเป็น Higher High / Higher Low",
        ),
        warnings=(
            "RSI Overbought — ห้ามไล่ราคา Buy",
            "OTC MODE: ราคาอาจแตกต่างจากตลาดจริง",
            "แนะนำลดขนาดไม้และรอ Candle Confirmation",
        ),
    )


def write_last_result(result: dict) -> None:
    tmp = SIGNAL_FILE.with_name(SIGNAL_FILE.name + ".tmp")
    tmp.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp.replace(SIGNAL_FILE)


def self_test() -> dict:
    s = make_test_signal()

    print("SELF TEST   : RUNNING", flush=True)

    if not webhook_configured():
        ok = False
        status = "NOT_CONFIGURED"
    else:
        ok, status = notify_signal(s, force=True)

    result = {
        "version": APP_VERSION,
        "self_test": "PASS" if ok else "FAIL",
        "discord": "CONNECTED" if ok else status,
        "signal": asdict(s),
        "timestamp": int(time.time()),
    }

    write_last_result(result)
    return result


STOP = False


def stop_handler(signum, frame):
    global STOP
    STOP = True
    log.info("Shutdown signal received")


def startup() -> dict:
    print("=" * 60, flush=True)
    print(f"             TRADEIFY {APP_VERSION} STABLE", flush=True)
    print("=" * 60, flush=True)
    print(f"OUTPUT      : {OUTPUT_DIR}", flush=True)
    print(
        f"DISCORD     : {'CONFIGURED' if webhook_configured() else 'NOT_CONFIGURED'}",
        flush=True,
    )

    if not webhook_configured():
        print("", flush=True)
        print("⚠️ DISCORD WEBHOOK ยังไม่ได้ตั้งค่า", flush=True)
        print(
            "ตั้ง TRADEIFY_DISCORD_WEBHOOK ใน Environment ของ Container",
            flush=True,
        )
        print("", flush=True)

    result = self_test()

    print(f"SELF TEST   : {result['self_test']}", flush=True)
    print(f"DISCORD     : {result['discord']}", flush=True)
    print(f"JSON        : {SIGNAL_FILE}", flush=True)
    print("=" * 60, flush=True)

    if result["self_test"] == "PASS":
        print("✅ DISCORD TEST SENT — ตรวจห้อง Discord ได้เลย", flush=True)
    else:
        print(
            f"❌ DISCORD TEST FAILED — {result['discord']}",
            flush=True,
        )

    return result


def run_forever() -> None:
    global STOP

    heartbeat = os.getenv("TRADEIFY_HEARTBEAT", "60")
    try:
        heartbeat_seconds = max(5, int(heartbeat))
    except ValueError:
        heartbeat_seconds = 60

    print(
        f"STATUS      : ONLINE | heartbeat={heartbeat_seconds}s",
        flush=True,
    )

    while not STOP:
        time.sleep(heartbeat_seconds)
        if not STOP:
            log.info("TRADEIFY heartbeat: ONLINE")

    print("STATUS      : STOPPED", flush=True)


def main() -> int:
    os_signal.signal(os_signal.SIGTERM, stop_handler)
    os_signal.signal(os_signal.SIGINT, stop_handler)

    result = startup()

    # Explicit one-shot mode for testing/deployment health checks.
    run_once = os.getenv("TRADEIFY_RUN_ONCE", "0").strip().lower() in {
        "1", "true", "yes", "on"
    }

    if run_once:
        return 0 if result["self_test"] == "PASS" else 2

    # Keep the container alive after startup test.
    run_forever()

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        log.exception("FATAL: TRADEIFY crashed")
        raise
'''

out = Path("/mnt/data")
out.mkdir(parents=True, exist_ok=True)

py_path = out / "tradeify_2_2_3_stable.py"
py_path.write_text(code, encoding="utf-8")

env_path = out / "tradeify.env.example"
env_path.write_text(
    """# Required for Discord notifications
TRADEIFY_DISCORD_WEBHOOK=https://discord.com/api/webhooks/YOUR_ID/YOUR_TOKEN

# Keep container alive (default 0)
TRADEIFY_RUN_ONCE=0

# Heartbeat in seconds
TRADEIFY_HEARTBEAT=60

# Optional output location
TRADEIFY_OUTPUT_DIR=/app/output
""",
    encoding="utf-8",
)

print(f"สร้างไฟล์สำเร็จ: {py_path}")
print(f"ไฟล์ตั้งค่า: {env_path}")
