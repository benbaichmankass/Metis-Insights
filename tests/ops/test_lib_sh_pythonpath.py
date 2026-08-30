"""`scripts/ops/_lib.sh` must put the repo root on PYTHONPATH.

`BL-20260830-OPS-SCRIPTS-IMPORT-SRC-WITHOUT-A-SYS-PATH-BOOTSTRAP`.

Ten scripts under `scripts/` do `from src...` with no sys.path bootstrap of
their own. The wrappers `cd "${REPO_DIR}"` first, which looks sufficient and is
not: `python3 /abs/path/script.py` seeds `sys.path[0]` with the SCRIPT'S
directory, never the cwd. The first such script to ship died on its very first
live dispatch (#10446) before reaching any of its four safety guards.

⚠️ EVERY TEST HERE RUNS A SUBPROCESS FROM A FOREIGN CWD, and that is the whole
point. An in-process test CANNOT fail on this class — pytest already places the
repo root on `sys.path`, so `from src...` resolves and the missing bootstrap is
invisible. Sixteen in-process unit tests passed on the script that then died in
production. A test that cannot fail is not evidence.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
LIB = REPO / "scripts" / "ops" / "_lib.sh"

# Measured 2026-08-30: scripts importing src.* with no bootstrap of their own.
# A MODULE-LEVEL importer is used for the executable probe because `--help`
# exits inside argparse before a function-local import ever runs — which is
# exactly why `--help` produced a false all-clear on five of these.
_MODULE_LEVEL_IMPORTER = REPO / "scripts" / "ops" / "dead_leg_audit.py"


def _clean_env(**over: str) -> dict:
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env.update(over)
    return env


def test_lib_sh_exports_repo_root_on_pythonpath():
    """Sourced from a foreign cwd, _lib.sh must export the repo root."""
    out = subprocess.run(
        ["bash", "-c", f'source "{LIB}" >/dev/null 2>&1; printf "%s" "$PYTHONPATH"'],
        cwd=tempfile.gettempdir(), env=_clean_env(REPO_DIR=str(REPO)),
        capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert str(REPO) in out.stdout.split(":"), out.stdout


def test_an_existing_pythonpath_survives():
    """Append, never overwrite — a caller's own PYTHONPATH must not be lost."""
    out = subprocess.run(
        ["bash", "-c", f'source "{LIB}" >/dev/null 2>&1; printf "%s" "$PYTHONPATH"'],
        cwd=tempfile.gettempdir(),
        env=_clean_env(REPO_DIR=str(REPO), PYTHONPATH="/pre/existing"),
        capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    parts = out.stdout.split(":")
    assert str(REPO) in parts and "/pre/existing" in parts, out.stdout


def test_a_wrapper_invoked_script_can_import_src():
    """The end-to-end property: sourcing _lib.sh then running an affected
    script BY ABSOLUTE PATH from a foreign cwd must import `src` cleanly."""
    if not _MODULE_LEVEL_IMPORTER.exists():
        pytest.skip(f"{_MODULE_LEVEL_IMPORTER} not present")
    out = subprocess.run(
        ["bash", "-c",
         f'source "{LIB}" >/dev/null 2>&1; '
         f'exec "{sys.executable}" "{_MODULE_LEVEL_IMPORTER}" --help'],
        cwd=tempfile.gettempdir(), env=_clean_env(REPO_DIR=str(REPO)),
        capture_output=True, text=True, timeout=120)
    combined = out.stdout + out.stderr
    assert "ModuleNotFoundError" not in combined, combined[-800:]
    assert "No module named 'src'" not in combined, combined[-800:]


def test_the_probe_is_not_vacuous():
    """CONTROL: the same invocation WITHOUT _lib.sh must still fail.

    Without this, the test above could pass because the environment already
    resolves `src` for some unrelated reason, and it would then be green no
    matter what _lib.sh does.
    """
    if not _MODULE_LEVEL_IMPORTER.exists():
        pytest.skip(f"{_MODULE_LEVEL_IMPORTER} not present")
    out = subprocess.run(
        [sys.executable, str(_MODULE_LEVEL_IMPORTER), "--help"],
        cwd=tempfile.gettempdir(), env=_clean_env(),
        capture_output=True, text=True, timeout=120)
    combined = out.stdout + out.stderr
    assert "No module named 'src'" in combined, (
        "the control did NOT fail, so the test above proves nothing about "
        f"_lib.sh: {combined[-800:]}")
