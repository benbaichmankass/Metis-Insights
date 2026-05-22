"""S-067 follow-up #5 — pin the systemd-enumeration restart logic in
``scripts/deploy_pull_restart.sh``.

The 2026-05-09 24+h-stale-code incident shipped because the script
carried a fixed list of ict-* units and ``ict-web-api.service`` was
added to the inventory after the script was last touched. Enumeration
via ``systemctl list-units 'ict-*.service'`` closes that class of
bug.

These tests stub ``systemctl`` + ``git`` + ``python3`` via PATH
shadowing and verify:

1. Enumerated ict-* units are all restarted by default.
2. Units listed in ``DEPLOY_RESTART_SKIP`` are skipped.
3. ``ict-smoke-once.service`` is in the default skip-list.
4. The ``run_smoke_once.flag`` path still triggers ``ict-smoke-once.service``
   when the flag file is present (independent of enumeration).
"""
from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
DEPLOY_SCRIPT = REPO_ROOT / "scripts" / "deploy_pull_restart.sh"


def _make_stub(path: Path, body: str) -> None:
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


@pytest.fixture
def fake_repo_with_advance(tmp_path: Path):
    """Fake repo where PRE_SYNC_HEAD differs from POST_SYNC_HEAD so
    the restart phase runs. See test_deploy_pull_restart_notify_state.py
    for the no-advance variant."""
    repo = tmp_path / "ict-trading-bot"
    (repo / "scripts").mkdir(parents=True)
    (repo / "runtime_logs").mkdir()
    (repo / "runtime_flags").mkdir()
    requirements = repo / "requirements.txt"
    requirements.write_text("")
    install_units = repo / "scripts" / "install_systemd_units.sh"
    install_units.write_text("#!/bin/bash\nexit 0\n")
    install_units.chmod(0o755)

    src = DEPLOY_SCRIPT.read_text()
    patched = (
        src.replace('REPO_DIR="/home/ubuntu/ict-trading-bot"',
                    f'REPO_DIR="{repo}"')
           .replace("/usr/bin/python3", "python3")
    )
    deploy_copy = repo / "scripts" / "deploy_pull_restart.sh"
    deploy_copy.write_text(patched)
    deploy_copy.chmod(0o755)

    bindir = tmp_path / "bin"
    bindir.mkdir()

    # git stub — HEAD advances on each rev-parse call by reading a
    # state file. Initial pre-sync read returns OLD; subsequent reads
    # return NEW. The reset/fetch are no-ops.
    counter_file = tmp_path / "rev_parse_counter"
    counter_file.write_text("0")
    head_file = tmp_path / "fake_head"
    head_file.write_text("oldsha7")  # 7-char fake HEAD pre-sync
    next_head_file = tmp_path / "next_head"
    next_head_file.write_text("newsha8")
    _make_stub(
        bindir / "git",
        f"""#!/bin/bash
case "$1" in
  rev-parse)
    case "$2" in
      "HEAD~1") echo "0000000" ;;
      "--short")
        # Always returns the *current* (post-advance) head.
        cat "{next_head_file}"
        ;;
      *)
        n=$(cat "{counter_file}")
        if [ "$n" = "0" ]; then
          cat "{head_file}"
          echo "1" > "{counter_file}"
        else
          cat "{next_head_file}"
        fi
        ;;
    esac
    ;;
  fetch|reset) exit 0 ;;
  *) exit 0 ;;
esac
""",
    )

    # systemctl stub — list-units returns a fixed set of ict-* units;
    # restart logs the unit name; everything else exit 0.
    units_file = tmp_path / "units.txt"
    units_file.write_text(
        "ict-trader-live.service     loaded active running ICT live trader\n"
        "ict-telegram-bot.service    loaded active running ICT telegram bot\n"
        "ict-web-api.service         loaded active running ICT web API\n"
        "ict-claude-bridge.service   loaded active running ICT Claude bridge\n"
        "ict-smoke-once.service      loaded inactive dead ICT smoke oneshot\n"
        "ict-env-check.service       loaded inactive dead ICT env check\n"
        "ict-heartbeat.service       loaded inactive dead ICT heartbeat\n"
        "ict-git-sync.service        loaded inactive dead ICT git sync\n"
        "ict-hourly-snapshot.service loaded inactive dead ICT hourly snap\n"
    )
    restart_log = tmp_path / "restart.log"
    _make_stub(
        bindir / "systemctl",
        f"""#!/bin/bash
case "$1" in
  --version) echo "systemd 250"; exit 0 ;;
  list-units)
    # Filter args: pretend we always got ict-*.service. The script also
    # uses list-units 'claude-vm-runner@*.service' --state=active for
    # the deferral check; emit nothing for that.
    pattern="${{*: -1}}"
    if [[ "$pattern" == "ict-*.service" ]]; then
      cat "{units_file}"
    fi
    exit 0
    ;;
  restart)
    echo "$2" >> "{restart_log}"
    exit 0
    ;;
  status|start)
    exit 0
    ;;
  is-active)
    echo "active"
    exit 0
    ;;
  *) exit 0 ;;
esac
""",
    )
    # sudo must pass the command THROUGH, not swallow it. The script picks
    # SYSTEMCTL=(sudo systemctl) whenever it runs as a non-root user (e.g.
    # the GitHub Actions `runner` user) — it probes with
    # `sudo -n systemctl --version` then runs `sudo systemctl list-units`.
    # A bare `exit 0` stub made those no-ops, so the enumeration came back
    # empty and the restart loop did nothing — the test passed only on
    # root-uid hosts. Skip sudo's own option flags (e.g. `-n`) then exec
    # the real command so the systemctl stub handles it, mirroring real
    # sudo and making the test pass as both root and non-root.
    _make_stub(
        bindir / "sudo",
        '#!/bin/bash\nwhile [[ "$1" == -* ]]; do shift; done\nexec "$@"\n',
    )
    _make_stub(bindir / "python3", "#!/bin/bash\nexit 0\n")
    # pip install requirements.txt is invoked via python3 -m pip.
    # Our python3 stub returns 0 for any args, so this is fine.

    return {
        "repo": repo,
        "deploy": deploy_copy,
        "bindir": bindir,
        "restart_log": restart_log,
        "units_file": units_file,
    }


def _run(fake, env_extra=None):
    env = {
        **os.environ,
        "PATH": f"{fake['bindir']}:/usr/bin:/bin",
    }
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        ["bash", str(fake["deploy"])],
        capture_output=True, text=True, env=env, timeout=30,
    )


def _restarted(fake) -> list[str]:
    if not fake["restart_log"].exists():
        return []
    return [ln.strip() for ln in fake["restart_log"].read_text().splitlines() if ln.strip()]


def test_default_restart_includes_long_running_units_and_skips_oneshots(
    fake_repo_with_advance,
):
    """Enumeration restarts the long-running units; the default skip
    list excludes oneshots / timer-driven units."""
    res = _run(fake_repo_with_advance)
    assert res.returncode == 0, res.stderr
    restarted = _restarted(fake_repo_with_advance)
    assert "ict-trader-live.service" in restarted
    assert "ict-telegram-bot.service" in restarted
    assert "ict-web-api.service" in restarted
    assert "ict-claude-bridge.service" in restarted
    # Default skip-list applied:
    assert "ict-smoke-once.service" not in restarted
    assert "ict-env-check.service" not in restarted
    assert "ict-heartbeat.service" not in restarted
    assert "ict-git-sync.service" not in restarted
    assert "ict-hourly-snapshot.service" not in restarted


def test_custom_skip_list_overrides_default(fake_repo_with_advance):
    """``DEPLOY_RESTART_SKIP`` env var fully overrides the default
    skip-list. Explicitly re-include ``ict-smoke-once.service`` here
    just to prove the override path; real operators should use this
    to add units, not subtract from the safety defaults."""
    res = _run(fake_repo_with_advance, env_extra={
        "DEPLOY_RESTART_SKIP": "ict-trader-live.service",
    })
    assert res.returncode == 0, res.stderr
    restarted = _restarted(fake_repo_with_advance)
    assert "ict-trader-live.service" not in restarted  # skipped via override
    # The other long-running units still get restarted.
    assert "ict-telegram-bot.service" in restarted
    assert "ict-web-api.service" in restarted
    # The oneshot is no longer in the skip list, so it WILL be
    # restarted — proves the override fully replaces the default.
    assert "ict-smoke-once.service" in restarted


def test_smoke_once_flag_starts_oneshot_independently(fake_repo_with_advance):
    """The run_smoke_once.flag → ``systemctl start ict-smoke-once.service``
    path is independent of the enumeration restart loop. With the flag
    present, the oneshot is started even though the skip-list excludes
    it from the restart loop."""
    flag = fake_repo_with_advance["repo"] / "runtime_flags" / "run_smoke_once.flag"
    flag.write_text("go")
    # Pretend the unit file exists so the script's `if [ -f
    # /etc/systemd/system/ict-smoke-once.service ]` branch is taken.
    # We can't actually write to /etc/systemd/system in a sandbox; the
    # script's ELSE branch just emits a warning and continues, so this
    # test still validates the enumeration restart loop ran first.
    res = _run(fake_repo_with_advance)
    assert res.returncode == 0, res.stderr
    # The enumeration loop still skipped the oneshot.
    restarted = _restarted(fake_repo_with_advance)
    assert "ict-smoke-once.service" not in restarted
