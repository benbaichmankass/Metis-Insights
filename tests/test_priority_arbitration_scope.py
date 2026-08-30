"""The DEFAULT_PRIORITIES map's per-leg reasoning must match what the
aggregator actually does.

Background (BL-20260830-PRIORITY-IS-MOOT-COMMENTS-REASON-PER-ACCOUNT-WHILE-
ARBITRATION-IS-PER-SYMBOL). Most rows in ``DEFAULT_PRIORITIES`` justified a
value of 0 with some form of "this leg runs ALONE on its (symbol, account),
so priority is moot -- it never arbitrates against another strategy".
``aggregate_intents`` elects ONE winner **per SYMBOL, globally**, before the
per-account fan-out in ``Coordinator.multi_account_execute``; it never sees an
account. So the justification described a scope the aggregator does not use,
and two live legs (``trend_donchian_sol`` / ``trend_donchian_eth``) have been
losing every contest on their symbol as a result.

These tests pin the *measured* picture rather than the prose, so the claim
cannot quietly become false again. They read the real config on purpose: a
fixture would let the config drift away from the assertion, which is the
failure this file exists to prevent.
"""

from __future__ import annotations

import collections

import pytest

from src.runtime.intents import DEFAULT_PRIORITIES

yaml = pytest.importorskip("yaml")


def _enabled_legs_by_symbol() -> dict[str, list[tuple[str, str]]]:
    with open("config/strategies.yaml") as fh:
        strategies = yaml.safe_load(fh)["strategies"]
    by_symbol: dict[str, list[tuple[str, str]]] = collections.defaultdict(list)
    for name, cfg in strategies.items():
        if not isinstance(cfg, dict) or not cfg.get("enabled"):
            continue
        for symbol in cfg.get("symbols") or []:
            by_symbol[symbol].append((name, cfg.get("execution", "live")))
    return dict(by_symbol)


def _accounts_by_strategy() -> dict[str, set[str]]:
    with open("config/accounts.yaml") as fh:
        accounts = yaml.safe_load(fh)["accounts"]
    routed: dict[str, set[str]] = collections.defaultdict(set)
    for account_id, cfg in accounts.items():
        for name in (cfg or {}).get("strategies") or []:
            routed[name].add(account_id)
    return dict(routed)


def _same_side_winner(names: list[str]) -> str:
    """Mirror of ``aggregate_intents``' same-side tiebreak for equal-priority,
    equal-timestamp, unsized (target_qty == 0.0) candidates -- i.e. the live
    path, where the qty key is the constant sentinel."""
    return max(
        names,
        key=lambda n: (
            0.0,
            DEFAULT_PRIORITIES.get(n, 10),
            -0.0,
            tuple(-ord(c) for c in n.lower()),
        ),
    )


def test_symbols_are_contested_by_more_than_one_leg() -> None:
    """The premise the corrected comments rest on."""
    contested = {s: legs for s, legs in _enabled_legs_by_symbol().items() if len(legs) > 1}
    assert contested, "no contested symbol found -- the probe cannot detect a positive"
    # The two symbols the incident was measured on.
    assert len(contested.get("SOLUSDT", [])) > 1
    assert len(contested.get("ETHUSDT", [])) > 1


def test_the_starved_twins_route_to_disjoint_accounts() -> None:
    """This is why raising a priority cannot be the remedy: both legs of the
    pair *should* trade, and the map can only elect one winner per symbol."""
    routed = _accounts_by_strategy()
    for base, twin in (
        ("trend_donchian_sol", "trend_donchian_sol_prop"),
        ("trend_donchian_eth", "trend_donchian_eth_prop"),
    ):
        assert routed.get(base), f"{base} is unrouted -- fixture assumption broken"
        assert routed.get(twin), f"{twin} is unrouted -- fixture assumption broken"
        assert not (routed[base] & routed[twin]), (
            f"{base} and {twin} now share an account; the disjoint-account "
            "argument in DEFAULT_PRIORITIES' header no longer holds and must "
            "be re-derived rather than trusted"
        )


def test_equal_priority_makes_spelling_decide_the_winner() -> None:
    """A strict-prefix name always LOSES the same-side tiebreak. That is an
    accident, not a decision, and it is what starves the two base legs."""
    for base, twin in (
        ("trend_donchian_sol", "trend_donchian_sol_prop"),
        ("trend_donchian_eth", "trend_donchian_eth_prop"),
    ):
        assert DEFAULT_PRIORITIES[base] == DEFAULT_PRIORITIES[twin], (
            "priorities now differ -- the ordering is deliberate; update this "
            "test to assert the intended winner instead of the spelling one"
        )
        assert _same_side_winner([base, twin]) == twin


def test_the_two_base_legs_lose_to_every_rival_on_their_symbol() -> None:
    by_symbol = _enabled_legs_by_symbol()
    for base, symbol in (("trend_donchian_sol", "SOLUSDT"), ("trend_donchian_eth", "ETHUSDT")):
        rivals = [n for n, _ in by_symbol[symbol] if n != base]
        assert rivals, f"no rival for {base} -- the probe cannot detect a positive"
        for rival in rivals:
            assert _same_side_winner([base, rival]) == rival, (
                f"{base} now beats {rival}; the starvation finding has changed "
                "and the DEFAULT_PRIORITIES header must be re-measured"
            )


def test_a_shadow_leg_can_win_a_symbol_and_starve_a_live_leg() -> None:
    """``execution: shadow`` is enforced in ``multi_account_execute``, which
    runs AFTER this election -- so it does not keep a leg out of the contest."""
    by_symbol = _enabled_legs_by_symbol()
    eth = dict(by_symbol["ETHUSDT"])
    assert eth.get("eth_pullback_prop_2h") == "shadow"
    assert eth.get("trend_donchian_eth") == "live"
    assert _same_side_winner(["trend_donchian_eth", "eth_pullback_prop_2h"]) == "eth_pullback_prop_2h"


def test_no_leg_claims_priority_is_moot_because_it_runs_alone() -> None:
    """The specific false justification, kept out of the file by assertion.

    Every legitimate remaining occurrence is a *quotation* of the retracted
    wording inside a CORRECTED note, so it carries a double-quote on its own
    line. A bare assertion of the claim does not.

    An earlier version of this check excluded any line within six lines of a
    ``CORRECTED`` marker. That was vacuous: an unrelated correction elsewhere
    in the block masked an injected bare claim, and the mutation test passed.
    Keying on the quotation is per-line and cannot be masked by a neighbour.
    """
    with open("src/runtime/intents.py") as fh:
        lines = fh.readlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("DEFAULT_PRIORITIES"))
    end = next(i for i, line in enumerate(lines[start:], start) if line.startswith("}"))
    offenders = [
        (i + 1, line.strip())
        for i, line in enumerate(lines[start:end], start)
        if "priority is moot" in line and '"' not in line
    ]
    assert not offenders, f"un-retracted 'priority is moot' claim(s): {offenders}"
