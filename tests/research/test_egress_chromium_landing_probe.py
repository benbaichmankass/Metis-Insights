"""Guards for the landing-only Chromium probe and its optional proxy (PROP-TERM, 2026-09-30).

The properties that matter and are asserted here, with a FAKE Playwright whose
errors deliberately embed the proxy string:

  1. the proxy value (string, host, user, password) never reaches stdout, stderr
     or the stored result file, on ANY path: success, proxy failure, browser
     launch failure, navigation failure, a page title that echoes the host;
  2. FAIL-CLOSED: a proxy that cannot be used yields ``proxy_unreachable`` and the
     script never navigates to a Breakout host, never launches a second browser
     without the proxy and never asks the direct route for the egress
     organisation; a malformed value never runs at all;
  3. the PASS rule is evaluated by code: three passing runs at least one hour
     apart with an identical known organisation, and the first failure ends it;
  4. the workflow can only pass the secret through an ``env:`` mapping, holds no
     write permission, and posts nothing to an issue or a comment.

The script has no issue-body path; ``stdout`` is what the Actions log (and any
issue that quotes it) would show, so a secret-free stdout is the guarantee.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
for _p in (str(ROOT), str(ROOT / "scripts" / "research")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import egress_chromium_landing_probe as probe  # noqa: E402

HOST, USER, PW, PORT = "zz-proxy-host.example.net", "zzuser42", "zzP4ssw0rd!x", 12321
RAW_URL = f"http://{USER}:{PW}@{HOST}:{PORT}"
RAW_COLON = f"{HOST}:{PORT}:{USER}:{PW}"
SECRET_BITS = (HOST, USER, PW, RAW_URL, RAW_COLON)
APP = "https://app.breakoutprop.com/"
WSS = "https://wss.breakoutprop.com/"
ORG = "AS7018 AT&T Services, Inc."


class FakeError(Exception):
    pass


class _Resp:
    def __init__(self, status, headers):
        self.status, self.headers = status, headers


class _Req:
    def is_navigation_request(self):
        return True


class _NavResp(_Resp):
    def __init__(self, status, headers, frame):
        super().__init__(status, headers)
        self.request, self.frame = _Req(), frame


class _Page:
    def __init__(self, world):
        self.w, self.cur, self.main_frame = world, None, object()
        self.elapsed, self.clear_at, self.then, self.listeners = 0, None, None, []

    def on(self, event, cb):
        self.listeners.append(cb)

    def goto(self, url, **kw):
        self.w.navigations.append(url)
        route = self.w.routes.get(url) or {"status": 404, "body": ""}
        if isinstance(route, Exception):
            raise route
        self.cur, self.elapsed = route, 0
        self.clear_at, self.then = route.get("clears_after_ms"), route.get("then")
        return _Resp(route.get("status", 200), route.get("headers", {"server": "cloudflare"}))

    def _tick(self):
        if self.clear_at is not None and self.elapsed >= self.clear_at:
            self.cur, self.clear_at = self.then, None  # the challenge cleared: the page navigated again
            for cb in self.listeners:
                cb(_NavResp(self.cur.get("status", 200), self.cur.get("headers", {"server": "cloudflare"}), self.main_frame))

    def wait_for_timeout(self, ms):
        self.elapsed += ms
        self._tick()

    def title(self):
        return self.cur.get("title", "")

    def inner_text(self, sel):
        return self.cur.get("body", "")

    def content(self):
        return self.cur.get("html", "")

    def query_selector(self, sel):
        if sel == "body":
            return object()
        if sel.startswith("iframe"):
            return object() if self.cur.get("widget") else None
        if sel.startswith("input[type=password]"):
            return object() if self.cur.get("login") else None
        return object() if self.cur.get("login") else None


class _Ctx:
    def __init__(self, world):
        self.w = world

    def new_page(self):
        if self.w.new_page_error:
            raise self.w.new_page_error
        return _Page(self.w)

    def cookies(self):
        return list(self.w.cookies)

    def close(self):
        if self.w.ctx_close_error:
            raise self.w.ctx_close_error


class _Browser:
    def __init__(self, world):
        self.w = world

    def new_context(self, **kw):
        if self.w.new_context_error:
            raise self.w.new_context_error
        return _Ctx(self.w)

    def close(self):
        if self.w.browser_close_error:
            raise self.w.browser_close_error


class _Chromium:
    def __init__(self, world):
        self.w = world

    def launch(self, **kw):
        self.w.launches.append(kw)
        if self.w.launch_error:
            raise self.w.launch_error
        return _Browser(self.w)


class _PW:
    def __init__(self, world):
        self.chromium = _Chromium(world)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class World:
    def __init__(self):
        self.launches, self.navigations, self.routes, self.launch_error = [], [], {}, None
        self.new_context_error = self.new_page_error = self.ctx_close_error = self.browser_close_error = None
        self.cookies = []


def served():
    return {"status": 200, "headers": {"server": "cloudflare"}, "title": "Breakout", "body": "Sign in", "login": True}


@pytest.fixture
def world(monkeypatch):
    w = World()
    monkeypatch.setattr(probe, "_load_playwright", lambda: (lambda: _PW(w), FakeError))

    def _no_direct():
        raise AssertionError("the direct route was asked for the egress organisation")

    monkeypatch.setattr(probe, "direct_egress_org", _no_direct)
    monkeypatch.delenv("GITHUB_EVENT_NAME", raising=False)
    return w


def run_main(monkeypatch, tmp_path, capsys, secret):
    if secret is None:
        monkeypatch.delenv("EGRESS_PROBE_PROXY", raising=False)
    else:
        monkeypatch.setenv("EGRESS_PROBE_PROXY", secret)
    out = tmp_path / "r.json"
    code = probe.main(["--out", str(out)])
    cap = capsys.readouterr()
    return code, cap.out + cap.err, out


def assert_clean(text):
    for bit in SECRET_BITS:
        assert bit not in text, f"secret fragment leaked: {bit[:4]}..."


# ---------------------------------------------------------------- parsing
@pytest.mark.parametrize("raw", [RAW_URL, RAW_COLON, f"{USER}:{PW}@{HOST}:{PORT}"])
def test_parse_accepts_the_three_shapes(raw):
    s = probe.parse_proxy(raw)
    assert (s.host, s.port, s.username, s.password) == (HOST, PORT, USER, PW)
    assert s.playwright_proxy() == {"server": f"http://{HOST}:{PORT}", "username": USER, "password": PW}


@pytest.mark.parametrize("raw,reason", [
    ("", "empty_or_whitespace"), (f"{HOST}:{PORT} {USER}", "empty_or_whitespace"),
    (f"socks5://{USER}:{PW}@{HOST}:{PORT}", "socks5_with_auth_unsupported"),
    (f"{HOST}:99999:{USER}:{PW}", "bad_port"), (f"{HOST}:{USER}", "unrecognised_shape"),
    (f"http://{USER}@{HOST}:{PORT}", "credentials_need_user_and_password"),
])
def test_parse_refuses_bad_values_without_quoting_them(raw, reason):
    with pytest.raises(probe.ProxyFormatError) as e:
        probe.parse_proxy(raw)
    assert e.value.reason == reason
    assert HOST not in str(e.value) and PW not in str(e.value)


def test_scrub_and_error_token_remove_every_fragment():
    spec = probe.parse_proxy(RAW_URL)
    msg = f"net::ERR_PROXY_CONNECTION_FAILED at {RAW_URL} host={HOST} u={USER} p={PW}"
    assert probe.error_token(FakeError(msg), spec.secrets()) == "net::ERR_PROXY_CONNECTION_FAILED"
    assert probe.error_token(FakeError(f"boom {PW}"), spec.secrets()) == "FakeError"
    assert_clean(probe.scrub(msg, spec.secrets()))


# ---------------------------------------------------------------- fail-closed + no leakage
def test_proxy_unreachable_is_fail_closed_and_silent(world, monkeypatch, tmp_path, capsys):
    world.routes["https://ipinfo.io/org"] = FakeError(
        f"page.goto: net::ERR_PROXY_CONNECTION_FAILED at {RAW_URL} ({HOST}:{PORT} {USER} {PW})")
    code, text, out = run_main(monkeypatch, tmp_path, capsys, RAW_URL)
    assert code == 0
    assert "proxy_state=proxy_unreachable" in text
    assert_clean(text)
    assert_clean(out.read_text())
    assert len(world.launches) == 1 and "proxy" in world.launches[0]
    assert APP not in world.navigations and WSS not in world.navigations  # never reached a target, never went direct
    assert json.loads(out.read_text())["app_pass"] is False


def test_launch_error_that_echoes_the_secret_is_masked(world, monkeypatch, tmp_path, capsys):
    world.launch_error = FakeError(f"launch failed for {RAW_URL} {PW}")
    code, text, _ = run_main(monkeypatch, tmp_path, capsys, RAW_COLON)
    assert code == 1
    assert_clean(text)
    assert len(world.launches) == 1  # no second, proxy-less launch


def test_navigation_error_is_reduced_to_a_token(world, monkeypatch, tmp_path, capsys):
    world.routes["https://ipinfo.io/org"] = {"body": ORG}
    world.routes[APP] = FakeError(f"net::ERR_TUNNEL_CONNECTION_FAILED via {HOST}:{PORT} as {USER}/{PW}")
    world.routes[WSS] = served()
    code, text, out = run_main(monkeypatch, tmp_path, capsys, RAW_URL)
    assert code == 0
    assert "navigation_error=net::ERR_TUNNEL_CONNECTION_FAILED" in text
    assert "proxy_state=proxy_unreachable" in text
    assert_clean(text)
    assert_clean(out.read_text())


def test_a_page_title_that_echoes_the_proxy_is_masked(world, monkeypatch, tmp_path, capsys):
    world.routes["https://ipinfo.io/org"] = {"body": ORG}
    world.routes[APP] = dict(served(), title=f"Proxy {HOST} auth {USER}")
    world.routes[WSS] = served()
    code, text, out = run_main(monkeypatch, tmp_path, capsys, RAW_URL)
    assert code == 0
    assert_clean(text)
    assert_clean(out.read_text())


@pytest.mark.parametrize("bad", ["garbage-value-xyz", f"socks5://{USER}:{PW}@{HOST}:{PORT}"])
def test_malformed_secret_never_runs_is_not_echoed_and_leaves_a_failing_result(world, monkeypatch, tmp_path, capsys, bad):
    code, text, out = run_main(monkeypatch, tmp_path, capsys, bad)
    assert code == 2
    assert "proxy_format_invalid:" in text
    assert "garbage-value-xyz" not in text
    assert_clean(text)
    assert world.launches == [] and world.navigations == []
    r = json.loads(out.read_text())  # a malformed secret must not read as "no run happened"
    assert r["via_proxy"] is True and r["proxy_state"] == "format_invalid" and r["app_pass"] is False
    assert "garbage-value-xyz" not in out.read_text()
    assert_clean(out.read_text())


def test_success_through_the_proxy_records_org_and_passes(world, monkeypatch, tmp_path, capsys):
    world.routes["https://ipinfo.io/org"] = {"body": ORG}
    world.routes[APP] = served()
    world.routes[WSS] = served()
    code, text, out = run_main(monkeypatch, tmp_path, capsys, RAW_URL)
    r = json.loads(out.read_text())
    assert code == 0 and r["via_proxy"] and r["proxy_state"] == "ok" and r["egress_org"] == ORG and r["app_pass"]
    assert world.launches[0]["proxy"]["server"] == f"http://{HOST}:{PORT}"
    assert_clean(text)
    assert_clean(out.read_text())


def test_a_challenge_is_not_a_pass(world, monkeypatch, tmp_path, capsys):
    world.routes["https://ipinfo.io/org"] = {"body": ORG}
    world.routes[APP] = {"status": 403, "headers": {"cf-mitigated": "challenge"}, "title": "Just a moment...",
                         "body": "Just a moment", "login": False}
    world.routes[WSS] = served()
    _, text, out = run_main(monkeypatch, tmp_path, capsys, RAW_URL)
    r = json.loads(out.read_text())
    assert r["app_pass"] is False and "cf-mitigated=challenge" in text


def test_direct_mode_is_unchanged(world, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(probe, "direct_egress_org", lambda: "AS8075 Microsoft Corporation")
    world.routes[APP] = {"status": 403, "headers": {"cf-mitigated": "challenge"}, "title": "Just a moment...", "login": False}
    world.routes[WSS] = served()
    code, text, out = run_main(monkeypatch, tmp_path, capsys, None)
    r = json.loads(out.read_text())
    assert code == 0 and r["via_proxy"] is False and "proxy" not in world.launches[0]
    assert "https://ipinfo.io/org" not in world.navigations
    assert r["egress_org"] == "AS8075 Microsoft Corporation"


def test_schedule_is_gated(world, monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("GITHUB_EVENT_NAME", "schedule")
    monkeypatch.delenv("EGRESS_PROBE_PROXY", raising=False)
    code, text, _ = probe.main(["--out", str(tmp_path / "r.json")]), capsys.readouterr().out, None
    assert code == 0 and "schedule_skipped" in text and world.launches == []
    monkeypatch.setattr(probe, "SCHEDULE_ACTIVE_UNTIL", datetime(2020, 1, 1, tzinfo=timezone.utc))
    monkeypatch.setenv("EGRESS_PROBE_PROXY", RAW_URL)
    code = probe.main(["--out", str(tmp_path / "r.json")])
    text = capsys.readouterr().out
    assert code == 0 and "schedule_expired" in text and world.launches == []


# ---------------------------------------------------------------- review findings (uncaught errors, org lookup)
@pytest.mark.parametrize("where", ["new_context_error", "new_page_error", "ctx_close_error", "browser_close_error"])
def test_errors_outside_the_try_blocks_never_print_the_proxy(world, monkeypatch, tmp_path, capsys, where):
    """A Chromium crash surfaces outside the PlaywrightError handlers; the raw traceback would echo
    --proxy-server=host:port. Host is shouted in UPPER case on purpose (scrub is case-insensitive)."""
    world.routes["https://ipinfo.io/org"] = {"body": ORG}
    world.routes[APP] = served()
    world.routes[WSS] = served()
    setattr(world, where, RuntimeError(f"Chromium crashed: --proxy-server={HOST.upper()}:{PORT} user={USER.upper()} pw={PW}"))
    code, text, out = run_main(monkeypatch, tmp_path, capsys, RAW_URL)
    assert code == 1, text
    assert "error: unexpected failure (RuntimeError)" in text
    assert "Traceback" not in text
    for bit in (HOST, HOST.upper(), USER, USER.upper(), PW, RAW_URL):
        assert bit not in text and bit not in out.read_text()
    r = json.loads(out.read_text())  # the crash leaves a FAILING proxy-mode result, never nothing
    assert r["via_proxy"] is True and r["proxy_state"] == "error" and r["app_pass"] is False


def test_scrub_is_case_insensitive():
    spec = probe.parse_proxy(RAW_URL)
    assert probe.scrub(f"{HOST.upper()} {USER.upper()} {PW.upper()} {HOST.title()}", spec.secrets()).count(probe.MASK) == 4


def test_launch_failure_in_proxy_mode_stores_an_error_result(world, monkeypatch, tmp_path, capsys):
    world.launch_error = FakeError(f"launch failed for {RAW_URL}")
    code, text, out = run_main(monkeypatch, tmp_path, capsys, RAW_URL)
    r = json.loads(out.read_text())
    assert code == 1 and r["proxy_state"] == "error" and r["via_proxy"] is True
    assert_clean(out.read_text())


def test_a_429_from_the_org_lookup_is_org_unreadable_not_ok(world, monkeypatch, tmp_path, capsys):
    world.routes["https://ipinfo.io/org"] = {"status": 429, "body": "Too Many Requests"}
    world.routes[APP] = served()
    world.routes[WSS] = served()
    code, text, out = run_main(monkeypatch, tmp_path, capsys, RAW_URL)
    r = json.loads(out.read_text())
    assert code == 0 and r["proxy_state"] == "org_unreadable" and r["egress_org"] == "unknown" and r["app_pass"] is False
    assert "org_lookup_http_429" in text
    assert world.navigations.count("https://ipinfo.io/org") == 2  # one retry, then give up


def test_an_org_lookup_that_returns_an_error_page_is_org_unreadable(world, monkeypatch, tmp_path, capsys):
    world.routes["https://ipinfo.io/org"] = {"status": 200, "body": "<html>Access denied</html>"}
    world.routes[APP] = served()
    world.routes[WSS] = served()
    _, _, out = run_main(monkeypatch, tmp_path, capsys, RAW_URL)
    assert json.loads(out.read_text())["proxy_state"] == "org_unreadable"


# ---------------------------------------------------------------- the PASS rule
T0 = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
NOW = T0 + timedelta(hours=6)


def rec(minutes, *, ok=True, org=ORG, state="ok", proxy=True):
    ts = (T0 + timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")
    host = {"http_status": "200" if ok else "403", "cf_mitigated": "" if ok else "challenge",
            "title": "Breakout" if ok else "Just a moment...", "markers": [] if ok else ["challenge"],
            "login_form_rendered": ok}
    return {"ts": ts, "via_proxy": proxy, "proxy_state": state, "egress_org": org, "app_pass": ok,
            "hosts": {probe.APP_HOST: host}}


def test_three_spaced_passes_is_pass():
    v, _ = probe.evaluate([rec(0), rec(61), rec(125)], now=NOW)
    assert v == "PASS"


def test_passes_closer_than_an_hour_do_not_count():
    assert probe.evaluate([rec(0), rec(20), rec(40)], now=NOW)[0] == "PENDING"
    assert probe.evaluate([rec(0), rec(30), rec(65), rec(130)], now=NOW)[0] == "PASS"


def test_first_failure_ends_the_route():
    assert probe.evaluate([rec(0), rec(61, ok=False), rec(125), rec(190)], now=NOW)[0] == "FAIL"


def test_organisation_change_or_unknown_org_is_fail():
    assert probe.evaluate([rec(0), rec(61, org="AS701 Verizon"), rec(125)], now=NOW)[0] == "FAIL"
    assert probe.evaluate([rec(0, org="unknown")], now=NOW)[0] == "FAIL"


def test_proxy_unreachable_is_fail_and_direct_runs_are_ignored():
    assert probe.evaluate([rec(0), rec(61, state="proxy_unreachable", ok=False)], now=NOW)[0] == "FAIL"
    assert probe.evaluate([rec(0, proxy=False, ok=False)], now=NOW)[0] == "NO_PROXY_RUNS"


def test_old_results_are_outside_the_window():
    assert probe.evaluate([rec(0), rec(61), rec(125)], now=T0 + timedelta(hours=100))[0] == "NO_PROXY_RUNS"


def test_evaluate_cli_reads_a_directory(tmp_path, capsys):
    for i, m in enumerate((0, 61, 125)):
        d = tmp_path / f"run{i}"
        d.mkdir()
        (d / "probe-result.json").write_text(json.dumps(rec(m)))
    (tmp_path / "junk.json").write_text("{not json")
    # the CLI uses the real clock, so anchor the fixtures to it
    for f in tmp_path.rglob("probe-result.json"):
        r = json.loads(f.read_text())
        r["ts"] = (datetime.now(timezone.utc) - timedelta(hours=5) + (_off := timedelta(minutes=int(f.parent.name[3:]) * 70))
                   ).strftime("%Y-%m-%dT%H:%M:%SZ")
        f.write_text(json.dumps(r))
    assert probe.main(["--evaluate", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "verdict=PASS" in out
    assert probe.main(["--evaluate", str(tmp_path / "nope")]) == 2


def test_a_failed_workflow_run_between_passes_is_a_fail():
    """A crash writes no result file, so three passes AROUND it must not read as PASS."""
    crash = (T0 + timedelta(minutes=90)).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert probe.evaluate([rec(0), rec(61), rec(125)], now=NOW, failed_runs=[crash])[0] == "FAIL"
    assert probe.evaluate([rec(0), rec(61), rec(125)], now=NOW)[0] == "PASS"
    late = (T0 + timedelta(minutes=300)).strftime("%Y-%m-%dT%H:%M:%SZ")  # after the PASS was reached
    assert probe.evaluate([rec(0), rec(61), rec(125)], now=T0 + timedelta(hours=6), failed_runs=[late])[0] == "PASS"


@pytest.mark.parametrize("state", ["error", "format_invalid", "proxy_unreachable", "run_failed"])
def test_any_non_ok_state_ends_the_route(state):
    assert probe.evaluate([rec(0), rec(61, state=state, ok=False), rec(125)], now=NOW)[0] == "FAIL"


def test_org_unreadable_is_inconclusive_not_a_pass_and_not_a_fail():
    """Inconclusive ONLY when the app itself was served (ok=True hosts)."""
    v, why = probe.evaluate([rec(0), rec(61, state="org_unreadable", org="unknown"), rec(125), rec(190)], now=NOW)
    assert v == "PASS"  # passes at 0, 125, 190 (all >= 1 h apart); the unreadable run did not count either way
    v, why = probe.evaluate([rec(0), rec(61, state="org_unreadable", org="unknown")], now=NOW)
    assert v == "PENDING" and "inconclusive" in why[0]


def test_org_unreadable_with_a_challenged_app_is_a_fail_not_a_skipped_run():
    """False-PASS gap: a challenged/blocked landing page must not be hidden by a failed org lookup."""
    v, why = probe.evaluate([rec(0), rec(61, state="org_unreadable", ok=False, org="unknown"), rec(125), rec(190)], now=NOW)
    assert v == "FAIL" and "not served" in why[0]
    lone = rec(0, state="org_unreadable", ok=False, org="unknown")
    lone["hosts"] = {}  # no host record at all is also not a served app
    assert probe.evaluate([lone], now=NOW)[0] == "FAIL"


def test_evaluate_cli_counts_failed_runs_and_refuses_an_unreadable_failed_file(tmp_path, capsys):
    now = datetime.now(timezone.utc)
    for i, m in enumerate((300, 200, 100)):
        d = tmp_path / f"run{i}"
        d.mkdir()
        r = rec(0)
        r["ts"] = (now - timedelta(minutes=m)).strftime("%Y-%m-%dT%H:%M:%SZ")
        (d / "probe-result.json").write_text(json.dumps(r))
    ok = tmp_path / "failed-ok.json"
    ok.write_text("[]")
    assert probe.main(["--evaluate", str(tmp_path), "--failed-runs", str(ok)]) == 0
    assert "verdict=PASS" in capsys.readouterr().out
    crash = tmp_path / "failed-crash.json"
    crash.write_text(json.dumps([(now - timedelta(minutes=150)).strftime("%Y-%m-%dT%H:%M:%SZ")]))
    assert probe.main(["--evaluate", str(tmp_path), "--failed-runs", str(crash)]) == 0
    assert "verdict=FAIL" in capsys.readouterr().out
    assert probe.main(["--evaluate", str(tmp_path), "--failed-runs", str(tmp_path / "nope.json")]) == 2


# ---------------------------------------------------------------- the workflow
def test_workflow_handles_the_secret_safely():
    import yaml
    text = (ROOT / ".github" / "workflows" / "egress-chromium-landing-probe.yml").read_text()
    wf = yaml.safe_load(text)
    perms = wf["permissions"]
    assert perms.get("contents") == "read" and perms.get("actions") == "read"
    assert not [k for k, v in perms.items() if v == "write"]
    for job in wf["jobs"].values():
        for step in job["steps"]:
            run = step.get("run", "")
            assert "secrets.EGRESS_PROBE_PROXY" not in run, "the secret must only be passed through env:"
            assert "issues" not in step.get("uses", "") and "gh issue" not in run and "gh pr comment" not in run
            assert "add-mask" not in run and "echo \"$EGRESS_PROBE_PROXY" not in run
    import re
    holders = [s for j in wf["jobs"].values() for s in j["steps"] if "EGRESS_PROBE_PROXY" in json.dumps(s.get("env", {}))]
    assert len(holders) == 2  # the gate (tests emptiness only) and the probe
    for step in holders:
        for m in re.finditer(r"EGRESS_PROBE_PROXY", step["run"]):
            assert step["run"][max(0, m.start() - 6):m.start()] in (' -z "$',), "the variable may only be tested with -z"
    assert any("egress_chromium_landing_probe.py" in s["run"] for s in holders)
    on = wf.get(True, wf.get("on", {}))
    assert "schedule" not in on and "cron" not in json.dumps(on)  # PROP-TERM egress is PARKED: no standing cron
    assert {"pull_request", "workflow_dispatch"} <= set(on)       # ...a re-run is a dispatch (or a request-file PR)


def test_workflow_withholds_the_secret_from_pull_requests_and_only_reads_main():
    """Review findings 3 and 4: no secret on pull_request / non-main refs; only schedule and dispatch
    runs on main are trusted as evidence, so a fork or PR run cannot plant a result."""
    import yaml
    text = (ROOT / ".github" / "workflows" / "egress-chromium-landing-probe.yml").read_text()
    wf = yaml.safe_load(text)
    for job in wf["jobs"].values():
        for step in job["steps"]:
            expr = step.get("env", {}).get("EGRESS_PROBE_PROXY")
            if expr is None:
                continue
            assert "github.ref == 'refs/heads/main'" in expr
            assert "github.event_name == 'schedule'" in expr and "github.event_name == 'workflow_dispatch'" in expr
            assert "pull_request" not in expr  # a pull_request event can never satisfy the expression
            assert expr.rstrip().endswith("|| '' }}")  # falls back to the empty string, i.e. direct mode
    ev = [s for j in wf["jobs"].values() for s in j["steps"] if "--evaluate" in s.get("run", "")]
    assert len(ev) == 1
    run = ev[0]["run"]
    assert '--event "${ev}"' in run and "--branch main" in run and "for ev in schedule workflow_dispatch" in run
    assert "--failed-runs failed-runs.json" in run and '"failure"' in run and '"cancelled"' in run



def test_only_the_main_job_references_the_environment_and_pr_runs_never_do():
    """Manager decision 2026-09-30: the secret is an ENVIRONMENT secret (restricted to main). A
    pull_request run must never reference the environment (a pending deployment or red check), and
    the PR job must hold no secret, no environment and no evaluate step."""
    import yaml
    wf = yaml.safe_load((ROOT / ".github" / "workflows" / "egress-chromium-landing-probe.yml").read_text())
    jobs = wf["jobs"]
    with_env = {n: j for n, j in jobs.items() if "environment" in j}
    assert list(with_env) == ["probe-main"] and with_env["probe-main"]["environment"] == "egress-probe"
    assert with_env["probe-main"]["if"] == "github.event_name != 'pull_request'"
    pr = jobs["probe"]
    assert pr["if"] == "github.event_name == 'pull_request'"
    assert "EGRESS_PROBE_PROXY" not in json.dumps(pr) and "secrets." not in json.dumps(pr)
    assert "--evaluate" not in json.dumps(pr)


# ---------------------------------------------------------------- levers (variants): wait it out, new headless, headed
TRACE = "https://www.cloudflare.com/cdn-cgi/trace"
TRACE_BODY = "fl=1f1\nh=www.cloudflare.com\nip=203.0.113.77\nts=1.0\nvisit_scheme=https\nuag=x\ncolo=EWR\nhttp=http/2\nloc=US\ntls=TLSv1.3\n"


def challenged(clears_after_ms=None, widget=False):
    page = {"status": 403, "headers": {"cf-mitigated": "challenge", "server": "cloudflare"}, "title": "Just a moment...",
            "body": "Just a moment", "login": False, "widget": widget}
    if clears_after_ms is not None:
        page.update(clears_after_ms=clears_after_ms, then=served())
    return page


def run_variants(monkeypatch, tmp_path, capsys, variants, secret=RAW_URL):
    monkeypatch.setenv("EGRESS_PROBE_PROXY", secret)
    d = tmp_path / "out"
    code = probe.main(["--variants", variants, "--out-dir", str(d)])
    cap = capsys.readouterr()
    return code, cap.out + cap.err, d


def load(d, name):
    return json.loads((d / f"probe-result-{name}.json").read_text())


def test_exit_country_is_read_and_the_address_is_never_kept(world, monkeypatch, tmp_path, capsys):
    world.routes["https://ipinfo.io/org"] = {"body": ORG}
    world.routes[TRACE] = {"body": TRACE_BODY}
    world.routes[APP], world.routes[WSS] = served(), served()
    code, text, out = run_main(monkeypatch, tmp_path, capsys, RAW_URL)
    r = json.loads(out.read_text())
    assert r["exit_country"] == "US" and "exit_colo" not in r
    for kept in (text, out.read_text()):   # ONLY loc= is kept: not ip=, not colo=, not the user-agent line
        assert "203.0.113.77" not in kept and "EWR" not in kept and "uag" not in kept
    assert "exit_country=US" in text


def test_an_unreadable_trace_page_is_unknown_not_a_failure(world, monkeypatch, tmp_path, capsys):
    world.routes["https://ipinfo.io/org"] = {"body": ORG}
    world.routes[APP], world.routes[WSS] = served(), served()  # no trace route: the fake answers 404
    _, _, out = run_main(monkeypatch, tmp_path, capsys, RAW_URL)
    r = json.loads(out.read_text())
    assert r["exit_country"] == "unknown" and r["proxy_state"] == "ok"


def test_wait_variant_records_the_clearing_time_and_can_pass_where_baseline_fails(world, monkeypatch, tmp_path, capsys):
    world.routes["https://ipinfo.io/org"] = {"body": ORG}
    world.routes[APP] = challenged(clears_after_ms=12_000)
    world.routes[WSS] = served()
    code, text, d = run_variants(monkeypatch, tmp_path, capsys, "baseline,wait")
    base, wait = load(d, "baseline"), load(d, "wait")
    app_b, app_w = base["hosts"]["app.breakoutprop.com"], wait["hosts"]["app.breakoutprop.com"]
    assert base["app_pass"] is False and app_b["challenged_at_first_load"] is True and app_b["cleared_after_s"] is None
    assert wait["app_pass"] is True and app_w["challenged_at_first_load"] is True and app_w["cleared_after_s"] == 12
    assert app_w["first_http_status"] == "403" and app_w["first_cf_mitigated"] == "challenge"   # both views are kept
    assert app_w["http_status"] == "200" and app_w["challenge_kind"] == "none"
    assert "variant=wait" in text and "cleared_after_s=12" in text and "per-lever summary" in text
    assert base["variant"] == "baseline" and wait["variant"] == "wait" and code == 0


def test_a_challenge_that_never_clears_is_reported_as_never(world, monkeypatch, tmp_path, capsys):
    world.routes["https://ipinfo.io/org"] = {"body": ORG}
    world.routes[APP], world.routes[WSS] = challenged(), served()
    _, text, d = run_variants(monkeypatch, tmp_path, capsys, "wait")
    app = load(d, "wait")["hosts"]["app.breakoutprop.com"]
    assert app["cleared_after_s"] is None and app["challenge_kind"] == "js_or_managed" and load(d, "wait")["app_pass"] is False
    assert "cleared_after_s=never" in text


def test_an_interactive_widget_is_classified_and_never_clicked(world, monkeypatch, tmp_path, capsys):
    world.routes["https://ipinfo.io/org"] = {"body": ORG}
    world.routes[APP], world.routes[WSS] = challenged(widget=True), served()
    _, text, d = run_variants(monkeypatch, tmp_path, capsys, "wait")
    assert load(d, "wait")["hosts"]["app.breakoutprop.com"]["challenge_kind"] == "interactive_widget"
    assert "challenge_kind=interactive_widget" in text


def test_clearance_cookie_is_recorded_by_name_only(world, monkeypatch, tmp_path, capsys):
    world.routes["https://ipinfo.io/org"] = {"body": ORG}
    world.routes[APP], world.routes[WSS] = served(), served()
    world.cookies = [{"name": "cf_clearance", "value": "SECRETCOOKIEVALUE123"}]
    _, text, d = run_variants(monkeypatch, tmp_path, capsys, "wait")
    assert load(d, "wait")["hosts"]["app.breakoutprop.com"]["cf_clearance_set"] is True
    assert "SECRETCOOKIEVALUE123" not in text and "SECRETCOOKIEVALUE123" not in (d / "probe-result-wait.json").read_text()


def test_every_lever_launches_the_unmodified_browser_with_only_the_documented_options(world, monkeypatch, tmp_path, capsys):
    """Baseline: as before. newheadless: channel chromium. headed: a real window. None hides automation."""
    world.routes["https://ipinfo.io/org"] = {"body": ORG}
    world.routes[APP], world.routes[WSS] = served(), served()
    code, text, d = run_variants(monkeypatch, tmp_path, capsys, "baseline,wait,newheadless,headed")
    launches = {i: kw for i, kw in enumerate(world.launches)}
    assert len(world.launches) == 4 and code == 0
    assert launches[0]["headless"] is True and "channel" not in launches[0]
    assert launches[1]["headless"] is True and "channel" not in launches[1]
    assert launches[2]["headless"] is True and launches[2]["channel"] == "chromium"
    assert launches[3]["headless"] is False and launches[3]["channel"] == "chromium"
    for kw in world.launches:
        assert set(kw) <= {"headless", "channel", "proxy"}, "no args / user_agent / init-script options: nothing hides automation"
        assert kw["proxy"]["server"] == f"http://{HOST}:{PORT}"
    assert sorted(p.name for p in d.iterdir()) == ["probe-result-baseline.json", "probe-result-headed.json",
                                                    "probe-result-newheadless.json", "probe-result-wait.json"]
    assert_clean(text)
    assert all_clean_files(d)


def all_clean_files(d):
    for f in d.iterdir():
        assert_clean(f.read_text())
    return True


def test_unknown_variant_is_refused_without_launching(world, monkeypatch, tmp_path, capsys):
    code, text, _ = run_variants(monkeypatch, tmp_path, capsys, "wait,stealth")
    assert code == 2 and "unknown_variant" in text and world.launches == []


def test_a_lever_that_cannot_start_is_its_own_error_result_and_the_run_is_not_failed(world, monkeypatch, tmp_path, capsys):
    """Only the headed lever needs a display; here every launch fails, so all levers record `error` and the run exits 1."""
    world.launch_error = FakeError(f"browserType.launch: no display; proxy {RAW_URL} {HOST}")
    code, text, d = run_variants(monkeypatch, tmp_path, capsys, "baseline,headed")
    assert code == 1
    for name in ("baseline", "headed"):
        assert load(d, name)["proxy_state"] == "error"
    assert_clean(text)
    assert all_clean_files(d)


def test_malformed_secret_with_variants_stores_a_failing_result_per_lever(world, monkeypatch, tmp_path, capsys):
    code, text, d = run_variants(monkeypatch, tmp_path, capsys, "baseline,wait", secret=f"{HOST}:{PORT} {USER}")
    assert code == 2 and world.launches == []
    assert load(d, "baseline")["proxy_state"] == "format_invalid" and load(d, "wait")["proxy_state"] == "format_invalid"
    assert_clean(text)


def test_a_crash_between_levers_stores_an_error_for_every_lever_not_yet_run(world, monkeypatch, tmp_path, capsys):
    world.routes["https://ipinfo.io/org"] = {"body": ORG}
    world.routes[APP], world.routes[WSS] = served(), served()
    world.browser_close_error = FakeError(f"boom {RAW_URL} {PW}")   # raised outside the per-URL try: escapes to main()
    code, text, d = run_variants(monkeypatch, tmp_path, capsys, "baseline,wait")
    assert code == 1 and "unexpected failure" in text
    assert load(d, "baseline")["proxy_state"] == "error" and load(d, "wait")["proxy_state"] == "error"
    assert_clean(text)
    assert all_clean_files(d)


def test_evaluate_judges_one_variant_at_a_time():
    """A `wait` lever pass must not be counted as a baseline pass, and a baseline FAIL must not end the wait lever."""
    base_fail = rec(0, ok=False)                                # no variant field = baseline (old results stay valid)
    waits = [dict(rec(m), variant="wait") for m in (0, 61, 125)]
    assert probe.evaluate([base_fail] + waits, now=NOW, variant="wait")[0] == "PASS"
    assert probe.evaluate([base_fail] + waits, now=NOW)[0] == "FAIL"
    assert probe.evaluate(waits, now=NOW)[0] == "NO_PROXY_RUNS"


def test_evaluate_cli_takes_a_variant(tmp_path, capsys):
    now = datetime.now(timezone.utc)
    for i, m in enumerate((300, 200, 100)):
        d = tmp_path / f"run{i}"
        d.mkdir()
        r = dict(rec(0), variant="newheadless")
        r["ts"] = (now - timedelta(minutes=m)).strftime("%Y-%m-%dT%H:%M:%SZ")
        (d / "probe-result-newheadless.json").write_text(json.dumps(r))
    assert probe.main(["--evaluate", str(tmp_path), "--variant", "newheadless"]) == 0
    assert "verdict=PASS" in capsys.readouterr().out
    assert probe.main(["--evaluate", str(tmp_path)]) == 0
    assert "verdict=NO_PROXY_RUNS" in capsys.readouterr().out


def test_workflow_takes_a_variants_input_and_judges_the_first_lever():
    import yaml
    text = (ROOT / ".github" / "workflows" / "egress-chromium-landing-probe.yml").read_text()
    wf = yaml.safe_load(text)
    on = wf.get(True, wf.get("on"))
    assert on["workflow_dispatch"]["inputs"]["variants"]["default"] == "baseline"
    steps = wf["jobs"]["probe-main"]["steps"]
    probe_step = next(x for x in steps if x.get("name", "").startswith("Landing-only probe"))
    assert '--variants "${VARIANTS:-baseline}"' in probe_step["run"] and "xvfb-run -a" in probe_step["run"]
    assert probe_step["env"]["VARIANTS"] == "${{ inputs.variants }}"          # via env: never interpolated into the command
    ev = next(x for x in steps if "--evaluate prior" in x.get("run", ""))["run"]
    assert '--variant "${eval_variant}"' in ev and "eval_variant=baseline" in ev   # the rule counts ONLY the default variant
    assert "VARIANTS" not in next(x for x in steps if "--evaluate prior" in x.get("run", "")).get("env", {})
    assert "probe-out/*.json" in json.dumps(steps)
    # the pull_request job is the direct measurement: unchanged, no secret, no variants
    pr_job = json.dumps(wf["jobs"]["probe"])
    assert "EGRESS_PROBE_PROXY" not in pr_job and "--variants" not in pr_job and "secrets." not in pr_job


def test_a_clearance_cookie_also_ends_the_wait_once_the_challenge_page_is_gone(world, monkeypatch, tmp_path, capsys):
    """Some post-challenge pages have no login form we recognise: a cf_clearance cookie plus a non-challenge title is 'cleared'."""
    world.routes["https://ipinfo.io/org"] = {"body": ORG}
    page = challenged(clears_after_ms=12_000)
    page["then"] = {"status": 200, "headers": {"server": "cloudflare"}, "title": "Breakout", "body": "welcome", "login": False}
    world.routes[APP], world.routes[WSS] = page, served()
    world.cookies = [{"name": "cf_clearance", "value": "V"}]
    _, _, d = run_variants(monkeypatch, tmp_path, capsys, "wait")
    app = load(d, "wait")["hosts"]["app.breakoutprop.com"]
    assert app["cleared_after_s"] == 12 and app["login_form_rendered"] is False and app["cf_clearance_set"] is True
