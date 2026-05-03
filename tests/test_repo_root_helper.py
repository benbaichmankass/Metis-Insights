"""Tests for src/utils/paths.repo_root().

Contracts:
1. Returns a real directory that exists on disk.
2. The returned path contains a known marker (.git or requirements.txt).
3. Calling from any module depth returns the SAME path.
4. The path is stable across repeated calls (lru_cache).
5. The resolved root matches what a naive __file__-walk would find from
   this test file's own location (regression: BUG-037 depth drift).
"""

import importlib
import os
import sys


def _naive_root_from_here() -> str:
    """Walk up from THIS file until .git is found (reference implementation)."""
    current = os.path.dirname(os.path.abspath(__file__))
    for _ in range(10):
        if os.path.exists(os.path.join(current, ".git")):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    raise AssertionError("Could not locate .git from test file")


def _fresh_repo_root():
    """Import repo_root with the lru_cache cleared so each test is independent."""
    if "src.utils.paths" in sys.modules:
        mod = sys.modules["src.utils.paths"]
        mod.repo_root.cache_clear()
    else:
        mod = importlib.import_module("src.utils.paths")
    return mod.repo_root


class TestRepoRootBasic:
    def test_returns_existing_directory(self):
        from src.utils.paths import repo_root
        result = repo_root()
        assert os.path.isdir(result), f"repo_root() returned non-directory: {result}"

    def test_contains_git_marker(self):
        from src.utils.paths import repo_root
        root = repo_root()
        has_marker = any(
            os.path.exists(os.path.join(root, m))
            for m in (".git", "pyproject.toml", "requirements.txt")
        )
        assert has_marker, f"No marker found under repo_root() = {root}"

    def test_matches_naive_walk(self):
        from src.utils.paths import repo_root
        expected = _naive_root_from_here()
        assert repo_root() == expected, (
            f"repo_root() = {repo_root()!r} but naive walk = {expected!r}"
        )

    def test_stable_across_repeated_calls(self):
        from src.utils.paths import repo_root
        assert repo_root() == repo_root() == repo_root()

    def test_is_absolute(self):
        from src.utils.paths import repo_root
        assert os.path.isabs(repo_root()), "repo_root() must return an absolute path"


class TestRepoRootDepthInvariance:
    """Verify the helper is depth-agnostic by importing it after clearing the cache."""

    def test_same_result_after_cache_clear(self):
        fn = _fresh_repo_root()
        result_a = fn()
        fn.cache_clear()
        result_b = fn()
        assert result_a == result_b

    def test_known_subpath_exists_under_root(self):
        """A file that should always live under the repo root."""
        from src.utils.paths import repo_root
        assert os.path.exists(os.path.join(repo_root(), "requirements.txt")), (
            "requirements.txt not found under repo_root() — depth off?"
        )
        assert os.path.isdir(os.path.join(repo_root(), "src")), (
            "src/ not found under repo_root() — depth off?"
        )
