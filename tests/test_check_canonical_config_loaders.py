"""Tests for ``scripts/check_canonical_config_loaders.py``.

The CI guard's correctness is itself load-bearing — a permissive guard
fails silently when a new hand-rolled parser slips in. These tests
pin the behavior on three axes:

  * **catches** hand-rolled ``yaml.safe_load`` on ``accounts.yaml``
    in non-allowlisted files,
  * **respects allowlist** — ``src/config/accounts_loader.py`` and
    ``src/units/accounts/__init__.py`` are exempt,
  * **per-function scoping** — a file that parses ``strategies.yaml``
    in one function and references ``accounts.yaml`` in a docstring
    elsewhere is NOT flagged. False positives like that would push
    operators to disable the guard.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_guard_module():
    path = _REPO_ROOT / "scripts" / "check_canonical_config_loaders.py"
    spec = importlib.util.spec_from_file_location(
        "check_canonical_config_loaders", path,
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_main_repo_is_clean():
    """Running the guard against the committed tree must succeed.
    If this fails it means somebody introduced a tenth parser
    without going through src/config/accounts_loader.py."""
    result = subprocess.run(
        [sys.executable, str(_REPO_ROOT / "scripts" / "check_canonical_config_loaders.py"), "--list"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"guard failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


def test_catches_handrolled_parser(tmp_path, monkeypatch):
    """A function that does yaml.safe_load and mentions accounts.yaml
    in the same scope must be flagged."""
    offender = tmp_path / "src" / "_evil" / "loader.py"
    offender.parent.mkdir(parents=True)
    offender.write_text(
        "import yaml\n"
        "def _load():\n"
        "    with open('config/accounts.yaml') as fh:\n"
        "        return yaml.safe_load(fh) or {}\n",
        encoding="utf-8",
    )
    mod = _load_guard_module()
    monkeypatch.setattr(mod, "_REPO_ROOT", tmp_path)
    offenders = mod._gather_offenders()
    assert len(offenders) == 1
    path, func, _line = offenders[0]
    assert path == offender
    assert func == "_load"


def test_respects_allowlist(tmp_path, monkeypatch):
    """Files inside the allowlist (under their canonical paths)
    parse accounts.yaml freely — that's their whole purpose."""
    allowed = tmp_path / "src" / "config" / "accounts_loader.py"
    allowed.parent.mkdir(parents=True)
    allowed.write_text(
        "import yaml\n"
        "def load():\n"
        "    with open('config/accounts.yaml') as fh:\n"
        "        return yaml.safe_load(fh)\n",
        encoding="utf-8",
    )
    mod = _load_guard_module()
    monkeypatch.setattr(mod, "_REPO_ROOT", tmp_path)
    offenders = mod._gather_offenders()
    assert offenders == []


def test_per_function_scoping_avoids_false_positives(tmp_path, monkeypatch):
    """A file that mentions ``accounts.yaml`` in a docstring but
    only does ``yaml.safe_load`` on a different file (in a different
    function) must NOT be flagged. This is the pattern that breaks a
    naïve text scan."""
    mixed = tmp_path / "src" / "runtime" / "two_parsers.py"
    mixed.parent.mkdir(parents=True)
    mixed.write_text(
        '"""This module mentions accounts.yaml only in this docstring."""\n'
        "import yaml\n"
        "def _read_strategies():\n"
        "    # parses strategies.yaml, not accounts.yaml\n"
        "    with open('config/strategies.yaml') as fh:\n"
        "        return yaml.safe_load(fh)\n",
        encoding="utf-8",
    )
    mod = _load_guard_module()
    monkeypatch.setattr(mod, "_REPO_ROOT", tmp_path)
    offenders = mod._gather_offenders()
    assert offenders == []


def test_catches_offender_via_pathlib(tmp_path, monkeypatch):
    """Path constructions that include the literal ``accounts.yaml``
    are equally catchable — the guard looks for the string anywhere
    in the function body."""
    offender = tmp_path / "scripts" / "evil.py"
    offender.parent.mkdir(parents=True)
    offender.write_text(
        "import yaml\n"
        "from pathlib import Path\n"
        "def _load():\n"
        "    p = Path('/etc/configs') / 'accounts.yaml'\n"
        "    with p.open() as fh:\n"
        "        return yaml.safe_load(fh)\n",
        encoding="utf-8",
    )
    mod = _load_guard_module()
    monkeypatch.setattr(mod, "_REPO_ROOT", tmp_path)
    offenders = mod._gather_offenders()
    assert len(offenders) == 1
