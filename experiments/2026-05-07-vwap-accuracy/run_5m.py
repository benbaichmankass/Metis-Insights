"""Run the 6 hypotheses + robustness sweeps at the 5m timeframe.

Lives next to ``hypotheses.py`` so it can import it directly. Designed
to be invoked from ``.github/workflows/training-rerun-5m.yml`` after
``fetch_data.py`` has populated ``results/5m/_cache/`` with the BTCUSDT
5m and 4h parquet files.

Outputs:
    results/5m/SUMMARY.md
    results/5m/H{1..6}/{summary.md, metrics.json}
    results/5m/robustness/robustness.json

The 1h artifacts under ``results/`` are left untouched.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

# Make sure the env-var override is in effect *before* hypotheses.py is loaded.
os.environ.setdefault("VWAP_EXPERIMENT_TIMEFRAME", "5m")

# Resolve repo root so the in-tree imports inside hypotheses.py work
# (pkg path: scripts.training.*, src.units.strategies.vwap).
REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("hyp", HERE / "hypotheses.py")
hyp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hyp)

from scripts.training.backtest_helpers import simple_backtest, sl_tp_exit  # noqa: E402

OUT_ROOT = HERE / "results" / "5m"
OUT_ROOT.mkdir(parents=True, exist_ok=True)


def _write(d: Path, summary: dict) -> None:
    d.mkdir(parents=True, exist_ok=True)
    (d / "metrics.json").write_text(json.dumps(summary["metrics"], indent=2, default=str))
    (d / "summary.md").write_text(summary["summary_md"])


def main() -> None:
    ctx = {"cache_dir": str(OUT_ROOT / "_cache")}
    hyp.setup(ctx)

    rows = []
    for label, fn in hyp.HYPOTHESES:
        print(f"\n[{label}] running")
        result = fn(ctx)
        _write(OUT_ROOT / label, result)
        m = result["metrics"]
        b = result.get("baseline_metrics", {})
        rows.append((label, b, m))

    # SUMMARY.md
    head = (
        "# Run 2026-05-07-vwap-accuracy — 5m re-run\n\n"
        "Timeframe: **5m** (production cadence). Symbol: BTCUSDT.\n"
        "`scripts/training/data_loader.load_candles` provided the candles "
        "via the yfinance → Coinbase → Bybit fallback chain on a GitHub "
        "Actions runner (sandbox blocks all three).\n\n"
        "| Hypothesis | trades | win | E[R] | Sharpe |\n"
        "|---|---:|---:|---:|---:|\n"
    )
    body = []
    base = rows[0][1] if rows else {}
    if base:
        body.append(
            f"| **baseline** | {base.get('trades', 0)} | {base.get('win_rate', 0):.2%} | "
            f"{base.get('expectancy_r', 0):+.4f} | {base.get('sharpe', 0):+.2f} |"
        )
    for label, _, m in rows:
        body.append(
            f"| {label} | {m.get('trades', 0)} | {m.get('win_rate', 0):.2%} | "
            f"{m.get('expectancy_r', 0):+.4f} | {m.get('sharpe', 0):+.2f} |"
        )
    (OUT_ROOT / "SUMMARY.md").write_text(head + "\n".join(body) + "\n")

    # Robustness sweeps + walk-forward — equivalent of robustness.py at 5m.
    print("\n=== robustness sweeps (5m) ===")
    c = ctx["candles_1h"]                       # base (5m series)
    htf = ctx["candles_4h"].copy()
    htf["ema200"] = htf["close"].ewm(span=200, adjust=False).mean()
    htf_sorted = htf.sort_values("timestamp").reset_index(drop=True)
    baseline = simple_backtest(c, hyp._vwap_baseline, sl_tp_exit, hyp.LOOKBACK_BARS, hyp.MAX_HOLD_BARS)
    print(f"baseline trades={baseline['trades']} sharpe={baseline['sharpe']:+.2f} "
          f"E[R]={baseline['expectancy_r']:+.4f} win={baseline['win_rate']:.2%}")
    out = {"baseline": baseline}

    print("\nH2 slope-threshold sweep (5m)")
    out["h2_sweep"] = {}
    for thr in (0.0008, 0.0010, 0.0015, 0.0020, 0.0030):
        def _b(window, _t=thr):
            sig = hyp._vwap_baseline(window)
            if sig is None or hyp._vwap_slope_pct(window) > _t:
                return None
            return sig
        m = simple_backtest(c, _b, sl_tp_exit, hyp.LOOKBACK_BARS, hyp.MAX_HOLD_BARS)
        out["h2_sweep"][f"{thr:.4f}"] = m
        drop = (1 - m["trades"] / max(baseline["trades"], 1)) * 100
        print(f"  thr={thr:.4f}: trades={m['trades']} (drop {drop:.0f}%) "
              f"sharpe={m['sharpe']:+.2f} E[R]={m['expectancy_r']:+.4f} win={m['win_rate']:.2%}")

    print("\nH3 EMA-band sweep (5m, 4h EMA-200)")
    out["h3_sweep"] = {}
    for band in (0.005, 0.010, 0.015, 0.020, 0.025, 0.030):
        def _b(window, _band=band):
            sig = hyp._vwap_baseline(window)
            if sig is None:
                return None
            ts = window["timestamp"].iloc[-1]
            idx = htf_sorted["timestamp"].searchsorted(ts, side="right") - 1
            if idx < 0:
                return None
            row = htf_sorted.iloc[idx]
            close_htf, ema = float(row["close"]), float(row["ema200"])
            if sig["direction"] == "long" and close_htf < ema * (1 - _band):
                return None
            if sig["direction"] == "short" and close_htf > ema * (1 + _band):
                return None
            return sig
        m = simple_backtest(c, _b, sl_tp_exit, hyp.LOOKBACK_BARS, hyp.MAX_HOLD_BARS)
        out["h3_sweep"][f"{band:.3f}"] = m
        drop = (1 - m["trades"] / max(baseline["trades"], 1)) * 100
        print(f"  band={band:.3f}: trades={m['trades']} (drop {drop:.0f}%) "
              f"sharpe={m['sharpe']:+.2f} E[R]={m['expectancy_r']:+.4f} win={m['win_rate']:.2%}")

    # walk-forward 70/30
    print("\nwalk-forward (in-sample 70% / out-of-sample 30%)")
    cut_idx = int(len(c) * 0.7)
    in_sample = c.iloc[:cut_idx].reset_index(drop=True)
    out_sample = c.iloc[cut_idx - hyp.LOOKBACK_BARS:].reset_index(drop=True)

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

    rob_dir = OUT_ROOT / "robustness"
    rob_dir.mkdir(parents=True, exist_ok=True)
    (rob_dir / "robustness.json").write_text(json.dumps(out, indent=2, default=str))
    print(f"\nWrote {rob_dir / 'robustness.json'}")


if __name__ == "__main__":
    main()
