"""Session reuse in ``scripts/prop/breakout_login_check.py`` (W6-PROP-FEED,
operator decision 2026-09-28 "Reuse saved session").

Drives ``main()`` end to end against a fake ``playwright.sync_api`` and a fake
adapter, so no browser runs. Pinned:

- a saved session the terminal still accepts is REUSED: no credential login;
- an expired / rejected one is deleted and replaced by exactly ONE login
  (never a loop), then re-saved;
- a corrupt state file is deleted and falls back to a normal login;
- a challenge on the saved session stops with exit 4 and NO login attempt;
- without ``--storage-state`` (the one-off action) every run is a fresh login
  and nothing is saved;
- the state is written 0600;
- a token planted in the saved state reaches no output channel (stdout,
  stderr, the posted report).
"""
from __future__ import annotations

import json
import stat
import sys
import types
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "prop"))
import breakout_login_check as blc  # noqa: E402

from src.prop.platform.base import AccountSnapshot, FeasibilityError  # noqa: E402

TOKEN = "PLANTED-SESSION-TOKEN-19c2e7"


class FakeContext:
    def __init__(self, browser, state):
        self.browser, self.state = browser, state
        self.closed = False

    def new_page(self):
        return types.SimpleNamespace(url="https://wss.example/terminal", ctx=self,
                                     wait_for_timeout=lambda *_: None,
                                     inner_text=lambda *_a, **_k: "")

    def storage_state(self):
        return {"cookies": [{"name": "JSESSIONID", "value": TOKEN}],
                "origins": [{"origin": "https://wss.example",
                             "localStorage": [{"name": "auth", "value": TOKEN}]}]}

    def close(self):
        self.closed = True


class FakeBrowser:
    def __init__(self, reject_state=False):
        self.contexts = []
        self.reject_state = reject_state

    def new_context(self, storage_state=None):
        if storage_state is not None and self.reject_state:
            raise ValueError(f"bad state {json.dumps(storage_state)}")
        c = FakeContext(self, storage_state)
        self.contexts.append(c)
        return c

    def close(self):
        pass


class FakeAdapter:
    def __init__(self, resume_result="logged_in", login_exc=None):
        self.resume_result = resume_result
        self.login_exc = login_exc
        self.logins = 0
        self.resumes = 0
        self.timeout_ms = 0

    def resume_session(self, page, url):
        self.resumes += 1
        if isinstance(self.resume_result, Exception):
            raise self.resume_result
        return self.resume_result

    def login(self, page, url, u, p):
        self.logins += 1
        if self.login_exc:
            raise self.login_exc

    def wait_ready(self, page, timeout_ms=0):
        return True

    def read_account(self, page):
        return AccountSnapshot(balance=4724.0, equity=4724.0)

    def read_positions(self, page):
        return []

    def read_orders(self, page):
        return []


@pytest.fixture
def harness(monkeypatch):
    posted = []
    state = {"browser": FakeBrowser(), "adapter": FakeAdapter()}

    class _PW:
        chromium = types.SimpleNamespace(launch=lambda headless=True: state["browser"])

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    mod = types.ModuleType("playwright.sync_api")
    mod.sync_playwright = lambda: _PW()
    monkeypatch.setitem(sys.modules, "playwright", types.ModuleType("playwright"))
    monkeypatch.setitem(sys.modules, "playwright.sync_api", mod)
    monkeypatch.setattr(blc, "load_platform_config", lambda a: {
        "platform": "dxtrade", "login_url": "https://wss.example/",
        "username_env": "U_ENV", "password_env": "P_ENV"})
    monkeypatch.setattr(blc, "adapter_for_platform", lambda p: state["adapter"])
    monkeypatch.setattr(blc, "post_status", lambda rep, base: posted.append(rep) or {"id": 1})
    monkeypatch.setenv("U_ENV", "user@example.com")
    monkeypatch.setenv("P_ENV", "hunter2")
    state["posted"] = posted
    return state


def _run(args, capsys):
    rc = blc.main(["--symbols=", *args])
    out = capsys.readouterr()
    return rc, out.out + out.err


def _plant(path: Path):
    path.write_text(json.dumps({"cookies": [{"name": "JSESSIONID", "value": TOKEN}],
                                "origins": []}))


def test_reused_session_does_not_log_in_and_is_resaved_0600(harness, tmp_path, capsys):
    sf = tmp_path / "s.json"
    _plant(sf)
    rc, out = _run(["--storage-state", str(sf), "--emit-status"], capsys)
    assert rc == 0, out
    assert harness["adapter"].logins == 0 and harness["adapter"].resumes == 1
    assert "session: reused" in out and "session: relogin" not in out
    assert harness["browser"].contexts[0].state is not None  # opened WITH the state
    assert stat.S_IMODE(sf.stat().st_mode) == 0o600
    assert TOKEN not in out


@pytest.mark.parametrize("seen", ["login_form", "login_error", "unknown", RuntimeError("nav")])
def test_rejected_session_is_deleted_then_exactly_one_login(harness, tmp_path, capsys, seen):
    sf = tmp_path / "s.json"
    _plant(sf)
    harness["adapter"].resume_result = seen
    rc, out = _run(["--storage-state", str(sf)], capsys)
    assert rc == 0, out
    assert harness["adapter"].resumes == 1 and harness["adapter"].logins == 1
    assert "session: relogin" in out
    ctxs = harness["browser"].contexts
    assert ctxs[0].closed and ctxs[1].state is None  # relogin on a CLEAN context
    assert json.loads(sf.read_text())["cookies"][0]["value"] == TOKEN  # re-saved
    assert TOKEN not in out


def test_no_saved_state_logs_in_once_and_saves(harness, tmp_path, capsys):
    sf = tmp_path / "sub" / "s.json"
    rc, out = _run(["--storage-state", str(sf)], capsys)
    assert rc == 0
    assert harness["adapter"].resumes == 0 and harness["adapter"].logins == 1
    assert "session: relogin" in out and sf.exists()
    assert stat.S_IMODE(sf.stat().st_mode) == 0o600


@pytest.mark.parametrize("body", ["{not json " + TOKEN, json.dumps([TOKEN]),
                                  json.dumps({"cookies": TOKEN})])
def test_corrupt_state_is_deleted_and_falls_back_to_login(harness, tmp_path, capsys, body):
    sf = tmp_path / "s.json"
    sf.write_text(body)
    rc, out = _run(["--storage-state", str(sf)], capsys)
    assert rc == 0
    assert harness["adapter"].resumes == 0 and harness["adapter"].logins == 1
    assert "saved state unusable" in out and "session: relogin" in out
    assert TOKEN not in out


def test_state_the_browser_rejects_is_deleted_and_falls_back(harness, tmp_path, capsys):
    harness["browser"].reject_state = True  # its exception message carries the token
    sf = tmp_path / "s.json"
    _plant(sf)
    rc, out = _run(["--storage-state", str(sf)], capsys)
    assert rc == 0 and harness["adapter"].logins == 1
    assert "rejected by the browser" in out
    assert TOKEN not in out


@pytest.mark.parametrize("reason", ["challenge", "captcha", "2fa", "password_expired"])
def test_challenge_on_saved_session_stops_without_a_login(harness, tmp_path, capsys, reason):
    sf = tmp_path / "s.json"
    _plant(sf)
    harness["adapter"].resume_result = FeasibilityError(reason, "x")
    rc, out = _run(["--storage-state", str(sf)], capsys)
    assert rc == blc.EXIT_FEASIBILITY
    assert harness["adapter"].logins == 0
    assert not sf.exists()
    assert f"feasibility: {reason}" in out


def test_relogin_that_is_challenged_exits_4_once(harness, tmp_path, capsys):
    sf = tmp_path / "s.json"
    _plant(sf)
    harness["adapter"].resume_result = "login_form"
    harness["adapter"].login_exc = FeasibilityError("login_rejected", "x")
    rc, out = _run(["--storage-state", str(sf)], capsys)
    assert rc == blc.EXIT_FEASIBILITY
    assert harness["adapter"].logins == 1  # one attempt, never a loop
    assert not sf.exists()


def test_without_storage_state_every_run_is_fresh_and_saves_nothing(harness, tmp_path, capsys):
    rc, out = _run(["--dump-dir", ""], capsys)
    assert rc == 0
    assert harness["adapter"].resumes == 0 and harness["adapter"].logins == 1
    assert "session: fresh" in out and "state saved" not in out
    assert list(tmp_path.iterdir()) == []


def test_posted_report_never_carries_session_state(harness, tmp_path, capsys):
    sf = tmp_path / "s.json"
    _plant(sf)
    rc, out = _run(["--storage-state", str(sf), "--emit-status"], capsys)
    assert rc == 0 and len(harness["posted"]) == 1
    assert TOKEN not in json.dumps(harness["posted"][0])
    assert TOKEN not in out


def test_action_wrapper_never_passes_storage_state():
    action = (REPO / "scripts" / "ops" / "breakout_login_check_action.sh").read_text()
    assert "--storage-state" not in action
    assert "session_state" not in action


def test_login_attempt_is_announced_before_the_submit_even_when_it_fails(harness, tmp_path, capsys):
    sf = tmp_path / "s.json"
    harness["adapter"].login_exc = FeasibilityError("login_rejected", "x")
    rc, out = _run(["--storage-state", str(sf)], capsys)
    assert rc == blc.EXIT_FEASIBILITY
    assert "session: login_attempt" in out and "session: relogin" not in out


def test_login_attempt_not_announced_on_reuse_or_for_the_action(harness, tmp_path, capsys):
    sf = tmp_path / "s.json"
    _plant(sf)
    rc, out = _run(["--storage-state", str(sf)], capsys)
    assert rc == 0 and "session: reused" in out and "login_attempt" not in out
    rc, out = _run([], capsys)
    assert rc == 0 and "session: fresh" in out and "login_attempt" not in out


def test_every_session_line_is_flushed():
    """The feed reads these lines through a pipe and counts them even when a
    hard timeout kills the check: each must be printed with flush=True."""
    import re
    src = (REPO / "scripts" / "prop" / "breakout_login_check.py").read_text()
    lines = re.findall(r'print\(f?"session:[^\n]*', src)
    assert len(lines) >= 8
    assert all("flush=True" in ln for ln in lines), [ln for ln in lines if "flush=True" not in ln]
