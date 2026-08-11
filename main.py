from pathlib import Path

code = r'''import os
import sys
import time
import json
import runpy
from pathlib import Path

# ============================================================
# SIGZY BRAIN - RAILWAY SAFE LAUNCHER
# แก้ปัญหา FileNotFoundError จาก /mnt/data
# ใช้ได้ทั้ง Railway และ Python บนมือถือ
# ============================================================

BASE_DIR = Path(__file__).resolve().parent
BRAIN_FILE = BASE_DIR / "SIGZY_BRAIN_V6_0_3BRAIN.py"
MEMORY_FILE = BASE_DIR / "bot_memory_sigzy_brain_v4_crypto_forex.json"

print("=" * 64)
print("SIGZY BRAIN V6.0 - RAILWAY SAFE")
print("=" * 64)
print(f"[PATH] BASE_DIR : {BASE_DIR}")
print(f"[PATH] BRAIN    : {BRAIN_FILE}")
print(f"[PATH] MEMORY   : {MEMORY_FILE}")

# ------------------------------------------------------------
# Railway / environment variables
# ------------------------------------------------------------
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()

if DISCORD_WEBHOOK_URL:
    print("[OK] DISCORD_WEBHOOK_URL is set.")
else:
    print("[WARN] DISCORD_WEBHOOK_URL is not set.")

if GEMINI_API_KEY:
    print("[OK] GEMINI_API_KEY is set.")
else:
    print("[WARN] GEMINI_API_KEY is missing. AI-approved signals may be disabled.")

# ------------------------------------------------------------
# ใช้คลังสมองเดิม ถ้ามีอยู่ในโฟลเดอร์เดียวกัน
# ------------------------------------------------------------
if MEMORY_FILE.exists():
    print(f"[MEMORY] Found existing memory: {MEMORY_FILE}")
else:
    print("[MEMORY] Existing memory file was not found in Railway.")
    print("[MEMORY] The bot can create a new memory file if V6 supports it.")

# ------------------------------------------------------------
# ห้ามใช้ /mnt/data บน Railway
# ถ้าไฟล์สมองอยู่ในโฟลเดอร์เดียวกัน ให้รันทันที
# ------------------------------------------------------------
if not BRAIN_FILE.exists():
    print()
    print("[ERROR] Brain file not found.")
    print(f"[ERROR] Expected: {BRAIN_FILE}")
    print()
    print("ให้อยู่ใน repository เดียวกันแบบนี้:")
    print("  ai+บอท.py")
    print("  SIGZY_BRAIN_V6_0_3BRAIN.py")
    print("  bot_memory_sigzy_brain_v4_crypto_forex.json  (ถ้ามี)")
    print()
    sys.exit(1)

# ส่ง environment ต่อให้ไฟล์สมอง
os.environ["SIGZY_BASE_DIR"] = str(BASE_DIR)
os.environ["SIGZY_BRAIN_FILE"] = str(BRAIN_FILE)
os.environ["SIGZY_MEMORY_FILE"] = str(MEMORY_FILE)

print("[START] Launching SIGZY BRAIN V6.0...")
print("=" * 64)

try:
    # รันสมองโดยตรงใน process เดียวกัน
    runpy.run_path(str(BRAIN_FILE), run_name="__main__")
except KeyboardInterrupt:
    print("\n[STOP] Bot stopped by user.")
except SystemExit as e:
    print(f"\n[EXIT] Brain exited with code: {e.code}")
    raise
except Exception as e:
    print("\n[CRASH] SIGZY BRAIN stopped.")
    print(f"[ERROR] {type(e).__name__}: {e}")
    raise
'''

out = Path("/mnt/data/ai+บอท_RAILWAY_FIXED.py")
out.write_text(code, encoding="utf-8")

print(f"สร้างไฟล์เรียบร้อย: {out}")
