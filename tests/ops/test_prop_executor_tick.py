"""Per-account prop executor tick (TRADEIFY-EXECUTOR, 2026-10-03).

Operator popup ~17:30Z 2026-10-03, verbatim: "Build executor, then go live
(Recommended)". ``scripts/ops/prop_executor_tick.sh`` served breakout_1 only
(PI-20261003-YNBSL94J-0001); it now also runs as the template instance
``ict-prop-executor@<account>``.

What is pinned:

- breakout_1 is UNCHANGED: the exact python argv, its paths
  (``feed/session_state.json``, ``executor/``, ``login.lock``, ``feed/tripped``)
  and its kill switch ``PROP_EXECUTOR_MODE``;
- tradeify_1 uses ONLY its own session, state dir, lock, trip marker and
  ``PROP_EXECUTOR_MODE_TRADEIFY_1``, and never sees ``PROP_EXECUTOR_MODE``;
- the two accounts never share a lock: one account's held lock / trip / off
  switch never stops the other;
- an account with no platform entry, or a non-plain id, is refused before any
  path is built;
- the templated units exist, are opt-in, and sit on their own minute slot;
- a REAL ``prop_executor_tick.py`` read_only cycle for tradeify_1 runs end to
  end against the real tradeify_1 config and clicks nothing, even with
  breakout_1's ``PROP_EXECUTOR_MODE=live`` in the environment.

The shell tests drive the wrapper with a stub isolated-venv ``python`` that
records its argv and the mode keys it was handed: no browser, no login.
"""
import contextlib
import dataclasses
import fcntl
import json
import pathlib
import re
import stat
import subprocess
import types
from datetime import datetime, timedelta, timezone

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
TICK = REPO / "scripts" / "ops" / "prop_executor_tick.sh"
ACTION = REPO / "scripts" / "ops" / "breakout_login_check_action.sh"
B_UNIT = REPO / "deploy" / "ict-prop-executor.service"
B_TIMER = REPO / "deploy" / "opt-in" / "ict-prop-executor.timer"
T_UNIT = REPO / "deploy" / "ict-prop-executor@.service"
T_TIMER = REPO / "deploy" / "opt-in" / "ict-prop-executor@.timer"


def _exe(path: pathlib.Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/usr/bin/env bash\n" + body + "\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def _setup(tmp_path, dotenv=""):
    repo = tmp_path / "repo"
    (repo / "scripts" / "prop").mkdir(parents=True)
    # The real helper needs the real config; the stub answers like it does
    # for the two known accounts and fails closed for anything else.
    (repo / "scripts" / "prop" / "prop_env_keys.py").write_text(
        "import sys\n"
        "keys = {'tradeify_1': 'TRADEIFY_DX_USERNAME TRADEIFY_DX_PASSWORD PROP_EXECUTOR_MODE_TRADEIFY_1'}\n"
        "a = sys.argv[1] if len(sys.argv) > 1 else ''\n"
        "sys.exit(print(keys[a]) if a in keys else 1)\n")
    if dotenv:
        (repo / ".env").write_text(dotenv)
    base = tmp_path / "browser"
    calls = tmp_path / "calls.log"
    calls.write_text("")
    _exe(base / "venv" / "bin" / "python",
         f'echo "ARGV $*" >> "{calls}"\n'
         f'echo "ENV global=${{PROP_EXECUTOR_MODE-<unset>}} tradeify=${{PROP_EXECUTOR_MODE_TRADEIFY_1-<unset>}}" >> "{calls}"\n'
         'echo \'{"executor": "done", "mode": "read_only", "halted": null}\'\n'
         'exit "${STUB_RC:-0}"')
    _exe(tmp_path / "ping_py", "exit 0")
    return repo, base, calls


def _tick(tmp_path, repo, base, account=None, extra_env=None):
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(tmp_path),
        "REPO_DIR": str(repo),
        "PROP_BROWSER_BASE": str(base),
        "PROP_EXECUTOR_PING_PY": str(tmp_path / "ping_py"),
        "PROP_EXECUTOR_TIMEOUT_S": "20",
    }
    if account is not None:
        env["PROP_EXECUTOR_ACCOUNT"] = account
    env.update(extra_env or {})
    return subprocess.run(["bash", str(TICK)], capture_output=True, text=True, env=env)


def _lines(p: pathlib.Path):
    return [ln for ln in p.read_text().splitlines() if ln.strip()]


@contextlib.contextmanager
def _held(lock: pathlib.Path):
    lock.parent.mkdir(parents=True, exist_ok=True)
    with open(lock, "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


# ── breakout_1 is unchanged ──────────────────────────────────────────────


def test_breakout_1_default_invocation_is_exactly_the_old_one(tmp_path):
    repo, base, calls = _setup(tmp_path, dotenv="PROP_EXECUTOR_MODE=read_only\nPROP_EXECUTOR_MODE_TRADEIFY_1=live\n")
    r = _tick(tmp_path, repo, base)   # no PROP_EXECUTOR_ACCOUNT: the unit as it ships
    assert r.returncode == 0, r.stdout + r.stderr
    assert _lines(calls) == [
        f"ARGV -u scripts/prop/prop_executor_tick.py --account breakout_1 --login reuse "
        f"--storage-state {base}/feed/session_state.json --state-dir {base}/executor",
        # breakout_1 is handed ITS key only; tradeify_1's never reaches it
        "ENV global=read_only tradeify=<unset>",
    ]
    assert (base / "executor").is_dir()
    assert not (base / "accounts").exists()


def test_breakout_1_lock_trip_and_off_are_its_own(tmp_path):
    repo, base, calls = _setup(tmp_path)
    with _held(base / "login.lock"):
        r = _tick(tmp_path, repo, base)
    assert r.returncode == 0 and _lines(calls) == [] and "skipping this executor tick" in r.stdout + r.stderr
    (base / "feed").mkdir(parents=True)
    (base / "feed" / "tripped").write_text("x")
    r = _tick(tmp_path, repo, base)
    assert r.returncode == 0 and _lines(calls) == [] and "TRIPPED" in r.stdout + r.stderr
    (base / "feed" / "tripped").unlink()
    (repo / ".env").write_text("PROP_EXECUTOR_MODE=off\n")
    r = _tick(tmp_path, repo, base)
    assert r.returncode == 0 and _lines(calls) == [] and "PROP_EXECUTOR_MODE=off" in r.stdout + r.stderr


def test_breakout_1_is_not_stopped_by_anything_of_tradeify_1(tmp_path):
    repo, base, calls = _setup(tmp_path, dotenv="PROP_EXECUTOR_MODE_TRADEIFY_1=off\n")
    acct = base / "accounts" / "tradeify_1"
    (acct / "feed").mkdir(parents=True)
    (acct / "feed" / "tripped").write_text("x")
    with _held(acct / "login.lock"):
        r = _tick(tmp_path, repo, base, account="breakout_1")
    assert r.returncode == 0, r.stdout + r.stderr
    assert len([ln for ln in _lines(calls) if ln.startswith("ARGV")]) == 1


# ── tradeify_1 uses only its own paths and key ───────────────────────────


def test_tradeify_1_uses_only_its_own_session_state_and_mode_key(tmp_path):
    repo, base, calls = _setup(tmp_path, dotenv=(
        "PROP_EXECUTOR_MODE=live\n"                    # breakout_1's: must never reach tradeify_1
        "PROP_EXECUTOR_MODE_TRADEIFY_1=read_only\n"))
    r = _tick(tmp_path, repo, base, account="tradeify_1")
    assert r.returncode == 0, r.stdout + r.stderr
    acct = base / "accounts" / "tradeify_1"
    assert _lines(calls) == [
        f"ARGV -u scripts/prop/prop_executor_tick.py --account tradeify_1 --login reuse "
        f"--storage-state {acct}/feed/session_state.json --state-dir {acct}/executor",
        "ENV global=<unset> tradeify=read_only",
    ]
    assert (acct / "executor").is_dir()
    assert not (base / "executor").exists()      # breakout_1's state dir is never created by it


def test_tradeify_1_is_not_stopped_by_anything_of_breakout_1(tmp_path):
    repo, base, calls = _setup(tmp_path, dotenv="PROP_EXECUTOR_MODE=off\n")
    (base / "feed").mkdir(parents=True)
    (base / "feed" / "tripped").write_text("x")
    with _held(base / "login.lock"):
        r = _tick(tmp_path, repo, base, account="tradeify_1")
    assert r.returncode == 0, r.stdout + r.stderr
    assert _lines(calls)[1] == "ENV global=<unset> tradeify=<unset>"   # unset -> python reads read_only


def test_tradeify_1_lock_trip_and_off_are_its_own(tmp_path):
    repo, base, calls = _setup(tmp_path)
    acct = base / "accounts" / "tradeify_1"
    with _held(acct / "login.lock"):
        r = _tick(tmp_path, repo, base, account="tradeify_1")
    assert r.returncode == 0 and _lines(calls) == [] and str(acct / "login.lock") in r.stdout + r.stderr
    (acct / "feed").mkdir(parents=True)
    (acct / "feed" / "tripped").write_text("x")
    r = _tick(tmp_path, repo, base, account="tradeify_1")
    assert r.returncode == 0 and _lines(calls) == [] and "TRIPPED" in r.stdout + r.stderr
    (acct / "feed" / "tripped").unlink()
    (repo / ".env").write_text("PROP_EXECUTOR_MODE_TRADEIFY_1=off\n")
    r = _tick(tmp_path, repo, base, account="tradeify_1")
    assert r.returncode == 0 and _lines(calls) == [] and "PROP_EXECUTOR_MODE_TRADEIFY_1=off" in r.stdout + r.stderr


@pytest.mark.parametrize("account", ["no_such_account", "../breakout_1", "Tradeify_1", "a b"])
def test_an_unknown_or_non_plain_account_is_refused_before_any_path(tmp_path, account):
    repo, base, calls = _setup(tmp_path)
    r = _tick(tmp_path, repo, base, account=account)
    assert r.returncode == 1, r.stdout + r.stderr
    assert _lines(calls) == []
    assert not base.joinpath("accounts").exists() and not base.joinpath("executor").exists()


# ── units ────────────────────────────────────────────────────────────────


def _slot(timer: pathlib.Path) -> str:
    return re.search(r"^OnCalendar=\*-\*-\* \*:(\d\d)/5:00$", timer.read_text(), re.M).group(1)


def test_template_units_are_per_account_and_opt_in():
    unit = T_UNIT.read_text()
    assert "Environment=PROP_EXECUTOR_ACCOUNT=%i" in unit
    assert "ExecStart=/bin/bash /home/ubuntu/ict-trading-bot/scripts/ops/prop_executor_tick.sh" in unit
    assert "Unit=ict-prop-executor@%i.service" in T_TIMER.read_text()
    # opt-in: the timer is outside deploy/*.timer, which the installer auto-enables
    assert not (REPO / "deploy" / "ict-prop-executor@.timer").exists()
    # its own minute: off breakout_1's feed (:02) and executor (:04), after its feed (:01)
    slots = {"b_feed": _slot(REPO / "deploy" / "ict-prop-feed.timer"), "b_exec": _slot(B_TIMER),
             "t_feed": _slot(REPO / "deploy" / "opt-in" / "ict-prop-feed@.timer"), "t_exec": _slot(T_TIMER)}
    assert len(set(slots.values())) == 4, slots
    assert int(slots["t_exec"]) - int(slots["t_feed"]) == int(slots["b_exec"]) - int(slots["b_feed"]) == 2


def test_breakout_1_units_are_not_templated_and_carry_no_account():
    assert "PROP_EXECUTOR_ACCOUNT" not in B_UNIT.read_text()
    assert "Unit=ict-prop-executor.service" in B_TIMER.read_text()
    assert _slot(B_TIMER) == "04"


def test_action_enables_the_instance_only_for_other_accounts_and_only_after_the_feed():
    code = "\n".join(ln for ln in ACTION.read_text().splitlines() if not ln.lstrip().startswith("#"))
    other = code.split('X_UNIT="ict-prop-executor@${ACCOUNT}"')[1].split("\nfi\n")[0]
    gate = code.split('X_UNIT="ict-prop-executor@${ACCOUNT}"')[0].rsplit("TIMER_SRC=", 1)[1]
    assert '[ "${ACCOUNT}" != "breakout_1" ]' in gate
    assert "prop_env_keys.py" in gate and "*[!a-z0-9_]*" in gate
    assert 'is-active "ict-prop-feed@${ACCOUNT}.timer"' in other
    assert other.index('is-active "ict-prop-feed@${ACCOUNT}.timer"') < other.index("enable --now")
    assert 'enable --now "${X_UNIT}.timer"' in other and 'disable --now "${X_UNIT}.timer"' in other
    assert "ict-prop-executor.timer" not in other and "ict-prop-executor.service" not in other
    # breakout_1's own branch is the one that always existed
    assert "sudo -n systemctl enable --now ict-prop-executor.timer" in code


# ── a REAL read_only cycle for tradeify_1 clicks nothing ──────────────────


class _RecordingAdapter:
    """Answers the reads a cycle makes; records every call. Any order /
    position control called with ``arm=True`` is a failure of this test."""

    timeout_ms = 3_000

    def __init__(self, quote):
        from src.prop.platform.base import AccountSnapshot
        self.calls = []
        self.quote = quote
        self.account = AccountSnapshot(balance=10_000.0, equity=10_000.0, unrealized=0.0, realized_today=0.0)

    def resume_session(self, page, url):
        self.calls.append(("resume_session",))
        return "logged_in"

    def login(self, *a, **k):
        raise AssertionError("the executor never types credentials")

    def wait_ready(self, page, timeout_ms=None):
        return True

    def read_account(self, page):
        return self.account

    def read_positions(self, page):
        return []

    def read_orders(self, page):
        return []

    def read_quote(self, page, venue):
        self.calls.append(("read_quote", venue))
        return self.quote

    def _control(name):
        def f(self, *a, arm=False, **k):
            self.calls.append((name, arm))
            assert arm is False, f"{name} armed in read_only"
            return {"ok": True, "clicked": False}
        return f

    place_bracket = _control("place_bracket")
    cancel_order = _control("cancel_order")
    flatten = _control("flatten")
    modify_bracket = _control("modify_bracket")


class _Api:
    def __init__(self, tickets):
        self._tickets = tickets
        self.posts = []

    def tickets(self, account_id):
        assert account_id == "tradeify_1"
        return list(self._tickets)

    def open_fills(self, account_id):
        assert account_id == "tradeify_1"
        return []

    def post_report(self, body):
        self.posts.append(body)
        return {"ok": True}


def _eth_ticket(now):
    e = 2600.0
    return {"ticket_id": "prop-manual-tfy1", "account_id": "tradeify_1", "symbol": "ETHUSDT",
            "direction": "long", "entry": e, "sl": 2580.0, "tp": 2650.0, "qty": 0.01, "risk_usd": 0.2,
            "valid_until": (now + timedelta(minutes=30)).isoformat(), "created_at": now.isoformat(),
            "message": f"  Entry    : {e}   (only if live price is within {e - 2} … {e + 2})"}


@pytest.mark.parametrize("enable_eth", [False, True])
def test_a_real_tradeify_1_read_only_tick_runs_end_to_end_and_clicks_nothing(
        tmp_path, monkeypatch, capsys, enable_eth):
    import sys
    from scripts.prop import prop_executor_tick as tick
    from src.prop import prop_executor as pe
    from src.prop import prop_trail

    page = types.SimpleNamespace(wait_for_timeout=lambda ms: None)
    context = types.SimpleNamespace(new_page=lambda: page)
    browser = types.SimpleNamespace(new_context=lambda **kw: context, close=lambda: None)
    # A stand-in playwright (main() imports it lazily), so this runs with or
    # without the real package and can never open a real browser.
    fake_pw = types.ModuleType("playwright.sync_api")
    fake_pw.sync_playwright = lambda: contextlib.nullcontext(
        types.SimpleNamespace(chromium=types.SimpleNamespace(launch=lambda **kw: browser)))
    monkeypatch.setitem(sys.modules, "playwright", types.ModuleType("playwright"))
    monkeypatch.setitem(sys.modules, "playwright.sync_api", fake_pw)
    monkeypatch.setattr("scripts.prop.breakout_login_check.save_storage_state", lambda ctx, path: None)
    trail_calls = []
    monkeypatch.setattr(prop_trail, "default_candles_fn", lambda: (lambda *a, **k: []))
    monkeypatch.setattr(prop_trail, "run_trail_step", lambda **kw: trail_calls.append(kw["mode"]))

    # The REAL tradeify_1 executor config (accounts.yaml + ruleset + prop_platforms.yaml).
    real = pe.load_config("tradeify_1")
    assert real.account_id == "tradeify_1"
    cfg = dataclasses.replace(real, enabled_venue_symbols=["ETHUSD"]) if enable_eth else real
    monkeypatch.setattr(pe, "load_config", lambda account: cfg if account == "tradeify_1" else
                        (_ for _ in ()).throw(AssertionError(f"loaded {account}")))
    now = datetime.now(timezone.utc)
    api = _Api([_eth_ticket(now)])
    monkeypatch.setattr(pe, "LocalApi", lambda *a, **k: api)
    ad = _RecordingAdapter(quote={"bid": 2599.9, "ask": 2600.0})
    monkeypatch.setattr(tick, "adapter_for_platform", lambda platform: ad)

    # breakout_1's switch armed, tradeify_1's at read_only: tradeify_1 must read read_only.
    monkeypatch.setenv("PROP_EXECUTOR_MODE", "live")
    monkeypatch.setenv("PROP_EXECUTOR_MODE_TRADEIFY_1", "read_only")
    storage = tmp_path / "session_state.json"
    storage.write_text(json.dumps({"cookies": [], "origins": []}))
    code = tick.main(["--account", "tradeify_1", "--login", "reuse", "--storage-state", str(storage),
                      "--state-dir", str(tmp_path / "executor")])
    out = capsys.readouterr().out
    lines = [json.loads(ln) for ln in out.splitlines() if ln.startswith("{")]
    start = next(ln for ln in lines if ln.get("executor") == "start")
    assert (start["account"], start["mode"], start["env_mode"]) == ("tradeify_1", "read_only", "read_only"), out
    assert {"executor": "done", "mode": "read_only", "halted": None} in lines, out
    assert code == tick.EXIT_OK, out
    assert ("resume_session",) in ad.calls
    assert not any(len(c) > 1 and c[1] is True for c in ad.calls), ad.calls   # no armed control
    assert not any(c[0] == "place_bracket" for c in ad.calls), ad.calls       # read_only does not even walk the form
    assert api.posts == []                                                   # read_only writes nothing
    assert trail_calls == ["read_only"]
    if enable_eth:
        # the ticket was actually evaluated (quote read), not filtered out
        assert ("read_quote", "ETHUSD") in ad.calls, ad.calls
