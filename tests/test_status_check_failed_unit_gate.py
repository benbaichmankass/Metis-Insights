"""FIX-CA-15 / `CA-A10-402`: a failed one-shot unit must flip status_check.sh's exit code.

WHY THIS FILE EXISTS. `scripts/ops/status_check.sh` hardcodes its exit-code
gate to three long-running services (`CANONICAL_UNITS`, checked via
`is-active`). The broader "all ict-* units" enumeration further down the
script explicitly documents itself as never affecting the exit code. That left
a failed money/deploy-freshness-adjacent oneshot (ict-git-sync,
ict-db-integrity, or an exchange fills/funding/executions pull) discoverable
only if a human or session happened to read the uncapped dump — no push
alert, no exit-code signal. Confirmed 2026-09-27 evidence:
`grep -rl OnFailure deploy/` -> 0 of 64 tracked files.

The test runs the REAL "failed-unit gate" block, extracted from the shipped
script by its BEGIN/END markers, against a fake `systemctl` on PATH — so a
future edit that removes or neuters the gate fails here rather than being
caught only by someone reading the diff.
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "ops" / "status_check.sh"

_BEGIN = "# FIX-CA-15 (CA-A10-402): failed-unit gate begin"
_END = "# FIX-CA-15 (CA-A10-402): failed-unit gate end"

_FAKE_SYSTEMCTL = """#!/usr/bin/env bash
# Minimal fake `systemctl` for the failed-unit-gate test: only understands
# `is-failed <unit>`, driven by $FAKE_FAILED_UNITS (space-separated).
if [ "$1" = "is-failed" ]; then
    unit="$2"
    for f in ${FAKE_FAILED_UNITS:-}; do
        if [ "$f" = "$unit" ]; then
            echo "failed"
            exit 0
        fi
    done
    echo "inactive"
    exit 3
fi
echo "unsupported fake systemctl command: $*" >&2
exit 1
"""


def _extract_block() -> str:
    text = SCRIPT.read_text()
    assert _BEGIN in text and _END in text, (
        f"failed-unit-gate markers not found in {SCRIPT}. The block was "
        f"renamed or removed — fix this extractor rather than deleting the "
        f"test, or the CA-A10-402 regression it guards becomes invisible."
    )
    start = text.index(_BEGIN)
    end = text.index(_END) + len(_END)
    return text[start:end]


def _run(fake_failed_units: str, tmp_path: Path) -> tuple[str, int]:
    """Run the extracted block with overall_ok=0 and a fake failing systemctl."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    fake = bin_dir / "systemctl"
    fake.write_text(_FAKE_SYSTEMCTL)
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    snippet = (
        "overall_ok=0\n"
        + _extract_block()
        + '\necho "OVERALL_OK=${overall_ok}"\n'
    )
    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env["FAKE_FAILED_UNITS"] = fake_failed_units
    proc = subprocess.run(
        ["bash", "-c", snippet],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )
    assert proc.returncode == 0, f"block exited {proc.returncode}: {proc.stderr}"
    return proc.stdout, proc.returncode


class TestFailedUnitGate:
    def test_all_healthy_leaves_overall_ok_zero(self, tmp_path):
        out, _ = _run("", tmp_path)
        assert "OVERALL_OK=0" in out

    def test_a_failed_gated_oneshot_flips_overall_ok(self, tmp_path):
        """The must-fail-on-main case: a failed ict-git-sync must flip the gate."""
        out, _ = _run("ict-git-sync.service", tmp_path)
        assert "OVERALL_OK=1" in out
        assert "ict-git-sync.service" in out
        assert "failed" in out

    def test_each_named_gate_unit_is_actually_checked(self, tmp_path):
        for unit in (
            "ict-git-sync.service",
            "ict-db-integrity.service",
            "ict-exchange-fills-pull.service",
            "ict-alpaca-fills-pull.service",
            "ict-exchange-funding-pull.service",
            "ict-ib-executions-pull.service",
        ):
            out, _ = _run(unit, tmp_path)
            assert "OVERALL_OK=1" in out, f"{unit} failing did not flip the gate"

    def test_a_failed_unrelated_unit_does_not_flip_the_gate(self, tmp_path):
        """Only the named money/deploy-freshness oneshots gate — not every ict-* unit."""
        out, _ = _run("ict-some-unrelated-timer.service", tmp_path)
        assert "OVERALL_OK=0" in out
