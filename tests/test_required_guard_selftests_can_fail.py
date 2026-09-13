"""The four required guards' self-tests must be able to PRINT `SELF-TEST FAILED`.

WHY THIS EXISTS.
  `BL-20260909-FOUR-GUARDS-IN-THE-REQUIRED-MERGE-CONTEXT-HAVE-NO-FAILURE-PATH-EVIDENCE-AT-ALL`
  named four guards inside the REQUIRED `guards` merge context whose greens had
  never been shown capable of turning red. MI-226 (#11592) gave each of them a
  planted-defect `--self-test`. This file is the level above: a self-test that
  prints `ALL PASS` proves nothing until it has been seen to print
  `SELF-TEST FAILED`, so each guard's own detection is neutered here and its
  self-test is REQUIRED to notice.

  Without this, the four self-tests are themselves unexercised instruments —
  which is the exact shape of the finding they were written to close.

HOW THE PLANT WORKS, AND WHY IT IS NOT A FILE EDIT.
  Every one of the four calls the module-global `main(...)` from inside
  `self_test()`, so replacing `module.main` with a function that always returns
  0 is a total, faithful defect: the guard detects exactly as before and then
  reports success anyway. Nothing on disk is touched, so a failing run cannot
  leave the tree dirty and two runs cannot race each other.

⚠️ A PLANT THAT DOES NOT PLANT THE DEFECT IS INDISTINGUISHABLE FROM ONE THAT
   ESCAPES, and this file was built after walking into exactly that. The first
   attempt inserted `return 0` at the top of `main()` on disk; three of these
   four DISPATCH `--self-test` from inside `main()`, so the plant
   short-circuited the self-test itself — it printed nothing, exited 0, and read
   as `3 of 4 ESCAPED`. Hence `test_the_selftest_actually_runs_under_the_plant`:
   silence is graded INVALID, never as a pass and never as an escape.

⚠️ STATED LIMIT — WHAT THIS FILE DOES *NOT* DETECT.
   It proves a self-test can go red. It does NOT prove every one of that
   self-test's controls is load-bearing. MEASURED while building it: decoupling
   ONE of `check_provenance_consumers`'s four controls from `main()` ESCAPED,
   because a second control there also expects a 1 and still caught the
   neutering; the plant had to be a TWO-edit one before this file noticed. So a
   PARTIALLY decorative self-test — one live control carrying three dead ones —
   passes here. That population is
   `BL-20260909-ONLY-17-OF-58-WIRED-GUARD-SELFTESTS-ASSERT-ON-A-RETURN-CODE-SO-F04S-STATED-DETECTOR-COULD-NOT-BE-MADE-A-GATE`,
   and it is a different instrument's job.

PLANTED-DEFECT EVIDENCE FOR THIS FILE ITSELF (re-runnable, 2026-09-12):
   P1  every return-1 control decoupled from main()  -> CAUGHT
   P2  a control's expectation corrupted so the guard is red pre-plant -> CAUGHT
   P3  the self-test fails without printing SELF-TEST FAILED -> CAUGHT
   positive control on the unplanted tree -> 12 passed, quiet.
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys
import types

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]

# The four guards named by the row, each inside the required `guards` job.
GUARDS = [
    "scripts/check_provenance_consumers.py",
    "scripts/check_strategy_coverage.py",
    "scripts/ci/check_news_feed_coverage.py",
    "scripts/ci/check_workflow_failure_swallow.py",
]


def _load(rel: str) -> types.ModuleType:
    path = REPO / rel
    assert path.is_file(), f"{rel} does not exist — the row's population has moved"
    spec = importlib.util.spec_from_file_location(f"_guard_{path.stem}", path)
    mod = importlib.util.module_from_spec(spec)
    # Registered so dataclasses / typing lookups inside the guard resolve.
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _ran(out: str) -> bool:
    """Did the self-test actually execute? Silence is NOT a pass."""
    return any(line.strip().startswith(("PASS -", "FAIL -")) for line in out.splitlines())


@pytest.mark.parametrize("rel", GUARDS)
def test_the_guard_exposes_a_self_test_and_it_is_green(rel, capsys):
    """PRE-PLANT CONTROL. A red-after-plant proves nothing if it was already red."""
    mod = _load(rel)
    assert hasattr(mod, "self_test"), f"{rel} has no self_test() — the row has regressed"
    assert mod.self_test() == 0, f"{rel}'s self-test is failing BEFORE any plant"
    assert _ran(capsys.readouterr().out), f"{rel}'s self-test printed no control lines"


@pytest.mark.parametrize("rel", GUARDS)
def test_neutering_the_guard_makes_its_own_self_test_fail(rel, capsys, monkeypatch):
    """THE PLANT. main() always reports success; the self-test must refuse."""
    mod = _load(rel)
    assert mod.self_test() == 0, "pre-plant control"
    capsys.readouterr()

    monkeypatch.setattr(mod, "main", lambda *a, **k: 0)
    rc = mod.self_test()
    out = capsys.readouterr().out

    assert _ran(out), (
        f"{rel}: the self-test produced NO control lines under the plant, so this "
        f"run grades NOTHING. Do not read it as an escape — the plant did not land."
    )
    assert rc != 0, (
        f"{rel}: its detection was neutered and its own self-test still passed. "
        f"Its green is not evidence."
    )
    assert "SELF-TEST FAILED" in out, f"{rel}: went non-zero without saying so"
    assert any(line.strip().startswith("FAIL -") for line in out.splitlines()), (
        f"{rel}: failed without NAMING which control caught the plant"
    )


@pytest.mark.parametrize("rel", GUARDS)
def test_an_inert_plant_leaves_the_self_test_green(rel, capsys, monkeypatch):
    """NEGATIVE CONTROL.

    Proves the test above fails on the DEFECT and not merely on the act of
    monkeypatching `main`. An identity wrapper is patched in — same indirection,
    no behaviour change — and the self-test must stay green.
    """
    mod = _load(rel)
    real = mod.main
    monkeypatch.setattr(mod, "main", lambda *a, **k: real(*a, **k))
    assert mod.self_test() == 0, (
        f"{rel}: an identity wrapper around main() turned its self-test red, so the "
        f"plant above is detecting the wrapper rather than the defect"
    )
    assert _ran(capsys.readouterr().out)
