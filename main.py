import time, json, os, urllib.request
from datetime import datetime
from pathlib import Path

# ============================================================
# SIGZY BRAIN V6.0 - 3 BRAIN PAPER TRADER
# BOT + AI #1 PATTERN + AI #2 RISK/CONTRARIAN
# REAL MEMORY + BEST PAIR SELECTOR + DISCORD
# ============================================================

CRYPTO = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT"]
FOREX = ["GBPUSD", "GBPJPY", "USDJPY", "EURUSD", "EURJPY", "AUDJPY",
         "AUDUSD", "NZDUSD", "USDCAD", "USDCHF", "EURGBP", "NZDJPY", "CADJPY"]
SYMBOLS = CRYPTO + FOREX
YAHOO = {s: s + "=X" for s in FOREX}

PAPER_INTERVAL_SECONDS = 60
MIN_SCORE_THRESHOLD = 88.0
MAX_NEW_ALERTS_PER_SCAN = 1
MAX_SCAN_PAIRS = 18
SYMBOL_COOLDOWN_SECONDS = 300

PAIR_MIN_HISTORY = 10
SETUP_MIN_HISTORY = 10
PAIR_BLOCK_WR = 45.0
PAIR_WEAK_WR = 55.0
PAIR_FAVOR_WR = 65.0
PAIR_STRONG_WR = 70.0

# ---------------- CONFIGURATION ----------------
# หากมี Gemini API Key ให้ใส่ในอัญประกาศ หากไม่มีให้ปล่อยว่างไว้
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip() 
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()
AI_CONFIDENCE_THRESHOLD = float(os.getenv("AI_CONFIDENCE_THRESHOLD", "75"))
FINAL_SCORE_THRESHOLD = float(os.getenv("FINAL_SCORE_THRESHOLD", "80"))

# หากไม่มี GEMINI_API_KEY แนะนำให้ตั้ง STRICT_AI_CONSENSUS = False เพื่อให้บอททำงานส่งสัญญาณได้
STRICT_AI_CONSENSUS = False if not GEMINI_API_KEY else True

BASE = Path(__file__).resolve().parent
MEMORY_CANDIDATES = [
    BASE / "bot_memory_sigzy_brain_v6.json",
    BASE / "bot_memory_sigzy_brain_v5.json",
    BASE / "bot_memory_sigzy_brain_v4_crypto_forex.json",
    BASE / "bot_memory_sigzy_discord.json",
]
MEMORY_FILE = str(next((p for p in MEMORY_CANDIDATES if p.exists()), MEMORY_CANDIDATES[0]))

# ระบุ Discord Webhook URL ตรงนี้
DISCORD_WEBHOOK_URL = "https://discord.com/api/webhooks/1535993581414653973/g9d6Ma96SKD32EgcQs4oFoOc-gqd7vDqPNgpyN53BrJPMwImxQqKDqyDwWm6iJSbwOjD"

TUNING = {"DEFAULT": {"sr": .008, "zone": .015, "vol": 1.40}}
for s in CRYPTO:
    TUNING[s] = {
        "sr": {"BTCUSDT": .006, "ETHUSDT": .007, "SOLUSDT": .010, "XRPUSDT": .010, "BNBUSDT": .008}[s],
        "zone": {"BTCUSDT": .012, "ETHUSDT": .013, "SOLUSDT": .018, "XRPUSDT": .018, "BNBUSDT": .015}[s],
        "vol": {"BTCUSDT": 1.35, "ETHUSDT": 1.35, "SOLUSDT": 1.45, "XRPUSDT": 1.45, "BNBUSDT": 1.40}[s]}
for s in FOREX:
    TUNING[s] = {
        "sr": .005 if s in ("GBPUSD", "USDJPY", "EURUSD", "AUDUSD", "NZDUSD", "USDCAD", "USDCHF", "EURGBP") else .006,
        "zone": .012 if s in ("GBPUSD", "USDJPY", "EURUSD", "AUDUSD", "NZDUSD", "USDCAD", "USDCHF", "EURGBP") else .013,
        "vol": 1.20}

def discord(msg):
    if not DISCORD_WEBHOOK_URL:
        print("[WARN] DISCORD_WEBHOOK_URL is not set.", flush=True)
        return False
    try:
        data = json.dumps({"content": msg}, ensure_ascii=False).encode('utf-8')
        req = urllib.request.Request(DISCORD_WEBHOOK_URL, data=data,
            headers={"Content-Type": "application/json", "User-Agent": "SIGZY-V6"}, method="POST")
        with urllib.request.urlopen(req, timeout=15): pass
        return True
    except Exception as e:
        print("[DISCORD ERROR]", e, flush=True)
        return False

# ---------------- AI ----------------

def parse_ai_json(text):
    text = str(text).strip()
    if text.startswith("```"):
        lines = text.splitlines()[1:]
        if lines and lines[-1].strip() == "```": lines = lines[:-1]
        text = "\n".join(lines).strip()
        if text.lower().startswith("json"): text = text[4:].strip()
    a = text.find("{"); b = text.rfind("}")
    if a >= 0 and b > a: text = text[a:b + 1]
    return json.loads(text)

def gemini_call(role, data):
    if not GEMINI_API_KEY:
        return False, "NO_KEY", 0.0, "GEMINI_API_KEY is not set"
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
    prompt = {
        "role": role, "market_data": data,
        "output": {"decision": "APPROVE or REJECT", "confidence": "0-100", "reason": "short"},
        "rules": ["Use only supplied data.", "Do not invent candles.", "Do not claim certainty.", "Return JSON only."]
    }
    body = {"contents": [{"parts": [{"text": json.dumps(prompt, ensure_ascii=False)}]}],
            "generationConfig": {"temperature": 0.1, "responseMimeType": "application/json"}}
    try:
        req = urllib.request.Request(url, data=json.dumps(body, ensure_ascii=False).encode('utf-8'),
            headers={"Content-Type": "application/json", "User-Agent": "SIGZY-V6"}, method="POST")
        with urllib.request.urlopen(req, timeout=20) as r: raw = json.loads(r.read().decode())
        text = raw["candidates"][0]["content"]["parts"][0]["text"]
        j = parse_ai_json(text)
        d = str(j.get("decision", "REJECT")).upper()
        c = max(0, min(100, float(j.get("confidence", 0))))
        return True, d, c, str(j.get("reason", "No reason"))
    except Exception as e:
        return False, "ERROR", 0.0, str(e)

def candles_for_ai(c):
    return [{"open": x["open"], "high": x["high"], "low": x["low"],
             "close": x["close"], "volume": x["volume"]} for x in c[-12:]]

def ai_final_check(symbol, action, setup, trend, candles, bot_score, pair_hist, setup_hist):
    if not GEMINI_API_KEY:
        # หากไม่มี Gemini Key ให้ใช้ Bot Score เป็นเกณฑ์ผ่านโดยอัตโนมัติ
        return {"approved": True, "status": "APPROVED", "final_score": bot_score,
                "ai1": {"decision": "BYPASS", "confidence": 100, "reason": "No AI Key (Passed by Bot)"},
                "ai2": {"decision": "BYPASS", "confidence": 100, "reason": "No AI Key (Passed by Bot)"}}

    base = {
        "symbol": symbol, "proposed_action": action, "setup": setup, "trend": trend,
        "bot_score": bot_score, "pair_history": pair_hist,
        "setup_history": setup_hist, "candles": candles_for_ai(candles)}
    
    ok1, d1, c1, r1 = gemini_call(
        "AI #1 Pattern Analyst. Inspect candle structure, wicks, bodies, "
        "momentum, support/resistance and trend alignment. Confirm only when evidence supports the proposed direction.",
        base)
    print(f"🤖 AI1 {symbol}: {d1} {c1:.1f}% | {r1}", flush=True)
    
    if not ok1:
        return {"approved": False, "status": "AI1_ERROR", "final_score": 0,
                "ai1": {"decision": d1, "confidence": c1, "reason": r1},
                "ai2": {"decision": "NOT_RUN", "confidence": 0, "reason": "AI1 failed"}}
    
    data = dict(base)
    data["ai1"] = {"decision": d1, "confidence": c1, "reason": r1}
    
    ok2, d2, c2, r2 = gemini_call(
        "AI #2 Risk/Contrarian Analyst. Try to prove the proposed trade wrong. "
        "Look for trend conflict, fake reversal, late entry, weak rejection, abnormal momentum or other hidden risk. "
        "Do not approve merely because AI1 approved.",
        data)
    print(f"🛡️ AI2 {symbol}: {d2} {c2:.1f}% | {r2}", flush=True)
    
    if not ok2:
        return {"approved": False, "status": "AI2_ERROR", "final_score": 0,
                "ai1": {"decision": d1, "confidence": c1, "reason": r1},
                "ai2": {"decision": d2, "confidence": c2, "reason": r2}}
    
    final = bot_score * .50 + c1 * .25 + c2 * .25
    if STRICT_AI_CONSENSUS:
        approved = (d1 == "APPROVE" and d2 == "APPROVE" and
                    c1 >= AI_CONFIDENCE_THRESHOLD and c2 >= AI_CONFIDENCE_THRESHOLD and
                    final >= FINAL_SCORE_THRESHOLD)
    else:
        approved = (final >= FINAL_SCORE_THRESHOLD and
                    ((d1 == "APPROVE" and c1 >= AI_CONFIDENCE_THRESHOLD) or
                     (d2 == "APPROVE" and c2 >= AI_CONFIDENCE_THRESHOLD)))
    
    return {"approved": approved, "status": "APPROVED" if approved else "REJECTED",
            "final_score": round(final, 2),
            "ai1": {"decision": d1, "confidence": c1, "reason": r1},
            "ai2": {"decision": d2, "confidence": c2, "reason": r2}}

# ---------------- DATA ----------------

def fetch(symbol, limit=200):
    try:
        if symbol in CRYPTO:
            url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval=1m&limit={limit}"
        else:
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{YAHOO[symbol]}?interval=1m&range=1d"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as r: data = json.loads(r.read().decode())
        if symbol in CRYPTO:
            return [{"timestamp": int(x[0]), "open": float(x[1]), "high": float(x[2]),
                     "low": float(x[3]), "close": float(x[4]), "volume": float(x[5])} for x in data]
        res = data.get("chart", {}).get("result")
        if not res: return None
        res = res[0]; ts = res.get("timestamp", []); q = res.get("indicators", {}).get("quote", [{}])[0]; out = []
        for i, t in enumerate(ts):
            try:
                o, h, l, c = q["open"][i], q["high"][i], q["low"][i], q["close"][i]
                if None in (o, h, l, c): continue
                v = (q.get("volume", [0] * len(ts))[i] or 0)
                out.append({"timestamp": int(t) * 1000, "open": float(o), "high": float(h),
                            "low": float(l), "close": float(c), "volume": float(v)})
            except Exception: pass
        return out[-limit:]
    except Exception as e:
        print(f"[DATA ERROR] {symbol}: {e}", flush=True); return None

# ---------------- MEMORY ----------------

def default_setup(symbol="", action="", setup=""):
    return {"symbol": symbol, "action": action, "setup": setup, "total": 0, "wins": 0, "losses": 0, "draws": 0, "invalid": 0,
            "opportunity_1": {"wins": 0, "losses": 0, "draws": 0},
            "opportunity_2": {"wins": 0, "losses": 0, "draws": 0},
            "opportunity_3": {"wins": 0, "losses": 0, "draws": 0},
            "ai": {"approved": 0, "rejected": 0, "errors": 0}}

def default_memory():
    return {"version": "SIGZY_V6",
            "stats": {"total_setups": 0, "wins": 0, "losses": 0, "draws": 0, "invalid": 0},
            "score_bands": {
                "PREMIUM_95_100": {"signals": 0, "wins": 0, "losses": 0, "draws": 0},
                "HIGH_QUALITY_88_94": {"signals": 0, "wins": 0, "losses": 0, "draws": 0},
                "WATCH_82_87": {"signals": 0, "wins": 0, "losses": 0, "draws": 0}},
            "ai_stats": {"ai1_approvals": 0, "ai1_rejections": 0, "ai2_approvals": 0,
                        "ai2_rejections": 0, "ai_errors": 0, "consensus_approved": 0},
            "setups": {}}

def save(m):
    try:
        tmp = MEMORY_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f: json.dump(m, f, indent=2, ensure_ascii=False)
        os.replace(tmp, MEMORY_FILE)
    except Exception as e: print("[MEMORY SAVE ERROR]", e, flush=True)

def init_memory():
    if os.path.exists(MEMORY_FILE):
        try:
            with open(MEMORY_FILE, encoding="utf-8") as f: m = json.load(f)
            base = default_memory()
            for k, v in base["stats"].items(): m.setdefault("stats", {}).setdefault(k, v)
            for k, v in base["score_bands"].items(): m.setdefault("score_bands", {}).setdefault(k, v.copy())
            for k, v in base["ai_stats"].items(): m.setdefault("ai_stats", {}).setdefault(k, v)
            m.setdefault("setups", {})
            for s in m["setups"].values():
                if not isinstance(s, dict): continue
                for k in ("total", "wins", "losses", "draws", "invalid"): s.setdefault(k, 0)
                for i in (1, 2, 3): s.setdefault(f"opportunity_{i}", {"wins": 0, "losses": 0, "draws": 0})
                s.setdefault("ai", {"approved": 0, "rejected": 0, "errors": 0})
            if "losss" in m["stats"]:
                m["stats"]["losses"] += m["stats"].pop("losss"); save(m)
            print(f"[MEMORY] Loaded: {MEMORY_FILE}", flush=True); return m
        except Exception as e: print("[MEMORY LOAD ERROR]", e, flush=True)
    m = default_memory(); save(m); print(f"[MEMORY] Created: {MEMORY_FILE}", flush=True); return m

def quality(m, symbol, action, setup):
    s = m["setups"].get(f"{symbol}_{action}_{setup}")
    if not s: return {"wr": None, "decided": 0}
    d = int(s.get("wins", 0)) + int(s.get("losses", 0))
    return {"wr": s["wins"] / d * 100 if d else None, "decided": d}

def pair_stats(m, symbol):
    w = l = d = t = 0
    for k, s in m.get("setups", {}).items():
        if isinstance(s, dict) and (s.get("symbol") == symbol or k.startswith(symbol + "_")):
            w += int(s.get("wins", 0)); l += int(s.get("losses", 0)); d += int(s.get("draws", 0)); t += int(s.get("total", 0))
    decided = w + l
    return {"wins": w, "losses": l, "draws": d, "total": t, "decided": decided, "wr": w / decided * 100 if decided else None}

def pair_status(s):
    if s["decided"] < PAIR_MIN_HISTORY: return "LEARNING"
    if s["wr"] < PAIR_BLOCK_WR: return "BLOCK"
    if s["wr"] >= PAIR_STRONG_WR: return "STRONG"
    if s["wr"] >= PAIR_FAVOR_WR: return "FAVOR"
    if s["wr"] >= PAIR_WEAK_WR: return "NORMAL"
    return "WEAK"

def ema(values, n):
    n = min(n, len(values)); e = values[0]; k = 2 / (n + 1)
    for p in values[1:]: e = p * k + e * (1 - k)
    return e

def band(score):
    if score >= 95: return "PREMIUM_95_100"
    if score >= 88: return "HIGH_QUALITY_88_94"
    if score >= 82: return "WATCH_82_87"
    return "BELOW_WATCH"

def update_band(m, score, result=None):
    b = m["score_bands"].get(band(score))
    if not b: return
    if result is None: b["signals"] += 1
    elif result in ("WIN", "LOSS", "DRAW"): b[result.lower() + "s"] += 1

def historical_bonus(m, symbol, action, setup):
    q = quality(m, symbol, action, setup)
    if q["decided"] < SETUP_MIN_HISTORY or q["wr"] is None: return 0.0, "LEARNING"
    if q["wr"] >= 75: return 10.0, f"REAL_WR_{q['wr']:.1f}"
    if q["wr"] >= 70: return 6.0, f"REAL_WR_{q['wr']:.1f}"
    if q["wr"] >= 65: return 2.0, f"REAL_WR_{q['wr']:.1f}"
    if q["wr"] < 55: return -12.0, f"REAL_WR_{q['wr']:.1f}"
    return 0.0, f"REAL_WR_{q['wr']:.1f}"

# ---------------- BOT ----------------

def analyze(c, m, symbol):
    if not c or len(c) < 150: return {"action": "WAIT", "score": 0, "setup": "NEUTRAL", "trend": "N/A", "real_wr": None}
    x = c[:-1]; cur, pr, pr2 = x[-1], x[-2], x[-3]; cl = [z["close"] for z in x]
    e20, e50, e100 = ema(cl, 20), ema(cl, 50), ema(cl, 100)
    trend = ("BULL_STRONG" if cur["close"] > e20 > e50 > e100 else
             "BEAR_STRONG" if cur["close"] < e20 < e50 < e100 else
             "BULL" if cur["close"] > e50 else "BEAR" if cur["close"] < e50 else "RANGE")
    z = x[-101:-1]; sup = min(q["low"] for q in z); res = max(q["high"] for q in z); p = cur["close"]; t = TUNING.get(symbol, TUNING["DEFAULT"])
    ns = abs(p - sup) / max(abs(sup), 1e-12) <= t["sr"]; nr = abs(p - res) / max(abs(res), 1e-12) <= t["sr"]
    ins = abs(p - sup) / max(abs(sup), 1e-12) <= t["zone"]; inr = abs(p - res) / max(abs(res), 1e-12) <= t["zone"]
    vv = [q["volume"] for q in x[-16:-1] if q["volume"] > 0]; avg = sum(vv) / len(vv) if vv else 0
    vr = cur["volume"] / avg if avg and cur["volume"] else 1; hv = vr >= t["vol"]
    body = abs(cur["close"] - cur["open"]); rng = max(cur["high"] - cur["low"], 1e-12)
    up = cur["high"] - max(cur["open"], cur["close"]); lo = min(cur["open"], cur["close"]) - cur["low"]
    bull = cur["close"] > cur["open"]; bear = cur["close"] < cur["open"]
    bp = bull and lo >= max(body * 1.8, rng * .3); sp = bear and up >= max(body * 1.8, rng * .3)
    be = pr["close"] < pr["open"] and bull and cur["open"] <= pr["close"] and cur["close"] >= pr["open"]
    se = pr["close"] > pr["open"] and bear and cur["open"] >= pr["close"] and cur["close"] <= pr["open"]
    bf = bull and (pr["close"] > pr["open"] or pr2["close"] > pr2["open"] or be or bp)
    sf = bear and (pr["close"] < pr["open"] or pr2["close"] < pr2["open"] or se or sp)
    cs = (20 if trend == "BULL_STRONG" else 15 if trend == "BULL" else 5 if trend == "RANGE" else 0)
    ps = (20 if trend == "BEAR_STRONG" else 15 if trend == "BEAR" else 5 if trend == "RANGE" else 0)
    cs += 25 if ns else 10 if ins else 0; ps += 25 if nr else 10 if inr else 0
    cs += 20 if bp or be else 7 if bull else 0; ps += 20 if sp or se else 7 if bear else 0
    cs += 15 if hv and bull else 4 if bull else 0; ps += 15 if hv and bear else 4 if bear else 0
    cs += 10 if bf else 0; ps += 10 if sf else 0
    call_setup = "SUPPORT_REVERSAL_CALL" if ns else "MOMENTUM_CALL"
    put_setup = "RESISTANCE_REVERSAL_PUT" if nr else "MOMENTUM_PUT"
    cm = quality(m, symbol, "CALL", call_setup); pm = quality(m, symbol, "PUT", put_setup)
    cb, ct = historical_bonus(m, symbol, "CALL", call_setup); pb, pt = historical_bonus(m, symbol, "PUT", put_setup)
    cs += cb; ps += pb
    cv = ((ns and bull and (bp or be) and bf and trend != "BEAR_STRONG") or
          (trend in ("BULL", "BULL_STRONG") and bull and bf and hv and p > e20 and not nr))
    pv = ((nr and bear and (sp or se) and sf and trend != "BULL_STRONG") or
          (trend in ("BEAR", "BEAR_STRONG") and bear and sf and hv and p < e20 and not ns))
    if cm["decided"] >= SETUP_MIN_HISTORY and cm["wr"] < 55: cv = False
    if pm["decided"] >= SETUP_MIN_HISTORY and pm["wr"] < 55: pv = False
    if ns and bull and (bp or be): cs += 10
    if nr and bear and (sp or se): ps += 10
    cs = max(0, min(100, cs)); ps = max(0, min(100, ps))
    action = "WAIT"; score = 0; setup = "NEUTRAL"; wr = None; tag = ""
    if cv and cs >= MIN_SCORE_THRESHOLD and cs > ps: action, score, setup, wr, tag = "CALL", cs, call_setup, cm["wr"], ct
    elif pv and ps >= MIN_SCORE_THRESHOLD and ps > cs: action, score, setup, wr, tag = "PUT", ps, put_setup, pm["wr"], pt
    return {"action": action, "score": round(score, 2), "setup": setup, "real_wr": wr,
            "call_score": round(cs, 2), "put_score": round(ps, 2), "trend": trend,
            "vol_ratio": round(vr, 2), "memory_tag": tag, "call_history": cm, "put_history": pm}

def rank_candidates(candidates, m):
    def key(x):
        a = x["analysis"]; s = x["symbol"]; ps = pair_stats(m, s); q = quality(m, s, a["action"], a["setup"])
        pwr = ps["wr"] if ps["decided"] >= PAIR_MIN_HISTORY and ps["wr"] is not None else 50
        swr = q["wr"] if q["decided"] >= SETUP_MIN_HISTORY and q["wr"] is not None else 50
        return (a["score"], pwr, swr, min(ps["decided"], 50), min(q["decided"], 50))
    return sorted(candidates, key=key, reverse=True)

# ---------------- RESULT ----------------

def record(m, tr, result):
    st = m["stats"]; st["total_setups"] += 1
    if result == "WIN": st["wins"] += 1
    elif result == "LOSS": st["losses"] += 1
    elif result == "DRAW": st["draws"] += 1
    key = f'{tr["symbol"]}_{tr["action"]}_{tr["setup"]}'
    s = m["setups"].setdefault(key, default_setup(tr["symbol"], tr["action"], tr["setup"]))
    s["total"] += 1
    if result in ("WIN", "LOSS", "DRAW"): s[result.lower() + "s"] += 1
    for i in (1, 2, 3):
        r = tr.get("opportunity_results", {}).get(str(i))
        if r in ("WIN", "LOSS", "DRAW"): s[f"opportunity_{i}"][r.lower() + "s"] += 1
    ai = tr.get("ai", {}); as_ = m["ai_stats"]
    if ai.get("ai1_decision") == "APPROVE": as_["ai1_approvals"] += 1
    elif ai.get("ai1_decision") == "REJECT": as_["ai1_rejections"] += 1
    if ai.get("ai2_decision") == "APPROVE": as_["ai2_approvals"] += 1
    elif ai.get("ai2_decision") == "REJECT": as_["ai2_rejections"] += 1
    if ai.get("ai1_decision") == "APPROVE" and ai.get("ai2_decision") == "APPROVE": as_["consensus_approved"] += 1
    update_band(m, tr.get("entry_score", 0), result); save(m)
    d = s["wins"] + s["losses"]; wr = s["wins"] / d * 100 if d else 0
    msg = (f"📊 PAPER RESULT V6.0\nตลาด: {tr['symbol']}\nผลล่าสุด: {result}\n"
           f"Action: {tr['action']}\nSetup: {tr['setup']}\nBot Score: {tr['entry_score']:.1f}\n"
           f"Final AI Score: {tr.get('final_ai_score',0):.1f}\n"
           f"AI1: {ai.get('ai1_decision')} {ai.get('ai1_confidence',0):.1f}%\n"
           f"AI2: {ai.get('ai2_decision')} {ai.get('ai2_confidence',0):.1f}%\n"
           f"ครบ Opportunity: 3/3\nSetup WR: {wr:.1f}%\n"
           f"ระบบรวม: ชนะ {st['wins']} | เสมอ {st['draws']} | แพ้ {st['losses']}\n🧠 Memory updated: {key}")
    print(msg, flush=True); discord(msg)

# ---------------- MAIN ----------------

def run():
    print("=" * 72); print("🤖 SIGZY BRAIN V6.0"); print("🧠 BOT + 🤖 AI1 PATTERN + 🛡️ AI2 RISK")
    print("🏆 BEST PAIR SELECTOR + 📚 REAL MEMORY"); print("=" * 72)
    print(f"AI Model: {GEMINI_MODEL} | Threshold: {AI_CONFIDENCE_THRESHOLD} | Strict: {STRICT_AI_CONSENSUS}", flush=True)
    
    if DISCORD_WEBHOOK_URL:
        print("✅ Discord webhook loaded.", flush=True)
        discord("🟢 SIGZY BRAIN V6.0 ONLINE\n🧠 Bot + 🤖 AI1 Pattern + 🛡️ AI2 Risk\n🏆 Best Pair Selector + 📚 Real Memory")
    else:
        print("⚠️ DISCORD_WEBHOOK_URL is not set.", flush=True)
        
    if not GEMINI_API_KEY:
        print("⚠️ GEMINI_API_KEY missing: AI mode bypassed to Bot Decision Only.", flush=True)
        
    m = init_memory(); active = {}; last_alert = {}; last_signal = datetime.now()

    while True:
        now = datetime.now(); print("\n" + "=" * 72); print("🧠 SCAN", now.strftime("%Y-%m-%d %H:%M:%S"), flush=True)

        done = []
        for symbol, tr in list(active.items()):
            c = fetch(symbol, 200)
            if not c or len(c) < 3: continue
            cur = c[-2]
            if cur["timestamp"] == tr.get("last_ts"): continue
            tr["last_ts"] = cur["timestamp"]; tr["opportunity"] += 1; opp = tr["opportunity"]
            green = cur["close"] > cur["open"]; red = cur["close"] < cur["open"]
            result = ("WIN" if green else "LOSS" if red else "DRAW") if tr["action"] == "CALL" else ("WIN" if red else "LOSS" if green else "DRAW")
            tr["opportunity_results"][str(opp)] = result
            print(f"📍 {symbol} | OPP {opp}/3 | {tr['action']} | {result}", flush=True)
            if opp >= 3: record(m, tr, tr["opportunity_results"]["3"]); done.append(symbol)
        for s in done: active.pop(s, None)

        candidates = []
        for symbol in SYMBOLS[:MAX_SCAN_PAIRS]:
            ps = pair_stats(m, symbol); status = pair_status(ps)
            if status == "BLOCK" or symbol in active: continue
            if symbol in last_alert and (now - last_alert[symbol]).total_seconds() < SYMBOL_COOLDOWN_SECONDS: continue
            c = fetch(symbol, 200)
            if not c or len(c) < 150: continue
            a = analyze(c, m, symbol)
            if a["action"] != "WAIT" and a["score"] >= MIN_SCORE_THRESHOLD:
                closed = c[:-1]
                candidates.append({"symbol": symbol, "analysis": a, "price": closed[-1]["close"],
                                   "timestamp": closed[-1]["timestamp"], "candles": c})
                print(f"🎯 CANDIDATE {symbol} {a['action']} {a['score']:.1f} {a['setup']} | Pair={status} | PairWR={ps['wr'] if ps['wr'] is not None else 'N/A'}", flush=True)

        ranked = rank_candidates(candidates, m)
        approved = []
        for cand in ranked[:5]:
            a = cand["analysis"]; symbol = cand["symbol"]; ps = pair_stats(m, symbol)
            pair_hist = {"wins": ps["wins"], "losses": ps["losses"], "draws": ps["draws"],
                         "decided": ps["decided"], "wr": ps["wr"], "status": pair_status(ps)}
            setup_hist = quality(m, symbol, a["action"], a["setup"])
            ai = ai_final_check(symbol, a["action"], a["setup"], a["trend"], cand["candles"], a["score"], pair_hist, setup_hist)
            cand["ai"] = ai
            if ai["status"] in ("AI1_ERROR", "AI2_ERROR"):
                m["ai_stats"]["ai_errors"] += 1; save(m); continue
            if ai["approved"]: approved.append(cand); print(f"✅ CONSENSUS {symbol} Final={ai['final_score']:.1f}", flush=True)
            else: print(f"⛔ REJECTED {symbol} Final={ai['final_score']:.1f}", flush=True)

        approved.sort(key=lambda x: (x["ai"]["final_score"], x["analysis"]["score"]), reverse=True)
        selected = approved[:MAX_NEW_ALERTS_PER_SCAN]

        for cand in selected:
            a = cand["analysis"]; symbol = cand["symbol"]; ai = cand["ai"]; last_signal = now
            tr = {"symbol": symbol, "action": a["action"], "price": cand["price"], "entry_timestamp": cand["timestamp"],
                "entry_score": a["score"], "final_ai_score": ai["final_score"], "setup": a["setup"],
                "opportunity": 0, "last_ts": cand["timestamp"], "opportunity_results": {},
                "ai": {"ai1_decision": ai["ai1"]["decision"], "ai1_confidence": ai["ai1"]["confidence"],
                       "ai1_reason": ai["ai1"]["reason"], "ai2_decision": ai["ai2"]["decision"],
                       "ai2_confidence": ai["ai2"]["confidence"], "ai2_reason": ai["ai2"]["reason"]}}
            active[symbol] = tr; last_alert[symbol] = now; update_band(m, a["score"]); save(m)
            ps = pair_stats(m, symbol); wr = f"{a['real_wr']:.1f}%" if a["real_wr"] is not None else "กำลังเก็บสถิติ"
            pwr = f"{ps['wr']:.1f}% ({ps['decided']} decided)" if ps["wr"] is not None else "กำลังเรียนรู้"
            msg = (f"🚨 SIGZY BRAIN V6.0\n🏆 BEST PAIR OF THIS SCAN\n\nตลาด: {symbol}\n"
                 f"เวลา: {now.strftime('%H:%M:%S')}\nราคา: {cand['price']}\n"
                 f"ทิศทาง: {'CALL 🟢' if a['action']=='CALL' else 'PUT 🔴'}\n\n"
                 f"🧠 BOT SCORE: {a['score']:.1f}/100\n"
                 f"🤖 AI #1 Pattern: {ai['ai1']['decision']} {ai['ai1']['confidence']:.1f}%\n"
                 f"🛡️ AI #2 Risk: {ai['ai2']['decision']} {ai['ai2']['confidence']:.1f}%\n"
                 f"🏆 FINAL SCORE: {ai['final_score']:.1f}/100\n\n"
                 f"Setup: {a['setup']}\nTrend: {a['trend']}\nHistorical Setup WR: {wr}\nPair WR: {pwr}\n"
                 f"Memory: {a.get('memory_tag','LEARNING')}\n\n"
                 f"🤖 AI1: {ai['ai1']['reason']}\n🛡️ AI2: {ai['ai2']['reason']}\n\n"
                 f"📚 Paper Track: Opportunity 1 ➔ 2 ➔ 3\n🧠 Real Memory Learning: ON\n"
                 f"⚠️ Paper Trade — ไม่ใช่คำสั่งซื้อขายอัตโนมัติ")
            print("\n" + msg, flush=True); discord(msg)

        st = m["stats"]; d = st["wins"] + st["losses"]; wr = st["wins"] / d * 100 if d else 0; ai = m["ai_stats"]
        print(f"📊 MEMORY: {st['total_setups']} setups | WIN={st['wins']} LOSS={st['losses']} DRAW={st['draws']} | WR={wr:.2f}% | Active={len(active)}", flush=True)
        print(f"🤖 AI: A1 {ai['ai1_approvals']}A/{ai['ai1_rejections']}R | A2 {ai['ai2_approvals']}A/{ai['ai2_rejections']}R | Consensus={ai['consensus_approved']}", flush=True)
        print(f"⏳ Wait {PAPER_INTERVAL_SECONDS}s...", flush=True)
        time.sleep(PAPER_INTERVAL_SECONDS)

if __name__ == "__main__":
    try: run()
    except KeyboardInterrupt: print("🛑 SIGZY BRAIN V6.0 stopped.")
    except Exception as e: print("[FATAL ERROR]", repr(e), flush=True)
