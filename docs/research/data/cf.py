import json,subprocess,datetime as dt,sys,time
W="/tmp/claude-0/work/"
PROD={"BTCUSDT":"BTC-USD","ETHUSDT":"ETH-USD","XRPUSDT":"XRP-USD"}
def fetch(prod,a,b,gran=900):
    out=[];cur=a
    while cur<b:
        nxt=min(cur+dt.timedelta(seconds=gran*290),b)
        u=(f"https://api.exchange.coinbase.com/products/{prod}/candles?granularity={gran}"
           f"&start={cur.isoformat().replace('+00:00','Z')}&end={nxt.isoformat().replace('+00:00','Z')}")
        for attempt in range(4):
            o=subprocess.run(["curl","-sS","--max-time","25",u],capture_output=True,text=True).stdout
            try:
                d=json.loads(o)
                if isinstance(d,list): out+=d; break
            except Exception: pass
            time.sleep(1.5)
        cur=nxt; time.sleep(0.25)
    return sorted(set(map(tuple,out)))
A=dt.datetime(2026,8,15,tzinfo=dt.timezone.utc); B=dt.datetime(2026,9,7,tzinfo=dt.timezone.utc)
cand={}
for sym,p in PROD.items():
    cand[sym]=fetch(p,A,B)
    print(f"{sym}: {len(cand[sym])} candles  {dt.datetime.utcfromtimestamp(cand[sym][0][0])} .. {dt.datetime.utcfromtimestamp(cand[sym][-1][0])}",flush=True)
json.dump({k:[list(x) for x in v] for k,v in cand.items()},open(W+"candles.json","w"))
