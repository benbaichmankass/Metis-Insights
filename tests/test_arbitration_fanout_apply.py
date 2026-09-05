"""Per-account arbitration fan-out — the APPLY path (Lane P/P4).

THE DEFECT. ``aggregate_intents`` elects ONE winner per **symbol, globally**,
before any account is consulted. ``Coordinator.multi_account_execute`` then
drops every account that does not run *that* strategy
(``_eligible_for_dispatch``: ``pkg.strategy in assigned``). So an account
holding its own candidate on that symbol produces **no order package at all** —
invisible to the journal AND to every per-account detector, because that
account places fine for its other legs.

MEASURED, and none of it from a soak:

  * ``BL-20260827-PROP-ONLY-TWIN-WINS-THE-GLOBAL-SYMBOL-SLOT-AND-STARVES-ITS-PAPER-SIBLING``
    — live audit, SOLUSDT buy-side winners 2026-08-01..08-27, n=60:
    ``trend_donchian_sol`` won ZERO. It is routed to exactly one account
    (``bybit_1``), so "no trades anywhere" is literal: 120 buy signals, no
    journal rows.
  * Declared config, 2026-08-31: **13 of 23** live symbols have >= 2 live
    accounts competing for one global slot, ``bybit_portfolio`` and
    ``alpaca_portfolio`` among them — books whose entire job is to MIRROR the
    live ones.

⚠️ THE GLOBAL ELECTION IS NOT A PORTFOLIO DECISION. Its sort key is
``(target_qty, effective_priority, timestamp, name)`` and ``target_qty`` is
documented-inert in production (always 0.0 —
``BL-20260810-INTENT-TARGET-QTY-ALWAYS-ZERO-TWO-CONSEQUENCES``), so the winner
is decided by priority -> timestamp -> **alphabetical**. It buys no risk
control; each account already has its own RiskManager, balance and caps.
Arbitration is an ACCOUNT-level concern. What stays is arbitration WITHIN an
account: two strategies on one account+symbol under one-way netting genuinely
conflict.
"""
from __future__ import annotations

import textwrap
from typing import Any, Dict
from unittest.mock import patch

import pytest

from src.core.coordinator import Coordinator, OrderPackage
from src.runtime.arbitration_fanout import plan_per_account_election
from src.runtime.intents import StrategyIntent, elect_from_gated


def _intent(strategy: str, side: str = "long", symbol: str = "SOLUSDT",
            entry: float = 100.0, sl: float = 95.0, tp: float = 115.0,
            priority: int | None = None) -> StrategyIntent:
    kw: Dict[str, Any] = dict(
        strategy=strategy, symbol=symbol, side=side, target_qty=0.0,
        regime="trending", adx_14=30.0, vol_regime=None,
        entry=entry, sl=sl, tp=tp,
    )
    if priority is not None:
        kw["priority"] = priority
    return StrategyIntent(**kw)


# The real shape: trend_donchian_sol is bybit_1-only; the prop twin is the
# same 1h Donchian on the same symbol, on a different account.
_ROSTER = {
    "bybit_1": {"strategies": ["trend_donchian_sol", "ict_scalp_sol_5m"]},
    "breakout_1": {"strategies": ["trend_donchian_sol_prop"]},
    "bybit_2": {"strategies": ["trend_donchian_sol_prop"]},
    "ib_paper": {"strategies": ["mes_trend_long_1d"]},   # runs none of them
}


def _elect(cands, **kw):
    return elect_from_gated(cands, **kw)


# --- the defect, and that the plan fixes it --------------------------------


def test_the_starved_account_gets_its_own_winner():
    """bybit_1 must stop being silenced by breakout_1's global win."""
    candidates = (
        _intent("trend_donchian_sol"),
        _intent("trend_donchian_sol_prop"),
    )
    plan = plan_per_account_election(
        candidates, accounts=_ROSTER, elect_fn=_elect, intents_before_gate=2
    )
    assert plan["roster_state"] == "read"
    per = plan["per_account"]
    assert per["bybit_1"]["elected"] == "trend_donchian_sol"
    assert per["breakout_1"]["elected"] == "trend_donchian_sol_prop"
    assert per["bybit_2"]["elected"] == "trend_donchian_sol_prop"
    # An account running none of this tick's candidates is simply absent — it
    # is not a failure and must not be graded as one.
    assert "ib_paper" not in per

    # Accounts electing the SAME strategy share one dispatch round.
    rounds = {r["strategy"]: r["accounts"] for r in plan["rounds"]}
    assert rounds["trend_donchian_sol"] == ["bybit_1"]
    assert rounds["trend_donchian_sol_prop"] == ["breakout_1", "bybit_2"]


def test_within_account_arbitration_is_preserved():
    """Two candidates on ONE account still collapse to one winner.

    This is the half that must NOT change: under one-way netting two
    strategies on one account+symbol genuinely conflict. The fan-out removes
    cross-book suppression only.
    """
    candidates = (
        _intent("trend_donchian_sol", priority=5),
        _intent("ict_scalp_sol_5m", priority=1),
    )
    plan = plan_per_account_election(
        candidates, accounts=_ROSTER, elect_fn=_elect, intents_before_gate=2
    )
    assert plan["per_account"]["bybit_1"]["candidates"] == [
        "trend_donchian_sol", "ict_scalp_sol_5m"
    ]
    # ONE winner for that account, not two rounds.
    assert plan["per_account"]["bybit_1"]["elected"] == "trend_donchian_sol"
    assert len([r for r in plan["rounds"] if "bybit_1" in r["accounts"]]) == 1


# --- the invariants that keep this safe ------------------------------------


def test_never_routes_a_strategy_to_an_account_that_does_not_declare_it():
    """THE safety invariant.

    The defect being fixed is a strategy failing to reach an account that
    declared it. A planner that could route one to an account that did NOT is
    a worse version of the same bug, so the invariant is asserted rather than
    intended.
    """
    candidates = (
        _intent("trend_donchian_sol"),
        _intent("trend_donchian_sol_prop"),
    )
    plan = plan_per_account_election(
        candidates, accounts=_ROSTER, elect_fn=_elect, intents_before_gate=2
    )
    for r in plan["rounds"]:
        for account_id in r["accounts"]:
            declared = _ROSTER[account_id]["strategies"]
            assert r["strategy"] in declared, (
                f"planned {r['strategy']} for {account_id}, which declares {declared}"
            )


def test_a_round_carries_its_own_geometry_not_the_global_winners():
    """Each round's entry/sl/tp must come from ITS strategy.

    Inheriting the global winner's prices would place one strategy's trade
    under another strategy's name — worse than the starvation being fixed.
    """
    candidates = (
        _intent("trend_donchian_sol", entry=100.0, sl=95.0, tp=115.0),
        _intent("trend_donchian_sol_prop", entry=200.0, sl=190.0, tp=230.0),
    )
    plan = plan_per_account_election(
        candidates, accounts=_ROSTER, elect_fn=_elect, intents_before_gate=2
    )
    geo = {r["strategy"]: (r["entry"], r["sl"], r["tp"]) for r in plan["rounds"]}
    assert geo["trend_donchian_sol"] == (100.0, 95.0, 115.0)
    assert geo["trend_donchian_sol_prop"] == (200.0, 190.0, 230.0)


class _Stub:
    """A candidate with a missing leg of geometry.

    Duck-typed rather than a real ``StrategyIntent``, precisely because
    ``StrategyIntent`` validates its own fields — the planner must not rely on
    that, since it accepts whatever its caller hands it.
    """

    def __init__(self, strategy, side="long", entry=100.0, sl=95.0, tp=115.0):
        self.strategy = strategy
        self.symbol = "SOLUSDT"
        self.side = side
        self.entry = entry
        self.sl = sl
        self.tp = tp
        self.confidence = 0.0


@pytest.mark.parametrize("missing", ["entry", "sl", "tp"])
def test_incomplete_geometry_is_dropped_never_defaulted(missing):
    """A fabricated stop is not a stop.

    Each leg is checked separately: a guard that only catches a missing ``sl``
    would still let a target-less round through, and defaulting any of the
    three to 0.0 would place a real order at a price nobody chose.
    """
    bad = _Stub("trend_donchian_sol")
    setattr(bad, missing, None)

    def _elect_stub(cands, **kw):
        class _D:
            side = "long"
            winning_intent = cands[0]
        return _D()

    plan = plan_per_account_election(
        (bad,), accounts=_ROSTER, elect_fn=_elect_stub, intents_before_gate=1
    )
    assert plan["rounds"] == []
    assert plan["per_account"]["bybit_1"]["state"] == "unknown"
    assert plan["per_account"]["bybit_1"]["elected"] is None


def test_unreadable_roster_plans_nothing_and_says_so():
    """*We could not look* is not *no account had candidates*."""
    plan = plan_per_account_election(
        (_intent("trend_donchian_sol"),), accounts=None,
        elect_fn=_elect, intents_before_gate=1,
    )
    assert plan["roster_state"] == "unreadable"
    assert plan["rounds"] == []
    assert plan["accounts_planned"] == 0


def test_states_the_denominator():
    """A count with no denominator is the error this module was rewritten for."""
    candidates = (_intent("trend_donchian_sol"), _intent("trend_donchian_sol_prop"))
    plan = plan_per_account_election(
        candidates, accounts=_ROSTER, elect_fn=_elect, intents_before_gate=2
    )
    assert plan["accounts_planned"] == 3      # bybit_1, breakout_1, bybit_2
    assert plan["accounts_elected"] == 3
    assert plan["accounts_elected"] <= plan["accounts_planned"]


def test_an_election_failure_grades_unknown_and_never_raises():
    """A planner exception must not strand a live tick."""
    def _boom(*a, **k):
        raise RuntimeError("election blew up")

    plan = plan_per_account_election(
        (_intent("trend_donchian_sol"),), accounts=_ROSTER,
        elect_fn=_boom, intents_before_gate=1,
    )
    assert plan["per_account"]["bybit_1"]["state"] == "unknown"
    assert plan["rounds"] == []


# --- the pipeline's fail-closed read ---------------------------------------


def test_apply_rounds_absent_means_no_fanout():
    """At the shipped `annotate` default the key is absent -> unchanged path."""
    from src.runtime.pipeline import _fanout_apply_rounds
    assert _fanout_apply_rounds({"meta": {}}) == []
    assert _fanout_apply_rounds({}) == []
    assert _fanout_apply_rounds({"meta": {"arbitration_fanout": {}}}) == []


@pytest.mark.parametrize("bad_round", [
    {"strategy": "", "accounts": ["bybit_1"], "side": "long",
     "entry": 1.0, "sl": 0.9, "tp": 1.2},
    {"strategy": "s", "accounts": [], "side": "long",
     "entry": 1.0, "sl": 0.9, "tp": 1.2},
    {"strategy": "s", "accounts": ["bybit_1"], "side": "flat",
     "entry": 1.0, "sl": 0.9, "tp": 1.2},
    {"strategy": "s", "accounts": ["bybit_1"], "side": "long",
     "entry": None, "sl": 0.9, "tp": 1.2},
    {"strategy": "s", "accounts": ["bybit_1"], "side": "long",
     "entry": 1.0, "sl": None, "tp": 1.2},
    "not-a-dict",
])
def test_a_malformed_plan_fails_closed_to_the_unchanged_path(bad_round):
    """Fail-CLOSED.

    Losing the fan-out costs a starved account one tick — the state the system
    is already in. Acting on a plan we could not read is a live order on
    unverified routing.
    """
    from src.runtime.pipeline import _fanout_apply_rounds
    signal = {"meta": {"arbitration_fanout": {"apply_rounds": [bad_round]}}}
    assert _fanout_apply_rounds(signal) == []


def test_round_package_uses_the_rounds_geometry_and_strips_the_plan():
    from src.runtime.pipeline import _round_order_package
    signal = {
        "symbol": "SOLUSDT",
        "meta": {
            "strategy_name": "trend_donchian_sol_prop",   # the GLOBAL winner
            "arbitration_fanout": {"apply_rounds": [{"strategy": "x"}]},
        },
    }
    round_ = {
        "strategy": "trend_donchian_sol", "accounts": ["bybit_1"],
        "side": "long", "entry": 100.0, "sl": 95.0, "tp": 115.0,
    }
    pkg = _round_order_package(signal, round_, {})
    assert pkg is not None
    assert pkg.strategy == "trend_donchian_sol"      # NOT the global winner
    assert (pkg.entry, pkg.sl, pkg.tp) == (100.0, 95.0, 115.0)
    assert pkg.direction == "long"
    assert pkg.meta["strategy_name"] == "trend_donchian_sol"
    # The tick's plan is shared context, not this round's decision.
    assert "arbitration_fanout" not in pkg.meta
    assert pkg.meta["arbitration_fanout_round"]["accounts"] == ["bybit_1"]


# --- the order-path scope is a narrowing, never a widening -----------------


_SCOPE_ACCOUNTS_YAML = textwrap.dedent("""\
    accounts:
      bybit_1:
        type: regular
        exchange: bybit
        api_key_env: BYBIT_KEY_1
        strategies: [trend_donchian_sol]
        risk:
          max_dd_pct: 0.05
          daily_usd: 100
          pos_size: 500
          risk_pct: 0.01
          min_balance_usd: 50
      bybit_2:
        type: regular
        exchange: bybit
        api_key_env: BYBIT_KEY_2
        strategies: [vwap]
        risk:
          max_dd_pct: 0.05
          daily_usd: 100
          pos_size: 500
          risk_pct: 0.01
          min_balance_usd: 50
""")


@pytest.fixture()
def _scope_dispatch(tmp_path, monkeypatch):
    """Drive the REAL ``multi_account_execute`` and report which accounts it
    reached, so the scope property is measured rather than read off the source.
    """
    monkeypatch.setenv("TRADE_JOURNAL_DB", str(tmp_path / "trade_journal.db"))
    for var in ("BYBIT_KEY_1", "BYBIT_KEY_2"):
        monkeypatch.setenv(var, "test-value")
        monkeypatch.setenv(f"{var}_SECRET", "test-secret")
    units_yaml = tmp_path / "units.yaml"
    units_yaml.write_text("units: {}\n")
    accounts_yaml = tmp_path / "accounts.yaml"
    accounts_yaml.write_text(_SCOPE_ACCOUNTS_YAML)
    coord = Coordinator(units_path=str(units_yaml))

    def dispatch(scope):
        pkg = OrderPackage(
            strategy="trend_donchian_sol", symbol="SOLUSDT", direction="long",
            entry=100.0, sl=95.0, tp=115.0, confidence=0.7,
            meta={"strategy_name": "trend_donchian_sol"},
        )
        with patch(
            "src.units.accounts.execute.execute_pkg",
            side_effect=lambda p, cfg, **kw: f"dry-{cfg['account_id']}",
        ):
            results = coord.multi_account_execute(
                pkg, accounts_path=str(accounts_yaml), dry_run=True,
                balance_fetcher=lambda _a: 10_000.0,
                account_scope=scope,
            )
        # Every account that survives the eligibility filter produces exactly
        # one per-account result row, whether it placed or refused.
        return {r["name"] for r in results}

    return dispatch


def test_account_scope_can_only_subtract(_scope_dispatch):
    """``account_scope`` is applied LAST, after every existing eligibility rule.

    A caller passing a bad scope loses dispatches; it can never gain an
    unauthorised one.

    ⚠️ MEASURED THROUGH ``multi_account_execute``, NOT READ OFF ITS SOURCE.
    This test used to assert the property by ``inspect.getsource`` +
    ``.split("def _eligible_for_dispatch")``, which pinned the SPELLING of a
    private nested function rather than any behaviour: the MI-129 empty-sizing
    brake renamed it to ``_dispatch_exclusion_reason`` and replaced the
    list-comprehension the second split anchored on, and the test failed with
    ``IndexError`` for a change that altered nothing this test is about. A test
    that reads source text cannot survive a rename by construction, so it
    converts every future refactor into a CI failure with no behavioural
    meaning. The property itself is perfectly observable, which is what the
    four cases below do.

    The roster: ``bybit_1`` declares ``trend_donchian_sol``; ``bybit_2`` does
    not (it declares only ``vwap``), so the declared-strategies rule already
    rejects it BEFORE the scope is ever consulted.
    """
    # 1. No scope at all — the unchanged path. Only the account that declares
    #    the strategy is reached.
    assert _scope_dispatch(None) == {"bybit_1"}

    # 2. A scope naming exactly the eligible account changes nothing.
    assert _scope_dispatch(frozenset({"bybit_1"})) == {"bybit_1"}

    # 3. A scope that omits the eligible account SUBTRACTS it — the narrowing
    #    direction, and the only direction a scope may move the set.
    assert _scope_dispatch(frozenset({"bybit_2"})) == set()

    # 4. THE PROPERTY. A scope naming an account the declared-strategies rule
    #    already rejected does NOT add it back. The scope is a narrowing over
    #    the already-eligible set, never a re-admission.
    assert _scope_dispatch(frozenset({"bybit_1", "bybit_2"})) == {"bybit_1"}

    # 5. And an empty scope admits nobody — it cannot read as "no scope".
    assert _scope_dispatch(frozenset()) == set()
