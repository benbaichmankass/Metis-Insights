import json
import sys
import pandas as pd

sys.path.insert(0, ".")
from src.units.strategies import load_strategy_config
from src.units.strategies.trend_donchian import order_package as dc
from src.units.strategies.htf_pullback_trend_2h import order_package as pb

S = sys.argv[1]
cfgs = load_strategy_config()
LEGS = [
    ("xrp_pullback_2h", "XRPUSDT", "2h", pb, 60),
    ("ada_pullback_2h", "ADAUSDT", "2h", pb, 60),
    ("trend_donchian_eth_4h", "ETHUSDT", "4h", dc, 30),
    ("trend_donchian_xrp_4h", "XRPUSDT", "4h", dc, 30),
]
NOW = pd.Timestamp("2026-10-03T05:50Z")


def load(f, tf):
    df = pd.DataFrame(json.load(open(f))["candles"])
    df["timestamp"] = pd.to_datetime(df.time, unit="s", utc=True)
    df = df[df.timestamp + pd.Timedelta(tf) <= NOW].reset_index(drop=True)
    return df[["timestamp", "open", "high", "low", "close", "volume"]]


def sigs(df, fn, cfg, name, frm):
    sf = cfgs[name].get("side_filter", "both")
    out = {}
    for i in range(199, len(df)):
        if df.timestamp[i] < frm:
            continue
        try:
            p = fn(cfg, candles_df=df.iloc[i - 199 : i + 1].reset_index(drop=True))
        except ValueError:
            out[df.timestamp[i]] = 0
            continue
        out[df.timestamp[i]] = 1 if sf in ("both", p["direction"]) else 0
    return pd.Series(out)


for name, sym, tf, fn, W in LEGS:
    cfg = {"symbol": sym, "timeframe": tf, **cfgs[name], "strategy_label": name}
    bn = sigs(
        load(f"{S}/BN_{sym}_{tf}.json", tf),
        fn,
        cfg,
        name,
        pd.Timestamp("2026-04-01", tz="UTC"),
    )
    by = sigs(
        load(f"{S}/{sym}_{tf}.json", tf),
        fn,
        cfg,
        name,
        pd.Timestamp("2026-04-01", tz="UTC"),
    )
    common = bn.index.intersection(by.index)
    agree = ((bn[common] == 1) & (by[common] == 1)).sum()
    base = bn[bn.index < pd.Timestamp("2026-09-28", tz="UTC")]
    n = len(base)
    k = int(base.sum())
    roll = base.rolling(W).sum().dropna()
    frac0 = (roll == 0).mean()
    idx = [i for i, v in enumerate(base.values) if v]
    gaps = [b - a for a, b in zip(idx, idx[1:])]
    import numpy as np

    print(
        f"{name}: Binance-spot baseline 2026-04-01..09-27: {k} signals / {n} closed bars ({k / n:.4f}/bar, ~{k / n * W:.2f} expected per {W}-bar window); "
        f"fraction of {W}-bar windows with 0 signals = {frac0:.2f}; gaps(bars) median {np.median(gaps):.0f}, p90 {np.percentile(gaps, 90):.0f}, max {max(gaps)}; "
        f"proxy check vs Bybit on {len(common)} common bars: bybit sigs {int(by[common].sum())}, binance {int(bn[common].sum())}, both {agree}; "
        f"binance since 09-28: {int(bn[bn.index >= pd.Timestamp('2026-09-28T04:00Z')].sum())}"
    )

# joint silence: daily-resolved, all four legs, 5.1-day (122h) windows
ser = {}
for name, sym, tf, fn, W in LEGS:
    cfg = {"symbol": sym, "timeframe": tf, **cfgs[name], "strategy_label": name}
    s = sigs(
        load(f"{S}/BN_{sym}_{tf}.json", tf),
        fn,
        cfg,
        name,
        pd.Timestamp("2026-04-01", tz="UTC"),
    )
    ser[name] = s.resample("2h").max().fillna(0)
J = pd.DataFrame(ser).fillna(0)
J = J[J.index < pd.Timestamp("2026-09-28", tz="UTC")]
anyv = J.max(axis=1)
r = anyv.rolling(61).sum().dropna()
print(
    f"JOINT: fraction of 122h windows (Apr-Sep) with zero signals on ALL four legs = {(r == 0).mean():.3f} over {len(r)} windows; longest joint-silent run = {max((len(list(g)) for k, g in __import__('itertools').groupby(anyv.values) if k == 0), default=0) * 2}h"
)
