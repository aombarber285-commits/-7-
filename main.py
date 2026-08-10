import time, json, os, urllib.request
from datetime import datetime
from pathlib import Path

# ============================================================
# SIGZY BRAIN V4.3 ADAPTIVE ULTRA
# CRYPTO + FOREX | PAPER TRADE | MEMORY | DYNAMIC ROTATION
# ============================================================

CRYPTO = ["BTCUSDT","ETHUSDT","SOLUSDT","XRPUSDT","BNBUSDT"]
FOREX = ["GBPUSD","GBPJPY","USDJPY","EURUSD","EURJPY","AUDJPY","AUDUSD","NZDUSD","USDCAD","USDCHF","EURGBP","NZDJPY","CADJPY"]
SYMBOLS = CRYPTO + FOREX
YAHOO = {s: s + "=X" for s in FOREX}

PAPER_INTERVAL_SECONDS = 60
PREMIUM_SCORE = 95.0
HIGH_QUALITY_SCORE = 88.0
WATCH_SCORE = 82.0
MIN_SCORE_THRESHOLD = 88.0
MAX_NEW_ALERTS_PER_SCAN = 1
MAX_SAME_SETUP_PER_SCAN = 1
SYMBOL_COOLDOWN_SECONDS = 300
MAX_OPPORTUNITIES = 3
MIN_HISTORY_REQUIRED = 10
GROUP_WR_THRESHOLD = 70.0
PAIR_MIN_HISTORY = 10
PAIR_BLOCK_WR = 45.0
PAIR_WEAK_WR = 55.0
PAIR_FAVOR_WR = 65.0
PAIR_STRONG_WR = 70.0
MAX_ACTIVE_SCAN_PAIRS = 10

BASE = Path(__file__).resolve().parent
MEMORY_CANDIDATES = [BASE/"bot_memory_sigzy_brain_v4_crypto_forex.json", BASE/"bot_memory_sigzy_discord.json"]
MEMORY_FILE = str(next((p for p in MEMORY_CANDIDATES if p.exists()), MEMORY_CANDIDATES[0]))

# Railway -> Service -> Variables -> DISCORD_WEBHOOK_URL
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "").strip()

TUNING = {"DEFAULT":{"sr":.008,"zone":.015,"vol":1.40}}
for s in CRYPTO:
    TUNING[s] = {"sr": {"BTCUSDT":.006,"ETHUSDT":.007,"SOLUSDT":.010,"XRPUSDT":.010,"BNBUSDT":.008}[s], "zone": {"BTCUSDT":.012,"ETHUSDT":.013,"SOLUSDT":.018,"XRPUSDT":.018,"BNBUSDT":.015}[s], "vol": {"BTCUSDT":1.35,"ETHUSDT":1.35,"SOLUSDT":1.45,"XRPUSDT":1.45,"BNBUSDT":1.40}[s]}
for s in FOREX:
    TUNING[s] = {"sr":.005 if s in ("GBPUSD","USDJPY","EURUSD","AUDUSD","NZDUSD","USDCAD","USDCHF","EURGBP") else .006,
                 "zone":.012 if s in ("GBPUSD","USDJPY","EURUSD","AUDUSD","NZDUSD","USDCAD","USDCHF","EURGBP") else .013,"vol":1.20}


def discord(msg):
    if not DISCORD_WEBHOOK_URL:
        print("â ï¸ DISCORD_WEBHOOK_URL à¸¢à¸±à¸à¹à¸¡à¹à¹à¸à¹à¸à¸±à¹à¸à¸à¹à¸²", flush=True); return False
    try:
        data=json.dumps({"content":msg}).encode()
        req=urllib.request.Request(DISCORD_WEBHOOK_URL,data=data,headers={"Content-Type":"application/json","User-Agent":"SIGZY-V4.3"},method="POST")
        with urllib.request.urlopen(req,timeout=15): pass
        return True
    except Exception as e:
        print("[DISCORD ERROR]",e,flush=True); return False


def fetch(symbol, limit=200):
    try:
        if symbol in CRYPTO:
            url=f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval=1m&limit={limit}"
        else:
            url=f"https://query1.finance.yahoo.com/v8/finance/chart/{YAHOO[symbol]}?interval=1m&range=1d"
        req=urllib.request.Request(url,headers={"User-Agent":"Mozilla/5.0"})
        with urllib.request.urlopen(req,timeout=15) as r: data=json.loads(r.read().decode())
        if symbol in CRYPTO:
            return [{"timestamp":int(x[0]),"open":float(x[1]),"high":float(x[2]),"low":float(x[3]),"close":float(x[4]),"volume":float(x[5])} for x in data]
        res=data.get("chart",{}).get("result")
        if not res:return None
        res=res[0]; ts=res.get("timestamp",[]); q=res.get("indicators",{}).get("quote",[{}])[0]; out=[]
        for i,t in enumerate(ts):
            try:
                o,h,l,c=q["open"][i],q["high"][i],q["low"][i],q["close"][i]
                if None in (o,h,l,c):continue
                v=(q.get("volume",[0]*len(ts))[i] or 0)
                out.append({"timestamp":int(t)*1000,"open":float(o),"high":float(h),"low":float(l),"close":float(c),"volume":float(v)})
            except: pass
        return out[-limit:]
    except Exception as e:
        print(f"[DATA ERROR] {symbol}: {e}",flush=True); return None


def default_setup(symbol="",action="",setup=""):
    return {"symbol":symbol,"action":action,"setup":setup,"total":0,"wins":0,"losses":0,"draws":0,"invalid":0,
            **{f"opportunity_{i}":{"wins":0,"losses":0,"draws":0} for i in (1,2,3)}}


def default_memory():
    return {"stats":{"total_setups":0,"wins":0,"losses":0,"draws":0,"invalid":0},"score_bands":{
        "PREMIUM_95_100":{"signals":0,"wins":0,"losses":0,"draws":0},
        "HIGH_QUALITY_88_94":{"signals":0,"wins":0,"losses":0,"draws":0},
        "WATCH_82_87":{"signals":0,"wins":0,"losses":0,"draws":0}},"setups":{}}


def save(mem):
    try:
        tmp=MEMORY_FILE+".tmp"
        with open(tmp,"w",encoding="utf-8") as f:json.dump(mem,f,indent=2,ensure_ascii=False)
        os.replace(tmp,MEMORY_FILE)
    except Exception as e:print("[MEMORY SAVE ERROR]",e,flush=True)


def init_memory():
    if os.path.exists(MEMORY_FILE):
        try:
            with open(MEMORY_FILE,encoding="utf-8") as f:m=json.load(f)
            base=default_memory(); m.setdefault("stats",{}); m.setdefault("setups",{}); m.setdefault("score_bands",{})
            for k,v in base["stats"].items():m["stats"].setdefault(k,v)
            for k,v in base["score_bands"].items():m["score_bands"].setdefault(k,v.copy())
            for s in m["setups"].values():
                for k in ("total","wins","losses","draws","invalid"):s.setdefault(k,0)
                for i in (1,2,3):s.setdefault(f"opportunity_{i}",{"wins":0,"losses":0,"draws":0})
            if "losss" in m["stats"]:
                m["stats"]["losses"]+=m["stats"].pop("losss");save(m)
            print("ð§  à¹à¸«à¸¥à¸ Memory à¹à¸à¸´à¸¡à¸ªà¸³à¹à¸£à¹à¸",flush=True);return m
        except Exception as e:print("[MEMORY LOAD ERROR]",e,flush=True)
    m=default_memory();save(m);return m


def quality(m,symbol,action,setup):
    s=m["setups"].get(f"{symbol}_{action}_{setup}")
    if not s:return {"wr":None,"decided":0}
    d=s.get("wins",0)+s.get("losses",0)
    return {"wr":s["wins"]/d*100 if d else None,"decided":d}


def pair_stats(m,symbol):
    w=l=d=t=0
    for k,s in m.get("setups",{}).items():
        if isinstance(s,dict) and (s.get("symbol")==symbol or k.startswith(symbol+"_")):
            w+=int(s.get("wins",0));l+=int(s.get("losses",0));d+=int(s.get("draws",0));t+=int(s.get("total",0))
    dec=w+l
    return {"wins":w,"losses":l,"draws":d,"total":t,"decided":dec,"wr":w/dec*100 if dec else None}


def pair_status(s):
    if s["decided"]<PAIR_MIN_HISTORY:return "LEARNING"
    if s["wr"]<PAIR_BLOCK_WR:return "BLOCK"
    if s["wr"]>=PAIR_STRONG_WR:return "STRONG"
    if s["wr"]>=PAIR_FAVOR_WR:return "FAVOR"
    if s["wr"]>=PAIR_WEAK_WR:return "NORMAL"
    return "WEAK"


def dynamic_symbols(m):
    pri={"STRONG":5,"FAVOR":4,"NORMAL":3,"LEARNING":2,"WEAK":1}; rows=[]
    for s in SYMBOLS:
        st=pair_stats(m,s); status=pair_status(st)
        if status!="BLOCK":rows.append((s,status,st))
    rows.sort(key=lambda x:(pri[x[1]],x[2]["wr"] if x[2]["wr"] is not None else 50,min(x[2]["decided"],10)),reverse=True)
    active=rows[:MAX_ACTIVE_SCAN_PAIRS]
    print("\nð DYNAMIC PAIR ROTATION",flush=True)
    for s,status,st in active:print(f"   â ACTIVE {s} | {status} | WR={st['wr']:.1f}% | decided={st['decided']}" if st["wr"] is not None else f"   â ACTIVE {s} | {status} | WR=N/A | decided=0",flush=True)
    for s in SYMBOLS:
        st=pair_stats(m,s)
        if pair_status(st)=="BLOCK":print(f"   ð« BLOCK {s} | WR={st['wr']:.1f}% | decided={st['decided']}",flush=True)
    return [x[0] for x in active]


def ema(a,n):
    n=min(n,len(a));e=a[0];k=2/(n+1)
    for p in a[1:]:e=p*k+e*(1-k)
    return e


def band(score):return "PREMIUM_95_100" if score>=95 else "HIGH_QUALITY_88_94" if score>=88 else "WATCH_82_87" if score>=82 else "BELOW_WATCH"


def update_band(m,score,result=None):
    b=m["score_bands"].get(band(score))
    if not b:return
    if result is None:b["signals"]+=1
    elif result in ("WIN","LOSS","DRAW"):b[result.lower()+"s"]+=1


def analyze(c,m,symbol):
    if not c or len(c)<150:return {"action":"WAIT","score":0,"setup":"NEUTRAL","call_score":0,"put_score":0,"trend":"N/A","real_wr":None,"group_wr":None}
    x=c[:-1];cur,pr,pr2=x[-1],x[-2],x[-3];cl=[z["close"] for z in x]
    e20,e50,e100=ema(cl,20),ema(cl,50),ema(cl,100)
    trend="BULL_STRONG" if cur["close"]>e20>e50>e100 else "BEAR_STRONG" if cur["close"]<e20<e50<e100 else "BULL" if cur["close"]>e50 else "BEAR" if cur["close"]<e50 else "RANGE"
    z=x[-101:-1];sup=min(q["low"] for q in z);res=max(q["high"] for q in z);p=cur["close"];t=TUNING.get(symbol,TUNING["DEFAULT"])
    ns=abs(p-sup)/max(abs(sup),1e-12)<=t["sr"];nr=abs(p-res)/max(abs(res),1e-12)<=t["sr"]
    ins=abs(p-sup)/max(abs(sup),1e-12)<=t["zone"];inr=abs(p-res)/max(abs(res),1e-12)<=t["zone"]
    vv=[q["volume"] for q in x[-16:-1] if q["volume"]>0];avg=sum(vv)/len(vv) if vv else 0;vr=cur["volume"]/avg if avg and cur["volume"] else 1;hv=vr>=t["vol"]
    body=abs(cur["close"]-cur["open"]);rng=max(cur["high"]-cur["low"],1e-12);up=cur["high"]-max(cur["open"],cur["close"]);lo=min(cur["open"],cur["close"])-cur["low"];bull=cur["close"]>cur["open"];bear=cur["close"]<cur["open"]
    bp=bull and lo>=max(body*1.8,rng*.3);sp=bear and up>=max(body*1.8,rng*.3)
    be=pr["close"]<pr["open"] and bull and cur["open"]<=pr["close"] and cur["close"]>=pr["open"]
    se=pr["close"]>pr["open"] and bear and cur["open"]>=pr["close"] and cur["close"]<=pr["open"]
    bf=bull and (pr["close"]>pr["open"] or pr2["close"]>pr2["open"] or be or bp);sf=bear and (pr["close"]<pr["open"] or pr2["close"]<pr2["open"] or se or sp)
    cs=ps=0
    cs+=20 if trend=="BULL_STRONG" else 15 if trend=="BULL" else 5 if trend=="RANGE" else 0
    ps+=20 if trend=="BEAR_STRONG" else 15 if trend=="BEAR" else 5 if trend=="RANGE" else 0
    cs+=25 if ns else 10 if ins else 0;ps+=25 if nr else 10 if inr else 0
    cs+=20 if bp or be else 7 if bull else 0;ps+=20 if sp or se else 7 if bear else 0
    cs+=15 if hv and bull else 4 if bull else 0;ps+=15 if hv and bear else 4 if bear else 0
    cs+=10 if bf else 0;ps+=10 if sf else 0
    call_setup="SUPPORT_REVERSAL_CALL" if ns else "MOMENTUM_CALL";put_setup="RESISTANCE_REVERSAL_PUT" if nr else "MOMENTUM_PUT"
    cm=quality(m,symbol,"CALL",call_setup);pm=quality(m,symbol,"PUT",put_setup)
    if cm["decided"]>=10:cs+=10 if cm["wr"]>=75 else 6 if cm["wr"]>=70 else -12 if cm["wr"]<55 else 0
    if pm["decided"]>=10:ps+=10 if pm["wr"]>=75 else 6 if pm["wr"]>=70 else -12 if pm["wr"]<55 else 0
    cg=ns and bull and (bp or be) and bf and trend!="BEAR_STRONG";pg=nr and bear and (sp or se) and sf and trend!="BULL_STRONG"
    cmg=(trend in ("BULL","BULL_STRONG") and bull and bf and hv and p>e20 and not nr);pmg=(trend in ("BEAR","BEAR_STRONG") and bear and sf and hv and p<e20 and not ns)
    cv=cg or cmg;pv=pg or pmg
    if cm["decided"]>=10 and cm["wr"]<70:cv=False
    if pm["decided"]>=10 and pm["wr"]<70:pv=False
    if cg:cs+=10
    if pg:ps+=10
    cs=max(0,min(100,cs));ps=max(0,min(100,ps));action="WAIT";score=0;setup="NEUTRAL";wr=None
    if cv and cs>=88 and cs>ps:action,score,setup,wr="CALL",cs,call_setup,cm["wr"]
    elif pv and ps>=88 and ps>cs:action,score,setup,wr="PUT",ps,put_setup,pm["wr"]
    return {"action":action,"score":round(score,2),"setup":setup,"real_wr":wr,"group_wr":wr,"call_score":round(cs,2),"put_score":round(ps,2),"trend":trend,"vol_ratio":round(vr,2)}


def record(m,tr,result):
    st=m["stats"];st["total_setups"]+=1;st["wins" if result=="WIN" else "losses" if result=="LOSS" else "draws"]+=1
    key=f"{tr['symbol']}_{tr['action']}_{tr['setup']}";s=m["setups"].setdefault(key,default_setup(tr["symbol"],tr["action"],tr["setup"]));s["total"]+=1
    if result in ("WIN","LOSS","DRAW"):s["wins" if result=="WIN" else "losses" if result=="LOSS" else "draws"]+=1
    for i in (1,2,3):
        r=tr.get("opportunity_results",{}).get(str(i))
        if r in ("WIN","LOSS","DRAW"):s[f"opportunity_{i}"][r.lower()+"s"]+=1
    update_band(m,tr.get("entry_score",0),result);save(m)
    d=s["wins"]+s["losses"];wr=s["wins"]/d*100 if d else None
    msg=f"ð PAPER RESULT {tr['symbol']}\nà¸à¸¥à¸ªà¸¸à¸à¸à¹à¸²à¸¢: {result}\nAction: {tr['action']}\nSetup: {tr['setup']}\nEntry Score: {tr['entry_score']:.1f}\nà¸à¸£à¸ Opportunity: 3/3\nSetup WR: {wr:.1f}%\nà¸£à¸§à¸¡à¸£à¸°à¸à¸: à¸à¸à¸° {st['wins']} | à¹à¸ªà¸¡à¸­ {st['draws']} | à¹à¸à¹ {st['losses']}"
    print(msg,flush=True);discord(msg)


def run():
    print("="*70);print("ð¤ SIGZY BRAIN V4.3 ADAPTIVE ULTRA");print("CRYPTO + FOREX | PAPER TRADE");print("ð¥ PREMIUM >= 95 | ð¢ HIGH QUALITY >= 88 | ð¡ WATCH 82-87");print("="*70)
    if DISCORD_WEBHOOK_URL:
        print("â Discord webhook loaded from Railway Variables",flush=True)
        discord("ð¢ SIGZY V4.3 ONLINE â Paper Trade + Memory + Dynamic Rotation")
    else:print("â ï¸ à¸à¸±à¹à¸ DISCORD_WEBHOOK_URL à¹à¸ Railway Variables",flush=True)
    m=init_memory();active={};last_alert={};last_signal=datetime.now()
    while True:
        now=datetime.now();print("\n"+"="*70);print("ð§  SCAN",now.strftime("%Y-%m-%d %H:%M:%S"),flush=True)
        # Track active trades
        done=[]
        for symbol,tr in list(active.items()):
            c=fetch(symbol,200)
            if not c or len(c)<3:continue
            cur=c[-2]
            if cur["timestamp"]==tr.get("last_ts"):continue
            tr["last_ts"]=cur["timestamp"];tr["opportunity"]+=1;opp=tr["opportunity"]
            green=cur["close"]>cur["open"];red=cur["close"]<cur["open"]
            result=("WIN" if green else "LOSS" if red else "DRAW") if tr["action"]=="CALL" else ("WIN" if red else "LOSS" if green else "DRAW")
            tr["opportunity_results"][str(opp)]=result;print(f"ð {symbol} | OPP {opp}/3 | {tr['action']} | {result}",flush=True)
            if opp>=3:record(m,tr,tr["opportunity_results"]["3"]);done.append(symbol)
        for s in done:active.pop(s,None)
        # Scan dynamic pairs
        candidates=[]
        for symbol in dynamic_symbols(m):
            if symbol in active:continue
            if symbol in last_alert and (now-last_alert[symbol]).total_seconds()<300:continue
            c=fetch(symbol,200)
            if not c or len(c)<150:continue
            a=analyze(c,m,symbol);closed=c[:-1]
            if a["action"]!="WAIT" and a["score"]>=88:
                candidates.append({"symbol":symbol,"analysis":a,"price":closed[-1]["close"],"timestamp":closed[-1]["timestamp"]})
                print(f"ð¯ {symbol} {a['action']} {a['score']:.1f} {a['setup']}",flush=True)
        candidates.sort(key=lambda x:x["analysis"]["score"],reverse=True)
        selected=candidates[:MAX_NEW_ALERTS_PER_SCAN]
        if selected:last_signal=now
        for c in selected:
            a=c["analysis"];symbol=c["symbol"]
            tr={"symbol":symbol,"action":a["action"],"price":c["price"],"entry_timestamp":c["timestamp"],"entry_score":a["score"],"setup":a["setup"],"opportunity":0,"last_ts":c["timestamp"],"opportunity_results":{}}
            active[symbol]=tr;last_alert[symbol]=now;update_band(m,a["score"]);save(m)
            wr=f"{a['real_wr']:.1f}%" if a["real_wr"] is not None else "à¸à¸³à¸¥à¸±à¸à¹à¸à¹à¸à¸ªà¸à¸´à¸à¸´"
            msg=(f"ð¨ SIGZY V4.3 {band(a['score'])}\nà¸à¸¥à¸²à¸: {symbol}\nà¸à¸¹à¹: {symbol}\nà¹à¸§à¸¥à¸²: {now.strftime('%H:%M:%S')}\n"
                 f"à¸£à¸²à¸à¸²: {c['price']}\nà¸à¸´à¸¨à¸à¸²à¸: {'à¹à¸à¸·à¸­à¸à¸à¸¶à¹à¸ ð¢' if a['action']=='CALL' else 'à¹à¸à¸·à¸­à¸à¸¥à¸ ð´'}\nScore: {a['score']:.1f}/100\n"
                 f"Setup: {a['setup']}\nTrend: {a['trend']}\nHistorical WR: {wr}\n\nð§  Memory preserved\n"
                 f"ð Paper Track: Opportunity 1 â 2 â 3\nð Dynamic Pair Rotation active\nâ ï¸ à¹à¸¡à¹à¹à¸à¹à¸à¸³à¸ªà¸±à¹à¸à¸à¸·à¹à¸­à¸à¸²à¸¢à¸­à¸±à¸à¹à¸à¸¡à¸±à¸à¸´")
            print(msg,flush=True);discord(msg)
        print(f"ð Active trades: {len(active)} | Last signal: {(now-last_signal).total_seconds()/60:.0f} min ago",flush=True)
        print(f"â³ à¸£à¸­ {PAPER_INTERVAL_SECONDS} à¸§à¸´à¸à¸²à¸à¸µ...",flush=True);time.sleep(PAPER_INTERVAL_SECONDS)

if __name__=="__main__":
    try:run()
    except KeyboardInterrupt:print("ð à¸«à¸¢à¸¸à¸ SIGZY V4.3")
    except Exception as e:print("[FATAL ERROR]",repr(e),flush=True)
