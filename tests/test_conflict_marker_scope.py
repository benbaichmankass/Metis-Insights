"""The conflict-marker scan's corpus is a DECISION, and the false positive is the hard half.

`BL-20260913-THE-CONFLICT-MARKER-REFUSAL-WAS-RULED-FOR-TWO-REGISTER-FAMILIES-AND-NEVER-EXTENDED-TO-THE-CANONICAL-PROSE-A-SESSION-READS`.

The row was filed by PLANTING a committed conflict block in five files and
running the full registry: `docs/DOCUMENT-INDEX.md` was caught (R7), and
`CLAUDE.md`, `ROADMAP.md`, `docs/CLAUDE-RULES-CANONICAL.md` and
`docs/ARCHITECTURE-CANONICAL.md` were **not**. The first four are covered now.
Its criterion is that somebody DECIDE the repo-wide scope — *"a declared path
set with a stated override … or an explicit ruling that the corpus now covered
is the intended stopping point."*

**The decision: extend to every prose document a session may read.** What
decided it was the instruction hierarchy's own **level 4** — `docs/sprint-logs/`,
309 files, entirely uncovered, so "the current sprint log" could carry both
sides of a contested edit and pass every guard in the repo.

⚠️ **The row is explicit that a green guard does not close it:** any extension
must be shown FIRING on a planted marker AND STAYING SILENT on prose that merely
mentions one. Both halves are asserted below, and the silent half is the one
that carries the decision — a scan that flagged every doc discussing merge
conflicts would be disabled within a week.
"""
from __future__ import annotations

import importlib.util
import pathlib
import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "ci" / "check_canonical_doc_coherence.py"


def _load():
    spec = importlib.util.spec_from_file_location("_cdc", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


G = _load()

PLANTED = "intro\n<<<<<<< HEAD\nours\n=======\ntheirs\n>>>>>>> origin/main\ntail\n"


# ── 1. IT FIRES ──────────────────────────────────────────────────────────────


# ── A PROBE FILE NEVER LANDS IN THE REPO'S OWN docs/ ─────────────────────────

@pytest.fixture
def tmp_root(tmp_path, monkeypatch):
    """A throwaway tree, so writing a probe cannot touch the real `docs/`.

    ⚠️ THIS IS A FIX FOR A REAL FAILURE, NOT A STYLE PREFERENCE. The first
    version of this file wrote its probe files into the repository's own
    documentation directory, building each path off the repo root.
    `tests/test_pytest_run_filter.py` scans this suite for exactly that shape and
    treats every hit as a COMMITTED documentation file the suite reads; three of
    its tests went red on the first CI run, because a docs-only PR touching such
    a path would short-circuit `pytest-run` into a green tick having executed
    nothing — which is how PR #9208 merged and left `main` red.

    ⚠️ AND THE SHAPE MUST NOT BE WRITTEN IN PROSE EITHER: that scan is a
    per-LINE regex over this file's source, so spelling the offending join in a
    comment re-creates the finding it explains. It is described here instead.

    ⚠️ IT IS ALSO THE HAZARD ITSELF: a test that writes into the working tree
    leaves the file behind if the process dies between the write and the
    `finally`, and the next `check_conflict_markers()` over the REAL corpus
    would then report a planted conflict as a live finding.

    `_conflict_corpus()` and `check_conflict_markers()` both read the module-level
    `ROOT`, so pointing it at a tmp tree exercises the REAL functions rather than
    a stub — the corpus is still constructed, not faked.
    """
    (tmp_path / "docs").mkdir()
    monkeypatch.setattr(G, "ROOT", tmp_path)
    return tmp_path


def test_a_planted_conflict_block_is_found():
    state, hits = G.file_integrity(PLANTED)
    assert state == G.CONFLICT_FOUND
    assert [n for n, _ in hits] == [2, 4, 6]


def test_an_unreadable_file_is_a_finding_never_a_pass():
    state, _ = G.file_integrity(None)
    assert state == G.CONFLICT_UNREADABLE
    assert state != G.CONFLICT_CLEAN


def test_an_unreadable_file_reaches_the_REPORT_not_just_the_predicate(tmp_root):
    """The predicate and the reporter are different layers, and I had only tested one.

    Planting `if state == CONFLICT_UNREADABLE:` away was ABSORBED by the first
    version of this file: `file_integrity(None)` still returned the right value
    while `check_conflict_markers()` silently dropped it. A control that stops
    one layer short of the thing a human reads is not a control.
    """
    doc = tmp_root / "docs" / "unreadable.md"
    doc.write_bytes(b"ok\n\xff\xfe not utf-8 \xff\n")
    fails = [f for f in G.check_conflict_markers() if "unreadable" in f]
    assert fails, "an undecodable doc must be REPORTED, not skipped"
    assert "could not be READ" in fails[0]
    assert "never 'the document is intact'" in fails[0]


# ── 2. IT STAYS SILENT ON PROSE — the half the row calls harder ──────────────

def test_an_inline_mention_is_not_a_conflict():
    assert G.file_integrity(
        "Resolve by editing the `<<<<<<< HEAD` block.\n")[0] == G.CONFLICT_CLEAN


def test_an_indented_or_fenced_illustration_is_not_a_conflict():
    assert G.file_integrity(
        "    <<<<<<< HEAD\n    =======\n    >>>>>>> x\n")[0] == G.CONFLICT_CLEAN


def test_a_bare_row_of_equals_is_not_a_conflict():
    """A markdown rule must never fire on its own — `=======` needs a trigger."""
    assert G.file_integrity("Title\n=======\nbody\n")[0] == G.CONFLICT_CLEAN


def test_the_whole_live_corpus_is_clean_right_now():
    """The measurement the decision rests on, re-run as a test.

    If this ever reddens it is doing its job — but it also means the extension
    is no longer free, which is what a reader of this decision needs to know.
    """
    assert G.check_conflict_markers() == []


# ── 3. THE DECIDED SCOPE ─────────────────────────────────────────────────────

def test_the_hierarchy_gap_that_decided_it_is_now_covered():
    """Level 4 of the instruction hierarchy: the current sprint log."""
    corpus = {p.resolve() for p in G._conflict_corpus()}
    logs = [p for p in (REPO / "docs" / "sprint-logs").glob("*.md") if p.is_file()]
    assert logs, "positive control: there are sprint logs to cover"
    missing = [p for p in logs if p.resolve() not in corpus]
    assert not missing, f"{len(missing)} sprint log(s) uncovered, e.g. {missing[:2]}"


def test_the_previously_covered_files_are_still_covered():
    """Extending must not silently DROP anything — the file's own warning."""
    corpus = {str(p.relative_to(REPO)) for p in G._conflict_corpus()}
    for must in ("CLAUDE.md", "ROADMAP.md", "docs/CLAUDE-RULES-CANONICAL.md",
                 "docs/ARCHITECTURE-CANONICAL.md"):
        assert must in corpus, must
    assert any(c.endswith("SKILL.md") for c in corpus)


def test_the_corpus_has_no_duplicates():
    corpus = G._conflict_corpus()
    assert len(corpus) == len({p.resolve() for p in corpus})


# ── 4. THE OVERRIDE IS VERIFIED IN BOTH DIRECTIONS ───────────────────────────

def test_the_override_table_is_empty_which_is_the_measured_state():
    assert G.CONFLICT_MARKER_EXPECTED == {}


def test_a_declared_line_is_excused_but_an_undeclared_one_in_the_same_file_is_not(tmp_root, monkeypatch):
    rel = "docs/probe.md"
    (tmp_root / rel).write_text(PLANTED, encoding="utf-8")
    # Undeclared: fires.
    fails = [f for f in G.check_conflict_markers() if "probe.md" in f]
    assert fails, "positive control: an undeclared marker must fire"
    # Fully declared: excused.
    monkeypatch.setattr(G, "CONFLICT_MARKER_EXPECTED", {rel: {2, 4, 6}})
    assert not [f for f in G.check_conflict_markers() if "probe.md" in f]
    # PARTIALLY declared: the undeclared line still fires — this is what stops a
    # declaration from becoming a blanket silencer.
    monkeypatch.setattr(G, "CONFLICT_MARKER_EXPECTED", {rel: {2}})
    rest = [f for f in G.check_conflict_markers() if "probe.md" in f]
    assert rest and "line 6" in rest[0]


def test_a_stale_declaration_is_itself_a_finding(monkeypatch):
    """An entry that excuses nothing must not sit there widening what it might."""
    monkeypatch.setattr(G, "CONFLICT_MARKER_EXPECTED", {"CLAUDE.md": {7}})
    fails = [f for f in G.check_conflict_markers() if f.startswith("CLAUDE.md")]
    assert fails and "stale" in fails[0]


def test_a_declaration_naming_a_missing_file_is_a_finding(monkeypatch):
    monkeypatch.setattr(G, "CONFLICT_MARKER_EXPECTED", {"docs/nope-not-here.md": {1}})
    fails = [f for f in G.check_conflict_markers() if "nope-not-here" in f]
    assert fails and "does not exist" in fails[0]


def test_the_failure_message_names_the_declared_route_not_a_narrower_scan(tmp_root):
    (tmp_root / "docs" / "probe2.md").write_text(PLANTED, encoding="utf-8")
    fails = [f for f in G.check_conflict_markers() if "probe2.md" in f]
    assert fails
    assert "CONFLICT_MARKER_EXPECTED" in fails[0]
    assert "narrowing the scan" in fails[0]
