"""`src/runtime/regime_flip_exit.py` — default-off, three-state, one predicate.

The lever this module implements has 43 `honest_negative` cells in the M20
coverage matrix and, until now, NO runtime implementation — the only
implementations were the offline replays. So these tests pin the two properties
that decide whether the runtime version can ever be trusted against those 43
gradings:

  1. It asks the LIVE gate (`policy._evaluate_trend_cell`) rather than reading
     the policy dict itself. The replay's local `off_cell()` is a second copy,
     and that copy is what graded all 43 cells.
  2. It is DEFAULT-OFF and three-state, so an undeclared leg is never reported
     as "evaluated, regime fine".
"""

from __future__ import annotations

import itertools

import pytest

from src.runtime.regime import policy as policy_mod
from src.runtime import regime_flip_exit as rfe


def _replay_off_cell(policy, label, key, direction):
    """VERBATIM copy of scripts/research/m20_regime_flip_replay.py::off_cell.

    Kept here on purpose. It is the predicate that produced the 43 recorded
    dispositions, so the equivalence below is a statement about the EVIDENCE,
    not a tautology about the new module.
    """
    cell = ((policy.get(label) or {}).get(key) or {})
    v = cell.get(direction, "on")
    return v is False or (isinstance(v, str) and v.lower() == "off")


def test_module_agrees_with_the_predicate_that_graded_the_43_cells() -> None:
    """Exhaustive over the committed policy — not a sampled spot-check."""
    pol = policy_mod.load_policy()
    labels = sorted(k for k, v in pol.items() if isinstance(v, dict))
    keys = sorted({k for lab in labels for k in (pol.get(lab) or {})})
    assert labels, "policy has no regime labels — the comparison would be vacuous"
    assert keys, "policy has no strategy keys — the comparison would be vacuous"

    compared = 0
    disagreements = []
    # `unknown` is included: it is the ADX warm-up label, and both sides must
    # agree that it never produces an exit.
    for label, key, direction in itertools.product(
        labels + ["unknown"], keys, ["long", "short"]
    ):
        compared += 1
        theirs = _replay_off_cell(pol, label, key, direction)
        ours, _cell = rfe.cell_is_off(
            policy=pol, regime=label, strategy_key=key, direction=direction
        )
        if theirs != ours:
            disagreements.append((label, key, direction, theirs, ours))

    assert compared >= 50, f"denominator too small to mean anything: {compared}"
    assert not disagreements, (
        f"{len(disagreements)} of {compared} triples disagree between the "
        f"replay predicate that graded the matrix and the live gate this module "
        f"delegates to: {disagreements[:10]}"
    )


def test_an_undeclared_leg_is_not_declared_and_never_exits() -> None:
    """`not_declared` must not collapse into `no_flip`.

    An unconfigured leg and a leg actively judged safe are opposite claims; if
    they share a value, nothing downstream can tell "we did not look" from "we
    looked and it is fine".
    """
    pol = policy_mod.load_policy()
    for cfg in (None, {}, {"regime_flip_exit": False}, {"regime_flip_exit": None}, {"other": 1}):
        v = rfe.evaluate(
            strategy_cfg=cfg, policy=pol, regime="chop",
            strategy_key="trend_donchian", direction="long",
        )
        assert v.state == rfe.STATE_NOT_DECLARED, (cfg, v)
        assert v.should_exit is False
        assert v.close_reason is None


def test_a_declared_leg_in_an_off_cell_exits_and_names_the_regime() -> None:
    pol = {"chop": {"demo_strat": {"long": "off"}}}
    v = rfe.evaluate(
        strategy_cfg={"regime_flip_exit": True}, policy=pol, regime="chop",
        strategy_key="demo_strat", direction="long",
    )
    assert v.state == rfe.STATE_FLIP
    assert v.should_exit is True
    # The reason must carry the regime that caused it — a bare "regime_flip"
    # would leave a closed trade unattributable to the label that closed it.
    assert v.close_reason == "regime_flip_chop"
    assert v.regime == "chop"


def test_a_declared_leg_in_an_on_cell_does_not_exit() -> None:
    pol = {"chop": {"demo_strat": {"long": "off"}}}
    v = rfe.evaluate(
        strategy_cfg={"regime_flip_exit": True}, policy=pol, regime="trending",
        strategy_key="demo_strat", direction="long",
    )
    assert v.state == rfe.STATE_NO_FLIP
    assert v.should_exit is False


def test_an_unknown_regime_is_permissive_never_an_exit() -> None:
    """A detector warm-up must not manufacture a close on a live position."""
    pol = {"chop": {"demo_strat": {"long": "off"}}}
    for regime in (None, "unknown", "", "not_a_regime"):
        v = rfe.evaluate(
            strategy_cfg={"regime_flip_exit": True}, policy=pol, regime=regime,
            strategy_key="demo_strat", direction="long",
        )
        assert v.should_exit is False, (regime, v)


def test_the_module_is_not_wired_into_the_order_path_yet() -> None:
    """Shipped DISABLED, per the operator's decision.

    This is the test that would fail the day someone wires it in, forcing that
    change to be a deliberate, reviewed one rather than a silent import.
    """
    from pathlib import Path

    repo = Path(__file__).resolve().parents[1]
    callers = [
        p for p in (repo / "src").rglob("*.py")
        if p.name != "regime_flip_exit.py"
        and "regime_flip_exit" in p.read_text()
    ]
    assert not callers, (
        "regime_flip_exit is now imported by "
        f"{[str(p.relative_to(repo)) for p in callers]}. Wiring it into a live "
        "exit path is a separate, tier-gated decision -- update this test in "
        "the same change that does it, so the wiring is never silent."
    )


def test_it_delegates_rather_than_re_reading_the_policy_dict() -> None:
    """A third copy of the cell predicate would defeat the module's purpose."""
    src = (rfe.__file__ and open(rfe.__file__).read()) or ""
    assert "_evaluate_trend_cell" in src, (
        "the module no longer delegates to the live gate; it has grown its own "
        "copy of the predicate, which is the drift this file exists to prevent"
    )


@pytest.mark.parametrize("direction", ["long", "short"])
def test_direction_is_honoured_independently(direction: str) -> None:
    """An OFF long cell must not close a short, or vice versa."""
    pol = {"chop": {"demo_strat": {"long": "off", "short": "on"}}}
    v = rfe.evaluate(
        strategy_cfg={"regime_flip_exit": True}, policy=pol, regime="chop",
        strategy_key="demo_strat", direction=direction,
    )
    assert v.should_exit is (direction == "long"), (direction, v)
