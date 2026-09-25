"""Tests for src/runtime/leg_flow_detector.py (E18).

Positive controls for all four never-collapsed states, plus the two live
gotchas this module's own dogfooding on 2026-09-25 surfaced: a non
long/short actionable side token, and prop vs standard "received" sources
staying apart.
"""
from __future__ import annotations

from scripts.ops import leg_flow_detector as lfd


def test_all_four_states_are_distinct_string_values():
    assert len(set(lfd.LEG_STATES)) == 4 == len(lfd.LEG_STATES)


def test_unreadable_when_intents_source_is_none():
    v = lfd.assess_leg(intents=None, received=0)
    assert v["state"] == lfd.LEG_UNREADABLE


def test_unreadable_when_received_source_is_none():
    v = lfd.assess_leg(intents=5, received=None)
    assert v["state"] == lfd.LEG_UNREADABLE


def test_unreadable_never_reads_as_starved_even_though_both_look_like_zero():
    """The collapse this module exists to prevent: `None` (we did not look)
    must never be indistinguishable from `0` (we looked, and it is zero)."""
    unreadable = lfd.assess_leg(intents=None, received=None)
    starved = lfd.assess_leg(intents=1, received=0)
    assert unreadable["state"] != starved["state"]
    assert unreadable["state"] == lfd.LEG_UNREADABLE
    assert starved["state"] == lfd.LEG_STARVED


def test_no_intents_when_the_strategy_produced_nothing():
    v = lfd.assess_leg(intents=0, received=0)
    assert v["state"] == lfd.LEG_NO_INTENTS


def test_no_intents_is_distinct_from_starved():
    assert lfd.assess_leg(intents=0, received=0)["state"] != \
        lfd.assess_leg(intents=1, received=0)["state"]


def test_starved_is_the_breakout_1_shape():
    """21 intents over 22 days, zero tickets — the motivating incident."""
    v = lfd.assess_leg(intents=21, received=0)
    assert v["state"] == lfd.LEG_STARVED


def test_starved_holds_even_when_every_intent_was_held_back():
    """A refused/suppressed leg is still zero RECEIVED — `held_back` is
    context for a reader (BLOCKED vs UNREACHED), never a softening of the
    state itself."""
    v = lfd.assess_leg(intents=3, received=0, held_back=3)
    assert v["state"] == lfd.LEG_STARVED
    assert v["held_back"] == 3


def test_flowing_when_at_least_one_order_was_received():
    v = lfd.assess_leg(intents=4, received=2)
    assert v["state"] == lfd.LEG_FLOWING


def test_count_intents_reads_the_none_sentinel_not_a_hardcoded_vocabulary():
    """MEASURED live 2026-09-25: `trend_donchian_eth_prop` emits side='sell',
    not 'long'/'short'. A detector hardcoding ('long','short') as the
    actionable set would have undercounted this leg's intents to zero."""
    rows = [{"side": "none"}, {"side": "sell"}, {"side": "buy"}, {"side": None}]
    assert lfd.count_intents(rows) == 2


def test_count_intents_none_in_none_out_never_zero():
    assert lfd.count_intents(None) is None


def test_count_intent_episodes_debounces_a_contiguous_run():
    rows = [
        {"side": "none", "logged_at_utc": "2026-01-01T00:00:00Z"},
        {"side": "long", "logged_at_utc": "2026-01-01T00:02:00Z"},
        {"side": "long", "logged_at_utc": "2026-01-01T00:04:00Z"},
        {"side": "none", "logged_at_utc": "2026-01-01T00:06:00Z"},
        {"side": "long", "logged_at_utc": "2026-01-01T00:08:00Z"},
    ]
    assert lfd.count_intent_episodes(rows) == 2


def test_count_intent_episodes_ignores_row_order_in_the_input():
    """The function sorts internally — a caller passing rows in any order
    (e.g. a paginated newest-first API response) must not corrupt the count."""
    rows = [
        {"side": "long", "logged_at_utc": "2026-01-01T00:08:00Z"},
        {"side": "none", "logged_at_utc": "2026-01-01T00:00:00Z"},
        {"side": "long", "logged_at_utc": "2026-01-01T00:02:00Z"},
    ]
    assert lfd.count_intent_episodes(rows) == 1


def test_count_received_standard_splits_placed_from_refused():
    rows = [
        {"account_id": "a", "strategy_name": "s", "status": "open"},
        {"account_id": "a", "strategy_name": "s", "status": "closed"},
        {"account_id": "a", "strategy_name": "s", "status": "rejected"},
        {"account_id": "a", "strategy_name": "s", "status": "exchange_rejected"},
        {"account_id": "b", "strategy_name": "s", "status": "open"},  # other account
        {"account_id": "a", "strategy_name": "other", "status": "open"},  # other strategy
    ]
    placed, refused = lfd.count_received_standard(rows, account_id="a", strategy="s")
    assert (placed, refused) == (2, 2)


def test_count_received_standard_none_in_none_out():
    assert lfd.count_received_standard(None, account_id="a", strategy="s") is None


def test_count_received_prop_splits_received_from_held_back():
    rows = [
        {"strategy": "x", "status": "shadow"},        # never dispatched
        {"strategy": "x", "status": "filled"},         # received
        {"strategy": "x", "status": "suppressed"},     # blocked, not received
        {"strategy": "x", "status": "emitted"},        # received (in flight)
        {"strategy": "y", "status": "filled"},         # other strategy, excluded
    ]
    received, held_back = lfd.count_received_prop(rows, strategy="x")
    assert (received, held_back) == (2, 2)


def test_prop_and_standard_received_vocabularies_do_not_overlap_confusingly():
    """A status meaningful on one side must not silently satisfy the other —
    'open'/'closed' (trades) and 'emitted'/'filled' (prop tickets) name
    genuinely different lifecycles."""
    assert not (set(lfd.PROP_RECEIVED_STATUSES) & {"open"})


def test_enumerate_live_legs_gates_on_both_account_and_strategy():
    config = {
        "accounts": [
            {"id": "live_acct", "yaml_mode": "live", "enabled": True,
             "account_class": "paper",
             "strategies": ["live_strat", "shadow_strat", "unknown_strat"]},
            {"id": "dry_acct", "yaml_mode": "dry_run", "enabled": True,
             "account_class": "real_money", "strategies": ["live_strat"]},
            {"id": "disabled_acct", "yaml_mode": "live", "enabled": False,
             "account_class": "paper", "strategies": ["live_strat"]},
            {"id": "prop_acct", "yaml_mode": "live", "enabled": True,
             "account_class": "prop", "strategies": ["live_strat"]},
        ]
    }
    strategies = {
        "strategies": [
            {"name": "live_strat", "execution": "live", "enabled": True},
            {"name": "shadow_strat", "execution": "shadow", "enabled": True},
        ]
    }
    legs = lfd.enumerate_live_legs(config, strategies)
    keys = {(leg["account_id"], leg["strategy"]) for leg in legs}
    assert ("live_acct", "live_strat") in keys
    assert ("live_acct", "shadow_strat") not in keys  # shadow excluded
    # a strategy absent from strategies.yaml defaults to live (default-permissive)
    assert ("live_acct", "unknown_strat") in keys
    assert ("dry_acct", "live_strat") not in keys      # account not live
    assert ("disabled_acct", "live_strat") not in keys  # account disabled
    assert ("prop_acct", "live_strat") in keys

    prop_leg = next(leg for leg in legs if leg["account_id"] == "prop_acct")
    assert prop_leg["is_prop"] is True
    standard_leg = next(leg for leg in legs if leg["account_id"] == "live_acct"
                         and leg["strategy"] == "live_strat")
    assert standard_leg["is_prop"] is False
