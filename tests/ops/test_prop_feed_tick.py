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
        "scripts/prop/breakout_login_check.py --account breakout_1 --emit-status --symbols= "
        f"--storage-state {base}/feed/session_state.json"
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
    clear = code.index('rm -f "${BASE}/feed/tripped"')
    assert lock < run < clear  # after the check, while fd 9 (the lock) is still held
    guard = code.rindex('if [ "${rc}" = "0" ]', 0, clear)
    assert run < guard < clear
    assert code.count('rm -f "${BASE}/feed/tripped"') == 1
