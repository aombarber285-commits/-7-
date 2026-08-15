from pathlib import Path
import os
import sys

code = r'''#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import logging
import os
import signal
import sys
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

APP_VERSION = "2.2.4"

# Runtime storage — ห้ามพึ่ง /mnt/data
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
class SignalData:
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


def get_webhook() -> str:
    return (
        os.getenv("TRADEIFY_DISCORD_WEBHOOK", "").strip()
        or os.getenv("DISCORD_WEBHOOK_URL", "").strip()
        or os.getenv("DISCORD_WEBHOOK", "").strip()
    )


def load_state() -> dict:
    try:
        if STATE_FILE.exists():
            value = json.loads(
                STATE_FILE.read_text(encoding="utf-8")
            )
            if isinstance(value, dict):
                return value
    except Exception:
        log.exception("Cannot read state")
    return {}


def save_state(state: dict) -> None:
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp.replace(STATE_FILE)


def fingerprint(signal_data: SignalData) -> str:
    raw = json.dumps(
        asdict(signal_data),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode()).hexdigest()


def send_discord(
    message: str,
    retries: int = 3,
    timeout: int = 10,
) -> tuple[bool, str]:

    webhook = get_webhook()

    if not webhook:
        return False, "NOT_CONFIGURED"

    payload = {
        "username": "TRADEIFY",
        "content": message[:1900],
        "allowed_mentions": {
            "parse": []
        },
    }

    data = json.dumps(
        payload,
        ensure_ascii=False,
    ).encode("utf-8")

    last_error = "UNKNOWN"

    for attempt in range(1, retries + 1):

        try:
            request = Request(
                webhook,
                data=data,
                method="POST",
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": f"TRADEIFY/{APP_VERSION}",
                },
            )

            with urlopen(
                request,
                timeout=timeout,
            ) as response:

                status = int(
                    getattr(response, "status", 0)
                )

            if 200 <= status < 300:
                log.info(
                    "DISCORD SUCCESS HTTP_%s",
                    status,
                )
                return True, f"HTTP_{status}"

            last_error = f"HTTP_{status}"

        except HTTPError as exc:

            last_error = f"HTTP_{exc.code}"

            try:
                detail = exc.read().decode(
                    "utf-8",
                    errors="replace",
                )[:500]
            except Exception:
                detail = ""

            log.error(
                "DISCORD ERROR attempt=%s/%s status=%s detail=%s",
                attempt,
                retries,
                last_error,
                detail,
            )

        except (
            URLError,
            TimeoutError,
            OSError,
        ) as exc:

            last_error = (
                f"{type(exc).__name__}: {exc}"
            )

            log.error(
                "DISCORD NETWORK ERROR attempt=%s/%s %s",
                attempt,
                retries,
                last_error,
            )

        except Exception as exc:

            last_error = (
                f"{type(exc).__name__}: {exc}"
            )

            log.exception(
                "DISCORD UNKNOWN ERROR"
            )

        if attempt < retries:
            time.sleep(
                min(
                    2 ** (attempt - 1),
                    4,
                )
            )

    return False, last_error


def format_signal(s: SignalData) -> str:

    if s.signal.upper() == "BUY":
        icon = "🟢"
    elif s.signal.upper() == "SELL":
        icon = "🔴"
    else:
        icon = "🟡"

    reasons = "\n".join(
        f"• {x}" for x in s.reasons
    ) or "• ไม่มี"

    warnings = "\n".join(
        f"⚠️ {x}" for x in s.warnings
    ) or "ไม่มี"

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

**RSI14:** `{s.rsi14}`
**MACD Histogram:** `{s.macd_histogram}`
**EMA20:** `{s.ema20}`
**EMA50:** `{s.ema50}`

**REASONS**
{reasons}

**WARNINGS**
{warnings}

━━━━━━━━━━━━━━━━━━━━
⚠️ OTC MODE
ราคาจาก OTC อาจแตกต่างจากตลาดจริง
"""


def notify(signal_data: SignalData) -> tuple[bool, str]:

    state = load_state()

    fp = fingerprint(signal_data)

    # ไม่ส่งซ้ำ
    if state.get("last_fingerprint") == fp:
        return True, "DUPLICATE_SUPPRESSED"

    ok, status = send_discord(
        format_signal(signal_data)
    )

    state["last_attempt"] = int(time.time())
    state["last_discord_status"] = status

    if ok:
        state["last_fingerprint"] = fp
        state["last_sent_at"] = int(time.time())

    save_state(state)

    return ok, status


def test_signal() -> SignalData:

    return SignalData(
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


def save_result(result: dict) -> None:

    tmp = SIGNAL_FILE.with_suffix(".tmp")

    tmp.write_text(
        json.dumps(
            result,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    tmp.replace(SIGNAL_FILE)


def startup_test() -> dict:

    print("")
    print("=" * 60)
    print(
        f"        TRADEIFY {APP_VERSION} STABLE"
    )
    print("=" * 60)

    print(
        "OUTPUT :",
        OUTPUT_DIR,
        flush=True,
    )

    webhook = get_webhook()

    if webhook:
        print(
            "DISCORD : CONFIGURED",
            flush=True,
        )
    else:
        print(
            "DISCORD : NOT_CONFIGURED",
            flush=True,
        )

    print(
        "SELF TEST : RUNNING",
        flush=True,
    )

    signal_data = test_signal()

    ok, status = notify(signal_data)

    result = {
        "version": APP_VERSION,
        "self_test": (
            "PASS" if ok else "FAIL"
        ),
        "discord": (
            "CONNECTED"
            if ok
            else status
        ),
        "signal": asdict(signal_data),
        "timestamp": int(time.time()),
    }

    save_result(result)

    print(
        "SELF TEST :",
        result["self_test"],
        flush=True,
    )

    print(
        "DISCORD   :",
        result["discord"],
        flush=True,
    )

    print(
        "JSON      :",
        SIGNAL_FILE,
        flush=True,
    )

    if ok:

        print("")
        print(
            "✅ TEST SIGNAL SENT TO DISCORD",
            flush=True,
        )
        print(
            "ตรวจห้อง Discord ได้เลย",
            flush=True,
        )

    else:

        print("")
        print(
            "❌ DISCORD TEST FAILED",
            flush=True,
        )

        print(
            "ERROR :",
            status,
            flush=True,
        )

    print("=" * 60)

    return result


STOP = False


def stop_handler(signum, frame):
    global STOP
    STOP = True
    log.info("Stopping TRADEIFY...")


def heartbeat():

    raw = os.getenv(
        "TRADEIFY_HEARTBEAT",
        "60",
    )

    try:
        seconds = max(
            5,
            int(raw),
        )
    except ValueError:
        seconds = 60

    print(
        f"STATUS : ONLINE | heartbeat={seconds}s",
        flush=True,
    )

    while not STOP:

        time.sleep(seconds)

        if not STOP:

            log.info(
                "TRADEIFY heartbeat: ONLINE"
            )

    print(
        "STATUS : STOPPED",
        flush=True,
    )


def main() -> int:

    signal.signal(
        signal.SIGTERM,
        stop_handler,
    )

    signal.signal(
        signal.SIGINT,
        stop_handler,
    )

    result = startup_test()

    run_once = (
        os.getenv(
            "TRADEIFY_RUN_ONCE",
            "0",
        )
        .strip()
        .lower()
        in {
            "1",
            "true",
            "yes",
            "on",
        }
    )

    if run_once:

        return (
            0
            if result["self_test"] == "PASS"
            else 2
        )

    heartbeat()

    return 0


if __name__ == "__main__":

    try:
        raise SystemExit(main())

    except Exception:

        log.exception(
            "FATAL TRADEIFY ERROR"
        )

        raise
'''


# ============================================================
# BUILD
# ============================================================

# ใช้ /app เป็น runtime location
runtime_dir = Path("/app")
runtime_dir.mkdir(parents=True, exist_ok=True)

runtime_file = runtime_dir / "tradeify_runtime.py"

runtime_file.write_text(
    code,
    encoding="utf-8",
)

print(
    f"TRADEIFY runtime created: {runtime_file}",
    flush=True,
)


# ============================================================
# ENV EXAMPLE
# ============================================================

env_file = runtime_dir / "tradeify.env.example"

env_file.write_text(
    """# ==========================================
# TRADEIFY DISCORD
# ==========================================

TRADEIFY_DISCORD_WEBHOOK=https://discord.com/api/webhooks/YOUR_ID/YOUR_TOKEN

# 0 = keep container alive
# 1 = run Discord self-test then exit
TRADEIFY_RUN_ONCE=0

# heartbeat seconds
TRADEIFY_HEARTBEAT=60

# persistent output
TRADEIFY_OUTPUT_DIR=/app/output
""",
    encoding="utf-8",
)

print(
    f"ENV example created: {env_file}",
    flush=True,
)


# ============================================================
# IMPORTANT
# Run the REAL TRADEIFY process now.
# Do NOT exit after generating the file.
# ============================================================

print(
    "Starting TRADEIFY runtime...",
    flush=True,
)

os.execv(
    sys.executable,
    [
        sys.executable,
        str(runtime_file),
    ],
)
