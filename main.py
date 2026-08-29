# =========================================================
# TRADEIFY V8
# 15M MASTER + 5M ENTRY
# SEPARATE SCORE / GAP
# =========================================================
# =========================================================
# GENERIC CANDLE DATA
# =========================================================
def get_signal_candle(df):
    if len(df) < 4:
        raise ValueError(
            "ข้อมูลแท่งไม่เพียงพอ"
        )
    i = -2
    p = -3
    return {
        "open": float(df["open"].iloc[i]),
        "high": float(df["high"].iloc[i]),
        "low": float(df["low"].iloc[i]),
        "close": float(df["close"].iloc[i]),
        "prev_open": float(df["open"].iloc[p]),
        "prev_high": float(df["high"].iloc[p]),
        "prev_low": float(df["low"].iloc[p]),
        "prev_close": float(df["close"].iloc[p])
    }
# =========================================================
# TIMEFRAME SCORE
# =========================================================
def calculate_tf_score(df):
    if len(df) < 85:
        raise ValueError(
            "ข้อมูลไม่พอสำหรับ TF Score"
        )
    candle = get_signal_candle(df)
    o = candle["open"]
    h = candle["high"]
    l = candle["low"]
    c = candle["close"]
    ph = candle["prev_high"]
    pl = candle["prev_low"]
    pc = candle["prev_close"]
    # -----------------------------------------------------
    # BULL / BEAR
    # -----------------------------------------------------
    bull = c > o
    bear = c < o
    # -----------------------------------------------------
    # STRUCTURE
    # -----------------------------------------------------
    structure_up = (
        h >= ph
        or
        l >= pl
    )
    structure_down = (
        h <= ph
        or
        l <= pl
    )
    trend_call = (
        bull
        and
        structure_up
        and
        c >= pc
    )
    trend_put = (
        bear
        and
        structure_down
        and
        c <= pc
    )
    # -----------------------------------------------------
    # EMA
    # -----------------------------------------------------
    ema_fast = float(
        df["ema_fast"].iloc[-2]
    )
    ema_slow = float(
        df["ema_slow"].iloc[-2]
    )
    ema_trend = float(
        df["ema_trend"].iloc[-2]
    )
    ema_call = (
        ema_fast > ema_slow
        and
        ema_slow > ema_trend
    )
    ema_put = (
        ema_fast < ema_slow
        and
        ema_slow < ema_trend
    )
    # -----------------------------------------------------
    # RSI
    # -----------------------------------------------------
    rsi_value = float(
        df["rsi"].iloc[-2]
    )
    # -----------------------------------------------------
    # CANDLE ANATOMY
    # -----------------------------------------------------
    body = abs(
        c - o
    )
    candle_range = max(
        h - l,
        0.00000001
    )
    upper_wick = (
        h - max(o, c)
    )
    lower_wick = (
        min(o, c) - l
    )
    upper_ratio = (
        upper_wick /
        candle_range
    )
    lower_ratio = (
        lower_wick /
        candle_range
    )
    bull_rejection = (
        bull
        and
        lower_ratio >= 0.18
    )
    bear_rejection = (
        bear
        and
        upper_ratio >= 0.18
    )
    # -----------------------------------------------------
    # FLOW
    # -----------------------------------------------------
    c1 = float(
        df["close"].iloc[-2]
    )
    c2 = float(
        df["close"].iloc[-3]
    )
    c3 = float(
        df["close"].iloc[-4]
    )
    flow_up = (
        c1 >= c2
        and
        c2 >= c3
    )
    flow_down = (
        c1 <= c2
        and
        c2 <= c3
    )
    # -----------------------------------------------------
    # SUPPORT / RESISTANCE
    # -----------------------------------------------------
    closed_df = df.iloc[:-1]
    support = float(
        closed_df["low"]
        .tail(SR_PERIOD)
        .min()
    )
    resistance = float(
        closed_df["high"]
        .tail(SR_PERIOD)
        .max()
    )
    sr_range = max(
        resistance - support,
        0.00000001
    )
    near_support = (
        c <=
        support +
        sr_range * 0.22
    )
    near_resistance = (
        c >=
        resistance -
        sr_range * 0.22
    )
    room_call = (
        resistance - c
    ) / sr_range
    room_put = (
        c - support
    ) / sr_range
    enough_room_call = (
        room_call >= 0.15
    )
    enough_room_put = (
        room_put >= 0.15
    )
    # -----------------------------------------------------
    # PULLBACK
    # -----------------------------------------------------
    pullback_call = (
        (
            l <= ema_fast
            or
            l <= ema_slow
            or
            near_support
        )
        and
        c >= ema_fast
    )
    pullback_put = (
        (
            h >= ema_fast
            or
            h >= ema_slow
            or
            near_resistance
        )
        and
        c <= ema_fast
    )
    # =====================================================
    # SCORE
    # =====================================================
    call_score = 0
    put_score = 0
    # MASTER STRUCTURE
    if trend_call:
        call_score += 30
    if trend_put:
        put_score += 30
    # EMA
    if ema_call:
        call_score += 12
    if ema_put:
        put_score += 12
    # FLOW
    if flow_up:
        call_score += 8
    if flow_down:
        put_score += 8
    # REJECTION
    if bull_rejection:
        call_score += 12
    if bear_rejection:
        put_score += 12
    # RSI
    if rsi_value > RSI_MID:
        call_score += 5
    if rsi_value < RSI_MID:
        put_score += 5
    # PULLBACK
    if pullback_call:
        call_score += 8
    if pullback_put:
        put_score += 8
    # ROOM
    if enough_room_call:
        call_score += 5
    if enough_room_put:
        put_score += 5
    # SUPPORT
    if near_support:
        call_score += 5
    # RESISTANCE
    if near_resistance:
        put_score += 5
    # PENALTY
    if near_resistance:
        call_score -= 8
    if near_support:
        put_score -= 8
    # LIMIT
    call_score = max(
        0,
        min(
            call_score,
            100
        )
    )
    put_score = max(
        0,
        min(
            put_score,
            100
        )
    )
    # =====================================================
    # GAP
    # =====================================================
    gap = (
        call_score -
        put_score
    )
    # =====================================================
    # DIRECTION
    # =====================================================
    if (
        call_score >= MIN_SCORE
        and
        gap >= MIN_GAP
    ):
        direction = "CALL"
    elif (
        put_score >= MIN_SCORE
        and
        -gap >= MIN_GAP
    ):
        direction = "PUT"
    elif (
        call_score >= PRE_SCORE
        and
        gap >= MIN_GAP
    ):
        direction = "PRE CALL"
    elif (
        put_score >= PRE_SCORE
        and
        -gap >= MIN_GAP
    ):
        direction = "PRE PUT"
    else:
        direction = "WAIT"
    # =====================================================
    # RETURN
    # =====================================================
    return {
        "direction": direction,
        "call_score": call_score,
        "put_score": put_score,
        "gap": gap,
        "trend_call": trend_call,
        "trend_put": trend_put,
        "ema_call": ema_call,
        "ema_put": ema_put,
        "flow_up": flow_up,
        "flow_down": flow_down,
        "bull_rejection":
            bull_rejection,
        "bear_rejection":
            bear_rejection,
        "pullback_call":
            pullback_call,
        "pullback_put":
            pullback_put,
        "near_support":
            near_support,
        "near_resistance":
            near_resistance,
        "room_call":
            room_call,
        "room_put":
            room_put,
        "rsi":
            rsi_value,
        "price":
            c,
        "support":
            support,
        "resistance":
            resistance
    }
# =========================================================
# V8 MASTER + ENTRY
# =========================================================
def calculate_v8_score(
    df15,
    df5,
    strict_mode=False
):
    # -----------------------------------------------------
    # 15M MASTER SCORE
    # -----------------------------------------------------
    master = calculate_tf_score(
        df15
    )
    # -----------------------------------------------------
    # 5M ENTRY SCORE
    # -----------------------------------------------------
    entry = calculate_tf_score(
        df5
    )
    # =====================================================
    # 15M MASTER DIRECTION
    # =====================================================
    master_call = (
        master["direction"] == "CALL"
    )
    master_put = (
        master["direction"] == "PUT"
    )
    # -----------------------------------------------------
    # PRE MASTER
    # -----------------------------------------------------
    pre_master_call = (
        master["call_score"] >= PRE_SCORE
        and
        master["gap"] >= MIN_GAP
    )
    pre_master_put = (
        master["put_score"] >= PRE_SCORE
        and
        -master["gap"] >= MIN_GAP
    )
    # =====================================================
    # 5M ENTRY
    # =====================================================
    entry_call = (
        entry["call_score"] >= MIN_SCORE
        and
        entry["gap"] >= MIN_GAP
        and
        entry["ema_call"]
        and
        entry["bull_rejection"]
        and
        entry["pullback_call"]
    )
    entry_put = (
        entry["put_score"] >= MIN_SCORE
        and
        -entry["gap"] >= MIN_GAP
        and
        entry["ema_put"]
        and
        entry["bear_rejection"]
        and
        entry["pullback_put"]
    )
    # -----------------------------------------------------
    # STRICT
    # -----------------------------------------------------
    if strict_mode:
        entry_call = (
            entry_call
            and
            entry["flow_up"]
        )
        entry_put = (
            entry_put
            and
            entry["flow_down"]
        )
    # =====================================================
    # FINAL
    # =====================================================
    final_direction = "WAIT"
    final_reason = (
        "ยังไม่ครบเงื่อนไข"
    )
    # -----------------------------------------------------
    # MASTER CALL + ENTRY CALL
    # -----------------------------------------------------
    if (
        master_call
        and
        entry_call
    ):
        final_direction = "CALL"
        final_reason = (
            "15M MASTER CALL + "
            "5M ENTRY CALL"
        )
    # -----------------------------------------------------
    # MASTER PUT + ENTRY PUT
    # -----------------------------------------------------
    elif (
        master_put
        and
        entry_put
    ):
        final_direction = "PUT"
        final_reason = (
            "15M MASTER PUT + "
            "5M ENTRY PUT"
        )
    # -----------------------------------------------------
    # PRE CALL
    # -----------------------------------------------------
    elif (
        (
            master_call
            or
            pre_master_call
        )
        and
        entry["direction"] in (
            "PRE CALL",
            "CALL"
        )
    ):
        final_direction = "PRE CALL"
        final_reason = (
            "15M เริ่มเป็น CALL "
            "+ 5M กำลังยืนยัน"
        )
    # -----------------------------------------------------
    # PRE PUT
    # -----------------------------------------------------
    elif (
        (
            master_put
            or
            pre_master_put
        )
        and
        entry["direction"] in (
            "PRE PUT",
            "PUT"
        )
    ):
        final_direction = "PRE PUT"
        final_reason = (
            "15M เริ่มเป็น PUT "
            "+ 5M กำลังยืนยัน"
        )
    # -----------------------------------------------------
    # CONFLICT
    # -----------------------------------------------------
    elif (
        master_call
        and
        entry["direction"] == "PUT"
    ):
        final_direction = "WAIT"
        final_reason = (
            "15M CALL แต่ 5M PUT "
            "→ BLOCK"
        )
    elif (
        master_put
        and
        entry["direction"] == "CALL"
    ):
        final_direction = "WAIT"
        final_reason = (
            "15M PUT แต่ 5M CALL "
            "→ BLOCK"
        )
    # =====================================================
    # RETURN
    # =====================================================
    return {
        "direction":
            final_direction,
        "reason":
            final_reason,
        # 15M
        "master":
            master,
        "master_direction":
            master["direction"],
        "master_call":
            master_call,
        "master_put":
            master_put,
        # 5M
        "entry":
            entry,
        "entry_direction":
            entry["direction"],
        "entry_call":
            entry_call,
        "entry_put":
            entry_put
    }
# =========================================================
# DISCORD FORMAT
# =========================================================
def format_signal(
    symbol,
    result
):
    master = result["master"]
    entry = result["entry"]
    now = thai_now().strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    final = result[
        "direction"
    ]
    # -----------------------------------------------------
    # FINAL ICON
    # -----------------------------------------------------
    if final == "CALL":
        final_text = "🟢 CALL"
    elif final == "PUT":
        final_text = "🔴 PUT"
    elif final == "PRE CALL":
        final_text = "🟡 PRE CALL"
    elif final == "PRE PUT":
        final_text = "🟡 PRE PUT"
    else:
        final_text = "⚪ WAIT"
    # -----------------------------------------------------
    # MASTER
    # -----------------------------------------------------
    if master["direction"] == "CALL":
        master_text = "🟢 CALL"
    elif master["direction"] == "PUT":
        master_text = "🔴 PUT"
    elif master["direction"] == "PRE CALL":
        master_text = "🟡 PRE CALL"
    elif master["direction"] == "PRE PUT":
        master_text = "🟡 PRE PUT"
    else:
        master_text = "⚪ WAIT"
    # -----------------------------------------------------
    # ENTRY
    # -----------------------------------------------------
    if entry["direction"] == "CALL":
        entry_text = "🟢 CALL"
    elif entry["direction"] == "PUT":
        entry_text = "🔴 PUT"
    elif entry["direction"] == "PRE CALL":
        entry_text = "🟡 PRE CALL"
    elif entry["direction"] == "PRE PUT":
        entry_text = "🟡 PRE PUT"
    else:
        entry_text = "⚪ WAIT"
    # -----------------------------------------------------
    # GAP TEXT
    # -----------------------------------------------------
    master_gap = master["gap"]
    entry_gap = entry["gap"]
    master_gap_text = (
        f"+{master_gap}"
        if master_gap >= 0
        else str(master_gap)
    )
    entry_gap_text = (
        f"+{entry_gap}"
        if entry_gap >= 0
        else str(entry_gap)
    )
    # -----------------------------------------------------
    # MESSAGE
    # -----------------------------------------------------
    message = (
        f"**🔥 TRADEIFY V8 SYNC**\n"
        f"`{symbol}`\n"
        f"เวลา: `{now}`\n"
        f"ราคา: `{entry['price']}`\n\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📊 **15M MASTER**\n"
        f"ทิศทาง: **{master_text}**\n"
        f"CALL Score: `{master['call_score']}`\n"
        f"PUT Score: `{master['put_score']}`\n"
        f"Gap: `{master_gap_text}`\n"
        f"EMA CALL: `{master['ema_call']}`\n"
        f"EMA PUT: `{master['ema_put']}`\n"
        f"Rejection CALL: "
        f"`{master['bull_rejection']}`\n"
        f"Rejection PUT: "
        f"`{master['bear_rejection']}`\n\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🎯 **5M ENTRY**\n"
        f"ทิศทาง: **{entry_text}**\n"
        f"CALL Score: `{entry['call_score']}`\n"
        f"PUT Score: `{entry['put_score']}`\n"
        f"Gap: `{entry_gap_text}`\n"
        f"EMA CALL: `{entry['ema_call']}`\n"
        f"EMA PUT: `{entry['ema_put']}`\n"
        f"Rejection CALL: "
        f"`{entry['bull_rejection']}`\n"
        f"Rejection PUT: "
        f"`{entry['bear_rejection']}`\n"
        f"Pullback CALL: "
        f"`{entry['pullback_call']}`\n"
        f"Pullback PUT: "
        f"`{entry['pullback_put']}`\n"
        f"RSI: `{entry['rsi']:.2f}`\n\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🔥 **FINAL**\n"
        f"**{final_text}**\n"
        f"เหตุผล: `{result['reason']}`\n\n"
        f"Score ขั้นต่ำ: `{MIN_SCORE}`\n"
        f"Gap ขั้นต่ำ: `{MIN_GAP}`"
    )
    return message
