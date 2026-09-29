#!/usr/bin/env python3
"""Price MD-DEMOTE-S2-S1 / R4 trigger rules on each Stage-2 leg's Stage-0 record.

PI-20260929-VOLSKIP-SIGNAL-0005 (read-only analysis, lane VOLSKIP-SIGNAL).
Trade stream per leg at its record rate (Poisson arrivals), per-trade net R
bootstrapped from comms/strategy_evidence/<leg>.json source_run and
mean-shifted to the scenario edge; 40 healthy seed trades; the rolling
last-20-trade window is re-evaluated after every new trade (== a daily check
while a leg closes <=1 trade/day). Triggers: T0 net<0; T1 below the leg's own
Stage-0 p10 of 20-trade sums; T2 p5; T3 T1 on 2 consecutive NON-overlapping
20-trade windows. Reports P(healthy leg ever demoted within 1y) and, for true
edge 0 and -0.1R/trade, P(demoted within 1y / 5y) and the median time.
Run from the repo root: python3 scripts/research/r4_trigger_pricing_sim.py
"""
import json
import numpy as np
rng=np.random.default_rng(5007); S=4000
legs=["xrp_pullback_2h","trend_donchian_eth_4h","trend_donchian_xrp_4h","ada_pullback_2h","ief_pullback_1d","slv_pullback_1d","iaum_pullback_1d"]
def draws(x,mu,n): y=x-x.mean()+mu; return y[rng.integers(0,len(y),n)]
for leg in legs:
    rec=json.load(open(f"comms/strategy_evidence/{leg}.json"))
    x=np.array([json.loads(l)["net_r"] for l in open(rec["source_run"]) if l.strip()],float)
    rate=len(x)/(rec.get("window_days") or 365)*365
    boot=draws(x,x.mean(),200000*20).reshape(-1,20).sum(1)
    p10,p5=np.percentile(boot,[10,5])
    thr={"T0":0.0,"T1":p10,"T2":p5,"T3":p10}
    res={}
    for scen,mu,H in (("healthy",x.mean(),1.0),("edge0",0.0,5.0),("edge-0.1",-0.1,5.0)):
        first={k:np.full(S,np.inf) for k in thr}
        for s in range(S):
            n=rng.poisson(rate*H); t=np.sort(rng.uniform(0,H,n))
            seq=np.concatenate([draws(x,x.mean(),40),draws(x,mu,n)])
            cs=np.concatenate([[0],np.cumsum(seq)]); w=cs[20:]-cs[:-20]   # w[j]=sum trades j..j+19 ; window ending at trade j+19
            ends=np.arange(39,40+n)            # eval points: seed end, then each post trade
            times=np.concatenate([[0.0],t])
            wc=w[ends-19]; wp=w[ends-39]
            for k,v in thr.items():
                hit=(wc<v)&(wp<v) if k=="T3" else (wc<v)
                idx=np.flatnonzero(hit)
                if idx.size: first[k][s]=times[idx[0]]
        res[scen]=first
    print(f"\n{leg}: rate {rate:.0f}/yr, p10 {p10:+.2f}R p5 {p5:+.2f}R (20-trade sum; record mean*20 = {x.mean()*20:+.2f}R)")
    for k in thr:
        fp=(res['healthy'][k]<=1.0).mean()
        out=[f"{k}: healthy ever-demoted<=1y {fp:.3f}"]
        for sc in ("edge0","edge-0.1"):
            f=res[sc][k]; within1=(f<=1.0).mean(); within5=(f<=5.0).mean(); med=np.median(f)
            out.append(f"{sc}: P<=1y {within1:.2f} P<=5y {within5:.2f} median {'>5y' if not np.isfinite(med) else f'{med*365:.0f}d'}")
        print("  "+" | ".join(out))
