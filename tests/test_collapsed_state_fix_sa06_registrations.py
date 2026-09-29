"""FIX-SA-06: the production three-state fields are registered AND detectable.

A registration is only worth something if collapsing the state it protects makes
the guard fail. Registering the rows first and running the guard showed it did
not, for 4 of 10 planted collapses: a producer file that also CONSUMES its own
field carries the state literal on a branch line
(``if balance.get("balance_state") == "unreported"``), so deleting the real
emission left the contract satisfied. ``producer_line_pattern`` narrows the
evidence to emission lines; these tests mutate the REAL producer text (not a
synthetic stand-in) and pin that each collapse is now seen.
"""
from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "_csg_sa06", REPO / "scripts" / "ci" / "check_collapsed_states.py")
G = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(G)

NEW = ["broker_truth.read_state", "prop_reconcile.open_risk_state",
       "prop_reconcile.day_pnl_state", "ib_client.verify_state",
       "prop_fills_staleness.balance_state", "clients.query_state",
       "diag_venue.read_state"]
CONTRACT = {c["name"]: c for c in G.CONTRACTS}


def _missing(name: str, text: str) -> list:
    c = CONTRACT[name]
    got = G._states_in(text, list(c["states"]), str(c.get("producer_field") or ""),
                       line_pattern=str(c.get("producer_line_pattern") or ""))
    return [s for s in c["states"] if s not in got]


def _real(name: str) -> str:
    return (REPO / str(CONTRACT[name]["producer"])).read_text(encoding="utf-8")


_BRANCH = re.compile(r"==|!=|\bnot in\b|\bin \(")


def _collapse(name: str, text: str, state: str, into: str) -> str:
    """Remove the producer's ability to emit ``state``: replace ``"<state>"`` with
    ``"<into>"`` on every line that is NOT a comparison.

    Deliberately independent of the contract's own evidence filter. A mutation
    defined by that filter is self-fulfilling -- it would mutate exactly the
    lines the filter counts, so it passes whatever the filter is (measured: with
    ``producer_line_pattern`` deleted, every case still passed and only the
    hole-control failed). Branch lines (``== "<state>"``) are left alone because
    a real collapse edits the emission, not the consumers beside it.
    """
    out = []
    for ln in text.splitlines(keepends=True):
        out.append(ln if _BRANCH.search(ln)
                   else ln.replace(f'"{state}"', f'"{into}"'))
    return "".join(out)


@pytest.mark.parametrize("name", NEW)
def test_registered_and_the_real_producer_emits_every_declared_state(name):
    assert name in CONTRACT
    assert _missing(name, _real(name)) == []


# (contract, state whose only emission is removed, the state it collapses into)
@pytest.mark.parametrize("name,state,into", [
    ("broker_truth.read_state", "absent", "unreadable"),          # deploy failure -> looked
    ("broker_truth.read_state", "unreadable", "read"),
    ("prop_reconcile.open_risk_state", "stop_unknown", "measured"),
    ("prop_reconcile.open_risk_state", "unreadable", "no_open_positions"),
    ("prop_reconcile.day_pnl_state", "realized_unreported", "measured"),
    ("prop_reconcile.day_pnl_state", "unreported", "measured"),
    ("ib_client.verify_state", "unverified", "verified"),         # re-read failed -> "cancel worked"
    ("ib_client.verify_state", "verified", "unverified"),
    ("ib_client.verify_state", "not_attempted", "verified"),
    ("prop_fills_staleness.balance_state", "read_failed", "within_noise"),
    ("prop_fills_staleness.balance_state", "insufficient_snapshots", "within_noise"),
    ("prop_fills_staleness.balance_state", "unreported", "explained"),
    ("clients.query_state", "could_not_look", "no_rows"),         # raised -> "venue is empty"
    ("clients.query_state", "no_rows", "rows_returned"),
    ("diag_venue.read_state", "could_not_look", "orders_read"),
    ("diag_venue.read_state", "orders_read", "could_not_look"),
])
def test_collapsing_the_state_in_the_real_producer_is_detected(name, state, into):
    mutated = _collapse(name, _real(name), state, into)
    assert mutated != _real(name), "the mutation must actually change the file"
    assert state in _missing(name, mutated)


def test_without_the_line_pattern_a_branch_line_hides_the_collapse():
    """The reason ``producer_line_pattern`` exists. Same mutation as
    ``balance_state -> unreported removed``, evidence taken the OLD way (field
    only): the branch ``balance.get("balance_state") == "unreported"`` still
    supplies the literal, so the guard stays green over a real collapse."""
    name = "prop_fills_staleness.balance_state"
    c = CONTRACT[name]
    mutated = _collapse(name, _real(name), "unreported", "explained")
    old_way = G._states_in(mutated, list(c["states"]), str(c["producer_field"]))
    assert "unreported" in old_way            # the hole
    assert "unreported" in _missing(name, mutated)   # closed


def test_line_pattern_is_opt_in_and_does_not_change_existing_contracts():
    text = 'x = {"state": "a"}\nif d["state"] == "b":\n    pass\n'
    assert G._states_in(text, ["a", "b"], "state") == {"a", "b"}
    assert G._states_in(text, ["a", "b"], "state",
                        line_pattern=r'"state"\s*:\s*"') == {"a"}
    assert not any(
        "producer_line_pattern" in c for c in G.CONTRACTS if c["name"] not in NEW)


def test_day_pnl_state_has_no_production_consumer_and_says_so():
    """Field beats comment: the module says `nothing refuses a trade on this, the
    API serves it as-is`. The guard's consumer credit therefore comes from the
    test that reads it; if a production reader appears this can be relaxed."""
    c = CONTRACT["prop_reconcile.day_pnl_state"]
    tok = re.compile(str(c["consumer_token"]))
    prod = [p for p in (REPO / "src").rglob("*.py")
            if p != REPO / str(c["producer"]) and tok.search(
                p.read_text(encoding="utf-8", errors="replace"))]
    assert prod == []
