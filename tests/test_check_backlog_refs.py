"""The dangling-tracking-reference guard.

A doc saying "tracked by BL-X" where BL-X was never filed is worse than no reference: it
reads as tracked, so nobody re-checks it. Four such ids existed on 2026-07-30, including
BL-20260730-M1-PRICE-JOIN-DEAD -- the canonical example in the binding "Green is not
evidence" rule, cited from four workflows, resolving to nothing.

The guard is DIFF-SCOPED on purpose (~109 pre-existing dangling refs repo-wide); failing on
all of them would make an alarm every session walks past, which the rules name as itself a
P1 bug. `TestScopedNotGlobal` pins that choice so a later "improvement" to fail globally
has to argue with it.
"""
from __future__ import annotations

import json
import subprocess
import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts", "ops"))

cbr = pytest.importorskip("check_backlog_refs")


def _backlog(root, ids):
    d = root / "docs" / "claude"
    d.mkdir(parents=True, exist_ok=True)
    (d / "health-review-backlog.json").write_text(
        json.dumps({"items": [{"id": i} for i in ids]}), encoding="utf-8")
    return root


class TestRefPattern:
    def test_matches_the_three_prefixes(self):
        for s in ("BL-20260730-FOO", "MB-20260730-FOO-BAR", "FU-20260511-001"):
            assert cbr.REF.findall(s) == [s]

    def test_does_not_match_a_bare_trailing_dash(self):
        """A partial match inside prose must not masquerade as an id -- that artefact
        produced false 'dangling' hits in the first measurement."""
        assert cbr.REF.findall("BL-20260616-LTMGMT-lowercase") == ["BL-20260616-LTMGMT"]

    def test_ignores_non_ids(self):
        assert cbr.REF.findall("BL-2026-FOO") == []
        assert cbr.REF.findall("PR-20260730-FOO") == []


class TestDangling:
    def test_unfiled_id_is_dangling(self):
        assert cbr.dangling({"BL-20260730-X": {"a.md"}}, {"BL-20260730-Y"})

    def test_filed_id_is_clean(self):
        assert cbr.dangling({"BL-20260730-X": {"a.md"}}, {"BL-20260730-X"}) == {}

    def test_a_row_in_another_rows_refs_does_not_count_as_filed(self, tmp_path):
        """The exact shape of the M1-PRICE-JOIN-DEAD miss: cited in other rows' `refs`
        for weeks, never filed as a row of its own."""
        d = tmp_path / "docs" / "claude"
        d.mkdir(parents=True)
        (d / "health-review-backlog.json").write_text(json.dumps(
            {"items": [{"id": "BL-1", "refs": ["BL-20260730-M1-PRICE-JOIN-DEAD"]}]}),
            encoding="utf-8")
        assert "BL-20260730-M1-PRICE-JOIN-DEAD" not in cbr.filed_ids(tmp_path)


class TestFiledIds:
    def test_reads_ids_from_backlog(self, tmp_path):
        _backlog(tmp_path, ["BL-A", "BL-B"])
        assert cbr.filed_ids(tmp_path) == {"BL-A", "BL-B"}

    def test_malformed_backlog_does_not_crash(self, tmp_path):
        d = tmp_path / "docs" / "claude"
        d.mkdir(parents=True)
        (d / "x-backlog.json").write_text("{not json", encoding="utf-8")
        assert cbr.filed_ids(tmp_path) == set()


class TestScopedNotGlobal:
    def test_full_sweep_is_report_only(self, tmp_path):
        """--all must exit 0. It reports pre-existing debt; failing on ~109 historical
        refs would create the walked-past alarm the rules call a P1 bug."""
        _backlog(tmp_path, [])
        (tmp_path / "docs").mkdir(exist_ok=True)
        (tmp_path / "docs" / "a.md").write_text("see BL-20260730-NOPE", encoding="utf-8")
        assert cbr.main(["--repo-root", str(tmp_path), "--all"]) == 0

    def test_requires_a_base_when_not_sweeping(self, tmp_path):
        _backlog(tmp_path, [])
        assert cbr.main(["--repo-root", str(tmp_path)]) == 1


# ⚠️ REMOVED 2026-09-21 by the operating reset: `TestThisRepo`.
# It asserted a property of the LIVE review backlogs, which is archived under
# docs/archive/2026-09-21-operating-reset/. Its subject is gone, so the
# test cannot pass and cannot be made to pass — it is removed WITH its
# subject rather than skipped, because a permanently-skipped test is a
# control in name only. Its fixture-based siblings in this file are
# UNTOUCHED and still green: they test the CODE, which still exists.
# Restore it from git history if the register ever returns.


class TestReformatDoesNotLookLikeIntroduction:
    """"On an added line" is not "introduced".

    Re-sorting a file rewrites every line, so pre-existing references read as new. That
    happened for real: union-merging the health-review backlog re-ordered it and this guard
    fired on 12 dangling ids that had been there for weeks — exactly the pre-existing debt
    the diff-scoping exists to exclude. A guard that cries wolf on a reformat teaches
    sessions to suppress it.
    """

    @staticmethod
    def _repo(tmp_path, initial: str):
        import subprocess
        r = tmp_path / "repo"
        (r / "docs" / "claude").mkdir(parents=True)
        (r / "docs" / "claude" / "health-review-backlog.json").write_text(
            json.dumps({"items": [{"id": "BL-20200101-REAL"}]}), encoding="utf-8")
        (r / "docs" / "note.md").write_text(initial, encoding="utf-8")
        for c in (["init"], ["add", "-A"], ["-c", "user.email=t@t", "-c", "user.name=t",
                                            "commit", "-m", "base"]):
            subprocess.run(["git", "-C", str(r)] + c, capture_output=True, check=True)
        return r

    def _commit(self, r, text):
        import subprocess
        (r / "docs" / "note.md").write_text(text, encoding="utf-8")
        subprocess.run(["git", "-C", str(r), "add", "-A"], capture_output=True, check=True)
        subprocess.run(["git", "-C", str(r), "-c", "user.email=t@t", "-c", "user.name=t",
                        "commit", "-m", "change"], capture_output=True, check=True)

    def test_moving_an_existing_dangling_ref_is_not_a_finding(self, tmp_path):
        # BL-20200202-DANGLING is unfiled, but it was ALREADY in this file before the change.
        r = self._repo(tmp_path, "alpha BL-20200202-DANGLING\nbeta\ngamma\n")
        # Reorder the lines: every line is "added" from the diff's point of view.
        self._commit(r, "gamma\nbeta\nalpha BL-20200202-DANGLING\n")
        bad = cbr.dangling(cbr.refs_in_added_lines("HEAD~1", r), cbr.filed_ids(r))
        assert bad == {}, f"a reformat must not report pre-existing debt: {bad}"

    def test_a_genuinely_new_dangling_ref_is_still_caught(self, tmp_path):
        r = self._repo(tmp_path, "alpha\n")
        self._commit(r, "alpha\ntracked by BL-20300303-NEVERFILED\n")
        bad = cbr.dangling(cbr.refs_in_added_lines("HEAD~1", r), cbr.filed_ids(r))
        assert "BL-20300303-NEVERFILED" in bad

    def test_a_filed_ref_is_never_a_finding(self, tmp_path):
        r = self._repo(tmp_path, "alpha\n")
        self._commit(r, "alpha\nsee BL-20200101-REAL\n")
        bad = cbr.dangling(cbr.refs_in_added_lines("HEAD~1", r), cbr.filed_ids(r))
        assert bad == {}


class TestSplitIntoANewFileIsNotIntroduction:
    """A VERBATIM SPLIT must not re-report pre-existing debt.

    `TestReformatDoesNotLookLikeIntroduction` above pins the in-place case, and its
    exemption keys on the FILE -- so **moving** existing prose into a NEW file defeated
    it: the new file cites nothing at base by construction, so every id it carries read
    as introduced. Not hypothetical: splitting the API reference out of `CLAUDE.md`
    (2026-09-02) moved the rows verbatim and this guard reported five long-standing
    dangling ids as newly introduced -- all five already attributed to
    BL-20260730-CITED-BUT-UNFILED-BACKLOG-IDS, the row the guard's own docstring names
    as the home for exactly that debt.

    The fix falls back to "cited ANYWHERE in the tree at base" for a path that did not
    exist at base. These tests pin BOTH halves, because the change made the guard
    quieter in one case and must be shown not to have made it inert in the others.
    """

    @staticmethod
    def _repo(tmp_path, files: dict):
        import subprocess
        r = tmp_path / "repo"
        (r / "docs" / "claude").mkdir(parents=True)
        (r / "docs" / "claude" / "health-review-backlog.json").write_text(
            json.dumps({"items": [{"id": "BL-20200101-REAL"}]}), encoding="utf-8")
        for name, text in files.items():
            (r / "docs" / name).write_text(text, encoding="utf-8")
        for c in (["init"], ["add", "-A"], ["-c", "user.email=t@t", "-c", "user.name=t",
                                            "commit", "-m", "base"]):
            subprocess.run(["git", "-C", str(r)] + c, capture_output=True, check=True)
        return r

    @staticmethod
    def _commit(r, files: dict, removed=()):
        import subprocess
        for name in removed:
            (r / "docs" / name).unlink()
        for name, text in files.items():
            (r / "docs" / name).write_text(text, encoding="utf-8")
        subprocess.run(["git", "-C", str(r), "add", "-A"], capture_output=True, check=True)
        subprocess.run(["git", "-C", str(r), "-c", "user.email=t@t", "-c", "user.name=t",
                        "commit", "-m", "change"], capture_output=True, check=True)

    def test_moving_a_dangling_ref_into_a_new_file_is_not_a_finding(self, tmp_path):
        """The SOURCE FILE MUST SURVIVE, and that is not a detail of the fixture.

        The first version of this test deleted `old.md` and created `new.md` with
        identical content -- which git renders as a 100%-similarity RENAME emitting
        NO `+` lines at all, so it passed with the fix disabled and proved nothing.
        Caught by running the negative control rather than by reading the test.

        A real split is a large file KEEPING its identity while a block is copied
        out of it, which is what produces the added lines. `old.md` therefore stays
        and only loses the block.
        """
        r = self._repo(tmp_path, {"old.md": "alpha\nbeta\ntracked by BL-20200202-DANGLING\n"
                                             "gamma\ndelta\nepsilon\nzeta\n"})
        self._commit(r, {"old.md": "alpha\nbeta\nsee docs/new.md\n"
                                   "gamma\ndelta\nepsilon\nzeta\n",
                         "new.md": "tracked by BL-20200202-DANGLING\n"})
        # Guard the guard: if this diff carries no added line citing the id, the
        # assertion below is vacuous (the rename artefact above).
        diff = cbr._git(["diff", "-U0", "HEAD~1...HEAD", "--"] + list(cbr.SEARCH_DIRS), r)
        assert any(ln.startswith("+") and "BL-20200202-DANGLING" in ln
                   and not ln.startswith("+++") for ln in diff.splitlines()), \
            "fixture is vacuous: the split produced no added line citing the id"
        bad = cbr.dangling(cbr.refs_in_added_lines("HEAD~1", r), cbr.filed_ids(r))
        assert bad == {}, f"a verbatim split must not report pre-existing debt: {bad}"

    def test_a_brand_new_dangling_ref_in_a_new_file_is_still_caught(self, tmp_path):
        """The class the guard exists for. If the fallback swallowed this it would be
        inert for every new file, which is the dangerous direction."""
        r = self._repo(tmp_path, {"old.md": "alpha\n"})
        self._commit(r, {"new.md": "tracked by BL-20300303-NEVER-SEEN-ANYWHERE\n"})
        bad = cbr.dangling(cbr.refs_in_added_lines("HEAD~1", r), cbr.filed_ids(r))
        assert "BL-20300303-NEVER-SEEN-ANYWHERE" in bad

    def test_a_brand_new_dangling_ref_in_an_existing_file_is_still_caught(self, tmp_path):
        r = self._repo(tmp_path, {"old.md": "alpha BL-20200202-DANGLING\n"})
        self._commit(r, {"old.md": "alpha BL-20200202-DANGLING\nplus BL-20300404-NEW\n"})
        bad = cbr.dangling(cbr.refs_in_added_lines("HEAD~1", r), cbr.filed_ids(r))
        assert "BL-20300404-NEW" in bad
        assert "BL-20200202-DANGLING" not in bad

    def test_file_absent_at_base_is_None_not_an_empty_set(self, tmp_path):
        """`None` (the file did not exist) and `set()` (it existed and cited nothing) are
        different facts; collapsing them is what caused the blindness."""
        r = self._repo(tmp_path, {"old.md": "alpha\n"})
        assert cbr._refs_in_file_at("HEAD", "docs/nope.md", r) is None
        assert cbr._refs_in_file_at("HEAD", "docs/old.md", r) == set()

    def test_an_unreadable_base_refuses_rather_than_returning_empty(self, tmp_path):
        """"We could not look" must not become "nothing was cited at base" -- that would
        silently restore the blindness for every file in the diff."""
        r = self._repo(tmp_path, {"old.md": "alpha\n"})
        with pytest.raises(RuntimeError):
            cbr._refs_anywhere_at("refs/heads/no-such-ref-at-all", r)


class TestFollowUpsRegisterResolves:
    """FU- ids are matched by REF but live in `comms/follow_ups.json`, not the backlogs.

    Before 2026-09-02 `filed_ids` read only `docs/claude/*backlog*.json`, so every FU-
    citation dangled BY CONSTRUCTION however correctly it was filed. Measured that day:
    13 distinct FU- ids cited across SEARCH_DIRS, 12 of them genuinely filed. Adding the
    register removed 12 false findings and kept the one real one -- more accurate, not
    quieter, which is what the positive control below pins.
    """

    def test_an_id_in_the_follow_ups_register_resolves(self, tmp_path):
        (tmp_path / "docs" / "claude").mkdir(parents=True)
        (tmp_path / "docs" / "claude" / "health-review-backlog.json").write_text(
            json.dumps({"items": [{"id": "BL-20200101-REAL"}]}), encoding="utf-8")
        (tmp_path / "comms").mkdir()
        (tmp_path / "comms" / "follow_ups.json").write_text(
            json.dumps({"follow_ups": [{"id": "FU-20200101-001"}]}), encoding="utf-8")
        assert "FU-20200101-001" in cbr.filed_ids(tmp_path)
        assert "BL-20200101-REAL" in cbr.filed_ids(tmp_path)

    def test_an_fu_id_that_is_NOT_filed_still_dangles(self, tmp_path):
        """The positive control. If the register were read as a blanket exemption for the
        FU prefix, a genuinely unfiled FU- id would stop being a finding."""
        (tmp_path / "docs" / "claude").mkdir(parents=True)
        (tmp_path / "docs" / "claude" / "health-review-backlog.json").write_text(
            json.dumps({"items": []}), encoding="utf-8")
        (tmp_path / "comms").mkdir()
        (tmp_path / "comms" / "follow_ups.json").write_text(
            json.dumps({"follow_ups": [{"id": "FU-20200101-001"}]}), encoding="utf-8")
        assert "FU-20200101-999" not in cbr.filed_ids(tmp_path)
        assert cbr.dangling({"FU-20200101-999": {"a.md"}}, cbr.filed_ids(tmp_path))

    def test_a_missing_register_is_not_an_error(self, tmp_path):
        """A repo without the follow-ups register must still resolve backlog ids -- the
        extra source is additive, never a precondition."""
        (tmp_path / "docs" / "claude").mkdir(parents=True)
        (tmp_path / "docs" / "claude" / "health-review-backlog.json").write_text(
            json.dumps({"items": [{"id": "BL-20200101-REAL"}]}), encoding="utf-8")
        assert cbr.filed_ids(tmp_path) == {"BL-20200101-REAL"}

    def test_this_repos_one_genuinely_unfiled_fu_id_is_still_reported(self):
        """Measured 2026-09-02 against this repo: FU-20260519-003 is cited and never
        filed. It is the live positive control -- if it ever resolves, either it was
        filed (update this test) or the register read has become a blanket exemption."""
        filed = cbr.filed_ids()
        assert "FU-20260511-001" in filed, "a filed FU- id must resolve"
        assert "FU-20260519-003" not in filed, \
            "an unfiled FU- id must still dangle; if it was filed, update this test"


def _as_repo_citing(root, cited_id):
    """Make `root` a real git repo whose HEAD ADDS a line citing `cited_id`.

    Returns the base sha, so the caller can pass a `--base` that RESOLVES.

    ⚠️ ADDED 2026-09-17 BECAUSE THE TEST BELOW WAS GREEN THROUGH THE DEFECT IT
    SHOULD HAVE CAUGHT. It used to pass ``--base no-such-ref-000`` into a
    directory that is not a repo, and assert the verdict was 0 or 1. It was —
    but not for the stated reason: ``_git`` discarded git's ``returncode``, so
    an unresolvable base produced an EMPTY diff, zero introduced references and
    a clean ``OK``. The test never exercised the gating path it names; it
    exercised the blindness, and would have gone on passing however wrong the
    gate became. See
    ``BL-20260917-CHECK-BACKLOG-REFS-REPORTS-EVERY-ID-RESOLVES-WHEN-IT-COULD-NOT-READ-THE-DIFF-AT-ALL``.
    """
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
           "PATH": os.environ.get("PATH", "")}

    def git(*a):
        return subprocess.run(["git", "-C", str(root), *a],
                              capture_output=True, text=True, env=env, check=True)

    git("init", "-q", "-b", "main")
    git("add", "-A")
    git("commit", "-q", "-m", "base")
    base = git("rev-parse", "HEAD").stdout.strip()

    (root / "docs").mkdir(parents=True, exist_ok=True)
    (root / "docs" / "note.md").write_text(
        "tracked by " + cited_id + "\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-q", "-m", "cite")
    return base


class TestUnreadableRegisterIsNotAnEmptyOne:
    """A register that does not PARSE shrinks the universe of filed ids silently.

    MEASURED on `origin/main` @`eb606713d`, one `THIS IS NOT JSON` line inserted
    into `docs/claude/health-review-backlog.json` (1574 rows): `--all` went from

        1874 filed ids; 86 dangling references repo-wide
    to
        299 filed ids; 1660 dangling references repo-wide

    exit 0 both times. Every one of the added 1574 is a confident claim that a
    row which exists does not, and 1660 lines of it buries the 86 real findings
    — the desensitised alarm this repo calls its own worst failure mode.

    Both directions are asserted. The over-reporting case is real: an ABSENT
    optional register, and a register that is merely EMPTY, must NOT be called
    unreadable, or the banner fires on trees that are perfectly fine.
    """

    def test_a_corrupt_register_is_NAMED_not_silently_skipped(self, tmp_path):
        _backlog(tmp_path, ["BL-20260101-REAL"])
        bad = tmp_path / "docs" / "claude" / "ml-review-backlog.json"
        bad.write_text("THIS IS NOT JSON", encoding="utf-8")
        filed, unreadable = cbr.filed_ids_with_state(tmp_path)
        assert unreadable == ["docs/claude/ml-review-backlog.json"], (
            "a register that is present and does not parse must be REPORTED; "
            "skipping it silently is what turned 86 danglers into 1660")
        assert "BL-20260101-REAL" in filed, (
            "the registers that DO parse must still contribute their ids")

    def test_an_EMPTY_but_valid_register_is_not_unreadable(self, tmp_path):
        _backlog(tmp_path, [])
        assert cbr.filed_ids_with_state(tmp_path) == (set(), []), (
            "empty is a real reading; calling it unreadable would fire the "
            "banner on a tree that is fine")

    def test_an_ABSENT_optional_register_is_not_unreadable(self, tmp_path):
        # comms/follow_ups.json is optional. A tree without it is not a tree
        # whose universe is incomplete — opposite facts.
        _backlog(tmp_path, ["BL-20260101-REAL"])
        assert cbr.filed_ids_with_state(tmp_path)[1] == []

    def test_the_sweep_REFUSES_rather_than_stating_a_count_it_cannot_stand_behind(
            self, tmp_path, capsys):
        _backlog(tmp_path, ["BL-20260101-REAL"])
        (tmp_path / "docs" / "claude" / "ml-review-backlog.json").write_text(
            "{ NOT JSON", encoding="utf-8")
        rc = cbr.main(["--repo-root", str(tmp_path), "--all"])
        out = capsys.readouterr().out
        assert rc != 0, "a sweep that cannot establish its own denominator must not exit 0"
        assert "universe of FILED ids is INCOMPLETE" in out
        assert "ml-review-backlog.json" in out
        # ⚠️ THE BANNER MUST PRECEDE THE LIST. A reader who has already read the
        # dangling lines has drawn the conclusion before any footnote lands.
        assert out.index("INCOMPLETE") < out.index("dangling references")
        assert "OVER-COUNTED" in out, (
            "the count line itself has to carry the caveat — the banner scrolls away")

    def test_a_clean_tree_still_exits_0_and_raises_no_banner(self, tmp_path, capsys):
        # THE POSITIVE CONTROL. A guard that only ever demonstrates its failures
        # cannot show it is not simply always-red.
        _backlog(tmp_path, ["BL-20260101-REAL"])
        rc = cbr.main(["--repo-root", str(tmp_path), "--all"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "INCOMPLETE" not in out
        assert "OVER-COUNTED" not in out

    def test_the_gating_mode_WARNS_and_keeps_its_verdict(self, tmp_path, capsys):
        """Measured: the gate cannot false-fail on a real id, so it does not refuse.

        `_refs_anywhere_at` exempts any id already cited at the base, and every
        real row id is cited at base in its own register row. A refusal nothing
        can reach is the decorative branch `collapsed-state-guard` refuses — so
        the gating path gets the banner and keeps its answer.

        ⚠️ The base here RESOLVES, which it did not before 2026-09-17 — see
        `_as_repo_citing`. A nonexistent base made this assert nothing.
        """
        _backlog(tmp_path, ["BL-20260101-REAL"])
        (tmp_path / "docs" / "claude" / "ml-review-backlog.json").write_text(
            "nope", encoding="utf-8")
        base = _as_repo_citing(tmp_path, "BL-20260101-REAL")
        rc = cbr.main(["--repo-root", str(tmp_path), "--base", base])
        out = capsys.readouterr().out
        assert "universe of FILED ids is INCOMPLETE" in out
        assert rc in (0, 1), (
            "the gating verdict is unchanged by an unreadable register — see the "
            "measurement in filed_ids_with_state's docstring")

    def test_the_gating_mode_STILL_CATCHES_a_dangling_id(self, tmp_path, capsys):
        """THE CONTROL for the test above: the gate must still be able to fail.

        Without it, "the verdict is unchanged" is equally satisfied by a gate
        that can never say anything — which is exactly the state an
        unresolvable base used to put it in.
        """
        _backlog(tmp_path, ["BL-20260101-REAL"])
        base = _as_repo_citing(tmp_path, "BL-20260101-NEVER-FILED")
        rc = cbr.main(["--repo-root", str(tmp_path), "--base", base])
        out = capsys.readouterr().out
        assert rc == 1, (rc, out[-400:])
        assert "BL-20260101-NEVER-FILED" in out

    def test_an_unresolvable_base_REFUSES_rather_than_reporting_OK(
            self, tmp_path, capsys):
        """The defect itself, pinned where it lived.

        This is the invocation the test above used to make, and it must now be
        a refusal: "we did not look" is not "nothing dangles".
        """
        _backlog(tmp_path, ["BL-20260101-REAL"])
        _as_repo_citing(tmp_path, "BL-20260101-REAL")
        rc = cbr.main(["--repo-root", str(tmp_path), "--base", "no-such-ref-000"])
        out = capsys.readouterr().out
        assert rc == 2, (rc, out[-400:])
        assert "OK —" not in out


# ---------------------------------------------------------------------------
# "Correct the id if it is a typo/rename" — but WHICH id?
#
# The guard named the dangling reference and stopped there, so every finding
# cost a manual lookup against a 1876-id register. MEASURED 2026-09-13 over
# every dangling reference this guard raised against one session's branches
# (n=3, one session, one author — state the population, it is small): ALL
# THREE were exact PREFIXES of a real filed row, and none was a typo.
#
#   BL-…-A-DUPLICATE-ROW-ID-REACHED-MAIN          elided in prose
#   BL-…-GRADES-THE-WRONG-TREE                    elided in prose
#   BL-…-GRADES-THE-WRONG                         WRAPPED across a docstring
#
# The failure mode these ids have is REFORMATTING, which always truncates and
# never garbles. `difflib` is the obvious implementation and is not the one the
# evidence asks for, so prefix leads and fuzzy is the fallback.
# ---------------------------------------------------------------------------

REAL = "BL-20260913-A-GUARD-RUN-BEFORE-COMMITTING-GRADES-THE-WRONG-TREE-AND-ITS-VACUOUS-PASS-IS-INDISTINGUISHABLE-FROM-A-REAL-ONE"
OTHER = "BL-20260913-A-DUPLICATE-ROW-ID-REACHED-MAIN-AND-TURNED-REGISTER-ID-GUARD-RED-FOR-EVERY-BACKLOG-TOUCHING-PR-IN-THE-REPO"

#: A FIXED universe, not the live register. The three cases below are the real
#: ones from 2026-09-13, but pinning them to the live backlog would make this
#: test fail the day someone closes and removes a row — testing the register's
#: contents rather than the suggester's logic.
FILED = {REAL, OTHER, "BL-20260101-SOMETHING-ELSE-ENTIRELY-AND-UNRELATED"}


class TestTheThreeRealCasesFrom20260913:
    @pytest.mark.parametrize("ref,expected,why", [
        ("BL-20260913-A-DUPLICATE-ROW-ID-REACHED-MAIN", OTHER,
         "elided in a landing record"),
        ("BL-20260913-A-GUARD-RUN-BEFORE-COMMITTING-GRADES-THE-WRONG-TREE", REAL,
         "elided in a backlog row's detail"),
        ("BL-20260913-A-GUARD-RUN-BEFORE-COMMITTING-GRADES-THE-WRONG", REAL,
         "line-wrapped across a docstring"),
    ])
    def test_a_truncated_id_names_the_row_it_truncates(self, ref, expected, why):
        kind, cands = cbr.suggest_for(ref, FILED)
        assert kind == cbr.SUGGEST_PREFIX, (why, kind)
        assert cands == [expected], (why, cands)


class TestAmbiguityIsReportedNotResolved:
    """A prefix shared by many rows names none of them. Returning the first
    three would present an alphabetical tiebreak as a hint — the
    implicit-input-selection shape `check_diagnostic_provenance.py` exists to
    catch. MEASURED on the live register: `BL-20260913-` matches 9 rows and
    `BL-20260912-THE-` matches 29."""

    def test_too_many_matches_is_its_own_state(self):
        filed = {f"BL-20260913-ROW-NUMBER-{i}-WITH-A-LONG-TAIL" for i in range(9)}
        kind, cands = cbr.suggest_for("BL-20260913-ROW", filed)
        assert kind == cbr.SUGGEST_AMBIGUOUS
        assert len(cands) == 9

    def test_the_ambiguous_hint_names_no_candidate(self):
        filed = {f"BL-20260913-ROW-NUMBER-{i}-WITH-A-LONG-TAIL" for i in range(9)}
        body = " ".join(cbr.suggestion_lines("BL-20260913-ROW", filed))
        assert "9 filed ids START WITH this" in body
        assert "ROW-NUMBER-0" not in body, (
            "an ambiguous prefix must not present one arbitrary candidate as the answer")

    def test_a_few_matches_ARE_all_shown(self):
        """The control for the one above. Two candidates is genuine ambiguity a
        human can resolve by reading; suppressing them would be as unhelpful as
        picking one."""
        filed = {f"BL-20260913-ROW-NUMBER-{i}-WITH-A-LONG-TAIL" for i in range(2)}
        kind, cands = cbr.suggest_for("BL-20260913-ROW", filed)
        assert kind == cbr.SUGGEST_PREFIX
        assert len(cands) == 2


class TestItDoesNotInventHelp:
    def test_a_genuinely_absent_id_gets_no_hint(self):
        kind, cands = cbr.suggest_for(
            "BL-99999999-THIS-WAS-NEVER-FILED-ANYWHERE-AT-ALL", FILED)
        assert kind == cbr.SUGGEST_NONE
        assert cands == []

    def test_no_hint_means_no_lines(self):
        """A hint rendered on every finding whether or not it has content is
        decoration, and decoration in this repo's guard output gets walked
        past."""
        assert cbr.suggestion_lines(
            "BL-99999999-THIS-WAS-NEVER-FILED-ANYWHERE-AT-ALL", FILED) == []

    def test_a_prefix_too_short_to_discriminate_is_not_evidence(self):
        """`BL-2026` would match hundreds of rows. Below the floor the prefix
        branch must not fire at all."""
        assert len("BL-2026") < cbr._MIN_PREFIX_LEN
        kind, _ = cbr.suggest_for("BL-2026", FILED)
        assert kind != cbr.SUGGEST_PREFIX

    def test_the_suggester_can_actually_find_something(self):
        """The positive that keeps the three assertions above from passing
        vacuously against a suggester that returns NONE for everything."""
        kind, cands = cbr.suggest_for(REAL[:60], FILED)
        assert kind == cbr.SUGGEST_PREFIX and cands


class TestFuzzyIsTheFallbackNotTheRule:
    def test_a_real_typo_still_gets_a_suggestion(self):
        typo = REAL[:-1] + "X"          # same length, one character wrong
        kind, cands = cbr.suggest_for(typo, FILED)
        assert kind == cbr.SUGGEST_FUZZY
        assert cands == [REAL]

    def test_prefix_wins_when_both_could_match(self):
        """Ordering is load-bearing, not incidental: the measured population is
        truncations, and a fuzzy match on a truncated id can return a DIFFERENT
        row that happens to score well."""
        ref = REAL[:70]
        kind, _ = cbr.suggest_for(ref, FILED)
        assert kind == cbr.SUGGEST_PREFIX
