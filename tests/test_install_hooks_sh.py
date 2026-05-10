"""Tests for the opt-in pre-commit hook (S-AI-WS10 follow-up).

Two-pronged:

1. `scripts/git-hooks/pre-commit` behaves correctly given a synthetic
   `git diff --cached --name-only` output (high-impact paths only,
   high-impact + arch doc, no high-impact).
2. `scripts/install-hooks.sh` creates the symlink correctly and is
   idempotent.

The hook calls `git rev-parse` + `git diff --cached --name-only`,
so we run it inside a temporary git repo so the assertions are
real, not mocked.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_HOOK = _REPO_ROOT / "scripts" / "git-hooks" / "pre-commit"
_INSTALLER = _REPO_ROOT / "scripts" / "install-hooks.sh"
_GUARD = _REPO_ROOT / "scripts" / "arch_doc_guard.py"


def _make_synthetic_repo(tmp_path: Path) -> Path:
    """Create a synthetic git repo with the same `scripts/` tree as
    this repo, so the hook can find `arch_doc_guard.py` via the
    repo-root resolution it does internally."""
    repo = tmp_path / "synth-repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    # Copy the script tree the hook depends on.
    shutil.copytree(_REPO_ROOT / "scripts", repo / "scripts")
    return repo


def _stage(repo: Path, file_rel: str, content: str = "x\n") -> None:
    target = repo / file_rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    subprocess.run(
        ["git", "-C", str(repo), "add", file_rel], check=True,
    )


def _run_hook(repo: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["/bin/bash", str(repo / "scripts" / "git-hooks" / "pre-commit")],
        cwd=str(repo), capture_output=True, text=True, timeout=15,
    )


class TestPreCommitHook:
    def test_no_staged_files_exits_zero(self, tmp_path: Path):
        repo = _make_synthetic_repo(tmp_path)
        r = _run_hook(repo)
        assert r.returncode == 0
        assert r.stderr.strip() == ""

    def test_only_noise_files_exits_zero(self, tmp_path: Path):
        repo = _make_synthetic_repo(tmp_path)
        _stage(repo, "tests/test_foo.py")
        _stage(repo, "README.md")
        r = _run_hook(repo)
        assert r.returncode == 0
        assert "::warning" not in r.stderr

    def test_high_impact_without_doc_blocks(self, tmp_path: Path):
        repo = _make_synthetic_repo(tmp_path)
        _stage(repo, "src/runtime/pipeline.py")
        r = _run_hook(repo)
        assert r.returncode == 1
        # The warning should mention the path AND point to the checklist.
        assert "pipeline.py" in r.stderr
        assert "ARCHITECTURE-CHANGE-CHECKLIST" in r.stderr
        assert "--no-verify" in r.stderr

    def test_high_impact_with_arch_doc_passes(self, tmp_path: Path):
        repo = _make_synthetic_repo(tmp_path)
        _stage(repo, "src/runtime/pipeline.py")
        _stage(repo, "docs/ARCHITECTURE-CANONICAL.md", "# update\n")
        r = _run_hook(repo)
        assert r.returncode == 0
        assert "::warning" not in r.stderr


class TestInstallHooks:
    def test_install_creates_symlink(self, tmp_path: Path):
        repo = _make_synthetic_repo(tmp_path)
        r = subprocess.run(
            ["/bin/bash", "scripts/install-hooks.sh"],
            cwd=str(repo), capture_output=True, text=True, timeout=10,
        )
        assert r.returncode == 0, r.stderr
        hook_link = repo / ".git" / "hooks" / "pre-commit"
        assert hook_link.is_symlink()
        # The symlink resolves to the source.
        resolved = hook_link.resolve()
        assert resolved.name == "pre-commit"
        assert resolved.parent.name == "git-hooks"

    def test_install_is_idempotent(self, tmp_path: Path):
        repo = _make_synthetic_repo(tmp_path)
        for _ in range(2):
            r = subprocess.run(
                ["/bin/bash", "scripts/install-hooks.sh"],
                cwd=str(repo), capture_output=True, text=True, timeout=10,
            )
            assert r.returncode == 0, r.stderr
        # The symlink still resolves correctly.
        hook_link = repo / ".git" / "hooks" / "pre-commit"
        assert hook_link.is_symlink()
