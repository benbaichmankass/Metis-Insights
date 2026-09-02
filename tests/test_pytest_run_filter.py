"""`pytest-run`'s short-circuit must not skip a tree the suite asserts over.

`BL-20260813-PYTEST-RUN-SHORTCIRCUITS-SO-MAIN-MERGES-UNVERIFIED`.

pytest-run skips the pip-install + test steps when a PR's diff touches nothing
"relevant", to save CI minutes (MB-20260706-CI-MINUTES). The saving is real. The
hazard is that a skipped run reports the SAME green tick as an executed one, so
at the merge button they are indistinguishable — "green is not evidence".

That makes the filter's completeness load-bearing, and it has now failed FOUR
times, each time by omitting a tree the suite reads:

  1. config/            BL-20260707-FASTCI-CONFIG-DRIFT — #5850 and #5851 both
                        merged green while reddening main's full suite.
  2. .github/workflows/ same fix.
  3. scripts/           2026-08-13 — PR #8994 changed ONLY
                        scripts/ops/purge_vm_runner.sh, short-circuited to a
                        NINE-SECOND green pytest-run (a real run is 7-9 min over
                        ~10,600 tests) and merged to main having executed no
                        tests. deploy/ and comms/ were open the same way.
  4. docs/             2026-08-14 — PR #9208 changed ONLY
                        docs/research/exit-refinement-coverage.json, which
                        tests/test_exit_head_per_leg.py reads AS COMMITTED,
                        short-circuited to a TEN-SECOND green pytest-run, merged,
                        and left main red.
                        (BL-20260814-COVERAGE-MATRIX-SHORTCIRCUITS-THE-SUITE-THAT-READS-IT)

Enumerating the trees by hand is what keeps failing, so this test asserts the
filter against the directories the tests demonstrably read. A new tree of
assertions must either be covered or be excluded ON PURPOSE, here, with a
reason.

⚠️ THE HAND-WRITTEN TABLES BELOW ARE STILL THE OLD SHAPE, and instance 4 is what
that costs: every fix so far added the ONE tree just proven, and the next tree
was found by an incident rather than by a check. So the docs/ half is now
DERIVED from the tests — see `test_docs_committed_readers_are_all_covered` at
the bottom. Prefer extending that mechanism over extending the tables.

⚠️ AND NOTE WHAT NONE OF THE FOUR FIXES DID. Instance 3's backlog row required
"pytest-run cannot report a green tick without having executed the suite (or
reports a visibly distinct state when it skips)" and was marked RESOLVED without
that — closed by adding three trees, leaving the green-tick ambiguity that IS the
hazard fully intact. A skipped run and a 10,677-test run still report the same
tick. Until that changes, this class can recur through any tree the allowlist
does not name, and these tables are mitigation, not a fix.
"""
from __future__ import annotations

import ast
import re
import subprocess
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
WORKFLOW = REPO / ".github" / "workflows" / "pytest-run.yml"


def _filter_regex() -> str:
    """The `grep -Eq '...'` pattern that decides relevance."""
    text = WORKFLOW.read_text()
    m = re.search(r"grep -Eq '([^']+)'", text)
    assert m, "could not find the relevance grep in pytest-run.yml"
    return m.group(1)


def _matches(path: str) -> bool:
    return bool(re.search(_filter_regex(), path))


# Trees the suite ASSERTS OVER using non-.py files. A change to any of these can
# redden the suite, so a diff touching them must not short-circuit.
COVERED = {
    "scripts/ops/purge_vm_runner.sh":
        "tests/ops/test_system_actions_workflow.py reads scripts/ops/*.sh (OPS_DIR)",
    "deploy/ict-trader-live.service":
        "tests/test_s012_service_consolidation.py asserts the deploy/*.service set",
    "comms/schema/health_review_response.template.json":
        "the suite validates against the comms/ schema templates",
    "config/strategies.yaml":
        "test_strategy_execution_gate reads config/strategies.yaml",
    ".github/workflows/system-actions.yml":
        "test_system_actions_workflow reads the workflow itself",
    # The docs/ files the suite reads AS COMMITTED, found by scanning
    # tests/ for a docs/ path joined onto the repo root — not by hand, since
    # hand-enumeration is what produced instances 1-4 above. 19 test files carry
    # a docs/ path literal; these are the ones not writing a tmp_path
    # fixture. See test_docs_committed_readers_are_all_covered below, which
    # re-derives this set from the tests on every run so it cannot go stale.
    "docs/research/exit-refinement-coverage.json":
        "test_exit_head_per_leg reads THE REAL matrix (not a fixture); PR #9208 "
        "changed only this file, short-circuited to a TEN-SECOND green "
        "pytest-run, merged, and left main red",
    "docs/claude/impossibility-claim-baseline.json":
        "test_impossibility_claim_ratchet reads the REAL baseline; it records the "
        "ALLOWED per-file claim counts, so a docs-only PR RAISING them is exactly "
        "the diff that must not short-circuit into a green tick",
    "docs/research/m20-sweep-corpus.jsonl":
        "test_m20_regime_book_provenance asserts per-run axis agreement over the "
        "committed corpus",
    "docs/claude/system-actions.md":
        "tests/ops/test_system_actions_workflow reads DOC.read_text() and asserts "
        "the documented allowlist matches the workflow",
    "docs/ARCHITECTURE-CANONICAL.md":
        "test_audit_verification_checklist::test_live_repo_checklist_clean asserts "
        "the LIVE doc has no drift — and the only other check of that property is "
        "the WEEKLY doc-audit-weekly.yml, so without this a docs-only PR can break "
        "it and merge green. Costly (this doc is edited often) and included anyway",
    "docs/claude/SUNSET-DISPOSITIONS.json":
        "test_phase_g_sunset_and_pull::test_the_live_register_and_the_live_pass_agree "
        "runs check_sunset_dispositions.audit over the REAL register. A row claiming "
        "`retired` for a file that still exists is a DOCS-ONLY diff, so without this "
        "the retirement register could assert a removal that never happened and merge "
        "on a green tick — the exact shape of PR #9208, in the mechanism built to stop "
        "things being left behind",
    "docs/claude/CONSTRAINT.json":
        "test_phase_g_sunset_and_pull::test_the_live_constraint_is_the_one_the_guard_"
        "grades reads the REAL readout, which check_capability_pull BRANCHES on "
        "(enforcing / advisory / unknown). It is GENERATED, so it moves whenever the "
        "work store does — a real CI cost, accepted because the alternative is a shape "
        "change breaking the enforcement grading behind a green tick. ⚠️ If it proves "
        "noisy the honest move is DELIBERATELY_EXCLUDED with the `guards owns it` "
        "reason (guards runs the reader unconditionally), never deleting the check",
    "docs/research/m20-exit-head-rounds.jsonl":
        "test_exit_head_round_emits_evidence::test_the_emitted_schema_matches_the_"
        "committed_evidence_file reads THE REAL evidence file, so a change to it "
        "must run the suite that validates its schema. Added 2026-08-14 as the "
        "FIFTH committed docs/ reader — found by this file's derived check on the "
        "same day the file was created, which is the mechanism working: instances "
        "1-4 were each found by an incident instead",
    "src/runtime/order_monitor.py": "python",
    "requirements.txt": "dependency pin",
}

# Excluded ON PURPOSE — large, change on nearly every PR, and their assertions
# belong to the separate `guards` job, which does NOT short-circuit.
DELIBERATELY_EXCLUDED = {
    # VERIFIED 2026-08-14, not assumed: every test that touches this path writes
    # it under a `tmp_path` fixture (test_check_allow_degraded,
    # test_check_backlog_refs, test_check_allow_degraded) or only names it in a
    # docstring — none reads the committed file. So the "guards owns it"
    # premise genuinely holds HERE. It did NOT hold for the exit-coverage
    # matrix, which is why that file moved to COVERED above; the premise is
    # per-file and re-checking it is the point of this table.
    "docs/claude/health-review-backlog.json": "guards job owns doc coherence",
    "data/some_fixture.csv": "bulk data, not asserted structurally by pytest",
    # The rest of docs/research/ stays excluded: the matrix is the one file
    # there the suite reads as-committed, and widening to the tree would pull in
    # every research memo for no assertion.
    "docs/research/exit-refinement-notes.md":
        "prose; no suite assertion reads it",
}


@pytest.mark.parametrize("path,why", sorted(COVERED.items()))
def test_filter_covers_every_tree_the_suite_asserts_over(path, why):
    assert _matches(path), (
        f"pytest-run would SHORT-CIRCUIT on a diff touching {path!r} — but {why}. "
        "A green tick from a run that executed nothing is indistinguishable from "
        "a real pass at the merge button. Add the tree to the grep in "
        ".github/workflows/pytest-run.yml."
    )


@pytest.mark.parametrize("path,why", sorted(DELIBERATELY_EXCLUDED.items()))
def test_deliberate_exclusions_stay_excluded(path, why):
    """Pins the trade-off so it stays a DECISION.

    If someone widens the filter to these, CI minutes regress sharply and the
    short-circuit stops paying for itself. Failing here means: state the new
    reasoning, don't drift into it.
    """
    assert not _matches(path), (
        f"{path!r} is now matched by the pytest-run filter, but it was excluded "
        f"deliberately ({why}). Widening here costs CI minutes on nearly every "
        "PR — if that is intended, update this test with the new reasoning."
    )


def test_the_exact_regression_case_would_now_run():
    """PR #8994's real diff — the one that merged on a 9-second green."""
    assert _matches("scripts/ops/purge_vm_runner.sh")


def test_short_circuit_is_still_possible_at_all():
    """Negative control: if EVERYTHING matched, this test file would pass while
    the CI-minutes saving had been silently destroyed. A guard that cannot fail
    proves nothing."""
    assert not _matches("README.md"), (
        "nothing short-circuits any more — the filter has been widened to "
        "everything, so MB-20260706-CI-MINUTES's saving is gone"
    )


def test_workflow_still_parses():
    yaml.safe_load(WORKFLOW.read_text())


# ---------------------------------------------------------------------------
# The self-maintaining half (2026-08-14,
# BL-20260814-COVERAGE-MATRIX-SHORTCIRCUITS-THE-SUITE-THAT-READS-IT).
#
# The COVERED table above is still hand-written, and hand-enumeration is exactly
# what produced instances 1-4 in this file's header: each was fixed by adding
# the one proven tree, and the NEXT tree was found by an incident rather than by
# a check. So this derives the docs/ half from the tests themselves. A new test
# that reads a committed docs/ file fails here until the filter covers it —
# which is the difference between a list someone must remember to update and a
# property CI enforces.
# ---------------------------------------------------------------------------

# A path joined onto a repo-root-ish name. `tmp_path`/fixture roots are excluded
# by NAME (they are locals like `repo`, `root`, `r`, `tmp_path`), so this matches
# the module-level REPO / REPO_ROOT / _REPO_ROOT idiom the real readers use.
_REPO_DOCS_JOIN = re.compile(
    r'\b_?REPO(?:_ROOT)?\s*/\s*["\']docs["\']'
    r'|\b_?REPO(?:_ROOT)?\s*/\s*["\']docs/'
)
# `REPO / "docs" / "research" / "exit-refinement-coverage.json"` -> the path.
_SEGMENTS = re.compile(r'["\']([A-Za-z0-9_\-.]+)["\']')


def _committed_docs_readers() -> dict[str, set[str]]:
    """{docs path -> {test files that read it as committed}} across tests/."""
    found: dict[str, set[str]] = {}
    for path in sorted((REPO / "tests").rglob("*.py")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not _REPO_DOCS_JOIN.search(line):
                continue
            segs = _SEGMENTS.findall(line)
            if "docs" not in segs:
                continue
            joined = "/".join(segs[segs.index("docs"):])
            found.setdefault(joined, set()).add(
                path.relative_to(REPO).as_posix())
    return found


# ---------------------------------------------------------------------------
# THE GENERAL CASE. The docs/ scan below covers ONE tree. The class it belongs
# to (BL-20260813-...) has now recurred FIVE times, and every fix added the one
# tree just proven while the next was found by an incident. ml/configs/ was the
# fifth: tests/ml/_manifest_paths.py reads it AS COMMITTED and a manifest-only
# PR short-circuited to a 12-second green.
#
# So this derives the covered set from EVERY tree the suite reads, not just
# docs/. A new test that reads any committed path fails here until the filter
# covers it or it is excluded on purpose.
#
# WHY AST AND NOT A REGEX OVER SOURCE TEXT. Four false-positive classes were
# measured while building this, each of which a naive scan reports as a real
# finding:
#   1. on-disk vs COMMITTED — `Path.exists()` counts __pycache__/*.pyc, so
#      src/, tests/ and src/runtime/ all looked uncovered. `git ls-files` is
#      the only correct source of "a PR could change this".
#   2. directory vs file — the filter matches `^scripts/`, so the bare string
#      "scripts" does not match while every file under it does. A directory is
#      covered iff its committed children are.
#   3. DOCSTRINGS — a line-regex matched `_REPO_ROOT / "runtime_logs"` inside a
#      docstring in test_runtime_paths_alignment.py that was *describing the
#      historical bug*. The real code there calls runtime_logs_dir(). An AST
#      walk cannot match prose.
#   4. PREFIX sub-expressions — `ast.walk` visits the inner nodes of a
#      multi-segment join, so a three-segment path also yields its one- and
#      two-segment prefixes as separate hits, inflating the count. Only
#      MAXIMAL chains count.
#      (This comment deliberately does NOT spell that join out: the docs/ scan
#      below is a LINE REGEX, and an earlier draft that wrote the example
#      literally was picked up by it as a real reader of a file named "x" —
#      false-positive class 3, demonstrated by this very comment.)
# A check that over-reports gets muted; these four are why the number below is
# trustworthy.
# ---------------------------------------------------------------------------

_PATH_ROOTS = {"REPO", "REPO_ROOT", "_REPO_ROOT"}


def _tracked() -> set:
    """Committed files. NOT os.listdir — see false-positive class 1 above."""
    out = subprocess.run(["git", "ls-files"], cwd=REPO,
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return set(out.stdout.split())


def _chain(node) -> list | None:
    """`REPO / "a" / "b"` -> ["a", "b"]; None if not rooted at a repo name."""
    segs = []
    while isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        right = node.right
        if not (isinstance(right, ast.Constant) and isinstance(right.value, str)):
            return None
        segs.append(right.value)
        node = node.left
    if isinstance(node, ast.Name) and node.id in _PATH_ROOTS:
        return list(reversed(segs))
    return None


def _committed_readers_any_tree() -> dict:
    """{committed path -> {test files that read it}} over the whole suite."""
    tracked = _tracked()
    found: dict = {}
    for path in sorted((REPO / "tests").rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        inner = {id(n.left) for n in ast.walk(tree)
                 if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Div)
                 and isinstance(n.left, ast.BinOp)}
        for node in ast.walk(tree):
            if not isinstance(node, ast.BinOp) or id(node) in inner:
                continue                      # class 4: maximal chains only
            segs = _chain(node)
            if not segs:
                continue
            rel = "/".join(segs)
            if rel in tracked or any(t.startswith(rel + "/") for t in tracked):
                found.setdefault(rel, set()).add(
                    path.relative_to(REPO).as_posix())
    return found


def _uncovered_children(rel: str, tracked: set) -> list:
    """Committed files under *rel* the filter would let short-circuit."""
    if rel in tracked:                        # class 2: file vs directory
        return [] if _matches(rel) else [rel]
    return sorted(t for t in tracked
                  if t.startswith(rel + "/") and not _matches(t))


def test_the_general_scan_finds_the_tree_that_motivated_it():
    """Negative control FIRST — a scan finding nothing is vacuously green.

    ml/configs/ is the fifth instance of the class and is known to be read by
    tests/ml/_manifest_paths.py, so the scan must see it. If this ever fails,
    the scan is broken and the test below proves nothing.
    """
    found = _committed_readers_any_tree()
    assert "ml/configs" in found, sorted(found)
    assert any("_manifest_paths" in f for f in found["ml/configs"])
    # class 3 control: a docstring mention must NOT be picked up as a read.
    assert "runtime_logs" not in found, (
        "runtime_logs is only named in a DOCSTRING and as a write target; "
        "picking it up means the scan is matching prose again")


def test_every_committed_tree_the_suite_reads_is_covered():
    """No committed path the suite reads may short-circuit pytest-run.

    This is the general form of the docs/-only check below. It exists because
    the class recurred five times, each fix covering only the tree just proven.
    If this fails, add the tree to the grep in pytest-run.yml — do not delete
    the assertion, and do not narrow the scan.
    """
    tracked = _tracked()

    def _excluded(rel: str) -> bool:
        """Honour DELIBERATELY_EXCLUDED rather than silently overriding it.

        A documented decision outranks a derived one UNTIL evidence falsifies
        it — and then the decision is narrowed, not deleted. That happened here:
        `data/` carried "bulk data, not asserted structurally by pytest", but
        this scan measured three files under it that ARE read as committed
        (backtest_candles.csv, btc_1m_sample.csv, ict_validate_manifest.csv).
        The filter now names those three; the bulk tree stays excluded, so the
        original rationale still holds for everything it was actually about.
        """
        for ex in DELIBERATELY_EXCLUDED:
            top = ex.split("/", 1)[0]
            if rel == ex or rel == top or rel.startswith(top + "/"):
                return True
        return False

    uncovered = {
        rel: (sorted(readers), _uncovered_children(rel, tracked))
        for rel, readers in _committed_readers_any_tree().items()
        if not _excluded(rel)
    }
    uncovered = {k: v for k, v in uncovered.items() if v[1]}
    assert not uncovered, (
        "these COMMITTED paths are read by the suite but would SHORT-CIRCUIT "
        "pytest-run, so a PR touching only them merges having executed no "
        f"tests: { {k: (v[0], v[1][:3]) for k, v in uncovered.items()} }"
    )


def test_the_scan_finds_the_reader_we_already_know_about():
    """Negative control FIRST: a scan that finds nothing would make the test
    below vacuously green — the 'unasserted denominator' shape. The matrix
    reader is known to exist, so the scan must see it."""
    found = _committed_docs_readers()
    assert "docs/research/exit-refinement-coverage.json" in found, sorted(found)
    assert any("test_exit_head_per_leg" in f
               for f in found["docs/research/exit-refinement-coverage.json"])


def test_docs_committed_readers_are_all_covered():
    """EVERY committed docs/ file the suite reads must defeat the short-circuit.

    If this fails, a test is asserting over a docs/ file that a docs-only PR can
    change while `pytest-run` reports a green tick without executing anything.
    Fix by adding the path to the grep in .github/workflows/pytest-run.yml and
    to COVERED above — not by deleting the assertion.
    """
    uncovered = {
        path: sorted(readers)
        for path, readers in _committed_docs_readers().items()
        if not _matches(path)
    }
    assert not uncovered, (
        "these docs/ files are read as COMMITTED by the suite but would "
        f"SHORT-CIRCUIT pytest-run: {uncovered}. A green tick from a run that "
        "executed nothing is indistinguishable from a real pass at the merge "
        "button (this is how PR #9208 merged and left main red)."
    )
