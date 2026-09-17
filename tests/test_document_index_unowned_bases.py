"""`document_index.py --write` must not silently erase a basis it did not write.

WHY THIS EXISTS. `docs/DOCUMENT-INDEX.md` has two writers with opposite
conventions. The generator computes a `basis` it can derive; sessions hand-edit
a richer one recording what they ESTABLISHED about a document —
`mi-279:self-measured-2026-09-12 / population-stated /
positive-control-on-every-negative`. The generator does not know that
convention, so `--write` replaced it, and neither side said anything.

MEASURED 2026-09-13: **35** such bases existed at `b209768c2`, **0** after the
`--write` that rode #12122, and **1** again today — a session re-added one
after the reset, so the convention is live and the loss recurring. A `--write`
probe on a clean `main` rewrites exactly the file carrying that survivor.

THE FIX IS A REFUSAL, NOT A WARNING, and the precedent is this repo's own:
the hazard was already documented in `carry_last_verified`'s docstring and
still cost two PRs and 35 rows. `backlog_append` was fixed by making the helper
REFUSE.

⚠️ IT DOES NOT CARRY THE BASIS FORWARD. `STATUS_EXPLICIT`'s docstring forbids
this generator asserting what it cannot derive, and the row recording the 35
erasures REFUSED restoring them on exactly that reasoning. This makes the
replacement visible and deliberate; reversing that decision would be a
different one.
"""
from __future__ import annotations

import os
import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "ops"))

di = pytest.importorskip("document_index")

ROW = ("| `docs/research/x.md` | evidence | live | — | 2026-09-12 | "
       "`mi-279:self-measured-2026-09-12 / population-stated` | — |\n")


def _rows(path="docs/research/x.md", basis="dir:research-is-measurement / not-assessed"):
    return [{"path": path, "basis": basis}]


class TestWeDidNotLookIsNotNothingAtRisk:
    """Three states, never collapsed. An ABSENT index is the first-run case and
    genuinely has nothing to lose; one that exists and cannot be READ tells us
    nothing about what it holds. Sharing one value would let a permissions
    error green-light replacing every recorded basis in the file."""

    def test_the_three_states_are_distinct(self):
        assert len({di.BASES_READ, di.BASES_ABSENT, di.BASES_UNREADABLE}) == 3

    def test_a_parsed_table_reads(self):
        state, got = di.committed_bases("# Index\n" + ROW)
        assert state == di.BASES_READ
        assert got["docs/research/x.md"].startswith("mi-279:self-measured")

    def test_an_absent_index_is_absent_not_unreadable(self, monkeypatch, tmp_path):
        monkeypatch.setattr(di, "INDEX_PATH", tmp_path / "nope.md")
        assert di.committed_bases()[0] == di.BASES_ABSENT

    def test_an_unreadable_index_is_its_own_state(self, monkeypatch, tmp_path):
        """A DIRECTORY at the index path, not `chmod 000` — the first draft used
        the mode bits and SKIPPED as root, which is how this container runs, so
        the state it exists to pin went unexercised. `IsADirectoryError` is an
        `OSError` that is not `FileNotFoundError`, which is exactly the
        distinction under test, and it bites whatever the uid."""
        d = tmp_path / "DOCUMENT-INDEX.md"
        d.mkdir()
        monkeypatch.setattr(di, "INDEX_PATH", d)
        assert di.committed_bases()[0] == di.BASES_UNREADABLE

    @pytest.mark.skipif(os.geteuid() == 0, reason="root ignores the mode bits")
    def test_an_unreadable_index_is_its_own_state_via_permissions(
            self, monkeypatch, tmp_path):
        """The same state by the other route, kept for a non-root runner."""
        p = tmp_path / "DOCUMENT-INDEX.md"
        p.write_text("# Index\n", encoding="utf-8")
        p.chmod(0o000)
        monkeypatch.setattr(di, "INDEX_PATH", p)
        try:
            assert di.committed_bases()[0] == di.BASES_UNREADABLE
        finally:
            p.chmod(0o644)


class TestItFlagsOnlyWhatItDidNotWrite:
    def test_a_basis_the_generator_produced_is_not_flagged(self):
        """The false-positive control, and the one that matters: a check that
        fired on every row would be walked past."""
        same = "dir:research-is-measurement / not-assessed"
        assert di.unowned_bases({"docs/research/x.md": same}, _rows(basis=same)) == []

    def test_a_hand_written_basis_is_flagged_with_both_values(self):
        got = di.unowned_bases({"docs/research/x.md": "mi-279:self-measured-2026-09-12"},
                               _rows())
        assert len(got) == 1
        path, was, now = got[0]
        assert path == "docs/research/x.md"
        assert was == "mi-279:self-measured-2026-09-12"
        assert now == "dir:research-is-measurement / not-assessed"

    def test_a_NEW_document_has_nothing_to_lose(self):
        """A path with no committed row cannot be losing a recorded basis, and
        flagging it would refuse every PR that adds a document — which is the
        remedy R1 exists to make cheap."""
        assert di.unowned_bases({}, _rows()) == []

    def test_it_is_derived_not_pattern_matched(self):
        """The motivating convention is `<mi-id>:self-measured-…`. Matching THAT
        string would catch one habit and miss every other, so the test is a
        comparison against what the generator computes. A basis in a shape
        nobody has used before must still be flagged."""
        got = di.unowned_bases({"docs/research/x.md": "hand-written/in-some-other-style"},
                               _rows())
        assert len(got) == 1, got

    def test_the_check_can_fail(self):
        """Keeps the quiet assertions above from passing vacuously against an
        implementation that returns [] for everything."""
        assert di.unowned_bases({"docs/research/x.md": "anything-else"}, _rows())


class TestTheRefusalPrecedesTheWrite:
    """THE STRUCTURAL INVARIANT, asserted structurally — a warning printed
    beside a diff that already happened is the shape this repo has paid for.
    The pure functions could be perfect and `main()` could still call them
    after `write_text`."""

    SRC = (REPO / "scripts" / "ops" / "document_index.py").read_text(encoding="utf-8")

    def test_the_gate_is_before_the_index_write(self):
        gate = self.SRC.index("at_risk = unowned_bases(")
        write = self.SRC.index("INDEX_PATH.write_text(render_index(")
        assert gate < write, (
            "unowned_bases is consulted only after the index has been written — "
            "the refusal would be a report of an erasure that already happened")

    def test_the_unreadable_gate_is_before_the_index_write(self):
        gate = self.SRC.index("bases_state == di.BASES_UNREADABLE"
                              if "di.BASES_UNREADABLE" in self.SRC
                              else "bases_state == BASES_UNREADABLE")
        write = self.SRC.index("INDEX_PATH.write_text(render_index(")
        assert gate < write

    def test_an_explicit_flag_exists_to_override(self):
        """A refusal with no way past it would get the whole call removed. The
        override is named, and the run then RECORDS what it replaced rather
        than doing it quietly."""
        assert "--reset-unowned-bases" in self.SRC
        assert "REPLACED" in self.SRC
