from pathlib import Path

src = Path("/mnt/data/tradeify_v6_1in3_real_tracker.py")
code = src.read_text(encoding="utf-8")

# Railway-safe hardening:
# - Never attempt to write /mnt/data or any other local export file.
# - State is in memory only; Railway can restart and reset it.
# - Keep all configuration in environment variables.
# - Add a simple startup message that makes the deployment expectation explicit.
banner = '''# -*- coding: utf-8 -*-
"""
TRADEIFY v6 — RAILWAY SAFE
IMPORTANT:
- This file is meant to be the complete main.py for Railway.
- It does NOT create/read /mnt/data.
- Configure DISCORD_WEBHOOK_URL, MARKET_MODE and OTC_API_URL in Railway Variables.
- State is memory-only and resets when Railway restarts/redeploys.
"""
'''

# Replace the original module header/docstring area with the concise Railway-safe header,
# while retaining the executable code from the first import onward.
import_pos = code.find("import os")
if import_pos == -1:
    raise RuntimeError("Could not locate imports in generated script")

railway_code = banner + "\n" + code[import_pos:]

# Remove any accidental /mnt/data file-write code if present.
# (Current generated v6 does not contain it, but this makes the output safe.)
lines = railway_code.splitlines()
filtered = []
for line in lines:
    if "/mnt/data/" in line and ("write_text" in line or "open(" in line or "Path(" in line):
        continue
    filtered.append(line)
railway_code = "\n".join(filtered) + "\n"

out = Path("/mnt/data/main_railway.py")
out.write_text(railway_code, encoding="utf-8")

print(f"สร้างไฟล์ Railway-safe แล้ว: {out}")
print(f"ขนาด: {out.stat().st_size} bytes")
