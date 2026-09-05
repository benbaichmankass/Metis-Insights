"""Phase G — E3 sunset pass, its disposition guard, and the E2 pull rule.

These tests are deliberately THIN over the modules' own ``--self-test`` suites
rather than a second copy of them. The self-tests are the load-bearing ones —
they run inside CI on every guard invocation, so a failure path that stops
working is caught even on a PR that touches nothing near it. What is added here
is the part a self-test cannot assert about itself: that the self-tests are
WIRED (registered in `run_guards.py`), and the handful of invariants that hold
across the three modules together.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "ops"))
sys.path.insert(0, str(REPO / "scripts" / "ci"))

import check_capability_pull as cpull  # noqa: E402
import check_sunset_dispositions as sdisp  # noqa: E402
import sunset_pass as sp  # noqa: E402


def _run(*argv):
    return subprocess.run([sys.executable, *argv], cwd=REPO,
                          capture_output=True, text=True, timeout=300)


@pytest.mark.parametrize("script", [
    "scripts/ops/sunset_pass.py",
    "scripts/ci/check_sunset_dispositions.py",
    "scripts/ci/check_capability_pull.py",
])
def test_self_tests_pass(script):
    r = _run(script, "--self-test")
    assert r.returncode == 0, f"{script} --self-test failed:\n{r.stdout}\n{r.stderr}"


def test_both_guards_are_registered_and_run_their_self_test():
    """A self-test nothing invokes is the `check_selftest_wiring` lesson.

    Registration is the whole difference between a guard and a script: an
    unregistered guard never runs, which is exactly the `unwired` verdict the
    sunset pass itself reports on.
    """
    import run_guards
    by_name = {g["name"]: g for g in run_guards.GUARDS}
    for name in ("sunset-disposition-guard", "capability-pull-guard"):
        assert name in by_name, f"{name} is not registered in run_guards.GUARDS"
        steps = json.dumps(by_name[name]["steps"])
        assert "--self-test" in steps, f"{name} does not run its own self-test"


def test_the_live_register_and_the_live_pass_agree():
    """The repo's own committed state must satisfy the guard it ships with."""
    passes, state = sdisp.recent_passes(REPO / "comms" / "sunset",
                                        sdisp.CARRY_ESCALATION_PASSES)
    assert state in {"read", "no_passes"}, f"the sunset pass is {state}"
    reg = json.loads((REPO / "docs" / "claude" / "SUNSET-DISPOSITIONS.json")
                     .read_text(encoding="utf-8"))
    fail, _ = sdisp.audit(reg, passes, state, repo=REPO)
    assert not fail, "the committed disposition register fails its own guard:\n" + "\n".join(fail)


def test_a_refusing_constraint_never_reads_as_an_all_clear():
    """The single most dangerous misreading available to E2.

    *We do not know where the chain is stuck* is strictly weaker than *nothing
    is stuck*. A guard that blessed self-started capability on the strength of a
    refusal would invert the one rule the operating model calls its
    highest-leverage — so the advisory path must never grade a claim `verified`
    and must never grade an ABSENT readout the same as a measured refusal.
    """
    refusing = {"constraint": {"verdict": "insufficient_basis", "named_stage": None,
                               "assessed": 6, "population": 584,
                               "assessed_coverage": 0.01, "min_assessed_coverage": 0.5}}
    state, stage, _why = cpull.enforcement_state(refusing)
    assert (state, stage) == ("advisory", None)
    assert cpull.enforcement_state(None)[0] == "unknown", \
        "an absent readout must not be graded the same as a measured refusal"

    rc, lines = cpull.audit(added=["scripts/ops/anything.py"], modified=[],
                            diff_state="read", constraint=refusing, repo=REPO)
    body = "\n".join(lines)
    assert rc == 0, "advisory enforcement must not fail a PR"
    assert "UNVERIFIED" in body
    assert "VERIFIED:" not in body, "a refusal must never produce a verified verdict"


def test_the_live_constraint_is_the_one_the_guard_grades():
    """No opinion about which state the repo is in — only that it is a real one,
    and that the guard reads the file the readout actually writes."""
    doc = json.loads((REPO / "docs" / "claude" / "CONSTRAINT.json")
                     .read_text(encoding="utf-8"))
    state, _stage, why = cpull.enforcement_state(doc)
    assert state in {"enforcing", "advisory", "unknown"}
    assert why.strip(), "the enforcement state must always say why"


def test_phase_g_declares_the_stage_it_unblocks():
    """E2's done-condition, asserted against this change's own work object."""
    import yaml
    obj = yaml.safe_load(
        (REPO / "docs/claude/work/objects/WO-20260901-PHASE-G.yaml").read_text(encoding="utf-8"))
    assert obj.get("unblocks_stage") in cpull.STAGES


def test_sunset_pass_never_proposes_a_prop_leg_on_a_lifetime_zero():
    """The 25%-of-day-one false positive, pinned.

    Prop fills live in `prop_fills`, isolated from `trades`, so a prop-routed leg
    reads zero lifetime closes while trading normally. 3 of the 12 legs that read
    "never closed a trade" on 2026-09-01 were the `breakout_1` legs.
    """
    idx = [("2026-09-01", {"rows": [
        {"strategy": "p", "n_closed": 0,
         "reasons": ["no closed trades in window — insufficient evidence."]}]})]
    row, = sp.grade_strategies(idx, lifetime_state="read", lifetime={},
                               routing={"p": [("breakout_1", "prop")]})
    assert row["verdict"] == "not_assessed" and row["basis"] == "prop_routed"


def test_sunset_pass_reads_the_gate_floor_rather_than_restating_it():
    """Two files holding the same threshold is how they drift. The pass has no
    opinion about the M7 floor — it parses it out of the gate's own reason."""
    assert sp._floor_from_reason("insufficient evidence (n_closed=4 < 20) — x") == 20
    assert sp._floor_from_reason("no closed trades in window") is None


def test_the_live_pass_states_its_population():
    """A candidate count with no denominator is the unstated-population error."""
    doc = sp.latest(REPO)
    if doc is None:
        pytest.skip("no committed sunset pass yet")
    p = doc["population"]
    for k in ("strategy_legs_graded", "lifetime_state", "routing_state",
              "machinery_probe", "packet_dates_read"):
        assert k in p, f"the pass must publish `{k}` beside its verdict"
    assert p["machinery_probe"]["control"] in {"passed", "failed", "not_run"}


def test_sunset_pass_never_proposes_a_leg_absent_from_the_lifetime_capture():
    """MI-126 — an absent leg was being recorded as a measured ZERO.

    `sunset_pass.py` defaulted `lifetime.get(name, 0)` under a `read` capture,
    defended by a comment claiming `/api/bot/performance` "lists every strategy
    with any closed trade". It does not: `src/web/api/routers/performance.py`
    filters `AND t.pnl IS NOT NULL`, so it lists every strategy with a
    *pnl-bearing* close. A leg whose every close landed `pnl NULL` is simply
    ABSENT — and the default turned that absence into `never_closed_lifetime`
    and the note "has never closed a single trade in its life".

    Population, measured 2026-09-05 against `/api/bot/performance?window=all`:
    52 enabled legs, 46 present, **11 absent and silently defaulted to 0**;
    nine of the ten proposed retirements were among them, and five of the ten
    had closed trades in `trade_journal.db::trades` before the packet was
    written. See `docs/claude/diagnoses/MI-124-never-firing-legs-diagnosis.md`.

    ⚠️ This test FAILS on the old default — that is the point of it. `absent`
    and `measured_zero` below are identical in routing and in gate history and
    differ ONLY in whether the capture carries them, so the assertion cannot
    pass by accident.
    """
    idx = [("2026-09-01", {"rows": [
        {"strategy": "absent", "n_closed": 0,
         "reasons": ["no closed trades in window — insufficient evidence."]},
        {"strategy": "measured_zero", "n_closed": 0,
         "reasons": ["no closed trades in window — insufficient evidence."]},
    ]})]
    routing = {"absent": [("alpaca_paper", "paper")],
               "measured_zero": [("alpaca_paper", "paper")]}

    rows = {r["name"]: r for r in sp.grade_strategies(
        idx, lifetime_state="read", lifetime={"measured_zero": 0}, routing=routing)}

    absent = rows["absent"]
    assert absent["verdict"] != "retire_candidate", (
        "a leg ABSENT from the capture was proposed for retirement on that absence; "
        f"got verdict={absent['verdict']} basis={absent['basis']}")
    assert absent["basis"] == "lifetime_not_observed"
    assert absent["evidence"]["leg_lifetime_state"] == "not_observed"
    assert absent["evidence"]["lifetime_closed_trades"] is None, (
        "an absence must stay None — writing 0 asserts a measurement nobody made")

    # The POSITIVE CONTROL: the branch is not simply disabled. A leg that IS in
    # the capture and reads zero is still a candidate, on a real measurement.
    measured = rows["measured_zero"]
    assert measured["verdict"] == "retire_candidate"
    assert measured["basis"] == "never_closed_lifetime"
    assert measured["evidence"]["leg_lifetime_state"] == "observed"


def test_sunset_pass_states_how_many_legs_it_could_not_measure():
    """"Always state the population" — applied to the absence itself.

    A reader who cannot see how many legs were graded with NO lifetime
    measurement cannot tell how much of the pass rests on silence.
    """
    idx = [("2026-09-01", {"rows": [
        {"strategy": "absent", "n_closed": 0,
         "reasons": ["no closed trades in window — insufficient evidence."]}]})]
    rows = sp.grade_strategies(idx, lifetime_state="read", lifetime={},
                               routing={"absent": [("alpaca_paper", "paper")]})
    assert sum(1 for r in rows
               if r["evidence"]["leg_lifetime_state"] == "not_observed") == 1
