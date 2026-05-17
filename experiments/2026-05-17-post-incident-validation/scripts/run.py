"""Post-incident validation backtest — 2026-05-17.

Re-runs production configs (vwap + turtle_soup) for re-validation, plus
the turtle_soup cadence sweep (extended T3) and the naive 5m turtle
variant. ict_scalp_5m re-validation is delegated to
``scripts/backtest_ict_scalp.py`` by the orchestrator
(``scripts/ops/run_backtest_sweep.sh``); this script does not invoke it.

Scope + rationale: ../PLAN.md.

The vwap and turtle engine functions are imported from the
2026-05-08 harness via importlib (its directory name starts with a
digit so it cannot be a regular Python package). That harness is the
canonical source of the backtest engine; we extend variants here
without forking the engine.

Data: ``/home/ubuntu/ict-trader-data/btc_5m.parquet`` (qashdev/btc
mirror, fetched via ``scripts/ops/fetch_qashdev_btc_archive.py``).
Override with ``ICT_TRADER_DATA_ROOT`` env var.

Output:
    $OUT/all_metrics.json — every variant's Metrics dataclass dump
    $OUT/SUMMARY.md       — comparable table
    stdout                — copy of SUMMARY.md
Where ``$OUT = $ICT_TRADER_DATA_ROOT/backtests/<UTC-date>/`` by default.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict

import pandas as pd

# ---------------------------------------------------------------------------
# Load the 2026-05-08 engine via importlib (dir name is not a valid module)
# ---------------------------------------------------------------------------

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]
OLD_HARNESS_PATH = (
    REPO_ROOT
    / "experiments"
    / "2026-05-08-all-models-training"
    / "scripts"
    / "run.py"
)
if not OLD_HARNESS_PATH.exists():
    raise SystemExit(f"missing engine source: {OLD_HARNESS_PATH}")

sys.path.insert(0, str(REPO_ROOT))
_spec = importlib.util.spec_from_file_location("old_harness", OLD_HARNESS_PATH)
old = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(old)

# Reuse engine surface
load_5m = old.load_5m
resample = old.resample
backtest = old.backtest
Metrics = old.Metrics
fmt_metrics = old.fmt_metrics
make_htf_lookup = old.make_htf_lookup
_make_vwap_signal_fn = old._make_vwap_signal_fn
_make_turtle_signal_fn = old._make_turtle_signal_fn
LOOKBACK_5M = old.LOOKBACK_5M
MAX_HOLD_5M = old.MAX_HOLD_5M
STEP_5M = old.STEP_5M
LOOKBACK_15M = old.LOOKBACK_15M
MAX_HOLD_15M = old.MAX_HOLD_15M
STEP_15M = old.STEP_15M

# ---------------------------------------------------------------------------
# Persistent data + output paths (override-friendly)
# ---------------------------------------------------------------------------

DATA_ROOT = Path(os.environ.get(
    "ICT_TRADER_DATA_ROOT", "/home/ubuntu/ict-trader-data"
))
PARQUET = DATA_ROOT / "btc_5m.parquet"
TODAY_UTC = datetime.now(timezone.utc).strftime("%Y-%m-%d")
OUT = DATA_ROOT / "backtests" / TODAY_UTC
OUT.mkdir(parents=True, exist_ok=True)


def _patched_load_5m() -> pd.DataFrame:
    """``old.load_5m`` reads ``HERE / "data" / "btc_5m.parquet"`` (relative
    to the 2026-05-08 directory). We want the persistent cache instead, so
    bypass it directly."""
    df = pd.read_parquet(PARQUET)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    return df.sort_values("timestamp").reset_index(drop=True)


# ---------------------------------------------------------------------------
# Variants
# ---------------------------------------------------------------------------

# Production turtle_soup overrides vs the 2026-05-08 defaults (T_DEFAULTS):
#   atr_stop_mult: 0.35 -> 0.30 (PR #1175, 2026-05-08)
#   tp1_at_r:      1.25 -> 1.00 (PR #1184, 2026-05-09)
#   min_sweep_buffer_bps: 12 -> 10 (PR #1184, 2026-05-09)
# The other defaults (sweep_lookback=60, min_body_to_range=0.60,
# atr_period=14) match production.
TS_PROD = {
    "atr_stop_mult": 0.30,
    "tp1_at_r": 1.00,
    "min_sweep_buffer_bps": 10,
}

# Production vwap overrides vs the 2026-05-08 defaults:
#   ENTRY_THR: 1.0 -> 1.5 (PR #1205, 2026-05-15)
#   SL_MULT:   0.5 -> 0.75 (PR #1183, 2026-05-12 + ATR floor)
#   HTF gate:  band 0.02 (PR #1175, 2026-05-08 ship of V1)
V_PROD = {
    "entry_thr": 1.5,
    "sl_mult": 0.75,
    "htf_band": 0.02,
}


def main() -> int:
    t0 = time.time()
    print(f"loading {PARQUET} ...")
    if not PARQUET.exists():
        print(f"FATAL: parquet missing at {PARQUET}", file=sys.stderr)
        print(
            "Run scripts/ops/fetch_qashdev_btc_archive.py first.",
            file=sys.stderr,
        )
        return 2
    btc5 = _patched_load_5m()
    print(f"  {len(btc5):,} bars  {btc5['timestamp'].iloc[0]} -> {btc5['timestamp'].iloc[-1]}")

    btc15 = resample(btc5, "15min")
    btc4h = resample(btc5, "4h")
    print(f"  resampled: 15m={len(btc15):,}  4h={len(btc4h):,}")

    htf_4h_ema200 = make_htf_lookup(btc4h, 200)

    results: Dict = {"vwap": {}, "turtle_soup_15m": {}, "turtle_soup_5m": {}}

    # =========================================================================
    # vwap V_BASELINE and V_PROD
    # =========================================================================
    print("\n" + "=" * 72)
    print("VWAP — BTCUSDT 5m")
    print("=" * 72)

    print("[V_BASELINE]  ", end="", flush=True)
    m = backtest(btc5, _make_vwap_signal_fn(btc5), LOOKBACK_5M, MAX_HOLD_5M, STEP_5M)
    print(fmt_metrics(m))
    results["vwap"]["V_BASELINE"] = asdict(m)

    print("[V_PROD]      ", end="", flush=True)
    m = backtest(
        btc5,
        _make_vwap_signal_fn(
            btc5,
            entry_thr=V_PROD["entry_thr"],
            sl_mult=V_PROD["sl_mult"],
            htf_close_lookup=htf_4h_ema200,
            htf_band=V_PROD["htf_band"],
        ),
        LOOKBACK_5M, MAX_HOLD_5M, STEP_5M,
    )
    print(fmt_metrics(m))
    results["vwap"]["V_PROD"] = asdict(m)

    # =========================================================================
    # turtle_soup 15m — TS_PROD + T3 extended sweep
    # =========================================================================
    print("\n" + "=" * 72)
    print("Turtle Soup — BTCUSDT 15m")
    print("=" * 72)

    print("[TS_PROD]     ", end="", flush=True)
    m = backtest(
        btc15,
        _make_turtle_signal_fn(btc15, params=TS_PROD),
        LOOKBACK_15M, MAX_HOLD_15M, STEP_15M,
    )
    print(fmt_metrics(m))
    results["turtle_soup_15m"]["TS_PROD"] = asdict(m)

    print("\n[T3 extended sweep: min_sweep_buffer_bps]")
    for bps in (3, 5, 7, 10, 12):
        params = {**TS_PROD, "min_sweep_buffer_bps": bps}
        m = backtest(
            btc15,
            _make_turtle_signal_fn(btc15, params=params),
            LOOKBACK_15M, MAX_HOLD_15M, STEP_15M,
        )
        results["turtle_soup_15m"][f"T3_{bps}"] = asdict(m)
        print(f"  buffer_bps={bps:>2}: {fmt_metrics(m)}")

    # =========================================================================
    # turtle_soup 5m — naive port
    # =========================================================================
    print("\n" + "=" * 72)
    print("Turtle Soup — BTCUSDT 5m (NAIVE PORT)")
    print("=" * 72)

    # Scale bar-count params to preserve the wall-clock window. The
    # 15m harness uses LOOKBACK_15M=130, MAX_HOLD_15M=80, STEP_15M=4
    # (i.e. one entry attempt per hour). For 5m we scale by 3x:
    #   LOOKBACK_5M_TURTLE = 130*3 = 390 bars (~32h history)
    #   MAX_HOLD_5M_TURTLE =  80*3 = 240 bars (~20h)
    #   STEP_5M_TURTLE     =   4*3 =  12 bars (~1h)
    LOOKBACK_5M_TURTLE = LOOKBACK_15M * 3
    MAX_HOLD_5M_TURTLE = MAX_HOLD_15M * 3
    STEP_5M_TURTLE = STEP_15M * 3

    print("[T5M_NAIVE]   ", end="", flush=True)
    naive_params = {
        **TS_PROD,
        "sweep_lookback": 180,   # 60 * 3 — preserve ~15h window
        "atr_period": 42,        # 14 * 3 — preserve ATR horizon
    }
    m = backtest(
        btc5,
        _make_turtle_signal_fn(btc5, params=naive_params),
        LOOKBACK_5M_TURTLE, MAX_HOLD_5M_TURTLE, STEP_5M_TURTLE,
    )
    print(fmt_metrics(m))
    results["turtle_soup_5m"]["T5M_NAIVE"] = asdict(m)

    # =========================================================================
    # Persist + summary
    # =========================================================================
    metrics_path = OUT / "all_metrics.json"
    metrics_path.write_text(json.dumps(results, indent=2, default=str))
    print(f"\nwrote {metrics_path}")

    summary_path = OUT / "SUMMARY.md"
    summary = _summary_md(results)
    summary_path.write_text(summary)
    print(f"wrote {summary_path}")

    print("\n" + "=" * 72)
    print("SUMMARY")
    print("=" * 72)
    print(summary)

    print(f"\nWall-clock: {(time.time() - t0)/60:.2f} min")
    return 0


# ---------------------------------------------------------------------------
# Summary table
# ---------------------------------------------------------------------------

def _summary_md(results: Dict) -> str:
    rows = []
    rows.append("# Post-incident validation backtest — 2026-05-17")
    rows.append("")
    rows.append("Per-variant metrics. Gate criteria (per `experiments/2026-05-17-post-incident-validation/PLAN.md`):")
    rows.append("")
    rows.append("- win_rate ≥ 0.40")
    rows.append("- expectancy_r ≥ +0.20")
    rows.append("- max_dd_r ≤ 8")
    rows.append("- sharpe ≥ 0.5 per-trade (annualized ≥ 1.5 is also acceptable)")
    rows.append("")
    rows.append("| Group | Variant | Trades | Win % | E[R] | Sharpe | Max DD R | Avg hold (bars) | Gate |")
    rows.append("|---|---|---:|---:|---:|---:|---:|---:|:---:|")
    for group, variants in results.items():
        for name, m in variants.items():
            trades = m.get("trades", 0) or 0
            wr = (m.get("win_rate") or 0.0) * 100
            er = m.get("expectancy_r") or 0.0
            sh = m.get("sharpe") or 0.0
            dd = m.get("max_dd_r") or 0.0
            hold = m.get("avg_hold_bars") or 0.0
            passes = (
                wr >= 40.0
                and er >= 0.20
                and abs(dd) <= 8.0
                and sh >= 0.5
            )
            gate = "PASS" if passes else "fail" if trades > 0 else "n/a"
            rows.append(
                f"| {group} | {name} | {trades} | {wr:.1f} | {er:+.3f} | "
                f"{sh:+.2f} | {dd:+.2f} | {hold:.1f} | {gate} |"
            )
    rows.append("")
    return "\n".join(rows)


if __name__ == "__main__":
    sys.exit(main())
