"""M28 P2 — tests for the event-outcome resolver (the non-price decision DSL)."""

from __future__ import annotations

from src.units.strategies.macro_thesis.event_resolver import (
    eval_predicate,
    resolve_action,
    resolve_event_for_theses,
)


# --------------------------------------------------------------------------
# eval_predicate — the safe DSL
# --------------------------------------------------------------------------

def test_predicate_numeric_ops():
    o = {"surprise": 0.3, "actual": 3.1, "consensus": 3.0}
    assert eval_predicate({"field": "surprise", "op": "gt", "value": 0}, o) is True
    assert eval_predicate({"field": "surprise", "op": "lt", "value": 0}, o) is False
    assert eval_predicate({"field": "actual", "op": "gte", "value": 3.1}, o) is True
    assert eval_predicate({"field": "consensus", "op": "lte", "value": 3.0}, o) is True


def test_predicate_eq_ne_in():
    o = {"direction": "hawkish", "kind": "fomc"}
    assert eval_predicate({"field": "direction", "op": "eq", "value": "hawkish"}, o) is True
    assert eval_predicate({"field": "direction", "op": "ne", "value": "dovish"}, o) is True
    assert eval_predicate({"field": "kind", "op": "in", "value": ["fomc", "cpi"]}, o) is True
    assert eval_predicate({"field": "kind", "op": "not_in", "value": ["nfp"]}, o) is True


def test_predicate_fail_closed():
    o = {"surprise": 0.3}
    assert eval_predicate({"field": "missing", "op": "gt", "value": 0}, o) is False   # field absent
    assert eval_predicate({"field": "surprise", "op": "bogus", "value": 0}, o) is False  # unknown op
    assert eval_predicate({}, o) is False                                            # malformed
    assert eval_predicate({"field": "surprise", "op": "gt", "value": "notnum"}, o) is False  # bad value
    assert eval_predicate("notadict", o) is False  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# resolve_action — first match wins, unknown actions skipped
# --------------------------------------------------------------------------

def test_resolve_action_first_match():
    rules = [
        {"if": {"field": "surprise", "op": "gt", "value": 0.5}, "action": "add"},
        {"if": {"field": "surprise", "op": "gt", "value": 0.0}, "action": "hold"},
    ]
    # surprise 0.3 fails the first (>0.5), matches the second (>0)
    assert resolve_action(rules, {"surprise": 0.3})["action"] == "hold"
    # surprise 0.8 matches the first
    assert resolve_action(rules, {"surprise": 0.8})["action"] == "add"


def test_resolve_action_no_match_is_none():
    rules = [{"if": {"field": "surprise", "op": "gt", "value": 0}, "action": "add"}]
    assert resolve_action(rules, {"surprise": -0.1}) is None
    assert resolve_action([], {"surprise": 1}) is None


def test_resolve_action_skips_invalid_action():
    rules = [
        {"if": {"field": "x", "op": "eq", "value": 1}, "action": "launch_missiles"},  # not valid
        {"if": {"field": "x", "op": "eq", "value": 1}, "action": "exit"},
    ]
    assert resolve_action(rules, {"x": 1})["action"] == "exit"


def test_resolve_action_returns_matched_rule():
    rules = [{"if": {"field": "direction", "op": "eq", "value": "hawkish"}, "action": "trim"}]
    res = resolve_action(rules, {"direction": "hawkish"})
    assert res["action"] == "trim"
    assert res["matched_rule"]["action"] == "trim"


# --------------------------------------------------------------------------
# resolve_event_for_theses
# --------------------------------------------------------------------------

def test_resolve_event_matches_linked_theses():
    event = {"event_id": "evt-fomc-1", "realized_outcome": {"direction": "hawkish", "surprise": 0.4}}
    links = [
        {"thesis_id": "mth-A", "event_id": "evt-fomc-1",
         "on_outcome": [{"if": {"field": "direction", "op": "eq", "value": "hawkish"}, "action": "exit"}]},
        {"thesis_id": "mth-B", "event_id": "evt-fomc-1",
         "on_outcome": [{"if": {"field": "surprise", "op": "gt", "value": 0.0}, "action": "add"}]},
        {"thesis_id": "mth-C", "event_id": "evt-OTHER",   # different event -> filtered
         "on_outcome": [{"if": {"field": "surprise", "op": "gt", "value": 0.0}, "action": "add"}]},
    ]
    res = resolve_event_for_theses(event, links)
    by = {r["thesis_id"]: r["action"] for r in res}
    assert by == {"mth-A": "exit", "mth-B": "add"}   # C filtered by event_id


def test_resolve_event_no_realized_outcome_is_empty():
    event = {"event_id": "evt-1", "status": "scheduled"}   # not resolved yet
    links = [{"thesis_id": "A", "event_id": "evt-1",
              "on_outcome": [{"if": {"field": "x", "op": "eq", "value": 1}, "action": "exit"}]}]
    assert resolve_event_for_theses(event, links) == []


def test_resolve_event_no_matching_rule_omits_thesis():
    event = {"event_id": "e", "realized_outcome": {"surprise": -0.5}}
    links = [{"thesis_id": "A", "event_id": "e",
              "on_outcome": [{"if": {"field": "surprise", "op": "gt", "value": 0}, "action": "add"}]}]
    assert resolve_event_for_theses(event, links) == []
