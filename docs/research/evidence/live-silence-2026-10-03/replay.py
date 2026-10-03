import json, sys, datetime as dt, collections
import pandas as pd
sys.path.insert(0, '.')
from src.units.strategies import load_strategy_config
from src.units.strategies.trend_donchian import order_package as dc_pkg
from src.units.strategies.htf_pullback_trend_2h import order_package as pb_pkg
S = sys.argv[1]
cfgs = load_strategy_config()
LEGS = [("xrp_pullback_2h","XRPUSDT","2h",pb_pkg),("ada_pullback_2h","ADAUSDT","2h",pb_pkg),
        ("trend_donchian_eth_4h","ETHUSDT","4h",dc_pkg),("trend_donchian_xrp_4h","XRPUSDT","4h",dc_pkg)]
TF = {"2h":7200,"4h":14400}
NOW = pd.Timestamp("2026-10-03T05:50Z")
def load(sym, tf):
    c = json.load(open(f"{S}/{sym}_{tf}.json"))["candles"]
    df = pd.DataFrame(c); df["timestamp"] = pd.to_datetime(df["time"], unit="s", utc=True)
    return df[["timestamp","open","high","low","close","volume"]]
def ev(fn, cfg, w):
    try:
        p = fn(cfg, candles_df=w.reset_index(drop=True)); return p
    except ValueError as e: return str(e)
def sidef(name, d):
    sf = cfgs[name].get("side_filter") or ("long" if cfgs[name].get("long_only") else "both")
    return sf == "both" or sf == d
out = {}
for name, sym, tf, fn in LEGS:
    vc = cfgs[name]; cfg = {"symbol": sym, "timeframe": tf, **vc, "strategy_label": name}
    df = load(sym, tf)
    closed = df[df.timestamp + pd.Timedelta(seconds=TF[tf]) <= NOW].reset_index(drop=True)
    sigs = []; reasons = collections.Counter(); nbars = 0
    for i in range(199, len(closed)):
        w = closed.iloc[i-199:i+1]
        p = ev(fn, cfg, w); ts = closed.timestamp[i]
        if isinstance(p, dict):
            sigs.append((str(ts), p["direction"], p["confidence"], sidef(name, p["direction"])))
        else:
            reasons[(p.split(": ",1)[1][:40]) if ts >= pd.Timestamp("2026-09-28T00:00Z") else "pre"] += 1
        nbars += 1
    print(f"\n=== {name} closed-bar replay: {nbars} bars {closed.timestamp[199]} .. {closed.timestamp.iloc[-1]}  decision_bar={vc.get('decision_bar','forming')} side_filter={vc.get('side_filter','both')}")
    for s in sigs:
        if s[0] >= "2026-09-14": print("  SIG", s)
    post = [s for s in sigs if s[0] >= "2026-09-28" and s[3]]
    allv = [s for s in sigs if s[3]]
    nb_post = sum(1 for t in closed.timestamp[199:] if t >= pd.Timestamp("2026-09-28T03:35Z"))
    print(f"  valid-side signals total {len(allv)} over {nbars} bars ({len(allv)/nbars:.4f}/bar); since 09-28T03:35: {len(post)} over {nb_post} bars")
    print("  post-09-28 no-signal reasons:", {k:v for k,v in reasons.items() if k!='pre'})
    out[name] = dict(sigs=sigs, nbars=nbars)
    # forming-bar replay at 15m checkpoints (approximates live ~2min forming ticks)
    if vc.get("decision_bar","forming") == "forming":
        m15 = load(sym, "15m")
        fsig = []
        for k in range(len(m15)):
            t_end = m15.timestamp[k] + pd.Timedelta(minutes=15)
            if t_end > NOW: break
            bopen = t_end.floor(f"{TF[tf]}s")
            if t_end == bopen: continue  # that's a bar close, covered by closed replay
            part = m15[(m15.timestamp >= bopen) & (m15.timestamp < t_end)]
            hist = closed[closed.timestamp < bopen].iloc[-199:]
            if len(hist) < 199: continue
            fb = pd.DataFrame([{"timestamp": bopen, "open": part.open.iloc[0], "high": part.high.max(), "low": part.low.min(), "close": part.close.iloc[-1], "volume": part.volume.sum()}])
            w = pd.concat([hist, fb])
            p = ev(fn, cfg, w)
            if isinstance(p, dict): fsig.append((str(t_end), str(bopen), p["direction"], p["confidence"], sidef(name,p["direction"])))
        print(f"  forming-bar 15m-checkpoint signals since {m15.timestamp[0]}: {len(fsig)}")
        for s in fsig: print("   F", s)
