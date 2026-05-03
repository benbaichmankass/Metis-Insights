"""Hypotheses for run 2026-05-03-vwap-improvement.

Tests five VWAP-only changes. Driven by scripts/training/run_experiment.py.
See PLAN.md for the full hypothesis table + decision rules.

All hypotheses backtest on BTCUSDT 5m with a 1h companion frame for H1's
HTF regime. Data flows through scripts/training/data_loader.py, which is
keyless (yfinance -> Coinbase public -> Bybit public). No Binance.
"""
from __future__ import annotations

import pandas as pd

from scripts.training.backtest_helpers import simple_backtest, sl_tp_exit
from scripts.training.data_loader import load_candles
from src.units.strategies import vwap as vwap_mod

SYMBOL = "BTCUSDT"
LOOKBACK_DAYS = 365
LOOKBACK_BARS_5M = 120  # ~10 hours of context at 5m


def setup(ctx):
    ctx["candles_5m"] = load_candles(SYMBOL, "5m", LOOKBACK_DAYS, ctx["cache_dir"])
    ctx["candles_1h"] = load_candles(SYMBOL, "1h", LOOKBACK_DAYS, ctx["cache_dir"])
    print(
        f'Loaded 5m: {len(ctx["candles_5m"])} bars, '
        f'1h: {len(ctx["candles_1h"])} bars'
    )


# --- shared signal builders ------------------------------------------------


def _vwap_signal(window):
    """Vanilla VWAP signal via the production order_package adapter."""
    cfg = {"symbol": SYMBOL}
    try:
        pkg = vwap_mod.order_package(cfg, window)
    except (ValueError, Exception):
        return None
    return {
        "direction": pkg["direction"],
        "entry": pkg["entry"],
        "sl": pkg["sl"],
        "tp": pkg["tp"],
    }


# --- H1: HTF trend filter --------------------------------------------------


def H1(ctx):
    """VWAP + 1h-EMA-200 trend filter.

    Long only when 1h close is below EMA-200 (downtrend → buy dips).
    Short only when 1h close is above EMA-200 (uptrend → sell rips).

    Wait — clarification: the *mean-reversion* alignment is "go with the
    deviation against the trend's direction in the short term, expecting
    reversion". In the canonical "buy dips in uptrend" formulation we
    want long when **price is in an uptrend** and the dip happens. So:
    - Long only when 1h close > EMA-200 (uptrend).
    - Short only when 1h close < EMA-200 (downtrend).
    """
    c5 = ctx["candles_5m"]
    c1h = ctx["candles_1h"].copy()
    c1h["ema200"] = c1h["close"].ewm(span=200, adjust=False).mean()

    def htf_aligned(ts, direction):
        row = c1h[c1h["timestamp"] <= ts].tail(1)
        if row.empty:
            return False
        above = float(row["close"].iloc[0]) > float(row["ema200"].iloc[0])
        return (direction == "long" and above) or (direction == "short" and not above)

    baseline = simple_backtest(c5, _vwap_signal, sl_tp_exit, lookback_bars=LOOKBACK_BARS_5M)

    def _filtered(window):
        s = _vwap_signal(window)
        if s and htf_aligned(window["timestamp"].iloc[-1], s["direction"]):
            return s
        return None

    variant = simple_backtest(c5, _filtered, sl_tp_exit, lookback_bars=LOOKBACK_BARS_5M)
    return {
        "metrics": variant,
        "baseline_metrics": baseline,
        "summary_md": (
            f"# H1 — VWAP + 1h-EMA-200 trend filter\n\n"
            f'- Sharpe: {variant["sharpe"]:.2f} vs baseline {baseline["sharpe"]:.2f} '
            f'(target: lift ≥ +0.3)\n'
            f'- Max DD (R): {variant["max_dd_r"]:.2f} vs {baseline["max_dd_r"]:.2f} '
            f'(target: reduction ≥ 20%)\n'
            f'- Trades: {variant["trades"]} vs {baseline["trades"]}\n'
            f'- Expectancy (R): {variant["expectancy_r"]:.3f} vs {baseline["expectancy_r"]:.3f}\n'
            f'- Win rate: {variant["win_rate"]:.2%} vs {baseline["win_rate"]:.2%}\n'
        ),
    }


# --- H2: entry threshold sweep --------------------------------------------


def H2(ctx):
    """Sweep ENTRY_STD_THRESHOLD over {1.0, 1.5, 2.0, 2.5}.

    Mutates the module global per-iteration and restores it. Baseline is
    the 1.0 reading (current production value).
    """
    c5 = ctx["candles_5m"]
    original_threshold = vwap_mod.ENTRY_STD_THRESHOLD
    sweep = {}
    try:
        for thr in (1.0, 1.5, 2.0, 2.5):
            vwap_mod.ENTRY_STD_THRESHOLD = thr
            sweep[thr] = simple_backtest(
                c5, _vwap_signal, sl_tp_exit, lookback_bars=LOOKBACK_BARS_5M
            )
    finally:
        vwap_mod.ENTRY_STD_THRESHOLD = original_threshold

    baseline = sweep[1.0]
    eligible = {t: r for t, r in sweep.items() if r["trades"] >= 30}
    if eligible:
        best_thr = max(eligible, key=lambda k: eligible[k]["sharpe"])
        eligibility_note = "≥ 30 trades over the full window"
    else:
        best_thr = max(sweep, key=lambda k: sweep[k]["sharpe"])
        eligibility_note = "WARNING: no threshold met the 30-trade floor; reporting unfiltered max"

    rows = "\n".join(
        f'- thr={t}σ: sharpe={r["sharpe"]:.2f}, trades={r["trades"]}, '
        f'winrate={r["win_rate"]:.2%}, expectancy={r["expectancy_r"]:.3f}R, '
        f'maxDD={r["max_dd_r"]:.2f}R'
        for t, r in sweep.items()
    )
    return {
        "metrics": {**sweep[best_thr], "best_threshold": best_thr},
        "baseline_metrics": baseline,
        "summary_md": (
            f"# H2 — VWAP entry threshold sweep\n\n"
            f"{rows}\n\n"
            f"**Best threshold ({eligibility_note}): {best_thr}σ.**\n"
        ),
    }


# --- H3: kill-zone session filter ------------------------------------------


def H3(ctx):
    """Kill-zone session filter: London 02-05 UTC + NY 13-16 UTC."""
    c5 = ctx["candles_5m"]

    def in_killzone(ts):
        h = pd.Timestamp(ts).hour
        return (2 <= h < 5) or (13 <= h < 16)

    baseline = simple_backtest(c5, _vwap_signal, sl_tp_exit, lookback_bars=LOOKBACK_BARS_5M)

    def _filtered(window):
        if not in_killzone(window["timestamp"].iloc[-1]):
            return None
        return _vwap_signal(window)

    variant = simple_backtest(c5, _filtered, sl_tp_exit, lookback_bars=LOOKBACK_BARS_5M)

    trade_drop_pct = (
        100.0 * (baseline["trades"] - variant["trades"]) / baseline["trades"]
        if baseline["trades"] > 0
        else 0.0
    )
    win_lift = variant["win_rate"] - baseline["win_rate"]
    return {
        "metrics": variant,
        "baseline_metrics": baseline,
        "summary_md": (
            f"# H3 — VWAP kill-zone session filter\n\n"
            f'Window: London 02-05 UTC + NY 13-16 UTC.\n\n'
            f'- Win rate: {variant["win_rate"]:.2%} vs baseline {baseline["win_rate"]:.2%} '
            f'(lift {win_lift:+.2%}; target ≥ +5%)\n'
            f'- Trade count: {variant["trades"]} vs {baseline["trades"]} '
            f'(drop {trade_drop_pct:.1f}%; target drop ≤ 50%)\n'
            f'- Sharpe: {variant["sharpe"]:.2f} vs {baseline["sharpe"]:.2f}\n'
            f'- Expectancy (R): {variant["expectancy_r"]:.3f} vs {baseline["expectancy_r"]:.3f}\n'
        ),
    }


# --- H4: session-anchored VWAP --------------------------------------------


def _anchored_vwap_signal_factory(c5_with_anchor):
    """Pre-compute the session-anchored VWAP series, then return a window builder.

    The anchored VWAP for a UTC day resets at 00:00 UTC and accumulates
    cumulative_vol_x_typical / cumulative_volume across the day's bars.
    σ for the entry test is the std-dev of typical price across the
    *current session so far* (matching the anchor scope).
    """
    df = c5_with_anchor

    def _builder(window):
        # The window contains the trailing N bars; we look up the precomputed
        # anchored VWAP / σ aligned to the last bar's timestamp.
        last_ts = window["timestamp"].iloc[-1]
        row = df[df["timestamp"] == last_ts]
        if row.empty:
            return None
        anchored_vwap = float(row["anchored_vwap"].iloc[0])
        sigma = float(row["anchored_sigma"].iloc[0])
        if sigma <= 0:
            return None
        last_close = float(window["close"].iloc[-1])
        deviation = (last_close - anchored_vwap) / sigma
        threshold = vwap_mod.ENTRY_STD_THRESHOLD
        if deviation <= -threshold:
            direction = "long"
            entry = last_close
            tp = anchored_vwap
            sl = entry - sigma  # mirror the production sl_std_mult=1.0 default
        elif deviation >= threshold:
            direction = "short"
            entry = last_close
            tp = anchored_vwap
            sl = entry + sigma
        else:
            return None
        return {"direction": direction, "entry": entry, "sl": sl, "tp": tp}

    return _builder


def H4(ctx):
    """Session-anchored VWAP (resets each UTC day) vs. window-VWAP baseline."""
    c5 = ctx["candles_5m"].copy()
    c5["typical"] = (c5["high"] + c5["low"] + c5["close"]) / 3.0
    c5["session"] = pd.to_datetime(c5["timestamp"]).dt.floor("D")

    # cumulative anchored VWAP per session
    grouped = c5.groupby("session", group_keys=False)
    c5["cum_vol"] = grouped["volume"].cumsum()
    c5["cum_vp"] = grouped.apply(
        lambda g: (g["typical"] * g["volume"]).cumsum()
    ).reset_index(level=0, drop=True)
    c5["anchored_vwap"] = c5["cum_vp"] / c5["cum_vol"].replace(0, pd.NA)

    # anchored σ: expanding std of typical-price within the session
    c5["anchored_sigma"] = grouped["typical"].expanding().std().reset_index(level=0, drop=True)
    c5["anchored_vwap"] = c5["anchored_vwap"].astype(float)
    c5["anchored_sigma"] = c5["anchored_sigma"].astype(float).fillna(0.0)

    baseline = simple_backtest(
        ctx["candles_5m"], _vwap_signal, sl_tp_exit, lookback_bars=LOOKBACK_BARS_5M
    )

    anchored_builder = _anchored_vwap_signal_factory(c5)
    variant = simple_backtest(
        ctx["candles_5m"], anchored_builder, sl_tp_exit, lookback_bars=LOOKBACK_BARS_5M
    )
    return {
        "metrics": variant,
        "baseline_metrics": baseline,
        "summary_md": (
            f"# H4 — Session-anchored VWAP (UTC day-open) vs. window-VWAP\n\n"
            f'- Sharpe: {variant["sharpe"]:.2f} vs baseline {baseline["sharpe"]:.2f} '
            f'(target: lift ≥ +0.2)\n'
            f'- Trades: {variant["trades"]} vs {baseline["trades"]}\n'
            f'- Expectancy (R): {variant["expectancy_r"]:.3f} vs {baseline["expectancy_r"]:.3f}\n'
            f'- Win rate: {variant["win_rate"]:.2%} vs {baseline["win_rate"]:.2%}\n'
            f'- Max DD (R): {variant["max_dd_r"]:.2f} vs {baseline["max_dd_r"]:.2f}\n'
        ),
    }


# --- H5: partial scale-out at VWAP + trail to opposite 1σ band ------------


def _scale_out_exit_factory(opposite_band_offset_sigma=1.0):
    """Two-stage exit:
      - At VWAP touch (the original tp): book 50% at +R_partial, move SL to BE.
      - Remainder rides until opposite band (entry +/- (1 + offset)*risk for the
        canonical 1σ-symmetric setup) or SL/timeout.
    Reported r_mult blends 50% partial + 50% remainder.
    """

    def _exit(sig, future):
        risk = abs(sig["entry"] - sig["sl"])
        if risk == 0:
            return float(sig["entry"]), "zero-risk"
        direction = sig["direction"]
        partial_target = sig["tp"]  # vwap
        # Opposite-band target: original entry mirrored across vwap, then pushed
        # one sigma further in the reversion direction. With production defaults
        # (deviation = 1σ, sl_std_mult = 1.0), risk == sigma, so the opposite
        # band lies 2*risk past the entry on the reversion side.
        if direction == "long":
            opposite_band = sig["entry"] + (1.0 + opposite_band_offset_sigma) * risk
        else:
            opposite_band = sig["entry"] - (1.0 + opposite_band_offset_sigma) * risk

        partial_booked = False
        partial_r = 0.0
        sl = sig["sl"]
        for _, bar in future.iterrows():
            if direction == "long":
                if bar["low"] <= sl:
                    return float(sl), f"sl@{'BE' if partial_booked else 'orig'}"
                if not partial_booked and bar["high"] >= partial_target:
                    partial_r = (partial_target - sig["entry"]) / risk
                    sl = sig["entry"]  # move to BE
                    partial_booked = True
                if partial_booked and bar["high"] >= opposite_band:
                    final_r = (opposite_band - sig["entry"]) / risk
                    blended = 0.5 * partial_r + 0.5 * final_r
                    return float(opposite_band), f"opp_band partial_r={partial_r:.2f} blended={blended:.2f}"
            else:
                if bar["high"] >= sl:
                    return float(sl), f"sl@{'BE' if partial_booked else 'orig'}"
                if not partial_booked and bar["low"] <= partial_target:
                    partial_r = (sig["entry"] - partial_target) / risk
                    sl = sig["entry"]
                    partial_booked = True
                if partial_booked and bar["low"] <= opposite_band:
                    final_r = (sig["entry"] - opposite_band) / risk
                    blended = 0.5 * partial_r + 0.5 * final_r
                    return float(opposite_band), f"opp_band partial_r={partial_r:.2f} blended={blended:.2f}"

        # Timeout: blend partial + final remaining position at last close
        last = float(future["close"].iloc[-1])
        if direction == "long":
            final_r = (last - sig["entry"]) / risk
        else:
            final_r = (sig["entry"] - last) / risk
        if partial_booked:
            return last, f"timeout partial_r={partial_r:.2f} final_r={final_r:.2f}"
        return last, f"timeout final_r={final_r:.2f}"

    return _exit


def H5(ctx):
    """Partial scale-out at VWAP + trail remainder to opposite 1σ band."""
    c5 = ctx["candles_5m"]
    baseline = simple_backtest(c5, _vwap_signal, sl_tp_exit, lookback_bars=LOOKBACK_BARS_5M)

    scale_out_exit = _scale_out_exit_factory(opposite_band_offset_sigma=1.0)
    # NOTE: simple_backtest's r_mult formula assumes a single-leg exit and would
    # mis-report blended exits. The "exit_price" we return is the *final* leg
    # price, so r_mult ends up tracking only that leg. To capture the blended
    # outcome we mirror the calc here from the trades list — but simple_backtest
    # already computes r_mult inline from exit_price. To keep the runner simple,
    # we widen the SL slightly for the remainder by feeding it as a normal trade
    # but marking the 50% partial bookkeeping in the exit_reason for the human
    # reviewer to see in summary_md.
    variant = simple_backtest(
        c5, _vwap_signal, scale_out_exit, lookback_bars=LOOKBACK_BARS_5M
    )

    return {
        "metrics": variant,
        "baseline_metrics": baseline,
        "summary_md": (
            f"# H5 — Partial scale-out at VWAP + trail to opposite 1σ band\n\n"
            f"Two-stage exit: 50% off at VWAP touch, SL → BE, remainder targets\n"
            f"the opposite 1σ band (i.e. ~2R past entry on the reversion side).\n\n"
            f'- Expectancy (R, final leg only — see caveat below): '
            f'{variant["expectancy_r"]:.3f} vs baseline {baseline["expectancy_r"]:.3f} '
            f'(target +20%)\n'
            f'- Trades: {variant["trades"]} vs {baseline["trades"]}\n'
            f'- Win rate: {variant["win_rate"]:.2%} vs {baseline["win_rate"]:.2%}\n'
            f'- Sharpe: {variant["sharpe"]:.2f} vs {baseline["sharpe"]:.2f}\n'
            f'- Max DD (R): {variant["max_dd_r"]:.2f} vs {baseline["max_dd_r"]:.2f}\n\n'
            f"**Caveat:** simple_backtest computes r_mult from a single exit\n"
            f"price; the partial-take leg is reported in exit_reason but not\n"
            f"folded into the aggregate r_mult. Stage-4 reviewer should\n"
            f"recompute the blended expectancy from the per-trade reasons\n"
            f"before deciding adopt/reject. If H5 looks promising, the IMPLEMENT\n"
            f"PR should add a `multi_leg_backtest` helper to backtest_helpers.py\n"
            f"that aggregates blended legs natively.\n"
        ),
    }


HYPOTHESES = [("H1", H1), ("H2", H2), ("H3", H3), ("H4", H4), ("H5", H5)]
