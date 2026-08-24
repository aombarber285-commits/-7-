from pathlib import Path

src = Path("/mnt/data/tradeify_v6_1in3_real_tracker.py")
dst = Path("/mnt/data/main.py")

code = src.read_text(encoding="utf-8")

# Make a clean Railway entry file. It must contain the bot itself,
# not Python code that tries to read another file from /mnt/data.
if 'src.read_text(' in code or 'Path("/mnt/data/tradeify_v6_1in3_real_tracker.py")' in code:
    raise RuntimeError("ต้นฉบับยังเป็น wrapper ไม่ใช่ตัวบอทจริง")

dst.write_text(code, encoding="utf-8")

print(f"สร้างไฟล์พร้อมใช้สำหรับ Railway แล้ว: {dst}")
print(f"ขนาดไฟล์: {dst.stat().st_size:,} bytes")
