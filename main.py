# =========================================================
# TWELVE DATA MARKET DATA
# =========================================================
TWELVE_DATA_API_KEY = os.getenv(
    "TWELVE_DATA_API_KEY",
    ""
)
TWELVE_DATA_URL = (
    "https://api.twelvedata.com/time_series"
)
def normalize_twelve_symbol(symbol):
    """
    แปลงชื่อคู่เงินของระบบเราให้เป็นรูปแบบ Twelve Data
    EURUSD_OTC -> EUR/USD
    EURUSD     -> EUR/USD
    GBPUSD_OTC -> GBP/USD
    USDJPY_OTC -> USD/JPY
    """
    symbol = symbol.strip().upper()
    # เอา _OTC ออก
    if symbol.endswith("_OTC"):
        symbol = symbol[:-4]
    # ถ้ามี / อยู่แล้ว
    if "/" in symbol:
        return symbol
    # Forex 6 ตัวอักษร
    if len(symbol) == 6:
        return (
            symbol[:3]
            + "/"
            + symbol[3:]
        )
    return symbol
def normalize_interval(timeframe):
    """
    แปลง timeframe ของ TRADEIFY
    ให้เป็นรูปแบบ Twelve Data
    """
    mapping = {
        "5m": "5min",
        "15m": "15min",
        "1m": "1min",
        "30m": "30min",
        "1h": "1h",
        "4h": "4h",
        "1d": "1day"
    }
    tf = str(
        timeframe
    ).lower().strip()
    return mapping.get(
        tf,
        tf
    )
def get_market_data(
    symbol,
    timeframe,
    limit=200
):
    """
    ดึง OHLC จริงจาก Twelve Data
    ใช้กับ:
        5M
        15M
    Return:
        pandas.DataFrame
    Columns:
        datetime
        open
        high
        low
        close
    IMPORTANT:
        ต้องตั้งค่า Railway Variable:
        TWELVE_DATA_API_KEY=YOUR_KEY
    """
    if not TWELVE_DATA_API_KEY:
        raise RuntimeError(
            "ยังไม่ได้ตั้ง TWELVE_DATA_API_KEY "
            "ใน Railway Variables"
        )
    td_symbol = normalize_twelve_symbol(
        symbol
    )
    interval = normalize_interval(
        timeframe
    )
    # ---------------------------------------------
    # จำกัดจำนวนข้อมูล
    # ---------------------------------------------
    limit = max(
        100,
        min(int(limit), 5000)
    )
    params = {
        "symbol": td_symbol,
        "interval": interval,
        "outputsize": limit,
        "timezone": "Asia/Bangkok",
        "apikey": TWELVE_DATA_API_KEY
    }
    try:
        response = requests.get(
            TWELVE_DATA_URL,
            params=params,
            timeout=15
        )
    except requests.RequestException as e:
        raise RuntimeError(
            f"Twelve Data connection error: {e}"
        )
    # ---------------------------------------------
    # HTTP ERROR
    # ---------------------------------------------
    if response.status_code != 200:
        raise RuntimeError(
            "Twelve Data HTTP "
            f"{response.status_code}: "
            f"{response.text[:500]}"
        )
    # ---------------------------------------------
    # JSON
    # ---------------------------------------------
    try:
        data = response.json()
    except ValueError:
        raise RuntimeError(
            "Twelve Data ส่งข้อมูลที่ไม่ใช่ JSON"
        )
    # ---------------------------------------------
    # API ERROR
    # ---------------------------------------------
    if data.get("status") == "error":
        message = data.get(
            "message",
            "Unknown Twelve Data error"
        )
        raise RuntimeError(
            f"Twelve Data API Error: {message}"
        )
    # ---------------------------------------------
    # VALUES
    # ---------------------------------------------
    values = data.get(
        "values"
    )
    if not values:
        raise RuntimeError(
            f"Twelve Data ไม่มีข้อมูล "
            f"{td_symbol} {interval}"
        )
    # ---------------------------------------------
    # DATAFRAME
    # ---------------------------------------------
    df = pd.DataFrame(
        values
    )
    required = [
        "datetime",
        "open",
        "high",
        "low",
        "close"
    ]
    missing = [
        column
        for column in required
        if column not in df.columns
    ]
    if missing:
        raise RuntimeError(
            "Twelve Data ขาด column: "
            + ", ".join(missing)
        )
    # ---------------------------------------------
    # NUMERIC
    # ---------------------------------------------
    for column in [
        "open",
        "high",
        "low",
        "close"
    ]:
        df[column] = pd.to_numeric(
            df[column],
            errors="coerce"
        )
    # ---------------------------------------------
    # DATETIME
    # ---------------------------------------------
    df["datetime"] = pd.to_datetime(
        df["datetime"],
        errors="coerce"
    )
    # ---------------------------------------------
    # CLEAN
    # ---------------------------------------------
    df = df.dropna(
        subset=[
            "datetime",
            "open",
            "high",
            "low",
            "close"
        ]
    )
    # Twelve Data คืนข้อมูลล่าสุดก่อน
    # เราต้องเรียงเก่า -> ใหม่
    # เพื่อให้ EMA / RSI / structure ถูกต้อง
    df = df.sort_values(
        "datetime"
    ).reset_index(
        drop=True
    )
    # ---------------------------------------------
    # FINAL COLUMNS
    # ---------------------------------------------
    df = df[
        [
            "datetime",
            "open",
            "high",
            "low",
            "close"
        ]
    ]
    # ---------------------------------------------
    # VALIDATION
    # ---------------------------------------------
    if len(df) < 100:
        raise RuntimeError(
            f"ข้อมูล {td_symbol} {interval} "
            f"ไม่พอ: {len(df)} candles"
        )
    logger.info(
        "Twelve Data OK | %s | %s | %s candles | "
        "latest=%s",
        td_symbol,
        interval,
        len(df),
        df["datetime"].iloc[-1]
    )
    return df
