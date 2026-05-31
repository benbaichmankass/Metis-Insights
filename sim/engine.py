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

from sim.account import AccountConfig, SimAccount
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
    timeframe: str = "",
    account: Optional[AccountConfig] = None,
    flip_policy: Optional[str] = None,
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
    account : Phase-5 OPTIONAL $ account layer (``AccountConfig``). When None the
        replay is byte-identical to the R-based behavior (no ``account`` block in
        the summary, R metrics unchanged). When provided, the engine additionally
        tracks $ equity sized via ``backtest_system``'s ``_risk_qty`` math, a UTC
        daily-loss halt that blocks new opens once the cap is breached, and
        capital utilization — folding in the account-realism model that previously
        lived only in ``scripts/backtest_system.py``.
    flip_policy : Phase-5 conflict policy when a NEW opposite-side intent arrives
        while a position is open. ``None`` keeps the Phase-1 at-most-one-open
        behavior (ignore the new intent). ``"hold"`` is explicit-keep; ``"flat"``
        closes the open trade (no reopen); ``"reverse"`` closes + opens the new
        side. Mirrors ``backtest_system``'s reconcile; the live default comes from
        ``resolve_flip_policy`` (the CLI passes it — never hardcoded here).

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
    # Phase-5: OPTIONAL $ account layer. acct is None ⇒ the loop below is
    # byte-identical to the R-only replay and the ledger emits no account block.
    acct = SimAccount(account) if account is not None else None
    # The side currently held (for flip-policy conflict detection). Tracked
    # separately from open_position so we can compare a NEW intent's side to it.
    open_side: Optional[str] = None

    # Build the candle DataFrame once; slice a view per bar (cheap, no copy of
    # the underlying buffer beyond the slice).
    df = pd.DataFrame(candles)
    if "ts" in df.columns:
        df = df.sort_values("ts").reset_index(drop=True)
    n = len(df)

    # Phase-2 perf (MB-20260531-001): when a model is in the loop, each decision
    # needs the closes up to (and including) its bar to compute the live
    # vol_bucket. Materialize the full close column ONCE here instead of
    # rebuilding it from a growing DataFrame slice every bar — the old per-bar
    # `[float(c) for c in history["close"].tolist()]` was O(n) per decision →
    # O(n²) over the run, and ~19x of it was pure waste (`.tolist()` already
    # yields Python floats, so the float() pass re-converted them). Per decision
    # we now take an O(1)-to-set-up list slice `all_closes[: i + 1]`, which is
    # byte-identical to the old per-bar rebuild (same values, same order).
    all_closes: Optional[list[float]] = (
        [float(c) for c in df["close"].tolist()] if "close" in df.columns else None
    )

    # Phase-2 perf (MB-20260531-001): row-dict materialization. The fill model
    # resolves each trade against the FUTURE bars and the decision-bar snapshot
    # is a row dict. Building these with ``df.iloc[j].to_dict()`` per access is
    # pathological under pandas>=3.0 — columns are PyArrow-backed, so row-wise
    # access deboxes every cell through the arrow iterator — and ``future`` was
    # rebuilt for ALL remaining bars on EVERY decision => O(n^2) with a brutal
    # constant (cProfile attributed ~66% of a model-in-loop sweep to this one
    # list comp, the dominant driver of the multi-hour full-history runs).
    # Convert every row to a plain dict ONCE here, then index/slice the list per
    # decision: each row is deboxed exactly once. Byte-identical to the per-bar
    # ``df.iloc[j].to_dict()`` (same dicts, same order); ``future`` stays
    # read-only in fills.resolve and the decision bar is never mutated.
    all_rows: list[dict] = [df.iloc[j].to_dict() for j in range(n)]

    open_position = False  # Phase-1 single-net-position gate (per symbol)

    for i in range(warmup_bars, n):
        if open_position and max_concurrent_per_symbol <= 1:
            # A position is live; the integrated funnel won't open another
            # (single-net-position model). Strategies still don't emit into a
            # filled slot — this is real attrition, not a SIM artifact.
            # (Position resolution is handled at open time via the fill model,
            # so we re-check whether it has closed by this bar.)
            open_position = _any_open(ledger)
            # Phase-5: with a flip_policy set we must still EVALUATE this bar so a
            # conflicting opposite-side intent can trigger the policy below. With
            # no flip_policy this stays the exact Phase-1 short-circuit.
            if open_position and not flip_policy:
                continue

        history = df.iloc[: i + 1]  # inclusive of the current (decision) bar
        bar = all_rows[i]

        # Phase-5: snapshot the day-start balance so the daily-loss cap is
        # measured against the balance at the UTC day's open (no-op when None).
        if acct is not None:
            acct.note_day(bar.get("ts"))

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

        # Phase-5: flip-policy. When a position is already open and the desired
        # side OPPOSES it, the policy decides what happens (mirrors
        # backtest_system's reconcile). Default (flip_policy None / "hold") is the
        # Phase-1 at-most-one-open behavior: keep the open trade, ignore the new
        # intent. "flat"/"reverse" force-close the open trade at this bar's close.
        if open_position and open_side is not None and desired.side != open_side:
            if flip_policy in ("flat", "reverse"):
                _force_close_open(ledger, all_rows[i], acct)
                open_position = False
                open_side = None
                if flip_policy == "flat":
                    continue  # close, do not reopen
                # "reverse" falls through to open the new (opposite) side below
            else:
                continue  # "hold"/default: keep current, ignore opposite intent
        elif open_position:
            # same-side (or no conflict) while open — at-most-one-open still holds
            continue

        # Phase-1 structural risk gate: pass (full RiskManager is Phase 2).
        ledger.record_stage(winner, FunnelStage.PASSED_RISK)

        norm = emit_meta[winner]
        # Phase-5: daily-loss halt — block NEW opens for the UTC day once the
        # day's realized loss breaches the cap (no-op when account is None).
        if acct is not None and not acct.can_open(bar.get("ts")):
            continue
        # Phase-5: size the position via backtest_system's _risk_qty math; a
        # degenerate stop distance sizes to 0 risk-cash → skip the open (as live).
        risk_cash = 0.0
        if acct is not None:
            risk_cash = acct.size(norm["entry"], norm["sl"])
            if risk_cash <= 0:
                continue
        future = all_rows[i + 1:]
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
            # Closes up to and including the decision bar (never future) so a
            # regime model gets the live vol_bucket; matches the live builder
            # which passes the strategy's own candles_df.
            closes = all_closes[: i + 1] if all_closes is not None else []
            factor, scores = model_scorer.factor_for(
                row, closes=closes, symbol=symbol, timeframe=timeframe,
            )
            trade.model_factor = factor
            trade.model_scores = scores

        # Phase-5: stash the $ risk-cash committed at entry on the trade so the
        # account can turn the realized R into $ on close (meta, not a new field,
        # so SimTrade.to_dict stays back-compatible).
        if acct is not None:
            trade.meta["risk_cash"] = risk_cash

        ledger.open_trade(trade)
        open_side = norm["direction"]
        if res is not None:
            trade.exit_ts = res["exit_ts"]
            trade.exit = res["exit"]
            trade.exit_reason = res["exit_reason"]
            trade.r_multiple = res["r_multiple"]
            if trade.model_factor is not None:
                trade.r_multiple_model = round(res["r_multiple"] * trade.model_factor, 6)
            # Phase-5: the trade resolved within the available bars — realize $.
            if acct is not None:
                acct.on_close(winner, risk_cash, res["r_multiple"], res["exit_ts"])
            open_side = None  # resolved at open-time → flat again
        open_position = True

    # Phase-5: bar-level capital-utilization needs a pass over the held position
    # per bar. Because the engine resolves a trade at open-time against future
    # bars (Phase-1 model), "held" = the bars between this trade's entry_ts and
    # exit_ts. We attribute utilization from the recorded trades so the count is
    # deterministic and independent of the open/close ordering above.
    if acct is not None:
        _mark_account_utilization(acct, df, ledger)
        ledger.attach_account(acct)

    return ledger


def _force_close_open(ledger: SimLedger, bar: dict, acct: Optional[Any]) -> None:
    """Phase-5 flip-policy: mark-to-close the still-open trade at this bar's close.

    Mirrors backtest_system's ``_close(..., "flip")``: the open position is closed
    at the current bar's close price, the realized R is booked against the
    entry→SL distance, and (if an account is active) the $ PnL is realized.
    """
    open_trades = ledger.open_positions()
    if not open_trades:
        return
    t = open_trades[-1]
    risk = abs(float(t.entry) - float(t.sl))
    close = float(bar.get("close", t.entry))
    if risk <= 0:
        r = 0.0
    elif t.direction == "long":
        r = (close - t.entry) / risk
    else:
        r = (t.entry - close) / risk
    t.exit_ts = bar.get("ts")
    t.exit = close
    t.exit_reason = "flip"
    t.r_multiple = round(float(r), 6)
    if t.model_factor is not None:
        t.r_multiple_model = round(t.r_multiple * t.model_factor, 6)
    if acct is not None:
        acct.on_close(t.strategy, t.meta.get("risk_cash", 0.0), t.r_multiple, t.exit_ts)


def _mark_account_utilization(acct: Any, df: Any, ledger: SimLedger) -> None:
    """Deterministically accrue capital-utilization: bars with a position held.

    A bar counts as "deployed" if it falls within [entry_ts, exit_ts] of any
    closed trade. Mirrors backtest_system's ``util_bars`` / ``total_bars`` ratio.
    Also feeds the per-day start-balance map so summary day accounting is sound.
    """
    ts_list = [str(t) for t in df["ts"].tolist()] if "ts" in df.columns else []
    held = [False] * len(ts_list)
    for t in ledger.trades:
        if t.exit_ts is None:
            continue
        ent, ext = str(t.entry_ts), str(t.exit_ts)
        for k, b in enumerate(ts_list):
            if ent <= b <= ext:
                held[k] = True
    acct.mark_utilization(len(ts_list), sum(held))


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
