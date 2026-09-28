"""Tests for ``scripts/ops/prop_feed_tick.sh`` (W6-PROP-FEED).

The tick is driven end to end with a stub isolated-venv ``python`` (exits with
``$STUB_RC`` and records its argv) and a stub ping interpreter, so no browser,
no login and no real ping happen. What is pinned:

- the exact read-only invocation (``--emit-status --symbols=`` and nothing else
  that could widen it, e.g. no ``--dump-dir``);
- a feasibility stop (exit 4) trips on the FIRST occurrence and pings once;
- other failures trip only after N consecutive, and a success resets the count;
- a tripped feed never invokes python again;
- a held lock skips the tick without invoking python;
- a missing venv is an environment failure, never a pip install.
"""
import fcntl
import os
import pathlib
import stat
import subprocess

REPO = pathlib.Path(__file__).resolve().parents[2]
TICK = REPO / "scripts" / "ops" / "prop_feed_tick.sh"
UNIT = REPO / "deploy" / "ict-prop-feed.service"
TIMER = REPO / "deploy" / "ict-prop-feed.timer"


def _exe(path: pathlib.Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/usr/bin/env bash\n" + body + "\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def _setup(tmp_path, *, venv=True):
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    base = tmp_path / "browser"
    calls = tmp_path / "calls.log"
    pings = tmp_path / "pings.log"
    calls.write_text("")
    pings.write_text("")
    if venv:
        _exe(base / "venv" / "bin" / "python",
             'if [ "$1" = "-c" ]; then exit 0; fi\n'
             f'echo "$@" >> "{calls}"\n'
             'echo "account: {\\"balance\\": 4724}"\n'
             'exit "${STUB_RC:-0}"')
    _exe(tmp_path / "ping_py", f'echo "$@" >> "{pings}"')
    return repo, base, calls, pings


def _tick(tmp_path, repo, base, rc=0, max_failures=3):
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(tmp_path),
        "REPO_DIR": str(repo),
        "PROP_BROWSER_BASE": str(base),
        "PROP_FEED_PING_PY": str(tmp_path / "ping_py"),
        "PROP_FEED_MAX_FAILURES": str(max_failures),
        "PROP_FEED_TIMEOUT_S": "20",
        "STUB_RC": str(rc),
    }
    return subprocess.run(["bash", str(TICK)], capture_output=True, text=True, env=env)


def _lines(p: pathlib.Path):
    return [ln for ln in p.read_text().splitlines() if ln.strip()]


def test_success_invokes_the_read_only_check_exactly(tmp_path):
    repo, base, calls, pings = _setup(tmp_path)
    r = _tick(tmp_path, repo, base, rc=0)
    assert r.returncode == 0, r.stderr
    assert _lines(calls) == [
        "scripts/prop/breakout_login_check.py --account breakout_1 --emit-status --symbols="
    ]
    assert not (base / "feed" / "tripped").exists()
    assert (base / "feed" / "consecutive_failures").read_text().strip() == "0"
    assert _lines(pings) == []


def test_feasibility_stop_trips_on_first_occurrence_and_pings_once(tmp_path):
    repo, base, calls, pings = _setup(tmp_path)
    r = _tick(tmp_path, repo, base, rc=4)
    assert r.returncode == 4
    assert (base / "feed" / "tripped").exists()
    assert len(_lines(pings)) == 1 and "TRIPPED" in _lines(pings)[0]
    # Every later tick skips without logging in, and does not ping again.
    for _ in range(3):
        r = _tick(tmp_path, repo, base, rc=0)
        assert r.returncode == 0
    assert len(_lines(calls)) == 1
    assert len(_lines(pings)) == 1


def test_other_failures_trip_after_n_consecutive_and_success_resets(tmp_path):
    repo, base, calls, pings = _setup(tmp_path)
    assert _tick(tmp_path, repo, base, rc=1).returncode == 1
    assert _tick(tmp_path, repo, base, rc=3).returncode == 3
    assert not (base / "feed" / "tripped").exists()
    assert _tick(tmp_path, repo, base, rc=0).returncode == 0  # resets
    assert _tick(tmp_path, repo, base, rc=1).returncode == 1
    assert _tick(tmp_path, repo, base, rc=1).returncode == 1
    assert not (base / "feed" / "tripped").exists()
    assert _tick(tmp_path, repo, base, rc=5).returncode == 5
    assert (base / "feed" / "tripped").exists()
    assert len(_lines(pings)) == 1
    _tick(tmp_path, repo, base, rc=0)
    assert len(_lines(calls)) == 6  # the tripped tick did not log in


def test_held_lock_skips_without_logging_in(tmp_path):
    repo, base, calls, pings = _setup(tmp_path)
    base.mkdir(parents=True, exist_ok=True)
    with open(base / "login.lock", "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        r = _tick(tmp_path, repo, base, rc=0)
    assert r.returncode == 0
    assert _lines(calls) == []
    assert "skipping this tick" in r.stderr


def test_missing_venv_is_environment_failure_not_an_install(tmp_path):
    repo, base, calls, pings = _setup(tmp_path, venv=False)
    r = _tick(tmp_path, repo, base, rc=0)
    assert r.returncode == 5
    assert not (base / "venv").exists()
    assert "venv missing" in r.stderr


def test_tick_never_passes_flags_that_widen_the_read():
    src = "\n".join(ln for ln in TICK.read_text().splitlines()
                    if not ln.lstrip().startswith("#"))
    assert "--emit-status --symbols=" in src
    for forbidden in ("--dump-dir", "pip install", "playwright install", "set -x"):
        assert forbidden not in src, forbidden


def test_action_and_tick_share_one_lock_file():
    action = (REPO / "scripts" / "ops" / "breakout_login_check_action.sh").read_text()
    assert 'LOCK_FILE="${BASE}/login.lock"' in TICK.read_text()
    assert 'exec 9>"${BASE}/login.lock"' in action
    # Taken before the venv/Chromium bootstrap, so an install never swaps the
    # browser build under a running tick.
    assert action.index('exec 9>"${BASE}/login.lock"') < action.index('ensure_venv "${VENV}"')


def test_units_are_wired_every_five_minutes_with_a_hard_timeout():
    unit, timer = UNIT.read_text(), TIMER.read_text()
    assert "Type=oneshot" in unit and "User=ubuntu" in unit
    assert "TimeoutStartSec=" in unit
    assert "scripts/ops/prop_feed_tick.sh" in unit
    assert ".venv/bin/python" not in unit  # never the trader's Python
    assert "OnCalendar=*-*-* *:02/5:00" in timer
    assert "Unit=ict-prop-feed.service" in timer
    assert "Persistent=false" in timer
