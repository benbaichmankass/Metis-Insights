"""PI-20260927-YDVVYLKH-0002 — git-sync must never START a trader the operator
stopped on purpose, and must not re-run the liveness watchdog while its timer
is paused.

MEASURED 2026-09-27: stop-bot-service #13194 stopped ict-trader-live at
14:15:23Z; ict-git-sync (``scripts/deploy_pull_restart.sh``) restarted it at
14:16:59Z and 14:22:27Z because ``systemctl restart`` starts an inactive unit,
so the forced IB cancels #13203/#13204 hit Error 326.

The fix is an operator-stop marker (written by stop_bot.sh, cleared by
start_bot.sh / restart_bot.sh / pull_and_deploy.sh). These tests stub
``systemctl``/``git``/``python3`` via PATH shadowing (same shape as
test_deploy_pull_restart_enumeration.py) and pin:

1. marker + inactive trader  -> trader NOT restarted, other units still are.
2. marker + ACTIVE trader    -> stale marker: removed, trader restarted
   (keeps the BL-20260714 stale-code redeploy of a running trader).
3. no marker + inactive trader -> restarted as before (crash recovery kept).
4. watchdog timer inactive   -> ict-liveness-watchdog.service skipped.
5. watchdog timer active     -> ict-liveness-watchdog.service restarted.
6. the marker path in deploy_pull_restart.sh equals _lib.sh's, and each
   lifecycle script writes / clears it.
"""
from __future__ import annotations

import os
import re
import stat
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
DEPLOY_SCRIPT = REPO_ROOT / "scripts" / "deploy_pull_restart.sh"
OPS = REPO_ROOT / "scripts" / "ops"

TRADER = "ict-trader-live.service"
WATCHDOG = "ict-liveness-watchdog.service"


def _make_stub(path: Path, body: str) -> None:
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


@pytest.fixture
def fake(tmp_path: Path):
    repo = tmp_path / "ict-trading-bot"
    (repo / "scripts").mkdir(parents=True)
    (repo / "runtime_logs").mkdir()
    (repo / "runtime_flags").mkdir()
    (repo / "requirements.txt").write_text("")
    _make_stub(repo / "scripts" / "install_systemd_units.sh", "#!/bin/bash\nexit 0\n")

    patched = (
        DEPLOY_SCRIPT.read_text()
        .replace('REPO_DIR="/home/ubuntu/ict-trading-bot"', f'REPO_DIR="{repo}"')
        .replace("/usr/bin/python3", "python3")
    )
    deploy_copy = repo / "scripts" / "deploy_pull_restart.sh"
    deploy_copy.write_text(patched)
    deploy_copy.chmod(0o755)

    bindir = tmp_path / "bin"
    bindir.mkdir()

    # HEAD advances oldsha7 -> newsha8 so the restart phase runs; the diff
    # stub returns a runtime path so the non-runtime skip does not fire.
    counter = tmp_path / "rev_parse_counter"
    counter.write_text("0")
    _make_stub(
        bindir / "git",
        f"""#!/bin/bash
case "$1" in
  rev-parse)
    case "$2" in
      "HEAD~1") echo "0000000" ;;
      "--short") echo "newsha8" ;;
      *)
        if [ "$(cat "{counter}")" = "0" ]; then echo oldsha7; echo 1 > "{counter}"; else echo newsha8; fi ;;
    esac ;;
  diff) echo "src/main.py" ;;
  *) exit 0 ;;
esac
""",
    )

    units = tmp_path / "units.txt"
    units.write_text(
        f"{TRADER}     loaded active running ICT live trader\n"
        "ict-web-api.service         loaded active running ICT web API\n"
        f"{WATCHDOG} loaded inactive dead ICT liveness watchdog\n"
    )
    # Per-unit is-active answers; a missing file means "active".
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    restart_log = tmp_path / "restart.log"
    _make_stub(
        bindir / "systemctl",
        f"""#!/bin/bash
case "$1" in
  --version) echo "systemd 250" ;;
  list-units)
    pattern="${{*: -1}}"
    [[ "$pattern" == "ict-*.service" ]] && cat "{units}"
    ;;
  restart) echo "$2" >> "{restart_log}" ;;
  is-active)
    if [ -f "{state_dir}/$2" ]; then s=$(cat "{state_dir}/$2"); else s=active; fi
    echo "$s"; [ "$s" = active ] ;;
  *) exit 0 ;;
esac
""",
    )
    _make_stub(bindir / "sudo", '#!/bin/bash\nwhile [[ "$1" == -* ]]; do shift; done\nexec "$@"\n')
    _make_stub(bindir / "python3", "#!/bin/bash\nexit 0\n")

    def set_state(unit: str, state: str) -> None:
        (state_dir / unit).write_text(state)

    return {
        "repo": repo,
        "deploy": deploy_copy,
        "bindir": bindir,
        "restart_log": restart_log,
        "marker": repo / "runtime_logs" / "trader_operator_stop.json",
        "set_state": set_state,
    }


def _run(fake):
    env = {**os.environ, "PATH": f"{fake['bindir']}:/usr/bin:/bin"}
    env.pop("DEPLOY_RESTART_SKIP", None)
    env.pop("DEPLOY_FORCE_RESTART", None)
    # No web-api assertion: no DIAG token reachable in the sandbox.
    env["DIAG_TOKEN_FILE"] = str(fake["repo"] / "no-such-token")
    env.pop("DIAG_READ_TOKEN", None)
    return subprocess.run(
        ["bash", str(fake["deploy"])], capture_output=True, text=True, env=env, timeout=60
    )


def _restarted(fake) -> list[str]:
    log = fake["restart_log"]
    return [l.strip() for l in log.read_text().splitlines() if l.strip()] if log.exists() else []


def test_marker_with_inactive_trader_holds_it_stopped(fake):
    fake["marker"].write_text('{"action": "stop-bot-service"}\n')
    fake["set_state"](TRADER, "inactive")
    res = _run(fake)
    assert res.returncode == 0, res.stdout + res.stderr
    restarted = _restarted(fake)
    assert TRADER not in restarted, res.stdout
    assert "ict-web-api.service" in restarted  # the rest of the deploy still runs
    assert fake["marker"].exists()  # only start/restart/pull-and-deploy clear it
    assert "operator stop marker present" in res.stdout


def test_marker_with_active_trader_is_stale_and_trader_still_redeploys(fake):
    """A running trader must still pick up new code (BL-20260714)."""
    fake["marker"].write_text('{"action": "stop-bot-service"}\n')
    res = _run(fake)
    assert res.returncode == 0, res.stdout + res.stderr
    assert TRADER in _restarted(fake)
    assert not fake["marker"].exists()


def test_no_marker_inactive_trader_is_still_restarted(fake):
    """Unchanged behaviour without a marker: a crashed/failed trader is revived."""
    fake["set_state"](TRADER, "failed")
    res = _run(fake)
    assert res.returncode == 0, res.stdout + res.stderr
    assert TRADER in _restarted(fake)


def test_watchdog_oneshot_skipped_while_timer_paused(fake):
    fake["set_state"]("ict-liveness-watchdog.timer", "inactive")
    res = _run(fake)
    assert res.returncode == 0, res.stdout + res.stderr
    assert WATCHDOG not in _restarted(fake)
    assert "autoheal paused" in res.stdout


def test_watchdog_oneshot_restarted_when_timer_active(fake):
    res = _run(fake)
    assert res.returncode == 0, res.stdout + res.stderr
    assert WATCHDOG in _restarted(fake)


def test_marker_path_agrees_with_lib_and_lifecycle_scripts_use_it():
    deploy_src = DEPLOY_SCRIPT.read_text()
    lib_src = (OPS / "_lib.sh").read_text()
    pat = r'TRADER_STOP_MARKER="\$\{REPO_DIR\}/([^"]+)"'
    deploy_path = re.search(pat, deploy_src)
    lib_path = re.search(pat, lib_src)
    assert deploy_path and lib_path
    assert deploy_path.group(1) == lib_path.group(1) == "runtime_logs/trader_operator_stop.json"

    stop_src = (OPS / "stop_bot.sh").read_text()
    # written BEFORE the stop so a sync tick between the two cannot revive it
    assert stop_src.index('> "${TRADER_STOP_MARKER}"') < stop_src.index('stop "${UNIT}"')
    for name, action in (
        ("start_bot.sh", "start-bot-service"),
        ("restart_bot.sh", "restart-bot-service"),
        ("pull_and_deploy.sh", "pull-and-deploy"),
    ):
        assert f'clear_trader_stop_marker "{action}"' in (OPS / name).read_text(), name


def test_clear_trader_stop_marker_helper(tmp_path):
    repo = tmp_path / "repo"
    (repo / "runtime_logs").mkdir(parents=True)
    marker = repo / "runtime_logs" / "trader_operator_stop.json"
    marker.write_text("{}")
    res = subprocess.run(
        ["bash", "-c", f'source "{OPS / "_lib.sh"}"; clear_trader_stop_marker test; clear_trader_stop_marker again'],
        capture_output=True, text=True, env={**os.environ, "REPO_DIR": str(repo)}, timeout=30,
    )
    assert res.returncode == 0, res.stderr
    assert not marker.exists()
    assert res.stderr.count("Cleared operator-stop marker") == 1
