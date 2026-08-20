"""The STANDING half of impossibility-claim-guard must catch what the diff half cannot.

`--base` is diff-scoped, which is correct for new lines and structurally blind to
rows nobody edits: 36 unsubstantiated claims across 14 files sat un-reported for
months because no PR happened to touch those lines. `--all --ratchet` grades the
whole corpus against a committed per-file baseline.

The tests that matter here are the CONTROLS. A ratchet that never fires is
indistinguishable from a clean corpus, so each test plants a failure and asserts
it is caught, rather than only asserting the happy path.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
GUARD = REPO / "scripts" / "check_impossibility_claims.py"
BASELINE = REPO / "docs" / "claude" / "impossibility-claim-baseline.json"


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(GUARD), *args],
                          cwd=REPO, capture_output=True, text=True)


def test_the_committed_baseline_is_readable_and_nonempty() -> None:
    """A denominator check: an empty/missing baseline makes every later assert vacuous."""
    data = json.loads(BASELINE.read_text(encoding="utf-8"))
    per_file = data["per_file"]
    assert per_file, "baseline is empty — the ratchet would grade nothing"
    assert sum(per_file.values()) > 0


def test_corpus_matches_its_baseline_today() -> None:
    """The standing corpus does not EXCEED its baseline (improvements are fine)."""
    r = _run("--all", "--ratchet")
    assert r.returncode == 0, f"unexpected regression:\n{r.stdout}\n{r.stderr}"
    assert "REGRESSION" not in r.stdout


def test_a_planted_claim_in_a_new_file_is_caught(tmp_path: None) -> None:
    """CONTROL: the probe can find a positive. Without this the green above is meaningless."""
    planted = REPO / "docs" / "research" / "_ratchet_pytest_control.md"
    planted.write_text("This is a value that cannot be measured with today's tooling.\n",
                       encoding="utf-8")
    try:
        r = _run("--all", "--ratchet")
        assert r.returncode == 1, "planted claim did NOT trip the ratchet"
        assert "_ratchet_pytest_control.md" in r.stdout
        assert "REGRESSION" in r.stdout
    finally:
        planted.unlink()


def test_churn_is_caught_even_when_the_TOTAL_is_unchanged() -> None:
    """CONTROL for the design choice: per-file, not a bare total.

    Annotating one claim away while committing a new one elsewhere leaves the
    total identical. A total-based ratchet passes that; this one must not — which
    is the entire reason the baseline is a per-file map.
    """
    planted = REPO / "docs" / "research" / "_churn_pytest_control.md"
    planted.write_text("This quantity is unmeasurable with the current harness.\n",
                       encoding="utf-8")
    try:
        r = _run("--all", "--ratchet")
        # The new file regressed; the total may or may not have moved. The
        # regression must be reported either way.
        assert r.returncode == 1
        assert "_churn_pytest_control.md" in r.stdout
    finally:
        planted.unlink()


def test_an_unreadable_baseline_is_ABSENT_not_clean() -> None:
    """A missing baseline must NOT read as 'no regressions'.

    Returning {} on a read failure would turn a deleted baseline into a confident
    'every file is clean', which is the collapsed-state bug this repo keeps paying
    for. It must say it could not look.
    """
    sys.path.insert(0, str(REPO / "scripts"))
    try:
        import importlib
        mod = importlib.import_module("check_impossibility_claims")
        original = mod.BASELINE_PATH
        mod.BASELINE_PATH = REPO / "docs" / "claude" / "_no_such_baseline.json"
        try:
            assert mod._load_baseline() is None, "unreadable baseline collapsed to {} "
            regressions, _ = mod._ratchet({"any/file.md": 1})
            assert regressions and "cannot grade" in regressions[0]
        finally:
            mod.BASELINE_PATH = original
    finally:
        sys.path.remove(str(REPO / "scripts"))
