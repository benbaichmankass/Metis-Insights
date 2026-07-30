"""The shallow-clone history guard (BL-20260730-SHALLOW-CLONE-DEFEATS-HISTORY-RULE).

The guard's whole value is that it REFUSES rather than answering a history question
wrongly. So the load-bearing tests are the refusal paths — a guard that silently
degraded to "looks fine" would reproduce the exact defect it exists to catch.

These build real git repos in tmp_path (shallow ones via a depth-limited clone) rather
than monkeypatching `is_shallow`, because the bug was never in our logic — it was in
what git actually reports. Mocking it away would test the mock.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts", "ops"))

ghc = pytest.importorskip("git_history_check")

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git absent")


def _run(args, cwd):
    subprocess.run(args, cwd=cwd, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


@pytest.fixture
def full_repo(tmp_path):
    """A normal repo with several commits touching a tracked file."""
    src = tmp_path / "src"
    src.mkdir()
    _run(["git", "init", "-q", "-b", "main"], src)
    _run(["git", "config", "user.email", "t@t"], src)
    _run(["git", "config", "user.name", "t"], src)
    for i in range(5):
        (src / "cfg.yaml").write_text(f"value: {i}\n", encoding="utf-8")
        _run(["git", "add", "cfg.yaml"], src)
        _run(["git", "commit", "-q", "-m", f"c{i}"], src)
    return src


@pytest.fixture
def shallow_repo(tmp_path, full_repo):
    """A genuinely shallow clone — depth 1 — of the repo above."""
    dst = tmp_path / "shallow"
    _run(["git", "clone", "-q", "--depth", "1", f"file://{full_repo}", str(dst)],
         tmp_path)
    return dst


class TestDetection:
    def test_full_repo_is_not_shallow(self, full_repo):
        assert ghc.is_shallow(str(full_repo)) is False

    def test_shallow_clone_is_detected(self, shallow_repo):
        assert ghc.is_shallow(str(shallow_repo)) is True

    def test_depth_reflects_truncation(self, full_repo, shallow_repo):
        assert ghc.history_depth(str(full_repo)) == 5
        assert ghc.history_depth(str(shallow_repo)) == 1

    def test_a_non_repo_is_not_reported_as_shallow(self, tmp_path):
        """Reporting 'shallow' here would send a caller chasing a fetch that
        cannot possibly help — the wrong diagnosis, not just a useless one."""
        plain = tmp_path / "notgit"
        plain.mkdir()
        assert ghc.is_shallow(str(plain)) is False


class TestWarning:
    def test_no_warning_on_full_history(self, full_repo):
        assert ghc.shallow_warning(str(full_repo)) is None

    def test_warning_names_the_risk_and_the_fix(self, shallow_repo):
        w = ghc.shallow_warning(str(shallow_repo))
        assert w
        # The point of the message is that the WRONG ANSWER is silent.
        assert "PLAUSIBLE BUT WRONG" in w
        assert "deepen" in w
        assert "BL-20260730-SHALLOW-CLONE-DEFEATS-HISTORY-RULE" in w

    def test_severity_scales_with_depth(self, shallow_repo, monkeypatch):
        """A warning that describes a depth-1 clone while looking at a deep-but-
        bounded one is an overclaim, and an overclaiming alarm gets walked past —
        the alarm-fatigue failure mode named in CLAUDE.md."""
        severe = ghc.shallow_warning(str(shallow_repo))
        assert "files read as having ~one commit" in severe

        monkeypatch.setattr(ghc, "history_depth", lambda cwd=None: 2719)
        deep = ghc.shallow_warning(str(shallow_repo))
        assert "files read as having ~one commit" not in deep
        # Still must state the residual risk, not shrug it off.
        assert "INVISIBLE" in deep and "UNKNOWN, not absent" in deep

    def test_unknown_depth_is_treated_as_severe(self, shallow_repo, monkeypatch):
        """Fail-closed: if we cannot measure the depth, assume the worse case."""
        monkeypatch.setattr(ghc, "history_depth", lambda cwd=None: None)
        assert "files read as having ~one commit" in ghc.shallow_warning(
            str(shallow_repo))


class TestFailsClosed:
    """THE load-bearing behaviour: refuse, do not degrade."""

    def test_require_full_history_raises_on_shallow(self, shallow_repo):
        with pytest.raises(ghc.ShallowCloneError):
            ghc.require_full_history(str(shallow_repo))

    def test_require_full_history_passes_on_full(self, full_repo):
        ghc.require_full_history(str(full_repo))  # must not raise

    def test_file_history_refuses_on_shallow(self, shallow_repo):
        with pytest.raises(ghc.ShallowCloneError):
            ghc.file_history("cfg.yaml", cwd=str(shallow_repo))

    def test_file_history_answers_on_full(self, full_repo):
        out = ghc.file_history("cfg.yaml", n=10, cwd=str(full_repo))
        assert out.count("\n") == 5, "should see all five commits"

    def test_truncation_is_opt_in_and_visibly_wrong(self, shallow_repo):
        """allow_shallow exists so the tolerance is explicit at the call site.
        Note what it returns: ONE commit for a file that has five — which is
        exactly the wrong answer the guard exists to stop being implicit."""
        out = ghc.file_history("cfg.yaml", n=10, cwd=str(shallow_repo),
                               allow_shallow=True)
        assert out.count("\n") == 1


class TestCli:
    def test_exit_1_on_shallow(self, shallow_repo, monkeypatch):
        monkeypatch.setattr(ghc, "REPO", str(shallow_repo))
        assert ghc.main(["--quiet"]) == 1

    def test_exit_0_on_full(self, full_repo, monkeypatch):
        monkeypatch.setattr(ghc, "REPO", str(full_repo))
        assert ghc.main(["--quiet"]) == 0

    def test_file_query_exits_nonzero_on_shallow(self, shallow_repo, monkeypatch):
        """A caller shelling out must be able to detect the refusal by exit code —
        a zero exit with a short log is the failure mode being prevented."""
        monkeypatch.setattr(ghc, "REPO", str(shallow_repo))
        assert ghc.main(["--file", "cfg.yaml", "--quiet"]) == 1

    def test_file_query_succeeds_on_full(self, full_repo, monkeypatch, capsys):
        monkeypatch.setattr(ghc, "REPO", str(full_repo))
        assert ghc.main(["--file", "cfg.yaml", "--quiet"]) == 0
        assert "c4" in capsys.readouterr().out


class TestThisRepo:
    def test_the_hint_is_a_real_command(self):
        assert "git fetch" in ghc.DEEPEN_HINT

    def test_guard_runs_against_the_live_checkout_without_raising(self):
        """It must be callable here regardless of this checkout's depth — the guard
        reports, it never crashes the caller that asked."""
        assert isinstance(ghc.is_shallow(), bool)
