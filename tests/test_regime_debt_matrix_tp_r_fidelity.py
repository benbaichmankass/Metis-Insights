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

⚠️ E46 made the false claim VISIBLE (the grade follows the argv now) but did
NOT model the lever -- it only stopped `tp_r` from being wrongly counted as
modelled. `build_harness_cmd` still passed neither `--tp-cap-pct` nor `--tp-r`
for any trend/pullback leg, so every one of them graded `approximate` (or, for
a parked 50R sentinel, skated past disclosure by `_tp_r_binds`'s threshold --
see below). **E55 / `PI-20260924-JN54P2HH-0005` is the actual fix**: `_tp_r_flags`
now builds the argv that genuinely models a leg's declared `tp_r`, via the
SAME `--tp-cap-pct`/`--tp-r` pair backtest_trend.py's own CLI help already
describes as "LIVE-PARITY". The tests below that asserted the flag was NEVER
forwarded are the ones this change was always going to invert -- several said
so explicitly, in their own failure messages.

⚠️ THIS ALSO CLOSES A WIDER GAP THAN THE `tp_r: 3.0` CASE. `--tp-r` only binds
when `--tp-cap-pct > 0`, and the two together are the harness's ONLY way to
express the live formula `tp = min(entry*(1+TP_VENUE_CAP_PCT), entry +
tp_r*risk)` (`src/units/strategies/trend_donchian.py:393`,
`TP_VENUE_CAP_PCT = 0.099`). So modelling `tp_r` at all necessarily also
enables the venue price cap -- there is no flag that models one without the
other. That means a leg carrying the fleet's 50R "parked" sentinel is now ALSO
modelled (previously excused by `_tp_r_binds`'s nonbinding threshold, which
answered a narrower question -- see `_tp_r_flags`'s docstring in
`regime_debt_matrix.py`). This is the harness catching up to
`BL-20260810-BACKTEST-DOES-NOT-MODEL-THE-LIVE-CAPPED-TP` for every
trend/pullback leg that declares a `tp_r`, not only the ones whose target sits
close enough to bind under the old threshold.
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

def test_a_declared_tp_r_is_now_forwarded_and_the_leg_grades_faithful():
    """THE FIX. Before E55 this asserted the opposite -- see the module
    docstring. The grade still follows the argv (E46's mechanism, untouched);
    what changed is that the argv now actually carries the capability.
    """
    cap = str(rdm._tp_venue_cap_pct())
    for cfg, harness in ((_trend(tp_r=3.0), "trend"),
                         (_pullback(tp_r=3.0), "pullback")):
        argv, faithful, omitted = _build(cfg, harness)
        assert "--tp-cap-pct" in argv, f"{harness}: the cap was not forwarded"
        i = argv.index("--tp-cap-pct")
        assert argv[i + 1] == cap, f"{harness}: forwarded a different cap than live uses"
        assert "--tp-r" in argv, f"{harness}: tp_r itself was not forwarded"
        j = argv.index("--tp-r")
        assert argv[j + 1] == "3.0", f"{harness}: forwarded the wrong tp_r"
        assert "tp_r" not in omitted, f"{harness}: still names it as omitted"
        assert faithful is True, f"{harness}: still refuses to call it modelled"


def test_the_parked_50R_sentinel_is_also_now_modelled():
    """CORRECTED 2026-09-24 (E55). This used to read 'is not an omission' and
    stop there, on the theory that a harness with no take-profit path omits
    nothing REACHABLE from a 50R sentinel. That theory was right about the OLD
    harness and wrong about what actually binds: `--tp-cap-pct`/`--tp-r`
    together model `min(entry*(1+0.099), entry+tp_r*risk)`, and for almost any
    leg the PRICE term binds regardless of how large `tp_r` is --
    `BL-20260810-BACKTEST-DOES-NOT-MODEL-THE-LIVE-CAPPED-TP`. So a sentinel leg
    is not "harmlessly unreachable" once the cap can be modelled; it is simply
    modelled, the same as any other declared `tp_r`. `tp_r` still correctly
    does not appear in `omitted_levers` -- for the opposite reason than before.
    """
    cap = str(rdm._tp_venue_cap_pct())
    for cfg, harness in ((_trend(tp_r=50.0), "trend"),
                         (_pullback(tp_r=50.0), "pullback")):
        argv, faithful, omitted = _build(cfg, harness)
        assert "--tp-cap-pct" in argv, f"{harness}: the sentinel was not modelled"
        assert argv[argv.index("--tp-cap-pct") + 1] == cap
        assert "tp_r" not in omitted
        assert faithful is True


def test_the_nonbinding_threshold_still_applies_where_no_flag_can_carry_it():
    """`_tp_r_binds`/`_TP_R_NONBINDING` are not dead code -- `conditional_omissions`
    still uses them for any harness whose `_PLAIN_CONDITIONAL_ON_FLAG` entry
    carries `flag=None` (today: squeeze, which has no take-profit path at all
    to enable). Tested directly against `conditional_omissions`, because
    `build_harness_cmd`'s trend/pullback branches no longer exercise this path
    at any `tp_r` value -- they now forward the flag unconditionally whenever
    `tp_r` is declared and readable (see the two tests above).
    """
    below = rdm.conditional_omissions(
        "squeeze", {"tp_r": rdm._TP_R_NONBINDING - 0.1}, ["--data", "d.csv"])
    at = rdm.conditional_omissions(
        "squeeze", {"tp_r": rdm._TP_R_NONBINDING}, ["--data", "d.csv"])
    assert "tp_r" in below
    assert "tp_r" not in at


def test_a_leg_declaring_no_tp_r_omits_nothing_and_forwards_no_flag():
    """A missing `tp_r` is the harness's own default, not an omission -- and
    the argv must stay byte-identical to every pre-E55 run (no --tp-cap-pct,
    no --tp-r) for such a leg, since there is nothing to model.
    """
    argv, faithful, omitted = _build(_trend(), "trend")
    assert "--tp-cap-pct" not in argv
    assert "--tp-r" not in argv
    assert "tp_r" not in omitted
    assert faithful is True


def test_an_unreadable_tp_r_fails_TOWARD_omitted_not_toward_a_crashed_run():
    """`we could not read it` is never permission to claim `faithful` --
    and, since E55, it must also never reach the subprocess as a string
    argparse's `type=float` cannot parse. Forwarding it verbatim would turn
    an honest `approximate` grade into a crashed `harness_failed` run.
    """
    argv, faithful, omitted = _build(_trend(tp_r="probably fine"), "trend")
    assert "--tp-cap-pct" not in argv, "an unreadable tp_r must not reach the subprocess"
    assert "--tp-r" not in argv
    assert "tp_r" in omitted
    assert faithful is False


# --- the mechanism that builds the argv, in isolation -----------------------

def test_tp_r_flags_matches_the_live_formula_exactly():
    """`_tp_r_flags` is the one place this pairing is built; pin its shape."""
    assert rdm._tp_r_flags({"tp_r": 3.0}) == [
        "--tp-cap-pct", str(rdm._tp_venue_cap_pct()), "--tp-r", "3.0"]
    assert rdm._tp_venue_cap_pct() == 0.099, (
        "must stay the SAME value order_package sends to the venue -- "
        "imported from src/runtime/tp_venue_cap.py, never re-declared"
    )


def test_tp_r_flags_empty_for_missing_or_unreadable_tp_r():
    assert rdm._tp_r_flags({}) == []
    assert rdm._tp_r_flags({"tp_r": None}) == []
    assert rdm._tp_r_flags({"tp_r": "not a number"}) == []


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

def test_no_enabled_leg_claims_faithful_without_the_argv_to_back_it():
    """CORRECTED 2026-09-24 (E55). This used to assert the OPPOSITE property --
    that no faithful leg's tp_r ever `_tp_r_binds` -- which was the right guard
    for a harness that could not model tp_r at all (a faithful leg with a
    binding tp_r could only mean the omission was mis-graded). That is no
    longer true BY DESIGN: every trend/pullback leg with a declared, readable
    `tp_r` now genuinely models it and correctly grades faithful, whatever its
    value. Re-asserting the old property here would fail on 12 legs for the
    right reason and prove nothing.

    The property that actually catches E46's bug shape -- a leg claiming
    `faithful` without the capability that would justify it -- is checking the
    CAUSE instead of the symptom: any leg that grades faithful while declaring
    a `tp_r` must have the argv to show for it.
    """
    legs = yaml.safe_load((ROOT / "config" / "strategies.yaml").read_text())
    legs = {n: b for n, b in legs["strategies"].items()
            if isinstance(b, dict) and b.get("enabled", True)}
    offenders = []
    for name, cfg in sorted(legs.items()):
        harness = rdm.classify(cfg)
        if harness not in ("trend", "pullback") or cfg.get("tp_r") is None:
            continue
        argv, faithful, _omitted = _build(cfg, harness)
        if faithful and "--tp-cap-pct" not in argv:
            offenders.append((name, harness, cfg.get("tp_r")))
    assert not offenders, (
        "these legs grade `faithful` with a declared tp_r but never forwarded "
        f"the flag that would model it: {offenders}"
    )


def test_every_trend_or_pullback_leg_with_a_readable_tp_r_no_longer_omits_it():
    """The fleet-wide measurement this change makes true, stated as a property
    rather than a snapshot count (a count goes stale the next roster edit).

    MEASURED 2026-09-24 over the 51 routed legs of config/strategies.yaml, all
    41 trend/pullback: every one declares a numeric `tp_r` (none omits it), so
    this is exercised across the whole trend/pullback roster, not a slice.

    Deliberately checks `omitted_levers` for `tp_r` specifically, NOT overall
    `faithful` -- a leg can still grade `approximate` for an unrelated
    unmodelled lever (e.g. `trail_decay_stall_bars`), and that is correct, not
    a regression this test should catch.
    """
    legs = yaml.safe_load((ROOT / "config" / "strategies.yaml").read_text())
    legs = {n: b for n, b in legs["strategies"].items()
            if isinstance(b, dict) and b.get("enabled", True)}
    checked = 0
    offenders = []
    for name, cfg in sorted(legs.items()):
        harness = rdm.classify(cfg)
        if harness not in ("trend", "pullback"):
            continue
        try:
            float(cfg.get("tp_r"))
        except (TypeError, ValueError):
            continue
        checked += 1
        _argv, _faithful, omitted = _build(cfg, harness)
        if "tp_r" in omitted:
            offenders.append((name, harness, cfg.get("tp_r")))
    assert checked > 0, "the population is empty -- this would pass vacuously"
    assert not offenders, f"tp_r still omitted for: {offenders}"
