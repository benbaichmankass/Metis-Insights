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


class _Page:
    def __init__(self, world):
        self.w, self.cur = world, None

    def goto(self, url, **kw):
        self.w.navigations.append(url)
        route = self.w.routes.get(url)
        if isinstance(route, Exception):
            raise route
        self.cur = route
        return _Resp(route.get("status", 200), route.get("headers", {"server": "cloudflare"}))

    def wait_for_timeout(self, ms):
        pass

    def title(self):
        return self.cur.get("title", "")

    def inner_text(self, sel):
        return self.cur.get("body", "")

    def content(self):
        return self.cur.get("html", "")

    def query_selector(self, sel):
        if sel == "body":
            return object()
        if sel.startswith("input[type=password]"):
            return object() if self.cur.get("login") else None
        return object() if self.cur.get("login") else None


class _Ctx:
    def __init__(self, world):
        self.w = world

    def new_page(self):
        return _Page(self.w)

    def close(self):
        pass


class _Browser:
    def __init__(self, world):
        self.w = world

    def new_context(self, **kw):
        return _Ctx(self.w)

    def close(self):
        pass


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
def test_malformed_secret_never_runs_and_is_not_echoed(world, monkeypatch, tmp_path, capsys, bad):
    code, text, out = run_main(monkeypatch, tmp_path, capsys, bad)
    assert code == 2
    assert "proxy_format_invalid:" in text
    assert "garbage-value-xyz" not in text
    assert_clean(text)
    assert world.launches == [] and world.navigations == [] and not out.exists()


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
    assert "cron" in json.dumps(wf.get(True, wf.get("on", {})))  # the temporary hourly schedule exists...
    assert "2026-10-04" in text  # ...and is bounded
