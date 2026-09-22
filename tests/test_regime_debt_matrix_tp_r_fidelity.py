"""`fidelity` must be COMPUTED from the argv that ran, not asserted by a set.

E46 / `PI-20260922-E41-0005`. `comms/strategy_evidence/qqq_trend_long_1d.json`
recorded `fidelity: faithful` with `omitted_levers: []` against a config
declaring `tp_r: 3.0`, while the harness run behind it carried `tp_r: None` and
`tp_r_effective_n: 0` -- no take-profit at all.

The cause was an ASSERTION. `tp_r` sits in `_TREND_PLAIN` / `_PB_PLAIN`, sets
documented as the keys "the base harness fully models". Both harnesses do take
`--tp-r`, but it is "only consulted when --tp-cap-pct > 0" (backtest_trend.py's
own help text) and `build_harness_cmd` passes NEITHER flag. So membership in a
frozenset stood in for a capability the run did not exercise, on the corpus the
real-money promotion bar reads.

⚠️ AND IT WAS NOT COSMETIC. `PI-20260922-E41-0006`: the omitted lever is the one
that moved that leg's number 3.4x -- `net_r_oos` 1.9389 -> 6.6307 on an
IDENTICAL data span, the only difference being `trail_decay_arm_r` 3.56 -> 0.0.
With the TP modelled the 3.56R arm could not fire at all, so the +4.7R is a
fidelity artifact, not a benefit of the strip.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "research"))
_spec = importlib.util.spec_from_file_location(
    "regime_debt_matrix", ROOT / "scripts" / "research" / "regime_debt_matrix.py"
)
rdm = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(rdm)


def _build(cfg: dict, harness: str):
    return rdm.build_harness_cmd("leg", cfg, harness, "d.csv", "1h",
                                 "e.jsonl", "j.json")


def _trend(**over) -> dict:
    base = {"model": "donchian", "symbols": ["QQQ"], "timeframe": "1d",
            "donchian": 20, "atr_period": 14, "atr_stop_mult": 2.5,
            "trail_mult": 5.0, "min_confidence": 0.0, "long_only": True}
    base.update(over)
    return base


def _pullback(**over) -> dict:
    base = {"model": "pullback", "symbols": ["GLD"], "timeframe": "1h",
            "trend_lookback": 40, "pullback_lookback": 10, "pullback_frac": 0.5,
            "atr_period": 14, "atr_stop_mult": 2.5, "trail_mult": 5.0,
            "min_confidence": 0.0}
    base.update(over)
    return base


# --- the reported case, both harnesses -------------------------------------

def test_a_binding_tp_r_is_named_as_omitted_and_the_leg_is_not_faithful():
    for cfg, harness in ((_trend(tp_r=3.0), "trend"),
                         (_pullback(tp_r=3.0), "pullback")):
        argv, faithful, omitted = _build(cfg, harness)
        assert "--tp-cap-pct" not in argv, (
            "if this ever forwards the cap, the expectation below inverts -- and "
            "that is the point: the grade follows the argv"
        )
        assert "tp_r" in omitted, f"{harness}: the unmodelled TP is not named"
        assert faithful is False, f"{harness}: still claims to have modelled it"


def test_the_parked_50R_sentinel_is_not_an_omission():
    """The fleet's 50R convention parks the target beyond reach.

    A harness with no take-profit path omits nothing REACHABLE, so flipping 28
    legs over a sentinel would be noise, not honesty. Same threshold the
    squeeze branch has used since 2026-07-30.
    """
    for cfg, harness in ((_trend(tp_r=50.0), "trend"),
                         (_pullback(tp_r=50.0), "pullback")):
        _, faithful, omitted = _build(cfg, harness)
        assert "tp_r" not in omitted
        assert faithful is True


def test_the_threshold_is_checked_in_both_directions():
    below = _build(_trend(tp_r=rdm._TP_R_NONBINDING - 0.1), "trend")[2]
    at = _build(_trend(tp_r=rdm._TP_R_NONBINDING), "trend")[2]
    assert "tp_r" in below
    assert "tp_r" not in at


def test_a_leg_declaring_no_tp_r_omits_nothing():
    """A missing `tp_r` is the harness's own default, not an omission."""
    _, faithful, omitted = _build(_trend(), "trend")
    assert "tp_r" not in omitted
    assert faithful is True


def test_an_unreadable_tp_r_fails_TOWARD_omitted():
    """`we could not read it` is never permission to claim `faithful`."""
    _, faithful, omitted = _build(_trend(tp_r="probably fine"), "trend")
    assert "tp_r" in omitted
    assert faithful is False


# --- the property that makes it COMPUTED rather than asserted --------------

def test_the_omission_disappears_on_its_own_once_the_flag_is_forwarded():
    """THE load-bearing property, and the negative control for every test above.

    `conditional_omissions` reads the built argv. So the day someone wires
    `--tp-cap-pct` through, `tp_r` stops being omitted with no edit to any
    membership set -- and if this test fails, the tests above are passing
    because the mechanism is inert rather than because it is correct.
    """
    cfg = _trend(tp_r=3.0)
    without = rdm.conditional_omissions("trend", cfg, ["--data", "d.csv"])
    with_flag = rdm.conditional_omissions(
        "trend", cfg, ["--data", "d.csv", "--tp-cap-pct", "0.099"])
    assert without == ["tp_r"]
    assert with_flag == []


def test_squeeze_has_no_flag_that_could_model_a_take_profit():
    """`None` as the required flag means the capability does not exist at all.

    backtest_squeeze.py's Chandelier trail is its sole profit-exit, so no argv
    can clear this one -- distinct from trend/pullback, where a flag exists and
    simply is not passed.
    """
    flag, _ = rdm._PLAIN_CONDITIONAL_ON_FLAG["squeeze"]["tp_r"]
    assert flag is None
    assert rdm.conditional_omissions(
        "squeeze", {"tp_r": 3.0}, ["--tp-cap-pct", "0.099"]) == ["tp_r"]


# --- over the REAL config, because a fixture cannot catch a drifted leg -----

def test_no_enabled_leg_grades_faithful_while_declaring_a_binding_tp_r():
    """The guard-shaped assertion: a property over the fleet, not a count.

    A count would go stale the next time a leg is added or retired; this holds
    for any roster. MEASURED 2026-09-22 over the 51 routed legs of
    config/strategies.yaml: `faithful` is 25, was 37, and 12 of the 13 legs
    naming `tp_r` as omitted were previously claiming to have modelled it.
    """
    legs = yaml.safe_load((ROOT / "config" / "strategies.yaml").read_text())
    legs = {n: b for n, b in legs["strategies"].items()
            if isinstance(b, dict) and b.get("enabled", True)}
    offenders = []
    for name, cfg in sorted(legs.items()):
        harness = rdm.classify(cfg)
        if harness is None:
            continue
        _, faithful, _omitted = _build(cfg, harness)
        if faithful and rdm._tp_r_binds(cfg.get("tp_r")):
            offenders.append((name, harness, cfg.get("tp_r")))
    assert not offenders, (
        "these legs declare a tp_r that binds and still grade `faithful`: "
        f"{offenders}"
    )
