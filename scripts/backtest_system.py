#!/usr/bin/env python3
"""System / portfolio backtest — all strategies on ONE shared account.

WHY THIS EXISTS (operator directive 2026-05-30). The per-strategy harnesses
(scripts/backtest_{trend,fade,squeeze,fvg_range}.py) test each strategy ALONE,
in R-multiples, with UNCONSTRAINED capital. That proves a strategy's own edge —
the necessary first gate — but it is NOT how the money behaves live, because on
a shared account+symbol the strategies do not run independent positions: the
live runtime NETS their signals into a SINGLE position per symbol via
``src/runtime/intents.py::aggregate_intents`` (same side → max target_qty, NOT a
sum; opposite sides → the higher-priority strategy wins and the loser is
dropped), then sizes that one position against a FINITE shared balance with a
daily-loss cap. So "fade made +14R standalone" tells you nothing about whether
those entries even executed in the system or were out-voted, nor what capital
they tied up.

This harness is the SECOND gate: it replays all strategies together over one
price history, routes their signals through the REAL aggregate_intents, manages
ONE shared netted BTCUSDT position with a finite balance + daily-loss cap +
real per-trade risk sizing, runs the winning strategy's REAL monitor() for the
exit, and reports ACCOUNT-LEVEL equity ($), drawdown ($/%), capital
utilization, and per-strategy attribution. Changing one strategy's params (e.g.
the fade time-stop) or adding a member (e.g. fvg_range_15m) is then measured by
its effect on TOTAL portfolio profit + capital efficiency — not standalone R.

FAITHFULNESS. Signals come from each strategy's REAL ``order_package(cfg,
candles_df)`` and exits from its REAL ``monitor(cfg, candles_df, open_pkg)`` —
the exact functions the live trader calls. The conflict/netting is the REAL
``aggregate_intents``. The only re-implemented piece is the account bookkeeping
(fills at next-bar open, fees, equity, daily-loss cap), which the live exchange
owns and a backtest must simulate.

COVERAGE (v1). The BTCUSDT members with the unified order_package(cfg,
candles_df)+monitor() shape on cleanly-resamplable TFs: trend_donchian (2h),
fade_breakout_4h (4h), squeeze_breakout_4h (4h), fvg_range_15m (15m). vwap is
``execution: shadow`` (never trades — excluded). ict_scalp_5m + turtle_soup are
deferred (5m cost / turtle's 1m-entry MTF shape) — the registry-driven loader
makes adding them a matter of registering their signal-stream generator. Each
excluded/included member is logged in the run header so coverage is explicit.

PERFORMANCE. order_package is ~1ms/call, so a per-bar scan of a 15m strategy
over 6y is ~220s. Signal streams are therefore generated ONCE per strategy
(cached under runtime_logs/system_backtest/signals/) and the portfolio engine
runs off the cache — re-running with a different account config (balance,
daily-loss cap, roster) is then instant.

Tier-1 research tooling — does not import or alter any live-order path.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import numpy as np
import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT_DIR = str(Path(__file__).resolve().parent)
# Run-as-script puts the script's own dir (scripts/) at sys.path[0], and
# scripts/ml/ is a REAL package (has __init__.py) that shadows the repo-root
# ml/ package — so a lazy `import ml.registry...` resolves to scripts/ml and
# fails with "No module named 'ml.registry'". A *guarded* insert is not enough:
# when the caller sets PYTHONPATH=. (the trainer's invocation), repo_root is
# already in sys.path but BEHIND scripts/, so the guard skips the insert and the
# shadow stands. Fix unconditionally: drop the script dir and force repo root to
# the front so `ml.*` / `src.*` always resolve to the repo packages.
sys.path[:] = [p for p in sys.path if os.path.abspath(p) != _SCRIPT_DIR]
sys.path.insert(0, str(_REPO_ROOT))

from src.runtime.intents import StrategyIntent, aggregate_intents  # noqa: E402

# --- Optional evidence-layer deps (regime/vol stamping, conviction sizing) ---
# Guarded so a partial environment (e.g. no ML predictor stack) NEVER breaks
# the default harness path: each import failure degrades the corresponding
# evidence feature to a graceful no-op, not a crash. The default run (no new
# flags) does not depend on any of these.
try:
    from src.runtime.regime.detector import detect_regime as _detect_regime  # noqa: E402
except Exception:  # noqa: BLE001
    _detect_regime = None
try:
    from src.runtime.regime.vol_detector import detect_vol_regime as _detect_vol_regime  # noqa: E402
except Exception:  # noqa: BLE001
    _detect_vol_regime = None
try:
    from src.runtime.conviction import compute_conviction as _compute_conviction  # noqa: E402
except Exception:  # noqa: BLE001
    _compute_conviction = None

FEE_BPS_ROUNDTRIP = 7.5
# Per-trade risk budget a conviction=1.0 trade reaches (mirrors
# src.runtime.conviction_sizing.PER_TRADE_RISK_BUDGET = 2%). Used by the
# --conviction-sizing A/B so the conviction-scaled size matches the live
# would-be-size math (conviction × budget × basis / stop_dist).
_CONVICTION_RISK_BUDGET = 0.02
_SIG_CACHE = _REPO_ROOT / "runtime_logs" / "system_backtest" / "signals"


# --------------------------------------------------------------------------
# Roster: name -> (module path, timeframe). The order_package + monitor are
# imported from the live unit; the timeframe is the strategy's setup TF and
# MUST track config/strategies.yaml::<name>.timeframe — `_run_one` resamples the
# 5m base to spec["tf"] (line ~194) while merging the live cfg, so a drifted tf
# here silently backtests the wrong bars (trend_donchian was 2h vs live 1h until
# 2026-06-26, PB-20260626-006 / T0.1 audit). Keep this curated BTCUSDT subset
# aligned to the live roster's canonical headline strategies.
# vwap excluded (execution: shadow). turtle_soup + ict_scalp_5m added 2026-05-30
# (full live-roster coverage). turtle_soup's live adapter is single-TF (the 15m
# setup frame; its legacy 1m-entry confirmation is not in the order_package
# path). ict_scalp_5m needs the 1h EMA-20 HTF bias injected per bar — see
# generate_signal_stream's htf handling — else its HTF gate silently no-ops and
# overstates the signal count.
# --------------------------------------------------------------------------
ROSTER: Dict[str, Dict[str, str]] = {
    "trend_donchian":      {"module": "src.units.strategies.trend_donchian", "tf": "1h"},
    "fade_breakout_4h":    {"module": "src.units.strategies.fade_breakout_4h", "tf": "4h"},
    "squeeze_breakout_4h": {"module": "src.units.strategies.squeeze_breakout_4h", "tf": "4h"},
    "fvg_range_15m":       {"module": "src.units.strategies.fvg_range_15m", "tf": "15m"},
    "turtle_soup":         {"module": "src.units.strategies.turtle_soup", "tf": "15m"},
    "ict_scalp_5m":        {"module": "src.units.strategies.ict_scalp", "tf": "5m"},
    # --- HF prop-pass research candidates (2026-06-16, RESEARCH-ONLY) ---
    # Registered for the research harness ONLY (NOT config/strategies.yaml; NOT
    # the live order path). See docs/research/hf-prop-strategy-research-plan-
    # 2026-06-16.md + runtime_logs/prop_eval/2026-06-16-hf-research/NOTE.md.
    # hf_displacement_cont takes the same per-bar 1h-EMA HTF-bias injection as
    # ict_scalp_5m (generate_signal_stream special-cases both) so its hard
    # HTF trend-alignment gate is fed live-faithfully.
    "hf_displacement_cont": {"module": "src.units.strategies.hf_displacement_cont", "tf": "5m"},
    "hf_vwap_revert":       {"module": "src.units.strategies.hf_vwap_revert", "tf": "5m"},
    # --- Multi-symbol-A research rosters (2026-06-27, #1) ----------------------
    # The ETH/SOL live strategies reuse the SAME logic modules as their BTC
    # siblings (trend_donchian_eth == trend_donchian on ETH config;
    # eth_pullback_2h == htf_pullback_trend_2h on ETH config — verified against
    # config/strategies.yaml). Mapped here so the harness can run a per-symbol
    # vol-split with --symbol ETHUSDT/SOLUSDT (cells key on the live strategy
    # NAME, so the ETH cells are authored under these *_eth names). RESEARCH
    # ONLY; the live order path resolves these from config, not from ROSTER.
    "trend_donchian_eth":    {"module": "src.units.strategies.trend_donchian", "tf": "1h"},
    "trend_donchian_eth_4h": {"module": "src.units.strategies.trend_donchian", "tf": "4h"},
    "eth_pullback_2h":       {"module": "src.units.strategies.htf_pullback_trend_2h", "tf": "2h"},
    "trend_donchian_sol":    {"module": "src.units.strategies.trend_donchian", "tf": "1h"},
    "trend_donchian_sol_4h": {"module": "src.units.strategies.trend_donchian", "tf": "4h"},
    "sol_pullback_2h":       {"module": "src.units.strategies.htf_pullback_trend_2h", "tf": "2h"},
}
_PANDAS_TF = {"5m": "5min", "15m": "15min", "30m": "30min", "1h": "1h", "2h": "2h", "4h": "4h"}


# --------------------------------------------------------------------------
# Data
# --------------------------------------------------------------------------
def _load_candles(path: str) -> pd.DataFrame:
    df = pd.read_parquet(path) if path.endswith(".parquet") else pd.read_csv(path)
    cols = {c.lower(): c for c in df.columns}
    need = ["timestamp", "open", "high", "low", "close"]
    df = df.rename(columns={cols[c]: c for c in need if c in cols and cols[c] != c})
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
    return df.dropna(subset=["timestamp"]).reset_index(drop=True)


def _resample(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    agg = {"open": "first", "high": "max", "low": "min", "close": "last"}
    if "volume" in df.columns:
        agg["volume"] = "sum"
    return (df.set_index("timestamp").resample(rule, label="right", closed="right")
            .agg(agg).dropna().reset_index())


def _date_filter(df, start, end):
    if start:
        df = df[df["timestamp"] >= pd.Timestamp(start, tz="UTC")]
    if end:
        df = df[df["timestamp"] <= pd.Timestamp(end, tz="UTC")]
    return df.reset_index(drop=True)


# --------------------------------------------------------------------------
# Signal-stream generation (REAL order_package per strategy, cached)
# --------------------------------------------------------------------------
def _load_strategy_cfg(name: str) -> dict:
    """Merge config/strategies.yaml params for this strategy (the live cfg)."""
    try:
        from src.units.strategies import load_strategy_config
        return dict((load_strategy_config() or {}).get(name, {}) or {})
    except Exception:  # noqa: BLE001
        return {}


def _import_callable(module: str, attr: str) -> Optional[Callable]:
    import importlib
    try:
        return getattr(importlib.import_module(module), attr)
    except Exception:  # noqa: BLE001
        return None


def _cache_key(name: str, base_path: str, start, end, overrides: dict) -> Path:
    h = hashlib.sha1(
        json.dumps([name, base_path, str(start), str(end), overrides],
                   sort_keys=True, default=str).encode()
    ).hexdigest()[:12]
    return _SIG_CACHE / f"{name}_{h}.parquet"


def _data_fingerprint(df: pd.DataFrame) -> str:
    """Stable identity of the candle feed, for the signal-stream cache key.

    Without this the key hardcoded a constant string, so two DIFFERENT symbols
    over the same window+overrides collided and the second silently reused the
    first's cached signals (a cross-symbol sweep returned identical EV for every
    coin). Fingerprint = bar count + first/last timestamp + first/last close, so
    BTCUSDT and ETHUSDT (etc.) get distinct keys.
    """
    try:
        ts = df["timestamp"]
        return (f"{len(df)}:{ts.iloc[0]}:{ts.iloc[-1]}:"
                f"{float(df['close'].iloc[0]):.6f}:{float(df['close'].iloc[-1]):.6f}")
    except Exception:  # noqa: BLE001 — a degenerate frame falls back to a constant
        return "unknown-feed"


def generate_signal_stream(name: str, base5m: pd.DataFrame, *, start, end,
                           overrides: dict, refresh: bool = False,
                           symbol: str = "BTCUSDT") -> pd.DataFrame:
    """Run the REAL order_package on every closed bar of the strategy's TF.

    Returns a frame [ts, side, entry, sl, tp, confidence, meta_json] with one
    row per bar where the strategy emitted a signal (ValueError = no row).
    Cached to parquet keyed by (strategy, data, window, overrides).
    """
    cache = _cache_key(name, _data_fingerprint(base5m), start, end, overrides)
    if cache.exists() and not refresh:
        try:
            return pd.read_parquet(cache)
        except Exception:  # noqa: BLE001 — a missing/broken parquet engine must
            # not abort the run; fall through and regenerate the stream.
            pass

    spec = ROSTER[name]
    order_package = _import_callable(spec["module"], "order_package")
    if order_package is None:
        raise RuntimeError(f"{name}: no order_package")
    cfg = {"symbol": symbol, "timeframe": spec["tf"], **_load_strategy_cfg(name), **overrides}
    df = _resample(base5m, _PANDAS_TF[spec["tf"]])
    df = _date_filter(df, start, end)

    # ict_scalp_5m HTF bias: the unit's htf_trend_filter blocks trades against
    # the 1h EMA-20 bias, but only when the caller injects cfg["htf_close"] +
    # cfg["htf_ema"] (otherwise it silently no-ops, overstating the signal
    # count — exactly what the live signal builder computes). Precompute the 1h
    # EMA once over the FULL base feed and as-of-align it to each bar so the
    # in-system stream matches live behaviour. Other strategies: htf_series=None.
    htf_close_arr = htf_ema_arr = None
    if name in ("ict_scalp_5m", "hf_displacement_cont") and bool(cfg.get("htf_trend_filter_enabled", True)):
        htf_tf = _PANDAS_TF.get(str(cfg.get("htf_filter_timeframe") or "1h"), "1h")
        ema_period = int(cfg.get("htf_filter_ema_period") or 20)
        htf = _resample(base5m, htf_tf)
        htf["ema"] = htf["close"].ewm(span=ema_period, adjust=False).mean()
        htf = htf.dropna(subset=["ema"])
        # Vectorized as-of join: for each df bar, the latest 1h close/ema at or
        # before it. merge_asof is O(n) — the prior per-bar .loc filter was
        # O(n²) and stalled the 5m/6y stream (~600k bars).
        merged = pd.merge_asof(
            df[["timestamp"]].sort_values("timestamp"),
            htf[["timestamp", "close", "ema"]].rename(
                columns={"close": "_htf_close", "ema": "_htf_ema"}
            ).sort_values("timestamp"),
            on="timestamp", direction="backward",
        )
        htf_close_arr = merged["_htf_close"].to_numpy()
        htf_ema_arr = merged["_htf_ema"].to_numpy()

    rows = []
    warm = 260
    ts = df["timestamp"]
    for i in range(warm, len(df)):
        window = df.iloc[max(0, i - warm):i + 1]
        bar_cfg = dict(cfg)
        if htf_close_arr is not None:
            hc, he = htf_close_arr[i], htf_ema_arr[i]
            if hc == hc and he == he:  # not NaN
                bar_cfg["htf_close"] = float(hc)
                bar_cfg["htf_ema"] = float(he)
        try:
            pkg = order_package(bar_cfg, candles_df=window)
        except ValueError:
            continue
        except Exception:  # noqa: BLE001 — a strategy bug must not abort the sweep
            continue
        # Opt-in long-only research filter (default OFF): drop short signals so
        # the engine never opens a short — used to A/B a strategy's directional
        # discipline (the trend_donchian flagship is long-only; the prop alt
        # variants were first validated both-sides). No effect unless
        # cfg["long_only"] is truthy (set via the strategy YAML or an override).
        if cfg.get("long_only") and str(pkg.get("direction")) == "short":
            continue
        rows.append({
            "ts": ts.iloc[i], "side": pkg["direction"],
            "entry": float(pkg["entry"]), "sl": float(pkg["sl"]),
            "tp": float(pkg["tp"]), "confidence": float(pkg.get("confidence", 0.0)),
            "meta_json": json.dumps(pkg.get("meta") or {}, default=str),
        })
    out = pd.DataFrame(rows, columns=["ts", "side", "entry", "sl", "tp", "confidence", "meta_json"])
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        out.to_parquet(cache)
    except Exception:  # noqa: BLE001 — caching is an optimization; a missing
        # parquet engine (or unwritable dir) must not abort the backtest.
        pass
    return out


# --------------------------------------------------------------------------
# Evidence layer — regime/vol-axis stamping + ML-vol verdict (Designs A/B)
# --------------------------------------------------------------------------
# These compute the SAME axes the live signal builder stamps onto signal.meta
# (`regime`/`adx_14`/`vol_regime`) so the harness's intents carry what
# ``would_gate`` reads — past-only, from the harness's own candle frame. They
# are all best-effort: a missing detector dep / unresolvable head degrades to
# ``unknown`` (never raises), exactly like the live observe-only path.


def _adx_regime_for_window(window: pd.DataFrame) -> tuple[Optional[str], Optional[float]]:
    """ADX-14 trend regime + adx for the LATEST bar of ``window`` (past-only).

    Returns ``(regime, adx_14)``; ``(None, None)`` when the detector dep is
    unavailable or the window degenerate. Mirrors how the live builder calls
    ``detect_regime`` on the strategy's own candles up to the current bar.
    """
    if _detect_regime is None or window is None or len(window) == 0:
        return None, None
    try:
        out = _detect_regime(window)
        reg = out.get("regime")
        adx = out.get("adx")
        return (str(reg) if reg is not None else None,
                float(adx) if isinstance(adx, (int, float)) else None)
    except Exception:  # noqa: BLE001 — observe-only, never break the stream
        return None, None


def _frozen_vol_regime_for_window(
    window: pd.DataFrame, *, symbol: str, timeframe: str,
) -> Optional[str]:
    """Frozen-edge ``calm``/``volatile`` for the latest bar (replays the live
    ``vol_detector``). ``None`` when the dep / spec is unavailable (offline)."""
    if _detect_vol_regime is None or window is None or len(window) == 0:
        return None
    try:
        out = _detect_vol_regime(window, symbol=symbol, timeframe=timeframe)
        vr = out.get("vol_regime")
        return str(vr) if vr else None
    except Exception:  # noqa: BLE001
        return None


class _MlVolResolver:
    """Resolve + score a regime head's ``P(volatile)`` for the
    ``--vol-verdict=ml`` path (Design A).

    ``stage`` selects the registry stage the head is resolved from:

      * ``advisory`` (default) — the LIVE verdict source per Design A. This is
        what the live ``ml_vol_verdict`` path uses, so the offline replay matches
        production exactly.
      * ``shadow`` — replay a SHADOW-stage regime head **before** its
        ``shadow → advisory`` promotion. This is the "option-2" evidence lever:
        it lets A's vol-gating A/B be measured without first doing the live
        promotion (which is the Tier-3 operator gate). Observe-only — the
        harness never mutates the registry; it only *reads* a shadow head's
        ``predict_proba`` to stamp the would-be ``vol_regime``.

    ``model_id`` pins one exact head (overrides stage discovery) so an evidence
    run scores a specific candidate (e.g. ``btc-regime-15m-lgbm-v2``) with no
    ambiguity. ``prefer_timeframe`` is a soft hint (the harness clock_tf) so that
    when several heads match the stage, one whose id carries that timeframe is
    chosen — keeping a 15m clock on the 15m head.

    Offline (no registry / no datasets) this resolves NO head and every call
    returns ``unknown`` — the caller then falls back to the frozen label and
    counts the fallback. On the live trainer VM the same code path scores real
    heads. Entirely best-effort: any import / resolution / scoring failure marks
    the resolver unavailable and degrades to frozen fallback.
    """

    def __init__(self, *, threshold: float = 0.5, stage: str = "advisory",
                 model_id: Optional[str] = None,
                 prefer_timeframe: Optional[str] = None) -> None:
        self.threshold = float(threshold)
        self.stage = str(stage or "advisory")
        self.pin_model_id = str(model_id) if model_id else None
        self.prefer_timeframe = str(prefer_timeframe) if prefer_timeframe else None
        self.available = False
        self.reason = "unresolved"
        self.model_id: Optional[str] = None
        self.skips: dict[str, int] = {}  # per-window None-reason tallies (diag)
        self._predictor = None
        self._base = None  # the wrapped base predictor (has predict_proba)
        self._spec = None
        self._labels: tuple[str, ...] = ()
        self._resolve()

    def _skip(self, why: str) -> None:
        """Tally a per-window scoring skip + return None (caller falls back)."""
        self.skips[why] = self.skips.get(why, 0) + 1
        return None

    def _resolve(self) -> None:
        try:
            from pathlib import Path as _Path

            from ml.registry.model_registry import ModelRegistry
            from ml.shadow.factory import DEFAULT_REGISTRY_ROOT, resolve_predictors
            from src.runtime.regime_shadow import regime_spec_of

            registry = ModelRegistry(_Path(DEFAULT_REGISTRY_ROOT))
            # Candidate ids: an explicit pin wins; otherwise every head at the
            # requested registry stage. Prefer non-yz (yz heads saturate live —
            # the same skip vol_detector does) and, softly, the clock timeframe.
            if self.pin_model_id:
                cand_ids = [self.pin_model_id]
            else:
                cand_ids = sorted(
                    e.model_id for e in registry.list()
                    if getattr(e, "target_deployment_stage", None) == self.stage
                )
            if not cand_ids:
                self.reason = f"no_{self.stage}_head"
                return
            predictors = resolve_predictors(cand_ids, registry)
            chosen = None  # (score_tuple, predictor, spec, labels, model_id)
            for predictor in predictors:
                spec = regime_spec_of(predictor)
                if spec is None:
                    continue
                # class_labels live on the PREDICTOR (the wrapped base), NOT in
                # the regime spec dict (which carries vol_bucket_* / symbol / tf).
                # Reading them off the spec yields () and rejects every head.
                labels = tuple(str(c) for c in (
                    getattr(predictor, "class_labels", None)
                    or getattr(getattr(predictor, "wrapped", None),
                               "class_labels", None)
                    or []
                ))
                if "volatile" not in labels:
                    continue
                vol_col = str(spec.get("vol_feature_column") or "rolling_log_return_vol")
                mid = str(getattr(predictor, "model_id", "") or "")
                non_yz = 1 if vol_col == "rolling_log_return_vol" else 0
                tf_ok = 1 if (self.prefer_timeframe is None
                              or self.prefer_timeframe in mid) else 0
                score = (non_yz, tf_ok)
                if chosen is None or score > chosen[0]:
                    chosen = (score, predictor, spec, labels, mid)
                if score == (1, 1):
                    break  # best possible — non-yz and timeframe match
            if chosen is None:
                self.reason = "no_regime_spec"
                return
            _, self._predictor, self._spec, self._labels, self.model_id = chosen
            # resolve_predictors returns a ShadowPredictor wrapper whose public
            # interface is .predict (a scalar); predict_proba lives on the
            # wrapped base (LightGBMMulticlassPredictor). Score off the base.
            self._base = getattr(self._predictor, "wrapped", None) or self._predictor
            self.available = True
            self.reason = "ok"
        except Exception as exc:  # noqa: BLE001 — degrade to frozen fallback
            self.available = False
            # Include the message (not just the type) so an offline / trainer-venv
            # resolution failure names the actual missing module / bad path instead
            # of an opaque "ModuleNotFoundError".
            self.reason = f"resolve_error:{type(exc).__name__}:{exc}"[:300]

    def vol_regime_for_window(
        self, window: pd.DataFrame, *, symbol: str, timeframe: str,
    ) -> Optional[str]:
        """Score the head's ``P(volatile)`` on the latest bar → ``calm``/
        ``volatile`` thresholded at ``self.threshold``; ``None`` on any failure
        (caller falls back to frozen)."""
        if not self.available or window is None or len(window) == 0:
            return self._skip("empty_window")
        try:
            from src.runtime.regime_shadow import (
                closes_from_candles,
                feature_row_for_predictor,
                rolling_log_return_vol,
            )

            closes = closes_from_candles(window)
            row = feature_row_for_predictor(
                self._predictor, {}, closes=closes,
                symbol=symbol, timeframe=timeframe, candles_df=window,
            )
            if row is None:
                # Pinpoint why feature_row_for_predictor declined this window so
                # an offline run reports it (short past-window vs bucket/ohlc vs
                # symbol/timeframe mismatch) instead of an opaque fallback count.
                window_n = int((self._spec or {}).get("vol_window_n") or 20)
                if rolling_log_return_vol(closes, window_n) is None:
                    return self._skip("short_window")
                spec = self._spec or {}
                _nrm = lambda v: str(v or "").strip().upper()  # noqa: E731
                if _nrm(spec.get("symbol")) != _nrm(symbol):
                    return self._skip("symbol_mismatch")
                if _nrm(spec.get("timeframe")) != _nrm(timeframe):
                    return self._skip("timeframe_mismatch")
                return self._skip("row_none_bucket_or_ohlc")
            proba = self._base.predict_proba(row)
            p_vol = float(proba.get("volatile", 0.0))
            return "volatile" if p_vol >= self.threshold else "calm"
        except Exception as exc:  # noqa: BLE001
            return self._skip(f"exc:{type(exc).__name__}:{exc}"[:160])


# --------------------------------------------------------------------------
# Portfolio engine — ONE shared netted BTCUSDT position
# --------------------------------------------------------------------------
@dataclass
class _Position:
    side: str
    qty: float
    entry: float
    sl: float
    tp: float
    owner: str            # the winning strategy whose monitor() runs the exit
    entry_ts: Any
    entry_idx: int
    meta: dict
    notional: float
    regime: Any = None        # ADX trend regime at entry (cell attribution)
    vol_regime: Any = None    # vol_regime at entry (frozen or ML, per --vol-verdict)


@dataclass
class _ClosedTrade:
    owner: str
    side: str
    entry_ts: Any
    exit_ts: Any
    entry: float
    exit: float
    qty: float
    pnl: float
    fee: float
    reason: str
    bars_held: int
    regime: Any = None
    vol_regime: Any = None


def run_system_backtest(base5m: pd.DataFrame, *, roster: List[str], start, end,
                        initial_balance: float, risk_pct: float,
                        daily_loss_pct: float, signal_ttl_bars: int,
                        overrides: Dict[str, dict], refresh: bool,
                        clock_tf: str = "15m",
                        flip_policy: str = "reverse",
                        reentry_policy: str = "suppress",
                        attach_full: bool = False,
                        vol_verdict: str = "frozen",
                        ml_vol_threshold: float = 0.5,
                        ml_stage: str = "advisory",
                        ml_model_id: Optional[str] = None,
                        regime_router: str = "off",
                        regime_policy_path: Optional[str] = None,
                        conviction_sizing: bool = False,
                        allocator: str = "off",
                        symbol: str = "BTCUSDT") -> Dict[str, Any]:
    """Drive all `roster` strategies through aggregate_intents on a shared
    account. Clock runs on `clock_tf` bars; at each tick we read each
    strategy's latest live signal (emitted within signal_ttl_bars), net them
    via the REAL aggregate_intents, then open/flip/close ONE shared position
    sized against the running balance, and run the owner's REAL monitor().

    ``reentry_policy`` governs what happens when a fresh same-direction
    signal arrives while a position is already open (BL-20260608-DEMOPNL):

      * ``"suppress"`` (default; models the Option-A FIX + the harness's
        long-standing single-position behaviour) — ignore the re-entry; the
        open position stands until its monitor()/SL/TP exits. One trade =
        one position.
      * ``"net"`` (models CURRENT LIVE one-way-mode behaviour) — ADD to the
        position at the new signal's fill (weighted-average entry, summed
        qty) and OVERWRITE the single SL/TP with the new entry's, exactly
        as a Bybit one-way position nets same-side entries. This is the
        bug the guard removes; comparing ``net`` vs ``suppress`` is the
        walk-forward's apples-to-apples test."""
    # Sizing mirrors the live RiskManager.position_size math (src/units/
    # accounts/risk.py:141): risk_usd = balance * risk_pct; qty = risk_usd /
    # stop_distance. We use the formula directly rather than constructing a
    # RiskManager (which needs an account config + an OrderPackage) so the
    # capital model is transparent and self-contained.
    def _risk_qty(bal: float, rpct: float, entry_px: float, sl_px: float) -> float:
        stop_dist = abs(entry_px - sl_px)
        if stop_dist <= 0 or bal <= 0 or rpct <= 0:
            return 0.0
        return (bal * (rpct / 100.0)) / stop_dist

    # --conviction-sizing A/B (Design B): replace the flat per-trade risk %
    # with conviction × per_trade_risk_budget. OFFLINE the only conviction
    # input available is the calibrated strategy confidence (c_strat) — the ML
    # heads are not replayed for sizing here — so conviction ≈ c_strat (stated
    # limitation in --help + the run summary). Mirrors
    # conviction_sizing.compute_conviction_sizing's ``desired`` math:
    # conviction × (budget × balance) / stop_dist.
    def _conviction_qty(bal: float, entry_px: float, sl_px: float,
                        confidence: float) -> float:
        stop_dist = abs(entry_px - sl_px)
        if stop_dist <= 0 or bal <= 0:
            return 0.0
        conv: Optional[float]
        if _compute_conviction is not None:
            try:
                conv = _compute_conviction({"c_strat": float(confidence)}).conviction
            except Exception:  # noqa: BLE001
                conv = None
        else:
            conv = None
        if conv is None:  # no conviction input → fall back to the c_strat scalar
            conv = max(0.0, min(1.0, float(confidence)))
        risk_usd = conv * _CONVICTION_RISK_BUDGET * bal
        return risk_usd / stop_dist

    # --regime-router on: exercise the REAL hard gate (_hard_regime_gate) by
    # flipping REGIME_ROUTER_ENABLED in-process for the duration of the run,
    # and (if given) point the policy loader at a backtest-LOCAL policy via the
    # existing REGIME_POLICY_PATH override (never the live config/regime_policy.yaml).
    # The intents module caches the loaded policy, so the cache is cleared here
    # and restored on teardown — leaving the process env exactly as found.
    _prev_env: Dict[str, Optional[str]] = {}

    def _set_env(key: str, value: Optional[str]) -> None:
        _prev_env[key] = os.environ.get(key)
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value

    # The policy loader freezes its default path into a module global at import
    # (``regime.policy._REGIME_POLICY_PATH``), so REGIME_POLICY_PATH set after
    # import is NOT picked up by ``load_policy()`` — we therefore patch that
    # global directly for the run (and restore it on teardown) so the
    # backtest-local policy actually drives ``would_gate``.
    _prev_policy_path = {"set": False, "value": None}
    _prev_gate_hooks: Dict[str, Any] = {}

    def _clear_intents_policy_cache() -> None:
        try:
            import src.runtime.intents as _intents_mod
            _intents_mod._REGIME_POLICY_CACHE = None
        except Exception:  # noqa: BLE001
            pass

    def _teardown_env() -> None:
        for key, prev in _prev_env.items():
            if prev is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = prev
        if _prev_policy_path["set"]:
            try:
                import src.runtime.regime.policy as _pol
                _pol._REGIME_POLICY_PATH = _prev_policy_path["value"]
            except Exception:  # noqa: BLE001
                pass
        if _prev_gate_hooks:
            try:
                import src.runtime.intents as _im
                _im._decision_vol_regime = _prev_gate_hooks["decision"]
                _im._emit_ml_vol_shadow_rows = _prev_gate_hooks["shadow_rows"]
            except Exception:  # noqa: BLE001
                pass
        _clear_intents_policy_cache()

    if regime_router == "on":
        _set_env("REGIME_ROUTER_ENABLED", "1")
        # Vol-axis enforce for the offline replay (BL-20260706-VOLGATE-REPLAY):
        # since the #4896 ML-only-enforce guard, the live hard gate drops a
        # trend_vol OFF-cell only when REGIME_ML_VERDICT_MODE=use AND the LIVE
        # per-symbol advisory resolver returns a concrete calm/volatile. Neither
        # holds in a backtest (no live bar cache; the studied heads are often
        # pre-advisory), so a `--regime-router on` run silently stopped gating
        # vol cells — every A/B arm came back identical to ungated (caught
        # 2026-07-06 on the ETH/SOL evidence runs). The replay's contract is
        # that the label THIS RUN stamps on each intent (frozen or ML per
        # --vol-verdict) IS the decision label, so trust it: set the mode env
        # in-process and point the gate's decision hook at the stamped label.
        # Both are restored on teardown; the 1-D trend axis is unchanged.
        _set_env("REGIME_ML_VERDICT_MODE", "use")
        try:
            import src.runtime.intents as _im

            def _stamped_decision(intent, mode):  # noqa: ANN001 — mirror live signature
                v = getattr(intent, "vol_regime", None)
                return v, v, v, "backtest-stamped"

            _prev_gate_hooks["decision"] = _im._decision_vol_regime
            _prev_gate_hooks["shadow_rows"] = _im._emit_ml_vol_shadow_rows
            _im._decision_vol_regime = _stamped_decision
            _im._emit_ml_vol_shadow_rows = lambda c: None
        except Exception:  # noqa: BLE001 — replay degrades to trend-axis-only
            pass
    else:
        # The live regime router is BASELINE-ON (baseline-on + REGIME_ROUTER_DISABLED
        # kill-switch, since the Design-A vol-gate go-live). A backtest must NOT
        # inherit that default — the A/B baseline arm has to stay shadow-only, or
        # every run would hard-gate and the ungated/frozen arms would silently
        # become the gated arm. So a run that isn't `--regime-router on` explicitly
        # disables the router for the duration of the run (restored on teardown).
        _set_env("REGIME_ROUTER_DISABLED", "1")
    if regime_policy_path:
        _set_env("REGIME_POLICY_PATH", str(regime_policy_path))
        try:
            import src.runtime.regime.policy as _pol
            _prev_policy_path["set"] = True
            _prev_policy_path["value"] = _pol._REGIME_POLICY_PATH
            _pol._REGIME_POLICY_PATH = str(regime_policy_path)
        except Exception:  # noqa: BLE001
            pass
    if _prev_env or _prev_policy_path["set"]:
        # Drop any cached policy so the local path / enabled flag takes effect.
        _clear_intents_policy_cache()

    # Evidence-layer setup (Designs A/B). All best-effort: a missing dep / no
    # advisory head degrades to ``unknown``/frozen with a counted fallback.
    ml_resolver = (
        _MlVolResolver(threshold=ml_vol_threshold, stage=ml_stage,
                       model_id=ml_model_id, prefer_timeframe=clock_tf)
        if vol_verdict == "ml" else None
    )
    ev_counts = {
        "intents_stamped": 0,            # intents that received a vol_regime
        "ml_vol_scored": 0,              # bars the ML head produced a label
        "ml_vol_fallback": 0,            # bars ml-mode fell back to frozen/unknown
        "ml_vol_unavailable": ml_resolver is not None and not ml_resolver.available,
        "ml_vol_reason": ml_resolver.reason if ml_resolver is not None else None,
        "conviction_trades": 0,          # opens sized by conviction
        "allocator_multi_candidate_bars": 0,  # bars with >=2 directional candidates
        "allocator_divergences": 0,      # bars the EV-pick != the priority winner
    }

    # 1) signal streams (cached), indexed onto the clock grid
    streams: Dict[str, pd.DataFrame] = {}
    for name in roster:
        streams[name] = generate_signal_stream(
            name, base5m, start=start, end=end,
            overrides=overrides.get(name, {}), refresh=refresh, symbol=symbol)

    clock = _date_filter(_resample(base5m, _PANDAS_TF[clock_tf]), start, end).reset_index(drop=True)
    n = len(clock)
    ts = clock["timestamp"]
    h = clock["high"].to_numpy(float)
    lo = clock["low"].to_numpy(float)
    c = clock["close"].to_numpy(float)

    # map each strategy's signal rows onto clock indices (signal becomes
    # actionable on the NEXT clock bar after its bar close — no lookahead)
    clock_ts = ts.values
    sig_at: Dict[int, Dict[str, dict]] = {}
    for name, s in streams.items():
        for _, r in s.iterrows():
            idx = int(np.searchsorted(clock_ts, np.datetime64(pd.Timestamp(r["ts"])), side="right"))
            if idx >= n:
                continue
            sig_at.setdefault(idx, {})[name] = r.to_dict()

    monitors = {name: _import_callable(ROSTER[name]["module"], "monitor") for name in roster}
    cfgs = {name: {"symbol": symbol, "timeframe": ROSTER[name]["tf"],
                   **_load_strategy_cfg(name), **overrides.get(name, {})} for name in roster}

    balance = initial_balance
    equity_high = initial_balance
    day = None
    day_start_balance = balance
    daily_halted = False

    pos: Optional[_Position] = None
    latest: Dict[str, dict] = {}        # strategy -> its most-recent signal dict
    latest_idx: Dict[str, int] = {}     # strategy -> clock idx of that signal
    closed: List[_ClosedTrade] = []
    equity_curve = []
    util_bars = 0                       # bars with capital deployed
    fee_rate = FEE_BPS_ROUNDTRIP / 10_000.0

    def _close(p: _Position, price: float, ts_i, reason: str, idx_i: int):
        nonlocal balance
        gross = (price - p.entry) * p.qty if p.side == "long" else (p.entry - price) * p.qty
        fee = fee_rate * (p.entry + price) * p.qty
        pnl = gross - fee
        balance += pnl
        closed.append(_ClosedTrade(
            owner=p.owner, side=p.side, entry_ts=p.entry_ts, exit_ts=ts_i,
            entry=p.entry, exit=price, qty=p.qty, pnl=pnl, fee=fee,
            reason=reason, bars_held=idx_i - p.entry_idx,
            regime=p.regime, vol_regime=p.vol_regime))

    for i in range(n):
        # refresh per-day loss budget
        d = pd.Timestamp(ts.iloc[i]).date()
        if d != day:
            day = d
            day_start_balance = balance
            daily_halted = False
        # update latest live signal set (TTL: drop stale signals)
        if i in sig_at:
            for name, row in sig_at[i].items():
                latest[name] = row
                latest_idx[name] = i
        for name in list(latest):
            if i - latest_idx[name] >= signal_ttl_bars:
                latest.pop(name, None)

        # ---- manage the open position (owner's REAL monitor + SL/TP fills) ----
        if pos is not None:
            # intrabar SL/TP first (conservative)
            if pos.side == "long":
                if lo[i] <= pos.sl:
                    _close(pos, pos.sl, ts.iloc[i], "sl", i)
                    pos = None
                elif h[i] >= pos.tp:
                    _close(pos, pos.tp, ts.iloc[i], "tp", i)
                    pos = None
            else:
                if h[i] >= pos.sl:
                    _close(pos, pos.sl, ts.iloc[i], "sl", i)
                    pos = None
                elif lo[i] <= pos.tp:
                    _close(pos, pos.tp, ts.iloc[i], "tp", i)
                    pos = None
            # owner monitor() (trail ratchet / time-decay / explicit close)
            if pos is not None:
                mon = monitors.get(pos.owner)
                if mon is not None:
                    win = clock.iloc[max(0, i - 300):i + 1]
                    open_pkg = {"direction": pos.side, "entry": pos.entry,
                                "sl": pos.sl, "tp": pos.tp, "meta": pos.meta,
                                "created_at": str(pos.entry_ts)}
                    try:
                        verdict = mon(cfgs.get(pos.owner, {}), win, open_pkg)
                    except Exception:  # noqa: BLE001
                        verdict = None
                    if isinstance(verdict, dict):
                        if verdict.get("action") == "close":
                            _close(pos, c[i], ts.iloc[i],
                                   verdict.get("reason", "monitor_close"), i)
                            pos = None
                        elif "sl" in verdict:
                            pos.sl = float(verdict["sl"])
                        elif "tp" in verdict:
                            pos.tp = float(verdict["tp"])

        if pos is not None:
            util_bars += 1

        # ---- regime / vol axes for THIS bar (Design A) ----
        # Computed past-only from the harness's own clock window so the intents
        # carry the same axes ``would_gate`` reads on the live path. The trend
        # axis is ADX-14; the vol axis is the frozen-edge label (or the advisory
        # head's thresholded P(volatile) under --vol-verdict=ml, frozen on
        # fallback). One label per bar, stamped onto every intent that tick.
        regime_label = adx_14_val = None
        bar_vol_regime: Optional[str] = None
        if intents_pending := [n for n in latest if latest[n]["side"] in ("long", "short")]:
            reg_win = clock.iloc[max(0, i - 300):i + 1]
            regime_label, adx_14_val = _adx_regime_for_window(reg_win)
            if ml_resolver is not None:
                bar_vol_regime = ml_resolver.vol_regime_for_window(
                    reg_win, symbol=symbol, timeframe=clock_tf)
                if bar_vol_regime is not None:
                    ev_counts["ml_vol_scored"] += 1
                else:
                    ev_counts["ml_vol_fallback"] += 1
                    bar_vol_regime = _frozen_vol_regime_for_window(
                        reg_win, symbol=symbol, timeframe=clock_tf)
            else:
                bar_vol_regime = _frozen_vol_regime_for_window(
                    reg_win, symbol=symbol, timeframe=clock_tf)
            del intents_pending  # only used as a cheap "any directional intent" guard

        # ---- desired net position from the REAL aggregator ----
        intents = []
        for name, row in latest.items():
            if row["side"] not in ("long", "short"):
                continue
            intents.append(StrategyIntent(
                strategy=name, symbol=symbol, side=row["side"],
                target_qty=1.0, entry=row["entry"], sl=row["sl"], tp=row["tp"],
                confidence=row["confidence"],
                # Stamp the regime axes the live signal builder stamps, so the
                # REAL would_gate (via aggregate_intents) can measure gating.
                regime=regime_label, adx_14=adx_14_val, vol_regime=bar_vol_regime,
                meta={"_stream": True}))
            if bar_vol_regime is not None:
                ev_counts["intents_stamped"] += 1
        # --allocator ev (M18 P2 backtest arm): instead of letting the
        # priority-based aggregator pick the winner among competing candidates,
        # select the candidate with the highest cost-aware EV_R (the same
        # src.runtime.allocator_ev scorer the live soak ranks on) and pass only
        # it to aggregate_intents — so the harness TRADES the EV-pick and we can
        # A/B its realised net R / maxDD against the baseline (allocator=off).
        # Reductive only (it narrows the candidate set; downstream management is
        # identical). Counts divergences for the evidence footer.
        if allocator == "ev" and intents:
            directional = [i for i in intents if i.side in ("long", "short")]
            if len(directional) >= 2:
                ev_counts["allocator_multi_candidate_bars"] += 1
                from src.runtime.allocator_ev import compute_ev_r as _ev_r
                def _intent_ev(i):
                    v = _ev_r(entry=i.entry, sl=i.sl, tp=i.tp, p_win=i.confidence,
                              fee_bps_roundtrip=FEE_BPS_ROUNDTRIP)
                    return v if v is not None else -1.0e9
                ev_pick = max(directional, key=_intent_ev)
                priority_pick = aggregate_intents(directional, symbol=symbol)
                pri_strat = (priority_pick.winning_intent.strategy
                             if priority_pick is not None and priority_pick.winning_intent
                             else None)
                if ev_pick.strategy != pri_strat:
                    ev_counts["allocator_divergences"] += 1
                # Trade the EV-pick: keep it + any same-(strategy,side) reinforcers
                # are irrelevant here (one winner), so pass the singleton.
                intents = [ev_pick]
        desired = aggregate_intents(intents, symbol=symbol) if intents else None
        des_side = desired.side if desired is not None else "flat"

        # ---- reconcile: open / flip / (let monitor handle close) ----
        if not daily_halted and (balance - day_start_balance) <= -abs(daily_loss_pct) / 100.0 * day_start_balance:
            daily_halted = True

        if des_side in ("long", "short"):
            win_name = getattr(desired, "winning_strategy", None) or _winner_name(desired, latest)
            row = latest.get(win_name)
            if row is None:
                pass
            elif pos is None and not daily_halted:
                # open at next-bar open (use current close as the fill proxy)
                fill = c[i]
                if conviction_sizing:
                    qty = _conviction_qty(balance, fill, row["sl"], row["confidence"])
                else:
                    qty = _risk_qty(balance, risk_pct, fill, row["sl"])
                qty = float(qty) if qty else 0.0
                if qty > 0:
                    if conviction_sizing:
                        ev_counts["conviction_trades"] += 1
                    pos = _Position(side=des_side, qty=qty, entry=fill, sl=row["sl"],
                                    tp=row["tp"], owner=win_name, entry_ts=ts.iloc[i],
                                    entry_idx=i, meta=json.loads(row["meta_json"]),
                                    notional=qty * fill,
                                    regime=regime_label, vol_regime=bar_vol_regime)
            elif (
                pos is not None and pos.side == des_side
                and reentry_policy == "net" and not daily_halted
                and i == latest_idx.get(win_name)
            ):
                # CURRENT-LIVE one-way netting: a FRESH same-direction signal
                # (emitted this very bar) adds to the open position and
                # overwrites its single SL/TP — the demo-account growing-short
                # dynamic. ``suppress`` (default/fix) skips this branch so the
                # position stands as one trade. Gated on signal freshness so
                # a stale TTL-held signal doesn't pyramid every bar.
                fill = c[i]
                if conviction_sizing:
                    add_qty = _conviction_qty(balance, fill, row["sl"], row["confidence"])
                else:
                    add_qty = _risk_qty(balance, risk_pct, fill, row["sl"])
                add_qty = float(add_qty) if add_qty else 0.0
                if add_qty > 0:
                    new_qty = pos.qty + add_qty
                    pos.entry = (pos.entry * pos.qty + fill * add_qty) / new_qty
                    pos.qty = new_qty
                    pos.sl = float(row["sl"])   # single SL/TP overwritten by
                    pos.tp = float(row["tp"])   # each new entry (one-way mode)
                    pos.owner = win_name
                    pos.notional = new_qty * fill
            elif pos is not None and pos.side != des_side and not daily_halted:
                # opposite net desire — behaviour governed by flip_policy:
                #   "reverse" (default/live-faithful): close current + open the
                #             new side immediately.
                #   "hold":   keep the current position; ignore the opposite
                #             vote and let the owner's monitor()/SL/TP exit it
                #             naturally (tests whether flip-churn is the cost).
                #   "flat":   close the current position but do NOT re-open
                #             (stand aside on conflict).
                if flip_policy == "hold":
                    pass
                else:
                    _close(pos, c[i], ts.iloc[i], "flip", i)
                    pos = None
                    if flip_policy == "reverse":
                        fill = c[i]
                        if conviction_sizing:
                            qty = _conviction_qty(balance, fill, row["sl"], row["confidence"])
                        else:
                            qty = _risk_qty(balance, risk_pct, fill, row["sl"])
                        qty = float(qty) if qty else 0.0
                        if qty > 0:
                            if conviction_sizing:
                                ev_counts["conviction_trades"] += 1
                            pos = _Position(side=des_side, qty=qty, entry=fill,
                                            sl=row["sl"], tp=row["tp"], owner=win_name,
                                            entry_ts=ts.iloc[i], entry_idx=i,
                                            meta=json.loads(row["meta_json"]),
                                            notional=qty * fill,
                                            regime=regime_label, vol_regime=bar_vol_regime)

        eq = balance + _unrealized(pos, c[i])
        equity_high = max(equity_high, eq)
        equity_curve.append((str(ts.iloc[i]), round(eq, 2)))

    # final mark-to-close
    if pos is not None:
        _close(pos, c[-1], ts.iloc[-1], "eod", n - 1)
        pos = None

    summary = _summarize(closed, equity_curve, base_balance=initial_balance, symbol=symbol,
                         util_bars=util_bars, total_bars=n, roster=roster,
                         params={"initial_balance": initial_balance, "risk_pct": risk_pct,
                                 "daily_loss_pct": daily_loss_pct, "signal_ttl_bars": signal_ttl_bars,
                                 "clock_tf": clock_tf, "flip_policy": flip_policy,
                                 "reentry_policy": reentry_policy,
                                 # Evidence-layer knobs (Designs A/B), echoed so a
                                 # reader knows exactly what ran.
                                 "vol_verdict": vol_verdict,
                                 "ml_vol_threshold": ml_vol_threshold,
                                 "regime_router": regime_router,
                                 "regime_policy_path": regime_policy_path,
                                 "conviction_sizing": conviction_sizing,
                                 "overrides": overrides},
                         data_start=str(ts.iloc[0]) if n else None,
                         data_end=str(ts.iloc[-1]) if n else None)
    # Evidence-layer report block: knobs used + fallback counts so a reader
    # knows exactly what the run measured (esp. ml-vol availability offline).
    summary["evidence"] = {
        "vol_verdict": vol_verdict,
        "ml_vol_threshold": ml_vol_threshold if vol_verdict == "ml" else None,
        "ml_vol_stage": ml_stage if vol_verdict == "ml" else None,
        "ml_vol_model_id": (ml_resolver.model_id if ml_resolver is not None else None),
        "ml_vol_available": (ml_resolver.available if ml_resolver is not None else None),
        "ml_vol_reason": ev_counts["ml_vol_reason"],
        "ml_vol_scored_bars": ev_counts["ml_vol_scored"],
        "ml_vol_fallback_bars": ev_counts["ml_vol_fallback"],
        "ml_vol_skips": (dict(ml_resolver.skips) if ml_resolver is not None else None),
        "intents_stamped_with_vol": ev_counts["intents_stamped"],
        "regime_router": regime_router,
        "regime_policy_path": regime_policy_path,
        "conviction_sizing": conviction_sizing,
        "conviction_sized_opens": ev_counts["conviction_trades"],
        "allocator": allocator,
        "allocator_multi_candidate_bars": ev_counts["allocator_multi_candidate_bars"],
        "allocator_divergences": ev_counts["allocator_divergences"],
        "conviction_input_note": (
            "conviction ≈ calibrated c_strat only (ML heads not replayed for "
            "sizing offline)" if conviction_sizing else None
        ),
    }
    _teardown_env()
    if attach_full:
        # Purely additive (default off): expose the FULL equity curve + closed
        # ledger that _summarize otherwise discards (it serializes only
        # equity_curve_tail). Used by the in-process prop-firm evaluator
        # (scripts/prop/evaluate_prop.py) which needs per-trade pnl/owner/
        # timestamps + the whole curve for daily-bucket / drawdown / consistency
        # math. The CLI never sets this, so the printed + --json output is
        # byte-for-byte unchanged.
        summary["full_equity_curve"] = equity_curve
        summary["closed_trades"] = closed
    return summary


def _winner_name(desired, latest) -> Optional[str]:
    """Resolve the winning strategy from the DesiredPosition (best-effort
    across field-name variants), falling back to the highest-priority live
    signal on the desired side."""
    for attr in ("winning_strategy", "winner", "strategy"):
        v = getattr(desired, attr, None)
        if isinstance(v, str) and v in latest:
            return v
    wi = getattr(desired, "winning_intent", None)
    if wi is not None and getattr(wi, "strategy", None) in latest:
        return wi.strategy
    from src.runtime.intents import DEFAULT_PRIORITIES
    cands = [n for n, r in latest.items() if r["side"] == desired.side]
    return max(cands, key=lambda n: DEFAULT_PRIORITIES.get(n, 0), default=None)


def _unrealized(pos: Optional[_Position], price: float) -> float:
    if pos is None:
        return 0.0
    return (price - pos.entry) * pos.qty if pos.side == "long" else (pos.entry - price) * pos.qty


def _summarize(closed: List[_ClosedTrade], equity_curve, *, base_balance, util_bars,
               total_bars, roster, params, data_start, data_end,
               symbol: str = "BTCUSDT") -> Dict[str, Any]:
    n = len(closed)
    eq = [e for _, e in equity_curve]
    peak = base_balance
    mdd = 0.0
    for v in eq:
        peak = max(peak, v)
        mdd = max(mdd, peak - v)
    final = eq[-1] if eq else base_balance
    wins = [t for t in closed if t.pnl > 0]
    per_strat: Dict[str, Dict[str, Any]] = {}
    for t in closed:
        s = per_strat.setdefault(t.owner, {"trades": 0, "pnl": 0.0, "wins": 0})
        s["trades"] += 1
        s["pnl"] = round(s["pnl"] + t.pnl, 2)
        s["wins"] += 1 if t.pnl > 0 else 0
    by_reason: Dict[str, int] = {}
    for t in closed:
        by_reason[t.reason] = by_reason.get(t.reason, 0) + 1
    # Per-(strategy, trend_regime, vol_regime, side) cell attribution — the
    # 2-D vol-split of the regime-roster matrix that authors evidence-based
    # `trend_vol` OFF-cells. Only populated when regime/vol stamping ran (i.e.
    # any closed trade carries a regime tag); a default run leaves it empty.
    per_cell: Dict[str, Dict[str, Any]] = {}
    for t in closed:
        if t.regime is None and t.vol_regime is None:
            continue
        key = f"{t.owner}|{t.regime}|{t.vol_regime}|{t.side}"
        c = per_cell.setdefault(key, {"trades": 0, "pnl": 0.0, "wins": 0})
        c["trades"] += 1
        c["pnl"] = round(c["pnl"] + t.pnl, 2)
        c["wins"] += 1 if t.pnl > 0 else 0
    return {
        "kind": "system_backtest", "symbol": symbol, "roster": roster,
        "params": params, "data_start": data_start, "data_end": data_end,
        "run_date": str(date.today()), "fee_bps_roundtrip": FEE_BPS_ROUNDTRIP,
        "initial_balance": base_balance, "final_balance": round(final, 2),
        "net_pnl": round(final - base_balance, 2),
        "return_pct": round(100 * (final - base_balance) / base_balance, 2) if base_balance else 0.0,
        "max_drawdown_usd": round(mdd, 2),
        "max_drawdown_pct": round(100 * mdd / peak, 2) if peak else 0.0,
        "return_dd_ratio": round((final - base_balance) / mdd, 2) if mdd > 0 else None,
        "total_trades": n,
        "win_rate_pct": round(100 * len(wins) / n, 2) if n else 0.0,
        "capital_utilization_pct": round(100 * util_bars / total_bars, 2) if total_bars else 0.0,
        "by_exit_reason": by_reason,
        "per_strategy_attribution": per_strat,
        "per_cell_attribution": per_cell,
        "equity_curve_tail": equity_curve[-5:],
    }


def _fmt(s: Dict[str, Any]) -> str:
    L = [f"system_backtest — {s['symbol']} roster={s['roster']}",
         f"  data {s['data_start']} -> {s['data_end']}  "
         f"bal {s['initial_balance']:.0f} -> {s['final_balance']:.0f}",
         f"  net=${s['net_pnl']:.0f} ({s['return_pct']}%)  maxDD=${s['max_drawdown_usd']:.0f} "
         f"({s['max_drawdown_pct']}%)  ret/DD={s['return_dd_ratio']}",
         f"  trades={s['total_trades']} WR={s['win_rate_pct']}%  "
         f"capital_util={s['capital_utilization_pct']}%  exits={s['by_exit_reason']}",
         "  per-strategy attribution (net $ | trades | wins):"]
    for name, a in sorted(s["per_strategy_attribution"].items(), key=lambda kv: -kv[1]["pnl"]):
        L.append(f"    {name:22} ${a['pnl']:>9.0f}  {a['trades']:>4}t  {a['wins']:>4}w")
    # 2-D cell attribution (strategy|trend|vol|side → net $) — only when stamped.
    # Sorted worst-first so the net-negative OFF-cell candidates lead.
    cells = s.get("per_cell_attribution") or {}
    if cells:
        L.append("  cell attribution strategy|trend|vol|side (net $ | trades | wins) — worst first:")
        for key, a in sorted(cells.items(), key=lambda kv: kv[1]["pnl"]):
            flag = "  <-- OFF candidate" if a["pnl"] < 0 else ""
            L.append(f"    {key:48} ${a['pnl']:>9.0f}  {a['trades']:>4}t  {a['wins']:>4}w{flag}")
    # Evidence-layer footer — printed ONLY when an evidence knob is non-default,
    # so a default run (no new flags) prints byte-for-byte as before.
    ev = s.get("evidence") or {}
    active = (
        ev.get("vol_verdict") not in (None, "frozen")
        or ev.get("regime_router") not in (None, "off")
        or ev.get("conviction_sizing")
    )
    if active:
        L.append("  evidence layer:")
        L.append(
            f"    vol_verdict={ev.get('vol_verdict')} "
            f"regime_router={ev.get('regime_router')} "
            f"conviction_sizing={ev.get('conviction_sizing')}"
        )
        if ev.get("vol_verdict") == "ml":
            L.append(
                f"    ml-vol: stage={ev.get('ml_vol_stage')} "
                f"head={ev.get('ml_vol_model_id')} "
                f"available={ev.get('ml_vol_available')} "
                f"reason={ev.get('ml_vol_reason')} "
                f"scored={ev.get('ml_vol_scored_bars')} "
                f"fell_back_to_frozen={ev.get('ml_vol_fallback_bars')}"
            )
            if not ev.get("ml_vol_available"):
                L.append(
                    "    ml-vol UNAVAILABLE — fell back to frozen on all bars "
                    f"(no {ev.get('ml_vol_stage')}-stage head resolvable here; the "
                    "live trainer run scores real heads)."
                )
        if ev.get("regime_policy_path"):
            L.append(f"    regime_policy={ev.get('regime_policy_path')}")
        if ev.get("conviction_sizing"):
            L.append(
                f"    conviction opens={ev.get('conviction_sized_opens')} "
                f"({ev.get('conviction_input_note')})"
            )
    return "\n".join(L)


def main(argv: List[str]) -> int:
    global FEE_BPS_ROUNDTRIP
    p = argparse.ArgumentParser(description="System/portfolio backtest — all strategies, shared account.")
    p.add_argument("--data", default=os.environ.get("BACKTEST_DATA_PATH", "data/backtest_candles.csv"),
                   help="5m OHLCV CSV/parquet (resampled per strategy TF internally).")
    p.add_argument("--symbol", default="BTCUSDT",
                   help="Symbol the roster trades + the regime head scores "
                        "(default BTCUSDT). For multi-symbol-A: e.g. ETHUSDT with "
                        "--data data/ETHUSDT_5m.csv --roster trend_donchian_eth,...")
    p.add_argument("--start", default=None)
    p.add_argument("--end", default=None)
    p.add_argument("--roster", default=",".join(ROSTER.keys()),
                   help="Comma list of strategies to run together (default: all v1 members).")
    p.add_argument("--initial-balance", type=float, default=10_000.0)
    p.add_argument("--risk-pct", type=float, default=0.3,
                   help="Per-trade risk %% of balance (the shared account's risk_pct).")
    p.add_argument("--daily-loss-pct", type=float, default=3.0,
                   help="Daily-loss cap %% of day-start balance (halts new entries for the day).")
    p.add_argument("--signal-ttl-bars", type=int, default=1,
                   help="Clock bars a strategy's latest signal stays live (1 = act on the freshest only).")
    p.add_argument("--clock-tf", default="15m", choices=list(_PANDAS_TF.keys()))
    p.add_argument("--flip-policy", default="reverse", choices=["reverse", "hold", "flat"],
                   help="On an opposite net vote with a position open: reverse "
                        "(close+open new side, live-faithful), hold (ignore the "
                        "flip, let monitor/SL exit), or flat (close, stand aside).")
    p.add_argument("--reentry-policy", default="suppress", choices=["suppress", "net"],
                   help="Same-direction re-entry while a position is open: "
                        "suppress (Option-A fix / single-position, default) or "
                        "net (model current one-way-mode pyramiding+SL/TP "
                        "overwrite). See BL-20260608-DEMOPNL.")
    p.add_argument("--fee-bps-roundtrip", type=float, default=FEE_BPS_ROUNDTRIP)
    p.add_argument("--override", action="append", default=[], metavar="STRAT.key=val",
                   help="Per-strategy param override, e.g. fade_breakout_4h.timeout_bars=0. Repeatable.")
    p.add_argument("--refresh-signals", action="store_true", help="Ignore the signal cache.")
    # --- Evidence layer (Designs A/B; Tier-1 research). Default-off so a run
    # with none of these is byte-for-byte unchanged. ---
    p.add_argument("--vol-verdict", default="frozen", choices=["frozen", "ml"],
                   help="vol_regime source stamped on intents (Design A): frozen "
                        "(replay vol_detector's frozen-edge label, default) or ml "
                        "(threshold the advisory regime head's P(volatile)). Offline "
                        "with no advisory head, 'ml' degrades to frozen per bar and "
                        "reports the fallback count — it scores real heads only on "
                        "the live trainer run.")
    p.add_argument("--ml-vol-threshold", type=float, default=0.5,
                   help="P(volatile) cut for --vol-verdict=ml (default 0.5).")
    p.add_argument("--ml-stage", dest="ml_stage", default="advisory",
                   choices=["advisory", "shadow"],
                   help="Registry stage the --vol-verdict=ml head is resolved "
                        "from (default advisory — the live verdict source, so the "
                        "replay matches production). 'shadow' replays a "
                        "SHADOW-stage regime head BEFORE its shadow→advisory "
                        "promotion, so A's vol-gating evidence can be gathered "
                        "without the Tier-3 live promotion. Observe-only — never "
                        "mutates the registry stage.")
    p.add_argument("--ml-model-id", dest="ml_model_id", default=None, metavar="ID",
                   help="Pin the exact regime head id for --vol-verdict=ml "
                        "(overrides --ml-stage discovery). Score one specific "
                        "candidate unambiguously, e.g. btc-regime-15m-lgbm-v2.")
    p.add_argument("--regime-router", default="off", choices=["on", "off"],
                   help="Exercise the REAL hard regime gate (_hard_regime_gate) "
                        "in-process (default off → shadow-gate only, no trade change).")
    p.add_argument("--regime-policy", dest="regime_policy", default=None, metavar="PATH",
                   help="Backtest-LOCAL regime_policy.yaml for the gate (sets "
                        "REGIME_POLICY_PATH for the run; never touches the live "
                        "config/regime_policy.yaml). Use to author candidate "
                        "trend_vol OFF-cells without a live edit.")
    p.add_argument("--allocator", default="off", choices=["off", "ev"],
                   help="M18 allocator A/B: 'off' = baseline priority aggregator; "
                        "'ev' = trade the highest-cost-aware-EV_R candidate per bar. "
                        "Run both and compare net R / maxDD to test whether ranking "
                        "the opportunity set by EV beats priority-based routing.")
    p.add_argument("--conviction-sizing", action="store_true",
                   help="A/B sizing (Design B): size opens by conviction × 2%% "
                        "per-trade budget instead of the flat --risk-pct. OFFLINE "
                        "conviction ≈ the calibrated strategy confidence (c_strat) "
                        "only — ML heads are not replayed for sizing; stated in the "
                        "run summary.")
    p.add_argument("--json", dest="json_out", default=None)
    args = p.parse_args(argv[1:])
    FEE_BPS_ROUNDTRIP = args.fee_bps_roundtrip

    overrides: Dict[str, dict] = {}
    for ov in args.override:
        strat, kv = ov.split(".", 1)
        k, v = kv.split("=", 1)
        try:
            v2: Any = int(v)
        except ValueError:
            try:
                v2 = float(v)
            except ValueError:
                v2 = v
        overrides.setdefault(strat, {})[k] = v2

    roster = [r.strip() for r in args.roster.split(",") if r.strip() in ROSTER]
    try:
        base5m = _load_candles(args.data)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: load failed: {exc}", file=sys.stderr)
        return 1
    out = run_system_backtest(
        base5m, roster=roster, start=args.start, end=args.end,
        initial_balance=args.initial_balance, risk_pct=args.risk_pct,
        daily_loss_pct=args.daily_loss_pct, signal_ttl_bars=args.signal_ttl_bars,
        overrides=overrides, refresh=args.refresh_signals, clock_tf=args.clock_tf,
        flip_policy=args.flip_policy, reentry_policy=args.reentry_policy,
        vol_verdict=args.vol_verdict, ml_vol_threshold=args.ml_vol_threshold,
        ml_stage=args.ml_stage, ml_model_id=args.ml_model_id,
        regime_router=args.regime_router, regime_policy_path=args.regime_policy,
        conviction_sizing=args.conviction_sizing, allocator=args.allocator,
        symbol=args.symbol)
    print(_fmt(out))
    if args.json_out:
        payload = json.dumps(out, indent=2, default=str)
        if args.json_out == "-":
            print(payload)
        else:
            Path(args.json_out).write_text(payload)
            print(f"JSON -> {args.json_out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
