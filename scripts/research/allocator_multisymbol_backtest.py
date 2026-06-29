#!/usr/bin/env python3
"""Cross-SYMBOL portfolio allocator backtest — M18 P(b) research (Tier-1).

WHY THIS EXISTS (M18 capital-allocator program; the untested thesis).
``docs/research/M18-allocator-backtest-findings-2026-06-29.md`` recorded that the
INTRA-symbol EV ranking has NO edge: on a single BTC book the cost-aware EV pick
and the priority-aggregator pick agree ~96-97% of the time and the disagreements
are noise. ``scripts/backtest_system.py --allocator off|ev`` proved that on ONE
symbol. The allocator's REAL value proposition — per the design doc
``docs/research/capital-allocation-ai-DESIGN.md`` § 5.3 — is CROSS-symbol /
cross-market: when candidate trades from DIFFERENT symbols compete for a shared
capital / risk budget at the SAME tick, does deploying capital to the highest
cost-aware-EV candidate(s) beat treating each symbol independently? That is what
this harness measures, and what the single-symbol harness structurally cannot.

WHAT IT DOES. Two arms over one shared clock across N symbols (each with its own
data CSV + its own symbol-appropriate strategies):

  * BASELINE ("independent") — each symbol trades on its OWN budget slice (the
    initial balance split equally across the N symbols). This is today's
    behaviour: per-symbol, per-account independent sizing with no global step.
    By construction it equals the sum of N independent single-symbol runs.

  * ALLOCATOR ("ev") — a SHARED risk budget + a max-concurrent-positions cap.
    At each tick we gather the actionable candidates across ALL symbols, rank
    them by ``src.runtime.allocator_ev.candidate_ev_score`` (the SAME cost-aware
    EV_R scorer the live soak ranks on), and open only the top-K that fit the
    shared budget (greedy EV / unit-risk). Each opened position is then managed
    by the SAME per-symbol monitor/SL/TP logic the baseline uses.

REUSE (faithfulness). Signal streams come from
``scripts.backtest_system.generate_signal_stream`` — the EXACT per-bar
``order_package(cfg, candles_df)`` path the live trader and the single-symbol
harness use. We do NOT reimplement signal generation. The position bookkeeping
(fills at the bar close proxy, fees, equity, daily-loss cap, monitor()/SL/TP
exits) is forked into a small standalone ``_SymBook`` engine here because
``backtest_system.run_system_backtest`` hard-assumes ONE symbol and ONE shared
netted position — it has no seam for N independent per-symbol books under one
budget. That fork is intentional + documented (see ``_SymBook`` /
``_run_portfolio``); its per-position math mirrors ``run_system_backtest``'s
``_close`` / ``_unrealized`` / ``_risk_qty`` line-for-line so the two engines
price a trade identically.

NO LOOKAHEAD. Each symbol's signal becomes actionable on the NEXT clock bar after
its bar close (``np.searchsorted(..., side="right")`` — the same no-lookahead map
``run_system_backtest`` uses). The portfolio loop advances ONE shared clock tick
at a time; at tick ``i`` it only ever reads bar ``i`` (and the past window for
monitor()). No future bar is consulted in either arm.

CAPITAL ACCOUNTING INVARIANTS (asserted in the smoke test):
  * The allocator arm never exceeds the shared budget: at most ``max_concurrent``
    open positions, and each open's risk_usd is drawn from the shared
    risk-budget pool — an open is refused when the remaining pool can't fund it.
  * Realised PnL is conserved: final_balance == initial + Σ closed-trade pnl
    (both arms), checked to the cent.
  * Baseline == Σ independent per-symbol books: the baseline arm runs each symbol
    on an isolated balance slice with no cross-symbol interaction, so its
    aggregate equals N independent runs (the smoke test cross-checks one symbol's
    baseline book against an isolated single-symbol run of the same engine).

HONEST LIMITATIONS (read before trusting any number — also in the report):
  * SINGLE shared account. The live system is multi-ACCOUNT (bybit_2, ib_paper,
    alpaca, prop…) with per-account risk blocks, modes, and broker quirks. This
    models ONE shared crypto-style account (fractional qty, % risk, taker fee).
    It does NOT model prop rulesets, futures whole-contract sizing, IB/Alpaca
    fill mechanics, funding/swap, or per-account daily-loss interplay.
  * P_win == strategy confidence (c_strat). ``candidate_ev_score`` uses the
    candidate's ``confidence`` as its win-prob proxy (the scorer's documented P1
    behaviour). The full conviction blend + ML heads + per-cell historical
    expectancy (design § 5.2) are NOT applied here. A miscalibrated confidence
    directly biases the EV ranking.
  * Fees are a fixed round-trip bps model (``--fee-bps-roundtrip``); funding_R=0.
    No per-symbol fee differentiation beyond the single bps; no perp funding.
  * Fill proxy = the clock bar's close (same proxy as ``run_system_backtest``),
    not next-bar open; intrabar SL-before-TP is the conservative ordering.
  * Greedy EV/risk only (design § 5.3 selector step 1). No correlation/covariance
    risk budgeting (step 2) and no constrained optimisation (step 3) — so two
    highly-correlated longs can both be opened. This is the FIRST selector, not
    the final one.
  * Per-symbol netting within a symbol is single-position (one open per symbol,
    same as the single-symbol harness's ``suppress`` re-entry default); we do not
    model same-symbol pyramiding here.
  * Sample-size: the local smoke fixture is a tiny synthetic 2-symbol set purely
    to prove the engine + invariants. NO conclusion may be drawn from it. Real
    evidence needs the trainer-VM multi-symbol CSVs (BTC/ETH/SOL 5m, etc.).

Tier-1 research tooling. Imports the live ``allocator_ev`` scorer + ``intents``
(read-only) and reuses the research harness's signal generator; it does NOT
import or alter any live-ORDER path, and writes nothing outside its --json out.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT_DIR = str(Path(__file__).resolve().parent)
# Same sys.path hygiene as scripts/backtest_system.py: drop the script dir (so a
# `scripts.ml` package can't shadow the repo-root `ml` package) and force the
# repo root to the front so `src.*` / `scripts.*` resolve to the repo packages.
sys.path[:] = [p for p in sys.path if os.path.abspath(p) != _SCRIPT_DIR]
sys.path.insert(0, str(_REPO_ROOT))

# Reuse the research harness's faithful signal generator + roster + resamplers.
from scripts.backtest_system import (  # noqa: E402
    ROSTER,
    _PANDAS_TF,
    _date_filter,
    _import_callable,
    _load_candles,
    _load_strategy_cfg,
    _resample,
    generate_signal_stream,
)
from src.runtime.allocator_ev import (  # noqa: E402
    DEFAULT_FEE_BPS_ROUNDTRIP,
    compute_ev_r,
)

# Default per-symbol roster when --roster is not given for a symbol. Keyed by a
# coarse symbol family; values are research-harness ROSTER names. A symbol with
# no family match falls back to the BTC headline roster (the strategies are
# symbol-parameterised via cfg, so they still run — just flagged in the header).
_DEFAULT_ROSTER_BY_FAMILY: Dict[str, List[str]] = {
    "BTC": ["trend_donchian", "fade_breakout_4h", "squeeze_breakout_4h", "fvg_range_15m"],
    "ETH": ["trend_donchian_eth", "trend_donchian_eth_4h", "eth_pullback_2h"],
    "SOL": ["trend_donchian_sol", "trend_donchian_sol_4h", "sol_pullback_2h"],
}
_HEADLINE_ROSTER = _DEFAULT_ROSTER_BY_FAMILY["BTC"]


def _family_of(symbol: str) -> str:
    s = symbol.upper()
    for fam in _DEFAULT_ROSTER_BY_FAMILY:
        if s.startswith(fam):
            return fam
    return "BTC"


def _default_roster_for(symbol: str) -> List[str]:
    return list(_DEFAULT_ROSTER_BY_FAMILY.get(_family_of(symbol), _HEADLINE_ROSTER))


# --------------------------------------------------------------------------
# Per-symbol position book — one open position per symbol, shared-balance aware.
# Mirrors backtest_system.run_system_backtest's _Position / _close /
# _unrealized / _risk_qty so a trade is priced identically; the only addition is
# that opens draw from (and closes return to) a budget the PORTFOLIO owns, so
# the allocator arm can enforce a SHARED cap across symbols.
# --------------------------------------------------------------------------
@dataclass
class _OpenPos:
    symbol: str
    side: str
    qty: float
    entry: float
    sl: float
    tp: float
    owner: str
    entry_ts: Any
    entry_idx: int
    meta: dict
    risk_usd: float  # the risk budget this position consumed at open (for the cap)


@dataclass
class _Closed:
    symbol: str
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


@dataclass
class _Candidate:
    """One actionable cross-symbol candidate at a tick (pre-selection)."""
    symbol: str
    owner: str
    side: str
    entry: float
    sl: float
    tp: float
    confidence: float
    meta: dict
    ev_r: Optional[float]
    risk_per_unit: float  # |entry - sl|


@dataclass
class _SymState:
    """Per-symbol clock + signal map + the open position, on the SHARED clock."""
    symbol: str
    roster: List[str]
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    ts: pd.Series
    clock_df: pd.DataFrame
    clock_ts_np: np.ndarray  # clock_df["timestamp"] as tz-naive datetime64[ns] (as-of monitor window)
    sig_at: Dict[int, Dict[str, dict]]
    cfgs: Dict[str, dict]
    monitors: Dict[str, Any]
    latest: Dict[str, dict] = field(default_factory=dict)
    latest_idx: Dict[str, int] = field(default_factory=dict)
    pos: Optional[_OpenPos] = None


def _risk_qty(bal: float, rpct: float, entry_px: float, sl_px: float) -> float:
    """qty s.t. |entry-sl|*qty == bal*rpct%  (== run_system_backtest._risk_qty)."""
    stop_dist = abs(entry_px - sl_px)
    if stop_dist <= 0 or bal <= 0 or rpct <= 0:
        return 0.0
    return (bal * (rpct / 100.0)) / stop_dist


def _unrealized(pos: Optional[_OpenPos], price: float) -> float:
    if pos is None:
        return 0.0
    return (price - pos.entry) * pos.qty if pos.side == "long" else (pos.entry - price) * pos.qty


# --------------------------------------------------------------------------
# Build the shared clock + per-symbol signal maps (no lookahead).
# --------------------------------------------------------------------------
def _build_sym_states(
    *, symbols: List[str], data: Dict[str, str], rosters: Dict[str, List[str]],
    start, end, clock_tf: str, refresh: bool,
) -> Tuple[List[Any], Dict[str, _SymState]]:
    """Resample each symbol to the clock TF, generate its signal streams, and
    map each signal to the NEXT clock bar after its close (no lookahead).

    The shared clock is the UNION of all symbols' clock timestamps (sorted), so a
    tick where only one symbol has a bar still advances; each symbol indexes its
    own bar arrays against the shared grid via an as-of (backward) merge — at a
    tick where a symbol has no fresh bar, its last-known OHLC carries (monitor /
    SL/TP still evaluate against the most recent price, never a future one).
    """
    per_sym_clock: Dict[str, pd.DataFrame] = {}
    streams_by_sym: Dict[str, Dict[str, pd.DataFrame]] = {}
    union_ts: List[pd.Timestamp] = []

    for sym in symbols:
        base = _load_candles(data[sym])
        clock = _date_filter(_resample(base, _PANDAS_TF[clock_tf]), start, end).reset_index(drop=True)
        if clock.empty:
            raise RuntimeError(f"{sym}: empty clock after resample/date-filter ({data[sym]})")
        per_sym_clock[sym] = clock
        union_ts.extend(list(clock["timestamp"]))
        streams: Dict[str, pd.DataFrame] = {}
        for name in rosters[sym]:
            streams[name] = generate_signal_stream(
                name, base, start=start, end=end, overrides={}, refresh=refresh, symbol=sym)
        streams_by_sym[sym] = streams

    shared_ts = sorted(set(pd.Timestamp(t) for t in union_ts))
    # Strip tz before -> datetime64 (np has no tz repr; all bars are UTC, so the
    # naive instants are identical for the searchsorted index).
    shared_ts_np = np.array([np.datetime64(pd.Timestamp(t).tz_localize(None)) for t in shared_ts])
    n = len(shared_ts)

    states: Dict[str, _SymState] = {}
    for sym in symbols:
        clock = per_sym_clock[sym]
        # As-of align each symbol's bars onto the shared grid (backward = past-only).
        aligned = pd.merge_asof(
            pd.DataFrame({"timestamp": shared_ts}),
            clock[["timestamp", "high", "low", "close"]].sort_values("timestamp"),
            on="timestamp", direction="backward",
        )
        high = aligned["high"].to_numpy(float)
        low = aligned["low"].to_numpy(float)
        close = aligned["close"].to_numpy(float)

        # Map each strategy's signal rows onto SHARED clock indices: the signal
        # is actionable on the NEXT shared bar at/after its (right-labelled) bar
        # close. side="right" == no lookahead (same as run_system_backtest).
        sig_at: Dict[int, Dict[str, dict]] = {}
        for name, s in streams_by_sym[sym].items():
            for _, r in s.iterrows():
                sig_t = pd.Timestamp(r["ts"])
                if sig_t.tzinfo is not None:
                    sig_t = sig_t.tz_localize(None)
                idx = int(np.searchsorted(shared_ts_np, np.datetime64(sig_t), side="right"))
                if idx >= n:
                    continue
                sig_at.setdefault(idx, {})[name] = r.to_dict()

        cfgs = {
            name: {"symbol": sym, "timeframe": ROSTER[name]["tf"], **_load_strategy_cfg(name)}
            for name in rosters[sym]
        }
        monitors = {name: _import_callable(ROSTER[name]["module"], "monitor") for name in rosters[sym]}
        clock_ts_np = np.array(
            [np.datetime64(pd.Timestamp(t).tz_localize(None)) for t in clock["timestamp"]]
        )
        states[sym] = _SymState(
            symbol=sym, roster=list(rosters[sym]), high=high, low=low, close=close,
            ts=pd.Series(shared_ts), clock_df=clock, clock_ts_np=clock_ts_np,
            sig_at=sig_at, cfgs=cfgs, monitors=monitors)
    return shared_ts, states


# --------------------------------------------------------------------------
# The portfolio engine — drives BOTH arms over the shared clock.
# --------------------------------------------------------------------------
def _winner_intent(intents, symbol: str):
    """Priority winner among a symbol's directional intents (REAL aggregator)."""
    from src.runtime.intents import aggregate_intents
    if not intents:
        return None
    desired = aggregate_intents(intents, symbol=symbol)
    return desired


def _collect_symbol_candidate(
    st: _SymState, i: int, signal_ttl_bars: int, fee_bps: float, symbol: str,
) -> Optional[_Candidate]:
    """The one actionable candidate for ``symbol`` at tick ``i`` (priority winner
    among its live intents), scored with cost-aware EV_R. None if no directional
    intent is live. Reuses the REAL aggregate_intents to pick the per-symbol
    winner (so within a symbol the selection is exactly the live path); the
    CROSS-symbol choice is what the allocator arm then makes."""
    from src.runtime.intents import DEFAULT_PRIORITIES, StrategyIntent

    # refresh latest live signals + drop stale (TTL)
    if i in st.sig_at:
        for name, row in st.sig_at[i].items():
            st.latest[name] = row
            st.latest_idx[name] = i
    for name in list(st.latest):
        if i - st.latest_idx[name] >= signal_ttl_bars:
            st.latest.pop(name, None)

    intents = []
    for name, row in st.latest.items():
        if row["side"] not in ("long", "short"):
            continue
        intents.append(StrategyIntent(
            strategy=name, symbol=symbol, side=row["side"], target_qty=1.0,
            entry=row["entry"], sl=row["sl"], tp=row["tp"],
            confidence=row["confidence"], meta={"_stream": True}))
    if not intents:
        return None
    desired = _winner_intent(intents, symbol)
    if desired is None or getattr(desired, "side", "flat") not in ("long", "short"):
        return None
    # Resolve the winning strategy name (best-effort across field variants).
    win_name = None
    for attr in ("winning_strategy", "winner", "strategy"):
        v = getattr(desired, attr, None)
        if isinstance(v, str) and v in st.latest:
            win_name = v
            break
    if win_name is None:
        wi = getattr(desired, "winning_intent", None)
        if wi is not None and getattr(wi, "strategy", None) in st.latest:
            win_name = wi.strategy
    if win_name is None:
        cands = [n for n, r in st.latest.items() if r["side"] == desired.side]
        win_name = max(cands, key=lambda n: DEFAULT_PRIORITIES.get(n, 0), default=None)
    row = st.latest.get(win_name)
    if row is None:
        return None
    side = desired.side
    entry = float(st.close[i])  # fill proxy = current bar close (== run_system_backtest)
    sl, tp = float(row["sl"]), float(row["tp"])
    ev = compute_ev_r(entry=entry, sl=sl, tp=tp, p_win=float(row.get("confidence", 0.0)),
                      fee_bps_roundtrip=fee_bps)
    return _Candidate(
        symbol=symbol, owner=win_name, side=side, entry=entry, sl=sl, tp=tp,
        confidence=float(row.get("confidence", 0.0)), meta=json.loads(row["meta_json"]),
        ev_r=ev, risk_per_unit=abs(entry - sl))


def _manage_open(st: _SymState, i: int) -> Optional[_Closed]:
    """Run intrabar SL/TP then the owner's REAL monitor() on the open position.
    Returns the _Closed (and clears st.pos) on a close, else None. PnL is NOT
    applied to balance here — the caller does that so the budget pool is updated
    in lockstep."""
    pos = st.pos
    if pos is None:
        return None
    hi, lo, c = st.high[i], st.low[i], st.close[i]
    closed = None

    def _mk_close(price: float, reason: str) -> _Closed:
        gross = (price - pos.entry) * pos.qty if pos.side == "long" else (pos.entry - price) * pos.qty
        fee = (st._fee_rate) * (pos.entry + price) * pos.qty  # type: ignore[attr-defined]
        return _Closed(symbol=pos.symbol, owner=pos.owner, side=pos.side, entry_ts=pos.entry_ts,
                       exit_ts=st.ts.iloc[i], entry=pos.entry, exit=price, qty=pos.qty,
                       pnl=gross - fee, fee=fee, reason=reason, bars_held=i - pos.entry_idx)

    # intrabar SL/TP first (conservative: SL before TP)
    if pos.side == "long":
        if lo <= pos.sl:
            closed = _mk_close(pos.sl, "sl")
        elif hi >= pos.tp:
            closed = _mk_close(pos.tp, "tp")
    else:
        if hi >= pos.sl:
            closed = _mk_close(pos.sl, "sl")
        elif lo <= pos.tp:
            closed = _mk_close(pos.tp, "tp")
    if closed is not None:
        st.pos = None
        return closed

    # owner monitor() (trail ratchet / time-decay / explicit close)
    mon = st.monitors.get(pos.owner)
    if mon is not None:
        # PAST-ONLY window: the symbol's own clock bars up to (and including) the
        # current shared timestamp — NEVER future bars. searchsorted side='right'
        # includes a bar exactly at now; tail ~300 (the monitor reads its own TF).
        now64 = np.datetime64(pd.Timestamp(st.ts.iloc[i]).tz_localize(None))
        asof = int(np.searchsorted(st.clock_ts_np, now64, side="right"))
        recent = st.clock_df.iloc[max(0, asof - 300):asof]
        if recent.empty:
            return None
        open_pkg = {"direction": pos.side, "entry": pos.entry, "sl": pos.sl, "tp": pos.tp,
                    "meta": pos.meta, "created_at": str(pos.entry_ts)}
        try:
            verdict = mon(st.cfgs.get(pos.owner, {}), recent, open_pkg)
        except Exception:  # noqa: BLE001
            verdict = None
        if isinstance(verdict, dict):
            if verdict.get("action") == "close":
                closed = _mk_close(c, verdict.get("reason", "monitor_close"))
                st.pos = None
                return closed
            if "sl" in verdict:
                pos.sl = float(verdict["sl"])
            elif "tp" in verdict:
                pos.tp = float(verdict["tp"])
    return None


def _run_portfolio(
    *, arm: str, symbols: List[str], states: Dict[str, _SymState], shared_ts,
    initial_balance: float, risk_pct: float, daily_loss_pct: float,
    signal_ttl_bars: int, fee_bps: float, max_concurrent: int,
) -> Dict[str, Any]:
    """Drive one arm over the shared clock.

    arm == "independent" (BASELINE): each symbol gets balance/N as its OWN
        isolated book; opens size off that symbol's own slice; no cross-symbol
        budget. == the sum of N independent single-symbol runs.

    arm == "ev" (ALLOCATOR): ONE shared balance; ONE shared risk-budget pool +
        a max_concurrent cap. Each tick, gather every symbol's actionable
        candidate, rank by EV_R / unit-risk, and OPEN greedily the top ones that
        (a) fit under max_concurrent and (b) can be funded from the shared pool.
        The per-symbol manage/exit logic is identical to the baseline's.
    """
    fee_rate = fee_bps / 10_000.0
    for st in states.values():
        st._fee_rate = fee_rate  # type: ignore[attr-defined]
        st.latest = {}
        st.latest_idx = {}
        st.pos = None

    n = len(shared_ts)
    closed: List[_Closed] = []
    equity_curve: List[Tuple[str, float]] = []

    # contested-tick diagnostics (allocator-specific)
    contested_ticks = 0          # ticks with >= 2 cross-symbol candidates + a free slot
    allocator_skips = 0          # times a lower-EV symbol's candidate was skipped for a better one
    budget_binds = 0             # ticks where the concurrency/budget cap actually bound

    if arm == "independent":
        # Per-symbol isolated balances. Budget never crosses symbols.
        bal: Dict[str, float] = {s: initial_balance / len(symbols) for s in symbols}
        day = None
        day_start: Dict[str, float] = dict(bal)
        for i in range(n):
            d = pd.Timestamp(shared_ts[i]).date()
            if d != day:
                day = d
                day_start = dict(bal)
            for sym in symbols:
                st = states[sym]
                # manage open first
                cl = _manage_open(st, i)
                if cl is not None:
                    bal[sym] += cl.pnl
                    closed.append(cl)
                cand = _collect_symbol_candidate(st, i, signal_ttl_bars, fee_bps, sym)
                if cand is None or st.pos is not None:
                    continue
                halted = (bal[sym] - day_start[sym]) <= -abs(daily_loss_pct) / 100.0 * day_start[sym]
                if halted:
                    continue
                qty = _risk_qty(bal[sym], risk_pct, cand.entry, cand.sl)
                if qty <= 0:
                    continue
                st.pos = _OpenPos(symbol=sym, side=cand.side, qty=qty, entry=cand.entry,
                                  sl=cand.sl, tp=cand.tp, owner=cand.owner, entry_ts=st.ts.iloc[i],
                                  entry_idx=i, meta=cand.meta,
                                  risk_usd=bal[sym] * (risk_pct / 100.0))
            eq = sum(bal[s] + _unrealized(states[s].pos, states[s].close[i]) for s in symbols)
            equity_curve.append((str(shared_ts[i]), round(eq, 2)))
        final_real = sum(bal.values())

    else:  # arm == "ev" — shared budget + greedy EV selection
        balance = initial_balance
        day = None
        day_start_balance = balance
        # Shared risk-budget pool: total risk-$ that may be deployed at once is
        # capped by both (a) max_concurrent and (b) a pool = balance * risk_pct% *
        # max_concurrent. An open consumes balance*risk_pct% of the pool; it's
        # released on close. This is the SHARED cap the design's selector binds on.
        for i in range(n):
            d = pd.Timestamp(shared_ts[i]).date()
            if d != day:
                day = d
                day_start_balance = balance
            # 1) manage every open position first (exits free up slots + pool)
            for sym in symbols:
                cl = _manage_open(states[sym], i)
                if cl is not None:
                    balance += cl.pnl
                    closed.append(cl)
            open_count = sum(1 for s in symbols if states[s].pos is not None)
            # 2) gather candidates across symbols that currently have NO open pos
            cands: List[_Candidate] = []
            for sym in symbols:
                st = states[sym]
                cand = _collect_symbol_candidate(st, i, signal_ttl_bars, fee_bps, sym)
                if cand is not None and st.pos is None:
                    cands.append(cand)
            halted = (balance - day_start_balance) <= -abs(daily_loss_pct) / 100.0 * day_start_balance
            free_slots = max_concurrent - open_count
            if len(cands) >= 2 and free_slots > 0 and not halted:
                contested_ticks += 1
            if cands and free_slots > 0 and not halted:
                # rank by EV per unit-risk (greedy). EV_R is already per-unit-risk
                # (R-units), so rank directly on EV_R; None EV ranks last.
                ranked = sorted(cands, key=lambda cc: (cc.ev_r if cc.ev_r is not None else -1e9),
                                reverse=True)
                opened = 0
                pool_remaining = balance * (risk_pct / 100.0) * max_concurrent \
                    - open_count * balance * (risk_pct / 100.0)
                for cand in ranked:
                    if opened >= free_slots:
                        # we hit the concurrency cap with candidates still waiting
                        budget_binds += 1
                        allocator_skips += len(ranked) - (ranked.index(cand))
                        break
                    risk_usd = balance * (risk_pct / 100.0)
                    if risk_usd > pool_remaining + 1e-9:
                        budget_binds += 1
                        allocator_skips += len(ranked) - (ranked.index(cand))
                        break
                    qty = _risk_qty(balance, risk_pct, cand.entry, cand.sl)
                    if qty <= 0:
                        continue
                    st = states[cand.symbol]
                    st.pos = _OpenPos(symbol=cand.symbol, side=cand.side, qty=qty, entry=cand.entry,
                                      sl=cand.sl, tp=cand.tp, owner=cand.owner, entry_ts=st.ts.iloc[i],
                                      entry_idx=i, meta=cand.meta, risk_usd=risk_usd)
                    pool_remaining -= risk_usd
                    opened += 1
            eq = balance + sum(_unrealized(states[s].pos, states[s].close[i]) for s in symbols)
            equity_curve.append((str(shared_ts[i]), round(eq, 2)))
        final_real = balance

    # final mark-to-close any still-open positions at the last shared bar
    for sym in symbols:
        st = states[sym]
        if st.pos is not None:
            p = st.pos
            price = st.close[-1]
            gross = (price - p.entry) * p.qty if p.side == "long" else (p.entry - price) * p.qty
            fee = fee_rate * (p.entry + price) * p.qty
            closed.append(_Closed(symbol=sym, owner=p.owner, side=p.side, entry_ts=p.entry_ts,
                                  exit_ts=st.ts.iloc[-1], entry=p.entry, exit=price, qty=p.qty,
                                  pnl=gross - fee, fee=fee, reason="eod", bars_held=(n - 1) - p.entry_idx))
            final_real += gross - fee
            st.pos = None

    return _summarize_arm(
        arm=arm, symbols=symbols, closed=closed, equity_curve=equity_curve,
        initial_balance=initial_balance, final_balance=final_real,
        contested_ticks=contested_ticks, allocator_skips=allocator_skips,
        budget_binds=budget_binds, max_concurrent=max_concurrent)


def _summarize_arm(*, arm, symbols, closed, equity_curve, initial_balance,
                   final_balance, contested_ticks, allocator_skips, budget_binds,
                   max_concurrent) -> Dict[str, Any]:
    eq = [e for _, e in equity_curve]
    peak = initial_balance
    mdd = 0.0
    for v in eq:
        peak = max(peak, v)
        mdd = max(mdd, peak - v)
    n = len(closed)
    wins = [t for t in closed if t.pnl > 0]
    final_balance = float(final_balance)
    initial_balance = float(initial_balance)
    realised = round(float(sum(t.pnl for t in closed)), 2)
    per_sym: Dict[str, Dict[str, Any]] = {}
    for t in closed:
        s = per_sym.setdefault(t.symbol, {"trades": 0, "pnl": 0.0, "wins": 0})
        s["trades"] += 1
        s["pnl"] = round(float(s["pnl"]) + float(t.pnl), 2)
        s["wins"] += 1 if t.pnl > 0 else 0
    out: Dict[str, Any] = {
        "arm": arm,
        "symbols": symbols,
        "initial_balance": round(initial_balance, 2),
        "final_balance": round(final_balance, 2),
        "net_pnl": round(final_balance - initial_balance, 2),
        "return_pct": round(100 * (final_balance - initial_balance) / initial_balance, 2) if initial_balance else 0.0,
        "realised_pnl_sum": realised,
        "max_drawdown_usd": round(mdd, 2),
        "max_drawdown_pct": round(100 * mdd / peak, 2) if peak else 0.0,
        "total_trades": n,
        "win_rate_pct": round(100 * len(wins) / n, 2) if n else 0.0,
        "per_symbol": per_sym,
        "equity_curve_tail": equity_curve[-3:],
    }
    if arm == "ev":
        out["allocator"] = {
            "max_concurrent": max_concurrent,
            "cross_symbol_contested_ticks": contested_ticks,
            "lower_ev_skips": allocator_skips,
            "budget_binds": budget_binds,
        }
    return out


# --------------------------------------------------------------------------
# Top-level runner
# --------------------------------------------------------------------------
def run_multisymbol_backtest(
    *, symbols: List[str], data: Dict[str, str], rosters: Dict[str, List[str]],
    start, end, clock_tf: str, initial_balance: float, risk_pct: float,
    daily_loss_pct: float, signal_ttl_bars: int, fee_bps: float,
    max_concurrent: int, refresh: bool,
) -> Dict[str, Any]:
    shared_ts, states = _build_sym_states(
        symbols=symbols, data=data, rosters=rosters, start=start, end=end,
        clock_tf=clock_tf, refresh=refresh)
    baseline = _run_portfolio(
        arm="independent", symbols=symbols, states=states, shared_ts=shared_ts,
        initial_balance=initial_balance, risk_pct=risk_pct, daily_loss_pct=daily_loss_pct,
        signal_ttl_bars=signal_ttl_bars, fee_bps=fee_bps, max_concurrent=max_concurrent)
    allocator = _run_portfolio(
        arm="ev", symbols=symbols, states=states, shared_ts=shared_ts,
        initial_balance=initial_balance, risk_pct=risk_pct, daily_loss_pct=daily_loss_pct,
        signal_ttl_bars=signal_ttl_bars, fee_bps=fee_bps, max_concurrent=max_concurrent)
    return {
        "kind": "allocator_multisymbol_backtest",
        "symbols": symbols,
        "rosters": rosters,
        "data": data,
        "params": {
            "clock_tf": clock_tf, "initial_balance": initial_balance, "risk_pct": risk_pct,
            "daily_loss_pct": daily_loss_pct, "signal_ttl_bars": signal_ttl_bars,
            "fee_bps_roundtrip": fee_bps, "max_concurrent": max_concurrent,
            "start": str(start), "end": str(end),
        },
        "shared_clock_bars": len(shared_ts),
        "data_start": str(shared_ts[0]) if shared_ts else None,
        "data_end": str(shared_ts[-1]) if shared_ts else None,
        "baseline_independent": baseline,
        "allocator_ev": allocator,
        "comparison": {
            "net_pnl_delta_ev_minus_baseline": round(
                allocator["net_pnl"] - baseline["net_pnl"], 2),
            "maxdd_pct_delta_ev_minus_baseline": round(
                allocator["max_drawdown_pct"] - baseline["max_drawdown_pct"], 2),
            "ev_beats_baseline_net": bool(allocator["net_pnl"] > baseline["net_pnl"]),
        },
    }


def _fmt(s: Dict[str, Any]) -> str:
    b = s["baseline_independent"]
    a = s["allocator_ev"]
    al = a.get("allocator", {})
    L = [
        f"allocator_multisymbol_backtest — symbols={s['symbols']}",
        f"  data {s['data_start']} -> {s['data_end']}  shared_clock_bars={s['shared_clock_bars']}",
        f"  params: clock={s['params']['clock_tf']} bal={s['params']['initial_balance']:.0f} "
        f"risk%={s['params']['risk_pct']} daily_loss%={s['params']['daily_loss_pct']} "
        f"fee_bps={s['params']['fee_bps_roundtrip']} max_concurrent={s['params']['max_concurrent']}",
        "  ── ARM: BASELINE (independent per-symbol budgets) ──",
        f"    net=${b['net_pnl']:.0f} ({b['return_pct']}%)  maxDD={b['max_drawdown_pct']}%  "
        f"trades={b['total_trades']} WR={b['win_rate_pct']}%",
        f"    per-symbol: {b['per_symbol']}",
        "  ── ARM: ALLOCATOR (shared budget, greedy EV/risk) ──",
        f"    net=${a['net_pnl']:.0f} ({a['return_pct']}%)  maxDD={a['max_drawdown_pct']}%  "
        f"trades={a['total_trades']} WR={a['win_rate_pct']}%",
        f"    per-symbol: {a['per_symbol']}",
        f"    allocator: contested_ticks={al.get('cross_symbol_contested_ticks')} "
        f"lower_ev_skips={al.get('lower_ev_skips')} budget_binds={al.get('budget_binds')} "
        f"max_concurrent={al.get('max_concurrent')}",
        "  ── COMPARISON (ev − baseline) ──",
        f"    net_pnl_delta=${s['comparison']['net_pnl_delta_ev_minus_baseline']:.0f}  "
        f"maxdd_pct_delta={s['comparison']['maxdd_pct_delta_ev_minus_baseline']}  "
        f"ev_beats_baseline={s['comparison']['ev_beats_baseline_net']}",
    ]
    return "\n".join(L)


def _parse_symbols_and_data(args) -> Tuple[List[str], Dict[str, str], Dict[str, List[str]]]:
    symbols = [x.strip().upper() for x in args.symbols.split(",") if x.strip()]
    if not symbols:
        raise SystemExit("--symbols is required (comma list, e.g. BTCUSDT,ETHUSDT)")
    data: Dict[str, str] = {}
    for spec in args.data:  # --data SYM=path (repeatable)
        if "=" not in spec:
            raise SystemExit(f"--data must be SYM=path, got {spec!r}")
        sym, path = spec.split("=", 1)
        data[sym.strip().upper()] = path.strip()
    if args.data_dir:
        for sym in symbols:
            if sym not in data:
                for ext in (f"{sym}_{args.clock_tf}.csv", f"{sym}.csv", f"{sym}_5m.csv"):
                    cand = Path(args.data_dir) / ext
                    if cand.exists():
                        data[sym] = str(cand)
                        break
    missing = [s for s in symbols if s not in data]
    if missing:
        raise SystemExit(f"no data path for symbols {missing} (use --data SYM=path or --data-dir)")
    rosters: Dict[str, List[str]] = {}
    roster_overrides: Dict[str, List[str]] = {}
    for spec in args.roster:  # --roster SYM=a,b,c (repeatable)
        if "=" not in spec:
            raise SystemExit(f"--roster must be SYM=a,b,c, got {spec!r}")
        sym, names = spec.split("=", 1)
        roster_overrides[sym.strip().upper()] = [
            n.strip() for n in names.split(",") if n.strip() in ROSTER]
    for sym in symbols:
        rosters[sym] = roster_overrides.get(sym) or _default_roster_for(sym)
        if not rosters[sym]:
            raise SystemExit(f"{sym}: empty roster (no known strategies)")
    return symbols, data, rosters


def main(argv: List[str]) -> int:
    p = argparse.ArgumentParser(
        description="Cross-symbol portfolio allocator backtest (M18 P(b), Tier-1 research).")
    p.add_argument("--symbols", required=True,
                   help="Comma list, e.g. BTCUSDT,ETHUSDT,SOLUSDT")
    p.add_argument("--data", action="append", default=[], metavar="SYM=path",
                   help="Per-symbol 5m/1m OHLCV CSV/parquet (repeatable). "
                        "e.g. --data BTCUSDT=data/BTCUSDT_5m.csv")
    p.add_argument("--data-dir", default=None,
                   help="Dir to auto-discover <SYM>_<clocktf>.csv / <SYM>.csv per symbol.")
    p.add_argument("--roster", action="append", default=[], metavar="SYM=a,b,c",
                   help="Per-symbol roster override (repeatable). Default: a "
                        "symbol-family roster (BTC/ETH/SOL) or the BTC headline set.")
    p.add_argument("--start", default=None)
    p.add_argument("--end", default=None)
    p.add_argument("--clock-tf", default="15m", choices=list(_PANDAS_TF.keys()))
    p.add_argument("--initial-balance", type=float, default=10_000.0)
    p.add_argument("--risk-pct", type=float, default=0.3,
                   help="Per-trade risk %% of (per-symbol slice / shared) balance.")
    p.add_argument("--daily-loss-pct", type=float, default=3.0)
    p.add_argument("--signal-ttl-bars", type=int, default=1)
    p.add_argument("--fee-bps-roundtrip", type=float, default=DEFAULT_FEE_BPS_ROUNDTRIP)
    p.add_argument("--max-concurrent", type=int, default=2,
                   help="ALLOCATOR arm: max simultaneous open positions across all "
                        "symbols (the shared concurrency cap the selector binds on).")
    p.add_argument("--refresh-signals", action="store_true")
    p.add_argument("--json", dest="json_out", default=None)
    args = p.parse_args(argv[1:])

    symbols, data, rosters = _parse_symbols_and_data(args)
    out = run_multisymbol_backtest(
        symbols=symbols, data=data, rosters=rosters, start=args.start, end=args.end,
        clock_tf=args.clock_tf, initial_balance=args.initial_balance, risk_pct=args.risk_pct,
        daily_loss_pct=args.daily_loss_pct, signal_ttl_bars=args.signal_ttl_bars,
        fee_bps=args.fee_bps_roundtrip, max_concurrent=args.max_concurrent,
        refresh=args.refresh_signals)
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
