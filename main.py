def api_get(params, retries=4):

    global LAST_API_CALL

    with API_LOCK:

        for attempt in range(retries):

            try:

                now = time.time()

                wait = 2.0 - (
                    now - LAST_API_CALL
                )

                if wait > 0:
                    time.sleep(wait)

                response = requests.get(
                    TWELVE_DATA_URL,
                    params=params,
                    timeout=30
                )

                LAST_API_CALL = time.time()

                logger.info(
                    "TWELVE DATA HTTP=%s",
                    response.status_code
                )

                # -----------------------------------------
                # RATE LIMIT
                # -----------------------------------------

                if response.status_code == 429:

                    retry_after = 10

                    try:
                        payload = response.json()

                        retry_after = float(
                            payload.get(
                                "retry_after",
                                payload.get(
                                    "wait",
                                    10
                                )
                            )
                        )

                    except Exception:
                        pass

                    retry_after = min(
                        max(
                            retry_after,
                            5
                        ),
                        60
                    )

                    logger.warning(
                        "TWELVE DATA 429 | "
                        "WAIT %.1fs",
                        retry_after
                    )

                    time.sleep(
                        retry_after
                    )

                    continue

                # -----------------------------------------
                # HTTP ERROR
                # -----------------------------------------

                if response.status_code != 200:

                    logger.error(
                        "TWELVE DATA HTTP ERROR %s | %s",
                        response.status_code,
                        response.text[:1000]
                    )

                    raise RuntimeError(
                        "Twelve Data HTTP "
                        +
                        str(
                            response.status_code
                        )
                        +
                        ": "
                        +
                        response.text[:1000]
                    )

                # -----------------------------------------
                # JSON
                # -----------------------------------------

                try:

                    data = response.json()

                except Exception:

                    raise RuntimeError(
                        "Twelve Data ส่งข้อมูล "
                        "ไม่ใช่ JSON: "
                        +
                        response.text[:1000]
                    )

                # -----------------------------------------
                # API ERROR
                # -----------------------------------------

                if isinstance(
                    data,
                    dict
                ):

                    if data.get(
                        "status"
                    ) == "error":

                        message = data.get(
                            "message",
                            "Unknown Twelve Data error"
                        )

                        code = data.get(
                            "code",
                            ""
                        )

                        logger.error(
                            "TWELVE DATA API ERROR "
                            "code=%s | %s",
                            code,
                            message
                        )

                        raise RuntimeError(
                            "Twelve Data API ERROR: "
                            +
                            str(
                                message
                            )
                        )

                return data

            except requests.RequestException as e:

                logger.warning(
                    "TWELVE DATA CONNECTION ERROR "
                    "attempt=%s/%s | %s",
                    attempt + 1,
                    retries,
                    e
                )

                if attempt < retries - 1:

                    time.sleep(
                        3 *
                        (
                            attempt + 1
                        )
                    )

                else:

                    raise RuntimeError(
                        "Twelve Data connection failed: "
                        +
                        str(e)
                    )

        raise RuntimeError(
            "Twelve Data request failed "
            "after retries"
        )
