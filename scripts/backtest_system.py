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
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

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

from src.runtime import execution_costs  # noqa: E402  (the ONE shared cost model)
from src.research import risk_basis  # noqa: E402  (the ONE definition of live risk + its UNIT)
from src.runtime.intents import StrategyIntent, aggregate_intents  # noqa: E402
# The live override predicate + the canonical bar-length map, imported rather
# than mirrored so the harness measures the arm that actually runs.
from src.runtime.intents import _evaluate_confidence_override  # noqa: E402
from src.runtime.market_data import _TF_SECONDS  # noqa: E402
# P2 · unified engine: the ONE verdict interpreter, shared with the live
# order monitor so the harness cannot re-derive (and silently narrow) what a
# strategy's monitor() verdict means.
from src.runtime.monitor_verdict import (  # noqa: E402
    KIND_MODIFY, KIND_PARTIAL_CLOSE, interpret_verdict,
)

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

# 2026-08-20 (B4): was a hardcoded `7.5` — in a file that imports the ONE shared
# cost model at line 77 and already reads `execution_costs.FUNDING_WINDOW_HOURS`
# below. Both conventions lived here, and different call sites used each: the
# literal fed `_close`'s fee_rate + `roundtrip_cost_r` + the emitted metadata,
# while `roundtrip_cost_usd` took the owner's. They agreed at 7.5 and nothing
# enforced that — the same shape as F-113 (`risk_pct` as a fraction in the live
# sizer and a percent in this same harness fleet, a 5x gap invisible from the
# name). Now an alias, so the value has exactly one home.
FEE_BPS_ROUNDTRIP = execution_costs.DEFAULT_FEE_BPS_ROUNDTRIP
# Execution-realism cost (P1 § 3.B). This harness ALREADY charged a round-trip fee
# (`fee_rate·(entry+exit)·qty` in `_close`), so the fee convention is kept exactly —
# byte-identical PnL. Slippage + perp-only funding are ADDED through the ONE shared
# USD model (execution_costs.roundtrip_cost_usd, fee_bps_roundtrip=0.0), so they can
# never diverge from the R-space harnesses' cost. Both default 0.0 → an in-process
# caller (the trainer's signal-cache driver, sweeps) is byte-identical; only the CLI
# main() applies the mandatory venue-aware policy (funding perp-only).
SLIPPAGE_BPS_ROUNDTRIP = 0.0
FUNDING_BPS_PER_WINDOW = 0.0
FUNDING_WINDOW_HOURS = execution_costs.FUNDING_WINDOW_HOURS
# Per-trade risk budget a conviction=1.0 trade reaches (mirrors
# src.runtime.conviction_sizing.PER_TRADE_RISK_BUDGET = 2%). Used by the
# --conviction-sizing A/B so the conviction-scaled size matches the live
# would-be-size math (conviction × budget × basis / stop_dist).
_CONVICTION_RISK_BUDGET = 0.02
_SIG_CACHE = _REPO_ROOT / "runtime_logs" / "system_backtest" / "signals"

#: One-shot latch so a missing parquet engine warns once per process rather
#: than once per (strategy, window). See the cache write in
#: ``generate_signal_stream``.
_CACHE_WRITE_WARNED = False


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
    # --- The PROP exit variants (2026-08-23) -----------------------------------
    # Registered so `scripts/prop/account_compat_matrix.py` can score the legs
    # that are ACTUALLY routed to `breakout_1`. Without these the mandatory prop
    # gate could only score the BASE twins, which differ from what is routed in
    # exactly the dimension the gate cares about: same module, same 1h clock,
    # same symbol, same `donchian: 20` / `atr_stop_mult: 2.5` entry — and
    # `tp_r` 6.0 / `trail_mult` 3.5 against the base 50.0 / 5.0. Scoring the base
    # and calling it the prop leg's result would be a semantic substitution
    # (`CLAUDE.md` § "Diagnostic provenance", sub-class B: an implicit input
    # standing in for the declared one).
    # Params come from `config/strategies.yaml` by NAME via `_load_strategy_cfg`,
    # so these entries carry the real live exit geometry rather than a copy.
    # RESEARCH ONLY; the live order path resolves strategies from config, never
    # from ROSTER.
    "trend_donchian_sol_prop": {"module": "src.units.strategies.trend_donchian", "tf": "1h"},
    "trend_donchian_eth_prop": {"module": "src.units.strategies.trend_donchian", "tf": "1h"},
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
    except Exception as exc:  # noqa: BLE001 — caching is an optimization; a
        # missing parquet engine (or unwritable dir) must not abort the
        # backtest. But it must not be SILENT either: with no engine installed
        # every cell regenerates every stream, so `--prebuild-cache` becomes a
        # documented flag that does nothing and a walk-forward runs ~6x longer
        # for no reason (measured: a 4-strategy x 4-window precache wrote 0 of
        # 16 parquet files and every one of the 24 cells then re-derived them).
        # Warned ONCE per process — the condition is per-environment, not
        # per-call, so a per-call warning would be pure noise.
        global _CACHE_WRITE_WARNED
        if not _CACHE_WRITE_WARNED:
            _CACHE_WRITE_WARNED = True
            print(
                f"WARNING: signal-stream cache is not being written "
                f"({type(exc).__name__}: {exc}). The run will still be correct, "
                f"but every cell re-derives every stream — install a parquet "
                f"engine (`pip install pyarrow`) to make --prebuild-cache real.",
                file=sys.stderr, flush=True,
            )
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
    confidence: float = 0.0   # decision-time strategy confidence (c_strat) at entry
    entry_sl: Optional[float] = None  # entry-time stop, kept for R-normalized excursions
                                      # (pos.sl is trailed by monitor; this is not)

    def __post_init__(self) -> None:
        # Snapshot the entry-time stop once; pos.sl is later trailed by the
        # owner's monitor, but 1R for the excursion outcomes is defined against
        # the ENTRY stop.
        if self.entry_sl is None:
            self.entry_sl = self.sl


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
    # M30 · C1-for-backtests thread — decision-time facts + candle-window
    # indices so build_backtest_panel's backtest_system adapter can extract the
    # SAME feature vector component_vector reads and slice the held-window bars
    # for native MFE/MAE excursions. Populated on close; the summary/PnL math
    # never reads them (purely additive).
    entry_idx: Optional[int] = None
    exit_idx: Optional[int] = None
    sl: Optional[float] = None   # entry-time stop (for R-normalized excursions)
    meta: dict = field(default_factory=dict)
    confidence: Optional[float] = None
    # Execution-realism cost split (P1 § 3.B) — additive, default 0.0 so a legacy
    # run (no CLI cost flags) records them as 0 and PnL is byte-identical. `pnl`
    # already has fee+slippage+funding deducted; these are the split for reporting.
    slippage: float = 0.0
    funding: float = 0.0


# ---------------------------------------------------------------------------
# M26 conflict taxonomy + the LIVE flip-confidence-override predicate
# ---------------------------------------------------------------------------
# The override itself is NOT re-implemented here — ``_eval_flip_override``
# calls ``src.runtime.intents._evaluate_confidence_override`` directly, so the
# arm measured is the arm that runs. What IS local is the M26 P1 stratification
# (docs/research/M26-P1-conflict-taxonomy-2026-07-22.md § 3a), which is a
# reporting axis rather than a decision input: the live override is TF-ratio
# blind, and the point of the run is to show what that blindness costs.

_M26_TF_RATIO_K = 4.0  # P1 § 3a: r >= K => cross-clock coexistence; r < K => transition


def _tf_minutes(tf: Optional[str]) -> Optional[float]:
    """Bar length in minutes, from the CANONICAL map (market_data._TF_SECONDS).

    Deliberately not a local table — a second definition of "how long is a 4h
    bar" is free to drift from the one the fetcher uses.
    """
    if not tf:
        return None
    secs = _TF_SECONDS.get(str(tf).strip().lower())
    return (float(secs) / 60.0) if secs else None


def _tf_ratio(new_strategy: Optional[str], held_strategy: Optional[str]) -> Optional[float]:
    """M26 P1 § 3a: r = slower clock / faster clock (>= 1 by construction)."""
    a = _tf_minutes((ROSTER.get(new_strategy or "") or {}).get("tf"))
    b = _tf_minutes((ROSTER.get(held_strategy or "") or {}).get("tf"))
    if not a or not b:
        return None
    return max(a, b) / min(a, b)


def _m26_tf_class(ratio: Optional[float]) -> str:
    """`unknown` is a REAL third state, never folded into either class — a
    conflict whose clocks we could not resolve is not evidence of coexistence."""
    if ratio is None:
        return "unknown"
    return "cross_clock" if ratio >= _M26_TF_RATIO_K else "same_clock"


_M26_TF_CLASSES = ("cross_clock", "same_clock", "unknown")


def _tf_class_allowed(tf_class: str, allowed: Optional[frozenset]) -> bool:
    """Is this conflict inside the arm's TF-class restriction?

    ``None`` means UNRESTRICTED and is the live shape: the deployed override is
    TF-ratio blind, so the arm that mirrors production must be too. A non-None
    set is a COUNTERFACTUAL research arm (M26 P1 `A_coexist_crossclock`) that no
    live code path implements — it exists to answer whether the blind arm's loss
    is concentrated in one class, which is the question M26 P0 calls decisive.

    `unknown` fires only if named EXPLICITLY. It is not folded into either class
    because "we could not resolve the two clocks" is not evidence of coexistence
    OR of transition — the same three-state discipline `_m26_tf_class` applies.
    A consequence worth stating because it is a reconciliation check, not a
    rounding error: cross-only fires + same-only fires need NOT equal the blind
    arm's fires, and the shortfall is exactly the `unknown` bucket.
    """
    if allowed is None:
        return True
    return tf_class in allowed


def _parse_tf_classes(spec: Optional[str]) -> Optional[frozenset]:
    """CSV -> class filter. Empty/`all`/None => None (unrestricted, = live)."""
    if spec is None:
        return None
    raw = [s.strip().lower() for s in str(spec).split(",") if s.strip()]
    if not raw or raw == ["all"]:
        return None
    bad = [s for s in raw if s not in _M26_TF_CLASSES]
    if bad:
        # Loud, not permissive: silently dropping an unrecognised class would
        # run a DIFFERENT arm than the one asked for and label it as asked.
        raise ValueError(
            f"unknown tf class(es) {bad!r}; expected a subset of {list(_M26_TF_CLASSES)} "
            f"(or 'all' / empty for the unrestricted, live-shaped arm)")
    return frozenset(raw)


def _position_age_hours(pos, now_ts) -> Optional[float]:
    try:
        delta = pd.Timestamp(now_ts) - pd.Timestamp(pos.entry_ts)
        return float(delta.total_seconds()) / 3600.0
    except Exception:  # noqa: BLE001 — unparseable stamp => age unknown
        return None


def _eval_flip_override(desired, pos, now_ts, row) -> Optional[str]:
    """Call the LIVE predicate. Returns its audit reason, or None to hold.

    Fidelity note (stated because it moves the result): live reads the held
    position's confidence from the journal, where it can be NULL => the
    override is skipped. The harness's ``_Position.confidence`` is always a
    float (defaulting to 0.0), so the harness can fire the override in a case
    live would decline. That biases the measurement TOWARD the override
    looking more active than it is, i.e. against the incumbent — the safe
    direction for a run whose question is "does this arm earn its place".
    """
    return _evaluate_confidence_override(
        desired, float(pos.confidence or 0.0), _position_age_hours(pos, now_ts))


def _conflict_record(pos, new_strategy, row, now_ts, override_reason,
                     *, tf_class: str,
                     would_fire_tf_blind: bool) -> Dict[str, Any]:
    """One row per opposite-direction conflict seen under the `hold` arm.

    This is the run's DENOMINATOR. A run reporting "no PnL difference" is
    uninterpretable without it: zero fired overrides and a genuinely neutral
    arm render identically in the headline, and only this ledger separates
    them.

    THE TWO FIRE FIELDS ARE NOT REDUNDANT and must not be collapsed:
    ``override_fired`` is what THIS arm did; ``would_fire_tf_blind`` is what the
    LIVE (TF-blind) override does at the same conflict. They are equal on the
    unrestricted arm by construction, and their difference on a restricted arm
    IS the measurement — the set of live fires that a TF-aware gate suppresses.
    Recording only the former would make a suppressed conflict indistinguishable
    from one the predicate never wanted, which is the whole quantity of interest.
    """
    age_h = _position_age_hours(pos, now_ts)
    new_conf = float(row.get("confidence", 0.0) or 0.0)
    old_conf = float(pos.confidence or 0.0)
    ratio = _tf_ratio(new_strategy, pos.owner)
    return {
        "ts": str(now_ts),
        "held_strategy": pos.owner,
        "new_strategy": new_strategy,
        "held_side": pos.side,
        "tf_ratio": round(ratio, 3) if ratio is not None else None,
        "tf_class": tf_class,
        "new_confidence": round(new_conf, 4),
        "held_confidence": round(old_conf, 4),
        "confidence_gap": round(new_conf - old_conf, 4),
        "age_hours": round(age_h, 3) if age_h is not None else None,
        "override_fired": override_reason is not None,
        "would_fire_tf_blind": bool(would_fire_tf_blind),
        "suppressed_by_tf_filter": bool(would_fire_tf_blind) and override_reason is None,
        "override_reason": override_reason,
    }


def _summarize_conflicts(conflicts: List[Dict[str, Any]],
                         threshold: float, min_age_hours: float,
                         tf_classes: Optional[frozenset] = None) -> Dict[str, Any]:
    """Roll the conflict ledger up into the gate-by-gate attrition of the
    override predicate, so a zero-fire run says WHICH precondition bound."""
    fired = [c for c in conflicts if c["override_fired"]]
    gap_ok = [c for c in conflicts if c["confidence_gap"] >= threshold] if threshold > 0 else []
    age_ok = [c for c in conflicts
              if c["age_hours"] is not None and c["age_hours"] >= min_age_hours]
    by_class: Dict[str, Dict[str, int]] = {}
    for c in conflicts:
        b = by_class.setdefault(c["tf_class"], {
            "conflicts": 0, "overrides_fired": 0,
            "would_fire_tf_blind": 0, "suppressed_by_tf_filter": 0})
        b["conflicts"] += 1
        if c["override_fired"]:
            b["overrides_fired"] += 1
        if c.get("would_fire_tf_blind"):
            b["would_fire_tf_blind"] += 1
        if c.get("suppressed_by_tf_filter"):
            b["suppressed_by_tf_filter"] += 1
    return {
        "arm": {"flip_confidence_threshold": threshold,
                "flip_min_position_age_hours": min_age_hours,
                # None is the LIVE shape (TF-blind). A list is a counterfactual
                # research arm; stating it in the payload means a reader can
                # never mistake a restricted result for the deployed one.
                "tf_class_filter": (sorted(tf_classes) if tf_classes else None),
                "tf_ratio_k": _M26_TF_RATIO_K},
        "conflicts_observed": len(conflicts),
        "overrides_fired": len(fired),
        # What the DEPLOYED override would do over this same population. Equal
        # to overrides_fired on the unrestricted arm; the gap on a restricted
        # arm is what the TF filter bought or cost.
        "would_fire_tf_blind": sum(1 for c in conflicts if c.get("would_fire_tf_blind")),
        "suppressed_by_tf_filter": sum(1 for c in conflicts
                                       if c.get("suppressed_by_tf_filter")),
        # Gate attrition: how many conflicts cleared EACH precondition
        # independently. Both must hold for a fire, so these bound the fire
        # count from above and identify the binding constraint.
        "passed_confidence_gap": len(gap_ok),
        "passed_min_age": len(age_ok),
        "by_tf_class": by_class,
        "max_confidence_gap_seen": (round(max((c["confidence_gap"] for c in conflicts)), 4)
                                    if conflicts else None),
    }


def run_system_backtest(base5m: pd.DataFrame, *, roster: List[str], start, end,
                        initial_balance: float, risk_pct: float,
                        daily_loss_pct: float, signal_ttl_bars: int,
                        overrides: Dict[str, dict], refresh: bool,
                        clock_tf: str = "15m",
                        flip_policy: str = "reverse",
                        flip_confidence_threshold: float = 0.0,
                        flip_min_position_age_hours: float = 0.0,
                        flip_confgap_tf_classes: Optional[frozenset] = None,
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
                # MUST match _decision_vol_regime's arity exactly. The call
                # sites unpack a fixed-width tuple inside the gate loop, and
                # that loop is fail-permissive — so a stale arity here does not
                # raise loudly, it gets swallowed and the run SILENTLY STOPS
                # GATING. That is the identical failure mode this block was
                # written to fix on 2026-07-06. `p_volatile` is None because a
                # stamped replay has a label but no probability behind it —
                # None is the honest value, never a fabricated 0.0 or 0.5.
                v = getattr(intent, "vol_regime", None)
                return v, v, v, "backtest-stamped", None

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

    # ---- flip-confidence override (live parity) -----------------------------
    # The hold-policy confidence override is resolved by the LIVE module from
    # the environment (``src/runtime/intents.py::resolve_flip_confidence_threshold``
    # / ``resolve_flip_min_position_age_hours``). The harness therefore drives
    # the REAL predicate rather than re-implementing it — the same choice the
    # module header makes for ``aggregate_intents`` ("the only re-implemented
    # piece is the account bookkeeping"). A mirror would be free to drift from
    # the arm it claims to measure, which is the whole defect this run exists
    # to avoid.
    #
    # BOTH keys are pinned UNCONDITIONALLY, including to "0" when the arm is
    # off. This is not defensive noise: the live VM carries
    # FLIP_CONFIDENCE_THRESHOLD=0.15 / FLIP_MIN_POSITION_AGE_HOURS=4.0, so a
    # run on a box that inherits them would silently make the *baseline* arm
    # the override arm and report the two as identical — the same
    # inherited-default trap the REGIME_ROUTER_DISABLED block above exists to
    # close. Restored on teardown.
    _set_env("FLIP_CONFIDENCE_THRESHOLD", str(float(flip_confidence_threshold or 0.0)))
    _set_env("FLIP_MIN_POSITION_AGE_HOURS", str(float(flip_min_position_age_hours or 0.0)))

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
    # Opposite-direction conflicts seen under the `hold` arm, one row each —
    # the denominator for any claim about the flip-confidence override.
    _conflicts: List[Dict[str, Any]] = []

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
    # A monitor() that raises is a BROKEN exit path, not a quiet one. Counted
    # per owner (with one example message) and surfaced in the run summary so a
    # run can never report a clean exit profile over a monitor that never ran.
    monitor_errors: Dict[str, int] = {}
    monitor_error_examples: Dict[str, str] = {}
    fee_rate = FEE_BPS_ROUNDTRIP / 10_000.0

    def _close(p: _Position, price: float, ts_i, reason: str, idx_i: int,
               qty: Optional[float] = None):
        """Book a closed trade for ``qty`` of ``p`` (default: the whole position).

        ``qty`` exists for the partial-close path (P2) — a monitor verdict
        carrying ``close_qty_pct < 1`` books the scaled-out portion here and
        leaves the runner open. It does NOT mutate ``p``; the caller owns the
        remaining-qty bookkeeping, so a full close stays byte-identical.
        """
        nonlocal balance
        q = p.qty if qty is None else float(qty)
        gross = (price - p.entry) * q if p.side == "long" else (p.entry - price) * q
        # Fee convention UNCHANGED (byte-identical): bps on both legs' notional.
        fee = fee_rate * (p.entry + price) * q
        # Execution-realism ADD-ON (P1 § 3.B): slippage + perp-only funding from the
        # ONE shared USD model (fee_bps_roundtrip=0.0 → fee is NOT double-counted).
        # Funding counts the 8h perp windows the hold crossed (entry_ts → ts_i). Both
        # default 0.0 → this is a no-op and PnL is byte-identical to the legacy run.
        extra = execution_costs.roundtrip_cost_usd(
            entry_price=p.entry, qty=q,
            entry_time=p.entry_ts, exit_time=ts_i,
            fee_bps_roundtrip=0.0,
            slippage_bps_roundtrip=SLIPPAGE_BPS_ROUNDTRIP,
            funding_bps_per_window=FUNDING_BPS_PER_WINDOW,
            funding_window_hours=FUNDING_WINDOW_HOURS,
        )
        slippage = extra["slippage_usd"] or 0.0
        funding = extra["funding_usd"] or 0.0
        pnl = gross - fee - slippage - funding
        balance += pnl
        closed.append(_ClosedTrade(
            owner=p.owner, side=p.side, entry_ts=p.entry_ts, exit_ts=ts_i,
            entry=p.entry, exit=price, qty=q, pnl=pnl, fee=fee,
            slippage=slippage, funding=funding,
            reason=reason, bars_held=idx_i - p.entry_idx,
            regime=p.regime, vol_regime=p.vol_regime,
            entry_idx=p.entry_idx, exit_idx=idx_i, sl=p.entry_sl, meta=p.meta,
            confidence=p.confidence))

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
                    except Exception as exc:  # noqa: BLE001
                        # A crashing monitor is NOT "the monitor declined to
                        # act" — swallowing it silently made a broken exit path
                        # indistinguishable from a quiet one (silent-empty
                        # class). Count it so the run summary can report it.
                        verdict = None
                        monitor_errors[pos.owner] = (
                            monitor_errors.get(pos.owner, 0) + 1)
                        if pos.owner not in monitor_error_examples:
                            monitor_error_examples[pos.owner] = (
                                f"{type(exc).__name__}: {exc}")
                    # ONE interpreter, shared with the live order monitor
                    # (src/runtime/monitor_verdict.py). The harness owns only
                    # the EFFECTUATION below; it no longer re-derives what a
                    # verdict means — that re-derivation silently dropped
                    # exit_price, close_qty_pct and next_tp. See the module
                    # docstring for the measured population.
                    decision = interpret_verdict(
                        verdict, current_sl=pos.sl, current_tp=pos.tp)
                    if decision.is_close:
                        # Live fills AT the verdict's exit_price when there is
                        # no exchange fill to read; only fall back to the bar
                        # close when the verdict named no price.
                        px = decision.exit_price
                        if px is None:
                            px = c[i]
                        _close(pos, float(px), ts.iloc[i],
                               decision.reason or "monitor_close", i)
                        pos = None
                    elif decision.kind == KIND_PARTIAL_CLOSE:
                        px = decision.exit_price
                        if px is None:
                            px = c[i]
                        part = pos.qty * float(decision.close_qty_pct or 0.0)
                        if part > 0:
                            _close(pos, float(px), ts.iloc[i],
                                   decision.reason or "monitor_close", i,
                                   qty=part)
                            pos.qty -= part
                            pos.notional = pos.entry * pos.qty
                        # turtle_soup rolls TP1 -> TP2 alongside the scale-out;
                        # without this the runner would exit at the target it
                        # just took profit at.
                        if decision.next_tp is not None:
                            pos.tp = float(decision.next_tp)
                        if pos.qty <= 0:
                            pos = None
                    elif decision.kind == KIND_MODIFY:
                        if decision.sl is not None:
                            pos.sl = decision.sl
                        if decision.tp is not None:
                            pos.tp = decision.tp

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
                                    regime=regime_label, vol_regime=bar_vol_regime,
                                    confidence=float(row.get("confidence", 0.0) or 0.0))
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
                    pos.confidence = float(row.get("confidence", 0.0) or 0.0)
            elif pos is not None and pos.side != des_side and not daily_halted:
                # opposite net desire — behaviour governed by flip_policy:
                #   "reverse" (default/live-faithful): close current + open the
                #             new side immediately.
                #   "hold":   keep the current position; ignore the opposite
                #             vote and let the owner's monitor()/SL/TP exit it
                #             naturally (tests whether flip-churn is the cost).
                #   "flat":   close the current position but do NOT re-open
                #             (stand aside on conflict).
                #
                # The `hold` arm is additionally subject to the LIVE
                # confidence-gap override (FLIP_CONFIDENCE_THRESHOLD /
                # FLIP_MIN_POSITION_AGE_HOURS). When it fires, `hold` behaves
                # exactly as `reverse` — which is what the live
                # ``compute_execution_delta`` does (it falls through to the
                # same flip return). Both knobs default to 0 => the predicate
                # is a no-op and this branch is byte-identical to the previous
                # bare `pass`.
                _override_reason = None
                if flip_policy == "hold":
                    _override_reason = _eval_flip_override(desired, pos, ts.iloc[i], row)
                    # What LIVE does here, captured BEFORE the research-only TF
                    # filter can suppress it — the filter changes this arm's
                    # behaviour, and the deployed baseline has to survive that
                    # for the two to be comparable at all.
                    _would_fire_blind = _override_reason is not None
                    _tf_class = _m26_tf_class(_tf_ratio(win_name, pos.owner))
                    if _override_reason is not None and not _tf_class_allowed(
                            _tf_class, flip_confgap_tf_classes):
                        _override_reason = None
                    _conflicts.append(_conflict_record(
                        pos, win_name, row, ts.iloc[i], _override_reason,
                        tf_class=_tf_class,
                        would_fire_tf_blind=_would_fire_blind))
                if flip_policy == "hold" and _override_reason is None:
                    pass
                else:
                    # Distinct exit reason when the override drove the flip, so
                    # by_exit_reason attributes override churn separately from a
                    # plain `reverse`-arm flip.
                    _close(pos, c[i], ts.iloc[i],
                           "flip_confgap" if _override_reason else "flip", i)
                    pos = None
                    # An override under `hold` must REOPEN — live falls through
                    # to the same `action="flip"` return as `reverse`. Without
                    # the `_override_reason` clause `hold`+override would close
                    # and stand aside, i.e. silently behave as `flat` and
                    # measure the wrong arm.
                    if flip_policy == "reverse" or _override_reason is not None:
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
                                            regime=regime_label, vol_regime=bar_vol_regime,
                                            confidence=float(row.get("confidence", 0.0) or 0.0))

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
                                 # Computed HERE rather than threaded from
                                 # main(), so EVERY caller gets it — including
                                 # the in-process ones (the trainer's
                                 # signal-cache driver, the sweeps) that never
                                 # go through the CLI. A stamp only the CLI
                                 # applied would leave exactly the callers that
                                 # cannot be inspected from a run log ungraded.
                                 # Pure + fail-permissive: an unreadable
                                 # accounts.yaml yields verdict `live_unknown`,
                                 # never a fabricated match.
                                 "risk_basis": risk_basis.compare_to_live(risk_pct),
                                 "daily_loss_pct": daily_loss_pct, "signal_ttl_bars": signal_ttl_bars,
                                 "clock_tf": clock_tf, "flip_policy": flip_policy,
                                 "flip_confidence_threshold": flip_confidence_threshold,
                                 "flip_min_position_age_hours": flip_min_position_age_hours,
                                 "flip_confgap_tf_classes": (sorted(flip_confgap_tf_classes)
                                                             if flip_confgap_tf_classes else None),
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
    # P2 · exit-path fidelity. `monitor_errors` is the honest denominator for
    # the exit profile: a nonzero count means some bars produced NO verdict
    # because the monitor raised, so `by_exit_reason` under-reports
    # monitor-driven exits by an unknown amount. Absent/zero is the clean read.
    summary["monitor_errors"] = {
        "total": sum(monitor_errors.values()),
        "by_owner": dict(monitor_errors),
        "examples": dict(monitor_error_examples),
    }
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
        # Flip-confidence override (BL-20260811). Present on EVERY run, including
        # the disabled baseline, so the two arms are compared on a stated
        # denominator rather than on two headline PnLs whose difference could
        # equally mean "the arm is neutral" or "the arm never fired".
        "flip_override": _summarize_conflicts(
            _conflicts, float(flip_confidence_threshold or 0.0),
            float(flip_min_position_age_hours or 0.0),
            flip_confgap_tf_classes),
    }
    if attach_full:
        summary["flip_conflicts"] = _conflicts
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
        # M30 · C1-for-backtests — the clock-tf candle frame that _ClosedTrade's
        # entry_idx/exit_idx index into, so build_backtest_panel can slice the
        # held-window bars for native MFE/MAE excursions. Additive; the CLI/JSON
        # output is unaffected (attach_full is never set there).
        summary["clock_frame"] = clock
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
    # Execution-realism cost totals (P1 § 3.B). `net_pnl` (= final − base) already
    # nets fee+slippage+funding; `net_pnl_fee_only` adds slippage+funding back so a
    # reader sees the with/without delta without a re-run.
    total_fee = round(sum(t.fee for t in closed), 2)
    total_slippage = round(sum(getattr(t, "slippage", 0.0) for t in closed), 2)
    total_funding = round(sum(getattr(t, "funding", 0.0) for t in closed), 2)
    return {
        "kind": "system_backtest", "symbol": symbol, "roster": roster,
        "params": params, "data_start": data_start, "data_end": data_end,
        "run_date": str(date.today()), "fee_bps_roundtrip": FEE_BPS_ROUNDTRIP,
        # The risk basis this run sized against, next to live's — so a stored
        # result can be graded later without re-deriving what live was that day.
        # `params["risk_pct"]` alone cannot: it carries no unit and no
        # comparison, which is how the 5x gap went unseen.
        "risk_basis": params.get("risk_basis"),
        # Cost config in effect for this run (funding is perp-only → 0 for a non-perp).
        "slippage_bps_roundtrip": SLIPPAGE_BPS_ROUNDTRIP,
        "funding_bps_per_window": FUNDING_BPS_PER_WINDOW,
        "total_fee_usd": total_fee,
        "total_slippage_usd": total_slippage,
        "total_funding_usd": total_funding,
        "initial_balance": base_balance, "final_balance": round(final, 2),
        "net_pnl": round(final - base_balance, 2),
        "net_pnl_fee_only": round(final - base_balance + total_slippage + total_funding, 2),
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
         f"  cost: fee=${s.get('total_fee_usd', 0):.0f} slip=${s.get('total_slippage_usd', 0):.0f}"
         f"({s.get('slippage_bps_roundtrip')}bps) fund=${s.get('total_funding_usd', 0):.0f}"
         f"({s.get('funding_bps_per_window')}bps/8h) → net above vs "
         f"FEE-ONLY net=${s.get('net_pnl_fee_only', s['net_pnl']):.0f}",
         f"  trades={s['total_trades']} WR={s['win_rate_pct']}%  "
         f"capital_util={s['capital_utilization_pct']}%  exits={s['by_exit_reason']}",
         "  per-strategy attribution (net $ | trades | wins):"]
    # Loud, not a footnote: a raising monitor means the exit profile above was
    # measured over bars where the exit path did not run.
    _me = s.get("monitor_errors") or {}
    if _me.get("total"):
        L.insert(len(L) - 1,
                 f"  !! monitor() raised on {_me['total']} bar(s) — exits under-counted: "
                 f"{_me.get('by_owner')} e.g. {list(_me.get('examples', {}).values())[:1]}")
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


def _fmt_risk_grid(arms: List[Dict[str, Any]],
                   risk_report: Dict[str, Any]) -> str:
    """One table across the risk arms, with the caveat that bounds it.

    ⚠️ THE ARMS ARE NOT INDEPENDENT SAMPLES. Same data, same signals, same
    entries — only the sizing differs, so an arm's edge is the same edge under
    a different multiplier. What the sweep DOES answer is whether a conclusion
    SURVIVES a change in risk (drawdown, the daily-loss halt, and compounding
    are all non-linear in it), which is the operator's question.

    ⚠️ AND IT CANNOT ANSWER THE REFUSAL QUESTION. `_risk_qty` returns a
    CONTINUOUS quantity: no whole-contract floor, no `min_qty`, no margin cap.
    Production quantizes and REFUSES sub-1-contract futures orders outright
    (`IB_PLACE_CONFIRM_S` row, BL-20260611-001) and floors Alpaca to whole
    shares. So below some threshold a real trade does not shrink — it does not
    happen, and this harness would still book it. The error is FLATTERING: a
    small-risk arm reads as safe when it may mean the leg never traded. Stated
    here rather than left for a reader to assume, and filed as
    BL-20260820-HARNESS-DOES-NOT-MODEL-QUANTIZATION-REFUSAL.
    """
    live_pct = risk_report.get("live_percent")
    lines = ["", "=" * 78,
             f"RISK GRID — {len(arms)} arm(s) around live {live_pct:g}% "
             f"({risk_report.get('account_id')})",
             "=" * 78,
             f"{'risk%':>8} {'xlive':>7} {'trades':>7} {'net pnl':>12} "
             f"{'maxDD':>10} {'ret/DD':>8}"]
    def _n(v: Any, w: int, spec: str = ".2f") -> str:
        # bool is an int subclass; excluded so a flag never renders as a number.
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return "—".rjust(w)
        return format(v, spec).rjust(w)

    for a in arms:
        r = a["result"] or {}
        # ⚠️ These accessor names are the engine's ACTUAL keys, read off a real
        # run — the first draft guessed `trades` / `max_drawdown` / a
        # self-derived ret/DD and rendered em-dashes over a run that had all
        # three, which is sub-class A (the label names a quantity the accessor
        # does not return) in the very table that warns about it.
        # `return_dd_ratio` is the ENGINE's own figure: re-deriving pnl/dd here
        # would be a second definition free to drift from the one _fmt prints.
        lines.append(
            f"{a['risk_pct']:>8.4g} {a['multiple_of_live']:>6.2f}x "
            f"{_n(r.get('total_trades'), 7, 'd')} "
            f"{_n(r.get('net_pnl'), 12)} "
            f"{_n(r.get('max_drawdown_usd'), 10)} "
            f"{_n(r.get('return_dd_ratio'), 8)}")
    lines += [
        "",
        "The arms share data, signals and entries — only sizing differs, so this",
        "asks whether a conclusion SURVIVES a change in risk, not whether the",
        "arms are independent evidence.",
        "⚠️  It does NOT model production's quantization: futures REFUSE a",
        "    sub-1-contract order and Alpaca floors to whole shares, while",
        "    _risk_qty returns a continuous quantity. A low-risk arm can read",
        "    clean here where the real leg would not have traded at all.",
        "=" * 78, ""]
    return "\n".join(lines)


def _risk_pct_arg(raw: str) -> float | str:
    """`--risk-pct` accepts a number, the literal `live`, or the literal `grid`.

    Returned as the STRING "live" and resolved later, not here: resolution
    reads config/accounts.yaml and must be able to REFUSE loudly, while an
    argparse type that raised would print a usage blurb instead of the reason
    the live value could not be read.
    """
    lowered = str(raw).strip().lower()
    if lowered in ("live", "grid"):
        return lowered
    try:
        return float(raw)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError(
            f"--risk-pct must be a number (percent, e.g. 0.3) or `live`, got {raw!r}")


def main(argv: List[str]) -> int:
    global FEE_BPS_ROUNDTRIP, SLIPPAGE_BPS_ROUNDTRIP, FUNDING_BPS_PER_WINDOW, FUNDING_WINDOW_HOURS
    p = argparse.ArgumentParser(
        description="System/portfolio backtest — all strategies, shared account "
                    "(net-of-cost: fee+slippage+funding).")
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
    p.add_argument("--risk-pct", type=_risk_pct_arg, default=0.3,
                   help="Per-trade risk %% of balance, in the HARNESS unit "
                        "(percent: 0.3 = 0.3%%). Pass `live` to resolve the "
                        "reference account's declared risk from "
                        "config/accounts.yaml instead, or `grid` to SWEEP "
                        "live x (0.5, 1.0, 2.0) and report every arm — a "
                        "result that holds at "
                        "only one risk setting is a result about that setting, "
                        "not about the strategy. The numeric DEFAULT is "
                        "deliberately unchanged — moving it would silently "
                        "re-base every historical comparison.")
    p.add_argument("--risk-account", default=risk_basis.DEFAULT_REFERENCE_ACCOUNT,
                   help="Account whose declared risk `--risk-pct live` and the "
                        "reported comparison resolve against.")
    p.add_argument("--daily-loss-pct", type=float, default=3.0,
                   help="Daily-loss cap %% of day-start balance (halts new entries for the day).")
    p.add_argument("--signal-ttl-bars", type=int, default=1,
                   help="Clock bars a strategy's latest signal stays live (1 = act on the freshest only).")
    p.add_argument("--clock-tf", default="15m", choices=list(_PANDAS_TF.keys()))
    p.add_argument("--flip-policy", default="reverse", choices=["reverse", "hold", "flat"],
                   help="On an opposite net vote with a position open: reverse "
                        "(close+open new side, live-faithful), hold (ignore the "
                        "flip, let monitor/SL exit), or flat (close, stand aside).")
    p.add_argument("--flip-confidence-threshold", type=float, default=0.0,
                   help="Hold-policy confidence-gap override (live: "
                        "FLIP_CONFIDENCE_THRESHOLD). 0 = disabled (hold always "
                        "wins). A positive value lets an opposing signal whose "
                        "confidence exceeds the held position's entry confidence "
                        "by >= this gap flip it. LIVE VALUE 0.15. Only meaningful "
                        "with --flip-policy hold.")
    p.add_argument("--flip-min-position-age-hours", type=float, default=0.0,
                   help="Minimum age of the held position before the confidence "
                        "override may flip it (live: FLIP_MIN_POSITION_AGE_HOURS). "
                        "0 = no age requirement. BOTH gates must pass. LIVE VALUE 4.0.")
    p.add_argument("--flip-confgap-tf-classes", dest="flip_confgap_tf_classes",
                   default=None, metavar="CSV",
                   help="RESEARCH-ONLY counterfactual: restrict the confidence "
                        "override to conflicts of these M26 P1 TF classes "
                        "(cross_clock,same_clock,unknown; 'all' or unset = "
                        "unrestricted). NO LIVE CODE PATH IMPLEMENTS THIS -- the "
                        "deployed override is TF-blind, so unset is the arm that "
                        "mirrors production and any value here is a proposed gate "
                        "(M26 A_coexist_crossclock), not a measurement of the "
                        "current one. `unknown` is never implied by either class.")
    p.add_argument("--reentry-policy", default="suppress", choices=["suppress", "net"],
                   help="Same-direction re-entry while a position is open: "
                        "suppress (Option-A fix / single-position, default) or "
                        "net (model current one-way-mode pyramiding+SL/TP "
                        "overwrite). See BL-20260608-DEMOPNL.")
    p.add_argument("--fee-bps-roundtrip", type=float, default=FEE_BPS_ROUNDTRIP)
    p.add_argument("--slippage-bps-roundtrip", type=float, default=None,
                   help="Execution-realism (P1 § 3.B): round-trip slippage in bps of "
                        "notional (half-spread + impact). DEFAULT (unset) = the "
                        "venue-aware default (execution_costs.slippage_bps_roundtrip_for, "
                        "~5 bps). Pass 0 for the fee-only comparison arm. ADDED on top of "
                        "the existing fee (the fee formula is unchanged).")
    p.add_argument("--funding-bps-per-window", type=float, default=None,
                   help="Execution-realism (P1 § 3.B): perp funding magnitude in bps of "
                        "notional per 8h window; the hold is charged for every window it "
                        "crosses. DEFAULT (unset) = the VENUE-AWARE default "
                        "(execution_costs.funding_bps_per_window_for): ~1 bps/8h for a "
                        "crypto PERP, 0 for futures/equity/fx (no perp funding). Pass 0 "
                        "for the fee-only arm.")
    p.add_argument("--funding-window-hours", type=float, default=FUNDING_WINDOW_HOURS,
                   help="Perp funding window length in hours (default 8.0).")
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
    # Mandatory venue-aware cost policy (operator directive 2026-08-04): a faithful
    # backtest is net-of-real-cost by default. Unset flags resolve to the venue-aware
    # defaults (funding is perp-only → 0 for a non-perp, never a fabricated cost); an
    # explicit value (incl. 0 for the fee-only comparison arm) always wins.
    SLIPPAGE_BPS_ROUNDTRIP = (
        execution_costs.slippage_bps_roundtrip_for(args.symbol)
        if args.slippage_bps_roundtrip is None else args.slippage_bps_roundtrip)
    FUNDING_BPS_PER_WINDOW = (
        execution_costs.funding_bps_per_window_for(args.symbol)
        if args.funding_bps_per_window is None else args.funding_bps_per_window)
    FUNDING_WINDOW_HOURS = args.funding_window_hours

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

    # --- Risk basis (fix 2.3 / B3+B5, 2026-08-20) ---------------------------
    # Measured 2026-08-20: 0 of 25 harness files read config/accounts.yaml or
    # reached live risk at all, so `--risk-pct 0.3` (percent) ran against a live
    # `risk_pct: 0.015` (fraction) = 1.5% — a 5x gap invisible from the name
    # (F-113). `src/research/risk_basis.py` shipped the ONE definition and its
    # UNIT the day before this and had NO consumer, which is the very
    # build-and-abandon class this audit exists to catch. This is that consumer.
    #
    # `live` RESOLVES; anything else is taken as the harness percent. A failed
    # resolution REFUSES rather than falling back to the default — silently
    # sizing at 0.3 while the operator asked for live is exactly the collapse
    # (`we could not look` read as `here is the number you wanted`).
    #
    # `grid` is the half the operator actually asked for: *"it needs to, in any
    # case, check various different risk percentages to see how they perform."*
    # It is also the consumer `risk_grid_percent()` shipped WITHOUT — measured
    # the next day at 6 references, every one of them a test and none in
    # production, which is definition-of-done clause 2 violated by the very
    # change that wrote the clause.
    risk_grid: Optional[Sequence[float]] = None
    if args.risk_pct == "grid":
        risk_grid, live = risk_basis.risk_grid_percent(args.risk_account)
        if risk_grid is None:
            print(f"ERROR: --risk-pct grid could not resolve a live basis to "
                  f"bracket: {live.describe()}", file=sys.stderr)
            return 1
        args.risk_pct = float(live.percent)  # the 1.0x arm is the reported one
        print(f"--risk-pct grid -> arms {[f'{g:g}%' for g in risk_grid]} "
              f"around live {live.percent:g}% (account {args.risk_account})",
              file=sys.stderr)
    risk_report = risk_basis.compare_to_live(
        0.0 if args.risk_pct == "live" else float(args.risk_pct),
        account_id=args.risk_account)
    if args.risk_pct == "live":
        live = risk_basis.live_risk(args.risk_account)
        if not live.ok or live.percent is None:
            print(f"ERROR: --risk-pct live could not resolve: {live.describe()}",
                  file=sys.stderr)
            return 1
        args.risk_pct = live.percent
        risk_report = risk_basis.compare_to_live(live.percent,
                                                 account_id=args.risk_account)
        print(f"--risk-pct live -> {live.percent:g}% "
              f"(account {args.risk_account}, {live.state})", file=sys.stderr)
    else:
        args.risk_pct = float(args.risk_pct)
    # Reported on EVERY run, not just `live` ones: a run whose risk differs from
    # live is not wrong, but a reader must be able to see that it does. The
    # verdict is three-way — `live_unknown` is NOT `matches_live`.
    print(f"risk basis: harness {risk_report['harness_percent']:g}% vs live "
          f"{risk_report.get('live_percent')} — {risk_report.get('verdict')}",
          file=sys.stderr)

    roster = [r.strip() for r in args.roster.split(",") if r.strip() in ROSTER]
    try:
        base5m = _load_candles(args.data)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: load failed: {exc}", file=sys.stderr)
        return 1
    def _run_at(rpct: float) -> Dict[str, Any]:
        return run_system_backtest(
            base5m, roster=roster, start=args.start, end=args.end,
            initial_balance=args.initial_balance, risk_pct=rpct,
            daily_loss_pct=args.daily_loss_pct, signal_ttl_bars=args.signal_ttl_bars,
        overrides=overrides, refresh=args.refresh_signals, clock_tf=args.clock_tf,
        flip_policy=args.flip_policy,
        flip_confidence_threshold=args.flip_confidence_threshold,
        flip_min_position_age_hours=args.flip_min_position_age_hours,
        flip_confgap_tf_classes=_parse_tf_classes(args.flip_confgap_tf_classes),
        reentry_policy=args.reentry_policy,
        vol_verdict=args.vol_verdict, ml_vol_threshold=args.ml_vol_threshold,
        ml_stage=args.ml_stage, ml_model_id=args.ml_model_id,
        regime_router=args.regime_router, regime_policy_path=args.regime_policy,
            conviction_sizing=args.conviction_sizing, allocator=args.allocator,
            symbol=args.symbol)

    if risk_grid is not None:
        arms: List[Dict[str, Any]] = []
        for rpct in risk_grid:
            print(f"\n=== risk arm {rpct:g}% "
                  f"({rpct / risk_report['live_percent']:.2f}x live) ===",
                  file=sys.stderr)
            arm_out = _run_at(rpct)
            print(_fmt(arm_out))
            arms.append({"risk_pct": rpct,
                         "multiple_of_live": rpct / risk_report["live_percent"],
                         "result": arm_out})
        print(_fmt_risk_grid(arms, risk_report))
        out = {"mode": "risk_grid", "risk_basis": risk_report,
               "grid_percent": list(risk_grid), "arms": arms}
    else:
        out = _run_at(args.risk_pct)
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
