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
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.runtime.intents import StrategyIntent, aggregate_intents  # noqa: E402

FEE_BPS_ROUNDTRIP = 7.5
_SIG_CACHE = _REPO_ROOT / "runtime_logs" / "system_backtest" / "signals"


# --------------------------------------------------------------------------
# Roster: name -> (module path, timeframe). The order_package + monitor are
# imported from the live unit; the timeframe is the strategy's setup TF.
# vwap excluded (execution: shadow). turtle_soup + ict_scalp_5m added 2026-05-30
# (full live-roster coverage). turtle_soup's live adapter is single-TF (the 15m
# setup frame; its legacy 1m-entry confirmation is not in the order_package
# path). ict_scalp_5m needs the 1h EMA-20 HTF bias injected per bar — see
# generate_signal_stream's htf handling — else its HTF gate silently no-ops and
# overstates the signal count.
# --------------------------------------------------------------------------
ROSTER: Dict[str, Dict[str, str]] = {
    "trend_donchian":      {"module": "src.units.strategies.trend_donchian", "tf": "2h"},
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
                           overrides: dict, refresh: bool = False) -> pd.DataFrame:
    """Run the REAL order_package on every closed bar of the strategy's TF.

    Returns a frame [ts, side, entry, sl, tp, confidence, meta_json] with one
    row per bar where the strategy emitted a signal (ValueError = no row).
    Cached to parquet keyed by (strategy, data, window, overrides).
    """
    cache = _cache_key(name, _data_fingerprint(base5m), start, end, overrides)
    if cache.exists() and not refresh:
        return pd.read_parquet(cache)

    spec = ROSTER[name]
    order_package = _import_callable(spec["module"], "order_package")
    if order_package is None:
        raise RuntimeError(f"{name}: no order_package")
    cfg = {"symbol": "BTCUSDT", "timeframe": spec["tf"], **_load_strategy_cfg(name), **overrides}
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
    cache.parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(cache)
    return out


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


def run_system_backtest(base5m: pd.DataFrame, *, roster: List[str], start, end,
                        initial_balance: float, risk_pct: float,
                        daily_loss_pct: float, signal_ttl_bars: int,
                        overrides: Dict[str, dict], refresh: bool,
                        clock_tf: str = "15m",
                        flip_policy: str = "reverse",
                        reentry_policy: str = "suppress",
                        attach_full: bool = False) -> Dict[str, Any]:
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

    # 1) signal streams (cached), indexed onto the clock grid
    streams: Dict[str, pd.DataFrame] = {}
    for name in roster:
        streams[name] = generate_signal_stream(
            name, base5m, start=start, end=end,
            overrides=overrides.get(name, {}), refresh=refresh)

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
    cfgs = {name: {"symbol": "BTCUSDT", "timeframe": ROSTER[name]["tf"],
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
            reason=reason, bars_held=idx_i - p.entry_idx))

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

        # ---- desired net position from the REAL aggregator ----
        intents = []
        for name, row in latest.items():
            if row["side"] not in ("long", "short"):
                continue
            intents.append(StrategyIntent(
                strategy=name, symbol="BTCUSDT", side=row["side"],
                target_qty=1.0, entry=row["entry"], sl=row["sl"], tp=row["tp"],
                confidence=row["confidence"], meta={"_stream": True}))
        desired = aggregate_intents(intents, symbol="BTCUSDT") if intents else None
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
                qty = _risk_qty(balance, risk_pct, fill, row["sl"])
                qty = float(qty) if qty else 0.0
                if qty > 0:
                    pos = _Position(side=des_side, qty=qty, entry=fill, sl=row["sl"],
                                    tp=row["tp"], owner=win_name, entry_ts=ts.iloc[i],
                                    entry_idx=i, meta=json.loads(row["meta_json"]),
                                    notional=qty * fill)
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
                        qty = _risk_qty(balance, risk_pct, fill, row["sl"])
                        qty = float(qty) if qty else 0.0
                        if qty > 0:
                            pos = _Position(side=des_side, qty=qty, entry=fill,
                                            sl=row["sl"], tp=row["tp"], owner=win_name,
                                            entry_ts=ts.iloc[i], entry_idx=i,
                                            meta=json.loads(row["meta_json"]),
                                            notional=qty * fill)

        eq = balance + _unrealized(pos, c[i])
        equity_high = max(equity_high, eq)
        equity_curve.append((str(ts.iloc[i]), round(eq, 2)))

    # final mark-to-close
    if pos is not None:
        _close(pos, c[-1], ts.iloc[-1], "eod", n - 1)
        pos = None

    summary = _summarize(closed, equity_curve, base_balance=initial_balance,
                         util_bars=util_bars, total_bars=n, roster=roster,
                         params={"initial_balance": initial_balance, "risk_pct": risk_pct,
                                 "daily_loss_pct": daily_loss_pct, "signal_ttl_bars": signal_ttl_bars,
                                 "clock_tf": clock_tf, "flip_policy": flip_policy,
                                 "reentry_policy": reentry_policy,
                                 "overrides": overrides},
                         data_start=str(ts.iloc[0]) if n else None,
                         data_end=str(ts.iloc[-1]) if n else None)
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
               total_bars, roster, params, data_start, data_end) -> Dict[str, Any]:
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
    return {
        "kind": "system_backtest", "symbol": "BTCUSDT", "roster": roster,
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
    return "\n".join(L)


def main(argv: List[str]) -> int:
    global FEE_BPS_ROUNDTRIP
    p = argparse.ArgumentParser(description="System/portfolio backtest — all strategies, shared account.")
    p.add_argument("--data", default=os.environ.get("BACKTEST_DATA_PATH", "data/backtest_candles.csv"),
                   help="5m OHLCV CSV/parquet (resampled per strategy TF internally).")
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
        flip_policy=args.flip_policy, reentry_policy=args.reentry_policy)
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
