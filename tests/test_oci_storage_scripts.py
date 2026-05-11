"""Syntax and shebang smoke tests for OCI storage automation scripts.

These run in CI without the OCI CLI installed, so they only verify:
  - the scripts exist,
  - they start with a bash shebang,
  - `bash -n` parses cleanly,
  - `--help` exits 0 (no side effects).

The workflow invokes every script as `bash scripts/foo.sh ...`, so the
executable bit is not required.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

SCRIPTS = [
    "scripts/oci_volume_status.sh",
    "scripts/oci_create_volume.sh",
    "scripts/oci_attach_volume.sh",
    "scripts/oci_vm_ssh.sh",
    "scripts/verify_storage_setup.sh",
]

HELP_SCRIPTS = [
    "scripts/oci_volume_status.sh",
    "scripts/oci_create_volume.sh",
    "scripts/oci_attach_volume.sh",
    "scripts/oci_vm_ssh.sh",
]


@pytest.mark.parametrize("rel", SCRIPTS)
def test_script_has_bash_shebang(rel: str) -> None:
    path = ROOT / rel
    assert path.is_file(), f"missing: {rel}"
    first = path.read_text(encoding="utf-8").splitlines()[0]
    assert first.startswith("#!") and "bash" in first, (
        f"{rel} missing bash shebang (got {first!r})"
    )


@pytest.mark.parametrize("rel", SCRIPTS)
def test_script_bash_syntax_ok(rel: str) -> None:
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash not available")
    result = subprocess.run(
        [bash, "-n", str(ROOT / rel)],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, f"syntax error in {rel}:\n{result.stderr}"


@pytest.mark.parametrize("rel", HELP_SCRIPTS)
def test_script_help_exits_zero(rel: str) -> None:
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash not available")
    result = subprocess.run(
        [bash, str(ROOT / rel), "--help"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, f"--help failed for {rel}:\n{result.stderr}"
