"""A PARTIAL ``ARBITRATION_FANOUT_ACCOUNTS`` SUBTRACTS ACCOUNTS FROM A ROUND.

⚠️ **These tests pin a DEFECT, not an intended behaviour.** They exist so that
the fix (E33) has to flip an assertion consciously, and so the property cannot
regress back in unnoticed. Full measurement and the two proposals:
``docs/research/e33-arbitration-scope-starves-real-money-2026-09-22.md``.

The property, stated once: ``pipeline`` dispatches the fan-out as
``if _rounds: <per-round> else: <global>``, so the fan-out REPLACES the global
dispatch. ``_attach_fanout_plan`` narrows each round through
``scope_round_to_accounts(r, allow)``, which intersects the round's accounts
with the allowlist. An account that is IN a planned round but NOT on the
allowlist is therefore dropped from a dispatch the global path would have
served — the fan-out is narrower than doing nothing.

Replays the live soak row ``2026-09-21T20:30:08.586710+00:00`` / ``ETHUSDT``
(diag-request issue #12708, run 35695727461, read 2026-09-22T06:38:27Z), whose
``rounds_planned`` carried ``[bybit_1, bybit_2, bybit_portfolio]`` and whose
``rounds_written`` carried ``[bybit_1]``.
"""
from __future__ import annotations

import pytest

from src.runtime.arbitration_fanout import (
    accepted_rounds,
    plan_per_account_election,
    scope_round_to_accounts,
)

BYBIT_2_LEG = "trend_donchian_eth_4h"
BYBIT_1_ONLY_LEG = "ict_scalp_eth_15m"

ARMED_FOR_BYBIT_1 = frozenset({"bybit_1"})
ARMED_FOR_ALL = frozenset({"bybit_1", "bybit_2", "bybit_portfolio"})
UNARMED = frozenset()


class _Candidate:
    def __init__(self, strategy, confidence):
        self.strategy = strategy
        self.symbol = "ETHUSDT"
        self.side = "long"
        self.confidence = confidence
        # The geometry `accepted_rounds` fail-closes on.
        self.entry, self.sl, self.tp = 2806.17, 2717.32142857, 3083.98083


class _Elected:
    def __init__(self, candidate):
        self.winning_intent = candidate
        self.side = candidate.side


def _elect(own, **_kwargs):
    """Stands in for ``intents.elect_from_gated``: highest confidence wins."""
    return _Elected(max(own, key=lambda c: c.confidence))


def _dispatched(plan, allow):
    """The accounts the DISPATCHER would act on — or ``None`` for no fan-out.

    ``None`` is the global path (``pipeline``'s ``else:`` branch), which is a
    different outcome from "the fan-out ran and elected nobody" and must not be
    collapsed with it.
    """
    scoped = [
        s for s in (scope_round_to_accounts(r, allow) for r in plan["rounds"])
        if s is not None
    ]
    rounds = accepted_rounds(scoped)
    if not rounds:
        return None
    return sorted({a for r in rounds for a in r["accounts"]})


def _plan(candidates, rosters):
    return plan_per_account_election(
        candidates,
        accounts={a: {"strategies": list(s)} for a, s in rosters.items()},
        elect_fn=_elect,
    )


# ── Branch 1: the accounts co-elect, so they SHARE a round ──────────────────

@pytest.fixture
def coelect_plan():
    return _plan(
        [_Candidate(BYBIT_2_LEG, 0.6604)],
        {
            "bybit_1": [BYBIT_2_LEG],
            "bybit_2": [BYBIT_2_LEG],
            "bybit_portfolio": [BYBIT_2_LEG],
        },
    )


def test_coelecting_accounts_share_one_planned_round(coelect_plan):
    """Matches the live row's ``rounds_planned`` field for field."""
    assert [(r["strategy"], r["accounts"]) for r in coelect_plan["rounds"]] == [
        (BYBIT_2_LEG, ["bybit_1", "bybit_2", "bybit_portfolio"])
    ]
    assert {a: r["state"] for a, r in coelect_plan["per_account"].items()} == {
        "bybit_1": "elected",
        "bybit_2": "elected",
        "bybit_portfolio": "elected",
    }


def test_unarmed_leaves_the_global_path_which_serves_all_three(coelect_plan):
    assert _dispatched(coelect_plan, UNARMED) is None


def test_DEFECT_partial_allowlist_drops_bybit_2_from_a_round_it_had_won(
    coelect_plan,
):
    """⚠️ THE DEFECT. This is today's live configuration.

    ``bybit_2`` is real money and ``bybit_portfolio`` is its CI-enforced paper
    mirror. Both were elected, both were in the planned round, and both are
    absent from what the dispatcher acts on — while the global path they would
    otherwise have taken is suppressed by ``bybit_1``'s surviving round.
    """
    assert _dispatched(coelect_plan, ARMED_FOR_BYBIT_1) == ["bybit_1"]


def test_full_allowlist_serves_all_three(coelect_plan):
    assert _dispatched(coelect_plan, ARMED_FOR_ALL) == [
        "bybit_1",
        "bybit_2",
        "bybit_portfolio",
    ]


# ── Branch 2: bybit_1 elects a leg bybit_2 does not run (26 legs vs 3) ──────

@pytest.fixture
def divergent_plan():
    return _plan(
        [_Candidate(BYBIT_1_ONLY_LEG, 0.90), _Candidate(BYBIT_2_LEG, 0.66)],
        {
            "bybit_1": [BYBIT_1_ONLY_LEG, BYBIT_2_LEG],
            "bybit_2": [BYBIT_2_LEG],
            "bybit_portfolio": [BYBIT_2_LEG],
        },
    )


def test_divergent_election_plans_two_rounds(divergent_plan):
    assert [(r["strategy"], r["accounts"]) for r in divergent_plan["rounds"]] == [
        (BYBIT_1_ONLY_LEG, ["bybit_1"]),
        (BYBIT_2_LEG, ["bybit_2", "bybit_portfolio"]),
    ]


def test_DEFECT_partial_allowlist_drops_the_whole_bybit_2_round(divergent_plan):
    """``scope_round_to_accounts`` returns ``None`` for a round with no
    allowlisted account, so the round vanishes — and ``bybit_1``'s surviving
    round still suppresses the global dispatch."""
    assert _dispatched(divergent_plan, ARMED_FOR_BYBIT_1) == ["bybit_1"]


def test_full_allowlist_dispatches_both_rounds(divergent_plan):
    assert _dispatched(divergent_plan, ARMED_FOR_ALL) == [
        "bybit_1",
        "bybit_2",
        "bybit_portfolio",
    ]


# ── The roster fact that makes the above unavoidable, not occasional ────────

def test_every_bybit_2_leg_is_also_on_bybit_1():
    """So one of the two branches above fires on EVERY tick ``bybit_2`` has a
    candidate — there is no tick on which the live config can serve it.

    Reads the roster rather than restating it, so a future roster edit that
    breaks the premise fails here instead of silently invalidating the memo.
    """
    from src.config.accounts_loader import load_accounts_dict

    accounts = load_accounts_dict()
    bybit_1 = set((accounts.get("bybit_1") or {}).get("strategies") or [])
    bybit_2 = set((accounts.get("bybit_2") or {}).get("strategies") or [])
    assert bybit_2, "bybit_2 has no legs — the premise cannot be tested"
    assert bybit_2 <= bybit_1, sorted(bybit_2 - bybit_1)
