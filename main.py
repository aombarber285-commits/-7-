from pathlib import Path
import ast, re

src = Path("/mnt/data/tradeify_v2_1_utf8_fix.py")
dst = Path("/mnt/data/tradeify_v2_2.py")

code = src.read_text(encoding="utf-8")

# ---- V2.2 header/version ----
code = code.replace(
    'TRADEIFY V2.1\n',
    'TRADEIFY V2.2\n',
    1
)
code = code.replace(
    'tradeify_v21.lock',
    'tradeify_v22.lock'
)

# ---- Config: automatic feed + automatic mode tuning ----
code = code.replace(
    'OTC_API_URL = os.getenv("OTC_API_URL", "").strip()\n'
    'OTC_API_KEY = os.getenv("OTC_API_KEY", "").strip()\n'
    'REQUEST_TIMEOUT = max(5, int(os.getenv("REQUEST_TIMEOUT", "15")))\n'
    'YF_RETRIES = max(1, int(os.getenv("YF_RETRIES", "2")))\n'
    'MAX_DATA_AGE_SECONDS = max(0, int(os.getenv("MAX_DATA_AGE_SECONDS", "1200")))\n',
    '''OTC_API_URL = os.getenv("OTC_API_URL", "").strip()
OTC_API_URL_2 = os.getenv("OTC_API_URL_2", "").strip()
OTC_API_KEY = os.getenv("OTC_API_KEY", "").strip()
REQUEST_TIMEOUT = max(5, int(os.getenv("REQUEST_TIMEOUT", "15")))
YF_RETRIES = max(1, int(os.getenv("YF_RETRIES", "2")))

# V2.2 feed policy:
# - LIVE: Yahoo is allowed only when data is fresh.
# - OTC: configured OTC API is preferred. Yahoo is only a proxy fallback
#   when explicitly enabled AND its candles are fresh.
# - Never invent/forward-fill candles.
ALLOW_YAHOO_OTC_PROXY = os.getenv(
    "ALLOW_YAHOO_OTC_PROXY", "false"
).lower() in ("1", "true", "yes", "on")

# Maximum age of the LAST CLOSED candle.
# Auto defaults are intentionally different for LIVE/OTC.
MAX_DATA_AGE_LIVE = max(
    60, int(os.getenv("MAX_DATA_AGE_LIVE", "1200"))
)
MAX_DATA_AGE_OTC = max(
    60, int(os.getenv("MAX_DATA_AGE_OTC", "1200"))
)

# V2.2 automatic setup thresholds.
AUTO_TUNING = os.getenv("AUTO_TUNING", "true").lower() in (
    "1", "true", "yes", "on"
)
BASE_MIN_SETUP = max(
    50, int(os.getenv("BASE_MIN_SETUP", "62"))
)
OTC_MIN_SETUP = max(
    50, int(os.getenv("OTC_MIN_SETUP", "60"))
)
LIVE_MIN_SETUP = max(
    50, int(os.getenv("LIVE_MIN_SETUP", "62"))
)
'''
)

# ---- Add helpers immediately before get_candles ----
needle = '''def get_candles(symbol, interval, period="5d", limit=300):
'''
helpers = r'''def current_max_data_age():
    """Return the freshness limit for the current automatic mode."""
    if AUTO_TUNING:
        return MAX_DATA_AGE_OTC if mode_now() == "OTC" else MAX_DATA_AGE_LIVE
    return MAX_DATA_AGE_LIVE


def data_is_fresh(candles, interval):
    """Reject stale feeds; never manufacture a current candle."""
    if not candles:
        return False

    closed = closed_only(candles, interval)
    if not closed:
        return False

    latest = closed[-1]
    age = time.time() - (
        latest["timestamp"] + interval_seconds(interval)
    )

    # 15M can tolerate the same configured feed age.  The 5M scanner
    # remains the critical gate because it determines entry timing.
    return age <= current_max_data_age()


def feed_label(source, fresh):
    if source == "otc":
        return "OTC_API" if fresh else "OTC_API_STALE"
    if source == "yahoo":
        return "YAHOO_PROXY" if fresh else "YAHOO_STALE"
    return source.upper()


def get_candles(symbol, interval, period="5d", limit=300):
'''
code = code.replace(needle, helpers, 1)

# ---- Replace get_candles with V2.2 source-selection logic ----
start = code.index('def get_candles(symbol, interval, period="5d", limit=300):')
end = code.index('\ndef is_closed(candle, interval):', start)

new_get = r'''def get_candles(symbol, interval, period="5d", limit=300):
    """
    V2.2 automatic feed switching.

    OTC:
      1) OTC_API_URL
      2) OTC_API_URL_2
      3) Yahoo proxy only if ALLOW_YAHOO_OTC_PROXY=true
      4) otherwise return [] and pause signal generation

    LIVE:
      1) Yahoo
      2) optional OTC endpoint only if Yahoo is unavailable/freshness fails

    A stale feed is NEVER accepted as current data.
    """
    mode = mode_now()

    if mode == "OTC":
        otc_urls = [
            url for url in (OTC_API_URL, OTC_API_URL_2)
            if url
        ]

        for idx, url in enumerate(otc_urls, start=1):
            old_url = OTC_API_URL
            try:
                # Reuse the existing request parser without changing global config.
                params = {
                    "symbol": symbol.replace("/", ""),
                    "interval": interval,
                    "limit": limit,
                }
                if OTC_API_KEY:
                    params["apikey"] = OTC_API_KEY

                r = requests.get(
                    url,
                    params=params,
                    timeout=REQUEST_TIMEOUT,
                )
                r.raise_for_status()
                candles = normalize_otc_response(r.json())

                if candles and data_is_fresh(candles, interval):
                    latest = closed_only(candles, interval)[-1]
                    log(
                        f"{symbol} {interval}: feed=OTC_API#{idx} "
                        f"last={latest['datetime']} UTC"
                    )
                    return candles

                if candles:
                    stale_log(
                        symbol,
                        interval,
                        closed_only(candles, interval)[-1]
                        if closed_only(candles, interval)
                        else None,
                    )
            except Exception as e:
                log(f"OTC API#{idx} {symbol} {interval}: {e}")

        if ALLOW_YAHOO_OTC_PROXY:
            yahoo = get_yahoo_candles(symbol, interval, period)
            if yahoo and data_is_fresh(yahoo, interval):
                latest = closed_only(yahoo, interval)[-1]
                log(
                    f"{symbol} {interval}: feed=YAHOO_PROXY "
                    f"last={latest['datetime']} UTC"
                )
                return yahoo

            if yahoo:
                stale_log(
                    symbol,
                    interval,
                    closed_only(yahoo, interval)[-1]
                    if closed_only(yahoo, interval)
                    else None,
                )

        log(
            f"{symbol} {interval}: OTC feed unavailable/freshness failed; "
            f"signal blocked"
        )
        return []

    # LIVE mode
    yahoo = get_yahoo_candles(symbol, interval, period)
    if yahoo and data_is_fresh(yahoo, interval):
        latest = closed_only(yahoo, interval)[-1]
        log(
            f"{symbol} {interval}: feed=YAHOO "
            f"last={latest['datetime']} UTC"
        )
        return yahoo

    if yahoo:
        stale_log(
            symbol,
            interval,
            closed_only(yahoo, interval)[-1]
            if closed_only(yahoo, interval)
            else None,
        )

    # If a live API is configured, allow it as a fallback.
    if OTC_API_URL:
        otc = get_otc_candles(symbol, interval, limit)
        if otc and data_is_fresh(otc, interval):
            latest = closed_only(otc, interval)[-1]
            log(
                f"{symbol} {interval}: feed=API_FALLBACK "
                f"last={latest['datetime']} UTC"
            )
            return otc

    log(
        f"{symbol} {interval}: no fresh LIVE feed; "
        f"signal blocked"
    )
    return []

'''
code = code[:start] + new_get + code[end+1:]

# ---- Make scan threshold automatic instead of hard-coded 62 ----
code = code.replace(
    '    if master["setup_strength"] < 62:\n        return None\n',
    '''    min_setup = (
        OTC_MIN_SETUP if mode_now() == "OTC"
        else LIVE_MIN_SETUP
    ) if AUTO_TUNING else BASE_MIN_SETUP

    if master["setup_strength"] < min_setup:
        return None
''',
    1
)

# ---- Make stale check explicit for the 5M signal path ----
old = '''    c5 = closed_only(
        get_candles(
            symbol,
            "5m",
            "5d",
        )
    )

    if len(c5) < 70:
        return None
'''
new = '''    c5 = closed_only(
        get_candles(
            symbol,
            "5m",
            "5d",
        )
    )

    if len(c5) < 70:
        return None

    # Critical gate: the latest closed 5M candle must be fresh.
    if not data_is_fresh(c5, "5m"):
        stale_log(symbol, "5m", c5[-1] if c5 else None)
        return None
'''
code = code.replace(old, new, 1)

# ---- Version strings / startup reporting ----
code = code.replace(
    "[TRADEIFY V2.1 STARTED]",
    "[TRADEIFY V2.2 STARTED]"
)
code = code.replace(
    '"V2.1"',
    '"V2.2"'
)
code = code.replace(
    "TRADEIFY 15M + 5M + 3 OPPORTUNITIES — V2.1",
    "TRADEIFY 15M + 5M + 3 OPPORTUNITIES — V2.2"
)

# Add V2.2 startup details after existing OTC API line.
old_start = '        f"📡 OTC API: **{\'ON\' if OTC_API_URL else \'OFF\'}**\\n\\n"'
new_start = '''        f"📡 OTC API #1: **{'ON' if OTC_API_URL else 'OFF'}**\\n"
        f"📡 OTC API #2: **{'ON' if OTC_API_URL_2 else 'OFF'}**\\n"
        f"🔄 Auto Feed Switching: **ON**\\n"
        f"🎚️ Auto Tuning: **{'ON' if AUTO_TUNING else 'OFF'}**\\n"
        f"🧪 OTC Yahoo Proxy: **{'ON' if ALLOW_YAHOO_OTC_PROXY else 'OFF'}**\\n\\n"'''
code = code.replace(old_start, new_start, 1)

# ---- Main logging ----
code = code.replace(
    '    log(f"OTC API: {\'CONFIGURED\' if OTC_API_URL else \'NOT CONFIGURED\'}")\n',
    '''    log(f"OTC API #1: {'CONFIGURED' if OTC_API_URL else 'NOT CONFIGURED'}")
    log(f"OTC API #2: {'CONFIGURED' if OTC_API_URL_2 else 'NOT CONFIGURED'}")
    log(f"Auto feed switching: ON")
    log(f"Auto tuning: {'ON' if AUTO_TUNING else 'OFF'}")
    log(f"OTC Yahoo proxy: {'ON' if ALLOW_YAHOO_OTC_PROXY else 'OFF'}")
'''
)

# ---- Fix docs mentioning V2.1 ----
code = code.replace("V2.1", "V2.2")

# ---- Ensure generated file is valid Python ----
ast.parse(code)

dst.write_text(code, encoding="utf-8")

print(f"สร้างไฟล์ V2.2 สำเร็จ: {dst}")
print(f"จำนวนบรรทัด: {len(code.splitlines())}")
print("ตรวจ Python syntax: PASS")
print()
print("V2.2 ที่แก้แล้ว:")
print("1) AUTO feed switching")
print("2) รองรับ OTC_API_URL + OTC_API_URL_2")
print("3) ไม่รับ stale 5M/15M เป็น signal")
print("4) OTC จะหยุด signal หากไม่มี fresh OTC feed")
print("5) Yahoo OTC proxy เปิดได้ด้วย ALLOW_YAHOO_OTC_PROXY=true เท่านั้น")
print("6) Auto tuning threshold แยก LIVE/OTC")
print("7) UTF-8 Discord fix จาก V2.1 ยังคงอยู่")
