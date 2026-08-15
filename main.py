from pathlib import Path
import math
import json
import sys

VERSION = "TRADEIFY 2.2.1 STABLE"


# ============================================================
# SAFE OUTPUT
# ============================================================

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "output"

try:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
except Exception:
    OUTPUT_DIR = Path(".")


# ============================================================
# TRADE PLAN
# ไม่มี dataclass เพื่อหลีกเลี่ยงปัญหา Python 3.13 environment
# ============================================================

class TradePlan:

    def __init__(self, **kwargs):
        self.data = kwargs

        for key, value in kwargs.items():
            setattr(self, key, value)

    def to_dict(self):
        return dict(self.data)


# ============================================================
# BASIC MATH
# ============================================================

def safe_float(value, default=None):

    try:
        value = float(value)

        if math.isfinite(value):
            return value

    except Exception:
        pass

    return default


def mean(values):

    if not values:
        return None

    return sum(values) / len(values)


def ema(values, period):

    if len(values) < period:
        return [None] * len(values)

    result = [None] * len(values)

    alpha = 2.0 / (period + 1.0)

    previous = sum(values[:period]) / period

    result[period - 1] = previous

    for i in range(period, len(values)):

        previous = (
            alpha * values[i]
            + (1.0 - alpha) * previous
        )

        result[i] = previous

    return result


def rsi(values, period=14):

    result = [None] * len(values)

    if len(values) <= period:
        return result

    gains = []
    losses = []

    for i in range(1, period + 1):

        change = values[i] - values[i - 1]

        gains.append(max(change, 0))
        losses.append(max(-change, 0))

    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period

    if avg_loss == 0:
        result[period] = 100.0
    else:
        rs = avg_gain / avg_loss
        result[period] = 100 - (100 / (1 + rs))

    for i in range(period + 1, len(values)):

        change = values[i] - values[i - 1]

        gain = max(change, 0)
        loss = max(-change, 0)

        avg_gain = (
            (avg_gain * (period - 1)) + gain
        ) / period

        avg_loss = (
            (avg_loss * (period - 1)) + loss
        ) / period

        if avg_loss == 0:
            result[i] = 100.0

        else:
            rs = avg_gain / avg_loss
            result[i] = 100 - (100 / (1 + rs))

    return result


def atr(high, low, close, period=14):

    if len(close) < period:
        return [None] * len(close)

    tr = []

    for i in range(len(close)):

        if i == 0:

            value = high[i] - low[i]

        else:

            value = max(
                high[i] - low[i],
                abs(high[i] - close[i - 1]),
                abs(low[i] - close[i - 1])
            )

        tr.append(value)

    result = [None] * len(close)

    current = sum(tr[:period]) / period

    result[period - 1] = current

    for i in range(period, len(close)):

        current = (
            (current * (period - 1))
            + tr[i]
        ) / period

        result[i] = current

    return result


def last_valid(values):

    for value in reversed(values):

        if value is not None:
            return value

    return None


# ============================================================
# MACD
# ============================================================

def macd(close):

    fast = ema(close, 12)
    slow = ema(close, 26)

    line = [None] * len(close)

    compact = []
    indexes = []

    for i in range(len(close)):

        if fast[i] is not None and slow[i] is not None:

            line[i] = fast[i] - slow[i]

            compact.append(line[i])
            indexes.append(i)

    signal_compact = ema(compact, 9)

    signal = [None] * len(close)
    histogram = [None] * len(close)

    for i, original_index in enumerate(indexes):

        if signal_compact[i] is not None:

            signal[original_index] = signal_compact[i]

            histogram[original_index] = (
                line[original_index]
                - signal_compact[i]
            )

    return line, signal, histogram


# ============================================================
# PRICE STRUCTURE
# ============================================================

def market_structure(high, low):

    if len(high) < 20:
        return "UNKNOWN"

    recent_high = max(high[-10:])
    previous_high = max(high[-20:-10])

    recent_low = min(low[-10:])
    previous_low = min(low[-20:-10])

    if (
        recent_high > previous_high
        and recent_low > previous_low
    ):
        return "HH/HL"

    if (
        recent_high < previous_high
        and recent_low < previous_low
    ):
        return "LH/LL"

    return "RANGE"


# ============================================================
# MAIN TRADEIFY ENGINE
# ============================================================

def analyze_market(
    open_prices,
    high_prices,
    low_prices,
    close_prices,
    market="Crypto",
    symbol="UNKNOWN",
    timeframe="UNKNOWN",
    otc=False
):

    if len(close_prices) < 60:

        raise ValueError(
            "TRADEIFY ต้องการอย่างน้อย 60 candles"
        )

    close = [
        safe_float(x)
        for x in close_prices
    ]

    high = [
        safe_float(x)
        for x in high_prices
    ]

    low = [
        safe_float(x)
        for x in low_prices
    ]

    price = close[-1]

    ema20 = ema(close, 20)
    ema50 = ema(close, 50)

    rsi14 = rsi(close, 14)

    atr14 = atr(
        high,
        low,
        close,
        14
    )

    macd_line, macd_signal, macd_hist = macd(close)

    e20 = last_valid(ema20)
    e50 = last_valid(ema50)

    rsi_value = last_valid(rsi14)

    atr_value = last_valid(atr14)

    macd_value = last_valid(macd_line)
    macd_hist_value = last_valid(macd_hist)

    structure = market_structure(
        high,
        low
    )

    support = min(low[-50:])
    resistance = max(high[-50:])

    bullish_score = 0
    bearish_score = 0

    reasons = []
    warnings = []

    # ========================================================
    # TREND
    # ========================================================

    if e20 is not None and e50 is not None:

        if price > e20 and e20 > e50:

            bullish_score += 2

            reasons.append(
                "ราคาอยู่เหนือ EMA20 และ EMA20 อยู่เหนือ EMA50"
            )

            trend = "BULLISH"

        elif price < e20 and e20 < e50:

            bearish_score += 2

            reasons.append(
                "ราคาอยู่ใต้ EMA20 และ EMA20 อยู่ใต้ EMA50"
            )

            trend = "BEARISH"

        else:

            trend = "NEUTRAL"

    else:

        trend = "UNKNOWN"


    # ========================================================
    # STRUCTURE
    # ========================================================

    if structure == "HH/HL":

        bullish_score += 2

        reasons.append(
            "โครงสร้างราคาเป็น Higher High / Higher Low"
        )

    elif structure == "LH/LL":

        bearish_score += 2

        reasons.append(
            "โครงสร้างราคาเป็น Lower High / Lower Low"
        )

    else:

        warnings.append(
            "โครงสร้างยังเป็น Range / Mixed"
        )


    # ========================================================
    # RSI
    # ========================================================

    if rsi_value is not None:

        if 50 <= rsi_value <= 68:

            bullish_score += 1

            reasons.append(
                "RSI สนับสนุน Momentum ฝั่ง Buy"
            )

        elif 32 <= rsi_value < 50:

            bearish_score += 1

            reasons.append(
                "RSI สนับสนุน Momentum ฝั่ง Sell"
            )

        elif rsi_value > 75:

            warnings.append(
                "RSI Overbought — ห้ามไล่ราคา Buy"
            )

        elif rsi_value < 25:

            warnings.append(
                "RSI Oversold — ห้ามไล่ราคา Sell"
            )


    # ========================================================
    # MACD
    # ========================================================

    if macd_hist_value is not None:

        if macd_hist_value > 0:

            bullish_score += 1

            reasons.append(
                "MACD Histogram เป็นบวก"
            )

        elif macd_hist_value < 0:

            bearish_score += 1

            reasons.append(
                "MACD Histogram เป็นลบ"
            )


    # ========================================================
    # SUPPORT / RESISTANCE
    # ========================================================

    if atr_value and atr_value > 0:

        distance_support = abs(
            price - support
        )

        distance_resistance = abs(
            resistance - price
        )

        if distance_support <= atr_value * 0.6:

            bullish_score += 1

            reasons.append(
                "ราคาอยู่ใกล้ Support"
            )

        if distance_resistance <= atr_value * 0.6:

            bearish_score += 1

            reasons.append(
                "ราคาอยู่ใกล้ Resistance"
            )


    # ========================================================
    # CONFIDENCE
    # ========================================================

    strongest = max(
        bullish_score,
        bearish_score
    )

    total = max(
        bullish_score + bearish_score,
        1
    )

    confidence = (
        50
        + (strongest / total) * 30
        + abs(
            bullish_score
            - bearish_score
        ) * 3
    )

    confidence = min(
        confidence,
        95
    )


    # ========================================================
    # OTC SAFETY
    # ========================================================

    if (
        otc
        or str(market).upper() == "OTC"
    ):

        otc = True

        confidence = min(
            confidence,
            72
        )

        warnings.append(
            "OTC MODE: ราคาอาจแตกต่างจากตลาดจริง"
        )

        warnings.append(
            "ห้ามถือว่าสัญญาณ OTC = ราคาตลาดจริง 100%"
        )

        warnings.append(
            "แนะนำลดขนาดไม้และรอ Candle Confirmation"
        )


    # ========================================================
    # SIGNAL
    # ========================================================

    signal = "NO TRADE"

    bias = "NEUTRAL"

    entry = None
    stop_loss = None
    take_profit_1 = None
    take_profit_2 = None

    rr1 = None
    rr2 = None


    if atr_value is not None and atr_value > 0:

        if (
            bullish_score >= 4
            and bullish_score > bearish_score + 1
        ):

            signal = "BUY"
            bias = "LONG"

            entry = price

            stop_loss = min(
                min(low[-10:]),
                entry - atr_value * 1.15
            )

            risk = entry - stop_loss

            if risk > 0:

                take_profit_1 = (
                    entry + risk * 1.5
                )

                take_profit_2 = (
                    entry + risk * 2.2
                )

                rr1 = 1.5
                rr2 = 2.2


        elif (
            bearish_score >= 4
            and bearish_score > bullish_score + 1
        ):

            signal = "SELL"
            bias = "SHORT"

            entry = price

            stop_loss = max(
                max(high[-10:]),
                entry + atr_value * 1.15
            )

            risk = stop_loss - entry

            if risk > 0:

                take_profit_1 = (
                    entry - risk * 1.5
                )

                take_profit_2 = (
                    entry - risk * 2.2
                )

                rr1 = 1.5
                rr2 = 2.2


    # ========================================================
    # OTC EXTRA FILTER
    # ========================================================

    if otc and signal != "NO TRADE":

        if confidence < 62:

            signal = "NO TRADE"

            bias = "NEUTRAL"

            entry = None
            stop_loss = None
            take_profit_1 = None
            take_profit_2 = None

            rr1 = None
            rr2 = None

            warnings.append(
                "OTC confidence ต่ำเกินไป — NO TRADE"
            )

        else:

            warnings.append(
                "OTC: รอแท่งยืนยันก่อนเข้า"
            )


    # ========================================================
    # FINAL PLAN
    # ========================================================

    return TradePlan(

        version=VERSION,

        market=market,

        symbol=symbol,

        timeframe=timeframe,

        otc=otc,

        trend=trend,

        structure=structure,

        bias=bias,

        signal=signal,

        confidence=round(
            confidence,
            1
        ),

        entry=entry,

        stop_loss=stop_loss,

        take_profit_1=take_profit_1,

        take_profit_2=take_profit_2,

        rr_tp1=rr1,

        rr_tp2=rr2,

        support=support,

        resistance=resistance,

        atr=atr_value,

        rsi=rsi_value,

        ema20=e20,

        ema50=e50,

        macd=macd_value,

        bullish_score=bullish_score,

        bearish_score=bearish_score,

        reasons=reasons,

        warnings=warnings
    )


# ============================================================
# REPORT
# ============================================================

def fmt(value):

    if value is None:
        return "-"

    value = float(value)

    if abs(value) >= 1000:
        return f"{value:,.2f}"

    if abs(value) >= 1:
        return f"{value:.4f}".rstrip("0").rstrip(".")

    return f"{value:.8f}".rstrip("0").rstrip(".")


def report(plan):

    print()
    print("=" * 55)
    print("             TRADEIFY 2.2.1 STABLE")
    print("=" * 55)

    print(
        f"Market      : {plan.market}"
    )

    print(
        f"Symbol      : {plan.symbol}"
    )

    print(
        f"Timeframe   : {plan.timeframe}"
    )

    print(
        f"Trend       : {plan.trend}"
    )

    print(
        f"Structure   : {plan.structure}"
    )

    print(
        f"Signal      : {plan.signal}"
    )

    print(
        f"Confidence  : {plan.confidence}%"
    )

    print()

    print(
        f"Entry       : {fmt(plan.entry)}"
    )

    print(
        f"SL          : {fmt(plan.stop_loss)}"
    )

    print(
        f"TP1         : {fmt(plan.take_profit_1)}"
    )

    print(
        f"TP2         : {fmt(plan.take_profit_2)}"
    )

    print(
        f"R:R         : {plan.rr_tp1 or '-'} / {plan.rr_tp2 or '-'}"
    )

    print()

    print(
        f"Support     : {fmt(plan.support)}"
    )

    print(
        f"Resistance  : {fmt(plan.resistance)}"
    )

    print(
        f"ATR14       : {fmt(plan.atr)}"
    )

    print(
        f"RSI14       : {fmt(plan.rsi)}"
    )

    print(
        f"EMA20       : {fmt(plan.ema20)}"
    )

    print(
        f"EMA50       : {fmt(plan.ema50)}"
    )

    print()

    print("REASONS")

    for item in plan.reasons:

        print(
            f"  + {item}"
        )

    if plan.warnings:

        print()

        print("WARNINGS")

        for item in plan.warnings:

            print(
                f"  ! {item}"
            )

    print("=" * 55)
    print()


# ============================================================
# SAVE JSON
# ============================================================

def save_plan(plan):

    try:

        file_path = (
            OUTPUT_DIR
            / "tradeify_last_signal.json"
        )

        file_path.write_text(
            json.dumps(
                plan.to_dict(),
                ensure_ascii=False,
                indent=2
            ),
            encoding="utf-8"
        )

        return str(file_path)

    except Exception as error:

        print(
            "Warning: cannot save JSON:",
            error
        )

        return None


# ============================================================
# TEST DATA
# ============================================================

def generate_test_data():

    opens = []
    highs = []
    lows = []
    closes = []

    price = 100.0

    for i in range(150):

        wave = math.sin(
            i / 5
        ) * 0.15

        trend = 0.08

        new_price = (
            price
            + trend
            + wave
        )

        op = price

        cl = new_price

        hi = max(
            op,
            cl
        ) + 0.20

        lo = min(
            op,
            cl
        ) - 0.20

        opens.append(op)

        highs.append(hi)

        lows.append(lo)

        closes.append(cl)

        price = cl

    return (
        opens,
        highs,
        lows,
        closes
    )


# ============================================================
# SELF TEST
# ============================================================

def self_test():

    print()
    print("TRADEIFY SELF TEST")
    print("-" * 40)

    (
        opens,
        highs,
        lows,
        closes
    ) = generate_test_data()

    plan = analyze_market(
        opens,
        highs,
        lows,
        closes,
        market="OTC",
        symbol="TEST-OTC",
        timeframe="1m",
        otc=True
    )

    report(plan)

    saved = save_plan(plan)

    if saved:
        print(
            "JSON saved:",
            saved
        )

    print()
    print("SELF TEST: PASS")
    print()

    return 0


# ============================================================
# ENTRY POINT
# ============================================================

def main():

    try:

        if len(sys.argv) == 1:

            return self_test()

        command = sys.argv[1]

        if command in (
            "--version",
            "-v"
        ):

            print(VERSION)

            return 0

        if command in (
            "--test",
            "test"
        ):

            return self_test()

        print()
        print(
            "TRADEIFY is running."
        )
        print(
            "For first test use:"
        )
        print(
            "python main.py --test"
        )
        print()

        return 0

    except Exception as error:

        print()
        print("=" * 55)
        print("TRADEIFY SAFE ERROR")
        print("=" * 55)

        print(
            type(error).__name__,
            ":",
            str(error)
        )

        print()
        print(
            "ระบบหยุดอย่างปลอดภัย "
            "แทนการ crash loop"
        )

        print("=" * 55)

        return 1


if __name__ == "__main__":

    raise SystemExit(
        main()
    )
