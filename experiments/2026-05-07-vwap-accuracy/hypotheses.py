"""Hypotheses for run 2026-05-07-vwap-accuracy.

Goal: improve VWAP signal **accuracy** at the live cadence (1.0σ entry
threshold, 0.5σ SL) — not by raising selectivity. See ./PLAN.md.

Six variants (H1..H5 + stacked H6); each compared against a single
baseline that calls ``build_vwap_signal`` directly (which is what
``src/runtime/pipeline.py`` does live).

Timeframe selection
-------------------
``VWAP_EXPERIMENT_TIMEFRAME`` env var picks the base timeframe:
``"1h"`` (default) or ``"5m"``. The HTF reference for H3 is always
4h. Lookback / max-hold / slope-threshold scale with the choice so
the experiment runs cleanly at either cadence.

  * 1h base — uses the local CryptoRobotFr 6-year archive
    (51k bars, 2017-08 → 2023-07). Triggered by the original
    sandbox-only run on 2026-05-07.
  * 5m base — fetches live BTCUSDT 5m + 1h candles via
    ``scripts.training.data_loader.load_candles`` (yfinance →
    Coinbase → Bybit). Designed to run on a GitHub Actions
    runner since the local sandbox blocks all of those hosts.
    See ``run_5m.py`` and ``.github/workflows/training-rerun-5m.yml``.

Production runs at 5m, so the 5m variant is the source-of-truth for
the production-adoption decision. The 1h variant remains as a
6-year out-of-sample stress-test of the same filter set; the
RECOMMENDATIONS.md compares both.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Callable, Dict, Optional

import numpy as np
import pandas as pd

from scripts.training.backtest_helpers import simple_backtest, sl_tp_exit
from src.units.strategies import vwap as vwap_mod

SYMBOL = "BTCUSDT"
TIMEFRAME = os.environ.get("VWAP_EXPERIMENT_TIMEFRAME", "1h")
SL_STD_MULT = vwap_mod.SL_STD_MULT_DEFAULT  # 0.5 (production)
ENTRY_THR = vwap_mod.ENTRY_STD_THRESHOLD     # 1.0 (production)

if TIMEFRAME == "5m":
    LOOKBACK_BARS = 120          # 10 hours at 5m — matches production
    MAX_HOLD_BARS = 96           # 8 hours at 5m
    H2_SLOPE_THR = 0.0015        # 0.15% over 12 5m bars (1 hour)
    BASE_PARQUET_GLOB = "BTCUSDT_5m_*d.parquet"
    HTF_PARQUET_GLOB = "BTCUSDT_4h_*d.parquet"
elif TIMEFRAME == "1h":
    LOOKBACK_BARS = 120          # 5 days at 1h — same numeric LB
    MAX_HOLD_BARS = 96           # 4 days at 1h — same numeric MH
    H2_SLOPE_THR = 0.005         # 0.5% over 12 1h bars (12 hours)
    BASE_PARQUET_GLOB = "BTCUSDT_1h_*d.parquet"
    HTF_PARQUET_GLOB = "BTCUSDT_4h_*d.parquet"
else:
    raise ValueError(f"Unsupported VWAP_EXPERIMENT_TIMEFRAME: {TIMEFRAME!r}")


# ---------------------------------------------------------------------------
# setup — load real BTC candles from on-disk cache
# ---------------------------------------------------------------------------


def _resolve_one(cache: Path, glob: str) -> Path:
    matches = sorted(cache.glob(glob))
    if not matches:
        raise RuntimeError(
            f"Missing cached candles in {cache}: no match for {glob}. "
            "Run fetch_data.py (or the workflow) first."
        )
    return matches[-1]


def setup(ctx: dict) -> None:
    cache = Path(ctx["cache_dir"])
    p_base = _resolve_one(cache, BASE_PARQUET_GLOB)
    p_htf = _resolve_one(cache, HTF_PARQUET_GLOB)
    ctx["candles_1h"] = pd.read_parquet(p_base)  # name kept for back-compat
    ctx["candles_4h"] = pd.read_parquet(p_htf)
    print(f"timeframe={TIMEFRAME} base={p_base.name} htf={p_htf.name}")
    print(
        f"1h bars: {len(ctx['candles_1h'])} "
        f"({ctx['candles_1h']['timestamp'].min()} → {ctx['candles_1h']['timestamp'].max()})"
    )
    print(
        f"4h bars: {len(ctx['candles_4h'])} "
        f"({ctx['candles_4h']['timestamp'].min()} → {ctx['candles_4h']['timestamp'].max()})"
    )


# ---------------------------------------------------------------------------
# Baseline — calls live build_vwap_signal directly
# ---------------------------------------------------------------------------


def _vwap_baseline(window: pd.DataFrame) -> Optional[Dict]:
    """Production VWAP signal. Mirrors what src/runtime/pipeline.py invokes."""
    sig = vwap_mod.build_vwap_signal(window, symbol=SYMBOL, sl_std_mult=SL_STD_MULT)
    if sig.get("side") == "none":
        return None
    return {
        "direction": "long" if sig["side"] == "buy" else "short",
        "entry": float(sig["entry_price"]),
        "sl": float(sig["stop_loss"]),
        "tp": float(sig["take_profit"]),
    }


# ---------------------------------------------------------------------------
# Indicator helpers (operate on the rolling lookback window)
# ---------------------------------------------------------------------------


def _rsi(close: pd.Series, period: int = 14) -> float:
    """Wilder RSI of the final bar of `close`. Returns 50.0 if undefined."""
    delta = close.diff()
    up = delta.clip(lower=0).ewm(alpha=1.0 / period, adjust=False).mean()
    down = (-delta.clip(upper=0)).ewm(alpha=1.0 / period, adjust=False).mean()
    if down.iloc[-1] == 0:
        return 100.0
    rs = up.iloc[-1] / down.iloc[-1]
    return float(100.0 - 100.0 / (1.0 + rs))


def _vwap_slope_pct(window: pd.DataFrame, lag: int = 12) -> float:
    """% change between current rolling VWAP and VWAP `lag` bars earlier.

    Both VWAPs are computed across the same window using cumulative
    typical-price * volume / cumulative volume, so they share an anchor.
    """
    tp = (window["high"] + window["low"] + window["close"]) / 3.0
    cum_pv = (tp * window["volume"]).cumsum()
    cum_v = window["volume"].cumsum().replace(0, np.nan)
    vwap_series = (cum_pv / cum_v).ffill()
    if len(vwap_series) <= lag:
        return 0.0
    cur = float(vwap_series.iloc[-1])
    past = float(vwap_series.iloc[-1 - lag])
    if cur == 0:
        return 0.0
    return abs(cur - past) / cur


def _vol_ratio(window: pd.DataFrame, period: int = 20) -> float:
    """Last-bar volume divided by rolling-`period` mean of prior bars."""
    if len(window) < period + 1:
        return 1.0
    last_vol = float(window["volume"].iloc[-1])
    avg = float(window["volume"].iloc[-1 - period : -1].mean())
    if avg <= 0:
        return 1.0
    return last_vol / avg


# ---------------------------------------------------------------------------
# H1 — Anchored VWAP (UTC daily session reset)
# ---------------------------------------------------------------------------


def _anchored_signal_factory(full_candles: pd.DataFrame) -> Callable:
    """Return a build_signal closure that uses session-anchored VWAP.

    Anchor: UTC midnight of the day containing the last bar of the window.
    σ: standard deviation of typical price across the same session-to-date
    slice. Same 1.0σ entry threshold and 0.5σ SL as production.
    """
    full = full_candles.copy()
    if full["timestamp"].dt.tz is None:
        full["timestamp"] = full["timestamp"].dt.tz_localize("UTC")
    else:
        full["timestamp"] = full["timestamp"].dt.tz_convert("UTC")
    full = full.sort_values("timestamp").reset_index(drop=True)

    def _build(window: pd.DataFrame) -> Optional[Dict]:
        if window.empty or "volume" not in window.columns:
            return None
        last_ts = pd.Timestamp(window["timestamp"].iloc[-1])
        last_ts = last_ts.tz_convert("UTC") if last_ts.tz is not None else last_ts.tz_localize("UTC")
        session_start = last_ts.floor("D")
        sess = full[(full["timestamp"] >= session_start) & (full["timestamp"] <= last_ts)]
        if len(sess) < 5 or sess["volume"].sum() <= 0:
            return None
        tp = (sess["high"] + sess["low"] + sess["close"]) / 3.0
        vwap = float((tp * sess["volume"]).sum() / sess["volume"].sum())
        std = float(tp.std())
        if std <= 0:
            return None
        price = float(sess["close"].iloc[-1])
        deviation = (price - vwap) / std
        if deviation <= -ENTRY_THR:
            direction = "long"
            sl = price - SL_STD_MULT * std
        elif deviation >= ENTRY_THR:
            direction = "short"
            sl = price + SL_STD_MULT * std
        else:
            return None
        return {"direction": direction, "entry": price, "sl": sl, "tp": vwap}

    return _build


def H1(ctx: dict) -> dict:
    """Anchored VWAP (UTC daily session)."""
    c = ctx["candles_1h"]
    baseline = simple_backtest(c, _vwap_baseline, sl_tp_exit, LOOKBACK_BARS, MAX_HOLD_BARS)
    builder = _anchored_signal_factory(c)
    variant = simple_backtest(c, builder, sl_tp_exit, LOOKBACK_BARS, MAX_HOLD_BARS)
    return _hyp_result("H1 — Anchored VWAP (UTC daily session reset)", baseline, variant)


# ---------------------------------------------------------------------------
# H2 — VWAP slope filter (reject when slope > timeframe-scaled threshold
# over the trailing 12 bars). Threshold is set in the timeframe block
# at the top of the file (0.0015 at 5m, 0.005 at 1h).
# ---------------------------------------------------------------------------


def H2(ctx: dict) -> dict:
    c = ctx["candles_1h"]
    baseline = simple_backtest(c, _vwap_baseline, sl_tp_exit, LOOKBACK_BARS, MAX_HOLD_BARS)

    def _build(window: pd.DataFrame) -> Optional[Dict]:
        sig = _vwap_baseline(window)
        if sig is None:
            return None
        if _vwap_slope_pct(window) > H2_SLOPE_THR:
            return None
        return sig

    variant = simple_backtest(c, _build, sl_tp_exit, LOOKBACK_BARS, MAX_HOLD_BARS)
    title = (
        f"H2 — VWAP slope filter (reject when |ΔVWAP/VWAP|₁₂ₕ > {H2_SLOPE_THR:.2%})"
    )
    return _hyp_result(title, baseline, variant)


# ---------------------------------------------------------------------------
# H3 — HTF soft trend filter (4h EMA-200, ±1% band)
# ---------------------------------------------------------------------------

H3_BAND_PCT = 0.01


def H3(ctx: dict) -> dict:
    c = ctx["candles_1h"]
    htf = ctx["candles_4h"].copy()
    htf["ema200"] = htf["close"].ewm(span=200, adjust=False).mean()
    htf_sorted = htf.sort_values("timestamp").reset_index(drop=True)

    baseline = simple_backtest(c, _vwap_baseline, sl_tp_exit, LOOKBACK_BARS, MAX_HOLD_BARS)

    def _htf_lookup(ts: pd.Timestamp) -> Optional[tuple]:
        idx = htf_sorted["timestamp"].searchsorted(ts, side="right") - 1
        if idx < 0:
            return None
        row = htf_sorted.iloc[idx]
        return float(row["close"]), float(row["ema200"])

    def _build(window: pd.DataFrame) -> Optional[Dict]:
        sig = _vwap_baseline(window)
        if sig is None:
            return None
        ts = window["timestamp"].iloc[-1]
        h = _htf_lookup(ts)
        if h is None:
            return None
        close_htf, ema = h
        if sig["direction"] == "long" and close_htf < ema * (1 - H3_BAND_PCT):
            return None
        if sig["direction"] == "short" and close_htf > ema * (1 + H3_BAND_PCT):
            return None
        return sig

    variant = simple_backtest(c, _build, sl_tp_exit, LOOKBACK_BARS, MAX_HOLD_BARS)
    title = f"H3 — HTF soft trend filter (4h EMA-200 ±{H3_BAND_PCT:.0%})"
    return _hyp_result(title, baseline, variant)


# ---------------------------------------------------------------------------
# H4 — RSI(14) confirmation filter
# ---------------------------------------------------------------------------

H4_RSI_LOW = 45.0
H4_RSI_HIGH = 55.0


def H4(ctx: dict) -> dict:
    c = ctx["candles_1h"]
    baseline = simple_backtest(c, _vwap_baseline, sl_tp_exit, LOOKBACK_BARS, MAX_HOLD_BARS)

    def _build(window: pd.DataFrame) -> Optional[Dict]:
        sig = _vwap_baseline(window)
        if sig is None:
            return None
        rsi = _rsi(window["close"], period=14)
        if sig["direction"] == "long" and rsi >= H4_RSI_LOW:
            return None
        if sig["direction"] == "short" and rsi <= H4_RSI_HIGH:
            return None
        return sig

    variant = simple_backtest(c, _build, sl_tp_exit, LOOKBACK_BARS, MAX_HOLD_BARS)
    title = f"H4 — RSI(14) confirmation (BUY < {H4_RSI_LOW}, SELL > {H4_RSI_HIGH})"
    return _hyp_result(title, baseline, variant)


# ---------------------------------------------------------------------------
# H5 — Volume-spike confirmation
# ---------------------------------------------------------------------------

H5_VOL_RATIO = 1.3


def H5(ctx: dict) -> dict:
    c = ctx["candles_1h"]
    baseline = simple_backtest(c, _vwap_baseline, sl_tp_exit, LOOKBACK_BARS, MAX_HOLD_BARS)

    def _build(window: pd.DataFrame) -> Optional[Dict]:
        sig = _vwap_baseline(window)
        if sig is None:
            return None
        if _vol_ratio(window) < H5_VOL_RATIO:
            return None
        return sig

    variant = simple_backtest(c, _build, sl_tp_exit, LOOKBACK_BARS, MAX_HOLD_BARS)
    title = f"H5 — Volume-spike confirmation (vol > {H5_VOL_RATIO}× rolling-20)"
    return _hyp_result(title, baseline, variant)


# ---------------------------------------------------------------------------
# H6 — Stacked best (top-2 by Sharpe among H1..H5)
# ---------------------------------------------------------------------------


def H6(ctx: dict) -> dict:
    c = ctx["candles_1h"]
    htf = ctx["candles_4h"].copy()
    htf["ema200"] = htf["close"].ewm(span=200, adjust=False).mean()
    htf_sorted = htf.sort_values("timestamp").reset_index(drop=True)
    baseline = simple_backtest(c, _vwap_baseline, sl_tp_exit, LOOKBACK_BARS, MAX_HOLD_BARS)
    anchored_builder = _anchored_signal_factory(c)

    def _h2_filter(window, sig):
        return _vwap_slope_pct(window) <= H2_SLOPE_THR

    def _h3_filter(window, sig):
        ts = window["timestamp"].iloc[-1]
        idx = htf_sorted["timestamp"].searchsorted(ts, side="right") - 1
        if idx < 0:
            return False
        row = htf_sorted.iloc[idx]
        if sig["direction"] == "long" and float(row["close"]) < float(row["ema200"]) * (1 - H3_BAND_PCT):
            return False
        if sig["direction"] == "short" and float(row["close"]) > float(row["ema200"]) * (1 + H3_BAND_PCT):
            return False
        return True

    def _h4_filter(window, sig):
        rsi = _rsi(window["close"], period=14)
        if sig["direction"] == "long" and rsi >= H4_RSI_LOW:
            return False
        if sig["direction"] == "short" and rsi <= H4_RSI_HIGH:
            return False
        return True

    def _h5_filter(window, sig):
        return _vol_ratio(window) >= H5_VOL_RATIO

    candidates = [
        ("anchored_vwap", "anchored", []),
        ("slope_filter", "rolling", [_h2_filter]),
        ("htf_soft", "rolling", [_h3_filter]),
        ("rsi_conf", "rolling", [_h4_filter]),
        ("vol_spike", "rolling", [_h5_filter]),
    ]

    def _make_builder(mode: str, filters: list[Callable]):
        def _b(window: pd.DataFrame) -> Optional[Dict]:
            sig = anchored_builder(window) if mode == "anchored" else _vwap_baseline(window)
            if sig is None:
                return None
            for fn in filters:
                if not fn(window, sig):
                    return None
            return sig
        return _b

    ranked = []
    for label, mode, fs in candidates:
        m = simple_backtest(c, _make_builder(mode, fs), sl_tp_exit, LOOKBACK_BARS, MAX_HOLD_BARS)
        ranked.append((label, mode, fs, m))
        print(f"  H6 candidate {label:>14}: sharpe={m['sharpe']:+.2f} "
              f"win={m['win_rate']:.2%} trades={m['trades']} E[R]={m['expectancy_r']:+.4f}")

    qualified = [r for r in ranked if r[3]["trades"] >= 100]
    qualified.sort(key=lambda r: r[3]["sharpe"], reverse=True)
    if len(qualified) < 2:
        qualified = sorted(ranked, key=lambda r: r[3]["sharpe"], reverse=True)
    top = qualified[:2]
    print(f"  H6 stacking top-2: {[t[0] for t in top]}")

    use_anchor_mode = "anchored" if any(t[1] == "anchored" for t in top) else "rolling"
    fs_combined = [f for t in top for f in t[2]]

    variant = simple_backtest(
        c, _make_builder(use_anchor_mode, fs_combined),
        sl_tp_exit, LOOKBACK_BARS, MAX_HOLD_BARS,
    )

    title = (
        f"H6 — Stacked best (top-2 by Sharpe): "
        f"**{top[0][0]}** + **{top[1][0]}** (mode={use_anchor_mode})"
    )
    body = (
        "Per-candidate ranking (≥100 trades qualifies):\n"
        + "\n".join(
            f"- **{label}**: sharpe={m['sharpe']:+.2f} win={m['win_rate']:.2%} "
            f"trades={m['trades']} E[R]={m['expectancy_r']:+.4f}"
            for label, _, _, m in ranked
        )
        + "\n"
    )
    return {
        "metrics": {**variant, "stacked_filters": [t[0] for t in top]},
        "baseline_metrics": baseline,
        "summary_md": _format_summary(title, baseline, variant, body),
    }


# ---------------------------------------------------------------------------
# Output formatting helpers
# ---------------------------------------------------------------------------


def _format_summary(title: str, baseline: dict, variant: dict, extra: str = "") -> str:
    delta_trades = variant["trades"] - baseline["trades"]
    drop_pct = (
        100.0 * (1 - variant["trades"] / baseline["trades"])
        if baseline["trades"] > 0 else 0.0
    )
    return (
        f"# {title}\n\n"
        f"{extra}\n"
        f"| metric | baseline | variant | Δ |\n"
        f"|---|---|---|---|\n"
        f"| trades | {baseline['trades']} | {variant['trades']} | "
        f"{delta_trades:+d} (drop {drop_pct:.1f}%) |\n"
        f"| win_rate | {baseline['win_rate']:.2%} | {variant['win_rate']:.2%} | "
        f"{(variant['win_rate'] - baseline['win_rate']):+.2%} |\n"
        f"| expectancy_R | {baseline['expectancy_r']:+.4f} | {variant['expectancy_r']:+.4f} | "
        f"{(variant['expectancy_r'] - baseline['expectancy_r']):+.4f} |\n"
        f"| sharpe | {baseline['sharpe']:+.2f} | {variant['sharpe']:+.2f} | "
        f"{(variant['sharpe'] - baseline['sharpe']):+.2f} |\n"
        f"| max_dd_R | {baseline['max_dd_r']:+.2f} | {variant['max_dd_r']:+.2f} | "
        f"{(variant['max_dd_r'] - baseline['max_dd_r']):+.2f} |\n"
    )


def _hyp_result(title: str, baseline: dict, variant: dict) -> dict:
    return {
        "metrics": variant,
        "baseline_metrics": baseline,
        "summary_md": _format_summary(title, baseline, variant),
    }


HYPOTHESES = [("H1", H1), ("H2", H2), ("H3", H3), ("H4", H4), ("H5", H5), ("H6", H6)]
