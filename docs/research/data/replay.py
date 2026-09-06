import json,datetime as dt,sys,collections
W="/tmp/claude-0/work/"
cand={k:sorted(v) for k,v in json.load(open(W+"candles.json")).items()}
tr=json.load(open(W+"b2_trades.json")); pkgs={p["order_package_id"]:p for p in json.load(open(W+"pkgs.json"))}
E35={"trend_donchian","trend_donchian_eth_4h","trend_donchian_xrp_4h"}
E35_DEPLOY=dt.datetime(2026,8,30,8,53,tzinfo=dt.timezone.utc)
def P(s):
    if not s: return None
    s=str(s).replace(" ","T").replace("Z","+00:00")
    try:
        d=dt.datetime.fromisoformat(s)
        return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)
    except Exception: return None
def ct(t):
    return P(t.get("closed_at")) or P((pkgs.get(t.get("order_package_id")) or {}).get("updated_at")) or P(t.get("timestamp"))
def ref_price(sym,when):
    c=cand.get(sym)
    if not c: return None
    ts=when.timestamp(); best=None
    for row in c:
        if row[0]<=ts: best=row
        else: break
    return best[4] if best else None
def mae_rel(sym,direction,t0,t1):
    """Max adverse excursion as a FRACTION of the reference price at t0."""
    c=cand.get(sym); ref=ref_price(sym,t0)
    if not c or not ref: return None
    lo,hi=t0.timestamp(),t1.timestamp()
    seg=[r for r in c if lo<=r[0]<=hi]
    if not seg: return None
    d=str(direction or "").lower()
    if d in ("long","buy"):   return max(0.0,(ref-min(r[1] for r in seg))/ref)   # low
    if d in ("short","sell"): return max(0.0,(max(r[2] for r in seg)-ref)/ref)   # high
    return None

rows=[t for t in tr if t.get("status")=="closed" and not t.get("is_backtest")]
def win(t,a,b):
    c=ct(t); return c and P(a)<=c<P(b)
for label,a,b in [("PRIOR 08-16..08-29","2026-08-16T00:00:00Z","2026-08-30T00:00:00Z"),
                  ("POST  08-30..09-06","2026-08-30T00:00:00Z","2026-09-07T00:00:00Z")]:
    sel=[t for t in rows if win(t,a,b)]
    print(f"\n########## {label}   closed n={len(sel)} ##########")
    hdr=f"{'id':>5} {'strategy':22.22s} {'dir':5s} {'stopD%':>7s} {'MAE%':>7s} {'MAE/D':>7s} {'wider1.25 survives?':20s} {'pnl':>7s} {'e35-entry':9s}"
    print(hdr)
    surv=[];tot=0
    for t in sorted(sel,key=lambda x:ct(x)):
        e=t.get("entry_price"); s=t.get("stop_loss"); sym=t.get("symbol")
        t0=P(t.get("timestamp")); t1=ct(t)
        sn=t.get("strategy_name") or t.get("setup_type") or "?"
        e35e = "YES" if (sn in E35 and t0 and t0>=E35_DEPLOY) else ("pre-flip" if sn in E35 else "-")
        if not(e and s and t0 and t1 and sym in cand):
            print(f"{t['id']:>5} {sn:22.22s} {str(t.get('direction')):5.5s} {'-':>7} {'-':>7} {'-':>7} {'NO CANDLE/INPUT':20s} {(('%.2f'%float(t['pnl'])) if t.get('pnl') is not None else 'None'):>7} {e35e:9s}")
            continue
        D=abs(e-s)/e; m=mae_rel(sym,t.get("direction"),t0,t1)
        if m is None:
            print(f"{t['id']:>5} {sn:22.22s} {str(t.get('direction')):5.5s} {D*100:7.2f} {'-':>7} {'-':>7} {'NO MAE':20s}")
            continue
        ratio=m/D if D>0 else None
        stopped = (t.get("exit_reason") or "")=="sl"
        verdict = "n/a (not stopped)" if not stopped else ("SURVIVES" if ratio<1.25 else "still stopped")
        if stopped:
            tot+=1
            if ratio<1.25: surv.append(t["id"])
        print(f"{t['id']:>5} {sn:22.22s} {str(t.get('direction')):5.5s} {D*100:7.2f} {m*100:7.2f} {ratio:7.2f} {verdict:20s} {(('%.2f'%float(t['pnl'])) if t.get('pnl') is not None else 'None'):>7} {e35e:9s}")
    print(f"  -> of {tot} rows labelled exit_reason='sl', {len(surv)} would NOT have reached a 1.25x wider stop: {surv}")
