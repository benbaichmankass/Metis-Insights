"""SIM Phase-1 engine — integrated pipeline replay.

Walks historical bars and drives the SAME live decision funnel the production
pipeline uses, so strategies are tested together through the real intent
multiplexer rather than each in isolation.

Per bar, for the strategies under test:

  1. Call the strategy's ``order_package(cfg, candles_df=<history-up-to-bar>)``
     — the EXACT signal logic the live signal builder calls (the builder is
     just an exchange-fetch wrapper around this; SIM feeds it history instead).
     A ``ValueError`` (or ``side=none``) means "no actionable signal" — the
     strategy attrits at stage EMITTED-not-reached.
  2. Build a ``StrategyIntent`` per emitting strategy and run the REAL
     ``aggregate_intents`` (src/runtime/intents.py) to resolve conflicts /
     reinforcement exactly as live. The winner survives the multiplexer;
     losers are recorded as attrition.
  3. (Phase 1) a lightweight risk check: at-most-one-open-position per symbol
     (the integrated funnel's structural gate). The full ``RiskManager`` wire
     is a Phase-2 refinement — Phase 1's value is the multiplexer attrition +
     the portfolio-vs-solo comparison, which this already delivers faithfully.
  4. Open a position via the fill model; resolve it against future bars.

The headline output is the **funnel**: emitted -> survived_mux -> passed_risk
-> filled per strategy, so the operator can see how many of a strategy's solo
signals actually survive when it competes for one account.

NOT reimplemented here: signal logic (strategy ``order_package``) and intent
resolution (``aggregate_intents``). SIM only orchestrates + books.
"""
from __future__ import annotations

import importlib
import logging
from typing import Any, Callable, Optional

from sim.fills import BarFillModel
from sim.ledger import FunnelStage, SimLedger, SimTrade

logger = logging.getLogger(__name__)

# strategy-name -> (unit module, default cfg timeframe). The unit module's
# ``order_package(cfg, candles_df=...)`` is the live signal logic; the live
# signal builder is a thin exchange-fetch wrapper around it (see
# src/runtime/strategy_signal_builders.py). Driving order_package directly is
# the faithful seam for a historical replay.
STRATEGY_UNITS: dict[str, str] = {
    "turtle_soup": "src.units.strategies.turtle_soup",
    "vwap": "src.units.strategies.vwap",
    "ict_scalp_5m": "src.units.strategies.ict_scalp",
    "trend_donchian": "src.units.strategies.trend_donchian",
    "fade_breakout_4h": "src.units.strategies.fade_breakout_4h",
    "squeeze_breakout_4h": "src.units.strategies.squeeze_breakout_4h",
}


def _load_order_package(strategy: str) -> Callable[..., dict]:
    mod = importlib.import_module(STRATEGY_UNITS[strategy])
    return getattr(mod, "order_package")


def _normalize_signal(pkg: dict) -> Optional[dict]:
    """Map a strategy order_package dict to {direction, entry, sl, tp, conf, meta}.

    Strategies return either ``direction`` (long/short) or ``side``
    (buy/sell/none). Returns None for a non-actionable package so the caller
    records attrition rather than a fill.
    """
    if not isinstance(pkg, dict):
        return None
    direction = pkg.get("direction")
    if direction is None:
        side = str(pkg.get("side", "none")).lower()
        if side in ("buy", "long"):
            direction = "long"
        elif side in ("sell", "short"):
            direction = "short"
        else:
            return None
    direction = str(direction).lower()
    if direction not in ("long", "short"):
        return None
    try:
        entry = float(pkg["entry"])
        sl = float(pkg["sl"]) if pkg.get("sl") is not None else float(pkg["stop_loss"])
        tp = float(pkg["tp"]) if pkg.get("tp") is not None else float(pkg["take_profit"])
    except (KeyError, TypeError, ValueError):
        return None
    return {
        "direction": direction,
        "entry": entry,
        "sl": sl,
        "tp": tp,
        "confidence": float(pkg.get("confidence", 0.0) or 0.0),
        "meta": pkg.get("meta", {}) or {},
    }


def run_replay(
    *,
    candles: list[dict[str, Any]],
    strategies: list[str],
    symbol: str = "BTCUSDT",
    strategy_cfg: Optional[dict[str, dict]] = None,
    warmup_bars: int = 200,
    fee_bps_roundtrip: float = 7.5,
    timeout_bars: int = 0,
    max_concurrent_per_symbol: int = 1,
    model_scorer: Optional[Any] = None,
) -> SimLedger:
    """Replay ``strategies`` over ``candles`` through the real intent funnel.

    Parameters
    ----------
    candles : list of {ts, open, high, low, close, [volume]}, ascending by ts.
    strategies : strategy names (keys of STRATEGY_UNITS).
    strategy_cfg : optional per-strategy cfg dict passed to order_package
        (merged over a minimal {symbol, timeframe}).
    warmup_bars : bars of history each order_package call gets (the live
        builders fetch limit=200, so 200 mirrors live lookback).
    max_concurrent_per_symbol : structural risk gate for Phase 1 (1 = the live
        single-net-position model).

    Determinism: pure function of (candles, strategies, params). No clock, no
    randomness. Requires pandas (strategy order_package takes a DataFrame).
    """
    import pandas as pd  # local: strategies need a DataFrame; sandbox installs it

    from src.runtime.intents import StrategyIntent, aggregate_intents

    unknown = [s for s in strategies if s not in STRATEGY_UNITS]
    if unknown:
        raise ValueError(f"unknown strategies: {unknown}; known={sorted(STRATEGY_UNITS)}")

    strategy_cfg = strategy_cfg or {}
    order_pkgs = {s: _load_order_package(s) for s in strategies}
    fill = BarFillModel(fee_bps_roundtrip=fee_bps_roundtrip, timeout_bars=timeout_bars)
    ledger = SimLedger()

    # Build the candle DataFrame once; slice a view per bar (cheap, no copy of
    # the underlying buffer beyond the slice).
    df = pd.DataFrame(candles)
    if "ts" in df.columns:
        df = df.sort_values("ts").reset_index(drop=True)
    n = len(df)

    open_position = False  # Phase-1 single-net-position gate (per symbol)

    for i in range(warmup_bars, n):
        if open_position and max_concurrent_per_symbol <= 1:
            # A position is live; the integrated funnel won't open another
            # (single-net-position model). Strategies still don't emit into a
            # filled slot — this is real attrition, not a SIM artifact.
            # (Position resolution is handled at open time via the fill model,
            # so we re-check whether it has closed by this bar.)
            open_position = _any_open(ledger)
            if open_position:
                continue

        history = df.iloc[: i + 1]  # inclusive of the current (decision) bar
        bar = df.iloc[i].to_dict()

        intents: list[Any] = []
        emit_meta: dict[str, dict] = {}
        for s in strategies:
            cfg = {"symbol": symbol, "timeframe": strategy_cfg.get(s, {}).get("timeframe", "")}
            cfg.update(strategy_cfg.get(s, {}))
            try:
                pkg = order_pkgs[s](cfg, candles_df=history)
            except ValueError:
                continue  # no actionable setup this bar — not even EMITTED
            except Exception as exc:  # noqa: BLE001
                logger.debug("sim: %s order_package raised %s at bar %d", s, exc, i)
                continue
            norm = _normalize_signal(pkg)
            if norm is None:
                continue
            ledger.record_stage(s, FunnelStage.EMITTED)
            emit_meta[s] = norm
            try:
                intents.append(
                    StrategyIntent(
                        strategy=s,
                        symbol=symbol,
                        side=norm["direction"],
                        target_qty=0.0,  # live sentinel: RiskManager sizes
                        entry=norm["entry"],
                        sl=norm["sl"],
                        tp=norm["tp"],
                        confidence=norm["confidence"],
                    )
                )
            except ValueError as exc:
                logger.debug("sim: %s intent rejected: %s", s, exc)

        if not intents:
            continue

        desired = aggregate_intents(intents, symbol=symbol)
        if desired.side == "flat":
            continue

        # Which strategy won the multiplexer? aggregate_intents records the
        # winning intent's strategy in the DesiredPosition.
        winner = _winning_strategy(desired)
        if winner is None or winner not in emit_meta:
            continue
        ledger.record_stage(winner, FunnelStage.SURVIVED_MUX)

        # Phase-1 structural risk gate: pass (full RiskManager is Phase 2).
        ledger.record_stage(winner, FunnelStage.PASSED_RISK)

        norm = emit_meta[winner]
        future = [df.iloc[j].to_dict() for j in range(i + 1, n)]
        trade = SimTrade(
            strategy=winner,
            symbol=symbol,
            direction=norm["direction"],
            entry_ts=str(bar.get("ts")),
            entry=norm["entry"],
            sl=norm["sl"],
            tp=norm["tp"],
            confidence=norm["confidence"],
            meta={"contributing": sorted(emit_meta.keys())},
        )
        res = fill.resolve(
            direction=norm["direction"], entry=norm["entry"], sl=norm["sl"],
            tp=norm["tp"], future_bars=future,
        )
        ledger.record_stage(winner, FunnelStage.FILLED)

        # Phase 2: score the decision against the model(s) and record the
        # advisory size factor. The feature row is leakage-safe (signal-time
        # only) and the factor comes from the LIVE advisory_downsize_factor.
        # This is done at DECISION time on the signal-time row — never using
        # the (future) outcome — so it faithfully mirrors live advisory sizing.
        if model_scorer is not None:
            from sim.models import feature_row_for_trade

            row = feature_row_for_trade(
                strategy=winner, symbol=symbol, direction=norm["direction"],
                confidence=norm["confidence"], meta=norm.get("meta"),
            )
            factor, scores = model_scorer.factor_for(row)
            trade.model_factor = factor
            trade.model_scores = scores

        ledger.open_trade(trade)
        if res is not None:
            trade.exit_ts = res["exit_ts"]
            trade.exit = res["exit"]
            trade.exit_reason = res["exit_reason"]
            trade.r_multiple = res["r_multiple"]
            if trade.model_factor is not None:
                trade.r_multiple_model = round(res["r_multiple"] * trade.model_factor, 6)
        open_position = True

    return ledger


def _any_open(ledger: SimLedger) -> bool:
    return len(ledger.open_positions()) > 0


def _winning_strategy(desired: Any) -> Optional[str]:
    """Extract the winning strategy name from a DesiredPosition.

    aggregate_intents carries the winning intent; we read it defensively
    across the field names the dataclass may expose.
    """
    wi = getattr(desired, "winning_intent", None)
    if wi is not None and getattr(wi, "strategy", None):
        return wi.strategy
    meta = getattr(desired, "meta", None) or {}
    if meta.get("winning_strategy"):
        return meta["winning_strategy"]
    contributing = getattr(desired, "contributing_intents", None) or ()
    if contributing:
        return contributing[0].strategy
    return None
