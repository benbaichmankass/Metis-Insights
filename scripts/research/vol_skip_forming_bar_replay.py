#!/usr/bin/env python3
"""Replay the M21 entry vol-skip gate on a FORMING bar vs a CLOSED bar.

RQ-20260929-002 / RULE-RQ0929-002-VOLSKIP-FORMING / PI-20260929-EXITOPS-0005.

Live, the pullback and donchian variant builders fetch 200 bars whose last row
is the still-forming bar, and the vol gate ranks THAT bar's ATR within the
trailing 200-bar window. The Stage-0 harnesses rank the CLOSED trigger bar.
This script rebuilds, for every bar, the percentile each arm's gate reads and
runs the Stage-0 harness (config-exact, full cost stack) once per arm through
``run_backtest(vol_pctl_override=...)``, so the only thing that differs between
arms is the percentile the gate sees.

Arms (see the queue unit for the rule):
  A_closed  harness as-is (closed trigger bar, full-history ATR)
  F_<k>     live frame = 199 closed bars + bar i truncated to its first k
            minutes (from 1m data), ranked with the LIVE unit's own _atr /
            _trailing_atr_pctl
  L_lag1    live frame of 200 CLOSED bars ending at i-1 — what
            drop_forming_bar() in the gate reads live (with enough bars fetched)
  N_off     gate undefined — what drop_forming_bar() in the gate reads if the
            fetch stays at 200 bars (199 < window 200)

Input: Binance USD-M 1m kline zips (``<SYM>-1m-YYYY-MM[-DD].zip``) in
``--klines-dir``. Output: one JSON evidence record.
"""
from __future__ import annotations

import argparse
import glob
import io
import json
import os
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from src.runtime import execution_costs  # noqa: E402

import backtest_pullback  # noqa: E402
import backtest_trend  # noqa: E402
from src.units.strategies import htf_pullback_trend_2h as _pb_unit  # noqa: E402
from src.units.strategies import trend_donchian as _td_unit  # noqa: E402

TF_MIN = {"2h": 120, "4h": 240}
WINDOW = 200
OFFSETS = (2, 8, 30, 60)
PRIMARY_K = 8

# Config-exact to config/strategies.yaml as of 2026-09-29 (re-read by
# _assert_config_exact before any run — the script refuses on drift).
LEGS: Dict[str, Dict[str, Any]] = {
    "ada_pullback_2h": {
        "symbol": "ADAUSDT", "timeframe": "2h", "harness": "pullback",
        "unit": _pb_unit,
        "gate": {"vol_skip_below_pctl": 0.1},
        "kwargs": dict(trend_lookback=40, pullback_lookback=10,
                       pullback_frac=0.5, atr_period=14, atr_stop_mult=1.5,
                       trail_mult=5.0, timeout_bars=200, cooldown_bars=1,
                       min_confidence=0.0, adx_min=28.0, tp_r=4.0,
                       tp_cap_pct=0.099),
        "yaml_keys": {"trend_lookback": 40, "pullback_lookback": 10,
                      "pullback_frac": 0.5, "atr_period": 14,
                      "atr_stop_mult": 1.5, "trail_mult": 5.0,
                      "min_confidence": 0.0, "adx_min": 28, "tp_r": 4,
                      "vol_skip_below_pctl": 0.1},
    },
    "trend_donchian_xrp_4h": {
        "symbol": "XRPUSDT", "timeframe": "4h", "harness": "trend",
        "unit": _td_unit,
        "gate": {"vol_skip_above_pctl": 0.9},
        "kwargs": dict(donchian=20, atr_period=14, atr_stop_mult=2.0,
                       trail_mult=5.0, timeout_bars=200, cooldown_bars=1,
                       min_confidence=0.80, side_filter="short",
                       stale_exit_bars=8, stale_exit_below_r=0.0, tp_r=3.0,
                       tp_cap_pct=0.099, skip_hours="0"),
        "yaml_keys": {"donchian": 20, "atr_period": 14, "atr_stop_mult": 2,
                      "trail_mult": 5.0, "min_confidence": 0.80,
                      "side_filter": "short", "stale_exit_bars": 8,
                      "tp_r": 3, "skip_hours": "0",
                      "vol_skip_above_pctl": 0.9},
    },
}


def _assert_config_exact() -> None:
    import yaml
    cfg = yaml.safe_load((ROOT / "config/strategies.yaml").read_text())
    for leg, spec in LEGS.items():
        block = cfg["strategies"][leg] if "strategies" in cfg else cfg[leg]
        for k, v in spec["yaml_keys"].items():
            got = block.get(k)
            if str(got) != str(v) and not (
                    isinstance(v, (int, float)) and got is not None
                    and float(got) == float(v)):
                raise SystemExit(f"config drift: {leg}.{k} yaml={got!r} "
                                 f"script={v!r} — update LEGS first")


def load_1m(klines_dir: str, symbol: str) -> pd.DataFrame:
    frames = []
    for path in sorted(glob.glob(os.path.join(klines_dir, f"{symbol}-1m-*.zip"))):
        with zipfile.ZipFile(path) as zf:
            raw = zf.read(zf.namelist()[0]).decode()
        first = raw.split("\n", 1)[0]
        header = 0 if first.startswith("open_time") else None
        df = pd.read_csv(io.StringIO(raw), header=header, usecols=range(5))
        df.columns = ["open_time", "open", "high", "low", "close"]
        frames.append(df)
    m = pd.concat(frames, ignore_index=True)
    m = m.drop_duplicates("open_time").sort_values("open_time")
    m["ts"] = pd.to_datetime(m["open_time"], unit="ms", utc=True)
    return m.reset_index(drop=True)


def build_bars(m1: pd.DataFrame, tf_min: int) -> pd.DataFrame:
    """[open, open+tf) bars stamped at OPEN time (Bybit kline convention);
    incomplete bars (missing 1m rows) are dropped."""
    key = m1["ts"].dt.floor(f"{tf_min}min")
    g = m1.groupby(key)
    bars = pd.DataFrame({"open": g["open"].first(), "high": g["high"].max(),
                         "low": g["low"].min(), "close": g["close"].last(),
                         "n1m": g["open"].size()})
    bars = bars[bars["n1m"] >= tf_min * 0.95].drop(columns="n1m")
    return bars.reset_index().rename(columns={"ts": "timestamp"})


def partial_bars(m1: pd.DataFrame, bars: pd.DataFrame, tf_min: int,
                 k: int) -> pd.DataFrame:
    """Bar i as the live fetch sees it k minutes after it opened."""
    key = m1["ts"].dt.floor(f"{tf_min}min")
    mins = ((m1["ts"] - key).dt.total_seconds() // 60).astype(int)
    sub = m1[mins < k]
    g = sub.groupby(key[mins < k])
    p = pd.DataFrame({"high": g["high"].max(), "low": g["low"].min(),
                      "close": g["close"].last()})
    return p.reindex(pd.DatetimeIndex(bars["timestamp"])).reset_index(drop=True)


def live_frame_pctls(bars: pd.DataFrame, partial: pd.DataFrame | None,
                     unit: Any, atr_period: int, lag: int = 0) -> np.ndarray:
    """Per bar i: the percentile the live gate reads, computed by the LIVE
    unit's own ``_atr`` + ``_trailing_atr_pctl`` on the 200-row live frame.
    partial=None → the frame's last row is CLOSED bar i-lag; else it is
    bar i truncated to its first k minutes (live fetch k minutes in)."""
    base = bars[["timestamp", "open", "high", "low", "close"]].reset_index(drop=True)
    out = np.full(len(bars), np.nan)
    if partial is not None:
        P = partial[["high", "low", "close"]].to_numpy(float)
    for i in range(WINDOW, len(bars)):
        if partial is None:
            end = i - lag
            fr = base.iloc[end - WINDOW + 1:end + 1].reset_index(drop=True)
        else:
            if np.isnan(P[i][0]):
                continue
            fr = base.iloc[i - WINDOW + 1:i + 1].reset_index(drop=True).copy()
            fr.loc[WINDOW - 1, ["high", "low", "close"]] = P[i]
        v = unit._trailing_atr_pctl(unit._atr(fr, atr_period), -1, WINDOW)
        out[i] = np.nan if v is None else v
    return out


def _set_costs(mod: Any, symbol: str) -> Dict[str, float]:
    mod.FEE_BPS_ROUNDTRIP = execution_costs.DEFAULT_FEE_BPS_ROUNDTRIP
    mod.SLIPPAGE_BPS_ROUNDTRIP = execution_costs.slippage_bps_roundtrip_for(symbol)
    mod.FUNDING_BPS_PER_WINDOW = execution_costs.funding_bps_per_window_for(symbol)
    mod.FUNDING_WINDOW_HOURS = execution_costs.FUNDING_WINDOW_HOURS
    return {"fee_bps_roundtrip": mod.FEE_BPS_ROUNDTRIP,
            "slippage_bps_roundtrip": mod.SLIPPAGE_BPS_ROUNDTRIP,
            "funding_bps_per_window": mod.FUNDING_BPS_PER_WINDOW}


def run_arm(leg: str, spec: Dict[str, Any], bars: pd.DataFrame,
            override: np.ndarray | None, gate_on: bool, tmp: Path) -> List[dict]:
    mod = backtest_pullback if spec["harness"] == "pullback" else backtest_trend
    emit = tmp / f"{leg}.jsonl"
    kw = dict(spec["kwargs"])
    if gate_on:
        kw.update(spec["gate"])
    mod.run_backtest(bars[["timestamp", "open", "high", "low", "close"]].copy(),
                     timeframe=spec["timeframe"], symbol=spec["symbol"],
                     emit_path=str(emit), strategy_name=leg,
                     vol_pctl_override=(None if override is None
                                        else list(override)),
                     **kw)
    return [json.loads(x) for x in emit.read_text().splitlines() if x.strip()]


def _summ(trades: List[dict]) -> Dict[str, Any]:
    return {"n": len(trades),
            "net_r": round(sum(t["net_r"] for t in trades), 4),
            "gross_r": round(sum(t["gross_r"] for t in trades), 4)}


def _diff(a: List[dict], b: List[dict]) -> Dict[str, Any]:
    ka = {t["entry_time"]: t for t in a}
    kb = {t["entry_time"]: t for t in b}
    only_a = [ka[k] for k in sorted(set(ka) - set(kb))]
    only_b = [kb[k] for k in sorted(set(kb) - set(ka))]
    return {"only_in_closed": _summ(only_a), "only_in_arm": _summ(only_b),
            "only_in_closed_entries": [(t["entry_time"], t["net_r"]) for t in only_a],
            "only_in_arm_entries": [(t["entry_time"], t["net_r"]) for t in only_b]}


def _gate_skip(p: float, gate: Dict[str, float]) -> bool | None:
    if p is None or np.isnan(p):
        return False  # undefined never skips (fail-permissive)
    if gate.get("vol_skip_below_pctl") and p < gate["vol_skip_below_pctl"]:
        return True
    if gate.get("vol_skip_above_pctl") and p > gate["vol_skip_above_pctl"]:
        return True
    return False


def replay_leg(leg: str, spec: Dict[str, Any], klines_dir: str,
               tmp: Path) -> Dict[str, Any]:
    tf_min = TF_MIN[spec["timeframe"]]
    m1 = load_1m(klines_dir, spec["symbol"])
    bars = build_bars(m1, tf_min)
    atr_p = spec["kwargs"]["atr_period"]

    # A_closed's own series, exactly as the harness computes it.
    atr_full = backtest_pullback._atr(bars, atr_p)
    s_closed = atr_full.rolling(WINDOW, min_periods=WINDOW).rank(pct=True).to_numpy()
    series: Dict[str, np.ndarray | None] = {"A_closed": None}
    for k in OFFSETS:
        series[f"F_{k}"] = live_frame_pctls(
            bars, partial_bars(m1, bars, tf_min, k), spec["unit"], atr_p)
    series["L_lag1"] = live_frame_pctls(bars, None, spec["unit"], atr_p, lag=1)
    series["C_liveframe"] = live_frame_pctls(bars, None, spec["unit"], atr_p)
    series["N_off"] = np.full(len(bars), np.nan)

    mod = backtest_pullback if spec["harness"] == "pullback" else backtest_trend
    costs = _set_costs(mod, spec["symbol"])

    nogate = run_arm(leg, spec, bars, None, False, tmp)
    trades = {name: run_arm(leg, spec, bars, s, True, tmp)
              for name, s in series.items()}

    # Gate-decision agreement over the no-gate run's entry bars (the bars
    # where the signal fires and the gate is the deciding input).
    idx_of = {str(ts): i for i, ts in enumerate(bars["timestamp"])}
    sig_idx = [idx_of[t["entry_time"]] for t in nogate if t["entry_time"] in idx_of]
    ref = [_gate_skip(s_closed[i], spec["gate"]) for i in sig_idx]
    arms: Dict[str, Any] = {}
    for name, s in series.items():
        s_eff = s_closed if s is None else s
        dec = [_gate_skip(s_eff[i], spec["gate"]) for i in sig_idx]
        agree = sum(1 for x, y in zip(dec, ref) if x == y)
        arms[name] = {
            **_summ(trades[name]),
            "gate_skips_on_signal_bars": int(sum(dec)),
            "agreement_with_closed": round(agree / len(sig_idx), 4) if sig_idx else None,
            "diff_vs_closed": _diff(trades["A_closed"], trades[name]),
        }
    oos = {name: _summ([t for t in tr if t["entry_time"] >= "2026-01-01"])
           for name, tr in trades.items()}
    return {
        "symbol": spec["symbol"], "timeframe": spec["timeframe"],
        "gate": spec["gate"], "harness_kwargs": spec["kwargs"], "costs": costs,
        "bars": len(bars), "first_bar": str(bars["timestamp"].iloc[0]),
        "last_bar": str(bars["timestamp"].iloc[-1]),
        "signal_bars_no_gate": len(sig_idx),
        "no_gate": _summ(nogate), "arms": arms, "oos_2026": oos,
    }


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--klines-dir", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--leg", action="append", default=None)
    args = ap.parse_args(argv)
    _assert_config_exact()
    tmp = Path(args.out).with_suffix(".tmp.d")
    tmp.mkdir(parents=True, exist_ok=True)
    legs = args.leg or list(LEGS)
    rec = {
        "unit": "RQ-20260929-002", "rule": "RULE-RQ0929-002-VOLSKIP-FORMING",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "data_source": "Binance USD-M perp 1m klines (data.binance.vision); "
                       "proxy for Bybit (api.bybit.com geo-blocked from the "
                       "research container)",
        "primary_offset_min": PRIMARY_K,
        "legs": {leg: replay_leg(leg, LEGS[leg], args.klines_dir, tmp) for leg in legs},
    }
    for f in tmp.glob("*"):
        f.unlink()
    tmp.rmdir()
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(rec, indent=1, default=str) + "\n")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
