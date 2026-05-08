"""Phase-2 follow-on: stacked best-of variants for VWAP and Turtle Soup.

Combines the strongest individual filters from run.py and re-validates
on the same 38-month dataset, including walk-forward.
"""
from __future__ import annotations

import json
import sys
import time
from dataclasses import asdict
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

# Re-use the engine from run.py — register in sys.modules so dataclasses works
import importlib.util
spec = importlib.util.spec_from_file_location("run_mod", HERE / "run.py")
run_mod = importlib.util.module_from_spec(spec)
sys.modules["run_mod"] = run_mod
spec.loader.exec_module(run_mod)

DATA = HERE.parent / "data" / "btc_5m.parquet"
OUT = HERE.parent / "results" / "stacked.json"


def main() -> None:
    t0 = time.time()
    btc5 = run_mod.load_5m()
    btc15 = run_mod.resample(btc5, "15min")
    btc1h = run_mod.resample(btc5, "1h")
    btc4h = run_mod.resample(btc5, "4h")

    htf_1h_ema200 = run_mod.make_htf_lookup(btc1h, 200)
    htf_4h_ema200 = run_mod.make_htf_lookup(btc4h, 200)

    in_s_5m, oos_5m = run_mod.split_70_30(btc5)
    in_s_15m, oos_15m = run_mod.split_70_30(btc15)

    results = {}

    # ===== VWAP stacked variants =====
    # VS1: HTF 1h EMA200 + band 0.020 (best HTF tf + best band)
    # VS2: VS1 + tighter SL (0.40)
    # VS3: VS2 + 2.0σ entry threshold
    print("VWAP — stacked variants")
    print("-" * 72)

    def vwap_variant(name: str, **kwargs):
        f = run_mod._make_vwap_signal_fn(btc5, **kwargs)
        m = run_mod.backtest(btc5, f, run_mod.LOOKBACK_5M, run_mod.MAX_HOLD_5M, run_mod.STEP_5M)
        return name, m

    name, m = vwap_variant("VS0_baseline")
    print(f"{name:<32} {run_mod.fmt_metrics(m)}")
    results[name] = asdict(m)

    name, m = vwap_variant("VS1_htf1h_band020", htf_close_lookup=htf_1h_ema200, htf_band=0.020)
    print(f"{name:<32} {run_mod.fmt_metrics(m)}")
    results[name] = asdict(m)

    name, m = vwap_variant("VS2_VS1_sl040", htf_close_lookup=htf_1h_ema200, htf_band=0.020, sl_mult=0.40)
    print(f"{name:<32} {run_mod.fmt_metrics(m)}")
    results[name] = asdict(m)

    name, m = vwap_variant("VS3_VS2_thr20", htf_close_lookup=htf_1h_ema200, htf_band=0.020, sl_mult=0.40, entry_thr=2.0)
    print(f"{name:<32} {run_mod.fmt_metrics(m)}")
    results[name] = asdict(m)

    # Re-test the original V1 (4h ±1%) for direct comparison
    name, m = vwap_variant("V1_orig_4h_band010", htf_close_lookup=htf_4h_ema200, htf_band=0.010)
    print(f"{name:<32} {run_mod.fmt_metrics(m)}")
    results[name] = asdict(m)

    # Walk-forward on VS1, VS2, VS3
    print("\nVWAP — walk-forward 70/30 on stacked variants")
    print("-" * 72)
    wf = {}
    cases = [
        ("VS0_baseline",       {}),
        ("VS1_htf1h_band020",  dict(htf_close_lookup=htf_1h_ema200, htf_band=0.020)),
        ("VS2_VS1_sl040",      dict(htf_close_lookup=htf_1h_ema200, htf_band=0.020, sl_mult=0.40)),
        ("VS3_VS2_thr20",      dict(htf_close_lookup=htf_1h_ema200, htf_band=0.020, sl_mult=0.40, entry_thr=2.0)),
        ("V1_orig_4h_band010", dict(htf_close_lookup=htf_4h_ema200, htf_band=0.010)),
    ]
    for name, kw in cases:
        # Note: HTF lookup must use the FULL htf series (which may extend beyond
        # the in-sample slice) but the signal fn is constructed against in_s_5m / oos_5m.
        # Since htf_*_ema200 closes are precomputed off the full 1h/4h series,
        # the in-sample test does see HTF data computed with knowledge of the full
        # series. For Phase-2 prod we need a pure walk: rebuild EMAs on the slice.
        # Approximate the leakage cost by rebuilding lookup per slice.
        is_lookup = run_mod.make_htf_lookup(run_mod.resample(in_s_5m, "1h"), 200) if "VS1" in name or "VS2" in name or "VS3" in name else \
                    run_mod.make_htf_lookup(run_mod.resample(in_s_5m, "4h"), 200) if "V1" in name else None
        oo_lookup = run_mod.make_htf_lookup(run_mod.resample(oos_5m, "1h"), 200) if "VS1" in name or "VS2" in name or "VS3" in name else \
                    run_mod.make_htf_lookup(run_mod.resample(oos_5m, "4h"), 200) if "V1" in name else None

        kw_is = dict(kw)
        kw_oo = dict(kw)
        if is_lookup is not None:
            kw_is["htf_close_lookup"] = is_lookup
            kw_oo["htf_close_lookup"] = oo_lookup

        f_is = run_mod._make_vwap_signal_fn(in_s_5m, **kw_is)
        f_oo = run_mod._make_vwap_signal_fn(oos_5m, **kw_oo)
        is_m = run_mod.backtest(in_s_5m, f_is, run_mod.LOOKBACK_5M, run_mod.MAX_HOLD_5M, run_mod.STEP_5M)
        oo_m = run_mod.backtest(oos_5m, f_oo, run_mod.LOOKBACK_5M, run_mod.MAX_HOLD_5M, run_mod.STEP_5M)
        wf[name] = {"in_sample": asdict(is_m), "out_of_sample": asdict(oo_m)}
        print(f"  {name:<25} IS  {run_mod.fmt_metrics(is_m)}")
        print(f"  {name:<25} OOS {run_mod.fmt_metrics(oo_m)}")
    results["walk_forward"] = wf

    # ===== Turtle Soup stacked =====
    print("\nTurtle Soup — stacked variants (best params from sweeps)")
    print("-" * 72)

    def turtle_variant(name: str, **kwargs):
        f = run_mod._make_turtle_signal_fn(btc15, **kwargs)
        m = run_mod.backtest(btc15, f, run_mod.LOOKBACK_15M, run_mod.MAX_HOLD_15M, run_mod.STEP_15M)
        return name, m

    name, m = turtle_variant("TS0_baseline")
    print(f"{name:<32} {run_mod.fmt_metrics(m)}")
    results[name] = asdict(m)

    # TS1: tighter atr_stop_mult (0.30) — sweet spot from T4
    name, m = turtle_variant("TS1_atr_30", params={"atr_stop_mult": 0.30})
    print(f"{name:<32} {run_mod.fmt_metrics(m)}")
    results[name] = asdict(m)

    # TS2: TS1 + tp1=2.0 (best from T5 with positive E[R])
    name, m = turtle_variant("TS2_atr30_tp20", params={"atr_stop_mult": 0.30, "tp1_at_r": 2.0})
    print(f"{name:<32} {run_mod.fmt_metrics(m)}")
    results[name] = asdict(m)

    # TS3: TS2 + body_to_range 0.55 to recover cadence
    name, m = turtle_variant("TS3_atr30_tp20_body055", params={"atr_stop_mult": 0.30, "tp1_at_r": 2.0, "min_body_to_range": 0.55})
    print(f"{name:<32} {run_mod.fmt_metrics(m)}")
    results[name] = asdict(m)

    # TS4: TS3 + atr_regime_min 0.0025
    name, m = turtle_variant("TS4_atr30_tp20_body055_regime", params={"atr_stop_mult": 0.30, "tp1_at_r": 2.0, "min_body_to_range": 0.55}, atr_regime_min=0.0025)
    print(f"{name:<32} {run_mod.fmt_metrics(m)}")
    results[name] = asdict(m)

    # Walk-forward turtle
    print("\nTurtle Soup — walk-forward 70/30")
    print("-" * 72)
    twf = {}
    tcases = [
        ("TS0_baseline", {}),
        ("TS1_atr_30", {"params": {"atr_stop_mult": 0.30}}),
        ("TS2_atr30_tp20", {"params": {"atr_stop_mult": 0.30, "tp1_at_r": 2.0}}),
        ("TS3_atr30_tp20_body055", {"params": {"atr_stop_mult": 0.30, "tp1_at_r": 2.0, "min_body_to_range": 0.55}}),
    ]
    for name, kw in tcases:
        f_is = run_mod._make_turtle_signal_fn(in_s_15m, **kw)
        f_oo = run_mod._make_turtle_signal_fn(oos_15m, **kw)
        is_m = run_mod.backtest(in_s_15m, f_is, run_mod.LOOKBACK_15M, run_mod.MAX_HOLD_15M, run_mod.STEP_15M)
        oo_m = run_mod.backtest(oos_15m, f_oo, run_mod.LOOKBACK_15M, run_mod.MAX_HOLD_15M, run_mod.STEP_15M)
        twf[name] = {"in_sample": asdict(is_m), "out_of_sample": asdict(oo_m)}
        print(f"  {name:<25} IS  {run_mod.fmt_metrics(is_m)}")
        print(f"  {name:<25} OOS {run_mod.fmt_metrics(oo_m)}")
    results["turtle_walk_forward"] = twf

    OUT.write_text(json.dumps(results, indent=2, default=str))
    print(f"\nWrote {OUT}")
    print(f"Wall-clock: {(time.time()-t0)/60:.2f} min")


if __name__ == "__main__":
    main()
