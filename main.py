from pathlib import Path

code = '''# SIGZY + TRADEIFY v4 A++ MTF SNIPER
# 15M Master | 5M Confirmation | 1M Entry
# IMPORTANT: No fake/random OTC data. Real OTC requires OTC_API_URL.
# Tracking only: this bot does NOT place orders and cannot guarantee wins.

import os, json, time, urllib.request, urllib.parse
from datetime import datetime, timedelta, timezone
from statistics import mean

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
MARKET_MODE = os.getenv("MARKET_MODE", "AUTO").upper()
OTC_API_URL = os.getenv("OTC_API_URL", "").strip()
SCAN_SECONDS = max(5, int(os.getenv("SCAN_SECONDS", "10")))
SYMBOLS = [x.strip() for x in os.getenv(
    "SYMBOLS", "EUR/USD,GBP/USD,USD/JPY,EUR/JPY"
).split(",") if x.strip()]

YAHOO_MAP = {
    "EUR/USD":"EURUSD=X", "GBP/USD":"GBPUSD=X", "USD/JPY":"JPY=X",
    "EUR/JPY":"EURJPY=X", "AUD/USD":"AUDUSD=X", "USD/CHF":"CHF=X",
    "USD/CAD":"CAD=X", "GBP/JPY":"GBPJPY=X"
}

MIN_SCORE = 78
MIN_EDGE = 12
SR_LOOKBACK = 120
MIN_CANDLES = 120
EXPIRY_MINUTES = 1
STAKE_BY_STEP = {1:100, 2:200, 3:300}

TZ = timezone(timedelta(hours=7))
CURRENT_DAY = None
CURRENT_STEP = 1
SET_ACTIVE = False
SET_NUMBER = 0
PENDING = {}
LAST_CANDLE = {}
LAST_SIGNAL = {}
DAILY = {"signals":0, "wins":0, "losses":0, "void":0}
STATS = {1:{"WIN":0,"LOSS":0,"VOID":0},2:{"WIN":0,"LOSS":0,"VOID":0},3:{"WIN":0,"LOSS":0,"VOID":0}}

def now(): return datetime.now(timezone.utc).astimezone(TZ)
def ts(): return int(time.time())

def http_json(url, timeout=10):
    req = urllib.request.Request(url, headers={"User-Agent":"TRADEIFY-v4","Accept":"application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())

def discord(msg):
    if not DISCORD_WEBHOOK_URL:
        print(msg); return
    try:
        data=json.dumps({"content":msg}).encode()
        req=urllib.request.Request(DISCORD_WEBHOOK_URL,data=data,
            headers={"Content-Type":"application/json"},method="POST")
        urllib.request.urlopen(req,timeout=10).close()
    except Exception as e:
        print("[DISCORD]",e)

def normalize(x):
    if isinstance(x,dict):
        t=x.get("timestamp",x.get("time",x.get("t")))
        o=x.get("open",x.get("o")); h=x.get("high",x.get("h"))
        l=x.get("low",x.get("l")); c=x.get("close",x.get("c"))
    elif isinstance(x,(list,tuple)) and len(x)>=5:
        t,o,h,l,c=x[:5]
    else: return None
    try:
        t=float(t); t=t/1000 if t>10_000_000_000 else t
        return {"timestamp":int(t),"open":float(o),"high":float(h),
                "low":float(l),"close":float(c)}
    except: return None

def fetch_yahoo(symbol):
    ticker=YAHOO_MAP.get(symbol,symbol)
    url=("https://query1.finance.yahoo.com/v8/finance/chart/"
         +urllib.parse.quote(ticker,safe="")+"?interval=1m&range=1d")
    try:
        r=http_json(url)["chart"]["result"][0]
        q=r["indicators"]["quote"][0]
        out=[]
        for i,t in enumerate(r.get("timestamp",[])):
            try:
                c={"timestamp":int(t),"open":float(q["open"][i]),
                   "high":float(q["high"][i]),"low":float(q["low"][i]),
                   "close":float(q["close"][i])}
                out.append(c)
            except: pass
        cutoff=ts()-60
        return [c for c in out if c["timestamp"]<=cutoff]
    except Exception as e:
        print("[YAHOO]",symbol,e); return []

def fetch_otc(symbol):
    if not OTC_API_URL:
        print("[OTC] OTC_API_URL is not configured"); return []
    try:
        sep="&" if "?" in OTC_API_URL else "?"
        url=OTC_API_URL+sep+urllib.parse.urlencode(
            {"symbol":symbol,"interval":"1m","limit":"500"})
        p=http_json(url)
        raw=p.get("candles",p.get("data",p if isinstance(p,list) else []))
        out=[normalize(x) for x in raw]
        out=[x for x in out if x]
        out.sort(key=lambda x:x["timestamp"])
        return [c for c in out if c["timestamp"]<=ts()-60]
    except Exception as e:
        print("[OTC]",symbol,e); return []

def fetch(symbol):
    if MARKET_MODE=="OTC": return fetch_otc(symbol)
    if MARKET_MODE=="LIVE": return fetch_yahoo(symbol)
    if OTC_API_URL:
        x=fetch_otc(symbol)
        if x: return x
    return fetch_yahoo(symbol)

def resample(candles,minutes):
    sec=minutes*60; b={}
    for c in candles: b.setdefault(c["timestamp"]//sec,[]).append(c)
    out=[]
    for g in b.values():
        g=sorted(g,key=lambda x:x["timestamp"])
        if len(g)<minutes: continue
        out.append({"timestamp":g[-1]["timestamp"],"open":g[0]["open"],
                    "high":max(x["high"] for x in g),
                    "low":min(x["low"] for x in g),"close":g[-1]["close"]})
    return sorted(out,key=lambda x:x["timestamp"])

def ema(v,n):
    if len(v)<n:return None
    e=mean(v[:n]); k=2/(n+1)
    for x in v[n:]: e=x*k+e*(1-k)
    return e

def rsi(v,n=14):
    if len(v)<n+1:return None
    g=[]; l=[]
    for i in range(1,len(v)):
        d=v[i]-v[i-1]; g.append(max(d,0)); l.append(max(-d,0))
    ag=mean(g[:n]); al=mean(l[:n])
    for i in range(n,len(g)):
        ag=((n-1)*ag+g[i])/n; al=((n-1)*al+l[i])/n
    return 100 if al==0 else 100-100/(1+ag/al)

def atr(c,n=14):
    if len(c)<n+1:return None
    x=[]
    for i in range(1,len(c)):
        a=c[i];p=c[i-1]
        x.append(max(a["high"]-a["low"],abs(a["high"]-p["close"]),
                     abs(a["low"]-p["close"])))
    return mean(x[-n:])

def structure(c,n=20):
    if len(c)<n:return ("RANGE",0)
    x=c[-n:]; h=len(x)//2
    a=x[:h];b=x[h:]
    ah=mean(z["high"] for z in a); bh=mean(z["high"] for z in b)
    al=mean(z["low"] for z in a); bl=mean(z["low"] for z in b)
    ac=mean(z["close"] for z in a); bc=mean(z["close"] for z in b)
    rng=mean(z["high"]-z["low"] for z in x)
    if bh>ah and bl>al and bc>ac:return ("CALL",min(1,abs(bc-ac)/max(rng,1e-12)))
    if bh<ah and bl<al and bc<ac:return ("PUT",min(1,abs(bc-ac)/max(rng,1e-12)))
    return ("RANGE",0)

def candle(c):
    r=max(c["high"]-c["low"],1e-12)
    return {"bull":c["close"]>c["open"],"bear":c["close"]<c["open"],
            "body":abs(c["close"]-c["open"])/r,
            "upper":(c["high"]-max(c["open"],c["close"]))/r,
            "lower":(min(c["open"],c["close"])-c["low"])/r}

def analyze(symbol,c1):
    if len(c1)<MIN_CANDLES:return None
    c5=resample(c1,5); c15=resample(c1,15)
    if len(c5)<30 or len(c15)<20:return None
    p1=[x["close"] for x in c1];p5=[x["close"] for x in c5];p15=[x["close"] for x in c15]
    e9,e21,e50=ema(p1,9),ema(p1,21),ema(p1,50)
    e59,e521=ema(p5,9),ema(p5,21);e159,e1521=ema(p15,9),ema(p15,21)
    r1,r5,r15=rsi(p1),rsi(p5),rsi(p15)
    a=atr(c1)
    if None in (e9,e21,e50,e59,e521,e159,e1521,r1,r5,r15,a):return None
    s15,_=structure(c15);s5,_=structure(c5)
    last=c1[-1]; ci=candle(last)
    look=c1[-SR_LOOKBACK:]; sup=min(x["low"] for x in look);res=max(x["high"] for x in look)
    pos=(last["close"]-sup)/max(res-sup,1e-12)
    zone="SUPPORT" if pos<=.20 else "RESISTANCE" if pos>=.80 else "MID"
    f=sum(1 for x in c1[-4:] if x["close"]>x["open"])
    g=sum(1 for x in c1[-4:] if x["close"]<x["open"])
    flow="CALL" if f>=3 else "PUT" if g>=3 else "RANGE"
    sc={"CALL":0,"PUT":0};why={"CALL":[],"PUT":[]}
    def add(d,n,t): sc[d]+=n;why[d].append(t)
    if s15=="CALL":add("CALL",28,"15M structure up")
    if s15=="PUT":add("PUT",28,"15M structure down")
    if e159>e1521:add("CALL",12,"15M EMA alignment")
    if e159<e1521:add("PUT",12,"15M EMA alignment")
    if 52<=r15<=68:add("CALL",8,"15M RSI regime")
    if 32<=r15<=48:add("PUT",8,"15M RSI regime")
    if s5=="CALL":add("CALL",20,"5M confirmation")
    if s5=="PUT":add("PUT",20,"5M confirmation")
    if e59>e521:add("CALL",10,"5M EMA alignment")
    if e59<e521:add("PUT",10,"5M EMA alignment")
    if flow=="CALL":add("CALL",8,"1M flow")
    if flow=="PUT":add("PUT",8,"1M flow")
    if ci["bull"] and ci["body"]>=.45:add("CALL",7,"1M bullish candle")
    if ci["bear"] and ci["body"]>=.45:add("PUT",7,"1M bearish candle")
    if ci["lower"]>=.25:add("CALL",8,"lower rejection")
    if ci["upper"]>=.25:add("PUT",8,"upper rejection")
    if zone=="SUPPORT":add("CALL",10,"major support")
    if zone=="RESISTANCE":add("PUT",10,"major resistance")
    if r1>72:sc["CALL"]-=10
    if r1<28:sc["PUT"]-=10
    sc["CALL"]=max(0,min(100,int(sc["CALL"])));sc["PUT"]=max(0,min(100,int(sc["PUT"])))
    d="CALL" if sc["CALL"]>sc["PUT"] else "PUT"; edge=sc[d]-sc["PUT" if d=="CALL" else "CALL"]
    ema_ok=(e9>e21>e50 and d=="CALL") or (e9<e21<e50 and d=="PUT")
    ok=(sc[d]>=MIN_SCORE and edge>=MIN_EDGE and s15==d and s5==d and ema_ok)
    if d=="CALL" and not(ci["bull"] or ci["lower"]>=.30):ok=False
    if d=="PUT" and not(ci["bear"] or ci["upper"]>=.30):ok=False
    return {"symbol":symbol,"decision":d if ok else None,"early":d if
            sc[d]>=MIN_SCORE-6 and edge>=MIN_EDGE-2 and s15==d and s5==d and ema_ok else None,
            "score":sc[d],"edge":edge,"entry":last["close"],"candle_ts":last["timestamp"],
            "s15":s15,"s5":s5,"zone":zone,"r1":r1,"r5":r5,"r15":r15,
            "why":why[d]}

def reset():
    global CURRENT_DAY,CURRENT_STEP,SET_ACTIVE,SET_NUMBER
    d=now().strftime("%Y-%m-%d")
    if d==CURRENT_DAY:return
    CURRENT_DAY=d;CURRENT_STEP=1;SET_ACTIVE=False;SET_NUMBER=0;PENDING.clear()
    LAST_CANDLE.clear();LAST_SIGNAL.clear()
    for x in DAILY:DAILY[x]=0
    for s in STATS.values():
        for x in s:s[x]=0

def result_check():
    global CURRENT_STEP,SET_ACTIVE
    for k,t in list(PENDING.items()):
        if ts()<t["expiry"]+5:continue
        c=fetch(t["symbol"]); q=[x for x in c if x["timestamp"]>=t["expiry"]]
        if not q:continue
        price=q[0]["close"]; step=t["step"]
        result="VOID" if price==t["entry"] else (
            "WIN" if (t["direction"]=="CALL" and price>t["entry"]) or
            (t["direction"]=="PUT" and price<t["entry"]) else "LOSS")
        STATS[step][result]+=1
        if result=="WIN":DAILY["wins"]+=1;SET_ACTIVE=False;CURRENT_STEP=1
        elif result=="LOSS":
            DAILY["losses"]+=1
            if step<3:CURRENT_STEP=step+1
            else:SET_ACTIVE=False;CURRENT_STEP=1
        else:DAILY["void"]+=1
        discord(f"📊 RESULT {t['symbol']} {t['direction']} | "
                f"STEP {step} {t['stake']}฿ | {result} | "
                f"{t['entry']:.6f} → {price:.6f} | "
                f"วันนี้ W{DAILY['wins']} L{DAILY['losses']} V{DAILY['void']}")
        del PENDING[k]

def scan(symbol):
    c=fetch(symbol)
    if len(c)<MIN_CANDLES:return
    lt=c[-1]["timestamp"]
    if LAST_CANDLE.get(symbol)==lt:return
    LAST_CANDLE[symbol]=lt
    a=analyze(symbol,c)
    if not a:return
    if a["early"]:
        ek=f"E:{symbol}:{lt}:{a['early']}"
        if LAST_SIGNAL.get(symbol)!=ek:
            discord(f"🟡 EARLY {symbol} → **{a['early']}** | "
                    f"Score {a['score']} Edge +{a['edge']} | "
                    f"15M {a['s15']} / 5M {a['s5']} / {a['zone']} | "
                    f"รอ 1M confirmation")
            LAST_SIGNAL[symbol]=ek
    if not a["decision"]:return
    if any(x["symbol"]==symbol for x in PENDING.values()):return
    global CURRENT_STEP,SET_ACTIVE,SET_NUMBER
    if not SET_ACTIVE:SET_ACTIVE=True;SET_NUMBER+=1;CURRENT_STEP=1
    k=f"{symbol}:{lt}:{a['decision']}"
    PENDING[k]={"symbol":symbol,"direction":a["decision"],"entry":a["entry"],
                "expiry":a["candle_ts"]+60,"step":CURRENT_STEP,
                "stake":STAKE_BY_STEP[CURRENT_STEP]}
    DAILY["signals"]+=1
    entry=datetime.fromtimestamp(a["candle_ts"],timezone.utc).astimezone(TZ)
    why="; ".join(a["why"][:6])
    discord(f"🎯 **TRADEIFY v4 A++ CONFIRMED**\\n"
            f"{'🟢' if a['decision']=='CALL' else '🔴'} {symbol} → **{a['decision']}**\\n"
            f"Entry {entry:%H:%M:%S} | Expiry +1M\\n"
            f"Score **{a['score']}/100** | Edge **+{a['edge']}**\\n"
            f"15M={a['s15']} | 5M={a['s5']} | Zone={a['zone']}\\n"
            f"RSI 15/5/1 = {a['r15']:.1f}/{a['r5']:.1f}/{a['r1']:.1f}\\n"
            f"STEP {CURRENT_STEP} = {STAKE_BY_STEP[CURRENT_STEP]} บาท\\n"
            f"เหตุผล: {why}\\n"
            f"⚠️ A++ คือการคัดสัญญาณ ไม่ใช่การรับประกันกำไร")

def main():
    reset()
    print("TRADEIFY v4 A++ started:",MARKET_MODE,SYMBOLS)
    if MARKET_MODE=="OTC" and not OTC_API_URL:
        print("WARNING: OTC_API_URL missing. No fake OTC data will be generated.")
    while True:
        try:
            reset();result_check()
            for s in SYMBOLS:scan(s)
            print(f"[{now():%H:%M:%S}] signals={DAILY['signals']} "
                  f"W={DAILY['wins']} L={DAILY['losses']} V={DAILY['void']} "
                  f"step={CURRENT_STEP} pending={len(PENDING)}")
            time.sleep(SCAN_SECONDS)
        except KeyboardInterrupt:break
        except Exception as e:
            print("ERROR",e);time.sleep(5)

if __name__=="__main__":main()
'''

p = Path("/mnt/data/SIGZY_TRADEIFY_v4_A_PLUS_PLUS.py")
t = Path("/mnt/data/SIGZY_TRADEIFY_v4_A_PLUS_PLUS.txt")
p.write_text(code, encoding="utf-8")
t.write_text(code, encoding="utf-8")
print("พร้อมดาวน์โหลด:")
print(p)
print(t)
