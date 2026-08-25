# ============================================================
# TRADEIFY A+ MTF SNIPER ENGINE
#
# 15M = MASTER
# 5M  = CONFIRMATION
# 1M  = ENTRY / REJECTION
#
# MATCHES INDICATOR LOGIC
# ============================================================

A_PLUS_MIN_SCORE = 82
A_PLUS_MIN_GAP = 12

EMA_FAST_LEN = 9
EMA_SLOW_LEN = 21
EMA_TREND_LEN = 50

RSI_PERIOD = 14
RSI_CALL_MAX = 48
RSI_PUT_MIN = 52
RSI_EXTREME_LOW = 30
RSI_EXTREME_HIGH = 70

BB_PERIOD = 20
BB_DEV = 2.0

SR_PERIOD = 100
SR_ZONE = 0.18

FLOW_BARS = 3

STRICT_MODE = True


# ============================================================
# SIMPLE HELPERS
# ============================================================

def clamp(value, low, high):
    return max(
        low,
        min(high, value)
    )


def candle_features(candle):

    body = abs(
        candle["close"]
        -
        candle["open"]
    )

    bar_range = max(
        candle["high"]
        -
        candle["low"],
        1e-12
    )

    upper_wick = (
        candle["high"]
        -
        max(
            candle["open"],
            candle["close"]
        )
    )

    lower_wick = (
        min(
            candle["open"],
            candle["close"]
        )
        -
        candle["low"]
    )

    body_ratio = (
        body / bar_range
    )

    upper_ratio = (
        upper_wick / bar_range
    )

    lower_ratio = (
        lower_wick / bar_range
    )

    bull = (
        candle["close"]
        >
        candle["open"]
    )

    bear = (
        candle["close"]
        <
        candle["open"]
    )

    bull_rejection = (
        lower_ratio >= 0.25
        and
        candle["close"]
        >=
        candle["open"]
    )

    bear_rejection = (
        upper_ratio >= 0.25
        and
        candle["close"]
        <=
        candle["open"]
    )

    strong_bull = (
        bull
        and
        body_ratio >= 0.45
    )

    strong_bear = (
        bear
        and
        body_ratio >= 0.45
    )

    return {
        "body": body,
        "range": bar_range,
        "upper_ratio": upper_ratio,
        "lower_ratio": lower_ratio,
        "body_ratio": body_ratio,
        "bull": bull,
        "bear": bear,
        "bull_rejection": bull_rejection,
        "bear_rejection": bear_rejection,
        "strong_bull": strong_bull,
        "strong_bear": strong_bear
    }


# ============================================================
# BOLLINGER
# ============================================================

def calculate_bollinger(
    candles,
    period=20,
    dev=2.0
):

    if len(candles) < period:

        return None

    closes = [
        x["close"]
        for x in candles[-period:]
    ]

    mid = mean(closes)

    variance = mean(
        (
            x - mid
        ) ** 2
        for x in closes
    )

    std = variance ** 0.5

    upper = (
        mid
        +
        dev * std
    )

    lower = (
        mid
        -
        dev * std
    )

    return {
        "mid": mid,
        "upper": upper,
        "lower": lower
    }


# ============================================================
# SUPPORT / RESISTANCE
# MATCH INDICATOR
#
# support = lowest(low, 100)
# resistance = highest(high, 100)
# ============================================================

def calculate_sr(
    candles,
    period=100
):

    if len(candles) < period:

        return None

    data = candles[-period:]

    support = min(
        x["low"]
        for x in data
    )

    resistance = max(
        x["high"]
        for x in data
    )

    current = data[-1]["close"]

    sr_range = max(
        resistance - support,
        1e-12
    )

    near_support = (
        current
        <=
        support
        +
        sr_range * SR_ZONE
    )

    near_resistance = (
        current
        >=
        resistance
        -
        sr_range * SR_ZONE
    )

    room_call = (
        resistance - current
    ) / sr_range

    room_put = (
        current - support
    ) / sr_range

    enough_room_call = (
        room_call >= 0.20
    )

    enough_room_put = (
        room_put >= 0.20
    )

    return {
        "support": support,
        "resistance": resistance,
        "range": sr_range,
        "near_support": near_support,
        "near_resistance": near_resistance,
        "room_call": room_call,
        "room_put": room_put,
        "enough_room_call": enough_room_call,
        "enough_room_put": enough_room_put
    }


# ============================================================
# 15M STRUCTURE
#
# Same basic structure logic as indicator
# ============================================================

def analyze_15m_structure(candles):

    if len(candles) < 3:

        return {
            "bull": False,
            "bear": False,
            "higher_high": False,
            "higher_low": False,
            "lower_high": False,
            "lower_low": False,
            "close_up": False,
            "close_down": False
        }

    c = candles[-1]
    p = candles[-2]

    bull = (
        c["close"] > c["open"]
    )

    bear = (
        c["close"] < c["open"]
    )

    higher_high = (
        c["high"] > p["high"]
    )

    higher_low = (
        c["low"] > p["low"]
    )

    lower_high = (
        c["high"] < p["high"]
    )

    lower_low = (
        c["low"] < p["low"]
    )

    close_up = (
        c["close"] > p["close"]
    )

    close_down = (
        c["close"] < p["close"]
    )

    trend_call = (
        bull
        and
        (
            higher_high
            or
            higher_low
        )
        and
        close_up
    )

    trend_put = (
        bear
        and
        (
            lower_high
            or
            lower_low
        )
        and
        close_down
    )

    return {
        "bull": bull,
        "bear": bear,
        "higher_high": higher_high,
        "higher_low": higher_low,
        "lower_high": lower_high,
        "lower_low": lower_low,
        "close_up": close_up,
        "close_down": close_down,
        "trend_call": trend_call,
        "trend_put": trend_put
    }


# ============================================================
# MAIN A+ ANALYSIS
# ============================================================

def analyze(
    symbol,
    candles_1m
):

    if len(candles_1m) < 150:

        return None

    # ========================================================
    # RESAMPLE
    # ========================================================

    candles_5m = resample(
        candles_1m,
        5
    )

    candles_15m = resample(
        candles_1m,
        15
    )

    if len(candles_5m) < 70:

        return None

    if len(candles_15m) < 80:

        return None

    # ========================================================
    # ONLY CLOSED CANDLES
    # ========================================================

    c1 = candles_1m[-1]

    c5 = candles_5m[-1]

    c15 = candles_15m[-1]

    # ========================================================
    # 15M STRUCTURE
    # ========================================================

    structure15 = (
        analyze_15m_structure(
            candles_15m
        )
    )

    trend15_call = (
        structure15["trend_call"]
    )

    trend15_put = (
        structure15["trend_put"]
    )

    # ========================================================
    # 5M STRUCTURE
    # ========================================================

    structure5 = (
        analyze_15m_structure(
            candles_5m
        )
    )

    trend5_call = (
        structure5["trend_call"]
    )

    trend5_put = (
        structure5["trend_put"]
    )

    # ========================================================
    # 1M EMA
    # ========================================================

    close_1m = [
        x["close"]
        for x in candles_1m
    ]

    ema_fast = calculate_ema(
        close_1m,
        EMA_FAST_LEN
    )

    ema_slow = calculate_ema(
        close_1m,
        EMA_SLOW_LEN
    )

    ema_trend = calculate_ema(
        close_1m,
        EMA_TREND_LEN
    )

    if (
        ema_fast is None
        or
        ema_slow is None
        or
        ema_trend is None
    ):

        return None

    # ========================================================
    # EMA FLOW
    # ========================================================

    previous_ema_fast = calculate_ema(
        close_1m[:-1],
        EMA_FAST_LEN
    )

    ema_up = (
        ema_fast
        >
        ema_slow
        and
        ema_slow
        >
        ema_trend
        and
        previous_ema_fast is not None
        and
        ema_fast
        >
        previous_ema_fast
    )

    ema_down = (
        ema_fast
        <
        ema_slow
        and
        ema_slow
        <
        ema_trend
        and
        previous_ema_fast is not None
        and
        ema_fast
        <
        previous_ema_fast
    )

    # ========================================================
    # RSI
    # ========================================================

    rsi_value = calculate_rsi(
        candles_1m,
        RSI_PERIOD
    )

    if rsi_value is None:

        return None

    # ========================================================
    # BOLLINGER
    # ========================================================

    bb = calculate_bollinger(
        candles_1m,
        BB_PERIOD,
        BB_DEV
    )

    if bb is None:

        return None

    # ========================================================
    # CANDLE
    # ========================================================

    features = candle_features(
        c1
    )

    bull = features["bull"]
    bear = features["bear"]

    bull_rejection = (
        features[
            "bull_rejection"
        ]
    )

    bear_rejection = (
        features[
            "bear_rejection"
        ]
    )

    strong_bull = (
        features["strong_bull"]
    )

    strong_bear = (
        features["strong_bear"]
    )

    body_ratio = (
        features["body_ratio"]
    )

    upper_ratio = (
        features["upper_ratio"]
    )

    lower_ratio = (
        features["lower_ratio"]
    )

    # ========================================================
    # PRICE FLOW
    # ========================================================

    flow_up = (
        c1["close"]
        >
        candles_1m[-2]["close"]
        and
        candles_1m[-2]["close"]
        >
        candles_1m[-3]["close"]
        and
        candles_1m[-3]["close"]
        >
        candles_1m[-4]["close"]
    )

    flow_down = (
        c1["close"]
        <
        candles_1m[-2]["close"]
        and
        candles_1m[-2]["close"]
        <
        candles_1m[-3]["close"]
        and
        candles_1m[-3]["close"]
        <
        candles_1m[-4]["close"]
    )

    # ========================================================
    # SUPPORT / RESISTANCE
    # ========================================================

    sr = calculate_sr(
        candles_1m,
        SR_PERIOD
    )

    if sr is None:

        return None

    support = sr["support"]

    resistance = sr["resistance"]

    near_support = (
        sr["near_support"]
    )

    near_resistance = (
        sr["near_resistance"]
    )

    enough_room_call = (
        sr["enough_room_call"]
    )

    enough_room_put = (
        sr["enough_room_put"]
    )

    # ========================================================
    # OVEREXTENSION
    # ========================================================

    overextended_call = (
        rsi_value
        >=
        RSI_EXTREME_HIGH
        or
        (
            bull
            and
            body_ratio >= 0.78
            and
            upper_ratio <= 0.08
        )
    )

    overextended_put = (
        rsi_value
        <=
        RSI_EXTREME_LOW
        or
        (
            bear
            and
            body_ratio >= 0.78
            and
            lower_ratio <= 0.08
        )
    )

    # ========================================================
    # PULLBACK
    # ========================================================

    pullback_call = (
        (
            c1["low"]
            <=
            ema_fast
        )
        or
        (
            c1["low"]
            <=
            bb["mid"]
        )
        or
        near_support
    ) and (
        c1["close"]
        >
        ema_fast
    )

    pullback_put = (
        (
            c1["high"]
            >=
            ema_fast
        )
        or
        (
            c1["high"]
            >=
            bb["mid"]
        )
        or
        near_resistance
    ) and (
        c1["close"]
        <
        ema_fast
    )

    # ========================================================
    # SCORE
    # EXACT A+ WEIGHTS
    # ========================================================

    call_score = 0
    put_score = 0

    call_reasons = []
    put_reasons = []

    # --------------------------------------------------------
    # 15M MASTER = 30
    # --------------------------------------------------------

    if trend15_call:

        call_score += 30

        call_reasons.append(
            "15M MASTER CALL"
        )

    if trend15_put:

        put_score += 30

        put_reasons.append(
            "15M MASTER PUT"
        )

    # --------------------------------------------------------
    # 5M CONFIRM = 25
    # --------------------------------------------------------

    if trend5_call:

        call_score += 25

        call_reasons.append(
            "5M CONFIRM CALL"
        )

    if trend5_put:

        put_score += 25

        put_reasons.append(
            "5M CONFIRM PUT"
        )

    # --------------------------------------------------------
    # EMA = 12
    # --------------------------------------------------------

    if ema_up:

        call_score += 12

        call_reasons.append(
            "EMA 9/21/50 UP"
        )

    if ema_down:

        put_score += 12

        put_reasons.append(
            "EMA 9/21/50 DOWN"
        )

    # --------------------------------------------------------
    # FLOW = 10
    # --------------------------------------------------------

    if flow_up:

        call_score += 10

        call_reasons.append(
            "3-BAR FLOW UP"
        )

    if flow_down:

        put_score += 10

        put_reasons.append(
            "3-BAR FLOW DOWN"
        )

    # --------------------------------------------------------
    # REJECTION = 12
    # --------------------------------------------------------

    if bull_rejection:

        call_score += 12

        call_reasons.append(
            "BULL REJECTION"
        )

    if bear_rejection:

        put_score += 12

        put_reasons.append(
            "BEAR REJECTION"
        )

    # --------------------------------------------------------
    # CANDLE QUALITY = 6
    # --------------------------------------------------------

    if strong_bull:

        call_score += 6

        call_reasons.append(
            "STRONG BULL CANDLE"
        )

    if strong_bear:

        put_score += 6

        put_reasons.append(
            "STRONG BEAR CANDLE"
        )

    # --------------------------------------------------------
    # RSI = 5
    # --------------------------------------------------------

    if (
        rsi_value
        <=
        RSI_CALL_MAX
        and
        rsi_value
        >
        RSI_EXTREME_LOW
    ):

        call_score += 5

        call_reasons.append(
            "RSI CALL ZONE"
        )

    if (
        rsi_value
        >=
        RSI_PUT_MIN
        and
        rsi_value
        <
        RSI_EXTREME_HIGH
    ):

        put_score += 5

        put_reasons.append(
            "RSI PUT ZONE"
        )

    # --------------------------------------------------------
    # S/R = 10
    # --------------------------------------------------------

    if near_support:

        call_score += 10

        call_reasons.append(
            "NEAR SUPPORT"
        )

    if near_resistance:

        put_score += 10

        put_reasons.append(
            "NEAR RESISTANCE"
        )

    # --------------------------------------------------------
    # PULLBACK = 8
    # --------------------------------------------------------

    if pullback_call:

        call_score += 8

        call_reasons.append(
            "CALL PULLBACK"
        )

    if pullback_put:

        put_score += 8

        put_reasons.append(
            "PUT PULLBACK"
        )

    # --------------------------------------------------------
    # ROOM = 5
    # --------------------------------------------------------

    if enough_room_call:

        call_score += 5

        call_reasons.append(
            "CALL HAS ROOM"
        )

    if enough_room_put:

        put_score += 5

        put_reasons.append(
            "PUT HAS ROOM"
        )

    # ========================================================
    # PENALTIES
    # ========================================================

    if near_resistance:

        call_score -= 15

    if near_support:

        put_score -= 15

    if overextended_call:

        call_score -= 20

    if overextended_put:

        put_score -= 20

    # --------------------------------------------------------
    # HTF CONFLICT
    # --------------------------------------------------------

    if (
        trend15_call
        and
        trend5_put
    ):

        call_score -= 25
        put_score -= 25

    if (
        trend15_put
        and
        trend5_call
    ):

        call_score -= 25
        put_score -= 25

    # ========================================================
    # CLAMP
    # ========================================================

    call_score = int(
        clamp(
            call_score,
            0,
            100
        )
    )

    put_score = int(
        clamp(
            put_score,
            0,
            100
        )
    )

    # ========================================================
    # DIRECTION
    # ========================================================

    if (
        call_score
        >
        put_score
    ):

        direction = "CALL"

    elif (
        put_score
        >
        call_score
    ):

        direction = "PUT"

    else:

        return None

    if direction == "CALL":

        score = call_score

        opposite_score = put_score

        score_gap = (
            call_score
            -
            put_score
        )

    else:

        score = put_score

        opposite_score = call_score

        score_gap = (
            put_score
            -
            call_score
        )

    # ========================================================
    # MAJOR CONDITIONS
    # ========================================================

    major_call = (
        trend15_call
        and
        trend5_call
        and
        ema_up
        and
        enough_room_call
        and
        not overextended_call
    )

    major_put = (
        trend15_put
        and
        trend5_put
        and
        ema_down
        and
        enough_room_put
        and
        not overextended_put
    )

    # ========================================================
    # EARLY
    # ========================================================

    early_call = (
        major_call
        and
        call_score
        >=
        (
            A_PLUS_MIN_SCORE
            - 8
        )
        and
        (
            call_score
            -
            put_score
        )
        >=
        A_PLUS_MIN_GAP
        and
        (
            pullback_call
            or
            bull_rejection
            or
            rsi_value
            <=
            RSI_CALL_MAX
        )
    )

    early_put = (
        major_put
        and
        put_score
        >=
        (
            A_PLUS_MIN_SCORE
            - 8
        )
        and
        (
            put_score
            -
            call_score
        )
        >=
        A_PLUS_MIN_GAP
        and
        (
            pullback_put
            or
            bear_rejection
            or
            rsi_value
            >=
            RSI_PUT_MIN
        )
    )

    # ========================================================
    # CONFIRMED
    # ========================================================

    call_signal = (
        major_call
        and
        call_score
        >=
        A_PLUS_MIN_SCORE
        and
        (
            call_score
            -
            put_score
        )
        >=
        A_PLUS_MIN_GAP
        and
        bull
        and
        bull_rejection
        and
        pullback_call
    )

    put_signal = (
        major_put
        and
        put_score
        >=
        A_PLUS_MIN_SCORE
        and
        (
            put_score
            -
            call_score
        )
        >=
        A_PLUS_MIN_GAP
        and
        bear
        and
        bear_rejection
        and
        pullback_put
    )

    # ========================================================
    # STRICT MODE
    # ========================================================

    if STRICT_MODE:

        call_signal = (
            call_signal
            and
            flow_up
        )

        put_signal = (
            put_signal
            and
            flow_down
        )

    # ========================================================
    # FINAL SIGNAL
    # ========================================================

    confirmed = (
        call_signal
        or
        put_signal
    )

    if call_signal:

        direction = "CALL"

    elif put_signal:

        direction = "PUT"

    # ========================================================
    # RETURN
    # ========================================================

    return {

        "symbol":
            symbol,

        "direction":
            direction,

        "early":
            bool(
                early_call
                if
                direction == "CALL"
                else
                early_put
            ),

        "confirmed":
            bool(confirmed),

        "score":
            int(score),

        "call_score":
            int(call_score),

        "put_score":
            int(put_score),

        "edge":
            int(score_gap),

        "entry":
            float(c1["close"]),

        "timestamp":
            int(c1["timestamp"]),

        "entry_5m":
            float(c5["close"]),

        "entry_15m":
            float(c15["close"]),

        "zone":
            (
                "SUPPORT"
                if near_support
                else
                "RESISTANCE"
                if near_resistance
                else
                "MID"
            ),

        "rsi":
            float(rsi_value),

        "support":
            float(support),

        "resistance":
            float(resistance),

        "ema_fast":
            float(ema_fast),

        "ema_slow":
            float(ema_slow),

        "ema_trend":
            float(ema_trend),

        "bb_mid":
            float(bb["mid"]),

        "bb_upper":
            float(bb["upper"]),

        "bb_lower":
            float(bb["lower"]),

        "pullback_call":
            bool(pullback_call),

        "pullback_put":
            bool(pullback_put),

        "rejection_call":
            bool(bull_rejection),

        "rejection_put":
            bool(bear_rejection),

        "flow_call":
            bool(flow_up),

        "flow_put":
            bool(flow_down),

        "overextended_call":
            bool(overextended_call),

        "overextended_put":
            bool(overextended_put),

        "major_call":
            bool(major_call),

        "major_put":
            bool(major_put),

        "reasons":
            (
                call_reasons
                if
                direction == "CALL"
                else
                put_reasons
            )
    }
