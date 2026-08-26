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


def _shim(tmp_path: Path, *, watchdog_active: str, trader_active: str = "active",
          force_nonroot: bool = False) -> dict:
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
    #
    # ⚠️ IT MUST STRIP SUDO'S OWN FLAGS FIRST. The script probes with
    # `sudo -n systemctl --version`, and a naive `exec "$@"` stub hands `-n` to
    # bash's `exec`, which rejects it ("invalid option") -- so the probe fails,
    # the script takes its "passwordless sudo unavailable" branch, and exits 1
    # BEFORE the pairing gate is ever evaluated. Every assertion below would
    # then be measuring an abort, not the gate. That is the same
    # tests-that-cannot-fail shape as the root-skip this file already removed,
    # just on the other branch -- and it is precisely what CI caught.
    (bindir / "sudo").write_text(
        "#!/usr/bin/env bash\n"
        "while [ $# -gt 0 ]; do\n"
        "  case \"$1\" in\n"
        "    -*) shift ;;\n"
        "    *) break ;;\n"
        "  esac\n"
        "done\n"
        'exec "$@"\n'
    )
    (bindir / "sudo").chmod(0o755)
    env = dict(os.environ)
    env["PATH"] = f"{bindir}:{env['PATH']}"
    # record_audit() writes under ${REPO_DIR}/runtime_logs/operator_actions, which
    # defaults to the VM path /home/ubuntu/ict-trading-bot and does not exist on a
    # runner. Point it at the tmp tree so the audit write is real and assertable
    # rather than an mkdir error the script has to tolerate.
    if force_nonroot:
        # Shim `id` so the script takes its `sudo systemctl` branch even when the
        # test host IS root. Without this the branch a runner actually uses is
        # only ever exercised on a runner -- which is how the `exec "$@"` sudo
        # stub above reached CI green locally and failed there. `id` is not a
        # bash builtin, so a PATH stub genuinely intercepts it.
        (bindir / "id").write_text(
            "#!/usr/bin/env bash\n"
            'case "$*" in\n'
            "  *-u*) echo 1000 ;;\n"
            "  *) echo runner ;;\n"
            "esac\n"
        )
        (bindir / "id").chmod(0o755)
    env["REPO_DIR"] = str(tmp_path / "repo")
    (tmp_path / "repo").mkdir(exist_ok=True)
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


def test_the_gate_holds_on_the_sudo_branch_too(tmp_path):
    """The refusal must not depend on which privilege branch the script took.

    CI runs as a non-root user and the sandbox that wrote this file runs as root,
    so a fixture that only ever exercises one branch leaves the other untested on
    every host that could catch it. Forcing `id -u` non-zero pins the sudo branch
    regardless of who is running, which is the branch the runner uses.
    """
    r = _run(_shim(tmp_path, watchdog_active="active", force_nonroot=True))
    combined = r.stdout + r.stderr
    # The abort must be the pairing gate, NOT the "passwordless sudo" bail-out —
    # those are different exits and only one of them is the property under test.
    assert "passwordless sudo" not in combined, combined[-2000:]
    assert r.returncode == 4, (r.returncode, combined[-2000:])
    assert "STOP-ISSUED" not in combined
