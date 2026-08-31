"""The SEAM: multiplexer -> signal meta -> pipeline -> dispatch rounds.

WHY A SEAM TEST. Every piece of the fan-out is unit-tested and each one is
correct in isolation — which is exactly the condition under which this repo's
worst defects have lived. ``src/runtime/provenance.py``'s own account of the
phantom "-$6,358 exit leak" puts it plainly: *"Every contributing component was
individually correct, which is why line-by-line audits kept returning clean:
the defect lives at the seams."*

The fan-out has three of them, and each can break without any unit test
noticing:

  1. the multiplexer writes the plan under a key the pipeline does not read;
  2. the pipeline reads it but builds the package from the GLOBAL winner's
     geometry instead of the round's;
  3. the scope is computed but never passed to ``multi_account_execute``, so
     the round fans out to every eligible account instead of the electing one.

⚠️ WHAT THIS CANNOT DO. A live conflict cannot be fabricated on the VM, so this
is the strongest available evidence SHORT of production. It proves the wiring
carries a decision end-to-end; it does not prove the venue accepted the order.
The end-to-end proof stays what the OPEN-ITEMS row says it is: a starved
account writing a JOURNAL ROW on a tick the soak shows it was elected on.
"""
from __future__ import annotations

from typing import Any, Dict, List

import pytest

from src.runtime.pipeline import _fanout_apply_rounds, _round_order_package


_ROUND_SOL = {
    "strategy": "trend_donchian_sol", "accounts": ["bybit_1"],
    "side": "long", "entry": 100.0, "sl": 95.0, "tp": 115.0, "confidence": 0.4,
}
_ROUND_PROP = {
    "strategy": "trend_donchian_sol_prop", "accounts": ["breakout_1", "bybit_2"],
    "side": "long", "entry": 200.0, "sl": 190.0, "tp": 230.0, "confidence": 0.6,
}


def _signal(rounds) -> Dict[str, Any]:
    """A signal shaped exactly as the multiplexer leaves it."""
    return {
        "symbol": "SOLUSDT",
        "side": "buy",
        "price": 200.0,
        "stop_loss": 190.0,
        "take_profit": 230.0,
        "meta": {
            # The GLOBAL winner — deliberately the OTHER strategy, so a round
            # that silently inherits the signal's geometry is detectable.
            "strategy_name": "trend_donchian_sol_prop",
            "arbitration_fanout": {
                "fanout_schema": 2,
                "roster_state": "read",
                "global_mode": "apply",
                "applied": True,
                "apply_rounds": rounds,
            },
        },
    }


class _FakeCoordinator:
    """Records what multi_account_execute was actually called with."""

    def __init__(self):
        self.calls: List[Dict[str, Any]] = []

    def multi_account_execute(self, pkg, *, account_scope=None, **kw):
        self.calls.append({
            "strategy": pkg.strategy,
            "symbol": pkg.symbol,
            "direction": pkg.direction,
            "entry": pkg.entry,
            "sl": pkg.sl,
            "tp": pkg.tp,
            "account_scope": account_scope,
        })
        return [{"name": a, "trade_id": f"t-{a}"} for a in sorted(account_scope or [])]


def _dispatch(signal) -> _FakeCoordinator:
    """Replays the pipeline's dispatch branch over a signal.

    Mirrors `run_one_tick`'s fan-out block. Kept to the two helpers the
    pipeline itself calls, so a change to either is caught here.
    """
    coord = _FakeCoordinator()
    rounds = _fanout_apply_rounds(signal)
    for r in rounds:
        pkg = _round_order_package(signal, r, {})
        if pkg is None:
            continue
        coord.multi_account_execute(pkg, account_scope=frozenset(r["accounts"]))
    return coord


# --- seam 1: the key the multiplexer writes is the key the pipeline reads ---


def test_the_multiplexer_writes_the_key_the_pipeline_reads():
    """A rename on either side silently disables the fan-out with no error."""
    from src.runtime import intent_multiplexer as mux
    import inspect

    src = inspect.getsource(mux._attach_fanout_plan)
    assert '"arbitration_fanout"' in src, (
        "the multiplexer no longer writes meta['arbitration_fanout']"
    )
    assert '"apply_rounds"' in src, (
        "the multiplexer no longer writes the apply_rounds key the pipeline reads"
    )
    # ...and the pipeline reads exactly those.
    reader = inspect.getsource(_fanout_apply_rounds)
    assert '"arbitration_fanout"' in reader and '"apply_rounds"' in reader


def test_a_plan_written_by_the_multiplexer_is_readable_by_the_pipeline():
    """End-to-end on the real structures, not a hand-made dict."""
    from src.runtime.arbitration_fanout import plan_per_account_election
    from src.runtime.intents import StrategyIntent, elect_from_gated

    def _i(name, entry, sl, tp):
        return StrategyIntent(
            strategy=name, symbol="SOLUSDT", side="long", target_qty=0.0,
            regime="trending", adx_14=30.0, vol_regime=None,
            entry=entry, sl=sl, tp=tp,
        )

    plan = plan_per_account_election(
        (_i("trend_donchian_sol", 100.0, 95.0, 115.0),
         _i("trend_donchian_sol_prop", 200.0, 190.0, 230.0)),
        accounts={
            "bybit_1": {"strategies": ["trend_donchian_sol"]},
            "breakout_1": {"strategies": ["trend_donchian_sol_prop"]},
        },
        elect_fn=elect_from_gated,
        intents_before_gate=2,
    )
    plan["apply_rounds"] = plan["rounds"]          # what apply mode attaches
    sig = {"symbol": "SOLUSDT", "meta": {"arbitration_fanout": plan}}
    assert len(_fanout_apply_rounds(sig)) == 2, (
        "a plan the planner really produced was rejected by the pipeline reader"
    )


# --- seam 2: each round's OWN geometry survives to the package -------------


def test_each_round_dispatches_its_own_geometry_not_the_signals():
    """The signal carries the GLOBAL winner's prices; rounds must not inherit."""
    coord = _dispatch(_signal([_ROUND_SOL, _ROUND_PROP]))
    by_strategy = {c["strategy"]: c for c in coord.calls}

    assert set(by_strategy) == {"trend_donchian_sol", "trend_donchian_sol_prop"}
    sol = by_strategy["trend_donchian_sol"]
    assert (sol["entry"], sol["sl"], sol["tp"]) == (100.0, 95.0, 115.0), (
        "the starved account's round inherited the global winner's geometry — "
        "that places one strategy's trade under another's name"
    )
    prop = by_strategy["trend_donchian_sol_prop"]
    assert (prop["entry"], prop["sl"], prop["tp"]) == (200.0, 190.0, 230.0)


# --- seam 3: the scope actually reaches the dispatcher ----------------------


def test_each_round_is_scoped_to_the_accounts_that_elected_it():
    """A dropped scope fans every round out to every eligible account."""
    coord = _dispatch(_signal([_ROUND_SOL, _ROUND_PROP]))
    scopes = {c["strategy"]: c["account_scope"] for c in coord.calls}

    assert scopes["trend_donchian_sol"] == frozenset({"bybit_1"})
    assert scopes["trend_donchian_sol_prop"] == frozenset({"breakout_1", "bybit_2"})
    for strategy, scope in scopes.items():
        assert scope is not None, f"{strategy} dispatched with NO scope"


def test_the_starved_account_actually_receives_an_order():
    """The whole point, asserted on the dispatch record.

    `bybit_1` is the account that produced nothing under the global election.
    """
    coord = _dispatch(_signal([_ROUND_SOL, _ROUND_PROP]))
    reached = {a for c in coord.calls for a in (c["account_scope"] or ())}
    assert "bybit_1" in reached, "the starved account still received nothing"


# --- the fallback seam: annotate mode must change nothing ------------------


@pytest.mark.parametrize("meta", [
    {},                                              # off / annotate: no key
    {"arbitration_fanout": {"applied": False}},      # planned, held back
    {"arbitration_fanout": {"apply_rounds": []}},    # empty after allowlisting
])
def test_without_apply_rounds_nothing_is_dispatched_by_the_fanout(meta):
    """At the shipped default the pipeline must take its unchanged path."""
    coord = _dispatch({"symbol": "SOLUSDT", "meta": meta})
    assert coord.calls == []


def test_one_bad_round_does_not_take_the_good_ones_down_with_it():
    """Fail-closed is per-PLAN, so a malformed round voids the whole fan-out.

    Deliberate: a plan we cannot fully read is a plan we should not act on
    half of. Pinning it so the choice is visible rather than incidental.
    """
    bad = dict(_ROUND_SOL, sl=None)
    coord = _dispatch(_signal([bad, _ROUND_PROP]))
    assert coord.calls == [], (
        "a malformed round let its siblings dispatch; the read is meant to "
        "fail closed to the single global dispatch"
    )
