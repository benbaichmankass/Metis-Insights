"""The STANDING half of impossibility-claim-guard must catch what the diff half cannot.

`--base` is diff-scoped, which is correct for new lines and structurally blind to
rows nobody edits: 36 unsubstantiated claims across 14 files sat un-reported for
months because no PR happened to touch those lines. `--all --ratchet` grades the
whole corpus against a committed per-file baseline.

The tests that matter here are the CONTROLS. A ratchet that never fires is
indistinguishable from a clean corpus, so the planted cases assert a failure IS
caught rather than only asserting the happy path.

⚠️ `docs/claude/impossibility-claim-baseline.json` is read AS COMMITTED below, so
it is enumerated in the `pytest-run.yml` relevance grep and in
`test_pytest_run_filter.COVERED`. That is load-bearing, not bookkeeping: the
baseline records the ALLOWED per-file counts, so a docs-only PR that RAISES them
is exactly the change that must not short-circuit `pytest-run` into a green tick
from a run that executed nothing (the PR #9208 shape).
"""
from __future__ import annotations

import importlib
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
GUARD = REPO / "scripts" / "check_impossibility_claims.py"
BASELINE = REPO / "docs" / "claude" / "impossibility-claim-baseline.json"

# The guard only scans `docs/research/**.md` + the three review backlogs, so an
# end-to-end planted control MUST be written inside that tree — there is nowhere
# else it would be seen. Built with `.joinpath()` rather than the repo-root-slash
# -docs-literal idiom DELIBERATELY: `test_pytest_run_filter` scans tests/ line by
# line (comments included) for that idiom to find files read AS COMMITTED, and
# spelling it out here would itself register as a reader. This one is the opposite of
# committed — it is created and deleted inside a single test and can never appear
# in a PR diff. Listing it as "covered" would put a path that does not exist into
# the relevance grep. The genuinely-committed baseline above IS listed there.
_PLANTED = REPO.joinpath("docs", "research", "_ratchet_pytest_control.md")


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(GUARD), *args],
                          cwd=REPO, capture_output=True, text=True)


def _guard_module():
    sys.path.insert(0, str(REPO / "scripts"))
    try:
        return importlib.import_module("check_impossibility_claims")
    finally:
        sys.path.remove(str(REPO / "scripts"))


def test_the_committed_baseline_is_readable_and_nonempty() -> None:
    """Denominator check: an empty/missing baseline makes every later assert vacuous."""
    per_file = json.loads(BASELINE.read_text(encoding="utf-8"))["per_file"]
    assert per_file, "baseline is empty — the ratchet would grade nothing"
    assert sum(per_file.values()) > 0


def test_corpus_does_not_exceed_its_baseline_today() -> None:
    """The standing corpus does not EXCEED its baseline (improvements are fine)."""
    r = _run("--all", "--ratchet")
    assert r.returncode == 0, f"unexpected regression:\n{r.stdout}\n{r.stderr}"
    assert "REGRESSION" not in r.stdout


def test_a_planted_claim_in_a_new_file_is_caught() -> None:
    """CONTROL, end-to-end: the probe can find a positive.

    Without this the green above is meaningless — it would be satisfied by a
    ratchet that never fires at all.
    """
    _PLANTED.write_text(
        "This is a value that cannot be measured with today's tooling.\n",
        encoding="utf-8")
    try:
        r = _run("--all", "--ratchet")
        assert r.returncode == 1, f"planted claim did NOT trip the ratchet:\n{r.stdout}"
        assert _PLANTED.name in r.stdout
        assert "REGRESSION" in r.stdout
    finally:
        _PLANTED.unlink(missing_ok=True)


def test_churn_is_caught_even_when_the_TOTAL_is_unchanged() -> None:
    """CONTROL for the design choice: per-file, not a bare total.

    Annotating one claim away while committing a new one elsewhere leaves the
    total identical. A total-based ratchet passes that; this one must not, which
    is the entire reason the baseline is a per-file map. Asserted at the function
    level over synthetic counts so it states the property directly, with no
    dependence on which real files happen to carry claims today.
    """
    mod = _guard_module()
    base = json.loads(BASELINE.read_text(encoding="utf-8"))["per_file"]
    victim = next(iter(base))                       # a file with a known count
    churned = dict(base)
    churned[victim] = base[victim] - 1              # one annotated away
    churned["docs/research/_synthetic_new.md"] = 1  # one added elsewhere
    assert sum(churned.values()) == sum(base.values()), "control is not churn-neutral"

    regressions, improvements = mod._ratchet(churned)
    assert regressions, "churn at a constant total slipped through — per-file failed"
    assert any("_synthetic_new" in r for r in regressions)
    assert any(victim in i for i in improvements)


def test_an_unreadable_baseline_is_ABSENT_not_clean(tmp_path) -> None:
    """A missing baseline must NOT read as 'no regressions'.

    Returning {} on a read failure would turn a deleted baseline into a confident
    'every file is clean' — the collapsed-state bug this repo keeps paying for.
    It must say it could not look. The substitute path is a `tmp_path` fixture,
    so this is not a committed-docs read.
    """
    mod = _guard_module()
    original = mod.BASELINE_PATH
    mod.BASELINE_PATH = tmp_path / "no_such_baseline.json"
    try:
        assert mod._load_baseline() is None, "unreadable baseline collapsed to {}"
        regressions, _ = mod._ratchet({"any/file.md": 1})
        assert regressions and "cannot grade" in regressions[0]
    finally:
        mod.BASELINE_PATH = original
