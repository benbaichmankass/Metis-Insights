#!/usr/bin/env python3
"""Measure the divergence between the TWO ``backtest_trend.py`` engines.

WHY THIS EXISTS
---------------
``BL-20260808-TREND-HARNESS-FORK-SPLITS-FIDELITY-FROM-EVIDENCE`` and design-doc
§5e recorded the fork as a **flag-inventory** difference (30 vs 38 flags,
"neither is a superset") and proposed converging by porting flags in whichever
direction a before/after showed was behaviour-preserving.

That framing is incomplete, and this tool exists so the correction is
**measured rather than argued**: the two files are not one engine with two flag
sets, they are two ENGINES that disagree about *which trades exist*. Running
both over identical candles with every optional lever OFF still yields
different trade counts, because they differ on:

* **trail ATR basis** — ``scripts/backtest_trend.py`` freezes the ENTRY bar's
  ATR for the whole trade; ``scripts/research/backtest_trend.py`` multiplies
  the CURRENT bar's rolling ATR each managed bar;
* **opposite-signal (flip) exit** — present only in the research engine;
* **cooldown bars** after an exit — present only in the pipeline engine;
* **fee basis** — average of entry/exit price vs entry price only;
* warm-up length, ``timeout_bars`` semantics, and the win-rate denominator
  (gross R > 0 vs net-of-fee R > 0).

WHICH ENGINE IS LIVE-FAITHFUL (the fact that decides the convergence direction)
------------------------------------------------------------------------------
``src/units/strategies/trend_donchian.py`` freezes the entry ATR into the order
package's ``meta["atr"]`` and its ``monitor()`` trails off that frozen value —
the rolling recompute there is a legacy fallback for packages missing the key.
The code says so at the write site: *"matching the backtest's fixed-ATR trail
(scripts/backtest_trend.py uses the entry bar's ATR for the whole trade).
Without this the live trail would drift with a rolling ATR and diverge from
what was validated."*

So on the trail's ATR basis — the load-bearing exit semantic for a strategy
whose only profit exit IS the trail — **live matches the pipeline copy**, and
the research copy is the one that "drifts with a rolling ATR".

WHAT THE NUMBERS HERE ARE, AND ARE NOT
--------------------------------------
Run against the committed ``data/backtest_candles.csv`` this covers **BTCUSDT,
2022-07-23 → 2022-07-27 (~3.5 days), n = 6..56 trades per configuration**. That
is enough to establish that the axis is FIRST-ORDER (tens of percent of gross
R, and sign-unstable across configurations) and nowhere near enough to estimate
its magnitude. Point ``--data`` at real history on a runner or the trainer for
a decision-grade figure. Every printed block states its own n.

Observe-only, Tier-1: reads candles, imports both harness engines, writes
nothing outside its own ``--json`` output.

Usage::

    python scripts/research/trend_harness_divergence.py
    python scripts/research/trend_harness_divergence.py --data /tmp/btc_1h.csv \\
        --resample 1h --configs 20:200,30:200 --json out.json
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sys
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

_REPO_ROOT = os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

PIPELINE_REL = "scripts/backtest_trend.py"
RESEARCH_REL = "scripts/research/backtest_trend.py"


def _load_engine(mod_name: str, rel: str):
    """Import one harness copy under a distinct module name (same basename)."""
    spec = importlib.util.spec_from_file_location(
        mod_name, os.path.join(_REPO_ROOT, rel))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


def declared_flags(rel: str) -> set:
    """The CLI flags a harness copy actually declares (argparse call sites)."""
    with open(os.path.join(_REPO_ROOT, rel), encoding="utf-8") as fh:
        src = fh.read()
    return set(re.findall(r"add_argument\(\s*['\"](--[a-z0-9-]+)['\"]", src))


def load_candles(path: str, resample: Optional[str]) -> pd.DataFrame:
    df = pd.read_csv(path) if not path.endswith(".parquet") else pd.read_parquet(path)
    cols = {c.lower(): c for c in df.columns}
    if "timestamp" not in cols and "ts" in cols:
        df = df.rename(columns={cols["ts"]: "timestamp"})
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
    df = df.dropna(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
    if resample:
        rule = resample.strip().lower()
        if rule.endswith("m") and not rule.endswith("min"):
            rule = rule[:-1] + "min"
        df = (df.set_index("timestamp")
              .resample(rule, label="right", closed="right")
              .agg({"open": "first", "high": "max", "low": "min", "close": "last"})
              .dropna().reset_index())
    return df


def _frozen_atr_variant(resr, df: pd.DataFrame, donchian: int, atr_p: int,
                        stop: float, trail: float, timeout: int) -> List[float]:
    """The RESEARCH engine's exit loop with ONLY the trail ATR basis frozen.

    Everything else — the flip exit, warm-up, entry rule — is the research
    engine's, so the returned R-list differs from the unmodified research run
    on exactly one axis: ``a`` is the entry bar's ATR instead of the managed
    bar's. That isolation is the point; it is not a third engine anyone runs.
    """
    atr = resr._atr(df, atr_p)
    sig = resr._signal(df, donchian)
    n, out, pos = len(df), [], None
    for i in range(max(donchian, atr_p) + 1, n):
        bar = df.iloc[i]
        hi, lo, cl = float(bar["high"]), float(bar["low"]), float(bar["close"])
        if pos is not None:
            a = pos["entry_atr"]          # <-- the single isolated change
            peak = (max(pos["peak"], hi) if pos["direction"] == "long"
                    else min(pos["peak"], lo))
            pos["peak"] = peak
            if pos["direction"] == "long":
                pos["sl"] = max(pos["sl"], peak - trail * a)
                hit = lo <= pos["sl"]
                r = ((pos["sl"] if hit else cl) - pos["entry"]) / pos["risk"]
            else:
                pos["sl"] = min(pos["sl"], peak + trail * a)
                hit = hi >= pos["sl"]
                r = (pos["entry"] - (pos["sl"] if hit else cl)) / pos["risk"]
            opp = ((sig.iloc[i] == -1 and pos["direction"] == "long")
                   or (sig.iloc[i] == 1 and pos["direction"] == "short"))
            tmo = timeout > 0 and (i - pos["entry_i"]) >= timeout
            if hit or opp or tmo:
                out.append(round(r, 6))
                pos = None
        if pos is None:
            s = int(sig.iloc[i])
            if s != 0:
                a = float(atr.iloc[i]) or 0.0
                if a <= 0:
                    continue
                d = "long" if s > 0 else "short"
                sl = cl - stop * a if d == "long" else cl + stop * a
                risk = abs(cl - sl)
                if risk <= 0:
                    continue
                pos = {"direction": d, "entry": cl, "sl": sl, "risk": risk,
                       "peak": hi if d == "long" else lo, "entry_i": i,
                       "entry_atr": a}
    return out


def compare(df: pd.DataFrame, *, donchian: int, timeout: int, atr_p: int,
            stop: float, trail: float, symbol: str, timeframe: str,
            pipe, resr) -> Dict[str, Any]:
    """One matched configuration through both engines, every lever OFF."""
    pipeline = {}
    for cooldown in (1, 0):
        pipeline[cooldown] = pipe.run_backtest(
            df.copy(), donchian=donchian, atr_period=atr_p, atr_stop_mult=stop,
            trail_mult=trail, timeout_bars=timeout, cooldown_bars=cooldown,
            timeframe=timeframe, symbol=symbol)
    research_trades = resr.backtest(df.copy(), donchian, atr_p, stop, trail,
                                    timeout, long_only=False, min_confidence=0.0)
    research = resr.summarize(
        research_trades,
        {"symbol": symbol, "timeframe": timeframe, "donchian": donchian,
         "atr_stop_mult": stop, "trail_mult": trail, "long_only": False}, df)
    frozen = _frozen_atr_variant(resr, df, donchian, atr_p, stop, trail, timeout)
    research_gross = [t.r_multiple for t in research_trades]
    outcomes: Dict[str, int] = {}
    for t in research_trades:
        outcomes[t.outcome] = outcomes.get(t.outcome, 0) + 1
    return {
        "config": {"donchian": donchian, "timeout_bars": timeout,
                   "atr_period": atr_p, "atr_stop_mult": stop,
                   "trail_mult": trail, "bars": int(len(df)),
                   "levers": "all OFF"},
        "pipeline_cooldown1": _slim(pipeline[1]),
        "pipeline_cooldown0": _slim(pipeline[0]),
        "research": _slim(research),
        "pipeline_by_outcome": pipeline[0].get("by_outcome"),
        "research_by_outcome": outcomes,
        "atr_basis_isolation": {
            "note": ("research engine, ONLY the trail ATR basis frozen to the "
                     "entry bar (the live monitor's basis); gross R, no cost"),
            "rolling_trades": len(research_gross),
            "rolling_gross_r": round(sum(research_gross), 4),
            "frozen_trades": len(frozen),
            "frozen_gross_r": round(sum(frozen), 4),
            "delta_gross_r": round(sum(frozen) - sum(research_gross), 4),
            "delta_pct_of_rolling_gross_r": (
                round((sum(frozen) - sum(research_gross))
                      / abs(sum(research_gross)) * 100, 1)
                if research_gross and sum(research_gross) else None),
        },
    }


def _slim(s: Dict[str, Any]) -> Dict[str, Any]:
    return {k: s.get(k) for k in
            ("total_trades", "trades_long", "trades_short", "net_total_r",
             "net_expectancy_r", "max_drawdown_r", "win_rate_pct")}


def _parse_configs(spec: str) -> List[Tuple[int, int]]:
    out = []
    for item in spec.split(","):
        item = item.strip()
        if not item:
            continue
        d, _, t = item.partition(":")
        out.append((int(d), int(t or 200)))
    return out


def main(argv: List[str]) -> int:
    p = argparse.ArgumentParser(
        description="Measure the two backtest_trend.py engines' divergence.")
    p.add_argument("--data", default=os.path.join(_REPO_ROOT,
                                                  "data/backtest_candles.csv"))
    p.add_argument("--resample", default="5min")
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--timeframe", default=None,
                   help="Label only (defaults to --resample).")
    p.add_argument("--configs", default="20:200,30:200",
                   help="CSV of donchian:timeout_bars pairs.")
    p.add_argument("--atr-period", type=int, default=14)
    p.add_argument("--atr-stop-mult", type=float, default=2.5)
    p.add_argument("--trail-mult", type=float, default=3.0)
    p.add_argument("--json", dest="json_out", default=None)
    a = p.parse_args(argv[1:])

    pipe = _load_engine("_bt_trend_pipeline", PIPELINE_REL)
    resr = _load_engine("_bt_trend_research", RESEARCH_REL)
    pf, rf = declared_flags(PIPELINE_REL), declared_flags(RESEARCH_REL)

    df = load_candles(a.data, a.resample)
    results = [compare(df, donchian=d, timeout=t, atr_p=a.atr_period,
                       stop=a.atr_stop_mult, trail=a.trail_mult,
                       symbol=a.symbol, timeframe=a.timeframe or a.resample,
                       pipe=pipe, resr=resr)
               for d, t in _parse_configs(a.configs)]

    payload = {
        "data": a.data, "resample": a.resample, "symbol": a.symbol,
        "bars": int(len(df)),
        "population": {
            "data_start": str(df["timestamp"].iloc[0]) if len(df) else None,
            "data_end": str(df["timestamp"].iloc[-1]) if len(df) else None,
            "configs_compared": len(results),
        },
        "flag_matrix": {
            "pipeline_file": PIPELINE_REL, "pipeline_flags": len(pf),
            "research_file": RESEARCH_REL, "research_flags": len(rf),
            "only_pipeline": sorted(pf - rf), "only_research": sorted(rf - pf),
            "shared": len(pf & rf),
        },
        "live_faithful_engine": {
            "file": PIPELINE_REL,
            "axis": "trail ATR basis",
            "evidence": ("src/units/strategies/trend_donchian.py freezes the "
                         "entry ATR into meta['atr']; monitor() trails off "
                         "that frozen value (rolling recompute is the legacy "
                         "fallback for packages missing the key)"),
        },
        "results": results,
    }

    print(f"trend-harness divergence — {a.symbol} {a.resample}, "
          f"{len(df)} bars ({payload['population']['data_start']} -> "
          f"{payload['population']['data_end']}), "
          f"{len(results)} configurations compared")
    print(f"  flags: {PIPELINE_REL} declares {len(pf)}; {RESEARCH_REL} declares "
          f"{len(rf)}; {len(pf & rf)} shared, {len(pf - rf)} pipeline-only, "
          f"{len(rf - pf)} research-only")
    for r in results:
        c = r["config"]
        print(f"\n  donchian={c['donchian']} timeout={c['timeout_bars']} "
              f"(levers {c['levers']}):")
        for label, key in (("pipeline (cooldown=1)", "pipeline_cooldown1"),
                           ("pipeline (cooldown=0)", "pipeline_cooldown0"),
                           ("research", "research")):
            s = r[key]
            print(f"    {label:<22} trades={s['total_trades']:>4} "
                  f"net_R={s['net_total_r']:>9.3f} "
                  f"exp_R={s['net_expectancy_r']:>7.3f}")
        iso = r["atr_basis_isolation"]
        print(f"    ATR-basis isolation: rolling {iso['rolling_trades']} trades "
              f"{iso['rolling_gross_r']:+.3f} gross R -> frozen "
              f"{iso['frozen_trades']} trades {iso['frozen_gross_r']:+.3f} "
              f"(delta {iso['delta_gross_r']:+.3f} R"
              + (f", {iso['delta_pct_of_rolling_gross_r']:+.1f}%)"
                 if iso["delta_pct_of_rolling_gross_r"] is not None else ")"))

    if a.json_out:
        blob = json.dumps(payload, indent=2, default=str)
        if a.json_out == "-":
            print(blob)
        else:
            with open(a.json_out, "w", encoding="utf-8") as fh:
                fh.write(blob)
            print(f"JSON -> {a.json_out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
