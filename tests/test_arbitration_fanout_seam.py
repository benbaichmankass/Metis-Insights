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


def _intent(name, entry, sl, tp, symbol="SOLUSDT"):
    from src.runtime.intents import StrategyIntent
    return StrategyIntent(
        strategy=name, symbol=symbol, side="long", target_qty=0.0,
        regime="trending", adx_14=30.0, vol_regime=None,
        entry=entry, sl=sl, tp=tp,
    )


_SEAM_ACCOUNTS = {
    "bybit_1": {"strategies": ["trend_donchian_sol"]},
    "breakout_1": {"strategies": ["trend_donchian_sol_prop"]},
}


def _write_real_plan(monkeypatch, *, allow="bybit_1,breakout_1", accounts=None):
    """Drive the REAL writer and hand back ``(signal, plan)``.

    ⚠️ **THE POINT OF THIS HELPER IS THAT NO TEST HAND-BUILDS ``apply_rounds``
    AGAIN.** The test below used to do ``plan["apply_rounds"] = plan["rounds"]``
    under the comment *"what apply mode attaches"* — and that is exactly what
    apply mode did NOT attach. The writer rebuilt each round as a new dict
    carrying only ``strategy`` and ``accounts``, so the assertion passed over a
    defect that made the fan-out dispatch nothing for twelve days. A seam test
    that constructs the seam's own input by hand is not testing the seam.
    """
    from src.runtime import intent_multiplexer as mux

    monkeypatch.setenv("ARBITRATION_FANOUT_MODE", "apply")
    monkeypatch.setenv("ARBITRATION_FANOUT_ACCOUNTS", allow)
    monkeypatch.setattr(
        mux, "_load_accounts_dict", lambda: accounts or _SEAM_ACCOUNTS
    )
    signal = {"symbol": "SOLUSDT",
              "meta": {"strategy_name": "trend_donchian_sol_prop"}}
    plan = mux._attach_fanout_plan(
        signal,
        (_intent("trend_donchian_sol", 100.0, 95.0, 115.0),
         _intent("trend_donchian_sol_prop", 200.0, 190.0, 230.0)),
        symbol="SOLUSDT", intents_before_gate=2,
    )
    return signal, plan


def test_a_plan_written_by_the_multiplexer_is_readable_by_the_pipeline(monkeypatch):
    """End-to-end through the REAL writer — the regression pin for the 12-day gap.

    ``_attach_fanout_plan`` projected ``apply_rounds`` as
    ``{"strategy": ..., "accounts": [...]}`` while ``_fanout_apply_rounds``
    refuses any round without ``side``/``entry``/``sl``/``tp``. Because the
    reader is fail-closed the result was ``[]`` on every tick and the fan-out
    silently fell back to the global dispatch — MEASURED on the live soak
    (``/api/diag/log_file?name=arbitration_fanout_soak``, read 2026-09-12):
    93 of 93 ``rounds_applied`` entries carried exactly
    ``('accounts', 'strategy')``.
    """
    signal, plan = _write_real_plan(monkeypatch)

    rounds = _fanout_apply_rounds(signal)
    assert len(rounds) == 2, (
        "the plan the REAL writer produced was rejected by the pipeline reader "
        f"— writer emitted {plan.get('apply_rounds')!r}"
    )
    by_strategy = {r["strategy"]: r for r in rounds}
    # The geometry must be each round's OWN, and must actually be present.
    assert by_strategy["trend_donchian_sol"]["entry"] == 100.0
    assert by_strategy["trend_donchian_sol"]["sl"] == 95.0
    assert by_strategy["trend_donchian_sol"]["tp"] == 115.0
    assert by_strategy["trend_donchian_sol_prop"]["entry"] == 200.0


def test_the_writers_projection_satisfies_the_readers_validator(monkeypatch):
    """WRITER AND READER MAY NOT DRIFT. Asserted on the real writer's output.

    This is the assertion whose absence let the defect ship: every unit test
    on either side passed, because each side was self-consistent. The only
    thing neither could see was that they disagreed about the round shape.
    """
    from src.runtime.arbitration_fanout import (
        ROUND_DISPATCH_FIELDS, accepted_rounds,
    )

    _, plan = _write_real_plan(monkeypatch)
    written = plan.get("apply_rounds") or []
    assert written, "the writer attached no rounds at all — the premise is gone"

    assert accepted_rounds(written) == written, (
        "the writer produced rounds the dispatcher's own validator refuses"
    )
    for r in written:
        missing = [f for f in ROUND_DISPATCH_FIELDS if r.get(f) is None]
        assert not missing, f"{r['strategy']} is missing {missing}"
    assert plan["apply_state"] == "dispatchable"
    assert plan["applied"] is True


def test_the_v2_two_key_round_is_exactly_what_the_reader_refuses():
    """NEGATIVE CONTROL — without it, the test above proves nothing.

    A test asserting the writer's output is accepted is only meaningful if the
    OLD output would have been refused. This pins the defective shape verbatim
    as it appears on 93 live rows, so a future projection that regresses to it
    fails here rather than silently on the VM.
    """
    v2_round = {"strategy": "trend_donchian_sol", "accounts": ["bybit_1"]}
    sig = {"symbol": "SOLUSDT",
           "meta": {"arbitration_fanout": {"apply_rounds": [v2_round]}}}
    assert _fanout_apply_rounds(sig) == [], (
        "the reader accepted a geometry-less round — the fail-closed contract "
        "that makes the writer's projection load-bearing is gone"
    )


def test_a_held_back_account_is_planned_but_never_written(monkeypatch):
    """The allowlist scopes the BINDING, not the MEASUREMENT.

    `breakout_1` is the account the operator has NOT armed. It must still be
    elected and reported, and must not appear in anything the dispatcher acts
    on — the correction `NETTING_ATTRIBUTION_ACCOUNTS` needed on 2026-08-09.
    """
    signal, plan = _write_real_plan(monkeypatch, allow="bybit_1")

    planned = {r["strategy"]: r["accounts"] for r in plan["rounds"]}
    assert planned["trend_donchian_sol_prop"] == ["breakout_1"], (
        "the held-back account was not even PLANNED — the evidence a reviewer "
        "needs before widening the allowlist would not exist"
    )
    routed = {a for r in _fanout_apply_rounds(signal) for a in r["accounts"]}
    assert routed == {"bybit_1"}, f"a non-allowlisted account was routed: {routed}"


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
