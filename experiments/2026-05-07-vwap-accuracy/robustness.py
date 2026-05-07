"""Robustness checks for the 2026-05-07-vwap-accuracy run.

Stand-alone script (not driven by run_experiment.py) — sweeps the two
winning filter parameters to verify the results are not overfit to
specific thresholds, and tests an alternative "looser" stack that
trades a smaller Sharpe lift for better cadence preservation.

Run:
    PYTHONPATH=. python3 experiments/2026-05-07-vwap-accuracy/robustness.py
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Callable, Optional, Dict

import pandas as pd

# Reuse the hypothesis module's helpers and baseline.
spec = importlib.util.spec_from_file_location(
    "hyp", "experiments/2026-05-07-vwap-accuracy/hypotheses.py"
)
hyp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hyp)

from scripts.training.backtest_helpers import simple_backtest, sl_tp_exit  # noqa: E402

OUT = Path("experiments/2026-05-07-vwap-accuracy/results/robustness")
OUT.mkdir(parents=True, exist_ok=True)


def main() -> None:
    ctx = {"cache_dir": "experiments/2026-05-07-vwap-accuracy/results/_cache"}
    hyp.setup(ctx)
    c = ctx["candles_1h"]
    htf = ctx["candles_4h"].copy()
    htf["ema200"] = htf["close"].ewm(span=200, adjust=False).mean()
    htf_sorted = htf.sort_values("timestamp").reset_index(drop=True)

    baseline = simple_backtest(c, hyp._vwap_baseline, sl_tp_exit, hyp.LOOKBACK_BARS, hyp.MAX_HOLD_BARS)
    print(f"Baseline: trades={baseline['trades']} sharpe={baseline['sharpe']:+.2f} "
          f"E[R]={baseline['expectancy_r']:+.4f} win={baseline['win_rate']:.2%}")

    out: Dict = {"baseline": baseline}

    # H2 slope sweep
    print("\n=== H2 slope-threshold sweep ===")
    out["h2_sweep"] = {}
    for thr in (0.003, 0.005, 0.007, 0.010, 0.015):
        def _b(window, _t=thr):
            sig = hyp._vwap_baseline(window)
            if sig is None or hyp._vwap_slope_pct(window) > _t:
                return None
            return sig
        m = simple_backtest(c, _b, sl_tp_exit, hyp.LOOKBACK_BARS, hyp.MAX_HOLD_BARS)
        out["h2_sweep"][f"{thr:.3f}"] = m
        drop = (1 - m["trades"] / baseline["trades"]) * 100
        print(f"  thr={thr:.3f}: trades={m['trades']} (drop {drop:.0f}%) "
              f"sharpe={m['sharpe']:+.2f} E[R]={m['expectancy_r']:+.4f} "
              f"win={m['win_rate']:.2%}")

    # H3 band sweep
    print("\n=== H3 EMA-band sweep (4h EMA-200) ===")
    out["h3_sweep"] = {}
    for band in (0.005, 0.010, 0.015, 0.020, 0.025, 0.030):
        def _b(window, _b=band):
            sig = hyp._vwap_baseline(window)
            if sig is None:
                return None
            ts = window["timestamp"].iloc[-1]
            idx = htf_sorted["timestamp"].searchsorted(ts, side="right") - 1
            if idx < 0:
                return None
            row = htf_sorted.iloc[idx]
            close_htf, ema = float(row["close"]), float(row["ema200"])
            if sig["direction"] == "long" and close_htf < ema * (1 - _b):
                return None
            if sig["direction"] == "short" and close_htf > ema * (1 + _b):
                return None
            return sig
        m = simple_backtest(c, _b, sl_tp_exit, hyp.LOOKBACK_BARS, hyp.MAX_HOLD_BARS)
        out["h3_sweep"][f"{band:.3f}"] = m
        drop = (1 - m["trades"] / baseline["trades"]) * 100
        print(f"  band={band:.3f}: trades={m['trades']} (drop {drop:.0f}%) "
              f"sharpe={m['sharpe']:+.2f} E[R]={m['expectancy_r']:+.4f} "
              f"win={m['win_rate']:.2%}")

    # H7 — looser stack designed for cadence preservation:
    # H3 with band=0.020 (less aggressive filter, more trades) + H2 default.
    print("\n=== H7 — looser stack (H3 band=0.020 + H2 thr=0.005) ===")

    def _h7(window):
        sig = hyp._vwap_baseline(window)
        if sig is None:
            return None
        # H2 slope
        if hyp._vwap_slope_pct(window) > 0.005:
            return None
        # H3 (looser band 2%)
        ts = window["timestamp"].iloc[-1]
        idx = htf_sorted["timestamp"].searchsorted(ts, side="right") - 1
        if idx < 0:
            return None
        row = htf_sorted.iloc[idx]
        close_htf, ema = float(row["close"]), float(row["ema200"])
        if sig["direction"] == "long" and close_htf < ema * 0.98:
            return None
        if sig["direction"] == "short" and close_htf > ema * 1.02:
            return None
        return sig

    m = simple_backtest(c, _h7, sl_tp_exit, hyp.LOOKBACK_BARS, hyp.MAX_HOLD_BARS)
    out["h7_loose_stack"] = m
    drop = (1 - m["trades"] / baseline["trades"]) * 100
    print(f"  trades={m['trades']} (drop {drop:.0f}%) sharpe={m['sharpe']:+.2f} "
          f"E[R]={m['expectancy_r']:+.4f} win={m['win_rate']:.2%}")

    # H8 — H3 (band=1%) only, but with HTF aware — already H3 default.
    # H9 — Walk-forward sanity: split data 70/30, fit on first half, eval on second.
    print("\n=== H9 — walk-forward (in-sample 2017-2021 / out-of-sample 2021+) ===")
    cut_idx = int(len(c) * 0.7)
    in_sample = c.iloc[:cut_idx].reset_index(drop=True)
    out_sample = c.iloc[cut_idx - hyp.LOOKBACK_BARS:].reset_index(drop=True)
    print(f"  in-sample: {len(in_sample)} bars "
          f"({in_sample['timestamp'].min()} → {in_sample['timestamp'].max()})")
    print(f"  out-sample: {len(out_sample)} bars "
          f"({out_sample['timestamp'].min()} → {out_sample['timestamp'].max()})")

    # H3 (1% band, the original winner) on each split.
    def _h3_only(window):
        sig = hyp._vwap_baseline(window)
        if sig is None:
            return None
        ts = window["timestamp"].iloc[-1]
        idx = htf_sorted["timestamp"].searchsorted(ts, side="right") - 1
        if idx < 0:
            return None
        row = htf_sorted.iloc[idx]
        close_htf, ema = float(row["close"]), float(row["ema200"])
        if sig["direction"] == "long" and close_htf < ema * 0.99:
            return None
        if sig["direction"] == "short" and close_htf > ema * 1.01:
            return None
        return sig

    is_base = simple_backtest(in_sample, hyp._vwap_baseline, sl_tp_exit, hyp.LOOKBACK_BARS, hyp.MAX_HOLD_BARS)
    is_h3 = simple_backtest(in_sample, _h3_only, sl_tp_exit, hyp.LOOKBACK_BARS, hyp.MAX_HOLD_BARS)
    oos_base = simple_backtest(out_sample, hyp._vwap_baseline, sl_tp_exit, hyp.LOOKBACK_BARS, hyp.MAX_HOLD_BARS)
    oos_h3 = simple_backtest(out_sample, _h3_only, sl_tp_exit, hyp.LOOKBACK_BARS, hyp.MAX_HOLD_BARS)
    out["walk_forward"] = {
        "in_sample_baseline": is_base, "in_sample_h3": is_h3,
        "out_sample_baseline": oos_base, "out_sample_h3": oos_h3,
    }
    print(f"  IN-sample  baseline: sharpe={is_base['sharpe']:+.2f} trades={is_base['trades']}")
    print(f"  IN-sample  H3:       sharpe={is_h3['sharpe']:+.2f} trades={is_h3['trades']}")
    print(f"  OUT-sample baseline: sharpe={oos_base['sharpe']:+.2f} trades={oos_base['trades']}")
    print(f"  OUT-sample H3:       sharpe={oos_h3['sharpe']:+.2f} trades={oos_h3['trades']}")

    (OUT / "robustness.json").write_text(json.dumps(out, indent=2, default=str))
    print(f"\nWrote {OUT / 'robustness.json'}")


if __name__ == "__main__":
    main()
