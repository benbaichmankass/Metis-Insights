"""E35 — elect PER ACCOUNT off the once-gated set; the fan-out workaround retired.

Operator directive 2026-09-22: *"we need to fix the fanout mechanism"*. The
global election picked ONE winner per symbol across every account, and the
fan-out repaired the damage after the fact behind an allowlist that, measured,
SUBTRACTED ``bybit_2`` / ``bybit_portfolio`` from 58 rounds each they had won
(E33, E34). Now every account elects from its OWN declared candidates, the
regime gate runs once per tick, and the pipeline dispatches one package per
distinct elected strategy scoped to exactly the accounts that elected it.

What these tests pin, in the order the task asked for them:

1. an account's sole leg wins ON THAT ACCOUNT even when a sibling on another
   account wins the global election;
2. no double-place (E34's open question P1): an account gets at most one
   package per tick, a package never reaches an account that did not elect its
   strategy, one strategy is one package however many accounts elected it —
   and, across ticks, each round is gated on ITS OWN strategy's open package;
3. ``regime_hard_gate`` (and the conviction-arbitration soak) is emitted once
   per tick, not once per account.
"""
from __future__ import annotations

import random
import types
from typing import Any, Dict, List, Optional

import pytest

from src.runtime import intent_multiplexer as mux
from src.runtime import intents as intents_mod
from src.runtime import pipeline
from src.runtime.intents import StrategyIntent, elect_from_gated, gate_intents

SYM = "ETHUSDT"


def _intent(strategy: str, *, side: str = "long", conf: float = 0.5,
            entry: float = 100.0, sl: float = 95.0, tp: float = 115.0,
            entry_time: Optional[str] = None, priority: Optional[int] = None,
            symbol: str = SYM) -> StrategyIntent:
    if side == "short":
        sl, tp = entry + (entry - sl), entry - (tp - entry)
    kw: Dict[str, Any] = dict(
        strategy=strategy, symbol=symbol, side=side, target_qty=0.0,
        entry=entry, sl=sl, tp=tp, confidence=conf, regime="trending",
        adx_14=30.0, vol_regime=None,
        meta={"entry_time": entry_time or f"{strategy}-bar", "timeframe": "1h"},
    )
    if priority is not None:
        kw["priority"] = priority
    return StrategyIntent(**kw)


# The live shape (config/accounts.yaml, 2026-09-25): bybit_1 runs everything,
# bybit_2 and bybit_portfolio are the Stage-2 mirror pair, breakout_1 is prop.
ROSTER = {
    "bybit_1": {"strategies": ["ict_scalp_eth_15m", "trend_donchian_eth",
                               "trend_donchian_eth_4h"]},
    "bybit_2": {"strategies": ["trend_donchian_eth_4h"]},
    "bybit_portfolio": {"strategies": ["trend_donchian_eth_4h"]},
    "breakout_1": {"strategies": ["trend_donchian_eth_prop"]},
    "ib_paper": {"strategies": ["mes_trend_long_1d"]},        # runs none of them
}


@pytest.fixture
def roster(monkeypatch):
    def _set(r=ROSTER):
        monkeypatch.setattr(mux, "_load_accounts_dict", lambda: r)
    _set()
    return _set


def _elect(cands, roster_=None):
    """Gate once + headline election + per-account plan, as the builder does."""
    gated, pre = gate_intents(cands, symbol=SYM)
    desired = elect_from_gated(gated, symbol=SYM, intents_before_gate=pre)
    signal = mux._desired_to_pipeline_signal(desired, symbol=SYM, settings={})
    plan = mux._attach_account_election(signal, gated, symbol=SYM,
                                        intents_before_gate=pre)
    return signal, plan, desired


def _rounds(signal) -> Dict[str, List[str]]:
    return {r["strategy"]: r["accounts"]
            for r in signal[mux.ACCOUNT_ROUNDS_KEY]}


# ── 1. the sole leg on an account wins THERE ────────────────────────────────

def test_sole_leg_on_account_wins_there_even_when_a_sibling_wins_globally(roster):
    """``trend_donchian_eth_4h`` is the ONLY leg ``bybit_2`` runs. Globally it
    loses to ``ict_scalp_eth_15m`` (bybit_1-only, higher confidence), and the
    pre-E35 global path therefore gave ``bybit_2`` nothing. Now it is elected on
    ``bybit_2`` and its mirror, while bybit_1 still gets its own winner."""
    signal, plan, desired = _elect([
        _intent("ict_scalp_eth_15m", conf=0.9),
        _intent("trend_donchian_eth_4h", conf=0.6, entry=200.0, sl=190.0, tp=230.0),
        _intent("trend_donchian_eth_prop", conf=0.7, entry=300.0, sl=290.0, tp=330.0),
    ])
    assert desired.winning_intent.strategy == "ict_scalp_eth_15m"   # the headline
    assert _rounds(signal) == {
        "ict_scalp_eth_15m": ["bybit_1"],
        "trend_donchian_eth_4h": ["bybit_2", "bybit_portfolio"],
        "trend_donchian_eth_prop": ["breakout_1"],
    }
    # The mirror pair is never split: same round, same package (P3).
    r = {x["strategy"]: x for x in signal[mux.ACCOUNT_ROUNDS_KEY]}
    g = r["trend_donchian_eth_4h"]["signal"]
    # ...and it carries ITS OWN geometry and meta, not the headline's.
    assert (g["entry_price"], g["stop_loss"], g["take_profit"]) == (200.0, 190.0, 230.0)
    assert g["meta"]["strategy_name"] == "trend_donchian_eth_4h"
    assert g["meta"]["entry_time"] == "trend_donchian_eth_4h-bar"
    assert signal["meta"]["account_election"]["per_account"]["bybit_2"] == {
        "state": "elected", "elected": "trend_donchian_eth_4h"}


def test_the_global_winners_account_gets_the_identical_signal(roster):
    """No regression for the account that already won: its round signal is
    exactly what the pre-E35 global path dispatched."""
    signal, _, _ = _elect([_intent("ict_scalp_eth_15m", conf=0.9),
                           _intent("trend_donchian_eth_4h", conf=0.6)])
    head = {k: v for k, v in signal.items() if k != mux.ACCOUNT_ROUNDS_KEY}
    rnd = next(r for r in signal[mux.ACCOUNT_ROUNDS_KEY]
               if r["strategy"] == "ict_scalp_eth_15m")["signal"]
    head_meta = {k: v for k, v in head["meta"].items() if k != "account_election"}
    assert {k: v for k, v in rnd.items() if k != "meta"} == {
        k: v for k, v in head.items() if k != "meta"}
    # `aggregation` differs by design (bybit_1 elected over its own subset);
    # every other meta key is the same.
    strip = ("aggregation", "contributing_strategies", "aggregation_reason")
    assert {k: v for k, v in rnd["meta"].items() if k not in strip} == {
        k: v for k, v in head_meta.items() if k not in strip}


def test_prefix_named_leg_is_no_longer_starved_by_its_prop_twin(roster):
    """BL-20260827: ``trend_donchian_eth`` vs ``trend_donchian_eth_prop`` — the
    same Donchian on two accounts; one always lost the global slot."""
    signal, _, _ = _elect([_intent("trend_donchian_eth", conf=0.5),
                           _intent("trend_donchian_eth_prop", conf=0.8)])
    assert _rounds(signal) == {"trend_donchian_eth": ["bybit_1"],
                               "trend_donchian_eth_prop": ["breakout_1"]}


# ── 3. gate ONCE per tick ───────────────────────────────────────────────────

def test_regime_hard_gate_runs_once_per_tick_not_per_account(roster, monkeypatch):
    """Four accounts elect; the gate (and its audit row) runs ONCE. And the
    candidate it removes reaches no account — the per-account elections run
    off the gated set, not the raw one."""
    calls: List[tuple] = []

    def _gate(cands):
        calls.append(tuple(c.strategy for c in cands))
        return tuple(c for c in cands if c.strategy != "trend_donchian_eth_prop")

    monkeypatch.setattr(intents_mod, "_regime_router_active", lambda: True)
    monkeypatch.setattr(intents_mod, "_hard_regime_gate", _gate)
    monkeypatch.setattr(mux, "_collect_intents", lambda *a, **k: [
        _intent("ict_scalp_eth_15m", conf=0.9),
        _intent("trend_donchian_eth_4h", conf=0.6),
        _intent("trend_donchian_eth_prop", conf=0.99),
    ])
    monkeypatch.setattr(mux, "_debounce_emissions", lambda i, **k: i)
    import src.runtime.arbitration_fanout_soak as soak
    monkeypatch.setattr(soak, "record", lambda *a, **k: None)
    signal = mux.multiplexed_intent_signal_builder(
        {"SYMBOL": SYM}, builders={}, strategies=[])
    assert len(calls) == 1, f"gate ran {len(calls)}x for one tick: {calls}"
    assert _rounds(signal) == {"ict_scalp_eth_15m": ["bybit_1"],
                               "trend_donchian_eth_4h": ["bybit_2", "bybit_portfolio"]}
    assert signal["meta"]["account_election"]["per_account"].get("breakout_1") is None


def test_conviction_arbitration_row_once_per_tick_not_per_account(roster, monkeypatch):
    """``elect_from_gated`` was documented "pure" and wrote a conviction-soak
    row on every call — N accounts, N rows for one decision. Per-account
    elections now pass ``annotate=False``."""
    seen: List[str] = []
    monkeypatch.setattr(intents_mod, "annotate_conviction_arbitration",
                        lambda *a, **k: seen.append(k.get("actual_winner_strategy")))
    _elect([_intent("ict_scalp_eth_15m", conf=0.9),
            _intent("trend_donchian_eth_4h", conf=0.6),
            _intent("trend_donchian_eth_prop", conf=0.7)])
    assert seen == ["ict_scalp_eth_15m"]


# ── the absent / empty / present distinction ────────────────────────────────

def test_flat_headline_plans_nothing_and_says_so(roster):
    signal, plan, _ = _elect([_intent("trend_donchian_eth_4h", side="flat")])
    assert plan is None
    assert mux.ACCOUNT_ROUNDS_KEY not in signal
    assert signal["meta"]["account_election"] == {"state": "headline_flat"}


def test_unreadable_roster_is_unavailable_and_falls_back_loudly(roster, caplog):
    roster(None)
    with caplog.at_level("ERROR"):
        signal, plan, _ = _elect([_intent("trend_donchian_eth_4h")])
    assert mux.ACCOUNT_ROUNDS_KEY not in signal        # absent = could not look
    assert signal["meta"]["account_election"]["state"] == "unavailable"
    assert any("GLOBAL-winner dispatch" in r.getMessage() for r in caplog.records)
    rounds, outcomes = pipeline._dispatch_rounds(signal)
    assert rounds == [(signal, None)] and outcomes == []


def test_the_key_the_multiplexer_writes_is_the_key_the_pipeline_reads():
    assert pipeline.ACCOUNT_ROUNDS_KEY == mux.ACCOUNT_ROUNDS_KEY


def test_retired_allowlist_env_changes_nothing(roster, monkeypatch):
    """The partial allowlist that subtracted real money is gone, not defaulted."""
    cands = [_intent("ict_scalp_eth_15m", conf=0.9), _intent("trend_donchian_eth_4h")]
    base, _, _ = _elect(cands)
    monkeypatch.setenv("ARBITRATION_FANOUT_MODE", "annotate")
    monkeypatch.setenv("ARBITRATION_FANOUT_ACCOUNTS", "bybit_1")
    armed, _, _ = _elect(cands)
    assert _rounds(armed) == _rounds(base)
    assert _rounds(armed)["trend_donchian_eth_4h"] == ["bybit_2", "bybit_portfolio"]


# ── 2. NO DOUBLE-PLACE — a property, over random rosters and candidates ─────

class _VenueCoordinator:
    """Applies the REAL eligibility rules that matter here (the account must
    declare the strategy; ``account_scope`` narrows) and records every
    (account, strategy) placement."""

    def __init__(self, roster_: Dict[str, Dict[str, Any]]):
        self.roster = roster_
        self.placed: List[tuple] = []
        self.calls: List[Dict[str, Any]] = []

    def multi_account_execute(self, pkg, account_scope=None, **_kw):
        self.calls.append({"strategy": pkg.strategy, "scope": account_scope,
                           "entry": pkg.entry, "meta": dict(pkg.meta or {})})
        pkg.meta = dict(pkg.meta or {})
        pkg.meta["order_package_id"] = f"op-{len(self.calls)}"
        out = []
        for acct, cfg in self.roster.items():
            if pkg.strategy not in (cfg.get("strategies") or []):
                continue
            if account_scope is not None and acct not in account_scope:
                continue
            self.placed.append((acct, pkg.strategy))
            out.append({"name": acct, "trade_id": f"{acct}-{pkg.strategy}"})
        return out


def _random_case(rng: random.Random):
    pool = [f"s{i}" for i in range(6)]
    roster_ = {
        f"acct_{a}": {"strategies": rng.sample(pool, rng.randint(1, 4))}
        for a in range(rng.randint(1, 6))
    }
    cands = [
        _intent(s, side=rng.choice(["long", "long", "short", "flat"]),
                conf=round(rng.random(), 3),
                priority=rng.choice([None, 0, 1, 5]))
        for s in rng.sample(pool, rng.randint(1, 6))
    ]
    return roster_, cands


@pytest.mark.parametrize("seed", range(300))
def test_no_double_place_property(seed, roster, monkeypatch):
    """E34's P1, over 300 random rosters × candidate sets (fixed seeds):

    * every account appears in AT MOST ONE round, so at most one package per
      account per tick;
    * every placement is of a strategy the account declares AND elected;
    * one strategy is ONE package per tick however many accounts elected it
      (two accounts never mint two packages for what is one decision);
    * NO SUBTRACTION vs the old routing: every account that held the global
      winner is still routed, and every account holding a non-flat candidate is
      routed something (P1 as E34 stated it).
    """
    rng = random.Random(seed)
    roster_, cands = _random_case(rng)
    roster(roster_)
    signal, plan, desired = _elect(cands)
    if signal.get("side") not in ("buy", "sell"):
        assert all(c.side == "flat" for c in cands)
        return
    rounds = signal[mux.ACCOUNT_ROUNDS_KEY]
    in_rounds = [a for r in rounds for a in r["accounts"]]
    assert len(in_rounds) == len(set(in_rounds)), rounds
    strategies = [r["strategy"] for r in rounds]
    assert len(strategies) == len(set(strategies)), rounds

    coord = _VenueCoordinator(roster_)
    dispatch, outcomes = pipeline._dispatch_rounds(signal)
    assert outcomes == []
    for rsig, scope in dispatch:
        coord.multi_account_execute(
            pipeline._signal_to_order_package(rsig, {}), account_scope=scope)
    accts = [a for a, _ in coord.placed]
    assert len(accts) == len(set(accts)), f"double-place: {coord.placed}"
    elected = {a: c["elected"] for a, c in plan["per_account"].items()}
    for acct, strat in coord.placed:
        assert strat in roster_[acct]["strategies"]
        assert elected[acct] == strat
    assert len(coord.calls) == len({c["strategy"] for c in coord.calls})

    winner = desired.winning_intent.strategy
    holders = {a for a, c in roster_.items() if winner in c["strategies"]}
    assert holders <= set(accts), "an account holding the global winner was dropped"
    non_flat = {c.strategy for c in cands if c.side != "flat"}
    wanting = {a for a, c in roster_.items() if non_flat & set(c["strategies"])}
    assert wanting == set(accts), (wanting, accts)


def test_a_plan_naming_an_account_twice_is_refused_outright():
    """Unreachable by construction; asserted anyway at the dispatcher."""
    sig = {"strategy": "x", "side": "buy", "entry_price": 1.0, "stop_loss": 0.9,
           "take_profit": 1.2, "meta": {"strategy_name": "x"}}
    signal = {"meta": {}, pipeline.ACCOUNT_ROUNDS_KEY: [
        {"strategy": "a", "accounts": ["bybit_1"], "signal": sig},
        {"strategy": "b", "accounts": ["bybit_1", "bybit_2"], "signal": sig},
    ]}
    rounds, outcomes = pipeline._dispatch_rounds(signal)
    assert rounds == []
    assert outcomes == [{"status": "refused", "reason": "double_round_accounts",
                         "accounts": ["bybit_1"]}]


def test_a_malformed_round_is_dropped_alone():
    """The retired reader voided the WHOLE plan on one bad round (E34 § 2)."""
    good = {"side": "buy", "entry_price": 1.0, "stop_loss": 0.9,
            "take_profit": 1.2, "meta": {"strategy_name": "a"}}
    bad = {"side": "buy", "entry_price": 1.0, "stop_loss": None,
           "take_profit": 1.2, "meta": {"strategy_name": "b"}}
    signal = {"meta": {"news": {"decision": "ok"}}, pipeline.ACCOUNT_ROUNDS_KEY: [
        {"strategy": "a", "accounts": ["bybit_1"], "signal": good},
        {"strategy": "b", "accounts": ["bybit_2"], "signal": bad},
    ]}
    rounds, outcomes = pipeline._dispatch_rounds(signal)
    assert [(r[0]["meta"]["strategy_name"], r[1]) for r in rounds] == [
        ("a", frozenset({"bybit_1"}))]
    # Tick-level context rides into every round's package.
    assert rounds[0][0]["meta"]["news"] == {"decision": "ok"}
    assert outcomes[0]["reason"] == "malformed_round"
    assert outcomes[0]["accounts"] == ["bybit_2"]


# ── the pipeline end to end: per-ROUND gates ────────────────────────────────

@pytest.fixture
def run(monkeypatch, roster):
    """``run_pipeline`` with the real dispatch branch and a recording venue."""
    for name in ("send_to_operator", "write_status", "write_signal",
                 "_write_ict_signals_from_meta", "log_signal", "report"):
        monkeypatch.setattr(pipeline, name, lambda *a, **k: None)
    monkeypatch.setattr(pipeline, "get_news_score", lambda *a, **k: types.SimpleNamespace(
        veto=False, decision="ok", adjustment=0.0, item_count=0, reason="ok"))
    monkeypatch.setattr(pipeline, "news_is_active", lambda *a, **k: False)
    monkeypatch.setattr(pipeline, "event_risk_for_symbol", lambda s: (0.0, None))
    monkeypatch.setattr(pipeline, "inject_runtime_counters", lambda s, c: s)
    monkeypatch.setattr(pipeline, "inject_per_strategy_counters", lambda s, n: s)
    monkeypatch.setenv("MULTI_ACCOUNT_DISPATCH", "true")
    monkeypatch.delenv("CENTRALIZED_ALLOCATOR", raising=False)
    monkeypatch.setattr(pipeline, "HALT_FLAG_PATH", "/nonexistent/halt.flag")
    gates = {"open": set(), "same_bar": set()}
    monkeypatch.setattr(pipeline, "_has_open_package_for_strategy",
                        lambda s, symbol=None: f"open-{s}" if s in gates["open"] else None)
    monkeypatch.setattr(pipeline, "_same_bar_entry_for_strategy",
                        lambda s, symbol=None: ({"bar_seconds": 900, "order_package_id": "p",
                                                 "last_created_at": "t"}
                                                if s in gates["same_bar"] else None))
    monkeypatch.setattr(pipeline, "_recent_refusal_for_strategy", lambda *a, **k: None)
    monkeypatch.setattr(pipeline, "_empty_sizing_refusal_for_signal", lambda *a, **k: None)
    venue = _VenueCoordinator(ROSTER)
    import src.core.coordinator as coord_mod
    monkeypatch.setattr(coord_mod, "Coordinator", lambda: venue)

    def _run(signal):
        out = pipeline.run_pipeline({"SYMBOL": SYM}, signal_builder=lambda s: signal)
        # A gate skip returns the raw result (pre-existing early-return shape,
        # kept byte-for-byte); every other path returns {signal, order_result}.
        return out.get("order_result", out), venue
    _run.gates = gates
    return _run


def _tick(roster_fixture=None):
    return _elect([
        _intent("ict_scalp_eth_15m", conf=0.9, entry_time="15:00"),
        _intent("trend_donchian_eth_4h", conf=0.6, entry=200.0, sl=190.0, tp=230.0,
                entry_time="12:00"),
    ])[0]


def test_every_elected_account_gets_exactly_one_package(run):
    result, venue = run(_tick())
    assert result["status"] == "multi_account_dispatched"
    assert sorted(venue.placed) == [
        ("bybit_1", "ict_scalp_eth_15m"),
        ("bybit_2", "trend_donchian_eth_4h"),
        ("bybit_portfolio", "trend_donchian_eth_4h"),
    ]
    by = {c["strategy"]: c for c in venue.calls}
    assert by["trend_donchian_eth_4h"]["scope"] == frozenset({"bybit_2", "bybit_portfolio"})
    assert by["trend_donchian_eth_4h"]["entry"] == 200.0
    # Its OWN entry_time — the brake/debounce identity — not the headline's.
    assert by["trend_donchian_eth_4h"]["meta"]["entry_time"] == "12:00"
    assert [o["status"] for o in result["round_outcomes"]] == ["dispatched", "dispatched"]


def test_the_global_winners_gate_no_longer_drops_another_accounts_round(run):
    """The XRPUSDT 2026-09-24T15:09Z shape: the headline strategy had already
    acted this bar, and the pre-E35 pipeline skipped the WHOLE tick — taking
    bybit_2 + bybit_portfolio's own winner with it. (Live, that round also
    held its own open package, so no order was lost that tick; here it does
    not, which is the case the old code got wrong.)"""
    run.gates["same_bar"].add("ict_scalp_eth_15m")
    result, venue = run(_tick())
    assert result["status"] == "multi_account_dispatched"
    assert sorted(venue.placed) == [("bybit_2", "trend_donchian_eth_4h"),
                                    ("bybit_portfolio", "trend_donchian_eth_4h")]
    skipped = [o for o in result["round_outcomes"] if o["status"] == "skipped"]
    assert skipped == [{"strategy": "ict_scalp_eth_15m", "accounts": ["bybit_1"],
                        "status": "skipped", "reason": "same_bar_reentry_debounce"}]


def test_a_rounds_own_open_package_gates_it_cross_tick_double_place(run):
    """The cross-tick half of P1: ``trend_donchian_eth_4h`` already has an open
    package. Pre-E35 the gate checked only the headline, so this round would
    have dispatched a SECOND package for it. Now it is gated on its own."""
    run.gates["open"].add("trend_donchian_eth_4h")
    result, venue = run(_tick())
    assert venue.placed == [("bybit_1", "ict_scalp_eth_15m")]
    assert {o["strategy"]: o["reason"] for o in result["round_outcomes"]
            if o["status"] == "skipped"} == {"trend_donchian_eth_4h": "open_package_exists"}


def test_every_round_gated_is_skipped_not_dispatched(run):
    run.gates["open"].update({"ict_scalp_eth_15m", "trend_donchian_eth_4h"})
    result, venue = run(_tick())
    assert venue.calls == []
    assert result["status"] == "skipped" and result["reason"] == "all_rounds_gated"


def test_one_round_gated_is_exactly_the_old_early_return(run):
    run.gates["open"].add("ict_scalp_eth_15m")
    signal = _elect([_intent("ict_scalp_eth_15m", conf=0.9)])[0]
    result, venue = run(signal)
    assert venue.calls == []
    assert result["status"] == "skipped"
    assert result["reason"] == "open_package_exists"
    assert result["open_package_id"] == "open-ict_scalp_eth_15m"


def test_a_signal_without_rounds_is_one_unscoped_dispatch(run):
    """Legacy builders (and an unavailable election) take the old path."""
    signal = {"symbol": SYM, "side": "buy", "entry_price": 100.0,
              "stop_loss": 95.0, "take_profit": 115.0,
              "meta": {"strategy_name": "trend_donchian_eth_4h"}}
    result, venue = run(signal)
    assert result["status"] == "multi_account_dispatched"
    assert [c["scope"] for c in venue.calls] == [None]
    assert sorted(venue.placed) == [("bybit_1", "trend_donchian_eth_4h"),
                                    ("bybit_2", "trend_donchian_eth_4h"),
                                    ("bybit_portfolio", "trend_donchian_eth_4h")]


def test_no_account_elected_is_reported_not_silent(run):
    signal = {"symbol": SYM, "side": "buy", "entry_price": 100.0,
              "stop_loss": 95.0, "take_profit": 115.0,
              "meta": {"strategy_name": "ghost"}, pipeline.ACCOUNT_ROUNDS_KEY: []}
    result, venue = run(signal)
    assert venue.calls == []
    assert result["status"] == "skipped" and result["reason"] == "no_account_elected"
