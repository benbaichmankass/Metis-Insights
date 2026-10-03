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
             'case "${STUB_SESSION:-}" in\n'
             '  relogin) echo "session: login_attempt"; echo "session: relogin" ;;\n'
             '  attempt) echo "session: login_attempt" ;;\n'
             '  "") ;;\n'
             '  *) echo "session: ${STUB_SESSION}" ;;\n'
             'esac\n'
             'exit "${STUB_RC:-0}"')
    _exe(tmp_path / "ping_py", f'echo "$@" >> "{pings}"')
    return repo, base, calls, pings


def _tick(tmp_path, repo, base, rc=0, max_failures=3, session="", max_relogins=12):
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(tmp_path),
        "REPO_DIR": str(repo),
        "PROP_BROWSER_BASE": str(base),
        "PROP_FEED_PING_PY": str(tmp_path / "ping_py"),
        "PROP_FEED_MAX_FAILURES": str(max_failures),
        "PROP_FEED_TIMEOUT_S": "20",
        "STUB_RC": str(rc),
        "STUB_SESSION": session,
        "PROP_FEED_MAX_RELOGINS_PER_DAY": str(max_relogins),
    }
    return subprocess.run(["bash", str(TICK)], capture_output=True, text=True, env=env)


def _lines(p: pathlib.Path):
    return [ln for ln in p.read_text().splitlines() if ln.strip()]


def test_success_invokes_the_read_only_check_exactly(tmp_path):
    repo, base, calls, pings = _setup(tmp_path)
    r = _tick(tmp_path, repo, base, rc=0)
    assert r.returncode == 0, r.stderr
    assert _lines(calls) == [
        "-u scripts/prop/breakout_login_check.py --account breakout_1 --emit-status --symbols= "
        f"--storage-state {base}/feed/session_state.json"
    ]
    # breakout_1's feed never asks for the layout canary (TRADEIFY-GOLIVE #15354).
    assert all("--layout-canary" not in c for c in _lines(calls))
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


def _audits(repo):
    d = repo / "runtime_logs" / "operator_actions"
    return sorted(d.glob("*-prop-feed.json")) if d.exists() else []


def test_relogins_are_counted_per_day_and_audited_reuses_are_not(tmp_path):
    repo, base, calls, pings = _setup(tmp_path)
    assert _tick(tmp_path, repo, base, session="reused").returncode == 0
    assert not list((base / "feed").glob("relogins-*"))
    assert _audits(repo) == []
    assert _tick(tmp_path, repo, base, session="relogin").returncode == 0
    counters = list((base / "feed").glob("relogins-*"))
    assert len(counters) == 1 and counters[0].read_text().strip() == "1"
    import json as _json
    import time as _time
    _time.sleep(1.1)  # audit file names are second-resolution
    assert _tick(tmp_path, repo, base, session="relogin").returncode == 0
    assert counters[0].read_text().strip() == "2"
    recs = [_json.loads(p.read_text()) for p in _audits(repo)]
    assert [r["relogins_today"] for r in recs] == [1, 2]
    assert all(r["status"] == "relogin" for r in recs)


def test_state_dir_is_private(tmp_path):
    repo, base, calls, pings = _setup(tmp_path)
    _tick(tmp_path, repo, base)
    assert stat.S_IMODE((base / "feed").stat().st_mode) == 0o700
    assert not list((base / "feed").glob(".tick-out.*"))  # scratch copy removed


def test_a_planted_session_token_reaches_no_output_channel(tmp_path):
    """The saved session is a credential equivalent: whatever the tick does
    (reuse, relogin, failure, trip), no stdout/stderr, ping or audit record
    may carry any of it."""
    token = "PLANTED-SESSION-TOKEN-7f3a9c"
    repo, base, calls, pings = _setup(tmp_path)
    (base / "feed").mkdir(parents=True, exist_ok=True)
    (base / "feed" / "session_state.json").write_text(
        '{"cookies": [{"name": "JSESSIONID", "value": "%s"}], '
        '"origins": [{"origin": "https://wss.example", "localStorage": '
        '[{"name": "auth", "value": "%s"}]}]}' % (token, token))
    outs = []
    for session, rc in (("reused", 0), ("relogin", 0), ("relogin", 1), ("", 4), ("", 0)):
        r = _tick(tmp_path, repo, base, rc=rc, session=session)
        outs += [r.stdout, r.stderr]
    channels = outs + [pings.read_text(), calls.read_text()]
    channels += [p.read_text() for p in _audits(repo)]
    channels += [p.read_text() for p in (base / "feed").iterdir()
                 if p.name != "session_state.json"]
    assert (base / "feed" / "tripped").exists()  # the rc=4 tick tripped it
    for text in channels:
        assert token not in text


def _today():
    import datetime as _dt
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%d")


def _plant_counter(base, name, value):
    (base / "feed").mkdir(parents=True, exist_ok=True)
    (base / "feed" / name).write_text(f"{value}\n")


def test_relogin_ceiling_trips_like_a_feasibility_stop(tmp_path):
    repo, base, calls, pings = _setup(tmp_path)
    _plant_counter(base, f"relogins-{_today()}", 11)
    r = _tick(tmp_path, repo, base, rc=0, session="relogin")
    assert r.returncode == 0  # the read itself was fine
    assert (base / "feed" / "tripped").exists()
    assert "relogin ceiling" in (base / "feed" / "tripped").read_text()
    assert len(_lines(pings)) == 1 and "TRIPPED" in _lines(pings)[0]
    _tick(tmp_path, repo, base, rc=0, session="relogin")  # tripped: no login
    assert len(_lines(calls)) == 1 and len(_lines(pings)) == 1


def test_relogin_ceiling_quiet_control_below_the_ceiling(tmp_path):
    repo, base, calls, pings = _setup(tmp_path)
    _plant_counter(base, f"relogins-{_today()}", 10)
    assert _tick(tmp_path, repo, base, rc=0, session="relogin").returncode == 0
    assert (base / "feed" / f"relogins-{_today()}").read_text().strip() == "11"
    assert not (base / "feed" / "tripped").exists()
    assert _lines(pings) == []
    # Reused ticks never move the relogin counter, however many there are.
    for _ in range(3):
        _tick(tmp_path, repo, base, rc=0, session="reused")
    assert (base / "feed" / f"relogins-{_today()}").read_text().strip() == "11"
    assert not (base / "feed" / "tripped").exists()


def test_failed_credential_attempts_count_toward_the_ceiling(tmp_path):
    repo, base, calls, pings = _setup(tmp_path)
    # rc=1 with no `session: relogin` line: a login that did not succeed.
    _tick(tmp_path, repo, base, rc=1, session="attempt", max_failures=99)
    _tick(tmp_path, repo, base, rc=1, session="attempt", max_failures=99)
    assert (base / "feed" / f"relogins-{_today()}").read_text().strip() == "2"
    import json as _json
    recs = [_json.loads(p.read_text()) for p in _audits(repo)]
    assert recs and all(r["succeeded"] is False for r in recs)
    r = _tick(tmp_path, repo, base, rc=1, session="attempt", max_failures=99, max_relogins=3)
    assert "relogin ceiling" in (base / "feed" / "tripped").read_text()
    assert r.returncode == 1


def test_a_day_with_no_reused_session_pings_once_ever(tmp_path):
    repo, base, calls, pings = _setup(tmp_path)
    _plant_counter(base, "ticks-20000101", 288)
    _plant_counter(base, "relogins-20000101", 5)
    _tick(tmp_path, repo, base, rc=0, session="reused")
    assert len(_lines(pings)) == 1 and "no reused session" in _lines(pings)[0]
    assert (base / "feed" / "noreuse-pinged").exists()
    assert not (base / "feed" / "ticks-20000101").exists()      # rolled over
    assert not (base / "feed" / "relogins-20000101").exists()
    assert not (base / "feed" / "tripped").exists()
    _plant_counter(base, "ticks-20000102", 288)                   # a second such day
    _tick(tmp_path, repo, base, rc=0, session="reused")
    assert len(_lines(pings)) == 1                                # one-time only


def test_a_day_with_a_reused_session_is_quiet(tmp_path):
    repo, base, calls, pings = _setup(tmp_path)
    _plant_counter(base, "ticks-20000101", 288)
    _plant_counter(base, "reused-20000101", 250)
    _tick(tmp_path, repo, base, rc=0, session="reused")
    assert _lines(pings) == []
    assert not (base / "feed" / "noreuse-pinged").exists()
    assert (base / "feed" / f"ticks-{_today()}").read_text().strip() == "1"
    assert (base / "feed" / f"reused-{_today()}").read_text().strip() == "1"


def test_reset_feed_rearms_only_after_a_clean_check_under_the_lock():
    action = (REPO / "scripts" / "ops" / "breakout_login_check_action.sh").read_text()
    code = "\n".join(ln for ln in action.splitlines() if not ln.lstrip().startswith("#"))
    lock = code.index('exec 9>"${BASE}/login.lock"')
    run = code.index("scripts/prop/breakout_login_check.py")
    clear = code.index('rm -f "${FEED_DIR}/tripped"')
    assert lock < run < clear  # after the check, while fd 9 (the lock) is still held
    guard = code.rindex('if [ "${rc}" = "0" ]', 0, clear)
    assert run < guard < clear
    assert code.count('rm -f "${FEED_DIR}/tripped"') == 1
    # breakout_1's feed dir is the one it always had (TRADEIFY-WIRE made it per account)
    assert 'FEED_DIR="${BASE}/feed"' in code


_HANGING_CHECK = (
    "import time\n"
    "print('session: login_attempt')   # deliberately NO flush=True\n"
    "time.sleep(60)\n"
)


def _python_venv(tmp_path, repo, base):
    """A venv whose python is REAL python (block-buffers a pipe), running a
    fake check that announces a login attempt and then hangs."""
    real = subprocess.run(["bash", "-c", "command -v python3"], capture_output=True,
                          text=True, check=True).stdout.strip()
    _exe(base / "venv" / "bin" / "python",
         'if [ "$1" = "-c" ]; then exit 0; fi\n'
         f'exec "{real}" "$@"')
    (repo / "scripts" / "prop").mkdir(parents=True, exist_ok=True)
    (repo / "scripts" / "prop" / "breakout_login_check.py").write_text(_HANGING_CHECK)


def test_control_block_buffered_output_is_lost_when_timeout_kills_python(tmp_path):
    """The control for the test below: without -u / PYTHONUNBUFFERED the
    line never reaches the pipe, which is the defect review found."""
    script = tmp_path / "hang.py"
    script.write_text(_HANGING_CHECK)
    env = {"PATH": "/usr/bin:/bin"}
    r = subprocess.run(["bash", "-c", f"timeout 1 python3 {script} | cat"],
                       capture_output=True, text=True, env=env)
    assert "login_attempt" not in r.stdout


def test_attempt_that_hangs_past_the_hard_timeout_is_still_counted(tmp_path):
    repo, base, calls, pings = _setup(tmp_path, venv=False)
    _python_venv(tmp_path, repo, base)
    env = {
        "PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "REPO_DIR": str(repo),
        "PROP_BROWSER_BASE": str(base), "PROP_FEED_PING_PY": str(tmp_path / "ping_py"),
        "PROP_FEED_TIMEOUT_S": "1", "PROP_FEED_MAX_FAILURES": "99",
    }
    r = subprocess.run(["bash", str(TICK)], capture_output=True, text=True, env=env, timeout=60)
    assert r.returncode == 124, r.stderr
    assert (base / "feed" / f"relogins-{_today()}").read_text().strip() == "1"


def test_corrupt_relogin_counter_fails_closed(tmp_path):
    repo, base, calls, pings = _setup(tmp_path)
    _plant_counter(base, f"relogins-{_today()}", "3x")
    _tick(tmp_path, repo, base, rc=0, session="relogin")
    trip = (base / "feed" / "tripped").read_text()
    assert "relogin counter corrupt" in trip and "failing closed" in trip
    assert len(_lines(pings)) == 1


def test_non_file_relogin_counter_fails_closed(tmp_path):
    repo, base, calls, pings = _setup(tmp_path)
    (base / "feed" / f"relogins-{_today()}").mkdir(parents=True)
    _tick(tmp_path, repo, base, rc=0, session="relogin")
    assert "not a regular file" in (base / "feed" / "tripped").read_text()
    assert len(_lines(pings)) == 1


def test_unwritable_relogin_counter_fails_closed(tmp_path):
    """Simulate a failed write (as on a full disk) with a failing `mv` shim
    on PATH; the counter must trip the feed, never read as 0 or stick at 1."""
    repo, base, calls, pings = _setup(tmp_path)
    shim = tmp_path / "shim"
    _exe(shim / "mv", 'exit 1')
    env_path = f"{shim}:/usr/bin:/bin"
    env = {
        "PATH": env_path, "HOME": str(tmp_path), "REPO_DIR": str(repo),
        "PROP_BROWSER_BASE": str(base), "PROP_FEED_PING_PY": str(tmp_path / "ping_py"),
        "PROP_FEED_TIMEOUT_S": "20", "STUB_RC": "0", "STUB_SESSION": "relogin",
    }
    subprocess.run(["bash", str(TICK)], capture_output=True, text=True, env=env)
    assert "write failed" in (base / "feed" / "tripped").read_text()
    assert len(_lines(pings)) == 1


def test_best_effort_counters_never_trip(tmp_path):
    repo, base, calls, pings = _setup(tmp_path)
    _plant_counter(base, f"ticks-{_today()}", "garbage")
    assert _tick(tmp_path, repo, base, rc=0, session="reused").returncode == 0
    assert not (base / "feed" / "tripped").exists()
    assert _lines(pings) == []


def test_unit_runs_python_unbuffered():
    assert "Environment=PYTHONUNBUFFERED=1" in UNIT.read_text()
    code = "\n".join(ln for ln in TICK.read_text().splitlines() if not ln.lstrip().startswith("#"))
    assert 'PYTHONUNBUFFERED=1 "${VENV}/bin/python" -u scripts/prop/breakout_login_check.py' in code


# ── second prop account (TRADEIFY-WIRE, 2026-09-30) ───────────────────────


def _tick_account(tmp_path, repo, base, account, keys_rc=0):
    (repo / "scripts" / "prop").mkdir(parents=True, exist_ok=True)
    (repo / "scripts" / "prop" / "prop_env_keys.py").write_text(
        "import sys\n"
        f"sys.exit({keys_rc}) if {keys_rc} else print("
        "'TRADEIFY_DX_USERNAME TRADEIFY_DX_PASSWORD PROP_EXECUTOR_MODE_TRADEIFY_1')\n")
    (repo / ".env").write_text("TRADEIFY_DX_USERNAME=u\nBREAKOUT_DX_USERNAME=b\n")
    env = {
        "PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "REPO_DIR": str(repo),
        "PROP_BROWSER_BASE": str(base), "PROP_FEED_PING_PY": str(tmp_path / "ping_py"),
        "PROP_FEED_TIMEOUT_S": "20", "STUB_RC": "0", "STUB_SESSION": "",
        "PROP_FEED_ACCOUNT": account,
    }
    return subprocess.run(["bash", str(TICK)], capture_output=True, text=True, env=env)


def test_second_account_uses_its_own_state_dir(tmp_path):
    repo, base, calls, pings = _setup(tmp_path)
    r = _tick_account(tmp_path, repo, base, "tradeify_1")
    assert r.returncode == 0, r.stderr
    sdir = base / "accounts" / "tradeify_1" / "feed"
    assert _lines(calls) == [
        "-u scripts/prop/breakout_login_check.py --account tradeify_1 --emit-status --symbols= "
        f"--storage-state {sdir}/session_state.json --layout-canary"
    ]
    assert (sdir / "consecutive_failures").read_text().strip() == "0"
    assert not (base / "feed").exists()   # breakout_1's dir is never touched


def test_second_account_without_platform_entry_never_logs_in(tmp_path):
    repo, base, calls, pings = _setup(tmp_path)
    r = _tick_account(tmp_path, repo, base, "nope_1", keys_rc=1)
    assert r.returncode == 1
    assert _lines(calls) == []


def test_template_unit_passes_the_instance_as_the_account():
    svc = (REPO / "deploy" / "ict-prop-feed@.service").read_text()
    tmr = (REPO / "deploy" / "opt-in" / "ict-prop-feed@.timer").read_text()
    assert "Environment=PROP_FEED_ACCOUNT=%i" in svc
    assert "ExecStart=/bin/bash /home/ubuntu/ict-trading-bot/scripts/ops/prop_feed_tick.sh" in svc
    assert "Unit=ict-prop-feed@%i.service" in tmr


def test_second_account_feed_never_contends_for_breakout_lock(tmp_path):
    """Manager review of #14663: a tradeify_1 feed tick must not take
    ${BASE}/login.lock, which breakout_1's real-money executor flock -n's."""
    repo, base, calls, pings = _setup(tmp_path)
    base.mkdir(parents=True, exist_ok=True)
    with open(base / "login.lock", "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)   # breakout_1 busy
        r = _tick_account(tmp_path, repo, base, "tradeify_1")
    assert r.returncode == 0, r.stderr
    assert len(_lines(calls)) == 1                       # it still ran
    # and its own lock blocks only itself
    own = base / "accounts" / "tradeify_1" / "login.lock"
    with open(own, "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        r2 = _tick_account(tmp_path, repo, base, "tradeify_1")
        r3 = _tick(tmp_path, repo, base, rc=0)            # breakout_1 unaffected
    assert "skipping this tick" in r2.stderr
    assert r3.returncode == 0 and len(_lines(calls)) == 2


def test_feed_template_timer_is_off_breakout_slots():
    tmr = (REPO / "deploy" / "opt-in" / "ict-prop-feed@.timer").read_text()
    feed = (REPO / "deploy" / "ict-prop-feed.timer").read_text()
    execu = (REPO / "deploy" / "opt-in" / "ict-prop-executor.timer").read_text()
    import re as _re
    slot = lambda t: _re.search(r"OnCalendar=\S+ \*:(\d\d)/5", t).group(1)  # noqa: E731
    assert slot(tmr) not in {slot(feed), slot(execu)}
