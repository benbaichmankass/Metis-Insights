"""VWAP strategy backtester with HTF trend-filter parameter sweep.

Backtests the live VWAP mean-reversion strategy (build_vwap_signal) against
historical M5 candle data with support for comparing different HTF trend-filter
configurations side-by-side.  Supports both full-range and random-window modes
for robust out-of-sample validation across multiple market regimes.

Problem context
---------------
The live config uses ``4h EMA-200`` (~800 h ≈ 33 days of look-back) which is
too slow to detect intraday reversals: the bot keeps entering longs into clear
short-term downtrends.  This script compares the current config against faster
alternatives to find the sweet spot before touching config/strategies.yaml.

Usage
-----
    # Compare all configs over random windows (recommended for robust results):
    python -m src.backtest.run_backtest_vwap --compare --windows 8 --window-days 30

    # Compare over full date range (no windowing):
    python -m src.backtest.run_backtest_vwap --compare

    # Limit to recent data (last 90 days):
    python -m src.backtest.run_backtest_vwap --compare --days 90

    # Single custom run with windows:
    python -m src.backtest.run_backtest_vwap --htf-timeframe 1h --ema-period 50 --windows 8

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
conditions (both up and down regimes).  The default data/backtest_candles.csv
in the repo is a small sample for unit tests only.

    BACKTEST_DATA_PATH=/tmp/fresh.csv \\
        python scripts/ops/fetch_backtest_candles.py --days 365
    BACKTEST_DATA_PATH=/tmp/fresh.csv \\
        python -m src.backtest.run_backtest_vwap --compare --windows 8

Output
------
Single line of compact JSON to stdout so ``tail -1`` in wrapper scripts
works.  Informational progress goes to stderr.

Trade simulation close conditions (matches vwap.monitor() priority order):
  1. SL-cross
  2. TP-cross
  3. VWAP-cross (live VWAP recomputed from rolling window at each bar)
  4. Time-decay (HOLD_BARS_MAX bars ≡ monitor_hold_window_minutes=240 min)
"""
from __future__ import annotations

import argparse
import json
import random
import statistics
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

# Round-trip taker fee on Bybit linear perps, basis points (S-STRAT-IMPROVE-S4,
# 2026-05-23). The live audit (bybit_2, 7d) showed the strategy is GROSS-
# positive but NET-negative: fees were 418% of gross because the tight 0.3σ
# stop makes the per-trade fee a large fraction of risk-R. So gross total_r is
# misleading for selectivity work — each backtest trade now also reports
# net_pnl_r = gross_pnl_r − fee_r, where fee_r = (FEE_BPS_ROUNDTRIP/1e4) ×
# (entry+exit)/2 / risk. Settable via --fee-bps-roundtrip. Module-level so
# _simulate_trade reads it without signature churn (mirrors ENTRY_STD_THRESHOLD).
FEE_BPS_ROUNDTRIP = 7.5

# monitor_hold_window_minutes = 240 min / 5 min per bar = 48 bars
HOLD_BARS_MAX = 48

# Per-bar timeframe in minutes — used by the hold-time gate. The vwap strategy
# trades 5m candles in live; the harness consumes 5m too. Module-level so
# _simulate_trade reads it without signature churn.
BAR_MINUTES = 5.0

# Live exit-side selectivity gates (PERF-20260601-003 — 2026-06-01). The live
# strategy applies these in vwap.monitor()/build_vwap_signal (see
# config/strategies.yaml::vwap and src/units/strategies/vwap.py::
# _vwap_cross_gates_pass + the BE ratchet at L1109-1146). The original
# _simulate_trade exited on any VWAP touch with no R / hold-time / break-even
# check — which makes the unfiltered harness bleed fees on micro-cross exits
# (the failure mode docs/research/regime-roster-matrix-2026-06-01.md flagged
# as "vwap −3749 R, NOT decision-grade"). Module-level (same pattern as
# ENTRY_STD_THRESHOLD / FEE_BPS_ROUNDTRIP) so callers monkey-patch a value in
# for a run and _simulate_trade reads it without signature churn:
#
#   * MIN_R_FOR_VWAP_CROSS — minimum captured R before vwap_cross is allowed
#     to fire (≥ this many R-multiples in the trade's favour). 0 = v1
#     behaviour ("any cross closes"). Live: 0.25.
#   * MIN_HOLD_MINUTES_FOR_VWAP_CROSS — minimum hold time before vwap_cross
#     is allowed to fire. 0 = no gate. Live: 10 minutes.
#   * BE_AT_R — when captured R ≥ this, ratchet SL to break-even (entry ±
#     BE_OFFSET_BPS / 10_000). 0 = no break-even ratchet. Live: 1.0.
#   * BE_OFFSET_BPS — basis points beyond entry to place the break-even SL.
#     Default 15 bps matches the live ``be_offset_bps``.
#
# CLI flags --min-r-for-vwap-cross / --min-hold-minutes-for-vwap-cross /
# --be-at-r / --be-offset-bps monkey-patch these for the run.
MIN_R_FOR_VWAP_CROSS = 0.0
MIN_HOLD_MINUTES_FOR_VWAP_CROSS = 0.0
BE_AT_R = 0.0
BE_OFFSET_BPS = 15.0

# Warmup bars prepended to each random window so HTF EMAs are stable at the
# window boundary.  7 days × 288 5m bars/day covers convergence for all
# configs in COMPARE_CONFIGS (longest is 1h EMA-50: 50 × 3 × 12 = 1800 bars).
WARMUP_BARS = 2016

# Normalised pandas frequency strings for resample() and pd.Timedelta().
_HTF_FREQ: dict[str, str] = {
    "5m": "5min",
    "15m": "15min",
    "30m": "30min",
    "1h": "1h",
    "4h": "4h",
    "1d": "1D",
}

# --compare sweeps these configs.  Configs are chosen to be appropriate for
# an intraday 5m mean-reversion strategy — fast enough to capture same-session
# and multi-session directional bias without the lag of EMA-200 on 4h bars.
COMPARE_CONFIGS: list[dict[str, Any]] = [
    {
        "label": "no HTF filter (baseline)",
        "htf_timeframe": None,
        "ema_period": None,
        "band_pct": None,
    },
    {
        "label": "15m EMA-20 (intraday fast)",
        "htf_timeframe": "15m",
        "ema_period": 20,
        "band_pct": 0.02,
    },
    {
        "label": "1h EMA-20 (intraday)",
        "htf_timeframe": "1h",
        "ema_period": 20,
        "band_pct": 0.02,
    },
    {
        "label": "1h EMA-50 (multi-session)",
        "htf_timeframe": "1h",
        "ema_period": 50,
        "band_pct": 0.02,
    },
    {
        "label": "4h EMA-20 (few-day)",
        "htf_timeframe": "4h",
        "ema_period": 20,
        "band_pct": 0.02,
    },
    {
        # Phase-3 design from the 2026-05-08-all-models-training run:
        # on the 38-month dataset, 1h EMA-200 dominated 4h EMA-200 across
        # every metric (Sharpe +3.23 vs +2.47). Distinct from the 1h
        # EMA-20 config tested in #1090 — EMA-20 looks back ~20 hours,
        # EMA-200 looks back ~200 hours (~8 days), so this is a true
        # regime filter rather than a session-trend filter. Currently
        # disabled in production because #1090 (8x 30-day windows) found
        # the fast-EMA configs near-zero Sharpe; this run is the
        # apples-to-apples test of whether the slow-EMA design helps
        # in the same regime where the fast-EMA ones didn't.
        "label": "1h EMA-200 (multi-week regime)",
        "htf_timeframe": "1h",
        "ema_period": 200,
        "band_pct": 0.02,
    },
]


# Entry-threshold sweep (2026-05-15). Triggered by --threshold-sweep, runs
# the no-HTF baseline at each threshold to isolate the standalone effect.
# Values picked to span the 2026-05-08 V6 experiment range and bracket
# the current live default (1.0σ).
THRESHOLD_SWEEP: list[float] = [0.8, 1.0, 1.2, 1.5, 2.0]

# Strategy-parameter sweep grid (Sprint S-VWAP-PARAM-SWEEP, 2026-05-19).
# Triggered by --param-sweep; runs all ENTRY × SL combinations so the
# next session can identify a winning (ENTRY, SL) pair before proposing a
# Tier-3 change to vwap.py constants. Values bracket the live defaults
# (1.0σ / 0.5σ) and the backtest-optimal points documented in vwap.py.
PARAM_SWEEP_ENTRY: list[float] = [0.8, 1.0, 1.2, 1.5]
PARAM_SWEEP_SL: list[float] = [0.3, 0.5, 0.7]


def _resample_to_htf(m5_df: pd.DataFrame, htf_timeframe: str) -> pd.DataFrame:
    """Resample an M5 OHLCV DataFrame to a higher timeframe.

    Uses left-closed, left-labelled periods so period ``T`` represents
    ``[T, T + freq)`` — the close of that period is the close of the
    last M5 bar whose timestamp falls in ``[T, T + freq)``.
    """
    freq = _HTF_FREQ.get(htf_timeframe, htf_timeframe)
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


def _vwap_cross_gates_allow(
    *,
    entry: float,
    sl: float,
    direction: str,
    bar_c: float,
    bars_open: int,
) -> bool:
    """Mirror of ``vwap._vwap_cross_gates_pass`` for the backtest.

    Suppresses ``vwap_cross`` exits that the live strategy would block —
    crosses where the trade hasn't captured enough R or has been open too
    briefly to be a real mean-reversion. Module-level ``MIN_R_FOR_VWAP_CROSS``
    / ``MIN_HOLD_MINUTES_FOR_VWAP_CROSS`` control the thresholds; 0 = gate
    disabled (legacy "any cross closes" behaviour).
    """
    risk = abs(entry - sl)
    if risk <= 0:
        return True
    if MIN_R_FOR_VWAP_CROSS > 0:
        r_captured = (bar_c - entry) / risk if direction == "long" else (entry - bar_c) / risk
        if r_captured < MIN_R_FOR_VWAP_CROSS:
            return False
    if MIN_HOLD_MINUTES_FOR_VWAP_CROSS > 0:
        if bars_open * BAR_MINUTES < MIN_HOLD_MINUTES_FOR_VWAP_CROSS:
            return False
    return True


def _simulate_trade(
    df: pd.DataFrame,
    entry_idx: int,
    direction: str,
    entry: float,
    sl: float,
    tp: float,
    vwap_anchor: str = "session",
) -> dict[str, Any] | None:
    """Forward-simulate a trade from ``entry_idx``.

    Checks SL/TP on each bar's high/low first (priority 1 & 2), then
    VWAP-cross on the bar close (priority 3). Time-decay fires after
    ``HOLD_BARS_MAX`` bars (priority 4).
    """
    risk = abs(entry - sl)
    if risk <= 0:
        return None

    # Mirrors the live monitor: SL may be ratcheted up to break-even mid-trade
    # once captured R ≥ BE_AT_R. ``sl`` is the original placed stop; ``sl_live``
    # is what the next bar actually checks against. Starts equal.
    sl_live = sl

    exit_price: float | None = None
    exit_reason = "time_decay"
    exit_idx = min(entry_idx + HOLD_BARS_MAX, len(df) - 1)

    for j in range(entry_idx + 1, min(entry_idx + HOLD_BARS_MAX + 1, len(df))):
        bar_h = float(df["high"].iloc[j])
        bar_lo = float(df["low"].iloc[j])
        bar_c = float(df["close"].iloc[j])

        if direction == "long":
            if bar_lo <= sl_live:
                exit_price, exit_reason, exit_idx = sl_live, "sl_cross", j
                break
            if bar_h >= tp:
                exit_price, exit_reason, exit_idx = tp, "tp_cross", j
                break
        else:  # short
            if bar_h >= sl_live:
                exit_price, exit_reason, exit_idx = sl_live, "sl_cross", j
                break
            if bar_lo <= tp:
                exit_price, exit_reason, exit_idx = tp, "tp_cross", j
                break

        # VWAP-cross: recompute live VWAP on the rolling window to this bar.
        win_start = max(0, j - M5_LOOKBACK_BARS + 1)
        try:
            _monitor_slice = df.iloc[win_start : j + 1]
            vwap_live = compute_vwap(
                _monitor_slice
                if vwap_anchor == "rolling"
                else _session_anchor_slice(_monitor_slice)
            )
            cross = (direction == "long" and bar_c >= vwap_live) or (
                direction == "short" and bar_c <= vwap_live
            )
            if cross and _vwap_cross_gates_allow(
                entry=entry,
                sl=sl,
                direction=direction,
                bar_c=bar_c,
                bars_open=j - entry_idx,
            ):
                exit_price, exit_reason, exit_idx = bar_c, "vwap_cross", j
                break
        except Exception:  # noqa: BLE001
            pass

        # Break-even ratchet: mirror the live monitor's BE step. If the bar's
        # high (long) / low (short) cleared ``BE_AT_R`` × risk, move SL up to
        # entry ± BE_OFFSET_BPS for the NEXT bar's SL check. Monotonic (never
        # loosens). 0 = no ratchet (default = harness v1 behaviour).
        if BE_AT_R > 0:
            if direction == "long":
                hwm_r = (bar_h - entry) / risk
                if hwm_r >= BE_AT_R:
                    be_sl = entry * (1.0 + BE_OFFSET_BPS / 10_000.0)
                    if be_sl > sl_live:
                        sl_live = be_sl
            else:
                hwm_r = (entry - bar_lo) / risk
                if hwm_r >= BE_AT_R:
                    be_sl = entry * (1.0 - BE_OFFSET_BPS / 10_000.0)
                    if be_sl < sl_live:
                        sl_live = be_sl

    if exit_price is None:
        exit_price = float(df["close"].iloc[exit_idx])

    pnl_r = (
        (exit_price - entry) / risk
        if direction == "long"
        else (entry - exit_price) / risk
    )
    # Net-of-fee R (S-STRAT-IMPROVE-S4). Round-trip taker fee in R units:
    # fee charged on both legs ≈ (FEE_BPS_ROUNDTRIP/1e4) × (entry+exit)/2 per
    # unit notional; dividing by ``risk`` expresses it in R. Tight stops make
    # this large (a 0.3σ ≈ 14 bps stop vs a 7.5 bps round-trip ≈ 0.5R/trade),
    # which is why high-frequency vwap is net-negative despite positive gross.
    fee_r = (FEE_BPS_ROUNDTRIP / 10_000.0) * ((entry + exit_price) / 2.0) / risk
    net_pnl_r = pnl_r - fee_r
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
        "fee_r": round(fee_r, 4),
        "net_pnl_r": round(net_pnl_r, 3),
        "duration_bars": exit_idx - entry_idx,
    }


def run_single(
    df: pd.DataFrame,
    htf_timeframe: str | None = "4h",
    ema_period: int | None = 200,
    band_pct: float = 0.02,
    label: str = "",
    start_bar: int = 0,
    sl_std_mult: float | None = None,
    vwap_anchor: str = "session",
    emit_trades: str | None = None,
) -> dict[str, Any]:
    """Run the VWAP backtest with one HTF config.

    ``start_bar`` sets the earliest bar index where trading signals are
    evaluated — useful when run_windows() prepends a warmup prefix.
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
        htf_period_delta = pd.Timedelta(_HTF_FREQ.get(htf_timeframe, htf_timeframe))
    else:
        htf_df = htf_ema_series = htf_period_delta = None

    trades: list[dict] = []
    blocked_count = 0
    in_trade_until = -1  # bar index; skip bars i <= in_trade_until

    trade_start = max(M5_LOOKBACK_BARS, start_bar)
    for i in range(trade_start, len(df)):
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

        window_for_signal = (
            window.drop(columns=["timestamp"], errors="ignore")
            if vwap_anchor == "rolling"
            else window
        )
        signal = build_vwap_signal(
            window_for_signal,
            symbol="BTCUSDT",
            htf_close=htf_close,
            htf_ema=htf_ema_val,
            htf_band_pct=band_pct if use_htf else 0.02,
            **({"sl_std_mult": sl_std_mult} if sl_std_mult is not None else {}),
        )

        if signal.get("side") == "none":
            if use_htf and signal.get("meta", {}).get("htf_blocked"):
                blocked_count += 1
            continue

        direction = "long" if signal["side"] == "buy" else "short"
        entry = signal["entry_price"]
        sl = signal["stop_loss"]
        tp = signal["take_profit"]

        trade = _simulate_trade(df, i, direction, entry, sl, tp, vwap_anchor=vwap_anchor)
        if trade:
            # Capture the signal's confidence for the calibration corpus
            # (design § 4a). vwap DOES emit a varying confidence (sigma
            # deviation / threshold) — earlier the emit hook hardcoded None.
            _conf = signal.get("confidence")
            if _conf is None:
                _conf = signal.get("meta", {}).get("confidence")
            trade["confidence"] = _conf
            # Carry the LIVE signal meta verbatim (deviation_std / std_dev /
            # vwap / policy_threshold + stamped regime / adx_14 / vol_regime)
            # so the signal-research component-edge report can attribute
            # entry-component edge over backtest volume
            # (component_edge_report.py --backtest-log).
            trade["meta"] = signal.get("meta") or {}
            trades.append(trade)
            in_trade_until = i + trade["duration_bars"]

    long_trades = [t for t in trades if t["direction"] == "long"]
    short_trades = [t for t in trades if t["direction"] == "short"]
    if trades:
        r_vals = [t["pnl_r"] for t in trades]
        wins = sum(1 for r in r_vals if r > 0)
        total_r = round(sum(r_vals), 2)
        win_rate = round(wins / len(trades) * 100, 1)
        avg_r = round(total_r / len(trades), 3)
        exit_reasons = dict(Counter(t["exit_reason"] for t in trades))
        sharpe_r = round(
            statistics.mean(r_vals) / statistics.stdev(r_vals)
            if len(r_vals) > 1
            else 0.0,
            3,
        )
        total_r_long = round(sum(t["pnl_r"] for t in long_trades), 2)
        total_r_short = round(sum(t["pnl_r"] for t in short_trades), 2)
        wins_long = sum(1 for t in long_trades if t["pnl_r"] > 0)
        wins_short = sum(1 for t in short_trades if t["pnl_r"] > 0)
        # Net-of-fee aggregates (S-STRAT-IMPROVE-S4). The selectivity-relevant
        # numbers: net total R, net per-trade R, and net win rate (a "win"
        # after fees), split by leg. These — not the gross fields above — are
        # what S4 ranks selectivity variants on.
        net_r_vals = [t["net_pnl_r"] for t in trades]
        net_total_r = round(sum(net_r_vals), 2)
        net_avg_r = round(net_total_r / len(trades), 3)
        net_wins = sum(1 for r in net_r_vals if r > 0)
        net_win_rate = round(net_wins / len(trades) * 100, 1)
        net_total_r_long = round(sum(t["net_pnl_r"] for t in long_trades), 2)
        net_total_r_short = round(sum(t["net_pnl_r"] for t in short_trades), 2)
        total_fee_r = round(sum(t["fee_r"] for t in trades), 2)
    else:
        wins = 0
        total_r = avg_r = win_rate = sharpe_r = 0.0
        exit_reasons = {}
        total_r_long = total_r_short = 0.0
        wins_long = wins_short = 0
        net_total_r = net_avg_r = net_win_rate = 0.0
        net_wins = 0
        net_total_r_long = net_total_r_short = 0.0
        total_fee_r = 0.0

    cfg_label = (
        f"{htf_timeframe} EMA-{ema_period}" if use_htf else "no HTF filter"
    )

    # Per-trade JSONL for the regime tagger (scripts/research/regime_tag_emitted.py)
    # and the confidence-calibration corpus (design § 4a). Same schema the
    # standalone harnesses emit; vwap's confidence is now captured per-trade.
    if emit_trades:
        import json as _json

        with open(emit_trades, "w", encoding="utf-8") as _fh:
            for _t in trades:
                _fh.write(
                    _json.dumps(
                        {
                            "strategy": "vwap",
                            "entry_time": str(_t["entry_time"]),
                            "direction": _t["direction"],
                            "gross_r": _t["pnl_r"],
                            "net_r": _t["net_pnl_r"],
                            "confidence": _t.get("confidence"),
                            # LIVE signal meta (deviation_std / std_dev / vwap /
                            # policy_threshold + stamped regime keys) for the
                            # component-edge report's --backtest-log path.
                            "meta": _t.get("meta"),
                        },
                        default=str,
                    )
                    + "\n"
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
        "trades_long": len(long_trades),
        "trades_short": len(short_trades),
        "wins": wins,
        "wins_long": wins_long,
        "wins_short": wins_short,
        "losses": len(trades) - wins,
        "win_rate_pct": win_rate,
        "total_r": total_r,
        "total_r_long": total_r_long,
        "total_r_short": total_r_short,
        "avg_r_per_trade": avg_r,
        # Net-of-fee (S-STRAT-IMPROVE-S4) — the selectivity-ranking metrics.
        "fee_bps_roundtrip": FEE_BPS_ROUNDTRIP,
        "total_fee_r": total_fee_r,
        "net_total_r": net_total_r,
        "net_total_r_long": net_total_r_long,
        "net_total_r_short": net_total_r_short,
        "net_avg_r_per_trade": net_avg_r,
        "net_win_rate_pct": net_win_rate,
        "net_wins": net_wins,
        "sharpe_r": sharpe_r,
        "htf_blocked_count": blocked_count,
        "exit_reasons": exit_reasons,
        "vwap_anchor": vwap_anchor,
    }


def classify_window_regime(df_slice: pd.DataFrame) -> dict[str, Any]:
    """Label a window by trend + volatility regime.

    Operator directive 2026-05-18: a strategy that overfits to one
    market regime is a strategy that will surprise us when the
    regime changes. We need to know which regimes each backtest
    window represents, and surface per-regime aggregate performance
    so we can tell whether 2.0σ is robust or just lucky on
    chop-heavy windows.

    Trend bucket — total % move over window:
      strong-down  : < -5%
      weak-down    : -5% .. -1%
      sideways     : -1% .. +1%
      weak-up      : +1% .. +5%
      strong-up    : > +5%

    Volatility bucket — mean per-bar high-low range as bps of close:
      low     : < 15 bps  (slow tape)
      medium  : 15-35 bps
      high    : > 35 bps  (volatile tape)

    Returns ``{"trend", "volatility", "regime", "pct_change",
    "avg_range_bps"}``. ``regime`` is the combined ``trend/volatility``
    label suitable for grouping.
    """
    if (
        df_slice is None
        or "close" not in df_slice.columns
        or len(df_slice) < 10
    ):
        return {
            "trend": "unknown", "volatility": "unknown",
            "regime": "unknown",
            "pct_change": 0.0, "avg_range_bps": 0.0,
        }
    close = df_slice["close"].astype(float)
    open_px = float(close.iloc[0])
    close_px = float(close.iloc[-1])
    if open_px <= 0:
        return {
            "trend": "unknown", "volatility": "unknown",
            "regime": "unknown",
            "pct_change": 0.0, "avg_range_bps": 0.0,
        }
    pct_change = (close_px - open_px) / open_px
    if pct_change < -0.05:
        trend = "strong-down"
    elif pct_change < -0.01:
        trend = "weak-down"
    elif pct_change < 0.01:
        trend = "sideways"
    elif pct_change < 0.05:
        trend = "weak-up"
    else:
        trend = "strong-up"

    high = df_slice["high"].astype(float)
    low = df_slice["low"].astype(float)
    # bar_range / close → fraction; × 10_000 → bps
    bar_range_bps = ((high - low) / close * 10_000).mean()
    if bar_range_bps < 15:
        volatility = "low"
    elif bar_range_bps < 35:
        volatility = "medium"
    else:
        volatility = "high"

    return {
        "trend": trend,
        "volatility": volatility,
        "regime": f"{trend}/{volatility}",
        "pct_change": round(pct_change * 100, 2),
        "avg_range_bps": round(float(bar_range_bps), 2),
    }


def _select_start_positions(
    rng: random.Random,
    pool_min: int,
    pool_max: int,
    n_windows: int,
    recent_only_frac: float = 1.0,
) -> list[int]:
    """Pick ``n_windows`` start positions from ``[pool_min, pool_max]``.

    When ``recent_only_frac < 1.0`` (e.g. 0.5), sampling is restricted
    to the most-recent fraction of the pool — i.e. windows starting
    in roughly the last ``recent_only_frac × pool_span`` bars. This
    biases the backtest toward present-regime data without over-
    sampling any single window.
    """
    if pool_max < pool_min:
        return []
    pool_span = pool_max - pool_min + 1
    recent_only_frac = max(0.0, min(1.0, float(recent_only_frac)))
    if recent_only_frac < 1.0:
        cutoff = pool_max - int(pool_span * recent_only_frac) + 1
        effective_min = max(pool_min, cutoff)
    else:
        effective_min = pool_min
    effective_pool = list(range(effective_min, pool_max + 1))
    k = min(n_windows, len(effective_pool))
    return sorted(rng.sample(effective_pool, k))


def run_windows(
    df: pd.DataFrame,
    n_windows: int = 8,
    window_days: int = 30,
    htf_timeframe: str | None = "4h",
    ema_period: int | None = 200,
    band_pct: float = 0.02,
    label: str = "",
    seed: int = 42,
    recent_only_frac: float = 1.0,
    adaptive: bool = False,
    sl_std_mult: float | None = None,
    vwap_anchor: str = "session",
) -> dict[str, Any]:
    """Run the backtest over N random windows and return aggregate stats.

    Each window is ``window_days`` long, preceded by ``WARMUP_BARS`` bars so
    that HTF EMAs are stable before trading starts.  Windows are sampled
    uniformly at random from the full dataset using ``seed`` for
    reproducibility across runs.
    """
    BARS_PER_DAY = 288  # 5m × 12/h × 24 h
    window_bars = window_days * BARS_PER_DAY

    min_length = WARMUP_BARS + window_bars
    if len(df) < min_length:
        raise ValueError(
            f"Dataset too small ({len(df)} bars) for warmup ({WARMUP_BARS}) "
            f"+ window ({window_bars}). Need at least {min_length} bars "
            f"(approx {min_length // BARS_PER_DAY} days)."
        )

    max_start = len(df) - window_bars
    pool_size = max_start - WARMUP_BARS + 1
    if pool_size < 1:
        raise ValueError(
            f"Not enough post-warmup bars for a window. "
            f"max_start={max_start}, WARMUP_BARS={WARMUP_BARS}"
        )

    rng = random.Random(seed)
    start_positions = _select_start_positions(
        rng, WARMUP_BARS, max_start, n_windows,
        recent_only_frac=recent_only_frac,
    )

    cfg_label = (
        f"{htf_timeframe} EMA-{ema_period}"
        if htf_timeframe and ema_period
        else "no HTF filter"
    )
    window_results: list[dict] = []
    for w_idx, start_pos in enumerate(start_positions):
        # Slice includes warmup prefix so EMA is warm at the window boundary.
        slice_df = df.iloc[
            start_pos - WARMUP_BARS : start_pos + window_bars
        ].reset_index(drop=True)
        w_start_date = df["timestamp"].iloc[start_pos].date()
        w_end_pos = min(start_pos + window_bars - 1, len(df) - 1)
        w_end_date = df["timestamp"].iloc[w_end_pos].date()
        print(
            f"  [{label or cfg_label}] window {w_idx + 1}/{len(start_positions)}: "
            f"{w_start_date} -> {w_end_date} …",
            file=sys.stderr,
        )
        r = run_single(
            slice_df,
            htf_timeframe=htf_timeframe,
            ema_period=ema_period,
            band_pct=band_pct,
            label=label or cfg_label,
            start_bar=WARMUP_BARS,
            sl_std_mult=sl_std_mult,
            vwap_anchor=vwap_anchor,
        )
        # Classify regime on the TRADING portion (skip warmup) so the
        # label reflects what the strategy actually faced, not the
        # warmup prefix used to seed HTF EMAs.
        regime = classify_window_regime(
            slice_df.iloc[WARMUP_BARS:].reset_index(drop=True)
        )
        r["regime"] = regime
        # Adaptive mode: consult vwap_policy. If the policy says skip,
        # mark the window as zero-trade. If it says allow with a
        # threshold override, re-run that window with the override. If
        # allow with ``threshold is None`` (post-#1536 skip-only design),
        # keep the result from the original run_single above — that
        # already used the module-level ENTRY_STD_THRESHOLD, which is
        # what threshold=None means.
        if adaptive:
            from src.units.strategies import vwap as _vwap_mod
            from src.units.strategies.vwap_policy import lookup_policy
            pol = lookup_policy(regime.get("regime") or "unknown")
            if not pol.get("allow"):
                # Override the result with a zero-trade window.
                r = {
                    **r,
                    "label": f"adaptive (SKIP {regime['regime']})",
                    "total_trades": 0, "trades_long": 0, "trades_short": 0,
                    "wins": 0, "wins_long": 0, "wins_short": 0, "losses": 0,
                    "win_rate_pct": None,
                    "total_r": 0.0, "total_r_long": 0.0, "total_r_short": 0.0,
                    "avg_r_per_trade": 0.0,
                    "net_total_r": 0.0, "net_total_r_long": 0.0,
                    "net_total_r_short": 0.0, "net_avg_r_per_trade": 0.0,
                    "net_win_rate_pct": None, "net_wins": 0, "total_fee_r": 0.0,
                    "sharpe_r": 0.0,
                    "exit_reasons": {},
                    "adaptive_skipped": True,
                    "adaptive_policy": pol,
                }
            elif pol.get("threshold") is None:
                # Allow but no override — keep the original run_single
                # result (already used module ENTRY_STD_THRESHOLD).
                r["label"] = (
                    f"adaptive ({regime['regime']} @ module "
                    f"ENTRY_STD_THRESHOLD={_vwap_mod.ENTRY_STD_THRESHOLD}σ)"
                )
                r["adaptive_skipped"] = False
                r["adaptive_policy"] = pol
            else:
                # Re-run with the policy threshold override.
                _original = _vwap_mod.ENTRY_STD_THRESHOLD
                _vwap_mod.ENTRY_STD_THRESHOLD = pol["threshold"]
                _vwap_mod._ENTRY_STD_THRESHOLD = pol["threshold"]
                try:
                    r = run_single(
                        slice_df,
                        htf_timeframe=htf_timeframe,
                        ema_period=ema_period,
                        band_pct=band_pct,
                        label=f"adaptive ({regime['regime']} @ {pol['threshold']}σ)",
                        start_bar=WARMUP_BARS,
                        sl_std_mult=sl_std_mult,
                        vwap_anchor=vwap_anchor,
                    )
                finally:
                    _vwap_mod.ENTRY_STD_THRESHOLD = _original
                    _vwap_mod._ENTRY_STD_THRESHOLD = _original
                r["regime"] = regime
                r["adaptive_skipped"] = False
                r["adaptive_policy"] = pol
        window_results.append(r)

    sharpe_vals = [r["sharpe_r"] for r in window_results]
    total_r_vals = [r["total_r"] for r in window_results]
    total_r_long_vals = [r.get("total_r_long", 0.0) for r in window_results]
    total_r_short_vals = [r.get("total_r_short", 0.0) for r in window_results]
    # Net-of-fee per-window aggregates (S-STRAT-IMPROVE-S4).
    net_total_r_vals = [r.get("net_total_r", 0.0) for r in window_results]
    net_total_r_long_vals = [r.get("net_total_r_long", 0.0) for r in window_results]
    net_total_r_short_vals = [r.get("net_total_r_short", 0.0) for r in window_results]
    total_trades_vals = [r.get("total_trades", 0) for r in window_results]
    # Adaptive mode marks skipped windows as ``win_rate_pct=None`` (no
    # trades taken means win-rate is undefined, not 0). Filter for
    # aggregation; ``statistics.mean`` chokes on ``None``.
    win_rate_vals = [
        r["win_rate_pct"] for r in window_results
        if r.get("win_rate_pct") is not None
    ]
    positive_windows = sum(1 for r in window_results if r["total_r"] > 0)
    net_positive_windows = sum(
        1 for r in window_results if r.get("net_total_r", 0.0) > 0
    )

    # Per-regime aggregation: group by regime label, compute mean
    # total_r / win_rate / sharpe / sample count per regime. Surfaces
    # whether a config works only in one regime or across regimes.
    by_regime: dict[str, list[dict]] = {}
    for r in window_results:
        regime_label = r.get("regime", {}).get("regime", "unknown")
        by_regime.setdefault(regime_label, []).append(r)
    per_regime_stats = []
    for reg, items in sorted(by_regime.items()):
        rs = [it["total_r"] for it in items]
        rs_long = [it.get("total_r_long", 0.0) for it in items]
        rs_short = [it.get("total_r_short", 0.0) for it in items]
        net_rs = [it.get("net_total_r", 0.0) for it in items]
        sharpes = [it["sharpe_r"] for it in items]
        wrs = [it["win_rate_pct"] for it in items if it.get("win_rate_pct") is not None]
        per_regime_stats.append({
            "regime": reg,
            "n_windows": len(items),
            "mean_total_r": round(statistics.mean(rs), 2),
            "mean_total_r_long": round(statistics.mean(rs_long), 2),
            "mean_total_r_short": round(statistics.mean(rs_short), 2),
            "mean_net_total_r": round(statistics.mean(net_rs), 2),
            "mean_sharpe": round(statistics.mean(sharpes), 3),
            "mean_win_rate_pct": round(statistics.mean(wrs), 1) if wrs else None,
            "positive_windows": sum(1 for v in rs if v > 0),
            "net_positive_windows": sum(1 for v in net_rs if v > 0),
        })

    return {
        "label": label or cfg_label,
        "config": {
            "htf_timeframe": htf_timeframe,
            "ema_period": ema_period,
            "band_pct": band_pct if htf_timeframe and ema_period else None,
        },
        "n_windows": len(window_results),
        "window_days": window_days,
        "seed": seed,
        "recent_only_frac": recent_only_frac,
        "mean_sharpe": round(statistics.mean(sharpe_vals), 3),
        "std_sharpe": round(
            statistics.stdev(sharpe_vals) if len(sharpe_vals) > 1 else 0.0, 3
        ),
        "min_sharpe": round(min(sharpe_vals), 3),
        "max_sharpe": round(max(sharpe_vals), 3),
        "mean_total_r": round(statistics.mean(total_r_vals), 2),
        "mean_total_r_long": round(statistics.mean(total_r_long_vals), 2),
        "mean_total_r_short": round(statistics.mean(total_r_short_vals), 2),
        # Net-of-fee aggregates (S-STRAT-IMPROVE-S4) — S4 ranks variants on
        # these, not the gross mean_total_r above.
        "fee_bps_roundtrip": FEE_BPS_ROUNDTRIP,
        "mean_net_total_r": round(statistics.mean(net_total_r_vals), 2),
        "mean_net_total_r_long": round(statistics.mean(net_total_r_long_vals), 2),
        "mean_net_total_r_short": round(statistics.mean(net_total_r_short_vals), 2),
        "mean_trades_per_window": round(statistics.mean(total_trades_vals), 1),
        "net_positive_windows": net_positive_windows,
        "mean_win_rate_pct": (
            round(statistics.mean(win_rate_vals), 1) if win_rate_vals else None
        ),
        "positive_windows": positive_windows,
        "vwap_anchor": vwap_anchor,
        "windows": window_results,
        "by_regime": per_regime_stats,
    }


def main(argv: list[str]) -> int:
    global FEE_BPS_ROUNDTRIP  # set from --fee-bps-roundtrip after parse
    global MIN_R_FOR_VWAP_CROSS, MIN_HOLD_MINUTES_FOR_VWAP_CROSS
    global BE_AT_R, BE_OFFSET_BPS
    parser = argparse.ArgumentParser(description="VWAP HTF-filter backtest")
    parser.add_argument(
        "--htf-timeframe",
        default="4h",
        help="HTF timeframe (15m, 1h, 4h, 1d) — ignored with --compare",
    )
    parser.add_argument(
        "--ema-period",
        type=int,
        default=200,
        help="HTF EMA period — ignored with --compare",
    )
    parser.add_argument("--band-pct", type=float, default=0.02)
    parser.add_argument(
        "--fee-bps-roundtrip",
        type=float,
        default=FEE_BPS_ROUNDTRIP,
        help="Round-trip taker fee in bps for net-of-fee R (default 7.5, "
             "Bybit linear). Set 0 to reproduce gross-only results.",
    )
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
    parser.add_argument(
        "--threshold-sweep",
        action="store_true",
        help=(
            "Sweep ENTRY_STD_THRESHOLD across THRESHOLD_SWEEP values "
            "(0.8/1.0/1.2/1.5/2.0σ), no HTF gate. Mutually exclusive "
            "with --compare."
        ),
    )
    parser.add_argument(
        "--adaptive",
        action="store_true",
        help=(
            "Adaptive mode: classify each window's regime and apply the "
            "per-regime entry threshold (or skip) from "
            "``src/units/strategies/vwap_policy.py``. Mutually exclusive "
            "with --compare and --threshold-sweep."
        ),
    )
    parser.add_argument(
        "--param-sweep",
        action="store_true",
        help=(
            "Sweep ENTRY × SL across PARAM_SWEEP_ENTRY × PARAM_SWEEP_SL grids "
            "(no HTF gate). Mutually exclusive with --compare, --threshold-sweep, "
            "and --adaptive."
        ),
    )
    parser.add_argument(
        "--entry-threshold",
        type=float,
        default=None,
        metavar="SIGMA",
        help=(
            "Override ENTRY_STD_THRESHOLD for a single run (default: use "
            "module constant). Ignored by --param-sweep (which sweeps its own grid)."
        ),
    )
    parser.add_argument(
        "--min-r-for-vwap-cross",
        type=float,
        default=None,
        metavar="R",
        help=(
            "Live exit-side selectivity gate (PERF-20260601-003). Blocks "
            "vwap_cross until the trade has captured ≥ R-multiples in its "
            "favour. 0 = disable; live default 0.25. Default: module constant."
        ),
    )
    parser.add_argument(
        "--min-hold-minutes-for-vwap-cross",
        type=float,
        default=None,
        metavar="MIN",
        help=(
            "Live exit-side selectivity gate (PERF-20260601-003). Blocks "
            "vwap_cross until the trade has been open ≥ this many minutes. "
            "0 = disable; live default 10. Default: module constant."
        ),
    )
    parser.add_argument(
        "--be-at-r",
        type=float,
        default=None,
        metavar="R",
        help=(
            "Live break-even ratchet (PERF-20260601-003). When captured R "
            "≥ this value, move SL to entry ± BE_OFFSET_BPS/10_000. "
            "0 = no ratchet; live default 1.0. Default: module constant."
        ),
    )
    parser.add_argument(
        "--be-offset-bps",
        type=float,
        default=None,
        metavar="BPS",
        help=(
            "Basis-points offset for the break-even SL (PERF-20260601-003). "
            "Live default 15. Default: module constant."
        ),
    )
    parser.add_argument(
        "--sl-mult",
        type=float,
        default=None,
        metavar="SIGMA",
        help=(
            "Override sl_std_mult for a single run (default: use "
            "SL_STD_MULT_DEFAULT). Ignored by --param-sweep (which sweeps its own grid)."
        ),
    )
    parser.add_argument(
        "--vwap-anchor",
        choices=["session", "rolling", "compare"],
        default="session",
        help=(
            "VWAP anchor mode. 'session' (default) = UTC-midnight reset "
            "(matches live build_vwap_signal behaviour); 'rolling' = pure "
            "300-bar rolling window (no daily reset); 'compare' = run both "
            "variants on the same windows and output a side-by-side table. "
            "'compare' requires --windows and is mutually exclusive with "
            "--compare / --threshold-sweep / --adaptive / --param-sweep."
        ),
    )
    parser.add_argument("--label", default="", help="Label for the run")
    parser.add_argument(
        "--emit-trades",
        default=None,
        metavar="PATH",
        help="Write per-trade {strategy, entry_time, direction, gross_r, net_r} "
        "JSONL (single-run mode only) for scripts/research/regime_tag_emitted.py.",
    )
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
    parser.add_argument(
        "--windows",
        type=int,
        default=0,
        help="Number of random windows to sample (0 = use full date range).",
    )
    parser.add_argument(
        "--window-days",
        type=int,
        default=30,
        help="Calendar days per random window (used with --windows).",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed for window sampling (used with --windows).",
    )
    parser.add_argument(
        "--recent-only-frac",
        type=float,
        default=1.0,
        help=(
            "Restrict window-sampling pool to the most-recent fraction "
            "of the dataset (e.g. 0.5 = last half). Default 1.0 (full "
            "range). Use < 1.0 to weight the backtest toward present "
            "market conditions without distorting individual windows."
        ),
    )
    args = parser.parse_args(argv[1:])

    # Apply the fee rate globally so _simulate_trade picks it up without a
    # signature change (mirrors the ENTRY_STD_THRESHOLD monkey-patch pattern).
    # ``global`` is declared at the top of main() (the argparse default reads
    # the module value, which counts as a use).
    FEE_BPS_ROUNDTRIP = args.fee_bps_roundtrip
    # PERF-20260601-003 live exit-side gates — same pattern. CLI flag overrides
    # the module default when explicitly supplied (None = leave as-is).
    if args.min_r_for_vwap_cross is not None:
        MIN_R_FOR_VWAP_CROSS = args.min_r_for_vwap_cross
    if args.min_hold_minutes_for_vwap_cross is not None:
        MIN_HOLD_MINUTES_FOR_VWAP_CROSS = args.min_hold_minutes_for_vwap_cross
    if args.be_at_r is not None:
        BE_AT_R = args.be_at_r
    if args.be_offset_bps is not None:
        BE_OFFSET_BPS = args.be_offset_bps

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
            f"({df['timestamp'].iloc[0].date()} -> {df['timestamp'].iloc[-1].date()})",
            file=sys.stderr,
        )

    try:
        use_windows = args.windows > 0

        anchor_compare = args.vwap_anchor == "compare"
        if sum(bool(x) for x in (args.compare, args.threshold_sweep,
                                   args.adaptive, args.param_sweep,
                                   anchor_compare)) > 1:
            sys.stderr.write(
                "--compare / --threshold-sweep / --adaptive / --param-sweep "
                "/ --vwap-anchor compare are mutually exclusive\n"
            )
            return 1

        if anchor_compare:
            if not use_windows:
                sys.stderr.write(
                    "--vwap-anchor compare requires --windows\n"
                )
                return 1
            from src.units.strategies import vwap as _vwap_mod
            _orig_thr = _vwap_mod.ENTRY_STD_THRESHOLD
            if args.entry_threshold is not None:
                _vwap_mod.ENTRY_STD_THRESHOLD = args.entry_threshold
                _vwap_mod._ENTRY_STD_THRESHOLD = args.entry_threshold
            htf_tf = None if args.no_htf else args.htf_timeframe
            ema_p = None if args.no_htf else args.ema_period
            results = []
            try:
                for anchor in ("session", "rolling"):
                    print(
                        f"Running: anchor={anchor} …",
                        file=sys.stderr,
                    )
                    r = run_windows(
                        df,
                        n_windows=args.windows,
                        window_days=args.window_days,
                        htf_timeframe=htf_tf,
                        ema_period=ema_p,
                        band_pct=args.band_pct,
                        label=f"anchor={anchor}",
                        seed=args.seed,
                        recent_only_frac=args.recent_only_frac,
                        sl_std_mult=args.sl_mult,
                        vwap_anchor=anchor,
                    )
                    results.append(r)
            finally:
                if args.entry_threshold is not None:
                    _vwap_mod.ENTRY_STD_THRESHOLD = _orig_thr
                    _vwap_mod._ENTRY_STD_THRESHOLD = _orig_thr
            output: dict[str, Any] = {"anchor_window_comparison": results}
        elif args.adaptive:
            if not use_windows:
                sys.stderr.write(
                    "--adaptive requires --windows (regime is classified "
                    "per window)\n"
                )
                return 1
            print("Running: adaptive (regime → policy) …", file=sys.stderr)
            output = run_windows(
                df,
                n_windows=args.windows,
                window_days=args.window_days,
                htf_timeframe=None, ema_period=None, band_pct=0.02,
                label="adaptive",
                seed=args.seed,
                recent_only_frac=args.recent_only_frac,
                adaptive=True,
            )
        elif args.param_sweep:
            # Monkey-patch ENTRY_STD_THRESHOLD per row; pass sl_std_mult
            # directly to run_single/run_windows (no monkey-patch needed
            # — sl_std_mult is already a named parameter of build_vwap_signal).
            from src.units.strategies import vwap as _vwap_mod
            original_threshold = _vwap_mod.ENTRY_STD_THRESHOLD
            results = []
            try:
                for entry_thr in PARAM_SWEEP_ENTRY:
                    _vwap_mod.ENTRY_STD_THRESHOLD = entry_thr
                    _vwap_mod._ENTRY_STD_THRESHOLD = entry_thr
                    for sl_mult in PARAM_SWEEP_SL:
                        combo_label = f"entry {entry_thr}σ sl {sl_mult}σ"
                        print(f"Running: {combo_label} …", file=sys.stderr)
                        if use_windows:
                            r = run_windows(
                                df,
                                n_windows=args.windows,
                                window_days=args.window_days,
                                htf_timeframe=None,
                                ema_period=None,
                                band_pct=0.02,
                                label=combo_label,
                                seed=args.seed,
                                recent_only_frac=args.recent_only_frac,
                                sl_std_mult=sl_mult,
                            )
                        else:
                            r = run_single(
                                df,
                                htf_timeframe=None,
                                ema_period=None,
                                band_pct=0.02,
                                label=combo_label,
                                sl_std_mult=sl_mult,
                            )
                        r["entry_std_threshold"] = entry_thr
                        r["sl_std_mult"] = sl_mult
                        results.append(r)
            finally:
                _vwap_mod.ENTRY_STD_THRESHOLD = original_threshold
                _vwap_mod._ENTRY_STD_THRESHOLD = original_threshold
            key = "param_sweep_window" if use_windows else "param_sweep"
            output: dict[str, Any] = {key: results}
        elif args.threshold_sweep:
            # Monkey-patch the module-level threshold for each iteration.
            # Restore after the sweep so the rest of the process (and any
            # downstream tests in the same Python session) sees the live
            # default again.
            from src.units.strategies import vwap as _vwap_mod
            original_threshold = _vwap_mod.ENTRY_STD_THRESHOLD
            results = []
            try:
                for threshold in THRESHOLD_SWEEP:
                    _vwap_mod.ENTRY_STD_THRESHOLD = threshold
                    _vwap_mod._ENTRY_STD_THRESHOLD = threshold
                    label = f"entry {threshold}σ (no HTF)"
                    print(f"Running: {label} …", file=sys.stderr)
                    if use_windows:
                        r = run_windows(
                            df,
                            n_windows=args.windows,
                            window_days=args.window_days,
                            htf_timeframe=None,
                            ema_period=None,
                            band_pct=0.02,
                            label=label,
                            seed=args.seed,
                            recent_only_frac=args.recent_only_frac,
                        )
                    else:
                        r = run_single(
                            df,
                            htf_timeframe=None,
                            ema_period=None,
                            band_pct=0.02,
                            label=label,
                        )
                    # Tag the threshold so downstream readers can identify
                    # which σ value produced each row.
                    r["entry_std_threshold"] = threshold
                    results.append(r)
            finally:
                _vwap_mod.ENTRY_STD_THRESHOLD = original_threshold
                _vwap_mod._ENTRY_STD_THRESHOLD = original_threshold
            key = "threshold_window_comparison" if use_windows else "threshold_comparison"
            output: dict[str, Any] = {key: results}
        elif args.compare:
            results = []
            for cfg in COMPARE_CONFIGS:
                print(f"Running: {cfg['label']} …", file=sys.stderr)
                if use_windows:
                    r = run_windows(
                        df,
                        n_windows=args.windows,
                        window_days=args.window_days,
                        htf_timeframe=cfg["htf_timeframe"],
                        ema_period=cfg["ema_period"],
                        band_pct=cfg.get("band_pct") or 0.02,
                        label=cfg["label"],
                        seed=args.seed,
                        recent_only_frac=args.recent_only_frac,
                    )
                else:
                    r = run_single(
                        df,
                        htf_timeframe=cfg["htf_timeframe"],
                        ema_period=cfg["ema_period"],
                        band_pct=cfg.get("band_pct") or 0.02,
                        label=cfg["label"],
                    )
                results.append(r)
            key = "window_comparison" if use_windows else "comparison"
            output: dict[str, Any] = {key: results}
        else:
            from src.units.strategies import vwap as _vwap_mod
            _orig_thr = _vwap_mod.ENTRY_STD_THRESHOLD
            if args.entry_threshold is not None:
                _vwap_mod.ENTRY_STD_THRESHOLD = args.entry_threshold
                _vwap_mod._ENTRY_STD_THRESHOLD = args.entry_threshold
            htf_tf = None if args.no_htf else args.htf_timeframe
            ema_p = None if args.no_htf else args.ema_period
            try:
                if use_windows:
                    output = run_windows(
                        df,
                        n_windows=args.windows,
                        window_days=args.window_days,
                        htf_timeframe=htf_tf,
                        ema_period=ema_p,
                        band_pct=args.band_pct,
                        label=args.label,
                        seed=args.seed,
                        recent_only_frac=args.recent_only_frac,
                        sl_std_mult=args.sl_mult,
                        vwap_anchor=args.vwap_anchor,
                    )
                else:
                    output = run_single(
                        df,
                        htf_timeframe=htf_tf,
                        ema_period=ema_p,
                        band_pct=args.band_pct,
                        label=args.label,
                        sl_std_mult=args.sl_mult,
                        vwap_anchor=args.vwap_anchor,
                        emit_trades=args.emit_trades,
                    )
            finally:
                if args.entry_threshold is not None:
                    _vwap_mod.ENTRY_STD_THRESHOLD = _orig_thr
                    _vwap_mod._ENTRY_STD_THRESHOLD = _orig_thr
    except Exception as exc:
        sys.stderr.write(f"{type(exc).__name__}: {exc}\n")
        traceback.print_exc(file=sys.stderr)
        return 1

    # Print a human-readable regime-coverage summary to stderr so the
    # operator sees at a glance whether the windows span enough regimes
    # to draw robust conclusions. The JSON on stdout (below) carries
    # the per-config × per-regime breakdown for tooling.
    try:
        _print_regime_coverage(output, args.windows)
    except Exception as exc:  # noqa: BLE001
        print(f"(regime coverage summary failed: {exc})", file=sys.stderr)

    # Single compact line so ``tail -1`` in wrapper scripts gets the JSON.
    print(json.dumps(output, default=str))
    return 0


def _print_regime_coverage(output: dict[str, Any], n_windows: int) -> None:
    """Stderr-only readable summary of how windows are distributed by
    regime, and per-regime mean_total_r for each config."""
    # Find the per-config results list across all output shapes.
    configs = []
    if "window_comparison" in output:
        configs = output["window_comparison"]
    elif "threshold_window_comparison" in output:
        configs = output["threshold_window_comparison"]
    elif "param_sweep_window" in output:
        configs = output["param_sweep_window"]
    elif "anchor_window_comparison" in output:
        configs = output["anchor_window_comparison"]
    elif "windows" in output:
        configs = [output]
    if not configs or not isinstance(configs, list):
        return

    # Regime distribution is the same across configs (same windows, same
    # seed) so we just take the first.
    first = configs[0]
    windows = first.get("windows") or []
    if not windows:
        return

    regime_counts: dict[str, int] = {}
    for w in windows:
        reg = w.get("regime", {}).get("regime", "unknown")
        regime_counts[reg] = regime_counts.get(reg, 0) + 1

    print("\n===== regime coverage =====", file=sys.stderr)
    if len(windows) < 4:
        print(
            f"  ⚠️  only {len(windows)} window(s) — too few to draw "
            "regime-robust conclusions. Re-run with more --windows.",
            file=sys.stderr,
        )
    total = sum(regime_counts.values())
    for reg, cnt in sorted(regime_counts.items(),
                           key=lambda kv: kv[1], reverse=True):
        pct = 100.0 * cnt / total if total else 0.0
        print(f"  {reg:<28} {cnt:>3}  ({pct:.1f}%)", file=sys.stderr)
    # Coverage warnings — recent crypto tends to lack some regimes.
    expected = {
        "strong-down", "weak-down", "sideways", "weak-up", "strong-up",
    }
    seen_trends = {reg.split("/")[0] for reg in regime_counts.keys()}
    missing = expected - seen_trends
    if missing:
        print(
            f"  ⚠️  missing trend regimes: {sorted(missing)} — backtest "
            "coverage may be biased. Consider widening --days or fetching "
            "older candles.",
            file=sys.stderr,
        )

    # Per-config × per-regime PnL table
    print("\n===== per-config × per-regime mean_total_r =====",
          file=sys.stderr)
    print(f"  {'config':<35}  {'overall':>8}  {'per-regime':<40}",
          file=sys.stderr)
    for cfg in configs:
        label = (cfg.get("label") or "?")[:34]
        overall = cfg.get("mean_total_r", 0)
        overall_l = cfg.get("mean_total_r_long")
        overall_s = cfg.get("mean_total_r_short")
        by_reg = cfg.get("by_regime") or []
        reg_str = "  ".join(
            f"{r['regime'].split('/')[0][:6]}={r['mean_total_r']:+.1f}"
            f"(n={r['n_windows']})"
            for r in by_reg
        )
        ls_str = (
            f"  L:{overall_l:+.2f} S:{overall_s:+.2f}"
            if overall_l is not None and overall_s is not None
            else ""
        )
        print(f"  {label:<35}  {overall:+8.2f}{ls_str}  {reg_str}",
              file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
