def thai_datetime_from_ts(ts):
    """Convert Unix timestamp -> Thai datetime"""
    return datetime.fromtimestamp(
        int(ts),
        tz=timezone.utc
    ).astimezone(THAI_TZ)


def thai_date_from_ts(ts):
    return thai_datetime_from_ts(ts).strftime(
        "%Y-%m-%d"
    )


def thai_time_from_ts(ts):
    return thai_datetime_from_ts(ts).strftime(
        "%H:%M:%S"
    )


def candle_time_info(timestamp, timeframe_minutes=5):
    """
    timestamp = เวลาแท่ง 5M ตัวสุดท้าย
    คืนค่าเวลาเปิด / ปิดแท่งเป็นเวลาไทย
    """

    close_ts = (
        int(timestamp)
        + 60
    )

    open_ts = (
        close_ts
        -
        timeframe_minutes * 60
    )

    return {
        "open_ts": open_ts,
        "close_ts": close_ts,
        "date": thai_date_from_ts(open_ts),
        "open_time": thai_time_from_ts(open_ts),
        "close_time": thai_time_from_ts(close_ts)
    }
