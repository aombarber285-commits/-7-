from pathlib import Path

src = Path("/mnt/data/SIGZY_BRAIN_V6_0_3BRAIN.py")
dst = Path("/mnt/data/main_RAILWAY.py")
code = src.read_text(encoding="utf-8")

old = '''BASE=Path(__file__).resolve().parent
MEMORY_CANDIDATES=[
    BASE/"bot_memory_sigzy_brain_v6.json",
    BASE/"bot_memory_sigzy_brain_v5.json",
    BASE/"bot_memory_sigzy_brain_v4_crypto_forex.json",
    BASE/"bot_memory_sigzy_discord.json",
]
MEMORY_FILE=str(next((p for p in MEMORY_CANDIDATES if p.exists()),MEMORY_CANDIDATES[0]))
DISCORD_WEBHOOK_URL=os.getenv("DISCORD_WEBHOOK_URL","").strip()
'''

new = '''# ============================================================
# RAILWAY-SAFE STORAGE
# IMPORTANT:
# - This file is the actual bot. It does NOT create another .py file.
# - Never writes to /mnt/data (that path caused the Railway crash).
# - Put an old memory JSON in the same folder/repository to migrate it.
# - You can override the memory filename with MEMORY_FILE.
# ============================================================
BASE=Path(os.getenv("SIGZY_DATA_DIR", str(Path(__file__).resolve().parent))).resolve()
BASE.mkdir(parents=True, exist_ok=True)

ENV_MEMORY=os.getenv("MEMORY_FILE","").strip()
if ENV_MEMORY:
    MEMORY_FILE=str(Path(ENV_MEMORY).expanduser())
else:
    MEMORY_CANDIDATES=[
        BASE/"bot_memory_sigzy_brain_v6.json",
        BASE/"bot_memory_sigzy_brain_v5.json",
        BASE/"bot_memory_sigzy_brain_v4_crypto_forex.json",
        BASE/"bot_memory_sigzy_discord.json",
    ]
    # Prefer the existing V3/V4-era memory so the learned history is preserved.
    MEMORY_FILE=str(next((p for p in MEMORY_CANDIDATES if p.exists()),
                         BASE/"bot_memory_sigzy_brain_v4_crypto_forex.json"))

DISCORD_WEBHOOK_URL=os.getenv("DISCORD_WEBHOOK_URL","").strip()
'''

if old not in code:
    raise SystemExit("Target block not found")

code = code.replace(old, new)

# Make the startup message explicit so Railway logs are easy to verify.
old2 = '''    print(f"AI Model: {GEMINI_MODEL} | Threshold: {AI_CONFIDENCE_THRESHOLD} | Strict: {STRICT_AI_CONSENSUS}",flush=True)
'''
new2 = '''    print(f"AI Model: {GEMINI_MODEL} | Threshold: {AI_CONFIDENCE_THRESHOLD} | Strict: {STRICT_AI_CONSENSUS}",flush=True)
    print(f"📁 Memory file: {MEMORY_FILE}",flush=True)
    print("🚀 First scan starts immediately. No initial 60-second wait.",flush=True)
'''
code = code.replace(old2, new2)

dst.write_text(code, encoding="utf-8")
print(f"สร้างไฟล์เรียบร้อย: {dst}")
print(f"ขนาด: {dst.stat().st_size:,} bytes")
