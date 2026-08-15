from pathlib import Path
import re

src = Path("/mnt/data/tradeify_v2_1.py")
dst = Path("/mnt/data/tradeify_v2_1_utf8_fix.py")

text = src.read_text(encoding="utf-8")

old = '''def send_discord(message):
    if not DISCORD_WEBHOOK_URL:
        log("Discord: webhook not configured")
        return False
    try:
        r = requests.post(DISCORD_WEBHOOK_URL, json={"content": message[:1900]}, timeout=10)
        if r.status_code in (200, 204):
            return True
        log(f"Discord HTTP {r.status_code}: {r.text[:200]}")
    except Exception as e:
        log(f"Discord error: {e}")
    return False
'''

new = r'''def repair_mojibake(value):
    """Repair common UTF-8 -> Latin-1/CP1252 mojibake before Discord send."""
    if not isinstance(value, str):
        return value

    # Only attempt repair when typical mojibake markers are present.
    markers = ("ð", "Ã", "Â", "â", "à¸", "à¹", "ï¸")
    if not any(m in value for m in markers):
        return value

    for encoding in ("latin1", "cp1252"):
        try:
            fixed = value.encode(encoding).decode("utf-8")
            # Keep the repair only if it removed the common mojibake markers.
            if sum(value.count(m) for m in markers) > sum(fixed.count(m) for m in markers):
                return fixed
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass

    return value


def send_discord(message):
    if not DISCORD_WEBHOOK_URL:
        log("Discord: webhook not configured")
        return False
    try:
        # Normalize any already-corrupted UTF-8 text first.
        message = repair_mojibake(str(message))

        # Send explicit UTF-8 bytes + charset so no intermediary guesses
        # Latin-1/ASCII for Thai text or emoji.
        payload = json.dumps(
            {"content": message[:1900]},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")

        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "Accept": "application/json",
        }

        r = requests.post(
            DISCORD_WEBHOOK_URL,
            data=payload,
            headers=headers,
            timeout=10,
        )

        if r.status_code in (200, 204):
            return True
        log(f"Discord HTTP {r.status_code}: {r.text[:200]}")
    except Exception as e:
        log(f"Discord error: {e}")
    return False
'''

if old not in text:
    raise RuntimeError("ไม่พบฟังก์ชัน send_discord เดิมในไฟล์")

text = text.replace(old, new)

# Make the startup warning explicitly identify the data condition without
# relying on any non-UTF8 transport behavior.
text = text.replace(
    'f"⚠️ ถ้า OTC API OFF ระบบใช้ Yahoo public FX proxy และจะไม่สร้าง signal จากข้อมูลเก่า"',
    'f"⚠️ OTC API OFF: ใช้ Yahoo public FX proxy เป็นแหล่งข้อมูลสำรอง และห้ามสร้าง signal จากข้อมูลเก่า"'
)

dst.write_text(text, encoding="utf-8")

# Validate syntax and UTF-8 round-trip.
compile(text, str(dst), "exec")
dst.read_bytes().decode("utf-8")

print(f"สร้างไฟล์แก้ UTF-8 สำเร็จ: {dst}")
print(f"ขนาดไฟล์: {dst.stat().st_size:,} bytes")
print("Python syntax: PASS")
print("UTF-8 round-trip: PASS")
