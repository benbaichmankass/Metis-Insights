#!/usr/bin/env python3
"""Fast solo R-simulator for the HF research candidates — RESEARCH-ONLY.

A lightweight per-signal R-multiple replay that mirrors
``scripts/backtest_system.py``'s SOLO exit model exactly (single position,
intrabar SL-first then TP, monitor() break-even trail, next-bar fill at the
signal bar's close proxy) so a parameter grid can be swept in seconds instead
of a full engine run per cell. Used ONLY to tune candidate params on the IS
window; the FROZEN config is then verified through the REAL engine
(scripts/backtest_system.py) for the numbers that go in the NOTE.

Faithfulness contract: this re-implements the engine's solo exit loop, NOT a
bespoke model. It is validated against the engine on one config (see the NOTE's
"sim vs engine" check) before any tuning conclusion is drawn from it.

Tier-1 research tooling — does not import or alter any live-order path.
"""
from __future__ import annotations

import argparse
import importlib
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

FEE_BPS_ROUNDTRIP = 7.5
_PANDAS_TF = {"5m": "5min", "15m": "15min", "30m": "30min", "1h": "1h", "2h": "2h", "4h": "4h"}


def _load(path: str) -> pd.DataFrame:
    df = pd.read_parquet(path) if path.endswith(".parquet") else pd.read_csv(path)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
    return df.dropna(subset=["timestamp"]).reset_index(drop=True)


def _resample(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    agg = {"open": "first", "high": "max", "low": "min", "close": "last"}
    if "volume" in df.columns:
        agg["volume"] = "sum"
    return (df.set_index("timestamp").resample(rule, label="right", closed="right")
            .agg(agg).dropna().reset_index())


def _date_filter(df, start, end):
    if start:
        df = df[df["timestamp"] >= pd.Timestamp(start, tz="UTC")]
    if end:
        df = df[df["timestamp"] <= pd.Timestamp(end, tz="UTC")]
    return df.reset_index(drop=True)


def gen_signals(name: str, module: str, tf: str, base5m: pd.DataFrame, *,
                start, end, overrides: Dict[str, Any]) -> pd.DataFrame:
    """Per-bar order_package scan (mirrors generate_signal_stream incl. the
    1h-EMA HTF injection for the HTF-gated candidates)."""
    mod = importlib.import_module(module)
    order_package = getattr(mod, "order_package")
    cfg = {"symbol": "BTCUSDT", "timeframe": tf, **overrides}
    df = _date_filter(_resample(base5m, _PANDAS_TF[tf]), start, end)

    htf_close_arr = htf_ema_arr = None
    if name in ("ict_scalp_5m", "hf_displacement_cont") and bool(cfg.get("htf_trend_filter_enabled", True)):
        htf_tf = _PANDAS_TF.get(str(cfg.get("htf_filter_timeframe") or "1h"), "1h")
        ema_period = int(cfg.get("htf_filter_ema_period") or 50)
        htf = _resample(base5m, htf_tf)
        htf["ema"] = htf["close"].ewm(span=ema_period, adjust=False).mean()
        htf = htf.dropna(subset=["ema"])
        merged = pd.merge_asof(
            df[["timestamp"]].sort_values("timestamp"),
            htf[["timestamp", "close", "ema"]].rename(
                columns={"close": "_hc", "ema": "_he"}).sort_values("timestamp"),
            on="timestamp", direction="backward")
        htf_close_arr = merged["_hc"].to_numpy()
        htf_ema_arr = merged["_he"].to_numpy()

    rows = []
    warm = 260
    ts = df["timestamp"]
    for i in range(warm, len(df)):
        window = df.iloc[max(0, i - warm):i + 1]
        bar_cfg = dict(cfg)
        if htf_close_arr is not None:
            hc, he = htf_close_arr[i], htf_ema_arr[i]
            if hc == hc and he == he:
                bar_cfg["htf_close"] = float(hc)
                bar_cfg["htf_ema"] = float(he)
        try:
            pkg = order_package(bar_cfg, candles_df=window)
        except ValueError:
            continue
        except Exception:
            continue
        rows.append({"ts": ts.iloc[i], "side": pkg["direction"], "entry": float(pkg["entry"]),
                     "sl": float(pkg["sl"]), "tp": float(pkg["tp"]),
                     "confidence": float(pkg.get("confidence", 0.0))})
    return pd.DataFrame(rows, columns=["ts", "side", "entry", "sl", "tp", "confidence"])


def simulate(signals: pd.DataFrame, clock: pd.DataFrame, *, monitor_fn=None,
             cfg: Optional[dict] = None, signal_ttl_bars: int = 1,
             be_after_1r: bool = False) -> Dict[str, Any]:
    """Solo single-position replay on the clock grid (mirrors the engine).

    Returns per-trade R list + summary. R = pnl / initial_risk (sizing-
    independent). Fees folded in as an R-equivalent at entry+exit.
    """
    ts = clock["timestamp"].values
    h = clock["high"].to_numpy(float)
    lo = clock["low"].to_numpy(float)
    c = clock["close"].to_numpy(float)
    n = len(clock)

    sig_at: Dict[int, dict] = {}
    for _, r in signals.iterrows():
        idx = int(np.searchsorted(ts, np.datetime64(pd.Timestamp(r["ts"])), side="right"))
        if idx < n:
            sig_at[idx] = r.to_dict()  # last signal on a bar wins (rare collision)

    fee_rate = FEE_BPS_ROUNDTRIP / 10_000.0
    pos = None
    latest = None
    latest_idx = -10**9
    trades: List[float] = []
    exits = {"tp": 0, "sl": 0, "monitor": 0, "eod": 0}

    for i in range(n):
        if i in sig_at:
            latest = sig_at[i]
            latest_idx = i
        if latest is not None and i - latest_idx >= signal_ttl_bars and pos is None:
            latest = None  # TTL drop only matters for opening

        if pos is not None:
            side, entry, slv, tpv, init_risk, eidx = pos
            closed_px = None; reason = None
            if side == "long":
                if lo[i] <= slv:
                    closed_px, reason = slv, "sl"
                elif h[i] >= tpv:
                    closed_px, reason = tpv, "tp"
            else:
                if h[i] >= slv:
                    closed_px, reason = slv, "sl"
                elif lo[i] <= tpv:
                    closed_px, reason = tpv, "tp"
            if closed_px is None and be_after_1r:
                # break-even-after-1R trail (mirrors monitor_breakeven_sl)
                one_r = abs(entry - slv)
                if side == "long" and c[i] >= entry + one_r and slv < entry:
                    slv = entry; pos = (side, entry, slv, tpv, init_risk, eidx)
                elif side == "short" and c[i] <= entry - one_r and slv > entry:
                    slv = entry; pos = (side, entry, slv, tpv, init_risk, eidx)
            if closed_px is not None:
                gross = (closed_px - entry) if side == "long" else (entry - closed_px)
                fee = fee_rate * (entry + closed_px)
                r = (gross - fee) / init_risk
                trades.append(r); exits[reason] += 1
                pos = None

        if pos is None and latest is not None and i == latest_idx:
            # open at this bar's close proxy (engine uses current close as fill)
            fill = c[i]
            side = latest["side"]
            slv = float(latest["sl"]); tpv = float(latest["tp"])
            init_risk = abs(fill - slv)
            if init_risk > 0:
                pos = (side, fill, slv, tpv, init_risk, i)

    if pos is not None:
        side, entry, slv, tpv, init_risk, eidx = pos
        closed_px = c[-1]
        gross = (closed_px - entry) if side == "long" else (entry - closed_px)
        fee = fee_rate * (entry + closed_px)
        trades.append((gross - fee) / init_risk); exits["eod"] += 1

    arr = np.array(trades) if trades else np.array([0.0])
    wins = int((arr > 0).sum())
    n_t = len(trades)
    return {
        "trades": n_t,
        "win_rate": round(100 * wins / n_t, 2) if n_t else 0.0,
        "E_R": round(float(arr.mean()), 4) if n_t else 0.0,
        "total_R": round(float(arr.sum()), 2),
        "exits": exits,
        "R_list": trades,
    }


def main(argv):
    p = argparse.ArgumentParser()
    p.add_argument("--data", default="/home/user/ict-trader-data/btc_5m.parquet")
    p.add_argument("--name", required=True)
    p.add_argument("--module", required=True)
    p.add_argument("--tf", default="5m")
    p.add_argument("--clock-tf", default="1h")
    p.add_argument("--start", default=None)
    p.add_argument("--end", default=None)
    p.add_argument("--be-after-1r", action="store_true")
    p.add_argument("--override", action="append", default=[])
    args = p.parse_args(argv[1:])

    overrides = {}
    for ov in args.override:
        k, v = ov.split("=", 1)
        try:
            v2 = int(v)
        except ValueError:
            try:
                v2 = float(v)
            except ValueError:
                v2 = v
        overrides[k] = v2

    base5m = _load(args.data)
    sigs = gen_signals(args.name, args.module, args.tf, base5m,
                       start=args.start, end=args.end, overrides=overrides)
    clock = _date_filter(_resample(base5m, _PANDAS_TF[args.clock_tf]), args.start, args.end)
    out = simulate(sigs, clock, be_after_1r=args.be_after_1r)
    # trades/day
    days = max((pd.Timestamp(args.end or base5m['timestamp'].iloc[-1]) -
                pd.Timestamp(args.start or base5m['timestamp'].iloc[0])).days, 1)
    out["trades_per_day"] = round(out["trades"] / days, 3)
    print(f"{args.name}: trades={out['trades']} ({out['trades_per_day']}/day) "
          f"WR={out['win_rate']}% E_R={out['E_R']} total_R={out['total_R']} exits={out['exits']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
