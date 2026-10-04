"""LIVE-SILENCE-2 replay: every real-money leg, through now, plus distance-to-signal.

Calls the LIVE units' order_package() (config from config/strategies.yaml) on
candles from the bot's own feed (/api/bot/candles). Usage:

    python3 replay.py <candle_dir> <now_iso>

<candle_dir> holds <SYM>_<tf>.json as returned by
/api/bot/candles?symbol=<SYM>&interval=<tf>&limit=1000.
"""
import collections
import json
import sys

import pandas as pd

sys.path.insert(0, ".")
from src.units.strategies import load_strategy_config  # noqa: E402
from src.units.strategies.htf_pullback_trend_2h import (  # noqa: E402
    _adx, _atr, _trailing_atr_pctl, _trend_midline, order_package as pb_pkg)
from src.units.strategies.trend_donchian import order_package as dc_pkg  # noqa: E402

S, NOW = sys.argv[1], pd.Timestamp(sys.argv[2])
SINCE = pd.Timestamp("2026-09-28T03:35Z")
cfgs = load_strategy_config()
# (leg, symbol, tf, unit, account side_filter) — rosters read 2026-10-04 from config/accounts.yaml
LEGS = [
    ("xrp_pullback_2h", "XRPUSDT", "2h", pb_pkg, "both"),
    ("ada_pullback_2h", "ADAUSDT", "2h", pb_pkg, "both"),
    ("trend_donchian_eth_4h", "ETHUSDT", "4h", dc_pkg, "both"),
    ("trend_donchian_xrp_4h", "XRPUSDT", "4h", dc_pkg, "both"),
    ("ief_pullback_1d", "IEF", "1d", pb_pkg, "long"),
    ("slv_pullback_1d", "SLV", "1d", pb_pkg, "long"),
    ("iaum_pullback_1d", "IAUM", "1d", pb_pkg, "long"),
]
TF = {"2h": 7200, "4h": 14400, "1d": 86400}


def load(sym, tf):
    df = pd.DataFrame(json.load(open(f"{S}/{sym}_{tf}.json"))["candles"])
    df["timestamp"] = pd.to_datetime(df["time"], unit="s", utc=True)
    return df[["timestamp", "open", "high", "low", "close", "volume"]]


def ev(fn, cfg, w):
    try:
        return fn(cfg, candles_df=w.reset_index(drop=True))
    except ValueError as e:
        return str(e)


def valid(name, acct_sf, d):
    sf = cfgs[name].get("side_filter", "both")
    return (sf in ("both", d)) and (acct_sf in ("both", d))


def closed_frame(name, sym, tf):
    df = load(sym, tf)
    if tf == "1d":
        # daily bars are stamped 04:00Z (00:00 ET); a session is closed by 20:00Z
        return df[df.timestamp + pd.Timedelta(hours=16) <= NOW].reset_index(drop=True)
    return df[df.timestamp + pd.Timedelta(seconds=TF[tf]) <= NOW].reset_index(drop=True)


summary = {}
for name, sym, tf, fn, acct_sf in LEGS:
    vc = cfgs[name]
    cfg = {"symbol": sym, "timeframe": tf, **vc, "strategy_label": name}
    closed = closed_frame(name, sym, tf)
    sigs, reasons, n_post = [], collections.Counter(), 0
    for i in range(199, len(closed)):
        w = closed.iloc[i - 199: i + 1]
        p = ev(fn, cfg, w)
        ts = closed.timestamp[i]
        post = ts + pd.Timedelta(seconds=TF[tf]) > SINCE
        n_post += post
        if isinstance(p, dict):
            sigs.append((str(ts), p["direction"], p["confidence"], valid(name, acct_sf, p["direction"])))
        elif post:
            r = p.split(": ", 1)[1]
            reasons[r.split(" (close=")[0].split(" — ")[0][:60]] += 1
    vs = [s for s in sigs if s[3]]
    post_v = [s for s in vs if pd.Timestamp(s[0]) + pd.Timedelta(seconds=TF[tf]) > SINCE]
    print(f"\n=== {name} [{sym} {tf}] decision_bar={vc.get('decision_bar', 'forming')} "
          f"leg side_filter={vc.get('side_filter', 'both')} account side_filter={acct_sf}")
    print(f"  closed bars evaluated {len(closed) - 199} ({closed.timestamp[199]} .. {closed.timestamp.iloc[-1]}); "
          f"valid signals {len(vs)} ({len(vs) / (len(closed) - 199):.4f}/bar)")
    print(f"  since 09-28T03:35Z: {n_post} closed bars, {len(post_v)} valid signals; "
          f"reasons {dict(reasons)}")
    print(f"  last valid closed-bar signal: {vs[-1] if vs else None}; "
          f"last any-side: {sigs[-1] if sigs else None}")

    # forming-bar replay at 15m checkpoints
    fsig, nchk = [], 0
    if vc.get("decision_bar", "forming") == "forming":
        m15 = load(sym, "15m")
        for k in range(len(m15)):
            t_end = m15.timestamp[k] + pd.Timedelta(minutes=15)
            if t_end > NOW or t_end <= SINCE:
                continue
            if tf == "1d":
                bopen = t_end.normalize() + pd.Timedelta(hours=4)
                part = m15[(m15.timestamp >= t_end.normalize()) & (m15.timestamp < t_end)]
            else:
                bopen = t_end.floor(f"{TF[tf]}s")
                if t_end == bopen:
                    continue
                part = m15[(m15.timestamp >= bopen) & (m15.timestamp < t_end)]
            if part.empty:
                continue
            hist = closed[closed.timestamp < bopen].iloc[-199:]
            fb = pd.DataFrame([{"timestamp": bopen, "open": part.open.iloc[0],
                                "high": part.high.max(), "low": part.low.min(),
                                "close": part.close.iloc[-1], "volume": part.volume.sum()}])
            p = ev(fn, cfg, pd.concat([hist, fb]))
            nchk += 1
            if isinstance(p, dict):
                fsig.append((str(t_end), p["direction"], p["confidence"], valid(name, acct_sf, p["direction"])))
        fv = [s for s in fsig if s[3]]
        print(f"  forming-bar 15m checkpoints since 09-28T03:35Z: {nchk}, signals {len(fsig)}, valid {len(fv)} {fv[:5]}")
    summary[name] = dict(closed_post=n_post, sig_post=len(post_v),
                         forming_chk=nchk, forming_valid=len([s for s in fsig if s[3]]),
                         last_valid=vs[-1][0] if vs else None)

    # ---- distance to signal on the NEXT bar (latest closed bar becomes prev) ----
    df = closed.iloc[-250:].reset_index(drop=True)
    p = {**{"trend_lookback": 50, "pullback_lookback": 10, "pullback_frac": 0.33,
            "atr_period": 14, "adx_min": None, "donchian": 20, "min_confidence": 0.0}, **vc}
    atr = float(_atr(df, int(p["atr_period"])).iloc[-1])
    last = float(df.close.iloc[-1])
    print(f"  NOW: last closed close {last} @ {df.timestamp.iloc[-1]}, ATR {atr:.6g} ({atr / last * 100:.2f}%)")
    if fn is pb_pkg:
        tl, pl, pf = int(p["trend_lookback"]), int(p["pullback_lookback"]), float(p["pullback_frac"])
        # next bar: midline/range windows end at the latest closed bar
        mid = (df.high.iloc[-tl:].max() + df.low.iloc[-tl:].min()) / 2
        rhi, rlo = df.high.iloc[-pl:].max(), df.low.iloc[-pl:].min()
        rng = rhi - rlo
        long_hi = rlo + pf * rng          # long needs close <= this
        long_lo = max(mid, last)          # ... and > midline and > prev close
        short_lo = rhi - pf * rng         # short needs close >= this
        short_hi = min(mid, last)         # ... and < midline and < prev close
        adx = float(_adx(df, 14).iloc[-1])
        print(f"  pullback next-bar: midline {mid:.6g} (last is {(last - mid) / atr:+.2f} ATR from it); "
              f"range [{rlo:.6g},{rhi:.6g}] pos_in_range(last)={(last - rlo) / rng:.2f}; pull_frac {pf}")
        for side, lo_, hi_ in (("LONG", long_lo, long_hi), ("SHORT", short_lo, short_hi)):
            ok = lo_ < hi_
            print(f"    {side}: close must be in ({lo_:.6g}, {hi_:.6g}] -> "
                  + (f"window open, {min(abs(last - lo_), abs(last - hi_)) / atr:.2f} ATR from last" if ok
                     else f"EMPTY window (structurally impossible next bar; gap {(lo_ - hi_) / atr:.2f} ATR "
                          f"= {(lo_ - hi_) / last * 100:.2f}%)"))
        if p.get("adx_min"):
            print(f"    ADX(14) now {adx:.1f} vs adx_min {p['adx_min']} -> {'PASS' if adx >= float(p['adx_min']) else 'FAIL'}")
        if float(p.get("vol_skip_below_pctl") or 0) > 0:
            vp = _trailing_atr_pctl(_atr(df, int(p["atr_period"])), -1, 200)
            print(f"    ATR pctl now {vp} vs vol_skip_below {p['vol_skip_below_pctl']}")
    else:
        dn, mc = int(p["donchian"]), float(p["min_confidence"])
        hi, lo = df.high.iloc[-dn:].max(), df.low.iloc[-dn:].min()
        need_l, need_s = hi + mc * atr, lo - mc * atr
        print(f"  donchian next-bar channel [{lo:.6g}, {hi:.6g}], min_conf {mc} -> long needs close > {need_l:.6g} "
              f"(+{(need_l - last) / atr:.2f} ATR, +{(need_l - last) / last * 100:.2f}%), short needs close < {need_s:.6g} "
              f"(-{(last - need_s) / atr:.2f} ATR, -{(last - need_s) / last * 100:.2f}%)")
        if p.get("vol_skip_above_pctl"):
            vp = _trailing_atr_pctl(_atr(df, int(p["atr_period"])), -1, 200)
            print(f"    ATR pctl now {vp} vs vol_skip_above {p['vol_skip_above_pctl']}")
    _ = _trend_midline  # (entry midline definition used above: max/min of prior trend_lb bars)

print("\nSUMMARY", json.dumps(summary, indent=1))
