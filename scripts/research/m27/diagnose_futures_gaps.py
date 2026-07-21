#!/usr/bin/env python3
"""M27 Batch-2 follow-up — data-continuity diagnostic for the low futures
trade count (PB-20260721-M27-FUTURES-5M-LOWSIGNAL).

Question: is MES/MGC/MHG's 8-16 trades/yr on ict_scalp_5m genuine setup
rarity, or a stitched-ContFuture data-continuity artifact (roll-boundary
gaps / forward-filled flat bars) suppressing the rolling-window
sweep+FVG detector?

Checks, per symbol, over the full pulled history:
  1. Inter-bar time-gap histogram — flags gaps that are neither a normal
     ~5m step nor a normal overnight/weekend break (heuristic: > 6h on a
     weekday, or > 3 days spanning a weekend, is "expected"; anything else
     inside a session is "anomalous").
  2. Flat-bar runs — consecutive bars with an IDENTICAL close price. A
     genuine no-trade period on a stitched continuous future can either be
     (a) absent entirely (a time gap, case 1) or (b) forward-filled as a
     flat bar with zero volume. Long flat-bar runs are the direct
     mechanism for the degenerate q33=0.0 rolling-vol tercile edges found
     in the Batch-2 findings doc, and — more importantly — they corrupt
     ANY rolling window (sweep/swing/ATR lookback, all <= 20 bars for
     ict_scalp_5m) whose lookback spans one, since the "price action" the
     detector sees inside that window includes fabricated flat segments.
  3. Roll-boundary price discontinuities — CME quarterly futures roll near
     the 3rd Friday of Mar/Jun/Sep/Dec. Flags any single-bar return whose
     |log-return| exceeds N standard deviations of the whole series,
     labelled as roll-proximate (within 5 calendar days of a quarterly
     roll date) or not, so a splicing artifact (large jump AT the roll)
     is distinguishable from ordinary volatility.
  4. Setup-window contamination estimate — for the strategy's actual
     rolling lookbacks (sweep_lookback_bars=12, swing_lookback_bars=20,
     atr_period=14 — the max is 20), what fraction of all possible
     20-bar windows in the series contain at least one flat-bar run of
     length >= 5 or an anomalous time-gap. A high fraction is direct
     evidence the detector's inputs are corrupted for much of the series,
     not just at the edges.

Usage (trainer):
  .venv/bin/python scripts/research/m27/diagnose_futures_gaps.py \
      --csv /home/ubuntu/m27_data/MES_5m.csv --symbol MES \
      --out /home/ubuntu/m27_out_fut/MES/gap_diagnostic.json
"""
from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path

import pandas as pd

BAR_MINUTES = 5
# Quarterly futures roll clusters around the 3rd Friday of these months.
ROLL_MONTHS = (3, 6, 9, 12)
ROLL_PROXIMITY_DAYS = 5
FLAT_RUN_MIN = 5  # bars — matches the strategy's shortest meaningful lookback tier
MAX_LOOKBACK_BARS = 20  # swing_lookback_bars, the largest ict_scalp_5m rolling window


def nearest_roll_distance_days(ts: pd.Timestamp) -> int:
    """Days from ts to the nearest 3rd-Friday-of-a-roll-month (heuristic, +/- a
    few days is fine — we only need "roll week" granularity, not the exact day)."""
    candidates = []
    for year in (ts.year - 1, ts.year, ts.year + 1):
        for month in ROLL_MONTHS:
            # 3rd Friday: first Friday + 14 days, computed via weekday offset.
            first_of_month = pd.Timestamp(year=year, month=month, day=1, tz=ts.tz)
            first_friday_offset = (4 - first_of_month.weekday()) % 7
            third_friday = first_of_month + pd.Timedelta(days=first_friday_offset + 14)
            candidates.append(third_friday)
    return int(min(abs((ts - c).days) for c in candidates))


def diagnose(csv_path: Path, symbol: str) -> dict:
    df = pd.read_csv(csv_path)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
    df = df.dropna(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
    n = len(df)
    if n < 2:
        return {"symbol": symbol, "error": f"only {n} bars, cannot diagnose"}

    # 1. Time gaps
    deltas_min = df["timestamp"].diff().dt.total_seconds().dropna() / 60.0
    anomalous_gaps = []
    for i, gap_min in deltas_min.items():
        if gap_min <= BAR_MINUTES * 1.5:
            continue
        ts = df["timestamp"].iloc[i]
        is_weekend_ok = gap_min <= 3 * 24 * 60 and ts.weekday() in (0, 6)  # Mon/Sun edge of a weekend close
        is_daily_maint_ok = gap_min <= 6 * 60  # CME's ~1h daily maintenance break, generous bound
        if not (is_weekend_ok or is_daily_maint_ok):
            anomalous_gaps.append({
                "at": ts.isoformat(),
                "gap_minutes": round(float(gap_min), 1),
                "weekday": ts.day_name(),
            })

    # 2. Flat-bar runs (identical consecutive closes)
    closes = df["close"].astype(float).values
    flat_runs = []
    run_start = None
    run_len = 1
    for i in range(1, n):
        if closes[i] == closes[i - 1]:
            if run_start is None:
                run_start = i - 1
            run_len += 1
        else:
            if run_start is not None and run_len >= FLAT_RUN_MIN:
                flat_runs.append({
                    "start": df["timestamp"].iloc[run_start].isoformat(),
                    "end": df["timestamp"].iloc[i - 1].isoformat(),
                    "bars": run_len,
                })
            run_start = None
            run_len = 1
    if run_start is not None and run_len >= FLAT_RUN_MIN:
        flat_runs.append({
            "start": df["timestamp"].iloc[run_start].isoformat(),
            "end": df["timestamp"].iloc[n - 1].isoformat(),
            "bars": run_len,
        })
    total_flat_bars = sum(r["bars"] for r in flat_runs)

    # 3. Roll-boundary discontinuities
    log_ret = (df["close"].astype(float) / df["close"].astype(float).shift(1)).apply(
        lambda r: None if r is None or r <= 0 else __import__("math").log(r))
    log_ret = log_ret.dropna()
    if len(log_ret) > 1:
        mean_r = statistics.mean(log_ret)
        std_r = statistics.pstdev(log_ret) or 1e-9
        spikes = []
        for i, r in log_ret.items():
            z = abs((r - mean_r) / std_r)
            if z >= 6.0:
                ts = df["timestamp"].iloc[i]
                spikes.append({
                    "at": ts.isoformat(),
                    "log_return": round(float(r), 5),
                    "z_score": round(float(z), 2),
                    "days_from_nearest_roll": nearest_roll_distance_days(ts),
                })
    else:
        spikes = []
    roll_proximate_spikes = [s for s in spikes if s["days_from_nearest_roll"] <= ROLL_PROXIMITY_DAYS]

    # 4. Setup-window contamination estimate (rolling 20-bar windows)
    contaminated_windows = 0
    total_windows = max(0, n - MAX_LOOKBACK_BARS)
    if total_windows > 0:
        # A window is contaminated if it contains >= FLAT_RUN_MIN consecutive
        # identical closes anywhere inside it (cheap approximate check: any
        # flat run whose [start,end] bar-index range overlaps the window).
        flat_index_ranges = []
        run_start = None
        run_len = 1
        for i in range(1, n):
            if closes[i] == closes[i - 1]:
                if run_start is None:
                    run_start = i - 1
                run_len += 1
            else:
                if run_start is not None and run_len >= FLAT_RUN_MIN:
                    flat_index_ranges.append((run_start, i - 1))
                run_start = None
                run_len = 1
        if run_start is not None and run_len >= FLAT_RUN_MIN:
            flat_index_ranges.append((run_start, n - 1))

        for w_start in range(total_windows):
            w_end = w_start + MAX_LOOKBACK_BARS
            if any(not (r_end < w_start or r_start > w_end) for r_start, r_end in flat_index_ranges):
                contaminated_windows += 1

    return {
        "symbol": symbol,
        "total_bars": n,
        "date_range": [df["timestamp"].iloc[0].isoformat(), df["timestamp"].iloc[-1].isoformat()],
        "anomalous_time_gaps": {
            "count": len(anomalous_gaps),
            "examples": anomalous_gaps[:20],
        },
        "flat_bar_runs": {
            "count": len(flat_runs),
            "total_flat_bars": total_flat_bars,
            "pct_of_series": round(100.0 * total_flat_bars / n, 2),
            "longest_runs": sorted(flat_runs, key=lambda r: -r["bars"])[:10],
        },
        "roll_boundary_spikes": {
            "count_all": len(spikes),
            "count_roll_proximate": len(roll_proximate_spikes),
            "examples": sorted(spikes, key=lambda s: -s["z_score"])[:10],
        },
        "setup_window_contamination": {
            "total_20bar_windows": total_windows,
            "contaminated_windows": contaminated_windows,
            "pct_contaminated": round(100.0 * contaminated_windows / total_windows, 2) if total_windows else None,
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    result = diagnose(Path(args.csv), args.symbol)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2))

    print(f"=== {args.symbol} ===")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
