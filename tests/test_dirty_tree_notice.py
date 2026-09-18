"""A guard that graded the wrong tree must SAY so, at every guard a session types.

`BL-20260917-THE-DIRTY-TREE-NOTICE-LIVES-ONLY-IN-RUN-GUARDS-SO-ALL-18-DIRECTLY-INVOCABLE-DIFF-SCOPED-GUARDS-STILL-GRADE-THE-WRONG-TREE-SILENTLY`
was filed after FOUR instances in one day, all by an author who had read the
instruction to commit first. The row rules out two non-fixes in advance -- a
wrapper script (nothing makes a session type it) and another instruction (the
instruction already existed) -- so what is tested here is that the notice reaches
the guards themselves.
"""
from __future__ import annotations

import pathlib
import re
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "ci"))
import _dirty_tree as dt  # noqa: E402


# --------------------------------------------------------------------------
# the three states, and the one that is silent
# --------------------------------------------------------------------------

class TestTheThreeStatesAreNeverCollapsed:
    def test_a_clean_tree_is_SILENT(self):
        assert dt.notice_lines(dt.CLEAN, []) == []

    def test_could_not_look_is_NOT_silence(self):
        lines = dt.notice_lines(dt.COULD_NOT_LOOK, [])
        assert lines, "‘we could not establish whether your tree is clean’ is the "\
                      "exact collapse this module exists to stop"
        assert "UNKNOWN" in " ".join(lines)
        assert "UNCOMMITTED WORK" not in " ".join(lines), (
            "could_not_look must not be reported as a dirty tree — that is a "
            "different claim")

    def test_a_dirty_tree_names_the_paths_and_says_the_verdict_is_not_yours(self):
        lines = dt.notice_lines(dt.DIRTY, ["a.py", "b.py"])
        joined = " ".join(lines)
        assert "UNCOMMITTED WORK (2 path(s))" in joined
        assert "a.py" in joined and "b.py" in joined
        assert "not a clean bill" in joined

    def test_a_long_list_is_truncated_but_the_count_is_not(self):
        lines = dt.notice_lines(dt.DIRTY, [f"f{i}.py" for i in range(30)], limit=3)
        joined = " ".join(lines)
        assert "30 path(s)" in joined, "the COUNT must survive truncation"
        assert "and 27 more" in joined

    def test_a_missing_git_is_could_not_look_and_never_clean(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PATH", str(tmp_path))  # no git on PATH
        state, paths = dt.uncommitted()
        assert state == dt.COULD_NOT_LOOK, (state, paths)
        assert paths == []

    def test_a_directory_that_is_not_a_repo_is_could_not_look(self, tmp_path):
        state, paths = dt.uncommitted(str(tmp_path))
        assert state == dt.COULD_NOT_LOOK, (state, paths)


class TestTheReadItself:
    def _repo(self, tmp_path):
        root = str(tmp_path)
        for args in (["init", "--quiet", "-b", "main"], ["config", "user.name", "t"],
                     ["config", "user.email", "t@e"]):
            subprocess.run(["git", "-C", root] + args, check=True,
                           capture_output=True)
        (tmp_path / "a.txt").write_text("one\n", encoding="utf-8")
        subprocess.run(["git", "-C", root, "add", "-A"], check=True, capture_output=True)
        subprocess.run(["git", "-C", root, "commit", "--quiet", "-m", "b"],
                       check=True, capture_output=True)
        return root

    def test_a_committed_tree_reads_clean(self, tmp_path):
        assert dt.uncommitted(self._repo(tmp_path)) == (dt.CLEAN, [])

    def test_a_modified_file_reads_dirty(self, tmp_path):
        root = self._repo(tmp_path)
        (tmp_path / "a.txt").write_text("two\n", encoding="utf-8")
        state, paths = dt.uncommitted(root)
        assert (state, paths) == (dt.DIRTY, ["a.txt"])

    def test_an_UNTRACKED_file_reads_dirty_too(self, tmp_path):
        """⚠️ Deliberate. A guard reading `git show <base>:<path>` cannot see a
        file you have only created either, and that is the same trap."""
        root = self._repo(tmp_path)
        (tmp_path / "new.txt").write_text("hi\n", encoding="utf-8")
        state, paths = dt.uncommitted(root)
        assert state == dt.DIRTY and "new.txt" in paths, (state, paths)

    def test_a_rename_reports_the_NEW_path(self, tmp_path):
        root = self._repo(tmp_path)
        subprocess.run(["git", "-C", root, "mv", "a.txt", "b.txt"], check=True,
                       capture_output=True)
        state, paths = dt.uncommitted(root)
        assert state == dt.DIRTY and paths == ["b.txt"], (state, paths)


class TestWarn:
    def test_warn_writes_to_stderr_not_stdout(self, tmp_path, capsys, monkeypatch):
        monkeypatch.delenv("DIRTY_TREE_NOTICE", raising=False)
        root = TestTheReadItself()._repo(tmp_path)
        (tmp_path / "a.txt").write_text("changed\n", encoding="utf-8")
        state = dt.warn(root)
        cap = capsys.readouterr()
        assert state == dt.DIRTY
        assert "UNCOMMITTED WORK" in cap.err
        assert cap.out == "", (
            "several of these guards are parsed by their callers, so the notice "
            "must never reach stdout")

    def test_the_suppressor_is_opt_IN_so_an_unset_variable_leaves_it_ON(self, tmp_path, capsys, monkeypatch):
        root = TestTheReadItself()._repo(tmp_path)
        (tmp_path / "a.txt").write_text("changed\n", encoding="utf-8")
        monkeypatch.delenv("DIRTY_TREE_NOTICE", raising=False)
        assert dt.warn(root) == dt.DIRTY
        capsys.readouterr()
        monkeypatch.setenv("DIRTY_TREE_NOTICE", "0")
        assert dt.warn(root) == "suppressed"
        assert capsys.readouterr().err == ""

    def test_a_typo_in_the_suppressor_does_NOT_silence_it(self, tmp_path, capsys, monkeypatch):
        """A mistyped kill-switch must not quietly switch off the only thing
        watching for this class — the polarity this repo insists on."""
        root = TestTheReadItself()._repo(tmp_path)
        (tmp_path / "a.txt").write_text("changed\n", encoding="utf-8")
        monkeypatch.setenv("DIRTY_TREE_NOTICE", "flase")
        assert dt.warn(root) == dt.DIRTY
        assert "UNCOMMITTED WORK" in capsys.readouterr().err


# --------------------------------------------------------------------------
# the class, not the instances
# --------------------------------------------------------------------------

def _base_accepting_guards() -> list[str]:
    """Scripts that run_guards.py invokes AND that accept `--base`.

    Derived, never a frozen list: the previous attempt at this row froze a count
    of 18 and it was wrong by six within a day.
    """
    rg = (ROOT / "scripts" / "ci" / "run_guards.py").read_text(encoding="utf-8")
    referenced = set(re.findall(r'"(scripts/[A-Za-z0-9_/]+\.py)"', rg))
    out = []
    for rel in sorted(referenced):
        p = ROOT / rel
        if not p.exists():
            continue
        src = p.read_text(encoding="utf-8")
        if re.search(r'add_argument\(\s*"--base"', src):
            out.append(rel)
    return out


class TestEveryDiffScopedGuardCarriesIt:
    def test_the_population_is_not_empty(self):
        """Vacuity control. Without it, 'every guard carries the notice' is
        satisfied by a probe that finds no guards at all."""
        guards = _base_accepting_guards()
        assert len(guards) >= 20, guards

    @pytest.mark.parametrize("rel", _base_accepting_guards())
    def test_it_calls_the_shared_notice(self, rel):
        src = (ROOT / rel).read_text(encoding="utf-8")
        assert "_dirty_tree.warn()" in src, (
            f"{rel} accepts --base, so it grades a commit range, and a session "
            f"chasing a named CI failure types IT rather than run_guards.py")

    @pytest.mark.parametrize("rel", _base_accepting_guards())
    def test_it_still_runs(self, rel):
        r = subprocess.run([sys.executable, str(ROOT / rel), "--help"],
                           capture_output=True, text=True, cwd=str(ROOT))
        assert r.returncode == 0, (rel, r.returncode, r.stderr[-300:])

    def test_the_notice_is_reused_and_not_re_implemented(self):
        """One owner. A second copy of 'is the tree dirty' is how the two drift,
        which is the shape of the original defect one level up."""
        offenders = []
        for rel in _base_accepting_guards():
            src = (ROOT / rel).read_text(encoding="utf-8")
            if "UNCOMMITTED WORK (" in src:
                offenders.append(rel)
        assert offenders == [], offenders


class TestTheOrchestratorDeduplicates:
    """⚠️ FOUND BY MEASURING MY OWN CHANGE, not by reasoning about it.

    Wiring the notice into 24 guards made `run_guards.py` print it once per
    sub-invocation — MEASURED at 4 copies from a single selected guard, because
    a guard's self-test and its real run are separate processes. Over the full
    set that is dozens of identical paragraphs about one true fact, while the
    orchestrator already states it ONCE with more in it. That is the
    desensitised alarm this repo calls its own P1, introduced by the fix for it.
    """

    def test_run_guards_spawns_its_children_with_the_notice_OFF(self):
        src = (ROOT / "scripts" / "ci" / "run_guards.py").read_text(encoding="utf-8")
        assert "DIRTY_TREE_NOTICE" in src, (
            "run_guards prints its own richer notice; if it stops suppressing "
            "the per-guard one, every run gains dozens of duplicates")
        assert "env=_child_env()" in src, (
            "the suppression must be ON THE SPAWN, not merely defined")

    def test_the_orchestrator_still_prints_its_OWN_notice(self):
        """The suppression must not silence the thing it deduplicates TO."""
        src = (ROOT / "scripts" / "ci" / "run_guards.py").read_text(encoding="utf-8")
        assert "every guard above is " in src and "scoped to a COMMIT RANGE" in src

    def test_the_two_notices_are_distinguishable_in_output(self):
        """They are different sentences on purpose, so a reader (and a probe)
        can tell which layer spoke. A shared string would make the dedup
        unmeasurable."""
        orchestrator = (ROOT / "scripts" / "ci" / "run_guards.py").read_text(encoding="utf-8")
        per_guard = (ROOT / "scripts" / "ci" / "_dirty_tree.py").read_text(encoding="utf-8")
        assert "this guard graded a" in per_guard
        assert "this guard graded a" not in orchestrator
