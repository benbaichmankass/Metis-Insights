"""E63: a `landing: "hold"` PR was armed and merged anyway.

PR #12858 declared `.github/pr-landing/tender-mayer-2g1vtq.json` as
`{"tier": 1, "landing": "hold", "hold_reason": "changes_landing_machinery", ...}`.
`pr-landing-guard` graded that head `declared_hold` and exited 0 — correctly,
a hold is a valid non-blocking state. `claude-pr-automerge.yml` armed
auto-merge on that exact head anyway (job 107654502596, 2026-09-24T13:30:21Z:
"auto-merge enabled on #12858 — merges when CI is green"), and
`github-actions[bot]` squash-merged it at 13:53:00Z. MEASURED, not inferred:
the workflow's own run log names the head and the decision.

Root cause: nothing in the workflow ever read the landing declaration at all.
This module is the pure decision the workflow now consults before arming.

Run: ``python3 -m pytest tests/test_automerge_landing_gate.py``
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "ci" / "automerge_landing_gate.py"


def _load():
    spec = importlib.util.spec_from_file_location("_automerge_landing_gate", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


G = _load()


def test_self_test_passes():
    ok, fails = G._self_test(quiet=True)
    assert ok, f"automerge-landing-gate self-test failures: {fails}"


# ── THE LOAD-BEARING REFUSAL: the exact #12858 fixture ─────────────────────

def test_e63_fixture_the_12858_shape_refuses_to_arm():
    """The PLANTED FIXTURE reproducing PR #12858's own committed declaration:
    `.github/pr-landing/tender-mayer-2g1vtq.json` at the head that was armed
    and merged."""
    fixture = {
        "tier": 1,
        "landing": "hold",
        "why": "Manager integration PR...",
        "hold_reason": "changes_landing_machinery",
        "hold_text": "Held for one operator read per R12.",
    }
    r = G.grade(found=True, parse_ok=True, landing=fixture["landing"])
    assert r["state"] == G.NOT_SELF
    assert r["arm"] is False, "PR #12858's exact declaration must refuse to arm"


def test_a_naive_presence_check_would_have_passed_the_case_that_matters():
    """Stated as a test so the #12858 regression cannot come back quietly: a
    gate that only checked *whether a declaration file exists* (rather than
    what it says) would have seen `found=True` here and waved it through."""
    r = G.grade(found=True, parse_ok=True, landing="hold")
    assert r["arm"] is False, "presence alone is not enough — the VALUE must be self"


def test_the_refusal_names_the_incident_so_a_reader_can_find_it():
    why = G.grade(found=True, parse_ok=True, landing="hold")["why"]
    assert "12858" in why and "hold" in why


# ── the arming case ─────────────────────────────────────────────────────────

def test_landing_self_arms():
    r = G.grade(found=True, parse_ok=True, landing="self")
    assert r["state"] == G.SELF and r["arm"] is True


@pytest.mark.parametrize("bad", [None, "", "Self", "SELF", "hold", "hold ",
                                  "unvouchable_paths", 1, True, ["self"], {}])
def test_anything_other_than_exactly_self_refuses(bad):
    r = G.grade(found=True, parse_ok=True, landing=bad)
    assert r["state"] == G.NOT_SELF and r["arm"] is False


# ── absence is permissive, matching pr-landing-guard's own grandfathering ──

def test_no_declaration_file_at_all_arms():
    r = G.grade(found=False, parse_ok=False, landing=None)
    assert r["state"] == G.ABSENT and r["arm"] is True


# ── "we could not look" is its own state, and it refuses ───────────────────

def test_an_unparseable_declaration_is_unreadable_never_absent():
    r = G.grade(found=True, parse_ok=False, landing=None)
    assert r["state"] == G.UNREADABLE
    assert r["state"] != G.ABSENT
    assert r["arm"] is False
    assert "did not look" in r["why"]


# ── contract ────────────────────────────────────────────────────────────────

def test_arm_is_true_for_exactly_two_states():
    got = [G.grade(found=True, parse_ok=True, landing="self")["arm"],
           G.grade(found=False, parse_ok=False, landing=None)["arm"],
           G.grade(found=True, parse_ok=True, landing="hold")["arm"],
           G.grade(found=True, parse_ok=False, landing=None)["arm"]]
    assert got == [True, True, False, False]


def test_all_four_states_are_reachable_so_none_is_decorative():
    reached = {
        G.grade(found=True, parse_ok=True, landing="self")["state"],
        G.grade(found=True, parse_ok=True, landing="hold")["state"],
        G.grade(found=False, parse_ok=False, landing=None)["state"],
        G.grade(found=True, parse_ok=False, landing=None)["state"],
    }
    assert reached == set(G.ALL_STATES)


def test_there_is_no_override_that_forces_arming():
    src = SCRIPT.read_text(encoding="utf-8")
    for word in ("--force", "FORCE_ARM", "skip_gate", "ARM_ANYWAY"):
        assert word not in src, f"an override ({word}) makes the gate optional"


# ── the CLI, which is what the workflow actually runs ───────────────────────

def _run(payload: str):
    return subprocess.run([sys.executable, str(SCRIPT)], input=payload,
                          capture_output=True, text=True)


def test_cli_exits_0_when_it_arms_and_3_when_it_refuses():
    ok = _run(json.dumps({"found": True, "parse_ok": True, "landing": "self"}))
    no = _run(json.dumps({"found": True, "parse_ok": True, "landing": "hold"}))
    assert ok.returncode == 0 and json.loads(ok.stdout)["arm"] is True
    assert no.returncode == 3 and json.loads(no.stdout)["arm"] is False


def test_cli_refuses_on_unparseable_own_input():
    r = _run("{not json")
    assert r.returncode == 3
    assert json.loads(r.stdout)["state"] == G.UNREADABLE


def test_cli_refuses_a_payload_that_is_not_an_object():
    r = _run("[1,2,3]")
    assert r.returncode == 3 and json.loads(r.stdout)["arm"] is False


def test_cli_self_test_exits_zero():
    r = subprocess.run([sys.executable, str(SCRIPT), "--self-test"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


# ── mutation: break the predicate in isolation, the suite must go RED ──────

def _mutated(old: str, new: str):
    src = SCRIPT.read_text(encoding="utf-8")
    assert old in src, f"mutation anchor is stale: {old!r}"
    ns: dict = {}
    exec(compile(src.replace(old, new, 1), "<mutant>", "exec"), ns)
    return ns


def test_mutation_treating_presence_as_enough_is_load_bearing():
    """The exact #12858 regression: a mutant that arms on ANY declared value
    (not just "self") must arm on "hold" — and the fixed module must not."""
    m = _mutated('    if landing == SELF:',
                 '    if landing:')
    assert m["grade"](found=True, parse_ok=True, landing="hold")["arm"] is True, \
        "the mutant reproduces the #12858 bug…"
    with pytest.raises(AssertionError):
        r = m["grade"](found=True, parse_ok=True, landing="hold")
        assert r["arm"] is False


def test_mutation_folding_unreadable_into_absent_is_load_bearing():
    m = _mutated("if not found:", "if not (found and parse_ok):")
    assert m["grade"](found=True, parse_ok=False, landing=None)["arm"] is True, \
        "the mutant must arm on an unreadable declaration…"
    with pytest.raises(AssertionError):
        r = m["grade"](found=True, parse_ok=False, landing=None)
        assert r["arm"] is False
