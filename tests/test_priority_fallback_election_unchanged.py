"""Mapping the five unmapped legs + lowering the fallback changes NO election.

Operator, 2026-09-28 ("Fix both + wire guard"): *"The priority change is
checked first so it doesn't alter today's real trade choices."*

THE CHANGE. ``gdx_pullback_1d``, ``iaum_pullback_1d``, ``scha_trend_long_1d``,
``slv_pullback_1d`` and ``splg_trend_long_1d`` were absent from
``DEFAULT_PRIORITIES`` and resolved to ``_UNKNOWN_STRATEGY_PRIORITY`` = 10. They
now carry explicit rows and the fallback is -1.

THE PROOF. ``aggregate_intents`` elects one winner per SYMBOL across every
enabled leg (any ``execution``, since shadow legs are elected too), and
priority enters only as the declared-priority term. So the election is
unchanged iff, for every pair of enabled legs sharing a symbol, the SIGN of
their priority difference is unchanged. Population: every enabled leg in
``config/strategies.yaml`` at test time, grouped by its declared symbol(s).
"""
from __future__ import annotations

import collections
import pathlib
from typing import Any, Dict

import pytest
import yaml

from src.runtime import intents as I
from src.runtime.intents import StrategyIntent, elect_from_gated

REPO = pathlib.Path(__file__).resolve().parents[1]

#: The rows this change added, and the fallback every one of them resolved to.
ADDED = ("gdx_pullback_1d", "iaum_pullback_1d", "scha_trend_long_1d",
         "slv_pullback_1d", "splg_trend_long_1d")
OLD_FALLBACK = 10


def _old_priority(name: str) -> int:
    if name in ADDED:
        return OLD_FALLBACK
    return I.DEFAULT_PRIORITIES.get(name, OLD_FALLBACK)


def _new_priority(name: str) -> int:
    return I.DEFAULT_PRIORITIES.get(name, I._UNKNOWN_STRATEGY_PRIORITY)


def _enabled_by_symbol() -> Dict[str, list]:
    cfg = yaml.safe_load((REPO / "config" / "strategies.yaml").read_text())
    strats = cfg.get("strategies", cfg)
    by: Dict[str, list] = collections.defaultdict(list)
    for name, c in strats.items():
        if not isinstance(c, dict) or not c.get("enabled"):
            continue
        sym = c.get("symbol") or c.get("symbols")
        for s in (sym if isinstance(sym, list) else [sym]):
            if s:
                by[str(s).upper()].append(name)
    return by


def _sign(x: int) -> int:
    return (x > 0) - (x < 0)


def test_every_added_leg_is_now_mapped():
    """Each added leg has an explicit row, so none can reach the fallback."""
    for name in ADDED:
        assert name in I.DEFAULT_PRIORITIES


def test_no_pairwise_ordering_changes_on_any_contested_symbol():
    by = _enabled_by_symbol()
    assert by, "roster unreadable -- an empty population proves nothing"
    compared = 0
    flipped = []
    for sym, legs in sorted(by.items()):
        for i, a in enumerate(legs):
            for b in legs[i + 1:]:
                compared += 1
                before = _sign(_old_priority(a) - _old_priority(b))
                after = _sign(_new_priority(a) - _new_priority(b))
                if before != after:
                    flipped.append((sym, a, b, before, after))
    assert compared > 0, "no contested symbol found -- the probe must see a positive"
    assert flipped == []


def test_the_one_contested_added_leg_is_slv_and_it_still_beats_its_rival():
    """SLV is the only symbol where an added leg has a rival (measured
    2026-09-28). If the roster grows, this names the new pair instead of
    silently passing."""
    by = _enabled_by_symbol()
    contested = {sym: legs for sym, legs in by.items()
                 if len(legs) > 1 and any(n in ADDED for n in legs)}
    assert set(contested) == {"SLV"}
    assert sorted(contested["SLV"]) == ["slv_pullback_1d", "slv_trend_1h"]


def _intent(strategy: str) -> StrategyIntent:
    kw: Dict[str, Any] = dict(
        strategy=strategy, symbol="SLV", side="long", target_qty=0.0,
        regime="trending", adx_14=30.0, vol_regime=None,
        entry=100.0, sl=95.0, tp=115.0, timestamp=1000.0,
    )
    return StrategyIntent(**kw)


@pytest.mark.parametrize("order", [("slv_pullback_1d", "slv_trend_1h"),
                                   ("slv_trend_1h", "slv_pullback_1d")])
def test_live_slv_leg_still_wins_the_tie_against_the_shadow_leg(order):
    """Through the real election: at equal confidence the live, real-money
    ``slv_pullback_1d`` must still beat the shadow ``slv_trend_1h`` on
    declared priority, as it did at the old fallback of 10. Setting it to the
    ETF-scheme 0 would fail this."""
    cands = tuple(_intent(n) for n in order)
    desired = elect_from_gated(cands, symbol="SLV", intents_before_gate=len(cands))
    assert desired.winning_intent.strategy == "slv_pullback_1d"


def test_an_unmapped_leg_now_loses_the_priority_term_to_every_mapped_leg():
    assert I._UNKNOWN_STRATEGY_PRIORITY < min(I.DEFAULT_PRIORITIES.values())
    assert _intent("some_new_unmapped_leg").effective_priority() == -1
