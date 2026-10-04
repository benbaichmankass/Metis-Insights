"""LIVE-SILENCE-2: historical rarity of a joint silence across ALL real-money legs.

Closed-bar signals from the LIVE units (config/strategies.yaml) on:
  * crypto legs: Binance-spot candles (data-api.binance.vision; Bybit is
    geo-blocked from the session sandbox) BN_<SYM>_<tf>.json
  * alpaca legs: the bot's own Alpaca daily feed <SYM>_1d.json
A signal counts only if it passes the leg's side_filter AND the account's
(alpaca_live: long). Usage: python3 rarity.py <candle_dir> <now_iso>
"""
import itertools
import json
import sys

import pandas as pd

sys.path.insert(0, ".")
from src.units.strategies import load_strategy_config  # noqa: E402
from src.units.strategies.htf_pullback_trend_2h import order_package as pb  # noqa: E402
from src.units.strategies.trend_donchian import order_package as dc  # noqa: E402

S, NOW = sys.argv[1], pd.Timestamp(sys.argv[2])
START = pd.Timestamp("2025-01-01", tz="UTC")
cfgs = load_strategy_config()
LEGS = [
    ("xrp_pullback_2h", "XRPUSDT", "2h", pb, "BN_XRPUSDT_2h", "both"),
    ("ada_pullback_2h", "ADAUSDT", "2h", pb, "BN_ADAUSDT_2h", "both"),
    ("trend_donchian_eth_4h", "ETHUSDT", "4h", dc, "BN_ETHUSDT_4h", "both"),
    ("trend_donchian_xrp_4h", "XRPUSDT", "4h", dc, "BN_XRPUSDT_4h", "both"),
    ("ief_pullback_1d", "IEF", "1d", pb, "IEF_1d", "long"),
    ("slv_pullback_1d", "SLV", "1d", pb, "SLV_1d", "long"),
    ("iaum_pullback_1d", "IAUM", "1d", pb, "IAUM_1d", "long"),
]
CLOSE = {"2h": pd.Timedelta(hours=2), "4h": pd.Timedelta(hours=4), "1d": pd.Timedelta(hours=16)}


def load(f, tf):
    df = pd.DataFrame(json.load(open(f"{S}/{f}.json"))["candles"])
    df["timestamp"] = pd.to_datetime(df.time, unit="s", utc=True)
    df = df[df.timestamp + CLOSE[tf] <= NOW].reset_index(drop=True)
    return df[["timestamp", "open", "high", "low", "close", "volume"]]


def signal_times(name, sym, tf, fn, f, acct_sf):
    cfg = {"symbol": sym, "timeframe": tf, **cfgs[name], "strategy_label": name}
    leg_sf = cfgs[name].get("side_filter", "both")
    df = load(f, tf)
    out, n = [], 0
    for i in range(199, len(df)):
        if df.timestamp[i] < START - pd.Timedelta(days=1):
            continue
        n += 1
        try:
            p = fn(cfg, candles_df=df.iloc[i - 199: i + 1].reset_index(drop=True))
        except ValueError:
            continue
        d = p["direction"]
        if leg_sf in ("both", d) and acct_sf in ("both", d):
            out.append(df.timestamp[i] + CLOSE[tf])  # decision time = bar close
    return out, n, df.timestamp.iloc[-1]


grid = pd.date_range(START, NOW.floor("2h"), freq="2h")
hits = {}
for name, sym, tf, fn, f, sf in LEGS:
    ts, n, last_bar = signal_times(name, sym, tf, fn, f, sf)
    s = pd.Series(0, index=grid)
    for t in ts:
        b = t.floor("2h")
        if b in s.index:
            s[b] = 1
    hits[name] = s
    base = [t for t in ts if t < pd.Timestamp("2026-09-28", tz="UTC")]
    print(f"{name}: {len(base)} valid signals on {n} closed bars 2025-01-01..2026-09-27 "
          f"(last data bar {last_bar}); last valid signal {ts[-1] if ts else None}")

H = pd.DataFrame(hits)


def runs(any_sig):
    """Lengths (hours) and end times of every maximal zero-signal run."""
    out, cur, start = [], 0, None
    for t, v in any_sig.items():
        if v == 0:
            cur += 1
            start = start or t
        else:
            if cur:
                out.append((cur * 2, start, t))
            cur, start = 0, None
    if cur:
        out.append((cur * 2, start, None))  # still running
    return out


for label, cols in (("ALL 7 real-money legs", list(H.columns)),
                    ("bybit_2 4 legs", list(H.columns)[:4]),
                    ("alpaca_live 3 legs", list(H.columns)[4:])):
    anyv = H[cols].max(axis=1)
    rr = runs(anyv)
    cur = rr[-1] if rr and rr[-1][2] is None else None
    cur_h = cur[0] if cur else 0
    hist = anyv[anyv.index < pd.Timestamp("2026-09-28", tz="UTC")]
    hist_runs = [r for r in runs(hist) if r[2] is not None]
    W = max(cur_h // 2, 1)
    roll = hist.rolling(W).sum().dropna()
    longer = sorted([r for r in hist_runs if r[0] >= cur_h], reverse=True)
    print(f"\n[{label}] current silence {cur_h}h (since {cur[1] if cur else None}); "
          f"baseline 2025-01-01..2026-09-27: {len(roll)} rolling {W * 2}h windows, "
          f"{(roll == 0).mean():.4f} fully silent; longest silent run {max(r[0] for r in hist_runs)}h; "
          f"{len(longer)} distinct silent episodes >= {cur_h}h: "
          f"{[(r[0], str(r[1])[:16]) for r in longer[:8]]}")
    for horizon in (168, 200, 240):
        roll2 = hist.rolling(horizon // 2).sum().dropna()
        print(f"    windows of {horizon}h fully silent: {(roll2 == 0).mean():.4f} of {len(roll2)}")

# proxy check: Binance vs Bybit closed-bar signals on the overlap (bot feed files)
for name, sym, tf, fn, f, sf in LEGS[:4]:
    a, _, _ = signal_times(name, sym, tf, fn, f, sf)
    b, _, _ = signal_times(name, sym, tf, fn, f"{sym}_{tf}", sf)
    lo = pd.Timestamp(json.load(open(f"{S}/{sym}_{tf}.json"))["candles"][199]["time"], unit="s", tz="UTC")
    a2, b2 = {t for t in a if t >= lo}, {t for t in b if t >= lo}
    print(f"proxy {name}: since {lo:%Y-%m-%d} bybit {len(b2)} binance {len(a2)} both {len(a2 & b2)}")
_ = itertools
