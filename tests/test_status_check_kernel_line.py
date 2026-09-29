"""`status_check.sh` reports the RUNNING kernel (manager 2026-09-29).

After the 1057 -> 1062 kernel reboot no relay could read which kernel the box
had booted. The test runs the shipped line itself, not a copy, so dropping or
breaking it fails here.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "ops" / "status_check.sh"


def test_status_check_prints_the_running_kernel():
    lines = [ln for ln in SCRIPT.read_text().splitlines() if "uname -r" in ln and ln.startswith("echo")]
    assert len(lines) == 1, lines
    out = subprocess.run(["bash", "-c", lines[0]], capture_output=True, text=True, check=True).stdout.strip()
    expected = subprocess.run(["uname", "-r"], capture_output=True, text=True, check=True).stdout.strip()
    assert out == f"uname -r: {expected}"
