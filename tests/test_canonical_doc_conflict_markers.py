"""A governance doc must not be able to carry an unresolved merge conflict.

WHY THIS EXISTS. Two rulings in this repo already grade a conflicted register
as unusable — `check_register_reserialization` calls a conflicted JSON register
UNREADABLE, and `check_document_index` R7 refuses a conflicted
`docs/DOCUMENT-INDEX.md` — and neither reached the canonical PROSE. MEASURED
2026-09-13 by committing a conflict block into `CLAUDE.md` and running the FULL
guard registry over it: 68 guards passed, and the only failure was
`pr-landing-guard`, for the unrelated reason that the probe branch carried no
landing record. Nothing in the repo graded the corruption.

THE HARD HALF IS THE FALSE POSITIVE, not the detection. This corpus is partly
prose ABOUT merge conflicts, so a substring test would misfire — measured the
same day, `docs/claude/health-review-backlog.json` mentions the marker on four
lines, every one of them row prose. The detection is anchored TWICE, and
independently: the pattern carries `^...$`, and the call site uses `.match()`,
which anchors at position 0 on its own. Removing either alone leaves it
anchored — established by planting each in turn and watching the control stay
green — so the belt-and-braces is load-bearing rather than decorative.

The self-test in `guard_selftests.py` covers the end-to-end guard. This file
covers the pure function, which is the only place the `unreadable` state is
reachable: a corpus file that cannot be read is not a corpus file that is fine.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "ci"))

import check_canonical_doc_coherence as g  # noqa: E402

BLOCK = ("# doc\n"
         "<<<<<<< HEAD\n"
         "left\n"
         "=======\n"
         "right\n"
         ">>>>>>> origin/main\n")


class TestWeDidNotLookIsNotAPass:
    def test_unreadable_is_its_own_state(self):
        """`None` is 'we could not read it', never 'it is intact'. Collapsing
        the two is the exact failure the three-state discipline exists for."""
        assert g.file_integrity(None)[0] == g.CONFLICT_UNREADABLE

    def test_the_three_states_are_distinct_values(self):
        """A state nobody can tell apart from another is not a state."""
        assert len({g.CONFLICT_CLEAN, g.CONFLICT_FOUND,
                    g.CONFLICT_UNREADABLE}) == 3


class TestARealConflictIsCaught:
    def test_a_full_block_is_conflicted(self):
        state, hits = g.file_integrity(BLOCK)
        assert state == g.CONFLICT_FOUND
        assert [n for n, _ in hits] == [2, 4, 6]

    @pytest.mark.parametrize("text", [
        "# doc\n<<<<<<< HEAD\nleft\n",                 # truncated: open only
        "# doc\nright\n>>>>>>> origin/main\n",          # truncated: close only
        "<<<<<<<\n",                                    # git's bare form
    ])
    def test_a_half_written_conflict_still_counts(self, text):
        """A botched manual resolution leaves ONE marker behind — which is the
        shape that actually reached `main` and prompted the row. Requiring a
        complete block would miss the realistic case."""
        assert g.file_integrity(text)[0] == g.CONFLICT_FOUND


class TestProseAboutMergeConflictsStaysQuiet:
    @pytest.mark.parametrize("text", [
        "Resolving leaves `<<<<<<< HEAD` and `>>>>>>> origin/main` behind.\n",
        "| `stamp` | mentions <<<<<<< HEAD and >>>>>>> origin/main |\n",
        "    <<<<<<< HEAD\n",                           # indented (code block)
        "Some Heading\n=======\n",                      # setext underline
        "========\n",                                   # eight, not seven
        "<<<<<<<< HEAD\n",                              # eight, not seven
    ])
    def test_a_mention_is_not_a_conflict(self, text):
        assert g.file_integrity(text)[0] == g.CONFLICT_CLEAN

    def test_a_bare_divider_alone_never_triggers(self):
        """`=======` is corroboration, never a trigger. It is also a valid
        markdown setext underline, so firing on it would invent findings in a
        document nobody ever conflicted."""
        state, hits = g.file_integrity("a\n=======\nb\n")
        assert state == g.CONFLICT_CLEAN
        assert hits == []

    def test_this_very_file_is_clean(self):
        """The docstring above quotes the markers. If the check graded prose,
        this file would be a finding — so this is the false-positive control
        with the most direct stake in being right."""
        own = pathlib.Path(__file__).read_text(encoding="utf-8")
        assert g.file_integrity(own)[0] == g.CONFLICT_CLEAN


class TestTheCheckCanFail:
    def test_the_probe_is_not_vacuous(self):
        """A detector that returns CLEAN for everything would satisfy every
        quiet assertion above. This is the positive that proves it does not."""
        assert g.file_integrity(BLOCK)[0] != g.CONFLICT_CLEAN


class TestTheCorpusIsWhatItClaims:
    def test_the_declared_canonical_docs_are_covered(self):
        """Read from the module, never restated here: a hand-copied list would
        be free to drift from the real one, and then this test would pass while
        the file it names went unscanned."""
        corpus = {str(p.relative_to(g.ROOT)) for p in g._conflict_corpus()}
        for must in ("CLAUDE.md", "docs/CLAUDE-RULES-CANONICAL.md",
                     "docs/ARCHITECTURE-CANONICAL.md"):
            assert must in corpus, sorted(corpus)[:20]

    def test_roadmap_is_covered_even_though_it_is_not_in_active_docs(self):
        """The deliberate asymmetry. ROADMAP.md is third in the instruction
        hierarchy and carries the same merge exposure, but putting it in
        ACTIVE_DOCS would change what five content checks read — a separate
        decision. If someone later 'harmonises' the two, this fails and says
        why rather than silently dropping the file."""
        corpus = {str(p.relative_to(g.ROOT)) for p in g._conflict_corpus()}
        assert "ROADMAP.md" in corpus
        assert "ROADMAP.md" not in g.ACTIVE_DOCS

    def test_the_skills_tree_is_in_the_corpus(self):
        """`_active_files()` pulls every SKILL.md, and skills are governance
        too — a conflicted skill is a workflow nobody can follow."""
        corpus = {str(p.relative_to(g.ROOT)) for p in g._conflict_corpus()}
        assert any(c.startswith(".claude/skills/") for c in corpus)
