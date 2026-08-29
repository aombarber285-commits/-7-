instrument {
    name = "SIGZY TRADEIFY V8 SYNC 5M",
    short_name = "ST-V8",
    icon = "indicators:BB",
    overlay = true
}

-- =========================================================
-- TRADEIFY V8 SYNC INDICATOR
--
-- แนะนำให้ใช้บนกราฟ 5 นาที
--
-- 15M = MASTER TREND
-- 5M  = CONFIRM + ENTRY
--
-- ใช้แท่งที่ปิดแล้ว
-- เพื่อลด Repaint และให้ใกล้เคียง Python TRADEIFY V8
--
-- GREEN  = CALL
-- RED    = PUT
-- YELLOW = PRE WARNING
-- =========================================================


input_group {
    "SIGNAL FILTER",

    min_score = input {
        default = 68,
        type = input.integer
    },

    min_gap = input {
        default = 8,
        type = input.integer
    },

    strict_mode = input {
        default = false,
        type = input.plot_visibility
    }
}


input_group {
    "TIMEFRAME",

    tf15 = input {
        default = "15m",
        type = input.string
    },

    tf5 = input {
        default = "5m",
        type = input.string
    }
}


input_group {
    "TREND",

    ema_fast_len = input {
        default = 9,
        type = input.integer
    },

    ema_slow_len = input {
        default = 21,
        type = input.integer
    },

    ema_trend_len = input {
        default = 50,
        type = input.integer
    }
}


input_group {
    "MOMENTUM",

    rsi_period = input {
        default = 14,
        type = input.integer
    },

    rsi_mid = input {
        default = 50,
        type = input.integer
    }
}


input_group {
    "SUPPORT RESISTANCE",

    sr_period = input {
        default = 80,
        type = input.integer
    },

    use_sr = input {
        default = true,
        type = input.plot_visibility
    }
}


input_group {
    "COLORS",

    call_color = input {
        default = "#00FF66",
        type = input.color
    },

    put_color = input {
        default = "#FF2020",
        type = input.color
    },

    pre_color = input {
        default = "#FFFF00",
        type = input.color
    },

    ema_fast_color = input {
        default = "#00FFFF",
        type = input.color
    },

    ema_slow_color = input {
        default = "#FFA500",
        type = input.color
    },

    ema_trend_color = input {
        default = "#FFFFFF",
        type = input.color
    }
}


-- =========================================================
-- MTF DATA
-- =========================================================

sec15 = security(current_ticker_id, tf15)
sec5 = security(current_ticker_id, tf5)


-- =========================================================
-- CLOSED 15M CANDLE
-- =========================================================

m15_open = sec15.open[1]
m15_close = sec15.close[1]
m15_high = sec15.high[1]
m15_low = sec15.low[1]

m15_prev_close = sec15.close[2]
m15_prev_high = sec15.high[2]
m15_prev_low = sec15.low[2]


-- =========================================================
-- CLOSED 5M CANDLE
-- =========================================================

m5_open = sec5.open[1]
m5_close = sec5.close[1]
m5_high = sec5.high[1]
m5_low = sec5.low[1]

m5_prev_open = sec5.open[2]
m5_prev_close = sec5.close[2]
m5_prev_high = sec5.high[2]
m5_prev_low = sec5.low[2]


-- =========================================================
-- 15M MASTER STRUCTURE
-- =========================================================

m15_bull =
    m15_close > m15_open


m15_bear =
    m15_close < m15_open


m15_structure_up =
    m15_high >= m15_prev_high or
    m15_low >= m15_prev_low


m15_structure_down =
    m15_high <= m15_prev_high or
    m15_low <= m15_prev_low


trend15_call =
    m15_bull and
    m15_structure_up and
    m15_close >= m15_prev_close


trend15_put =
    m15_bear and
    m15_structure_down and
    m15_close <= m15_prev_close


-- =========================================================
-- 5M CONFIRM STRUCTURE
-- =========================================================

m5_bull =
    m5_close > m5_open


m5_bear =
    m5_close < m5_open


m5_structure_up =
    m5_high >= m5_prev_high or
    m5_low >= m5_prev_low


m5_structure_down =
    m5_high <= m5_prev_high or
    m5_low <= m5_prev_low


trend5_call =
    m5_bull and
    m5_structure_up and
    m5_close >= m5_prev_close


trend5_put =
    m5_bear and
    m5_structure_down and
    m5_close <= m5_prev_close


-- =========================================================
-- LOCAL 5M INDICATORS
--
-- ใช้ข้อมูลกราฟ 5 นาที
-- =========================================================

ema_fast =
    ema(close, ema_fast_len)

ema_slow =
    ema(close, ema_slow_len)

ema_trend =
    ema(close, ema_trend_len)


rsi_value =
    rsi(close, rsi_period)


-- =========================================================
-- EMA DIRECTION
-- =========================================================

ema_call =
    ema_fast > ema_slow and
    ema_slow > ema_trend


ema_put =
    ema_fast < ema_slow and
    ema_slow < ema_trend


-- =========================================================
-- CLOSED CANDLE ANATOMY
--
-- [1] = แท่งที่ปิดแล้ว
-- =========================================================

signal_open = open[1]
signal_high = high[1]
signal_low = low[1]
signal_close = close[1]


body =
    abs(signal_close - signal_open)


bar_range =
    max(
        signal_high - signal_low,
        0.00000001
    )


upper_wick =
    signal_high -
    max(signal_open, signal_close)


lower_wick =
    min(signal_open, signal_close) -
    signal_low


body_ratio =
    body / bar_range


upper_ratio =
    upper_wick / bar_range


lower_ratio =
    lower_wick / bar_range


bull =
    signal_close > signal_open


bear =
    signal_close < signal_open


-- =========================================================
-- REJECTION
-- =========================================================

bull_rejection =
    bull and
    lower_ratio >= 0.18


bear_rejection =
    bear and
    upper_ratio >= 0.18


-- =========================================================
-- FLOW
--
-- ใช้แท่งปิด 3 แท่ง
-- =========================================================

flow_up =
    close[1] >= close[2] and
    close[2] >= close[3]


flow_down =
    close[1] <= close[2] and
    close[2] <= close[3]


-- =========================================================
-- SUPPORT / RESISTANCE
-- =========================================================

support =
    lowest(low[1], sr_period)


resistance =
    highest(high[1], sr_period)


range_sr =
    max(
        resistance - support,
        0.00000001
    )


near_support =
    signal_close <=
    support + range_sr * 0.22


near_resistance =
    signal_close >=
    resistance - range_sr * 0.22


room_call =
    (resistance - signal_close) /
    range_sr


room_put =
    (signal_close - support) /
    range_sr


enough_room_call =
    room_call >= 0.15


enough_room_put =
    room_put >= 0.15


-- =========================================================
-- PULLBACK
-- =========================================================

pullback_call =
    (
        signal_low <= ema_fast or
        signal_low <= ema_slow or
        near_support
    )
    and
    signal_close >= ema_fast


pullback_put =
    (
        signal_high >= ema_fast or
        signal_high >= ema_slow or
        near_resistance
    )
    and
    signal_close <= ema_fast


-- =========================================================
-- SCORE
-- =========================================================

call_score = 0
put_score = 0


-- 15M MASTER

if trend15_call then
    call_score = call_score + 30
end


if trend15_put then
    put_score = put_score + 30
end


-- 5M CONFIRM

if trend5_call then
    call_score = call_score + 25
end


if trend5_put then
    put_score = put_score + 25
end


-- EMA

if ema_call then
    call_score = call_score + 12
end


if ema_put then
    put_score = put_score + 12
end


-- FLOW

if flow_up then
    call_score = call_score + 8
end


if flow_down then
    put_score = put_score + 8
end


-- REJECTION

if bull_rejection then
    call_score = call_score + 12
end


if bear_rejection then
    put_score = put_score + 12
end


-- RSI

if rsi_value > rsi_mid then
    call_score = call_score + 5
end


if rsi_value < rsi_mid then
    put_score = put_score + 5
end


-- PULLBACK

if pullback_call then
    call_score = call_score + 8
end


if pullback_put then
    put_score = put_score + 8
end


-- ROOM

if enough_room_call then
    call_score = call_score + 5
end


if enough_room_put then
    put_score = put_score + 5
end


-- SUPPORT

if near_support then
    call_score = call_score + 5
end


-- RESISTANCE

if near_resistance then
    put_score = put_score + 5
end


-- =========================================================
-- PENALTY
-- =========================================================

if near_resistance then
    call_score = call_score - 8
end


if near_support then
    put_score = put_score - 8
end


-- =========================================================
-- LIMIT SCORE
-- =========================================================

call_score =
    max(
        0,
        min(call_score, 100)
    )


put_score =
    max(
        0,
        min(put_score, 100)
    )


-- =========================================================
-- GAP
-- =========================================================

gap_call =
    call_score - put_score


gap_put =
    put_score - call_score


-- =========================================================
-- MASTER
-- =========================================================

master_call =
    trend15_call and
    trend5_call


master_put =
    trend15_put and
    trend5_put


-- =========================================================
-- PRE WARNING
--
-- Python V8:
-- MIN_SCORE - 10
-- =========================================================

pre_call =
    master_call and
    call_score >= min_score - 10 and
    gap_call >= min_gap


pre_put =
    master_put and
    put_score >= min_score - 10 and
    gap_put >= min_gap


-- =========================================================
-- FINAL SIGNAL
-- =========================================================

call_signal =
    master_call and
    call_score >= min_score and
    gap_call >= min_gap and
    ema_call and
    bull_rejection and
    pullback_call


put_signal =
    master_put and
    put_score >= min_score and
    gap_put >= min_gap and
    ema_put and
    bear_rejection and
    pullback_put


-- =========================================================
-- STRICT MODE
-- =========================================================

if strict_mode then

    call_signal =
        call_signal and
        flow_up

    put_signal =
        put_signal and
        flow_down

end


-- =========================================================
-- CONFLICT BLOCK
-- =========================================================

if master_call and master_put then

    call_signal = false
    put_signal = false

end


-- =========================================================
-- EMA PLOTS
-- =========================================================

plot(
    ema_fast,
    "EMA 9",
    ema_fast_color,
    1
)


plot(
    ema_slow,
    "EMA 21",
    ema_slow_color,
    1
)


plot(
    ema_trend,
    "EMA 50",
    ema_trend_color,
    1
)


-- =========================================================
-- SUPPORT RESISTANCE
-- =========================================================

if use_sr then

    plot(
        support,
        "SUPPORT",
        "#00CC66",
        1
    )


    plot(
        resistance,
        "RESISTANCE",
        "#FF3333",
        1
    )

end


-- =========================================================
-- PRE CALL
-- =========================================================

plot_shape(
    pre_call and not call_signal,
    "PRE CALL",
    shape_style.triangleup,
    shape_size.normal,
    pre_color,
    shape_location.belowbar,
    0,
    "PRE",
    pre_color
)


-- =========================================================
-- PRE PUT
-- =========================================================

plot_shape(
    pre_put and not put_signal,
    "PRE PUT",
    shape_style.triangledown,
    shape_size.normal,
    pre_color,
    shape_location.abovebar,
    0,
    "PRE",
    pre_color
)


-- =========================================================
-- FINAL CALL
-- =========================================================

plot_shape(
    call_signal,
    "CALL",
    shape_style.triangleup,
    shape_size.large,
    call_color,
    shape_location.belowbar,
    0,
    "CALL",
    call_color
)


-- =========================================================
-- FINAL PUT
-- =========================================================

plot_shape(
    put_signal,
    "PUT",
    shape_style.triangledown,
    shape_size.large,
    put_color,
    shape_location.abovebar,
    0,
    "PUT",
    put_color
)
