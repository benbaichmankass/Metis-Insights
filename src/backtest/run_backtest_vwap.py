"""VWAP strategy backtester with HTF trend-filter parameter sweep.

Backtests the live VWAP mean-reversion strategy (build_vwap_signal) against
historical M5 candle data with support for comparing different HTF trend-filter
configurations side-by-side.

Problem context
---------------
The live config uses ``4h EMA-200`` (~800 h ≈ 33 days of look-back) which is
too slow to detect intraday reversals: the bot keeps entering longs into clear
short-term downtrends. This script compares the current config against faster
alternatives to find the sweet spot before touching config/strategies.yaml.

Usage
-----
    # Compare all built-in configs (current vs proposed vs middle vs no-filter):
    python -m src.backtest.run_backtest_vwap --compare

    # Limit to recent data (last 90 days — matches live strategy timeframe):
    python -m src.backtest.run_backtest_vwap --compare --days 90

    # Single custom run:
    python -m src.backtest.run_backtest_vwap --htf-timeframe 1h --ema-period 50

    # Disable the HTF filter entirely (baseline):
    python -m src.backtest.run_backtest_vwap --no-htf

Environment
-----------
BACKTEST_DATA_PATH   Override CSV path (default: data/backtest_candles.csv)
TRADE_JOURNAL_DB     Override SQLite path (unused here but kept for parity)

Data freshness
--------------
For meaningful results, run scripts/ops/fetch_backtest_candles.py first to
populate BACKTEST_DATA_PATH with recent 5m data that covers current market
conditions (both up and down regimes). The default data/backtest_candles.csv
in the repo is a small sample for unit tests only.

    BACKTEST_DATA_PATH=/tmp/fresh.csv \
        python scripts/ops/fetch_backtest_candles.py --days 90
    BACKTEST_DATA_PATH=/tmp/fresh.csv \
        python -m src.backtest.run_backtest_vwap --compare

Output
------
Single line of compact JSON to stdout so ``tail -1`` in wrapper scripts
works. Informational progress goes to stderr.

Trade simulation close conditions (matches vwap.monitor() priority order):
  1. SL-cross
  2. TP-cross
  3. VWAP-cross (live VWAP recomputed from rolling window at each bar)
  4. Time-decay (HOLD_BARS_MAX bars ≡ monitor_hold_window_minutes=240 min)
"""
from __future__ import annotations

import argparse
import json
import sys
import traceback
from collections import Counter
from typing import Any

import pandas as pd

from src.backtest.run_backtest import load_data
from src.units.strategies.vwap import (
    _session_anchor_slice,
    build_vwap_signal,
    compute_vwap,
)

# Match the production pipeline candle lookback fed to build_vwap_signal.
M5_LOOKBACK_BARS = 300  # ~25 h at 5 m

# monitor_hold_window_minutes = 240 min / 5 min per bar = 48 bars
HOLD_BARS_MAX = 48

# --compare sweeps these configs.  Add or remove entries freely.
COMPARE_CONFIGS: list[dict[str, Any]] = [
    {
        "label": "current (4h EMA-200)",
        "htf_timeframe": "4h",
        "ema_period": 200,
        "band_pct": 0.02,
    },
    {
        "label": "proposed (1h EMA-50)",
        "htf_timeframe": "1h",
        "ema_period": 50,
        "band_pct": 0.02,
    },
    {
        "label": "middle (4h EMA-20)",
        "htf_timeframe": "4h",
        "ema_period": 20,
        "band_pct": 0.02,
    },
    {
        "label": "no HTF filter (baseline)",
        "htf_timeframe": None,
        "ema_period": None,
        "band_pct": None,
    },
]


def _resample_to_htf(m5_df: pd.DataFrame, htf_timeframe: str) -> pd.DataFrame:
    """Resample an M5 OHLCV DataFrame to a higher timeframe.

    Uses left-closed, left-labelled periods so period ``T`` represents
    ``[T, T + freq)`` — the close of that period is the close of the
    last M5 bar whose timestamp falls in ``[T, T + freq)``.
    """
    freq_map = {"1h": "1h", "4h": "4h", "1d": "1D"}
    freq = freq_map.get(htf_timeframe, htf_timeframe)
    df = m5_df.set_index("timestamp").sort_index()
    htf = (
        df.resample(freq, closed="left", label="left")
        .agg(
            open=("open", "first"),
            high=("high", "max"),
            low=("low", "min"),
            close=("close", "last"),
            volume=("volume", "sum"),
        )
        .dropna(subset=["close"])
    )
    return htf


def _build_htf_ema(htf_df: pd.DataFrame, ema_period: int) -> pd.Series:
    """EMA over HTF closes, same index as ``htf_df``."""
    return htf_df["close"].ewm(span=ema_period, adjust=False).mean()


def _get_htf_state(
    bar_ts: pd.Timestamp,
    htf_df: pd.DataFrame,
    htf_ema: pd.Series,
    htf_period_delta: pd.Timedelta,
) -> tuple[float | None, float | None]:
    """Return (htf_close, htf_ema_val) for the most recent *completed* HTF bar.

    A period starting at ``idx`` is complete when
    ``idx + htf_period_delta <= bar_ts``.  This prevents lookahead bias
    (e.g., using the 4 h close while we're still inside that 4 h bar).
    """
    completed = htf_df.index[htf_df.index + htf_period_delta <= bar_ts]
    if len(completed) == 0:
        return None, None
    last = completed[-1]
    return float(htf_df.at[last, "close"]), float(htf_ema.at[last])


def _simulate_trade(
    df: pd.DataFrame,
    entry_idx: int,
    direction: str,
    entry: float,
    sl: float,
    tp: float,
) -> dict[str, Any] | None:
    """Forward-simulate a trade from ``entry_idx``.

    Checks SL/TP on each bar's high/low first (priority 1 & 2), then
    VWAP-cross on the bar close (priority 3). Time-decay fires after
    ``HOLD_BARS_MAX`` bars (priority 4).
    """
    risk = abs(entry - sl)
    if risk <= 0:
        return None

    exit_price: float | None = None
    exit_reason = "time_decay"
    exit_idx = min(entry_idx + HOLD_BARS_MAX, len(df) - 1)

    for j in range(entry_idx + 1, min(entry_idx + HOLD_BARS_MAX + 1, len(df))):
        bar_h = float(df["high"].iloc[j])
        bar_lo = float(df["low"].iloc[j])
        bar_c = float(df["close"].iloc[j])

        if direction == "long":
            if bar_lo <= sl:
                exit_price, exit_reason, exit_idx = sl, "sl_cross", j
                break
            if bar_h >= tp:
                exit_price, exit_reason, exit_idx = tp, "tp_cross", j
                break
        else:  # short
            if bar_h >= sl:
                exit_price, exit_reason, exit_idx = sl, "sl_cross", j
                break
            if bar_lo <= tp:
                exit_price, exit_reason, exit_idx = tp, "tp_cross", j
                break

        # VWAP-cross: recompute live VWAP on the rolling window to this bar.
        win_start = max(0, j - M5_LOOKBACK_BARS + 1)
        try:
            vwap_live = compute_vwap(_session_anchor_slice(df.iloc[win_start : j + 1]))
            if direction == "long" and bar_c >= vwap_live:
                exit_price, exit_reason, exit_idx = bar_c, "vwap_cross", j
                break
            if direction == "short" and bar_c <= vwap_live:
                exit_price, exit_reason, exit_idx = bar_c, "vwap_cross", j
                break
        except Exception:  # noqa: BLE001
            pass

    if exit_price is None:
        exit_price = float(df["close"].iloc[exit_idx])

    pnl_r = (
        (exit_price - entry) / risk
        if direction == "long"
        else (entry - exit_price) / risk
    )
    return {
        "entry_time": str(df["timestamp"].iloc[entry_idx])[:16],
        "exit_time": str(df["timestamp"].iloc[exit_idx])[:16],
        "direction": direction,
        "entry": round(entry, 2),
        "sl": round(sl, 2),
        "tp": round(tp, 2),
        "exit_price": round(exit_price, 2),
        "exit_reason": exit_reason,
        "pnl_r": round(pnl_r, 3),
        "duration_bars": exit_idx - entry_idx,
    }


def run_single(
    df: pd.DataFrame,
    htf_timeframe: str | None = "4h",
    ema_period: int | None = 200,
    band_pct: float = 0.02,
    label: str = "",
) -> dict[str, Any]:
    """Run the VWAP backtest with one HTF config.

    When ``htf_timeframe`` or ``ema_period`` is None the HTF gate is
    disabled (baseline — no trend filtering).
    """
    df = df.copy().reset_index(drop=True)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
    df = df.dropna(subset=["timestamp"]).reset_index(drop=True)

    use_htf = htf_timeframe is not None and ema_period is not None
    if use_htf:
        print(
            f"  Building HTF series: {htf_timeframe} EMA-{ema_period} …",
            file=sys.stderr,
        )
        htf_df = _resample_to_htf(df, htf_timeframe)
        htf_ema_series = _build_htf_ema(htf_df, ema_period)
        htf_period_delta = pd.Timedelta(htf_timeframe)
    else:
        htf_df = htf_ema_series = htf_period_delta = None

    trades: list[dict] = []
    blocked_count = 0
    in_trade_until = -1  # bar index; skip bars i <= in_trade_until

    for i in range(M5_LOOKBACK_BARS, len(df)):
        if i <= in_trade_until:
            continue

        win_start = max(0, i - M5_LOOKBACK_BARS + 1)
        window = df.iloc[win_start : i + 1]

        if use_htf:
            bar_ts = df["timestamp"].iloc[i]
            htf_close, htf_ema_val = _get_htf_state(
                bar_ts, htf_df, htf_ema_series, htf_period_delta
            )
        else:
            htf_close = htf_ema_val = None

        signal = build_vwap_signal(
            window,
            symbol="BTCUSDT",
            htf_close=htf_close,
            htf_ema=htf_ema_val,
            htf_band_pct=band_pct if use_htf else 0.02,
        )

        if signal.get("side") == "none":
            if use_htf and signal.get("meta", {}).get("htf_blocked"):
                blocked_count += 1
            continue

        direction = "long" if signal["side"] == "buy" else "short"
        entry = signal["entry_price"]
        sl = signal["stop_loss"]
        tp = signal["take_profit"]

        trade = _simulate_trade(df, i, direction, entry, sl, tp)
        if trade:
            trades.append(trade)
            in_trade_until = i + trade["duration_bars"]

    if trades:
        r_vals = [t["pnl_r"] for t in trades]
        wins = sum(1 for r in r_vals if r > 0)
        total_r = round(sum(r_vals), 2)
        win_rate = round(wins / len(trades) * 100, 1)
        avg_r = round(total_r / len(trades), 3)
        exit_reasons = dict(Counter(t["exit_reason"] for t in trades))
        import statistics

        sharpe_r = round(
            statistics.mean(r_vals) / statistics.stdev(r_vals)
            if len(r_vals) > 1
            else 0.0,
            3,
        )
    else:
        wins = 0
        total_r = avg_r = win_rate = sharpe_r = 0.0
        exit_reasons = {}

    cfg_label = (
        f"{htf_timeframe} EMA-{ema_period}" if use_htf else "no HTF filter"
    )
    return {
        "label": label or cfg_label,
        "config": {
            "htf_timeframe": htf_timeframe,
            "ema_period": ema_period,
            "band_pct": band_pct if use_htf else None,
        },
        "data_bars": len(df),
        "start_date": str(df["timestamp"].iloc[0].date()),
        "end_date": str(df["timestamp"].iloc[-1].date()),
        "total_trades": len(trades),
        "wins": wins,
        "losses": len(trades) - wins,
        "win_rate_pct": win_rate,
        "total_r": total_r,
        "avg_r_per_trade": avg_r,
        "sharpe_r": sharpe_r,
        "htf_blocked_count": blocked_count,
        "exit_reasons": exit_reasons,
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="VWAP HTF-filter backtest")
    parser.add_argument(
        "--htf-timeframe",
        default="4h",
        help="HTF timeframe (1h, 4h, 1d) — ignored with --compare",
    )
    parser.add_argument(
        "--ema-period",
        type=int,
        default=200,
        help="HTF EMA period — ignored with --compare",
    )
    parser.add_argument("--band-pct", type=float, default=0.02)
    parser.add_argument(
        "--no-htf",
        action="store_true",
        help="Disable the HTF gate (baseline, no trend filter)",
    )
    parser.add_argument(
        "--compare",
        action="store_true",
        help="Run all COMPARE_CONFIGS side-by-side",
    )
    parser.add_argument("--label", default="", help="Label for the run")
    parser.add_argument(
        "--start-date",
        default="",
        help="Filter data from YYYY-MM-DD UTC (inclusive). Use with fresh 5m data.",
    )
    parser.add_argument(
        "--end-date",
        default="",
        help="Filter data up to YYYY-MM-DD UTC (inclusive).",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=0,
        help="Shorthand for --start-date N days ago (overridden by --start-date).",
    )
    args = parser.parse_args(argv[1:])

    try:
        df, source_path = load_data()
        print(f"Loaded {len(df)} M5 bars from {source_path}", file=sys.stderr)
    except Exception as exc:
        sys.stderr.write(f"load_data failed: {exc}\n")
        return 1

    # Date-range filtering — lets the caller window the CSV to recent data
    # without re-fetching the full file. --days is a convenience shorthand.
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
    df = df.dropna(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)

    start_date = args.start_date
    if not start_date and args.days > 0:
        import datetime as _dt
        start_date = (
            _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=args.days)
        ).strftime("%Y-%m-%d")

    if start_date:
        start_ts = pd.Timestamp(start_date, tz="UTC")
        df = df[df["timestamp"] >= start_ts].reset_index(drop=True)
    if args.end_date:
        end_ts = pd.Timestamp(args.end_date, tz="UTC") + pd.Timedelta(days=1)
        df = df[df["timestamp"] < end_ts].reset_index(drop=True)

    if df.empty:
        sys.stderr.write("No data remaining after date filtering.\n")
        return 1

    if start_date or args.end_date:
        print(
            f"Date-filtered: {len(df)} bars "
            f"({df['timestamp'].iloc[0].date()} → {df['timestamp'].iloc[-1].date()})",
            file=sys.stderr,
        )

    try:
        if args.compare:
            results = []
            for cfg in COMPARE_CONFIGS:
                print(f"Running: {cfg['label']} …", file=sys.stderr)
                r = run_single(
                    df,
                    htf_timeframe=cfg["htf_timeframe"],
                    ema_period=cfg["ema_period"],
                    band_pct=cfg.get("band_pct") or 0.02,
                    label=cfg["label"],
                )
                results.append(r)
            output: dict[str, Any] = {"comparison": results}
        else:
            htf_tf = None if args.no_htf else args.htf_timeframe
            ema_p = None if args.no_htf else args.ema_period
            output = run_single(
                df,
                htf_timeframe=htf_tf,
                ema_period=ema_p,
                band_pct=args.band_pct,
                label=args.label,
            )
    except Exception as exc:
        sys.stderr.write(f"{type(exc).__name__}: {exc}\n")
        traceback.print_exc(file=sys.stderr)
        return 1

    # Single compact line so ``tail -1`` in wrapper scripts gets the JSON.
    print(json.dumps(output, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
