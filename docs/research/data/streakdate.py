import json,datetime as dt
W="/tmp/claude-0/work/"
tr=json.load(open(W+"b2_trades.json")); pkgs={p["order_package_id"]:p for p in json.load(open(W+"pkgs.json"))}
def P(s):
    if not s: return None
    s=str(s).replace(" ","T").replace("Z","+00:00")
    try:
        d=dt.datetime.fromisoformat(s); return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)
    except Exception: return None
def ct(t): return P(t.get("closed_at")) or P((pkgs.get(t.get("order_package_id")) or {}).get("updated_at")) or P(t.get("timestamp"))
def leg(t): return t.get("strategy_name") or t.get("setup_type") or "?"
g=[t for t in tr if t.get("status")=="closed" and not t.get("is_backtest") and t.get("pnl") is not None and ct(t) and leg(t)!="vwap"]
g.sort(key=ct)
runs=[];cur=[]
for t in g:
    if float(t['pnl'])<=0: cur.append(t)
    else:
        if cur: runs.append(cur)
        cur=[]
if cur: runs.append(cur)
print("bybit_2 ex-vwap consecutive-loss streaks >= 5, dated (e35 shipped 2026-08-30T08:53Z):")
for r in sorted(runs,key=lambda x:-len(x)):
    if len(r)<5: continue
    p=sum(float(t['pnl']) for t in r)
    print(f"  len={len(r):3d}  {ct(r[0]).date()} .. {ct(r[-1]).date()}  pnl={p:+8.2f}  legs={sorted(set(leg(t) for t in r))}")

print("\n=== THE CURRENT STREAK, trade by trade (e35 deployed 2026-08-30T08:53Z) ===")
cur_streak=max(runs,key=len)
print(f"{'closed_at':22s} {'id':>5} {'leg':24s} {'entry_opened':20s} {'pnl':>8}  pre/post e35 ENTRY")
E=dt.datetime(2026,8,30,8,53,tzinfo=dt.timezone.utc)
for t in cur_streak:
    t0=P(t.get("timestamp"))
    print(f"{str(ct(t))[:19]:22s} {t['id']:>5} {leg(t):24.24s} {str(t0)[:19]:20s} {float(t['pnl']):8.2f}  {'POST' if t0 and t0>=E else 'PRE'}")
