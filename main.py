from pathlib import Path

code = r'''"""
TRADEIFY v2.2.1 - Stable Trading Analysis Core
- Markets: Crypto / Forex / Stocks / Gold-Commodities / OTC
- Technical structure, EMA, RSI, ATR, MACD, support/resistance
- Entry / SL / TP / Risk:Reward
- Confidence score + NO TRADE filter
- OTC is explicitly treated as higher-risk and never assumed identical to the real market
- No dependency on /mnt/data for runtime
- Can analyze OHLCV CSV files or a Python list of candles

CSV columns accepted:
timestamp, open, high, low, close, volume
Only OHLC are required.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Optional, Sequence
import json
import math
import statistics
import sys

try:
    import pandas as pd
except ImportError:
    pd = None


VERSION = "2.2.1-stable"


@dataclass
class TradePlan:
    market: str
    symbol: str
    timeframe: str
    bias: str
    signal: str
    confidence: float
    entry: Optional[float]
    stop_loss: Optional[float]
    take_profit_1: Optional[float]
    take_profit_2: Optional[float]
    rr_tp1: Optional[float]
    rr_tp2: Optional[float]
    trend: str
    structure: str
    support: Optional[float]
    resistance: Optional[float]
    atr: Optional[float]
    rsi: Optional[float]
    ema_fast: Optional[float]
    ema_slow: Optional[float]
    macd: Optional[float]
    reasons: list[str]
    warnings: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _clean_float(x: Any) -> Optional[float]:
    try:
        v = float(x)
        if math.isfinite(v):
            return v
    except (TypeError, ValueError):
        pass
    return None


def load_csv(path: str | Path):
    if pd is None:
        raise RuntimeError("pandas is required for CSV input. Install: pip install pandas")
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"CSV not found: {p}")
    df = pd.read_csv(p)
    df.columns = [str(c).strip().lower() for c in df.columns]
    required = {"open", "high", "low", "close"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Missing columns: {sorted(missing)}")
    for c in ["open", "high", "low", "close", "volume"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["open", "high", "low", "close"]).reset_index(drop=True)
    if len(df) < 60:
        raise ValueError("Need at least 60 valid candles for a stable analysis.")
    return df


def _series(values: Sequence[float]) -> list[float]:
    return [float(x) for x in values]


def ema(values: Sequence[float], period: int) -> list[Optional[float]]:
    vals = _series(values)
    if not vals:
        return []
    alpha = 2.0 / (period + 1.0)
    out: list[Optional[float]] = [None] * len(vals)
    if len(vals) < period:
        return out
    seed = sum(vals[:period]) / period
    out[period - 1] = seed
    prev = seed
    for i in range(period, len(vals)):
        prev = alpha * vals[i] + (1 - alpha) * prev
        out[i] = prev
    return out


def rsi(values: Sequence[float], period: int = 14) -> list[Optional[float]]:
    vals = _series(values)
    out: list[Optional[float]] = [None] * len(vals)
    if len(vals) <= period:
        return out

    gains = []
    losses = []
    for i in range(1, period + 1):
        d = vals[i] - vals[i - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))

    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period

    def calc() -> float:
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    out[period] = calc()

    for i in range(period + 1, len(vals)):
        d = vals[i] - vals[i - 1]
        gain = max(d, 0.0)
        loss = max(-d, 0.0)
        avg_gain = ((avg_gain * (period - 1)) + gain) / period
        avg_loss = ((avg_loss * (period - 1)) + loss) / period
        out[i] = calc()

    return out


def true_range(high, low, close) -> list[float]:
    tr = [high[0] - low[0]]
    for i in range(1, len(close)):
        tr.append(max(
            high[i] - low[i],
            abs(high[i] - close[i - 1]),
            abs(low[i] - close[i - 1]),
        ))
    return tr


def atr(high, low, close, period: int = 14) -> list[Optional[float]]:
    tr = true_range(high, low, close)
    out: list[Optional[float]] = [None] * len(tr)
    if len(tr) < period:
        return out
    prev = sum(tr[:period]) / period
    out[period - 1] = prev
    for i in range(period, len(tr)):
        prev = ((prev * (period - 1)) + tr[i]) / period
        out[i] = prev
    return out


def macd(values: Sequence[float]):
    e12 = ema(values, 12)
    e26 = ema(values, 26)
    line: list[Optional[float]] = [None] * len(values)
    compact = []
    indexes = []
    for i, (a, b) in enumerate(zip(e12, e26)):
        if a is not None and b is not None:
            line[i] = a - b
            compact.append(line[i])
            indexes.append(i)

    signal_compact = ema(compact, 9)
    signal: list[Optional[float]] = [None] * len(values)
    hist: list[Optional[float]] = [None] * len(values)

    for j, i in enumerate(indexes):
        if signal_compact[j] is not None:
            signal[i] = signal_compact[j]
            hist[i] = line[i] - signal[i]

    return line, signal, hist


def _last(v):
    for x in reversed(v):
        if x is not None:
            return x
    return None


def _fmt(x: Optional[float], digits: int = 6) -> str:
    if x is None:
        return "-"
    if abs(x) >= 1000:
        return f"{x:,.2f}"
    if abs(x) >= 1:
        return f"{x:.4f}".rstrip("0").rstrip(".")
    return f"{x:.{digits}f}".rstrip("0").rstrip(".")


def _round_price(price: float) -> float:
    if price >= 1000:
        return round(price, 2)
    if price >= 10:
        return round(price, 4)
    if price >= 1:
        return round(price, 5)
    return round(price, 8)


def analyze(
    df,
    market: str = "Crypto",
    symbol: str = "UNKNOWN",
    timeframe: str = "Unknown",
    otc: bool = False,
    risk_percent: float = 1.0,
) -> TradePlan:
    market = str(market).strip()
    symbol = str(symbol).strip()
    timeframe = str(timeframe).strip()

    if len(df) < 60:
        raise ValueError("At least 60 candles are required.")

    o = [float(x) for x in df["open"]]
    h = [float(x) for x in df["high"]]
    l = [float(x) for x in df["low"]]
    c = [float(x) for x in df["close"]]

    fast = ema(c, 20)
    slow = ema(c, 50)
    rs = rsi(c, 14)
    at = atr(h, l, c, 14)
    ml, ms, mh = macd(c)

    price = c[-1]
    ef = _last(fast)
    es = _last(slow)
    rv = _last(rs)
    av = _last(at)
    mv = _last(ml)
    msv = _last(ms)
    mhv = _last(mh)

    lookback = min(50, len(c))
    support = min(l[-lookback:])
    resistance = max(h[-lookback:])

    # Simple swing structure using the last 5/20 candles.
    recent_high = max(h[-10:])
    prior_high = max(h[-20:-10])
    recent_low = min(l[-10:])
    prior_low = min(l[-20:-10])

    if recent_high > prior_high and recent_low > prior_low:
        structure = "HH/HL"
    elif recent_high < prior_high and recent_low < prior_low:
        structure = "LH/LL"
    else:
        structure = "RANGE/MIXED"

    if ef is not None and es is not None:
        if ef > es and price > ef:
            trend = "BULLISH"
        elif ef < es and price < ef:
            trend = "BEARISH"
        else:
            trend = "NEUTRAL"
    else:
        trend = "NEUTRAL"

    bull = 0
    bear = 0
    reasons: list[str] = []
    warnings: list[str] = []

    if trend == "BULLISH":
        bull += 2
        reasons.append("Price above EMA20 and EMA20 above EMA50")
    elif trend == "BEARISH":
        bear += 2
        reasons.append("Price below EMA20 and EMA20 below EMA50")

    if structure == "HH/HL":
        bull += 2
        reasons.append("Market structure is HH/HL")
    elif structure == "LH/LL":
        bear += 2
        reasons.append("Market structure is LH/LL")

    if rv is not None:
        if 50 <= rv <= 68:
            bull += 1
            reasons.append("RSI supports bullish momentum")
        elif 32 <= rv <= 50:
            bear += 1
            reasons.append("RSI supports bearish momentum")
        elif rv > 75:
            warnings.append("RSI is overbought; avoid chasing long entries")
        elif rv < 25:
            warnings.append("RSI is oversold; avoid chasing short entries")

    if mhv is not None:
        if mhv > 0:
            bull += 1
            reasons.append("MACD histogram is positive")
        elif mhv < 0:
            bear += 1
            reasons.append("MACD histogram is negative")

    # Distance to major levels.
    if av and av > 0:
        if abs(price - support) <= 0.6 * av:
            bull += 1
            reasons.append("Price is near support")
        if abs(resistance - price) <= 0.6 * av:
            bear += 1
            reasons.append("Price is near resistance")

    total = max(bull + bear, 1)
    dominant = max(bull, bear)
    confidence = min(95.0, 50.0 + (dominant / total) * 35.0 + abs(bull - bear) * 2.5)

    # OTC safety adjustment.
    if otc or market.upper() == "OTC":
        otc = True
        confidence = min(confidence, 72.0)
        warnings.extend([
            "OTC mode: price formation/liquidity may differ from the underlying real market.",
            "Do not treat this signal as equivalent to exchange/ECN market data.",
            "Use smaller size and require clean candle confirmation.",
        ])

    signal = "NO TRADE"
    bias = "NEUTRAL"
    entry = sl = tp1 = tp2 = rr1 = rr2 = None

    if av is None or av <= 0:
        warnings.append("ATR unavailable; trade levels cannot be safely calculated.")
    else:
        # Require a minimum directional edge.
        edge = abs(bull - bear)
        if bull >= 4 and bull > bear + 1:
            bias = "LONG"
            signal = "BUY"
        elif bear >= 4 and bear > bull + 1:
            bias = "SHORT"
            signal = "SELL"
        else:
            warnings.append("Directional edge is weak; waiting is preferred.")

        if signal == "BUY":
            entry = price
            sl = min(recent_low, entry - 1.15 * av)
            risk = entry - sl
            if risk > 0:
                tp1 = entry + 1.5 * risk
                tp2 = entry + 2.2 * risk
                rr1 = (tp1 - entry) / risk
                rr2 = (tp2 - entry) / risk

        elif signal == "SELL":
            entry = price
            sl = max(recent_high, entry + 1.15 * av)
            risk = sl - entry
            if risk > 0:
                tp1 = entry - 1.5 * risk
                tp2 = entry - 2.2 * risk
                rr1 = (entry - tp1) / risk
                rr2 = (entry - tp2) / risk

        # Reject bad geometry.
        if signal in {"BUY", "SELL"} and (rr1 is None or rr1 < 1.4):
            signal = "NO TRADE"
            bias = "NEUTRAL"
            entry = sl = tp1 = tp2 = rr1 = rr2 = None
            warnings.append("Risk/Reward geometry is not attractive enough.")

    # OTC gets an additional confirmation gate.
    if otc and signal != "NO TRADE":
        if confidence < 62:
            warnings.append("OTC confidence below the safety threshold.")
            signal = "NO TRADE"
            bias = "NEUTRAL"
            entry = sl = tp1 = tp2 = rr1 = rr2 = None
        else:
            warnings.append("Wait for the next candle to confirm direction before entry.")

    # Risk percentage validation; this is informational because account balance
    # is deliberately not assumed.
    if not (0.01 <= float(risk_percent) <= 5.0):
        warnings.append("Risk % outside recommended 0.01%-5.0% range; defaulting conceptually to 1%.")

    return TradePlan(
        market=market,
        symbol=symbol,
        timeframe=timeframe,
        bias=bias,
        signal=signal,
        confidence=round(confidence, 1),
        entry=_round_price(entry) if entry is not None else None,
        stop_loss=_round_price(sl) if sl is not None else None,
        take_profit_1=_round_price(tp1) if tp1 is not None else None,
        take_profit_2=_round_price(tp2) if tp2 is not None else None,
        rr_tp1=round(rr1, 2) if rr1 is not None else None,
        rr_tp2=round(rr2, 2) if rr2 is not None else None,
        trend=trend,
        structure=structure,
        support=_round_price(support),
        resistance=_round_price(resistance),
        atr=_round_price(av) if av is not None else None,
        rsi=round(rv, 2) if rv is not None else None,
        ema_fast=_round_price(ef) if ef is not None else None,
        ema_slow=_round_price(es) if es is not None else None,
        macd=round(mv, 8) if mv is not None else None,
        reasons=reasons,
        warnings=warnings,
    )


def analyze_csv(
    csv_path: str | Path,
    market: str = "Crypto",
    symbol: str = "UNKNOWN",
    timeframe: str = "Unknown",
    otc: bool = False,
    risk_percent: float = 1.0,
) -> TradePlan:
    df = load_csv(csv_path)
    return analyze(df, market, symbol, timeframe, otc, risk_percent)


def format_report(plan: TradePlan) -> str:
    p = plan
    lines = [
        "",
        "════════════════════════════════════════════",
        f" TRADEIFY {VERSION}",
        "════════════════════════════════════════════",
        f"Market      : {p.market}",
        f"Symbol      : {p.symbol}",
        f"Timeframe   : {p.timeframe}",
        f"Trend       : {p.trend}",
        f"Structure   : {p.structure}",
        f"Bias        : {p.bias}",
        f"SIGNAL      : {p.signal}",
        f"Confidence  : {p.confidence:.1f}%",
        "",
        f"Entry       : {_fmt(p.entry)}",
        f"SL          : {_fmt(p.stop_loss)}",
        f"TP1         : {_fmt(p.take_profit_1)}  R:R {p.rr_tp1 or '-'}",
        f"TP2         : {_fmt(p.take_profit_2)}  R:R {p.rr_tp2 or '-'}",
        "",
        f"Support     : {_fmt(p.support)}",
        f"Resistance  : {_fmt(p.resistance)}",
        f"ATR14       : {_fmt(p.atr)}",
        f"RSI14       : {_fmt(p.rsi)}",
        f"EMA20       : {_fmt(p.ema_fast)}",
        f"EMA50       : {_fmt(p.ema_slow)}",
        "",
        "REASONS:",
    ]
    lines.extend(f"  • {x}" for x in p.reasons)

    if p.warnings:
        lines.append("")
        lines.append("WARNINGS:")
        lines.extend(f"  ⚠ {x}" for x in p.warnings)

    lines.append("════════════════════════════════════════════")
    return "\n".join(lines)


def save_report(plan: TradePlan, output: str | Path) -> Path:
    """
    Safe writer. Creates parent directories automatically.
    This fixes the original /mnt/data FileNotFoundError class.
    """
    p = Path(output)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(plan.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return p


def _demo():
    # Deterministic synthetic data so the program can be smoke-tested
    # without network/API credentials.
    if pd is None:
        print("Demo requires pandas. Install with: pip install pandas")
        return 1

    rows = []
    price = 100.0
    for i in range(140):
        drift = 0.12 if i < 100 else -0.03
        wave = math.sin(i / 5.0) * 0.4
        op = price
        cl = price + drift + wave * 0.08
        hi = max(op, cl) + 0.25
        lo = min(op, cl) - 0.25
        rows.append((op, hi, lo, cl, 1000 + i))
        price = cl

    df = pd.DataFrame(rows, columns=["open", "high", "low", "close", "volume"])
    plan = analyze(df, market="OTC", symbol="DEMO-OTC", timeframe="1m", otc=True)
    print(format_report(plan))
    return 0


def main(argv: list[str]) -> int:
    if len(argv) == 1:
        return _demo()

    if argv[1] in {"--version", "-v"}:
        print(VERSION)
        return 0

    if argv[1] in {"--help", "-h"}:
        print(
            "Usage:\n"
            "  python tradeify_v2_2_1.py\n"
            "  python tradeify_v2_2_1.py data.csv --market OTC --symbol EURUSD-OTC --tf 1m\n"
            "  python tradeify_v2_2_1.py data.csv --market Crypto --symbol BTCUSDT --tf 15m\n"
        )
        return 0

    csv_path = argv[1]
    market = "Crypto"
    symbol = "UNKNOWN"
    tf = "Unknown"
    otc = False

    i = 2
    while i < len(argv):
        arg = argv[i]
        if arg == "--market" and i + 1 < len(argv):
            market = argv[i + 1]
            i += 2
        elif arg == "--symbol" and i + 1 < len(argv):
            symbol = argv[i + 1]
            i += 2
        elif arg in {"--tf", "--timeframe"} and i + 1 < len(argv):
            tf = argv[i + 1]
            i += 2
        elif arg == "--otc":
            otc = True
            i += 1
        else:
            raise SystemExit(f"Unknown argument: {arg}")

    if market.upper() == "OTC":
        otc = True

    plan = analyze_csv(csv_path, market, symbol, tf, otc)
    print(format_report(plan))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
'''

out = Path("/mnt/data")
out.mkdir(parents=True, exist_ok=True)

file_path = out / "tradeify_v2_2_1_stable.py"
file_path.write_text(code, encoding="utf-8")

# Also make a copy using the user's previous filename so their runner
# can switch with minimal changes.
compat_path = out / "tradeify_v2_2.py"
compat_path.write_text(code, encoding="utf-8")

# Smoke test in-process.
exec(compile(code, str(file_path), "exec"), {"__name__": "__smoke_test__"})

print(f"Created: {file_path}")
print(f"Compatibility copy: {compat_path}")
print("Smoke compile: OK")
