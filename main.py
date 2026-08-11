# ---------------- AI ----------------

def parse_ai_json(text):
    text = str(text).strip()
    if text.startswith("```"):
        lines = text.splitlines()[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
        if text.lower().startswith("json"):
            text = text[4:].strip()
    a = text.find("{")
    b = text.rfind("}")
    if a >= 0 and b > a:
        text = text[a:b + 1]
    return json.loads(text)

def gemini_call(role, data):
    if not GEMINI_API_KEY:
        return False, "NO_KEY", 0.0, "GEMINI_API_KEY is not set"

    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
    )

    prompt = {
        "role": role,
        "market_data": data,
        "output": {
            "decision": "APPROVE or REJECT",
            "confidence": "0-100",
            "reason": "short"
        },
        "rules": [
            "Use only supplied data.",
            "Do not invent candles.",
            "Do not claim certainty.",
            "Be extremely strict. Reject if there is any doubt or counter-trend risk.",
            "Return JSON only."
        ]
    }

    body = {
        "contents": [{"parts": [{"text": json.dumps(prompt, ensure_ascii=False)}]}],
        "generationConfig": {
            "temperature": 0.1,
            "responseMimeType": "application/json"
        }
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
        return True, d, c, str(j.get("reason", "No reason"))

    except Exception as e:
        return False, "ERROR", 0.0, str(e)
