"""`check_guard_selftest_coverage.py` — the failure path, planted and asserted.

This file exists in ADDITION to the guard's own `--self-test`, not instead of
it. The two answer different questions and the distinction is the subject of
the guard itself: the `--self-test` proves the failure path resolves inside the
required `guards` merge context; this file runs in `pytest-run`, the other
required context, and pins the properties a future edit is most likely to
quietly break.

Everything below asserts on a RETURN VALUE. A test that merely imports the
module and checks it parses would be the presence-only failure the guard exists
to report.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
GUARD = REPO / "scripts" / "ci" / "check_guard_selftest_coverage.py"


def _load():
    spec = importlib.util.spec_from_file_location("_gsc", GUARD)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


g = _load()


BARE = "import sys\nprint('ok')\nsys.exit(0)\n"
WIRED = (
    "import argparse, sys\n"
    "def self_test():\n"
    "    if main([]) != 0:\n"
    "        return 1\n"
    "    return 0\n"
    "def main(argv=None):\n"
    "    ap = argparse.ArgumentParser()\n"
    "    ap.add_argument('--self-test', action='store_true')\n"
    "    a = ap.parse_args(argv)\n"
    "    return self_test() if a.self_test else 0\n"
)


def _repo(tmp_path: Path, *, guard: str, steps: str, registry: str = "SELFTESTS = {}\n",
          tests: str = "") -> Path:
    (tmp_path / "scripts" / "ci").mkdir(parents=True, exist_ok=True)
    (tmp_path / "tests").mkdir(parents=True, exist_ok=True)
    (tmp_path / "scripts" / "ci" / "check_planted.py").write_text(guard)
    (tmp_path / "scripts" / "ci" / "run_guards.py").write_text("GUARDS = [\n" + steps + "]\n")
    (tmp_path / "scripts" / "ci" / "guard_selftests.py").write_text(registry)
    if tests:
        (tmp_path / "tests" / "test_planted.py").write_text(tests)
    return tmp_path


STEP_PLAIN = '    ["python3", "scripts/ci/check_planted.py"],\n'
STEP_WRAPPED = ('    ["python3", "scripts/ci/check_planted.py",\n'
                '     "--self-test"],\n')


def test_the_guards_own_self_test_passes():
    """The guard's planted-failure controls must actually pass, here too.

    `run_guards.py` runs this same entry point, so a break shows up in both
    required contexts rather than only the one someone happens to read.
    """
    assert g.main(["--self-test"]) == 0


def test_the_repo_currently_passes():
    assert g.main([]) == 0


def test_a_guard_with_no_failure_path_is_a_violation(tmp_path):
    """THE PLANT. This is the whole point of the instrument."""
    root = _repo(tmp_path, guard=BARE, steps=STEP_PLAIN)
    rows = g.assess(root)
    assert [r["population"] for r in rows] == ["none"]
    violations, _ = g.evaluate(rows, {}, floor=0)
    assert any("[uncovered]" in v for v in violations)


def test_removing_the_plant_clears_it(tmp_path):
    """Without this half, a guard that failed unconditionally would look sound."""
    root = _repo(tmp_path, guard=WIRED, steps=STEP_WRAPPED)
    assert g.evaluate(g.assess(root), {}, floor=1)[0] == []


def test_a_wrapped_argv_still_counts_as_invoked(tmp_path):
    """The F-61 control, and the reason this instrument reads the AST.

    F-61 of the 2026-09-09 audit reported four guards as `invoked=0` when all
    four ARE invoked with `--self-test`; their argv is wrapped across two source
    lines, and the probe was a single-line grep. It even had a working positive
    control (a guard whose argv fits on one line) and was still wrong. If this
    test ever fails, the instrument has regressed into that same defect.
    """
    root = _repo(tmp_path, guard=WIRED, steps=STEP_WRAPPED)
    assert [r["population"] for r in g.assess(root)] == ["guards_context"]


def test_a_pytest_file_is_never_counted_as_covered(tmp_path):
    """`pytest_only` is UNPROVEN, not disproven — and never summed into covered.

    Naming a module is not planting a violation. The audit stated this limit
    about its own 21 rows and this instrument keeps it stated; collapsing the
    two populations is exactly how a coverage number stops being true.
    """
    root = _repo(tmp_path, guard=BARE, steps=STEP_PLAIN, tests="import check_planted\n")
    rows = g.assess(root)
    assert [r["population"] for r in rows] == ["pytest_only"]
    assert g.evaluate(rows, {}, floor=0)[1]["guards_context"] == 0


def test_a_declared_but_uninvoked_self_test_is_not_coverage(tmp_path):
    root = _repo(tmp_path, guard=WIRED, steps=STEP_PLAIN)
    rows = g.assess(root)
    assert rows[0]["population"] == "none"
    _, stats = g.evaluate(rows, {}, floor=0)
    assert stats["declared_but_never_invoked"] == ["scripts/ci/check_planted.py"]


@pytest.mark.parametrize("radius", ["accounting", "money-at-risk", "money_at_risk"])
def test_a_money_blast_radius_may_not_be_exempted(tmp_path, radius):
    """F-01's hard rule. A gate over the books may not ship unexercised."""
    root = _repo(tmp_path, guard=BARE, steps=STEP_PLAIN)
    ex = {"scripts/ci/check_planted.py": {
        "reason": "r", "added": "2026-09-09", "blast_radius": radius,
        "tracking_id": "MI-226"}}
    violations, _ = g.evaluate(g.assess(root), ex, floor=0)
    assert any("[exemption-refused]" in v and radius in v for v in violations)


def test_a_reachable_guard_may_not_be_exempted_even_declaring_docs(tmp_path):
    """The rule that makes the rule above enforceable.

    `blast_radius` is written by whoever writes the exemption, so gating on it
    alone would be cheaper to lie to than to satisfy — the presence-only marker
    failure this repo already paid for. Reachability is a fact about
    `run_guards.py` and cannot be declared away.
    """
    root = _repo(tmp_path, guard=BARE, steps=STEP_PLAIN)
    ex = {"scripts/ci/check_planted.py": {
        "reason": "r", "added": "2026-09-09", "blast_radius": "docs",
        "tracking_id": "MI-226"}}
    violations, _ = g.evaluate(g.assess(root), ex, floor=0)
    assert any("REACHABLE" in v for v in violations)


def test_an_unreachable_guard_is_exemptible(tmp_path):
    """...or the rule is a blanket ban and nobody could ever land an exemption."""
    root = _repo(tmp_path, guard=BARE, steps="")
    ex = {"scripts/ci/check_planted.py": {
        "reason": "r", "added": "2026-09-09", "blast_radius": "docs",
        "tracking_id": "MI-226"}}
    assert g.evaluate(g.assess(root), ex, floor=0)[0] == []


def test_a_stale_exemption_is_a_finding(tmp_path):
    """A stale exemption silently pre-approves a guard that may have regressed."""
    root = _repo(tmp_path, guard=WIRED, steps=STEP_WRAPPED)
    ex = {"scripts/ci/check_planted.py": {
        "reason": "r", "added": "2026-09-09", "blast_radius": "docs",
        "tracking_id": "MI-226"}}
    violations, _ = g.evaluate(g.assess(root), ex, floor=1)
    assert any("[stale-exemption]" in v for v in violations)


def test_the_ratchet_only_moves_up(tmp_path):
    root = _repo(tmp_path, guard=WIRED, steps=STEP_WRAPPED)
    rows = g.assess(root)
    assert any("[ratchet]" in v for v in g.evaluate(rows, {}, floor=99)[0])
    assert not any("[ratchet]" in v for v in g.evaluate(rows, {}, floor=1)[0])


def test_the_denominator_is_the_filesystem_not_a_registry(tmp_path):
    """The single property this instrument exists for.

    `check_selftest_wiring.py` measures the registry — deliberately, and it says
    so — which is why 8 guards were invisible to it. A guard on disk that is in
    no registry and in no `run_guards.py` step must still be COUNTED.
    """
    root = _repo(tmp_path, guard=BARE, steps="")
    (root / "scripts" / "check_second.py").write_text(BARE)
    assert g.evaluate(g.assess(root), {}, floor=0)[1]["total"] == 2


def test_a_malformed_exemption_file_is_reported_never_read_as_empty(tmp_path):
    """Reading an unparseable file as "nothing is exempt" would flip the verdict."""
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert g.load_exemptions(bad)[1] != []


def test_every_exemption_field_is_required(tmp_path):
    p = tmp_path / "inc.json"
    p.write_text(json.dumps({"exemptions": [{"guard": "g", "reason": "r"}]}))
    problems = g.load_exemptions(p)[1]
    assert len(problems) == 3  # added, blast_radius, tracking_id


def test_an_unparseable_added_date_is_reported(tmp_path):
    p = tmp_path / "undated.json"
    p.write_text(json.dumps({"exemptions": [{
        "guard": "g", "reason": "r", "added": "soon",
        "blast_radius": "docs", "tracking_id": "MI-226"}]}))
    assert any("unparseable" in x for x in g.load_exemptions(p)[1])


def test_the_committed_exemption_file_is_well_formed():
    """The live file, not a fixture — a broken one exits 2 on every PR."""
    assert g.load_exemptions(g.EXEMPTIONS)[1] == []


def test_a_print_only_self_test_is_flagged(tmp_path):
    printer = (
        "import argparse, sys\n"
        "def self_test():\n"
        "    print('PASS everything is fine')\n"
        "    return 0\n"
        "def main(argv=None):\n"
        "    ap = argparse.ArgumentParser()\n"
        "    ap.add_argument('--self-test', action='store_true')\n"
        "    a = ap.parse_args(argv)\n"
        "    return self_test() if a.self_test else 0\n"
    )
    root = _repo(tmp_path, guard=printer, steps=STEP_WRAPPED)
    assert any("[no-assertion]" in v for v in g.evaluate(g.assess(root), {}, floor=1)[0])


def test_an_assertion_one_call_away_still_counts(tmp_path):
    """A measured false positive on this script's own first run.

    `check_one_live_workplan.py` and `check_automerge_trigger.py` both plant
    real defects and assert on them through an `_expect(...)` helper rather than
    an inline comparison. Reading only the self-test's own body reported four
    correctly-armed guards as decoration — a claim about the extractor, not
    about the guards.
    """
    helper = (
        "import argparse, sys\n"
        "def _expect(got, want):\n"
        "    if got != want:\n"
        "        raise SystemExit('mismatch')\n"
        "def self_test():\n"
        "    _expect(main([]), 0)\n"
        "    return 0\n"
        "def main(argv=None):\n"
        "    ap = argparse.ArgumentParser()\n"
        "    ap.add_argument('--self-test', action='store_true')\n"
        "    a = ap.parse_args(argv)\n"
        "    return self_test() if a.self_test else 0\n"
    )
    root = _repo(tmp_path, guard=helper, steps=STEP_WRAPPED)
    assert g.evaluate(g.assess(root), {}, floor=1)[0] == []


def test_an_inline_self_test_branch_still_counts(tmp_path):
    """`check_matrix_corpus_agreement.py` runs its controls inside `main()`.

    Requiring a function of a particular NAME would report that guard as
    decoration — again a claim about the extractor rather than the guard.
    """
    inline = (
        "import argparse, sys\n"
        "def main(argv=None):\n"
        "    ap = argparse.ArgumentParser()\n"
        "    ap.add_argument('--self-test', action='store_true')\n"
        "    a = ap.parse_args(argv)\n"
        "    if a.self_test:\n"
        "        assert len([1]) == 1\n"
        "        return 0\n"
        "    return 0\n"
    )
    root = _repo(tmp_path, guard=inline, steps=STEP_WRAPPED)
    assert g.evaluate(g.assess(root), {}, floor=1)[0] == []
