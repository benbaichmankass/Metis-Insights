#!/usr/bin/env python3
"""N-BOOK portfolio replay — many books over one tick stream, with a swappable
election key. The capability the trade-prioritisation A/B was blocked on.

WHAT WAS MISSING, AND WHY IT BLOCKED TWO QUESTIONS
--------------------------------------------------
`scripts/backtest_system.py` models exactly ONE shared book. Its own design doc
says so in the same breath as the limitation:

    "The harness manages ONE shared book, so it measures the ranking key, NOT
     the per-account fan-out."
    -- docs/research/trade-prioritisation-research-DESIGN.yaml

So neither of the two open questions could be EXPRESSED:

  1. **Does the ranking KEY drive outcome?** (this module's subject) — live on
     REAL MONEY since 2026-08-31 (PR #10544), never shown to pick the
     better-performing trade. `OI-20260831-TRADE-PRIORITISATION-IS-LIVE-BUT-UNPROVEN-...`
  2. **Global vs per-account arbitration?** — blocked on the same missing
     capability. `OI-20260831-PER-ACCOUNT-ARBITRATION-SHIPPED-NOT-YET-ARMED-OR-EXERCISED`,
     and `BL-20260827-PROP-ONLY-TWIN-WINS-THE-GLOBAL-SYMBOL-SLOT-AND-STARVES-ITS-PAPER-SIBLING`
     is the measured harm (0 of 60).

This module answers (1) and makes (2) runnable. **It does not answer (2)** —
that needs its own population and its own write-up.

FAITHFULNESS — WHAT IS REUSED AND WHAT IS NEW, STATED RATHER THAN IMPLIED
-------------------------------------------------------------------------
REUSED from `scripts/backtest_system.py`, by import, never by copy: the signal
streams (each strategy's REAL `order_package`), the roster/config/monitor
loaders, `_Position`, `_ClosedTrade`, `_summarize`, the regime + vol stamping,
and `src.runtime.execution_costs` — the ONE shared cost model, at the SAME
`FEE_BPS_ROUNDTRIP` alias, so there is no second cost convention.

REUSED from `src/runtime/intents.py`, by import: `gate_intents` and
`elect_from_gated` — the REAL gate and the REAL election, and the split exists
precisely for this caller ("A caller needing several elections from one tick's
candidate set calls this ONCE and then calls `elect_from_gated` per election").

NEW here, because it exists nowhere: the multi-book tick loop and per-book
bookkeeping. That is a real duplication risk — two engines are free to drift —
so it is closed by an EXECUTABLE assertion rather than by a promise:
`tests/research/test_nbook_portfolio.py` runs this engine at N=1 with the full
roster against `backtest_system.run_system_backtest` on the same bars and
requires the same trades. A parity test is stronger than co-location, which is
what the two-expressions-of-one-ordering incidents in `intents.py` show.

CONTESTED IS THE UNIT, AND POOLING IS HOW YOU MANUFACTURE A NULL
----------------------------------------------------------------
A ranking key CANNOT matter on a tick with one candidate. Including uncontested
ticks dilutes any effect toward zero and would make every arm look identical —
a null result produced by the measurement rather than by the world. So every
headline here is computed on the CONTESTED subset, and the ACHIEVED contested
count is reported so a reader can grade the power against what actually
happened rather than against the design's declared 1,780.

`decided_by` is carried on every contested election and every trade it opened,
because ~50% of live contests are exact confidence ties (n=371 soak) — an
average over both halves is uninterpretable.

THE ARBITRATION MODES ARE THE ADJACENT QUESTION, WIRED BUT NOT GRADED HERE
--------------------------------------------------------------------------
  * ``per_account`` — each book elects over ITS OWN roster's candidates.
  * ``global``      — ONE election over the union; a book that does not carry
    the winning strategy STANDS ASIDE, which is exactly the starvation shape
    `BL-20260827-...-STARVES-ITS-PAPER-SIBLING` measured live.

At N=1 with the full roster the two are identical by construction, which is why
the ranking A/B (a one-book question) is unaffected by this axis.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS = _REPO_ROOT / "scripts"
for _p in (str(_REPO_ROOT), str(_SCRIPTS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import backtest_system as bs  # noqa: E402  (the ONE portfolio harness)
from src.runtime import execution_costs  # noqa: E402  (the ONE cost model)
from src.runtime.intents import (  # noqa: E402
    StrategyIntent, elect_from_gated, gate_intents,
)
from src.runtime.monitor_verdict import (  # noqa: E402
    KIND_MODIFY, KIND_PARTIAL_CLOSE, interpret_verdict,
)
from ranking_keys import (  # noqa: E402
    ARMS, CONTROL_ARM, SHIPPED_ARM, ReplayTrackRecord, install_ranking_key,
)

ARBITRATIONS = ("per_account", "global")


# --------------------------------------------------------------------------
# Book spec
# --------------------------------------------------------------------------
@dataclass
class Book:
    """One independent account: its own roster, balance, cap and position.

    Books share the tick stream and the GATED candidate set; they share no
    capital. That is deliberate and is what makes the live per-account model
    representable — the live coupling between accounts is the ELECTION, not the
    balance sheet.
    """
    name: str
    roster: Tuple[str, ...]
    initial_balance: float
    risk_pct: float
    daily_loss_pct: float


def parse_book_spec(spec: str, *, default_roster: Sequence[str],
                    default_balance: float, default_risk_pct: float,
                    default_daily_loss_pct: float) -> Book:
    """``name=paper:roster=a|b:balance=10000:risk_pct=0.3:daily_loss_pct=3``

    An UNKNOWN key is REFUSED rather than ignored. A silently-dropped key would
    report a full result under a narrowed label, which is the shape this repo
    calls unprovenanced diagnostic output (sub-class B).
    """
    fields: Dict[str, str] = {}
    for part in spec.split(":"):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            raise ValueError(f"book spec fragment {part!r} is not key=value")
        k, v = part.split("=", 1)
        fields[k.strip()] = v.strip()
    unknown = set(fields) - {"name", "roster", "balance", "risk_pct", "daily_loss_pct"}
    if unknown:
        raise ValueError(f"unknown book-spec key(s): {sorted(unknown)}")
    name = fields.get("name")
    if not name:
        raise ValueError(f"book spec {spec!r} has no name=")
    roster_raw = fields.get("roster")
    roster = (tuple(s for s in (x.strip() for x in roster_raw.split("|")) if s)
              if roster_raw else tuple(default_roster))
    unknown_strats = [s for s in roster if s not in bs.ROSTER]
    if unknown_strats:
        raise ValueError(f"book {name!r} names strategies absent from the harness "
                         f"ROSTER: {unknown_strats}")
    return Book(
        name=name,
        roster=roster,
        initial_balance=float(fields.get("balance", default_balance)),
        risk_pct=float(fields.get("risk_pct", default_risk_pct)),
        daily_loss_pct=float(fields.get("daily_loss_pct", default_daily_loss_pct)),
    )


# --------------------------------------------------------------------------
# Per-book mutable state
# --------------------------------------------------------------------------
@dataclass
class _BookState:
    book: Book
    balance: float
    equity_high: float
    day: Any = None
    day_start_balance: float = 0.0
    daily_halted: bool = False
    pos: Optional[Any] = None            # bs._Position
    closed: List[Any] = field(default_factory=list)   # bs._ClosedTrade
    equity_curve: List[Tuple[str, float]] = field(default_factory=list)
    util_bars: int = 0
    monitor_errors: Dict[str, int] = field(default_factory=dict)
    monitor_error_examples: Dict[str, str] = field(default_factory=dict)
    # Elections this book actually held, one row per contested election.
    contested: List[Dict[str, Any]] = field(default_factory=list)
    elections_held: int = 0
    elections_uncontested: int = 0
    stood_aside_global: int = 0
    # entry clock-index -> the contested election that produced the open, so a
    # closed trade can be attributed back to the arbitration that opened it.
    open_election: Dict[int, Dict[str, Any]] = field(default_factory=dict)


def _risk_qty(bal: float, rpct: float, entry_px: float, sl_px: float) -> float:
    """Mirrors `backtest_system`'s sizing, which mirrors the live
    `RiskManager.position_size`. Kept identical so an N-book run at N=1 is
    comparable to a one-book run trade-for-trade."""
    stop_dist = abs(entry_px - sl_px)
    if stop_dist <= 0 or bal <= 0 or rpct <= 0:
        return 0.0
    return (bal * (rpct / 100.0)) / stop_dist


def _epoch_s(ts: Any) -> float:
    return float(pd.Timestamp(ts).timestamp())


# --------------------------------------------------------------------------
# The engine
# --------------------------------------------------------------------------
def run_nbook(base5m: pd.DataFrame, *, books: Sequence[Book], start, end,
              signal_ttl_bars: int = 1, clock_tf: str = "15m",
              overrides: Optional[Dict[str, dict]] = None, refresh: bool = False,
              symbol: str = "BTCUSDT",
              arbitration: str = "per_account",
              ranking_key: str = SHIPPED_ARM, seed: int = 0,
              flip_policy: str = "reverse",
              folds: int = 6,
              track_record: Optional[ReplayTrackRecord] = None,
              attach_trades: bool = False) -> Dict[str, Any]:
    """Replay `books` over one tick stream under one `ranking_key` arm."""
    if arbitration not in ARBITRATIONS:
        raise ValueError(f"unknown arbitration {arbitration!r}; declared: {list(ARBITRATIONS)}")
    if ranking_key not in ARMS:
        raise ValueError(f"unknown ranking-key arm {ranking_key!r}; declared: {list(ARMS)}")
    overrides = overrides or {}

    union_roster: List[str] = []
    for b in books:
        for s in b.roster:
            if s not in union_roster:
                union_roster.append(s)

    # The live regime router is BASELINE-ON. A replay must not inherit that, or
    # every arm silently becomes the gated arm — the same reasoning
    # `backtest_system.run_system_backtest` states for its own `else` branch.
    _prev_router = os.environ.get("REGIME_ROUTER_DISABLED")
    os.environ["REGIME_ROUTER_DISABLED"] = "1"
    try:
        import src.runtime.intents as _im
        _im._REGIME_POLICY_CACHE = None
    except (ImportError, AttributeError):
        # NARROW deliberately: the only reachable failures are the module not
        # importing or the cache attribute not existing. Anything else raising
        # here is a real fault and must NOT be swallowed.
        pass

    try:
        streams: Dict[str, pd.DataFrame] = {
            name: bs.generate_signal_stream(
                name, base5m, start=start, end=end,
                overrides=overrides.get(name, {}), refresh=refresh, symbol=symbol)
            for name in union_roster
        }

        clock = bs._date_filter(
            bs._resample(base5m, bs._PANDAS_TF[clock_tf]), start, end
        ).reset_index(drop=True)
        n = len(clock)
        ts = clock["timestamp"]
        hi = clock["high"].to_numpy(float)
        lo = clock["low"].to_numpy(float)
        cl = clock["close"].to_numpy(float)
        clock_ts = ts.values

        sig_at: Dict[int, Dict[str, dict]] = {}
        for name, s in streams.items():
            for _, r in s.iterrows():
                idx = int(np.searchsorted(
                    clock_ts, np.datetime64(pd.Timestamp(r["ts"])), side="right"))
                if idx >= n:
                    continue
                sig_at.setdefault(idx, {})[name] = r.to_dict()

        monitors = {name: bs._import_callable(bs.ROSTER[name]["module"], "monitor")
                    for name in union_roster}
        cfgs = {name: {"symbol": symbol, "timeframe": bs.ROSTER[name]["tf"],
                       **bs._load_strategy_cfg(name), **overrides.get(name, {})}
                for name in union_roster}

        # Injectable so a parity harness can give BOTH engines one definition
        # of the tier-3 term and thereby isolate the engine from the arm.
        track = ReplayTrackRecord() if track_record is None else track_record
        states = [_BookState(book=b, balance=b.initial_balance,
                             equity_high=b.initial_balance,
                             day_start_balance=b.initial_balance)
                  for b in books]
        fee_rate = bs.FEE_BPS_ROUNDTRIP / 10_000.0
        latest: Dict[str, dict] = {}
        latest_idx: Dict[str, int] = {}

        def _close(st: _BookState, p, price: float, ts_i, reason: str, idx_i: int,
                   qty: Optional[float] = None) -> None:
            """Book a close for `st`. Cost math is the ONE shared model, at the
            same fee convention `backtest_system._close` uses — the split is
            not re-derived here, only applied per book."""
            q = p.qty if qty is None else float(qty)
            gross = (price - p.entry) * q if p.side == "long" else (p.entry - price) * q
            fee = fee_rate * (p.entry + price) * q
            extra = execution_costs.roundtrip_cost_usd(
                entry_price=p.entry, qty=q,
                entry_time=p.entry_ts, exit_time=ts_i,
                fee_bps_roundtrip=0.0,
                slippage_bps_roundtrip=bs.SLIPPAGE_BPS_ROUNDTRIP,
                funding_bps_per_window=bs.FUNDING_BPS_PER_WINDOW,
                funding_window_hours=bs.FUNDING_WINDOW_HOURS,
            )
            slippage = extra["slippage_usd"] or 0.0
            funding = extra["funding_usd"] or 0.0
            pnl = gross - fee - slippage - funding
            st.balance += pnl
            tr = bs._ClosedTrade(
                owner=p.owner, side=p.side, entry_ts=p.entry_ts, exit_ts=ts_i,
                entry=p.entry, exit=price, qty=q, pnl=pnl, fee=fee,
                slippage=slippage, funding=funding, reason=reason,
                bars_held=idx_i - p.entry_idx, regime=p.regime,
                vol_regime=p.vol_regime, entry_idx=p.entry_idx, exit_idx=idx_i,
                sl=p.entry_sl, meta=p.meta, confidence=p.confidence)
            st.closed.append(tr)
            # Feed the REPLAY's own track record — this is what the tier-3
            # election term reads, instead of the live trader's journal.
            track.record_close(strategy=p.owner, pnl=pnl, exit_epoch_s=_epoch_s(ts_i))

        def _open(st: _BookState, *, side: str, row: dict, owner: str, i: int,
                  regime_label, bar_vol_regime) -> bool:
            fill = cl[i]
            qty = _risk_qty(st.balance, st.book.risk_pct, fill, row["sl"])
            qty = float(qty) if qty else 0.0
            if qty <= 0:
                return False
            st.pos = bs._Position(
                side=side, qty=qty, entry=fill, sl=row["sl"], tp=row["tp"],
                owner=owner, entry_ts=ts.iloc[i], entry_idx=i,
                meta=json.loads(row["meta_json"]), notional=qty * fill,
                regime=regime_label, vol_regime=bar_vol_regime,
                confidence=float(row.get("confidence", 0.0) or 0.0))
            return True

        with install_ranking_key(ranking_key, seed=seed, track_record=track):
            for i in range(n):
                now_s = _epoch_s(ts.iloc[i])
                track.set_now(now_s)
                d = pd.Timestamp(ts.iloc[i]).date()

                if i in sig_at:
                    for name, row in sig_at[i].items():
                        latest[name] = row
                        latest_idx[name] = i
                for name in list(latest):
                    if i - latest_idx[name] >= signal_ttl_bars:
                        latest.pop(name, None)

                # ---- per-book position management -------------------------
                for st in states:
                    if st.day != d:
                        st.day = d
                        st.day_start_balance = st.balance
                        st.daily_halted = False
                    p = st.pos
                    if p is not None:
                        if p.side == "long":
                            if lo[i] <= p.sl:
                                _close(st, p, p.sl, ts.iloc[i], "sl", i)
                                st.pos = None
                            elif hi[i] >= p.tp:
                                _close(st, p, p.tp, ts.iloc[i], "tp", i)
                                st.pos = None
                        else:
                            if hi[i] >= p.sl:
                                _close(st, p, p.sl, ts.iloc[i], "sl", i)
                                st.pos = None
                            elif lo[i] <= p.tp:
                                _close(st, p, p.tp, ts.iloc[i], "tp", i)
                                st.pos = None
                    p = st.pos
                    if p is not None:
                        mon = monitors.get(p.owner)
                        if mon is not None:
                            win = clock.iloc[max(0, i - 300):i + 1]
                            open_pkg = {"direction": p.side, "entry": p.entry,
                                        "sl": p.sl, "tp": p.tp, "meta": p.meta,
                                        "created_at": str(p.entry_ts)}
                            try:
                                verdict = mon(cfgs.get(p.owner, {}), win, open_pkg)
                            except Exception as exc:  # noqa: BLE001  # allow-silent: a strategy monitor may raise anything, and this failure is NOT silent — it is COUNTED per owner with one example message and surfaced as books[].monitor_errors, which is the honest denominator for the exit profile (a nonzero count means by_exit_reason under-reports monitor-driven exits by an unknown amount). Re-raising would abort a whole replay on one leg's bug.
                                # A crashing monitor is a BROKEN exit path, not a
                                # quiet one — counted so the run can never report
                                # a clean exit profile over a monitor that never ran.
                                verdict = None
                                st.monitor_errors[p.owner] = st.monitor_errors.get(p.owner, 0) + 1
                                st.monitor_error_examples.setdefault(
                                    p.owner, f"{type(exc).__name__}: {exc}")
                            decision = interpret_verdict(
                                verdict, current_sl=p.sl, current_tp=p.tp)
                            if decision.is_close:
                                px = decision.exit_price
                                _close(st, p, float(cl[i] if px is None else px),
                                       ts.iloc[i], decision.reason or "monitor_close", i)
                                st.pos = None
                            elif decision.kind == KIND_PARTIAL_CLOSE:
                                px = decision.exit_price
                                part = p.qty * float(decision.close_qty_pct or 0.0)
                                if part > 0:
                                    _close(st, p, float(cl[i] if px is None else px),
                                           ts.iloc[i], decision.reason or "monitor_close",
                                           i, qty=part)
                                    p.qty -= part
                                    p.notional = p.entry * p.qty
                                if decision.next_tp is not None:
                                    p.tp = float(decision.next_tp)
                                if p.qty <= 0:
                                    st.pos = None
                            elif decision.kind == KIND_MODIFY:
                                if decision.sl is not None:
                                    p.sl = decision.sl
                                if decision.tp is not None:
                                    p.tp = decision.tp
                    if st.pos is not None:
                        st.util_bars += 1

                # ---- regime / vol axes for this bar ------------------------
                regime_label = adx_14_val = None
                bar_vol_regime: Optional[str] = None
                if [nm for nm in latest if latest[nm]["side"] in ("long", "short")]:
                    reg_win = clock.iloc[max(0, i - 300):i + 1]
                    regime_label, adx_14_val = bs._adx_regime_for_window(reg_win)
                    bar_vol_regime = bs._frozen_vol_regime_for_window(
                        reg_win, symbol=symbol, timeframe=clock_tf)

                # ---- ONE gate for the tick, N elections -------------------
                intents = [
                    StrategyIntent(
                        strategy=name, symbol=symbol, side=row["side"],
                        target_qty=1.0, entry=row["entry"], sl=row["sl"],
                        tp=row["tp"], confidence=row["confidence"],
                        regime=regime_label, adx_14=adx_14_val,
                        vol_regime=bar_vol_regime, meta={"_stream": True})
                    for name, row in latest.items()
                    if row["side"] in ("long", "short")
                ]
                candidates, pre_gate = (gate_intents(intents, symbol=symbol)
                                        if intents else (tuple(), 0))

                global_desired = None
                if arbitration == "global" and candidates:
                    global_desired = elect_from_gated(
                        candidates, symbol=symbol, intents_before_gate=pre_gate)

                for st in states:
                    own = tuple(c for c in candidates if c.strategy in st.book.roster)
                    if arbitration == "per_account":
                        desired = (elect_from_gated(own, symbol=symbol,
                                                    intents_before_gate=len(own))
                                   if own else None)
                        contest_pool = own
                    else:
                        desired = global_desired
                        contest_pool = candidates
                        # A book that does not carry the winning strategy STANDS
                        # ASIDE. That is the starvation shape, and it is counted
                        # rather than silently rendering as "no signal".
                        if (desired is not None
                                and getattr(desired, "winning_intent", None) is not None
                                and desired.winning_intent.strategy not in st.book.roster):
                            st.stood_aside_global += 1
                            desired = None

                    # The daily-loss cap is evaluated HERE, before the open/flip
                    # block — the same order `backtest_system.run_system_backtest`
                    # uses. Evaluating it after would let a bar that crosses the
                    # cap still open, which is a one-bar divergence invisible in
                    # aggregate and fatal to the N=1 parity assertion.
                    if (not st.daily_halted
                            and (st.balance - st.day_start_balance)
                            <= -abs(st.book.daily_loss_pct) / 100.0 * st.day_start_balance):
                        st.daily_halted = True

                    des_side = desired.side if desired is not None else "flat"
                    election: Optional[Dict[str, Any]] = None
                    if desired is not None and des_side in ("long", "short"):
                        st.elections_held += 1
                        n_cands = len([c for c in contest_pool if c.side in ("long", "short")])
                        decided_by = (desired.meta or {}).get("decided_by", "unknown")
                        if n_cands >= 2:
                            election = {
                                "ts": str(ts.iloc[i]), "idx": i, "book": st.book.name,
                                "n_candidates": n_cands,
                                "winner": desired.winning_intent.strategy,
                                "decided_by": decided_by,
                                "resolution": (desired.meta or {}).get("resolution"),
                                "candidates": sorted({c.strategy for c in contest_pool
                                                      if c.side in ("long", "short")}),
                            }
                            st.contested.append(election)
                        else:
                            st.elections_uncontested += 1

                    if des_side in ("long", "short"):
                        win_name = (getattr(desired, "winning_strategy", None)
                                    or bs._winner_name(desired, latest))
                        row = latest.get(win_name)
                        if row is None:
                            pass
                        elif st.pos is None and not st.daily_halted:
                            if _open(st, side=des_side, row=row, owner=win_name, i=i,
                                     regime_label=regime_label,
                                     bar_vol_regime=bar_vol_regime) and election:
                                st.open_election[i] = election
                        elif (st.pos is not None and st.pos.side != des_side
                              and not st.daily_halted and flip_policy != "hold"):
                            _close(st, st.pos, cl[i], ts.iloc[i], "flip", i)
                            st.pos = None
                            if flip_policy == "reverse":
                                if _open(st, side=des_side, row=row, owner=win_name, i=i,
                                         regime_label=regime_label,
                                         bar_vol_regime=bar_vol_regime) and election:
                                    st.open_election[i] = election

                    eq = st.balance + bs._unrealized(st.pos, cl[i])
                    st.equity_high = max(st.equity_high, eq)
                    st.equity_curve.append((str(ts.iloc[i]), round(eq, 2)))

            for st in states:
                if st.pos is not None:
                    _close(st, st.pos, cl[-1], ts.iloc[-1], "eod", n - 1)
                    st.pos = None

        return _summarise_run(
            states, clock=clock, symbol=symbol, arbitration=arbitration,
            ranking_key=ranking_key, seed=seed, folds=folds,
            union_roster=union_roster, clock_tf=clock_tf,
            signal_ttl_bars=signal_ttl_bars, flip_policy=flip_policy,
            attach_trades=attach_trades)
    finally:
        if _prev_router is None:
            os.environ.pop("REGIME_ROUTER_DISABLED", None)
        else:
            os.environ["REGIME_ROUTER_DISABLED"] = _prev_router


# --------------------------------------------------------------------------
# Reporting — CONTESTED SUBSET ONLY, stratified by decided_by
# --------------------------------------------------------------------------
def _trade_r(t) -> Optional[float]:  # noqa: ANN001
    """PnL in R, on the harness's own risk basis: qty x |entry - ENTRY stop|.

    `_ClosedTrade.sl` is the ENTRY-time stop (`_Position.entry_sl`), not the
    trailed one — which is what makes this 1R and not a moving denominator.
    Returns None when the risk is unreadable or zero: an unmeasurable R must
    not become a 0.0 that drags an expectancy toward a value nobody observed.
    """
    try:
        risk = abs(float(t.entry) - float(t.sl)) * float(t.qty)
    except (TypeError, ValueError):
        return None
    if risk <= 0:
        return None
    return float(t.pnl) / risk


def _max_drawdown(path: Sequence[float]) -> float:
    peak = 0.0
    mdd = 0.0
    for v in path:
        peak = max(peak, v)
        mdd = max(mdd, peak - v)
    return mdd


def _contested_stats(trades: List[Any]) -> Dict[str, Any]:
    """Net PnL, expectancy in R and maxDD over a set of contested trades.

    ⚠️ `max_drawdown_usd` here is the drawdown of the CONTESTED-TRADE
    CUMULATIVE-PnL PATH (trades in exit order, starting at 0), NOT the book's
    account equity. Those are different quantities and conflating them would
    report an account drawdown the account never had. The book's real maxDD is
    reported separately, per book, under `books[].max_drawdown_usd`.
    """
    rs = [r for r in (_trade_r(t) for t in trades) if r is not None]
    ordered = sorted(trades, key=lambda t: str(t.exit_ts))
    cum = 0.0
    path = []
    for t in ordered:
        cum += float(t.pnl)
        path.append(cum)
    return {
        "trades": len(trades),
        "net_pnl_usd": round(sum(float(t.pnl) for t in trades), 2),
        "net_r": round(sum(rs), 4) if rs else 0.0,
        "expectancy_r": round(sum(rs) / len(rs), 4) if rs else None,
        "r_gradeable": len(rs),
        "r_ungradeable": len(trades) - len(rs),
        "max_drawdown_usd": round(_max_drawdown(path), 2),
        "wins": sum(1 for t in trades if t.pnl > 0),
        # The per-trade R sample itself, so the reporter can run a two-sample
        # test without a re-run. Carried rather than summarised because a mean
        # and an n cannot reconstruct a spread, and the spread is what decides
        # whether an arm's edge is separable from the control's noise.
        "r_values": [round(r, 6) for r in rs],
    }


def _fold_bounds(clock: pd.DataFrame, folds: int) -> List[Tuple[Any, Any]]:
    """A FIXED fold panel over the clock range — equal-width time slices.

    Fixed, because the gate is pooled-net AND fold-majority and a panel that
    moved between arms would make the majority half meaningless.
    """
    if folds <= 0 or clock.empty:
        return []
    t0 = pd.Timestamp(clock["timestamp"].iloc[0])
    t1 = pd.Timestamp(clock["timestamp"].iloc[-1])
    edges = pd.date_range(t0, t1, periods=folds + 1)
    return [(edges[k], edges[k + 1]) for k in range(folds)]


def _by_exit_reason(trades: List[Any]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for t in trades:
        out[t.reason] = out.get(t.reason, 0) + 1
    return dict(sorted(out.items()))


def trade_digest(t) -> Dict[str, Any]:  # noqa: ANN001
    """One closed trade, flattened. The unit the N=1 parity assertion compares."""
    return {"owner": t.owner, "side": t.side, "entry_ts": str(t.entry_ts),
            "exit_ts": str(t.exit_ts), "entry": round(float(t.entry), 8),
            "exit": round(float(t.exit), 8), "qty": round(float(t.qty), 12),
            "pnl": round(float(t.pnl), 8), "reason": t.reason,
            "decided_by": (t.meta or {}).get("_decided_by")}


def _summarise_run(states: List[_BookState], *, clock, symbol, arbitration,
                   ranking_key, seed, folds, union_roster, clock_tf,
                   signal_ttl_bars, flip_policy,
                   attach_trades: bool = False) -> Dict[str, Any]:
    all_contested: List[Any] = []
    per_book: List[Dict[str, Any]] = []
    contested_elections: List[Dict[str, Any]] = []
    total_elections = 0
    total_uncontested = 0
    total_stood_aside = 0

    for st in states:
        contested_trades = [t for t in st.closed
                            if t.entry_idx in st.open_election]
        for t in contested_trades:
            t.meta = dict(t.meta or {})
            t.meta["_decided_by"] = st.open_election[t.entry_idx]["decided_by"]
            t.meta["_book"] = st.book.name
        all_contested.extend(contested_trades)
        contested_elections.extend(st.contested)
        total_elections += st.elections_held
        total_uncontested += st.elections_uncontested
        total_stood_aside += st.stood_aside_global
        eq = [v for _, v in st.equity_curve]
        peak = st.book.initial_balance
        mdd = 0.0
        for v in eq:
            peak = max(peak, v)
            mdd = max(mdd, peak - v)
        per_book.append({
            "name": st.book.name,
            "roster": list(st.book.roster),
            "initial_balance": st.book.initial_balance,
            "final_balance": round(eq[-1] if eq else st.book.initial_balance, 2),
            "net_pnl_usd": round((eq[-1] if eq else st.book.initial_balance)
                                 - st.book.initial_balance, 2),
            "max_drawdown_usd": round(mdd, 2),
            "total_trades": len(st.closed),
            "elections_held": st.elections_held,
            "elections_contested": len(st.contested),
            "elections_uncontested": st.elections_uncontested,
            "stood_aside_global": st.stood_aside_global,
            "contested_trades": len(contested_trades),
            # ⚠️ LOAD-BEARING FOR READING THE CONTROL, not decoration. The
            # control re-rolls its draw per tick (deliberately — a frozen draw
            # would be a NAME ranking wearing a random label), so it does not
            # merely rank differently: it makes the winning OWNER unstable
            # across ticks, which the live `_election_sort_key` docstring calls
            # "strictly worse than an arbitrary-but-stable one". The flip count
            # here is how a reader tells the COST OF INSTABILITY apart from the
            # VALUE OF THE RANKING SIGNAL, which the pooled deltas cannot.
            "by_exit_reason": _by_exit_reason(st.closed),
            **({"trades": [trade_digest(t) for t in st.closed]}
               if attach_trades else {}),
            "monitor_errors": {"total": sum(st.monitor_errors.values()),
                               "by_owner": dict(st.monitor_errors),
                               "examples": dict(st.monitor_error_examples)},
        })

    by_decided: Dict[str, Dict[str, Any]] = {}
    for term in sorted({str(t.meta.get("_decided_by")) for t in all_contested}):
        by_decided[term] = _contested_stats(
            [t for t in all_contested if str(t.meta.get("_decided_by")) == term])

    fold_rows: List[Dict[str, Any]] = []
    for k, (lo_ts, hi_ts) in enumerate(_fold_bounds(clock, folds)):
        sel = [t for t in all_contested
               if lo_ts <= pd.Timestamp(t.entry_ts) < hi_ts
               or (k == folds - 1 and pd.Timestamp(t.entry_ts) == hi_ts)]
        row = {"fold": k, "start": str(lo_ts), "end": str(hi_ts)}
        row.update(_contested_stats(sel))
        fold_rows.append(row)

    decided_counts: Dict[str, int] = {}
    for e in contested_elections:
        decided_counts[e["decided_by"]] = decided_counts.get(e["decided_by"], 0) + 1

    return {
        "kind": "nbook_ranking_backtest",
        "symbol": symbol,
        "ranking_key": ranking_key,
        "seed": seed,
        "arbitration": arbitration,
        "arms_declared": list(ARMS),
        "control_arm": CONTROL_ARM,
        "shipped_arm": SHIPPED_ARM,
        "params": {"clock_tf": clock_tf, "signal_ttl_bars": signal_ttl_bars,
                   "flip_policy": flip_policy, "folds": folds,
                   "union_roster": union_roster,
                   "fee_bps_roundtrip": bs.FEE_BPS_ROUNDTRIP,
                   "slippage_bps_roundtrip": bs.SLIPPAGE_BPS_ROUNDTRIP,
                   "funding_bps_per_window": bs.FUNDING_BPS_PER_WINDOW},
        "data_start": str(clock["timestamp"].iloc[0]) if len(clock) else None,
        "data_end": str(clock["timestamp"].iloc[-1]) if len(clock) else None,
        "clock_bars": int(len(clock)),
        # ⚠️ THE POPULATION. Every headline below is the CONTESTED subset; the
        # ACHIEVED count is what the power claim must be graded against, never
        # the design's declared 1,780.
        "population": {
            "elections_held": total_elections,
            "elections_contested_achieved": len(contested_elections),
            "elections_uncontested": total_uncontested,
            "stood_aside_global": total_stood_aside,
            "contested_trades": len(all_contested),
            "declared_expected_n": 1780,
            "basis": ("a CONTESTED election is one where >=2 non-flat candidates "
                      "reached the book's election. Uncontested ticks are EXCLUDED "
                      "from every headline: a ranking key cannot matter with one "
                      "candidate, and pooling them dilutes any effect toward zero."),
        },
        "contested": _contested_stats(all_contested),
        "contested_by_decided_by": by_decided,
        "contested_decided_by_counts": decided_counts,
        "folds": fold_rows,
        "books": per_book,
    }


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def main(argv: List[str]) -> int:
    p = argparse.ArgumentParser(
        description="N-book portfolio replay with a swappable election key.")
    p.add_argument("--data", default=os.environ.get(
        "BACKTEST_DATA_PATH", "data/backtest_candles.csv"))
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--start", default=None)
    p.add_argument("--end", default=None)
    p.add_argument("--roster", default="trend_donchian,fade_breakout_4h,"
                                       "squeeze_breakout_4h,fvg_range_15m",
                   help="Default roster for books that declare none.")
    p.add_argument("--book", action="append", default=[], metavar="SPEC",
                   help="name=X:roster=a|b:balance=N:risk_pct=N:daily_loss_pct=N. "
                        "Repeatable. NONE given = ONE book named 'main' carrying "
                        "--roster, which is the one-book harness's shape.")
    p.add_argument("--initial-balance", type=float, default=10_000.0)
    p.add_argument("--risk-pct", type=float, default=0.3)
    p.add_argument("--daily-loss-pct", type=float, default=3.0)
    p.add_argument("--signal-ttl-bars", type=int, default=1)
    p.add_argument("--clock-tf", default="15m", choices=list(bs._PANDAS_TF.keys()))
    p.add_argument("--flip-policy", default="reverse", choices=["reverse", "hold", "flat"])
    p.add_argument("--arbitration", default="per_account", choices=list(ARBITRATIONS))
    p.add_argument("--ranking-key", default=SHIPPED_ARM, choices=list(ARMS),
                   help=f"Election-key arm. {SHIPPED_ARM} is the SHIPPED live key "
                        f"(imported, not restated); {CONTROL_ARM} is the CONTROL "
                        f"and the floor the others must clear.")
    p.add_argument("--seed", type=int, default=0,
                   help="Seed for the random_tiebreak control. Deterministic: a "
                        "control nobody can re-run is not evidence.")
    p.add_argument("--folds", type=int, default=6,
                   help="Fixed fold panel for the majority half of the gate.")
    p.add_argument("--refresh-signals", action="store_true")
    p.add_argument("--json", dest="json_out", default=None)
    args = p.parse_args(argv[1:])

    default_roster = [r.strip() for r in args.roster.split(",")
                      if r.strip() in bs.ROSTER]
    if not default_roster:
        print("ERROR: --roster names no strategy in the harness ROSTER",
              file=sys.stderr)
        return 1
    try:
        books = ([parse_book_spec(s, default_roster=default_roster,
                                  default_balance=args.initial_balance,
                                  default_risk_pct=args.risk_pct,
                                  default_daily_loss_pct=args.daily_loss_pct)
                  for s in args.book]
                 or [Book(name="main", roster=tuple(default_roster),
                          initial_balance=args.initial_balance,
                          risk_pct=args.risk_pct,
                          daily_loss_pct=args.daily_loss_pct)])
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    names = [b.name for b in books]
    if len(set(names)) != len(names):
        print(f"ERROR: duplicate book names {names}", file=sys.stderr)
        return 1

    bs.SLIPPAGE_BPS_ROUNDTRIP = execution_costs.slippage_bps_roundtrip_for(args.symbol)
    bs.FUNDING_BPS_PER_WINDOW = execution_costs.funding_bps_per_window_for(args.symbol)

    try:
        base5m = bs._load_candles(args.data)
    except Exception as exc:  # noqa: BLE001  # allow-silent: NOT silent — the CLI prints the cause and returns 1, so the caller (and the workflow step) fails. A narrower type would let an unanticipated loader failure traceback instead of naming the file.
        print(f"ERROR: load failed: {exc}", file=sys.stderr)
        return 1

    out = run_nbook(base5m, books=books, start=args.start, end=args.end,
                    signal_ttl_bars=args.signal_ttl_bars, clock_tf=args.clock_tf,
                    refresh=args.refresh_signals, symbol=args.symbol,
                    arbitration=args.arbitration, ranking_key=args.ranking_key,
                    seed=args.seed, flip_policy=args.flip_policy, folds=args.folds)
    print(_fmt(out))
    if args.json_out:
        payload = json.dumps(out, indent=2, default=str)
        if args.json_out == "-":
            print(payload)
        else:
            Path(args.json_out).parent.mkdir(parents=True, exist_ok=True)
            Path(args.json_out).write_text(payload)
            print(f"JSON -> {args.json_out}", file=sys.stderr)
    return 0


def _fmt(s: Dict[str, Any]) -> str:
    pop = s["population"]
    c = s["contested"]
    L = [f"nbook_ranking_backtest — {s['symbol']} arm={s['ranking_key']} "
         f"arbitration={s['arbitration']} seed={s['seed']}",
         f"  data {s['data_start']} -> {s['data_end']}  bars={s['clock_bars']}",
         f"  POPULATION: contested elections ACHIEVED={pop['elections_contested_achieved']} "
         f"(declared expectation {pop['declared_expected_n']}), "
         f"uncontested={pop['elections_uncontested']}, "
         f"stood_aside_global={pop['stood_aside_global']}",
         f"  CONTESTED SUBSET: trades={c['trades']} net=${c['net_pnl_usd']} "
         f"netR={c['net_r']} expectancyR={c['expectancy_r']} "
         f"maxDD(contested path)=${c['max_drawdown_usd']} "
         f"(R gradeable {c['r_gradeable']}/{c['trades']})"]
    for term, st in sorted(s["contested_by_decided_by"].items()):
        L.append(f"    decided_by={term:18s} n={st['trades']:4d} "
                 f"net=${st['net_pnl_usd']:>10} expectancyR={st['expectancy_r']}")
    for b in s["books"]:
        L.append(f"  book {b['name']:12s} net=${b['net_pnl_usd']:>10} "
                 f"maxDD=${b['max_drawdown_usd']:>9} trades={b['total_trades']:4d} "
                 f"contested={b['contested_trades']:4d} "
                 f"stood_aside={b['stood_aside_global']}")
        if b["monitor_errors"]["total"]:
            L.append(f"    ⚠️ monitor_errors={b['monitor_errors']['total']} — the exit "
                     f"profile under-reports monitor-driven exits by an unknown amount")
    return "\n".join(L)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
