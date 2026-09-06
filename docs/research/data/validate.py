import json,datetime as dt
W="/tmp/claude-0/work/"
cand={k:sorted(v) for k,v in json.load(open(W+"candles.json")).items()}
tr=json.load(open(W+"b2_trades.json")); pkgs={p["order_package_id"]:p for p in json.load(open(W+"pkgs.json"))}
def P(s):
    if not s: return None
    s=str(s).replace(" ","T").replace("Z","+00:00")
    try:
        d=dt.datetime.fromisoformat(s); return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)
    except Exception: return None
def ct(t): return P(t.get("closed_at")) or P((pkgs.get(t.get("order_package_id")) or {}).get("updated_at")) or P(t.get("timestamp"))
def ref(sym,when):
    c=cand.get(sym); b=None
    for r in c:
        if r[0]<=when.timestamp(): b=r
        else: break
    return b[4] if b else None
def mae(sym,d,t0,t1):
    c=cand.get(sym); rp=ref(sym,t0)
    if not rp: return None
    seg=[r for r in c if t0.timestamp()<=r[0]<=t1.timestamp()]
    if not seg: return None
    d=str(d or "").lower()
    if d in("long","buy"): return max(0.0,(rp-min(r[1] for r in seg))/rp)
    return max(0.0,(max(r[2] for r in seg)-rp)/rp)
rows=[t for t in tr if t.get("status")=="closed" and not t.get("is_backtest")
      and ct(t) and P("2026-08-16T00:00:00Z")<=ct(t)<P("2026-09-07T00:00:00Z")]
sl=[t for t in rows if (t.get("exit_reason") or "")=="sl"]
print("PROXY VALIDATION — population: bybit_2 closed rows 2026-08-16..09-06 with exit_reason='sl'")
print("A row that DID hit its stop must show proxy MAE/D >= 1.0. If it does not, the proxy")
print("cannot reproduce a known event and must not be used to adjudicate a hypothetical one.\n")
ok=bad=0; basis=[]
for t in sorted(sl,key=lambda x:ct(x)):
    e,s,sym=t.get("entry_price"),t.get("stop_loss"),t.get("symbol")
    t0,t1=P(t.get("timestamp")),ct(t)
    if not(e and s and sym in cand and t0 and t1): continue
    D=abs(e-s)/e; m=mae(sym,t.get("direction"),t0,t1)
    rp=ref(sym,t0); b=(e-rp)/rp*100 if rp else None
    r=m/D if m is not None and D else None
    v="reproduces" if (r and r>=1.0) else "FAILS"
    if r and r>=1.0: ok+=1
    else: bad+=1
    if b is not None: basis.append(abs(b))
    print(f"  id {t['id']:>5} {(t.get('strategy_name') or '?'):22.22s} stopD={D*100:5.2f}%  proxyMAE={m*100 if m else 0:5.2f}%  ratio={r if r else 0:5.2f}  {v:11s} venue-vs-proxy basis at entry={b:+.2f}%")
print(f"\n  reproduces: {ok}/{ok+bad}   FAILS: {bad}/{ok+bad}  ({100*bad/(ok+bad):.0f}%)")
print(f"  mean |basis| between Bybit perp entry and Coinbase spot at entry: {sum(basis)/len(basis):.2f}%  (max {max(basis):.2f}%)")
print(f"  median stop distance in this set: {sorted(abs(t['entry_price']-t['stop_loss'])/t['entry_price']*100 for t in sl if t.get('entry_price') and t.get('stop_loss'))[len(sl)//2]:.2f}%")
