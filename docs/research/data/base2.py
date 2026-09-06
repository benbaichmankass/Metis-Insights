import json,datetime as dt,collections
W="/tmp/claude-0/work/"
tr=json.load(open(W+"b2_trades.json")); pkgs={p["order_package_id"]:p for p in json.load(open(W+"pkgs.json"))}
def P(s):
    if not s: return None
    s=str(s).replace(" ","T").replace("Z","+00:00")
    try:
        d=dt.datetime.fromisoformat(s); return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)
    except Exception: return None
def ct(t): return P(t.get("closed_at")) or P((pkgs.get(t.get("order_package_id")) or {}).get("updated_at")) or P(t.get("timestamp"))
g=[t for t in tr if t.get("status")=="closed" and not t.get("is_backtest") and t.get("pnl") is not None and ct(t)]
g.sort(key=ct)
def leg(t): return t.get("strategy_name") or t.get("setup_type") or "?"
vw=[t for t in g if leg(t)=="vwap"]
print(f"vwap on bybit_2: n={len(vw)}  last close {ct(vw[-1]).date()}  first {ct(vw[0]).date()}")
nv=[t for t in g if leg(t)!="vwap"]
print(f"\nPOPULATION: bybit_2 closed, non-backtest, pnl NOT NULL, EXCLUDING vwap — n={len(nv)}")
print(f"  window {ct(nv[0]).date()} .. {ct(nv[-1]).date()}")
w=sum(1 for t in nv if float(t['pnl'])>0)
print(f"  wins {w}/{len(nv)} = {100*w/len(nv):.1f}%   PnL {sum(float(t['pnl']) for t in nv):+.2f}")
def streaks(rows):
    best=cur=0; runs=[]
    for t in rows:
        if float(t['pnl'])<=0: cur+=1; best=max(best,cur)
        else:
            if cur: runs.append(cur)
            cur=0
    if cur: runs.append(cur)
    return best,runs
b,runs=streaks(nv)
print(f"  longest consecutive-loss streak (ex-vwap): {b}")
print(f"  all streaks >=4: {sorted([r for r in runs if r>=4],reverse=True)}")
# monthly
print("\n  MONTHLY (ex-vwap):")
m=collections.defaultdict(lambda:[0,0,0.0])
for t in nv:
    k=ct(t).strftime("%Y-%m"); m[k][0]+=1; m[k][1]+= 1 if float(t['pnl'])>0 else 0; m[k][2]+=float(t['pnl'])
for k in sorted(m):
    c,ww,p=m[k]; print(f"    {k}  n={c:3d} wins={ww:3d} ({100*ww/c:5.1f}%)  pnl={p:+8.2f}")
