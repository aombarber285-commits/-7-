# ============================================================
# WIN SET
# ============================================================

if result == "WIN":

    SET_ACTIVE = False

    DAILY["set_wins"] += 1

    CURRENT_STEP = 1

    SET_MESSAGE = (
        f"🏆 SET #{set_number} WIN"
    )

    send_discord(

        "✅ **TRADE RESULT**\n"
        f"📌 `{symbol}`\n"
        f"➡️ **{direction}**\n"
        f"💰 Entry: `{entry}`\n"
        f"🏁 Exit: `{exit_price}`\n"
        f"🎯 Step: `{step}/3`\n"
        f"💵 Stake: `{stake}` บาท\n"
        f"📊 **WIN**\n"
        f"🏆 {SET_MESSAGE}\n"
        f"📈 Set Wins Today: "
        f"`{DAILY['set_wins']}`\n\n"
        f"🔄 เริ่ม SET ถัดไป\n"
        f"🎯 STEP 1"
    )
