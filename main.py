from pathlib import Path

code = r'''#!/usr/bin/env python3
"""
TRADEIFY 2.2.2 STABLE
- Robust output directory creation
- Discord webhook with timeout/retry
- Real HTTP status verification
- Duplicate signal protection
- OTC-aware warnings
- Self-test reports Discord status honestly
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

APP_VERSION = "2.2.2"
OUTPUT_DIR = Path(os.getenv("TRADEIFY_OUTPUT_DIR", "/app/output"))
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

SIGNAL_FILE = OUTPUT_DIR / "tradeify_last_signal.json"
STATE_FILE = OUTPUT_DIR / "tradeify_state.json"
LOG_FILE = OUTPUT_DIR / "tradeify.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler(LOG_FILE, encoding="utf-8")],
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


def _state() -> dict:
    try:
        if STATE_FILE.exists():
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        log.exception("Unable to read state file")
    return {}


def _save_state(state: dict) -> None:
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(STATE_FILE)


def signal_fingerprint(signal: Signal) -> str:
    payload = json.dumps(asdict(signal), ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def discord_webhook_url() -> str:
    return (
        os.getenv("TRADEIFY_DISCORD_WEBHOOK", "").strip()
        or os.getenv("DISCORD_WEBHOOK_URL", "").strip()
        or os.getenv("DISCORD_WEBHOOK", "").strip()
    )


def send_discord(
    content: str,
    *,
    username: str = "TRADEIFY",
    retries: int = 3,
    timeout: float = 10.0,
) -> tuple[bool, str]:
    """Send a Discord webhook and verify the actual HTTP response."""
    url = discord_webhook_url()
    if not url:
        return False, "NOT_CONFIGURED"

    body = json.dumps(
        {"username": username, "content": content[:1900], "allowed_mentions": {"parse": []}},
        ensure_ascii=False,
    ).encode("utf-8")

    last_error = "UNKNOWN"
    for attempt in range(1, retries + 1):
        try:
            req = Request(
                url,
                data=body,
                headers={"Content-Type": "application/json", "User-Agent": f"TRADEIFY/{APP_VERSION}"},
                method="POST",
            )
            with urlopen(req, timeout=timeout) as response:
                status = int(response.status)
                if 200 <= status < 300:
                    log.info("Discord sent successfully: HTTP %s", status)
                    return True, f"HTTP_{status}"
                last_error = f"HTTP_{status}"
        except HTTPError as e:
            last_error = f"HTTP_{e.code}"
            log.warning("Discord attempt %s/%s failed: %s", attempt, retries, last_error)
        except (URLError, TimeoutError, OSError) as e:
            last_error = f"{type(e).__name__}: {e}"
            log.warning("Discord attempt %s/%s failed: %s", attempt, retries, last_error)
        except Exception as e:
            last_error = f"{type(e).__name__}: {e}"
            log.exception("Unexpected Discord error")

        if attempt < retries:
            time.sleep(min(2 ** (attempt - 1), 4))

    return False, last_error


def format_signal(signal: Signal) -> str:
    reasons = "\n".join(f"• {x}" for x in signal.reasons) or "• ไม่มี"
    warnings = "\n".join(f"⚠️ {x}" for x in signal.warnings) or "ไม่มี"

    return f"""🚨 **TRADEIFY {APP_VERSION} — {signal.signal}**
━━━━━━━━━━━━━━━━━━━━
**Market:** {signal.market}
**Symbol:** `{signal.symbol}`
**TF:** `{signal.timeframe}`
**Trend:** {signal.trend}
**Structure:** {signal.structure}
**Confidence:** {signal.confidence:.0f}%

**Entry:** `{signal.entry:.5f}`
**SL:** `{signal.sl:.5f}`
**TP1:** `{signal.tp1:.5f}`
**TP2:** `{signal.tp2:.5f}`

**RSI14:** {signal.rsi14 if signal.rsi14 is not None else "-"}
**MACD Hist:** {signal.macd_histogram if signal.macd_histogram is not None else "-"}
**EMA20:** {signal.ema20 if signal.ema20 is not None else "-"}
**EMA50:** {signal.ema50 if signal.ema50 is not None else "-"}

**REASONS**
{reasons}

**WARNINGS**
{warnings}
━━━━━━━━━━━━━━━━━━━━
OTC: สัญญาณเป็นการวิเคราะห์จากราคา OTC ไม่รับประกันว่าตรงกับตลาดจริง
"""


def notify_signal(signal: Signal, force: bool = False) -> tuple[bool, str]:
    fp = signal_fingerprint(signal)
    state = _state()

    if not force and state.get("last_fingerprint") == fp:
        log.info("Duplicate signal suppressed")
        return True, "DUPLICATE_SUPPRESSED"

    ok, status = send_discord(format_signal(signal))
    if ok:
        state["last_fingerprint"] = fp
        state["last_sent_at"] = int(time.time())
        state["last_discord_status"] = status
        _save_state(state)
    else:
        state["last_discord_status"] = status
        _save_state(state)

    return ok, status


def self_test() -> dict:
    test = Signal(
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

    ok, status = notify_signal(test, force=True)

    result = {
        "version": APP_VERSION,
        "self_test": "PASS" if ok else "FAIL",
        "discord": "CONNECTED" if ok else status,
        "signal": asdict(test),
        "timestamp": int(time.time()),
    }
    SIGNAL_FILE.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


if __name__ == "__main__":
    print("=" * 59)
    print(f"             TRADEIFY {APP_VERSION} STABLE")
    print("=" * 59)
    result = self_test()
    print("SELF TEST :", result["self_test"])
    print("DISCORD   :", result["discord"])
    print("JSON saved:", SIGNAL_FILE)

    if result["self_test"] != "PASS":
        print()
        print("⚠️ Discord ยังส่งไม่ได้")
        print("ตั้ง environment variable:")
        print("TRADEIFY_DISCORD_WEBHOOK=<YOUR_DISCORD_WEBHOOK>")
        print("แล้ว restart container")
'''
out = Path("/mnt/data")
out.mkdir(parents=True, exist_ok=True)
path = out / "tradeify_v2_2_2_stable.py"
path.write_text(code, encoding="utf-8")

# Also make a small config example for deployment.
env = """# TRADEIFY Discord configuration
TRADEIFY_DISCORD_WEBHOOK=https://discord.com/api/webhooks/PUT_YOUR_WEBHOOK_HERE
TRADEIFY_OUTPUT_DIR=/app/output
"""
env_path = out / "tradeify.env.example"
env_path.write_text(env, encoding="utf-8")

print(f"สร้างไฟล์สำเร็จ: {path}")
print(f"ไฟล์ตั้งค่า: {env_path}")
