"""FLIPPED BY E35: an allowlist can no longer subtract an account from a round.

⚠️ **This file used to pin a DEFECT on purpose** (E33, 2026-09-22), so the fix
would have to flip it consciously. This is that flip. The two assertions that
read ``test_DEFECT_...`` asserted that ``ARBITRATION_FANOUT_ACCOUNTS=bybit_1``
dispatched ``["bybit_1"]`` alone, dropping ``bybit_2`` (real money) and
``bybit_portfolio`` (its CI-enforced mirror) from rounds they had WON. They now
assert the opposite — all three are dispatched, whatever the env says —
because E35 removed the allowlist and made the per-account election the
routing rather than a scoped repair of a global one.

The property, stated once: ``pipeline._dispatch_rounds`` turns every planned
round into a dispatch, scoped to exactly the accounts that elected it. There
is no scoping step between the planner and the dispatcher any more, so the
set of accounts dispatched equals the set of accounts that elected — P1 of the
E34 review (*no subtraction*), by construction.

Replays the live soak row ``2026-09-21T20:30:08.586710+00:00`` / ``ETHUSDT``
(diag-request issue #12708, run 35695727461, read 2026-09-22T06:38:27Z), whose
``rounds_planned`` carried ``[bybit_1, bybit_2, bybit_portfolio]`` and whose
``rounds_written`` carried ``[bybit_1]``.
"""
from __future__ import annotations

import pytest

from src.runtime import intent_multiplexer as mux
from src.runtime import pipeline
from src.runtime.intents import StrategyIntent, elect_from_gated, gate_intents

BYBIT_2_LEG = "trend_donchian_eth_4h"
BYBIT_1_ONLY_LEG = "ict_scalp_eth_15m"

ENV_VALUES = [
    None,                                   # unset
    "bybit_1",                              # the value that subtracted
    "bybit_1,bybit_2,bybit_portfolio",      # the 2026-09-22 widening
]


def _intent(strategy, confidence):
    return StrategyIntent(
        strategy=strategy, symbol="ETHUSDT", side="long", target_qty=0.0,
        entry=2806.17, sl=2717.32142857, tp=3083.98083, confidence=confidence,
        regime="trending", adx_14=30.0, vol_regime=None,
    )


def _dispatched(monkeypatch, candidates, rosters, allow):
    """The accounts the DISPATCHER acts on, driven through the real writer and
    the real reader — no hand-built round anywhere."""
    for k in ("ARBITRATION_FANOUT_MODE", "ARBITRATION_FANOUT_ACCOUNTS"):
        monkeypatch.delenv(k, raising=False)
    if allow is not None:
        monkeypatch.setenv("ARBITRATION_FANOUT_MODE", "apply")
        monkeypatch.setenv("ARBITRATION_FANOUT_ACCOUNTS", allow)
    monkeypatch.setattr(
        mux, "_load_accounts_dict",
        lambda: {a: {"strategies": list(s)} for a, s in rosters.items()},
    )
    gated, pre = gate_intents(candidates, symbol="ETHUSDT")
    desired = elect_from_gated(gated, symbol="ETHUSDT", intents_before_gate=pre)
    signal = mux._desired_to_pipeline_signal(desired, symbol="ETHUSDT", settings={})
    mux._attach_account_election(signal, gated, symbol="ETHUSDT",
                                 intents_before_gate=pre)
    rounds, _ = pipeline._dispatch_rounds(signal)
    return {
        (rsig["meta"]["strategy_name"], tuple(sorted(scope)))
        for rsig, scope in rounds
    }


COELECT = {
    "bybit_1": [BYBIT_2_LEG],
    "bybit_2": [BYBIT_2_LEG],
    "bybit_portfolio": [BYBIT_2_LEG],
}
DIVERGENT = {
    "bybit_1": [BYBIT_1_ONLY_LEG, BYBIT_2_LEG],
    "bybit_2": [BYBIT_2_LEG],
    "bybit_portfolio": [BYBIT_2_LEG],
}


@pytest.mark.parametrize("allow", ENV_VALUES)
def test_FLIPPED_coelecting_accounts_are_all_dispatched_one_package(monkeypatch, allow):
    """Was ``test_DEFECT_partial_allowlist_drops_bybit_2_from_a_round_it_had_won``
    (asserted ``== ["bybit_1"]`` under ``bybit_1``). One package, three accounts,
    under every env value."""
    got = _dispatched(monkeypatch, [_intent(BYBIT_2_LEG, 0.6604)], COELECT, allow)
    assert got == {(BYBIT_2_LEG, ("bybit_1", "bybit_2", "bybit_portfolio"))}


@pytest.mark.parametrize("allow", ENV_VALUES)
def test_FLIPPED_divergent_election_dispatches_both_rounds(monkeypatch, allow):
    """Was ``test_DEFECT_partial_allowlist_drops_the_whole_bybit_2_round``
    (asserted ``== ["bybit_1"]``). bybit_1 elects its own higher-confidence leg;
    the mirror pair still gets the leg it runs — the same round, never split."""
    got = _dispatched(
        monkeypatch,
        [_intent(BYBIT_1_ONLY_LEG, 0.90), _intent(BYBIT_2_LEG, 0.66)],
        DIVERGENT, allow,
    )
    assert got == {
        (BYBIT_1_ONLY_LEG, ("bybit_1",)),
        (BYBIT_2_LEG, ("bybit_2", "bybit_portfolio")),
    }


# ── The roster fact that made the defect total rather than occasional ──────

def test_every_bybit_2_leg_is_also_on_bybit_1():
    """So under the global election one of the two branches above fired on
    EVERY tick ``bybit_2`` had a candidate. Kept: it is why the flip above is
    the whole of bybit_2's routing, not an edge case.

    Reads the roster rather than restating it, so a future roster edit that
    breaks the premise fails here instead of silently invalidating the memo.
    """
    from src.config.accounts_loader import load_accounts_dict

    accounts = load_accounts_dict()
    bybit_1 = set((accounts.get("bybit_1") or {}).get("strategies") or [])
    bybit_2 = set((accounts.get("bybit_2") or {}).get("strategies") or [])
    assert bybit_2, "bybit_2 has no legs — the premise cannot be tested"
    assert bybit_2 <= bybit_1, sorted(bybit_2 - bybit_1)
