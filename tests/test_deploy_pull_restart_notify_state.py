"""S-020 — pin the state-file ping logic in deploy_pull_restart.sh.

The bug we're fixing: PRE_SYNC_HEAD == POST_SYNC_HEAD short-circuited the
ping path even when there were unpinged commits (because the operator did
a manual `git reset --hard` outside the timer's window). Fix is a
persisted ``runtime_logs/notify_state.txt`` that records the last commit
we pinged for, so the next tick can compare against that instead of
"the head we saw 1 second ago".

This test stubs git + python via PATH shadowing and runs the deploy
script in a tmp REPO_DIR to verify:

1. First run: notify_state.txt absent → notify is invoked with --pre=unknown.
2. After successful notify: state file holds POST_SYNC_HEAD.
3. Second run with the SAME HEAD: notify is NOT invoked (state already current).
4. Third run after a manual reset advances HEAD without the script's
   fetch finding anything: state mismatch → notify IS invoked with the
   new HEAD as --post (the regression case).
5. auto_ping_test.flag presence → notify is invoked with --force-checkpoint
   AND the flag is consumed on success.
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
def fake_repo(tmp_path: Path):
    """A minimal stand-in for /home/ubuntu/ict-trading-bot.

    We rewrite the deploy script's REPO_DIR via env-substitution: the script
    has a hard-coded path, so we make a copy with that path replaced.
    """
    repo = tmp_path / "ict-trading-bot"
    (repo / "scripts").mkdir(parents=True)
    (repo / "runtime_logs").mkdir()
    (repo / "runtime_flags").mkdir()

    # Patched copy of the deploy script with REPO_DIR pointing at tmp.
    src = DEPLOY_SCRIPT.read_text()
    patched = src.replace(
        'REPO_DIR="/home/ubuntu/ict-trading-bot"',
        f'REPO_DIR="{repo}"',
    )
    deploy_copy = repo / "scripts" / "deploy_pull_restart.sh"
    deploy_copy.write_text(patched)
    deploy_copy.chmod(0o755)

    # Stub bin dir on PATH for git, python3, systemctl, sudo.
    bindir = tmp_path / "bin"
    bindir.mkdir()

    log_file = tmp_path / "calls.log"
    head_file = tmp_path / "fake_head"
    head_file.write_text("aaaaaaa")  # initial fake HEAD

    # git stub: rev-parse HEAD echoes head_file; rev-parse HEAD~1 echoes
    # an artificial parent hash; everything else logs+exit 0.
    _make_stub(
        bindir / "git",
        f"""#!/bin/bash
echo "git $*" >> "{log_file}"
case "$1" in
  rev-parse)
    case "$2" in
      "HEAD~1") echo "0000000" ;;
      *) cat "{head_file}" ;;
    esac
    ;;
  fetch|reset) exit 0 ;;
  *) exit 0 ;;
esac
""",
    )

    # python3 stub: log invocation, succeed (so state file gets written).
    notify_invocations = tmp_path / "notify_invocations.log"
    _make_stub(
        bindir / "python3",
        f"""#!/bin/bash
shift  # drop "scripts/notify_on_pull.py"
echo "$*" >> "{notify_invocations}"
exit 0
""",
    )
    # The deploy script calls /usr/bin/python3 explicitly. Symlink that into
    # our bin dir is fine — but we have to override the absolute path. Simplest:
    # patch the script to call `python3` via PATH instead. Do it in the copy.
    text = deploy_copy.read_text().replace("/usr/bin/python3", "python3")
    deploy_copy.write_text(text)

    # systemctl/sudo stubs (the restart phase calls them; harmless stubs).
    _make_stub(bindir / "systemctl", "#!/bin/bash\nexit 0\n")
    _make_stub(bindir / "sudo", "#!/bin/bash\nexit 0\n")

    return {
        "repo": repo,
        "deploy": deploy_copy,
        "bindir": bindir,
        "head_file": head_file,
        "notify_invocations": notify_invocations,
        "calls_log": log_file,
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


def _read_invocations(fake):
    if not fake["notify_invocations"].exists():
        return []
    return [ln for ln in fake["notify_invocations"].read_text().splitlines() if ln.strip()]


def test_first_run_bootstraps_with_head_parent(fake_repo):
    """Cold-start: notify_state.txt absent → bootstrap with HEAD~1.

    notify_on_pull.py treats --pre=unknown as a hard short-circuit, so a
    bare unknown bootstrap would silently miss the very first checkpoint
    ping after the fix lands. We default to HEAD~1 so the merge commit's
    diff is included in the (pre, post] range.
    """
    fake_repo["head_file"].write_text("aaaaaaa")
    res = _run(fake_repo)
    assert res.returncode == 0, res.stderr
    invs = _read_invocations(fake_repo)
    assert len(invs) == 1
    assert "--pre 0000000" in invs[0]  # HEAD~1 from git stub
    assert "--post aaaaaaa" in invs[0]
    state = (fake_repo["repo"] / "runtime_logs" / "notify_state.txt").read_text().strip()
    assert state == "aaaaaaa"


def test_second_run_same_head_skips_ping(fake_repo):
    """After a successful first ping, a tick with no advance is silent."""
    fake_repo["head_file"].write_text("aaaaaaa")
    _run(fake_repo)  # first run sets state file
    # Wipe the invocations log to assert NO new invocation.
    fake_repo["notify_invocations"].write_text("")
    res = _run(fake_repo)
    assert res.returncode == 0
    assert _read_invocations(fake_repo) == []


def test_manual_reset_to_new_head_still_pings(fake_repo):
    """Regression for the S-020 bug: HEAD advanced via mid-debug
    `git reset --hard` (not via this script's fetch). The state file
    disagrees with the new HEAD, so we MUST ping."""
    fake_repo["head_file"].write_text("aaaaaaa")
    _run(fake_repo)  # state = aaaaaaa
    fake_repo["notify_invocations"].write_text("")

    # Operator (or whatever) advanced HEAD between ticks. The script's
    # PRE_SYNC_HEAD will be bbbbbbb, fetch+reset finds nothing new, so
    # PRE_SYNC_HEAD == POST_SYNC_HEAD == bbbbbbb. Old code would skip
    # the ping entirely; new code compares against the state file
    # (still aaaaaaa) and pings.
    fake_repo["head_file"].write_text("bbbbbbb")
    res = _run(fake_repo)
    assert res.returncode == 0, res.stderr
    invs = _read_invocations(fake_repo)
    assert len(invs) == 1
    assert "--pre aaaaaaa" in invs[0]
    assert "--post bbbbbbb" in invs[0]
    state = (fake_repo["repo"] / "runtime_logs" / "notify_state.txt").read_text().strip()
    assert state == "bbbbbbb"


def test_auto_ping_test_flag_forces_checkpoint_and_is_consumed(fake_repo):
    """If runtime_flags/auto_ping_test.flag exists, notify_on_pull is
    invoked with --force-checkpoint, and the flag file is removed on
    success. Pre==post is fine in this path."""
    fake_repo["head_file"].write_text("aaaaaaa")
    _run(fake_repo)  # state = aaaaaaa
    fake_repo["notify_invocations"].write_text("")

    flag = fake_repo["repo"] / "runtime_flags" / "auto_ping_test.flag"
    flag.write_text("trigger")

    # No HEAD advance. Without the flag this would be a silent tick.
    res = _run(fake_repo)
    assert res.returncode == 0, res.stderr
    invs = _read_invocations(fake_repo)
    assert len(invs) == 1
    assert "--force-checkpoint" in invs[0]
    assert not flag.exists()  # consumed


def test_failed_notify_leaves_state_untouched_for_retry(fake_repo):
    """If notify_on_pull exits non-zero, the state file must NOT be
    updated, so the next tick retries against the same baseline."""
    # Make python3 stub fail.
    _make_stub(
        fake_repo["bindir"] / "python3",
        f"""#!/bin/bash
shift
echo "$*" >> "{fake_repo['notify_invocations']}"
exit 7
""",
    )
    fake_repo["head_file"].write_text("aaaaaaa")
    res = _run(fake_repo)
    assert res.returncode == 0, res.stderr  # script absorbs the failure
    state_file = fake_repo["repo"] / "runtime_logs" / "notify_state.txt"
    assert not state_file.exists() or state_file.read_text().strip() == ""
