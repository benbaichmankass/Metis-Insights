"""Hypotheses for run 2026-05-01-strategy-tuning-dryrun.

Tests 5 changes to turtle_soup + vwap. Driven by scripts/training/run_experiment.py.
See PLAN.md for the full hypothesis table.
"""
from __future__ import annotations

import pandas as pd

from scripts.training.backtest_helpers import simple_backtest, sl_tp_exit
from scripts.training.data_loader import load_candles
from src.units.strategies import turtle_soup as ts_mod
from src.units.strategies import vwap as vwap_mod

SYMBOL = "BTCUSDT"
LOOKBACK_DAYS = 365


def setup(ctx):
    ctx["candles_5m"] = load_candles(SYMBOL, "5m", LOOKBACK_DAYS, ctx["cache_dir"])
    ctx["candles_15m"] = load_candles(SYMBOL, "15m", LOOKBACK_DAYS, ctx["cache_dir"])
    ctx["candles_1h"] = load_candles(SYMBOL, "1h", LOOKBACK_DAYS, ctx["cache_dir"])
    print(f'5m: {len(ctx["candles_5m"])} bars, 15m: {len(ctx["candles_15m"])} bars, 1h: {len(ctx["candles_1h"])} bars')


def _ts_signal(window, tp_r):
    cfg = {"symbol": SYMBOL, "tp1_at_r": tp_r}
    try:
        pkg = ts_mod.order_package(cfg, window)
    except ValueError:
        return None
    return {"direction": pkg["direction"], "entry": pkg["entry"], "sl": pkg["sl"], "tp": pkg["tp"]}


def _vwap_signal(window):
    cfg = {"symbol": SYMBOL, "max_qty": 1.0}
    try:
        pkg = vwap_mod.order_package(cfg, window)
    except ValueError:
        return None
    return {"direction": pkg["direction"], "entry": pkg["entry"], "sl": pkg["sl"], "tp": pkg["tp"]}


def H1(ctx):
    """Turtle Soup: scale out 25% at 1R, trail remainder to 3R."""
    c15 = ctx["candles_15m"]
    baseline = simple_backtest(c15, lambda w: _ts_signal(w, 1.25), sl_tp_exit, lookback_bars=80)

    def _exit_scaleout(sig, future):
        risk = abs(sig["entry"] - sig["sl"])
        first_target = sig["entry"] + (1.0 if sig["direction"] == "long" else -1.0) * risk
        scaled, partial_r = False, 0.0
        for _, bar in future.iterrows():
            if not scaled:
                if (sig["direction"] == "long" and bar["high"] >= first_target) or \
                   (sig["direction"] == "short" and bar["low"] <= first_target):
                    scaled, partial_r = True, 0.25
                    sig = {**sig, "sl": sig["entry"]}   # move SL to BE
            if sig["direction"] == "long":
                if bar["low"] <= sig["sl"]:
                    return float(sig["sl"]), f'sl ({"scaled" if scaled else "orig"}); partial_r={partial_r}'
                if bar["high"] >= sig["tp"]:
                    return float(sig["tp"]), f"tp; partial_r={partial_r}"
            else:
                if bar["high"] >= sig["sl"]:
                    return float(sig["sl"]), f'sl ({"scaled" if scaled else "orig"}); partial_r={partial_r}'
                if bar["low"] <= sig["tp"]:
                    return float(sig["tp"]), f"tp; partial_r={partial_r}"
        return float(future["close"].iloc[-1]), f"timeout; partial_r={partial_r}"

    variant = simple_backtest(c15, lambda w: _ts_signal(w, 3.0), _exit_scaleout, lookback_bars=80)
    return {
        "metrics": variant,
        "baseline_metrics": baseline,
        "summary_md": (
            f"# H1 — Turtle Soup scale-out + trail\n\n"
            f'Variant expectancy {variant["expectancy_r"]:.3f}R vs baseline {baseline["expectancy_r"]:.3f}R | '
            f'trades {variant["trades"]} vs {baseline["trades"]}'
        ),
    }


def H2(ctx):
    """VWAP: HTF trend filter (long only below 1h-EMA-200, short only above)."""
    c5 = ctx["candles_5m"]
    c1h_ema = ctx["candles_1h"].assign(ema200=ctx["candles_1h"]["close"].ewm(span=200).mean())

    def htf_align(ts, direction):
        row = c1h_ema[c1h_ema["timestamp"] <= ts].tail(1)
        if row.empty:
            return False
        above = float(row["close"].iloc[0]) > float(row["ema200"].iloc[0])
        return (direction == "long" and not above) or (direction == "short" and above)

    baseline = simple_backtest(c5, _vwap_signal, sl_tp_exit, lookback_bars=120)

    def _filtered(w):
        s = _vwap_signal(w)
        if s and htf_align(w["timestamp"].iloc[-1], s["direction"]):
            return s
        return None

    variant = simple_backtest(c5, _filtered, sl_tp_exit, lookback_bars=120)
    return {
        "metrics": variant,
        "baseline_metrics": baseline,
        "summary_md": (
            f"# H2 — VWAP + HTF trend filter\n\n"
            f'Sharpe {variant["sharpe"]:.2f} vs {baseline["sharpe"]:.2f} | '
            f'drawdown {variant["max_dd_r"]:.2f}R vs {baseline["max_dd_r"]:.2f}R'
        ),
    }


def H3(ctx):
    """VWAP: sweep ENTRY_STD_THRESHOLD over {1.0, 1.5, 2.0, 2.5}."""
    c5 = ctx["candles_5m"]
    sweep = {}
    for thr in (1.0, 1.5, 2.0, 2.5):
        vwap_mod.ENTRY_STD_THRESHOLD = thr
        sweep[thr] = simple_backtest(c5, _vwap_signal, sl_tp_exit, lookback_bars=120)
    vwap_mod.ENTRY_STD_THRESHOLD = 1.0
    best_thr = max(sweep, key=lambda k: sweep[k]["sharpe"] if sweep[k]["trades"] >= 30 else -1)
    rows = "\n".join(
        f'- thr={t}: sharpe={r["sharpe"]:.2f} trades={r["trades"]} winrate={r["win_rate"]:.2%}'
        for t, r in sweep.items()
    )
    return {
        "metrics": {**sweep[best_thr], "best_threshold": best_thr},
        "baseline_metrics": sweep[1.0],
        "summary_md": f"# H3 — VWAP threshold sweep\n\n{rows}\n\nBest (≥30 trades): thr={best_thr}",
    }


def H4(ctx):
    """Both strategies: kill-zone session filter (London 02-05 UTC + NY 13-16 UTC)."""
    def in_killzone(ts):
        h = pd.Timestamp(ts).hour
        return (2 <= h < 5) or (13 <= h < 16)

    out = {}
    for tag, candles, builder, lb in [
        ("turtle", ctx["candles_15m"], lambda w: _ts_signal(w, 1.25), 80),
        ("vwap", ctx["candles_5m"], _vwap_signal, 120),
    ]:
        baseline = simple_backtest(candles, builder, sl_tp_exit, lookback_bars=lb)

        def _filt(w, _builder=builder):
            if not in_killzone(w["timestamp"].iloc[-1]):
                return None
            return _builder(w)

        variant = simple_backtest(candles, _filt, sl_tp_exit, lookback_bars=lb)
        out[tag] = {"baseline": baseline, "variant": variant}
    rows = "\n".join(
        f'- {k}: sharpe {v["variant"]["sharpe"]:.2f} vs {v["baseline"]["sharpe"]:.2f} | '
        f'trades {v["variant"]["trades"]} vs {v["baseline"]["trades"]} | '
        f'winrate {v["variant"]["win_rate"]:.2%} vs {v["baseline"]["win_rate"]:.2%}'
        for k, v in out.items()
    )
    return {
        "metrics": {f'{k}_variant_sharpe': v["variant"]["sharpe"] for k, v in out.items()},
        "baseline_metrics": {f'{k}_baseline_sharpe': v["baseline"]["sharpe"] for k, v in out.items()},
        "summary_md": f"# H4 — Kill-zone session filter\n\n{rows}",
    }


def H5(ctx):
    """Turtle Soup: restore 1m entry confirmation. yfinance 1m caps at 7 days."""
    candles_1m = load_candles(SYMBOL, "1m", min(LOOKBACK_DAYS, 7), ctx["cache_dir"])
    span_days = (candles_1m["timestamp"].max() - candles_1m["timestamp"].min()).days
    if span_days < 5:
        raise RuntimeError(f"H5 skipped: only {span_days}d of 1m data ({len(candles_1m)} bars); need >=5d")
    cutoff = candles_1m["timestamp"].min()
    c15 = ctx["candles_15m"][ctx["candles_15m"]["timestamp"] >= cutoff].reset_index(drop=True)
    baseline = simple_backtest(c15, lambda w: _ts_signal(w, 1.25), sl_tp_exit, lookback_bars=80)

    def _ts_with_1m_confirm(w):
        s = _ts_signal(w, 1.25)
        if not s:
            return None
        ts15 = w["timestamp"].iloc[-1]
        confirm_window = candles_1m[
            (candles_1m["timestamp"] > ts15)
            & (candles_1m["timestamp"] <= ts15 + pd.Timedelta("20min"))
        ]
        if confirm_window.empty:
            return None
        if s["direction"] == "long" and confirm_window["low"].min() <= s["entry"] * 0.999:
            return s
        if s["direction"] == "short" and confirm_window["high"].max() >= s["entry"] * 1.001:
            return s
        return None

    variant = simple_backtest(c15, _ts_with_1m_confirm, sl_tp_exit, lookback_bars=80)
    return {
        "metrics": variant,
        "baseline_metrics": baseline,
        "summary_md": (
            f"# H5 — Turtle Soup with 1m entry confirmation\n\n"
            f"Window: last {span_days}d (yfinance 1m cap). "
            f'Drawdown {variant["max_dd_r"]:.2f}R vs baseline {baseline["max_dd_r"]:.2f}R | '
            f'trades {variant["trades"]} vs {baseline["trades"]}'
        ),
    }


HYPOTHESES = [("H1", H1), ("H2", H2), ("H3", H3), ("H4", H4), ("H5", H5)]
