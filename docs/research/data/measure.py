import json, sys, collections
sys.path.insert(0,'/home/user/Metis-Insights')
from src.runtime.r_provenance import classify_r, R_CONTAMINATED, R_CONFIRMED_INITIAL, R_UNVERIFIED, R_NO_BASIS
from src.web.api._clean_trades import r_multiple
from src.runtime.local_pnl import contract_value_usd_for
from src.runtime.provenance import classify_pnl

W="/tmp/claude-0/work/"
trades=json.load(open(W+"all_trades.json"))
pkgs={p["order_package_id"]:p for p in json.load(open(W+"pkgs.json"))}

def closed_pop(rows):
    """Population: status='closed', not backtest, pnl NOT NULL."""
    out=[]
    for t in rows:
        if t.get("status")!="closed": continue
        if t.get("is_backtest"): continue
        if t.get("pnl") is None: continue
        out.append(t)
    return out

def enrich(t):
    pk=pkgs.get(t.get("order_package_id"))
    return {
        "direction":t.get("direction"),"entry_price":t.get("entry_price"),
        "stop_loss":t.get("stop_loss"),"take_profit_1":t.get("take_profit_1"),
        "qty":t.get("position_size"),"package_meta":(pk or {}).get("meta"),
    }

pop=closed_pop(trades)
print(f"POPULATION: closed, non-backtest, pnl NOT NULL  n={len(pop)}  (of {len(trades)} total trades rows)")
states=collections.Counter(); rsum=0.0; rn=0
cont_r=[]; conf_r=[]
for t in pop:
    e=enrich(t); st,reason=classify_r(e); states[st]+=1
    rr=r_multiple(t["pnl"],t.get("entry_price"),t.get("stop_loss"),
                  t.get("position_size"),contract_value_usd_for(t.get("symbol")))
    if rr is not None:
        rsum+=rr; rn+=1
        if st==R_CONTAMINATED: cont_r.append(rr)
        elif st==R_CONFIRMED_INITIAL: conf_r.append(rr)
print("\nR-PROVENANCE over that population:")
for s in (R_CONTAMINATED,R_CONFIRMED_INITIAL,R_UNVERIFIED,R_NO_BASIS):
    print(f"  {s:20s} {states[s]:5d}  {100*states[s]/len(pop):5.1f}%")
print(f"\nR-measurable rows (r_multiple not None): {rn}  rCoverage={rn/len(pop):.3f}")
print(f"  totalR = {rsum:+.2f}   expectancyR = {rsum/rn:+.4f}")
print(f"\n  contaminated & R-measurable: n={len(cont_r)}  sumR={sum(cont_r):+.2f}  meanR={sum(cont_r)/len(cont_r):+.3f}" if cont_r else "")
print(f"  confirmed_initial          : n={len(conf_r)}  sumR={sum(conf_r):+.2f}  meanR={sum(conf_r)/len(conf_r):+.3f}" if conf_r else "")
if cont_r:
    cont_r_s=sorted(cont_r)
    print(f"  contaminated R range: min={cont_r_s[0]:+.1f} max={cont_r_s[-1]:+.1f}")
    print(f"  |R|>10 among contaminated: {sum(1 for x in cont_r if abs(x)>10)}")
    print(f"  |R|>10 among confirmed   : {sum(1 for x in conf_r if abs(x)>10)}")
pnl_tot=sum(float(t['pnl']) for t in pop)
print(f"\n  totalPnl over same population = {pnl_tot:+.2f} USD")
