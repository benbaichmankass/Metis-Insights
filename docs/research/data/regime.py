import json,datetime as dt,statistics as st
W="/tmp/claude-0/work/"
cand={k:sorted(v) for k,v in json.load(open(W+"candles.json")).items()}
def seg(sym,a,b):
    A=dt.datetime.fromisoformat(a).replace(tzinfo=dt.timezone.utc).timestamp()
    B=dt.datetime.fromisoformat(b).replace(tzinfo=dt.timezone.utc).timestamp()
    return [r for r in cand[sym] if A<=r[0]<=B]
WINS=[("PRIOR 08-16..08-29","2026-08-16T00:00:00","2026-08-30T00:00:00"),
      ("POST  08-30..09-06","2026-08-30T00:00:00","2026-09-07T00:00:00")]
print("REGIME — Coinbase spot 15m (proxy adequate here: 0.23% basis vs multi-% moves)\n")
print(f"{'window':20s} {'sym':8s} {'open':>10s} {'close':>10s} {'net%':>7s} {'high':>10s} {'low':>10s} {'range%':>7s} {'|15m|med%':>9s} {'efficiency':>10s}")
for lbl,a,b in WINS:
    for sym in ("BTCUSDT","ETHUSDT","XRPUSDT"):
        s=seg(sym,a,b)
        if not s: continue
        o=s[0][3]; c=s[-1][4]; hi=max(r[2] for r in s); lo=min(r[1] for r in s)
        net=(c-o)/o*100; rng=(hi-lo)/o*100
        moves=[abs(r[4]-r[3])/r[3]*100 for r in s if r[3]]
        path=sum(abs(s[i][4]-s[i-1][4]) for i in range(1,len(s)))
        eff=abs(c-o)/path if path else 0     # directional efficiency: 1=pure trend, 0=pure chop
        print(f"{lbl:20s} {sym:8s} {o:10.2f} {c:10.2f} {net:+7.2f} {hi:10.2f} {lo:10.2f} {rng:7.2f} {st.median(moves):9.3f} {eff:10.3f}")
    print()
