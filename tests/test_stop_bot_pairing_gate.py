"""The `stop-bot-service` pairing gate — refuse a stop the watchdog would undo.

WHY THIS EXISTS. `stop_bot.sh` stops the live trader so a repair can run in a
bounded window. The liveness watchdog (`check_heartbeat.py`, every 60 s) issues
`systemctl restart` on the trader once its heartbeat goes stale, so a stop taken
without `pause-autoheal` is SILENTLY UNDONE minutes later. The operator then
sees a stop that "did not work" rather than one that was reverted — different
failures, and only one of them is the script's.

That makes the gate a SAFETY property, not a convenience, and an untested
refusal gate is the "guard that cannot fail" shape this repo already has a
finding about. These tests shim `systemctl` on PATH so the decision is asserted
directly — no systemd, no VM, deterministic.

The gate reads the timer's **is-active**, not just is-enabled: a
disabled-but-still-running timer is precisely the one that would undo the stop.

These run as root or not: the script picks `systemctl` directly when root and
`sudo systemctl` otherwise, and the shim below supplies BOTH on PATH, so the
same assertions hold on either branch. An earlier draft skipped under root,
which would have shipped three tests that could never fail.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "ops" / "stop_bot.sh"


def _shim(tmp_path: Path, *, watchdog_active: str, trader_active: str = "active") -> dict:
    """PATH with a fake `systemctl` (and `sudo`) answering the two state reads."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "systemctl").write_text(
        "#!/usr/bin/env bash\n"
        "case \"$*\" in\n"
        "  *--version*) exit 0 ;;\n"
        "  *list-units*) exit 0 ;;\n"
        f"  *is-active*ict-liveness-watchdog.timer*) echo '{watchdog_active}'; exit 0 ;;\n"
        f"  *is-enabled*ict-liveness-watchdog.timer*) echo 'enabled'; exit 0 ;;\n"
        f"  *is-active*ict-trader-live.service*) echo '{trader_active}'; exit 0 ;;\n"
        "  *stop*) echo 'STOP-ISSUED'; exit 0 ;;\n"
        "  *) exit 0 ;;\n"
        "esac\n"
    )
    (bindir / "systemctl").chmod(0o755)
    # Force the non-root branch to resolve to our shim rather than real sudo.
    (bindir / "sudo").write_text('#!/usr/bin/env bash\nexec "$@"\n')
    (bindir / "sudo").chmod(0o755)
    env = dict(os.environ)
    env["PATH"] = f"{bindir}:{env['PATH']}"
    return env


def _run(env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=120
    )


def test_refuses_while_the_liveness_watchdog_is_active(tmp_path):
    r = _run(_shim(tmp_path, watchdog_active="active"))
    assert r.returncode == 4, (r.returncode, r.stdout[-2000:], r.stderr[-2000:])
    combined = r.stdout + r.stderr
    assert "ABORT" in combined
    assert "pause-autoheal" in combined, "the refusal must name the action that unblocks it"
    # The whole point: it must not have reached the stop.
    assert "STOP-ISSUED" not in combined


def test_proceeds_once_the_watchdog_is_inactive(tmp_path):
    # Same script, only the watchdog state differs — so a pass here is evidence
    # the refusal above is driven by THAT read and not by some unrelated abort.
    r = _run(_shim(tmp_path, watchdog_active="inactive", trader_active="inactive"))
    combined = r.stdout + r.stderr
    assert r.returncode == 0, (r.returncode, combined[-2000:])
    assert "STOP-ISSUED" in combined


def test_an_unreadable_watchdog_state_does_not_silently_proceed_as_active(tmp_path):
    # `unknown` is "we could not look". It must not be treated as "active"
    # (which would make the gate unpassable on a read failure) — but the run
    # must still say what it saw, so a reader can tell a graded state from an
    # ungraded one.
    r = _run(_shim(tmp_path, watchdog_active="unknown", trader_active="inactive"))
    combined = r.stdout + r.stderr
    assert "is-active=unknown" in combined, "the observed watchdog state must be reported verbatim"
    assert r.returncode == 0
