"""`status_check.sh`'s trader-env block must express THREE states, not two.

WHY THIS FILE EXISTS. `BL-20260811-STATUSCHECK-PID-LOOKUP-AND-STALE-REPO-HEARTBEAT`
was a COLLAPSED STATE: one string, "(trader pid not found or /proc unreadable —
env dump skipped)", served both "the unit is not running" and "we have a PID but
could not read it" — opposite diagnoses. The fix in `5a5ebdd` split them.

The post-deploy verification (status-check #8813) confirmed the happy path and
the not-running path, but `/proc` was readable on that run, so **the branch
carrying the entire point of the fix never executed**. A happy-path verification
of a collapsed-state fix confirms the least interesting half, and the rule the
parent row was filed under says a diff is not verification.

The test runs the REAL block, extracted from the shipped script text rather than
copied into the test, so collapsing the two messages back into one fails here
even if the copy in this file would still have "passed".
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "ops" / "status_check.sh"

# The exact opening line of the three-state block in the shipped script. If this
# stops matching, the test FAILS rather than silently testing nothing — an
# extraction that quietly finds no block is the unasserted-denominator trap.
_BLOCK_START = 'if [ -z "${TRADER_PID}" ]; then'


def _extract_block() -> str:
    """Pull the three-state `if/elif/else/fi` out of the real script.

    Deliberately NOT a copy of the block pasted into this file: the whole point
    is to assert on the text that actually ships. Balance is tracked by counting
    `if`/`fi` so the nested inner `if [ -n "${env_match}" ]` does not terminate
    the extraction early.
    """
    text = SCRIPT.read_text()
    assert _BLOCK_START in text, (
        f"three-state block not found in {SCRIPT}. The block was renamed or "
        f"removed — fix this extractor rather than deleting the test, or the "
        f"collapsed-state regression it guards becomes invisible."
    )
    lines = text.splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.strip() == _BLOCK_START)
    depth = 0
    for i in range(start, len(lines)):
        stripped = lines[i].strip()
        if stripped.startswith("if ") or stripped == "if":
            depth += 1
        elif stripped == "fi":
            depth -= 1
            if depth == 0:
                return "\n".join(lines[start : i + 1])
    raise AssertionError("unterminated three-state block in status_check.sh")


def _run(trader_pid: str) -> str:
    """Execute the extracted block with TRADER_PID pre-set."""
    snippet = f'TRADER_PID="{trader_pid}"\n' + _extract_block()
    proc = subprocess.run(
        ["bash", "-c", snippet],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, f"block exited {proc.returncode}: {proc.stderr}"
    return proc.stdout


class TestThreeStates:
    def test_no_mainpid_says_not_running(self):
        out = _run("")
        assert "no MainPID" in out
        assert "not running" in out
        # Must NOT claim anything about readability — it never looked.
        assert "COULD NOT LOOK" not in out

    def test_unreadable_proc_says_we_could_not_look(self):
        """The branch the live verification never exercised.

        PID 0 is never a real process, so `/proc/0/environ` is unreadable — the
        same condition a permissions failure produces, which is exactly the
        state that must not read as "the env is unset".
        """
        out = _run("0")
        assert "COULD NOT LOOK" in out
        assert "NOT evidence the env is unset" in out
        # It still reports the PID it had, so the reader can chase it.
        assert "trader pid: 0" in out
        # And it must not borrow the not-running wording.
        assert "no MainPID" not in out

    @pytest.mark.skipif(
        not os.path.isdir("/proc/self"), reason="no procfs on this platform"
    )
    def test_readable_proc_reports_the_env(self):
        out = _run(str(os.getpid()))
        assert f"trader pid: {os.getpid()}" in out
        assert "COULD NOT LOOK" not in out
        assert "no MainPID" not in out

    @pytest.mark.skipif(
        not os.path.isdir("/proc/self"), reason="no procfs on this platform"
    )
    def test_readable_but_unset_is_distinct_from_could_not_look(self, monkeypatch):
        """'read it, nothing set' and 'could not read it' are different answers.

        This is the same collapse one level in: a successful read that finds no
        DATA_DIR must not be reported with the could-not-look wording, or the
        third state buys nothing.
        """
        out = _run(str(os.getpid()))
        if "DATA_DIR=" not in out:
            assert "read the trader env successfully" in out
            assert "COULD NOT LOOK" not in out


class TestTheStatesAreDistinctStrings:
    def test_the_three_messages_share_no_literal(self):
        """A refactor that collapses two branches back into one string fails here.

        Asserted on the distinctness of the LITERALS rather than on any one
        wording, because the defect being guarded is two conditions arriving at
        the same sentence — whatever that sentence happens to say.
        """
        outs = {
            "no_pid": _run(""),
            "unreadable": _run("0"),
            "readable": _run(str(os.getpid())),
        }
        # Normalise away the PID so "trader pid: N" doesn't count as a shared
        # message — it is a fact, not a diagnosis.
        norm = {
            k: re.sub(r"trader pid: \d+", "", v).strip() for k, v in outs.items()
        }
        assert norm["no_pid"] != norm["unreadable"], (
            "not-running and could-not-look produced the SAME text — this is "
            "the exact collapse BL-20260811 was filed for"
        )
        assert norm["unreadable"] != norm["readable"]
        assert norm["no_pid"] != norm["readable"]
