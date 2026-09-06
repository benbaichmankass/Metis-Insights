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
print(f"POPULATION: bybit_2 closed, non-backtest, pnl NOT NULL, ALL TIME — n={len(g)}")
print(f"  first close {ct(g[0]).date()}  last close {ct(g[-1]).date()}")
w=sum(1 for t in g if float(t['pnl'])>0)
print(f"  lifetime wins {w}/{len(g)} = {100*w/len(g):.1f}%   lifetime PnL {sum(float(t['pnl']) for t in g):+.2f}\n")
# per leg lifetime
per=collections.defaultdict(lambda:[0,0,0.0])
for t in g:
    n=t.get("strategy_name") or t.get("setup_type") or "?"
    per[n][0]+=1; per[n][1]+= 1 if float(t['pnl'])>0 else 0; per[n][2]+=float(t['pnl'])
print(f"  {'leg':26s} {'n':>4} {'wins':>5} {'win%':>6} {'pnl':>10}")
for n,(c,ww,p) in sorted(per.items(),key=lambda x:-x[1][0]):
    if c>=3: print(f"  {n:26s} {c:4d} {ww:5d} {100*ww/c:5.1f}% {p:10.2f}")
# longest losing streak all-time, and ict_scalp streaks
def streaks(rows):
    best=cur=0; runs=[]
    for t in rows:
        if float(t['pnl'])<=0: cur+=1; best=max(best,cur)
        else:
            if cur: runs.append(cur)
            cur=0
    if cur: runs.append(cur)
    return best,runs
b,runs=streaks(g)
print(f"\n  ALL-TIME longest consecutive-loss streak on bybit_2: {b}")
print(f"  streaks >=5: {sorted([r for r in runs if r>=5],reverse=True)}")
sc=[t for t in g if (t.get('strategy_name') or t.get('setup_type'))=='ict_scalp_5m']
bs,rs=streaks(sc)
print(f"  ict_scalp_5m alone: n={len(sc)} wins={sum(1 for t in sc if float(t['pnl'])>0)} longest loss streak={bs} streaks>=4:{sorted([r for r in rs if r>=4],reverse=True)}")
