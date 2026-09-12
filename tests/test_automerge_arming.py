"""Arming auto-merge on a head no check is measuring IS the merge, done blind.

A PR opened by `claude-pr-automerge` was born with zero attached checks —
GitHub suppresses workflow triggers for the built-in `GITHUB_TOKEN`, so the
`pull_request` event never fires (isolated in `pr-opener.yml`'s header on
PR #10079, re-measured on #10683) — and the workflow armed it anyway. Six
instances on 2026-09-08 alone (#11356, #11392, #11395, #11407, #11419,
#11424); the 2026-09-12 one dropped the commit carrying a LIVE lane's registry
row and that lane ran unrecorded on `main` for nine minutes.

⚠️ **THE TEST THAT MATTERS IS `test_the_self_job_alone_does_not_count`.** A
zero-check PR does NOT report zero check runs — it reports ONE, this very
workflow's own `open-and-automerge`, green because it succeeded at opening the
PR. So `len(check_runs) > 0` is TRUE on precisely the PR the gate must refuse,
and a gate written that way passes every time while reading like a real test.

Run: ``python3 -m pytest tests/test_automerge_arming.py``
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "ci" / "automerge_arming.py"


def _load():
    spec = importlib.util.spec_from_file_location("_automerge_arming", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


G = _load()

SELF = [{"name": G.SELF_JOB, "conclusion": "success"}]
REAL = SELF + [{"name": "guards"}, {"name": "pytest-run"}, {"name": "pytest-collect"}]


def test_self_test_passes():
    ok, fails = G._self_test(quiet=True)
    assert ok, f"automerge-arming self-test failures: {fails}"


# ── THE LOAD-BEARING REFUSAL ────────────────────────────────────────────────

def test_the_self_job_alone_does_not_count():
    """The zero-check PR reports one check run: this job's own."""
    r = G.grade(SELF, checks_read_ok=True)
    assert r["state"] == G.NONE_ATTACHED
    assert r["arm"] is False
    assert r["attached"] == 0


def test_a_naive_length_test_would_have_passed_the_case_that_matters():
    """Stated as a test so the vacuous implementation cannot come back quietly."""
    assert len(SELF) > 0, "the naive predicate is TRUE here…"
    assert G.grade(SELF, checks_read_ok=True)["arm"] is False, "…and wrong."


def test_the_refusal_names_the_token_cause_not_just_the_symptom():
    why = G.grade(SELF, checks_read_ok=True)["why"]
    assert "GITHUB_TOKEN" in why and "recursion" in why
    assert "PAT" in why, "a refusal with no remedy trains people to bypass it"


def test_self_only_and_genuinely_empty_are_distinguishable():
    """Both refuse, but they mean different things and the field says which."""
    assert G.grade(SELF, checks_read_ok=True)["self_only"] is True
    assert G.grade([], checks_read_ok=True)["self_only"] is False


# ── the arming case ─────────────────────────────────────────────────────────

def test_real_checks_arm():
    r = G.grade(REAL, checks_read_ok=True)
    assert r["state"] == G.ATTACHED and r["arm"] is True
    assert r["attached"] == 3, "the self job must not be counted among them"


def test_one_real_check_beside_the_self_job_is_enough():
    r = G.grade(SELF + [{"name": "guards"}], checks_read_ok=True)
    assert r["arm"] is True and r["attached"] == 1


def test_a_cancelled_check_is_still_attached():
    """Existence is this gate's question. PASSING is ci_settle's, and conflating
    the two would refuse to arm a PR whose checks are merely re-running."""
    r = G.grade(SELF + [{"name": "guards", "conclusion": "cancelled"}],
                checks_read_ok=True)
    assert r["state"] == G.ATTACHED and r["arm"] is True


def test_a_failing_check_is_still_attached():
    """Refusing here would be this module deciding a merge question it does not
    own — GitHub will not merge a red PR, and saying so twice invites drift."""
    assert G.grade(SELF + [{"name": "guards", "conclusion": "failure"}],
                   checks_read_ok=True)["arm"] is True


# ── "we could not look" is its own state ────────────────────────────────────

def test_an_unread_list_is_unreadable_never_none_attached():
    r = G.grade(None, checks_read_ok=False)
    assert r["state"] == G.UNREADABLE
    assert r["state"] != G.NONE_ATTACHED
    assert r["arm"] is False
    assert "did not look" in r["why"]


def test_unreadable_reports_a_null_count_never_zero():
    """Zero is a real reading — a head with no checks. `None` is the absence of
    one, and the two must not render identically."""
    assert G.grade(None, checks_read_ok=False)["attached"] is None
    assert G.grade([], checks_read_ok=True)["attached"] == 0


@pytest.mark.parametrize("bad", [{"check_runs": []}, "0", 0, 7, None, True])
def test_a_non_array_payload_is_refused_not_read_as_a_zero(bad):
    r = G.grade(bad, checks_read_ok=True)
    assert r["state"] == G.UNREADABLE and r["arm"] is False


# ── malformed entries ───────────────────────────────────────────────────────

def test_malformed_entries_do_not_take_the_whole_read_down():
    r = G.grade([None, "junk", 7, {"name": "guards"}], checks_read_ok=True)
    assert r["state"] == G.ATTACHED and r["attached"] == 1


def test_nameless_entries_do_not_manufacture_an_attachment():
    """Found by this module's own self-test before it shipped: two empty names
    are each `!= SELF_JOB`, so a truthiness test on the remainder armed."""
    r = G.grade([{}, {"name": ""}, {"name": "   "}], checks_read_ok=True)
    assert r["state"] == G.NONE_ATTACHED and r["arm"] is False


def test_duplicate_check_names_are_counted_not_deduplicated_away():
    r = G.grade(SELF + [{"name": "guards"}, {"name": "guards"}], checks_read_ok=True)
    assert r["arm"] is True and r["attached"] == 2


# ── contract ────────────────────────────────────────────────────────────────

def test_arm_is_true_for_exactly_one_state():
    got = [G.grade(x, checks_read_ok=k)["arm"]
           for x, k in ((REAL, True), (SELF, True), ([], True), (None, False))]
    assert got == [True, False, False, False]


def test_all_three_states_are_reachable_so_none_is_decorative():
    reached = {G.grade(REAL, checks_read_ok=True)["state"],
               G.grade(SELF, checks_read_ok=True)["state"],
               G.grade(None, checks_read_ok=False)["state"]}
    assert reached == set(G.ALL_STATES)


def test_there_is_no_override_that_forces_arming():
    """The cheapest way past this gate must be to make the checks attach."""
    src = SCRIPT.read_text(encoding="utf-8")
    for word in ("--force", "FORCE_ARM", "skip_gate", "ARM_ANYWAY"):
        assert word not in src, f"an override ({word}) makes the gate optional"


# ── the CLI, which is what the workflow actually runs ───────────────────────

def _run(payload: str):
    return subprocess.run([sys.executable, str(SCRIPT)], input=payload,
                          capture_output=True, text=True)


def test_cli_exits_0_when_it_arms_and_3_when_it_refuses():
    ok = _run(json.dumps({"check_runs": REAL, "checks_read_ok": True}))
    no = _run(json.dumps({"check_runs": SELF, "checks_read_ok": True}))
    assert ok.returncode == 0 and json.loads(ok.stdout)["arm"] is True
    assert no.returncode == 3 and json.loads(no.stdout)["arm"] is False


def test_cli_refuses_on_unparseable_input_rather_than_waving_it_through():
    r = _run("{not json")
    assert r.returncode == 3
    v = json.loads(r.stdout)
    assert v["state"] == G.UNREADABLE and v["arm"] is False


def test_cli_refuses_a_payload_that_is_not_an_object():
    r = _run("[1,2,3]")
    assert r.returncode == 3 and json.loads(r.stdout)["arm"] is False


def test_cli_treats_a_missing_read_flag_as_a_failed_read():
    """Omitting the flag must not default to 'we looked'."""
    r = _run(json.dumps({"check_runs": REAL}))
    assert r.returncode == 3 and json.loads(r.stdout)["state"] == G.UNREADABLE


def test_cli_self_test_exits_zero():
    r = subprocess.run([sys.executable, str(SCRIPT), "--self-test"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


# ── mutation: break a predicate in isolation, the suite must go RED ─────────

def _mutated(old: str, new: str):
    src = SCRIPT.read_text(encoding="utf-8")
    assert old in src, f"mutation anchor is stale: {old!r}"
    ns: dict = {}
    exec(compile(src.replace(old, new, 1), "<mutant>", "exec"), ns)
    return ns


def test_mutation_not_excluding_the_self_job_is_load_bearing():
    m = _mutated("foreign = [n for n in named if n != SELF_JOB]",
                 "foreign = list(named)")
    assert m["grade"](SELF, checks_read_ok=True)["arm"] is True, \
        "the mutant must arm the zero-check PR…"
    with pytest.raises(AssertionError):
        r = m["grade"](SELF, checks_read_ok=True)
        assert r["state"] == m["NONE_ATTACHED"] and r["arm"] is False


def test_mutation_folding_unreadable_into_none_attached_is_load_bearing():
    m = _mutated('return {\n            "state": UNREADABLE, "arm": False, "attached": None, "self_only": None,\n            "why": "the check-run list could not be READ',
                 'return {\n            "state": NONE_ATTACHED, "arm": False, "attached": 0, "self_only": False,\n            "why": "the check-run list could not be READ')
    assert m["grade"](None, checks_read_ok=False)["state"] == m["NONE_ATTACHED"], \
        "the mutant must collapse the two…"
    with pytest.raises(AssertionError):
        assert m["grade"](None, checks_read_ok=False)["state"] == m["UNREADABLE"]


def test_mutation_reading_a_non_array_as_empty_is_load_bearing():
    m = _mutated("if not isinstance(check_runs, list):",
                 "if False:")
    with pytest.raises((AssertionError, TypeError, AttributeError)):
        r = m["grade"]("0", checks_read_ok=True)
        assert r["state"] == m["UNREADABLE"]


def test_mutation_dropping_the_empty_name_filter_is_load_bearing():
    m = _mutated("named = [n for n in names if n]", "named = list(names)")
    assert m["grade"]([{}, {"name": ""}], checks_read_ok=True)["arm"] is True, \
        "the mutant must arm on nameless stubs…"
    with pytest.raises(AssertionError):
        assert m["grade"]([{}, {"name": ""}], checks_read_ok=True)["arm"] is False
