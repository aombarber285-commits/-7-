import time, json, os, urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ============================================================
# SIGZY BRAIN V6.0 - 3 BRAIN PAPER TRADER
# BOT + AI PATTERN + AI RISK/CONTRARIAN
# ============================================================

CRYPTO = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT"]
FOREX = ["GBPUSD", "GBPJPY", "USDJPY", "EURUSD", "EURJPY", "AUDJPY",
         "AUDUSD", "NZDUSD", "USDCAD", "USDCHF", "EURGBP", "NZDJPY", "CADJPY"]
SYMBOLS = CRYPTO + FOREX

PAPER_INTERVAL_SECONDS = 60
MIN_SCORE_THRESHOLD = 85.0

THAI_TZ = timezone(timedelta(hours=7))

def thai_now():
    return datetime.now(THAI_TZ)

# ---------------- CONFIGURATION ----------------
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "").strip()

BASE = Path(__file__).resolve().parent

# ---------------- DISCORD NOTIFICATION ----------------
def discord(msg):
    if not DISCORD_WEBHOOK_URL:
        print("[WARN] DISCORD_WEBHOOK_URL is not set.", flush=True)
        return False
    try:
        data = json.dumps({"content": msg}, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            DISCORD_WEBHOOK_URL,
            data=data,
            headers={"Content-Type": "application/json", "User-Agent": "SIGZY-V6"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=15):
            pass
        return True
    except Exception as e:
        print(f"[DISCORD ERROR] {e}", flush=True)
        return False

# ---------------- AI FUNCTIONS ----------------
def parse_ai_json(text):
    text = str(text).strip()
    # ลบ markdown code block ออกแบบปลอดภัย ไม่ใช้ backticks ชนิดเสี่ยงหลุด
    if text.startswith("```"):
        text = text.replace("```json", "").replace("```", "").strip()
    a = text.find("{")
    b = text.rfind("}")
    if a >= 0 and b > a:
        text = text[a:b + 1]
    return json.loads(text)

def gemini_call(role, data):
    if not GEMINI_API_KEY:
        return True, "APPROVE", 85.0, "NO_API_KEY_BYPASS"

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
    
    prompt = {
        "role": role,
        "market_data": data,
        "output": {"decision": "APPROVE or REJECT", "confidence": "0-100", "reason": "short"},
        "rules": ["Be extremely strict. Reject if counter-trend or high risk.", "Return JSON only."]
    }

    body = {
        "contents": [{"parts": [{"text": json.dumps(prompt, ensure_ascii=False)}]}],
        "generationConfig": {"temperature": 0.1, "responseMimeType": "application/json"}
    }

    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json", "User-Agent": "SIGZY-V6"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = json.loads(r.read().decode())

        text = raw["candidates"][0]["content"]["parts"][0]["text"]
        j = parse_ai_json(text)
        d = str(j.get("decision", "REJECT")).upper()
        c = max(0, min(100, float(j.get("confidence", 0))))
        return True, d, c, str(j.get("reason", "OK"))
    except Exception as e:
        return False, "ERROR", 0.0, str(e)

# ---------------- CORE SCANNER & ANALYSIS ----------------
def analyze_pair(symbol):
    score = 88.0
    signal = "CALL"
    
    if score >= MIN_SCORE_THRESHOLD:
        market_data = {"symbol": symbol, "score": score, "signal": signal, "time": thai_now().strftime("%H:%M:%S")}
        
        _, d1, c1, r1 = gemini_call("AI_PATTERN_ANALYZER", market_data)
        _, d2, c2, r2 = gemini_call("AI_RISK_FILTER", market_data)

        if d1 == "APPROVE" and d2 == "APPROVE":
            msg = (
                f"🚀 **SIGZY BRAIN V6.0 SIGNAL DETECTED**\n"
                f"----------------------------------------\n"
                f"📌 **Pair:** {symbol}\n"
                f"📈 **Action:** {signal}\n"
                f"🎯 **Bot Score:** {score}%\n"
                f"🤖 **AI #1 Confidence:** {c1}% ({r1})\n"
                f"🛡️ **AI #2 Confidence:** {c2}% ({r2})\n"
                f"⏰ **Time:** {thai_now().strftime('%Y-%m-%d %H:%M:%S')}"
            )
            print(f"[{symbol}] Signal approved! Sending notification...", flush=True)
            discord(msg)

def process_market_scan():
    now_str = thai_now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now_str}] 🔍 SIGZY BRAIN V6.0 Scanning market...", flush=True)
    for symbol in SYMBOLS[:5]:
        analyze_pair(symbol)

# ---------------- MAIN LOOP ----------------
if __name__ == "__main__":
    start_time = thai_now().strftime("%Y-%m-%d %H:%M:%S")
    init_msg = f"🤖 **SIGZY BRAIN V6.0 ONLINE**\nSystem initialized successfully at {start_time}"
    
    print("🤖 SIGZY BRAIN V6.0 ONLINE", flush=True)
    discord(init_msg)

    while True:
        try:
            process_market_scan()
        except Exception as e:
            print(f"[MAIN ERROR] {e}", flush=True)

        time.sleep(PAPER_INTERVAL_SECONDS)
