#!/usr/bin/env python3
"""Landing-only Chromium reachability probe (PROP-TERM egress scoping, 2026-09-30).

Question it answers, and nothing else: from THIS machine's egress (or, when the
optional proxy is configured, through that proxy), does a real headless Chromium
get SERVED, CHALLENGED or BANNED at Breakout's two terminal hosts? A plain
``curl`` and a browser can be scored differently by Cloudflare.

Scope, deliberately narrow (same posture as ``breakout-terminal-probe``'s
landing mode):
  - a FIXED allowlist below; there is NO url argument;
  - one navigation per URL, default headless Chromium, no stealth of any kind;
  - NO credentials, nothing typed, nothing clicked, cookies never printed;
  - prints only: the egress ORGANISATION (never an address), HTTP status of the
    main document, ``server``, ``cf-mitigated``, the page title, Cloudflare
    block/challenge markers, and whether a login form rendered.

OPTIONAL PROXY (``EGRESS_PROBE_PROXY``, a GitHub Actions secret)
  Set: the browser goes through it. Unset or empty: direct, exactly as before.
  FAIL-CLOSED. If it is set and the proxy cannot be used, the run reports
  ``proxy_unreachable`` and NEVER falls back to the direct route. A value that
  cannot be parsed reports ``proxy_format_invalid`` (exit 2) and also never runs
  direct. Accepted shapes (any one): ``http://user:pass@host:port``,
  ``user:pass@host:port`` (http assumed) and ``host:port:user:pass``. Chromium
  cannot authenticate to a SOCKS5 proxy, so ``socks5://`` WITH credentials is
  refused (``proxy_format_invalid: socks5_with_auth_unsupported``).
  NOTHING about the proxy is ever printed: not the string, the host, the port,
  the user, the password or the address. Every exception is reduced to a short
  token (``net::ERR_...`` or the class name) and every printed string is
  scrubbed of the proxy's parts first.

PASS RULE (``--evaluate``), evaluated here and not by a reader
  Over the proxy-mode results, oldest first: each run must pass (see
  ``app_pass``: app.breakoutprop.com HTTP 200, empty ``cf-mitigated``, title not
  "Just a moment...", login form rendered, no Cloudflare marker) with a KNOWN
  egress organisation identical across every run. Three such runs at least one
  hour apart is PASS. The first failing run, or an organisation change, is FAIL
  (the route is ended). Fewer than three spaced passes and no failure is PENDING.

Exit: 0 for any measurement outcome; 1 the browser cannot start; 2 a malformed
proxy value, or an unreadable results directory.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote, unquote

URLS = (
    "https://app.breakoutprop.com/",
    "https://wss.breakoutprop.com/",
)
APP_HOST = "app.breakoutprop.com"
MARKERS = {
    "cloudflare_1005_asn_ban": re.compile(r"banned the autonomous system number|error code:?\s*1005", re.I),
    "cloudflare_other_error": re.compile(r"error code:?\s*10\d\d", re.I),
    "challenge": re.compile(r"just a moment|challenge-platform|cf-chl|turnstile|attention required", re.I),
}
ORG_RX = re.compile(r"^AS\d+\s+\S")
MASK = "<masked>"

#: The hourly schedule is a temporary measurement aid, not a standing cron: past
#: this instant a scheduled run exits at once. (A 24-hour proxy has long expired.)
SCHEDULE_ACTIVE_UNTIL = datetime(2026, 10, 4, tzinfo=timezone.utc)
MIN_SPACING_S = 3600
NEEDED_PASSES = 3
WINDOW_H = 72


class ProxyFormatError(ValueError):
    """Carries a reason token only, never any part of the value."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class ProxySpec:
    def __init__(self, scheme: str, host: str, port: int, username: str, password: str, raw: str):
        self.scheme, self.host, self.port = scheme, host, port
        self.username, self.password, self.raw = username, password, raw

    def playwright_proxy(self) -> dict:
        out = {"server": f"{self.scheme}://{self.host}:{self.port}"}
        if self.username or self.password:
            out["username"], out["password"] = self.username, self.password
        return out

    def secrets(self) -> list:
        parts = [self.raw, self.host, self.username, self.password,
                 f"{self.host}:{self.port}", quote(self.username, safe=""), quote(self.password, safe="")]
        return sorted({p for p in parts if p and len(p) >= 3}, key=len, reverse=True)


def _valid_port(text: str) -> int:
    if not text.isdigit() or not (1 <= int(text) <= 65535):
        raise ProxyFormatError("bad_port")
    return int(text)


def parse_proxy(raw: str) -> ProxySpec:
    """Parse the secret. Raises ProxyFormatError(reason) with a reason that quotes nothing."""
    raw = (raw or "").strip()
    if not raw or any(c in raw for c in "\r\n\t "):
        raise ProxyFormatError("empty_or_whitespace")
    scheme = "http"
    m = re.match(r"^(https?|socks5h?)://(.*)$", raw, re.I)
    body = raw
    if m:
        scheme, body = m.group(1).lower(), m.group(2)
    if "@" in body:  # [user:pass@]host:port
        cred, _, hostport = body.rpartition("@")
        if ":" not in cred:
            raise ProxyFormatError("credentials_need_user_and_password")
        user, _, pw = cred.partition(":")
        user, pw = unquote(user), unquote(pw)
        host, _, port = hostport.rpartition(":")
        if not host or not port:
            raise ProxyFormatError("missing_host_or_port")
    elif m:  # scheme://host:port  (no credentials)
        host, _, port = body.rpartition(":")
        if not host or not port:
            raise ProxyFormatError("missing_host_or_port")
        user = pw = ""
    else:  # host:port:user:pass  (password may itself contain colons)
        bits = body.split(":", 3)
        if len(bits) != 4:
            raise ProxyFormatError("unrecognised_shape")
        host, port, user, pw = bits
    if scheme.startswith("socks"):
        scheme = "socks5"  # Chromium spells it socks5; remote DNS is its default
    if scheme == "socks5" and (user or pw):
        raise ProxyFormatError("socks5_with_auth_unsupported")
    if not host or "/" in host:
        raise ProxyFormatError("bad_host")
    return ProxySpec(scheme, host, _valid_port(port), user, pw, raw)


def scrub(text: str, secrets) -> str:
    out = str(text)
    for s in secrets or ():
        if s:
            out = out.replace(s, MASK)
    return out


def error_token(exc: BaseException, secrets=()) -> str:
    """Reduce an exception to a short, secret-free token: a net::ERR_* code or the class name."""
    msg = scrub(str(exc), secrets)
    m = re.search(r"net::ERR_[A-Z0-9_]+", msg)
    return m.group(0) if m else type(exc).__name__


PROXY_ERRORS = ("ERR_PROXY", "ERR_TUNNEL", "ERR_NO_SUPPORTED_PROXIES", "ERR_SOCKS", "ERR_INVALID_AUTH")


def is_proxy_error(token: str) -> bool:
    return any(t in token for t in PROXY_ERRORS)


def direct_egress_org() -> str:
    """Direct mode only: the egress ORGANISATION (e.g. 'AS8075 Microsoft Corporation'), never the address."""
    try:
        out = subprocess.run(["curl", "-sS", "-m", "15", "https://ipinfo.io/org"],
                             capture_output=True, text=True, timeout=20).stdout.strip()
        return out[:80] if ORG_RX.match(out or "") else "unknown"
    except (subprocess.SubprocessError, OSError) as exc:
        return f"unknown ({type(exc).__name__})"


def app_pass(host: dict) -> bool:
    return bool(
        host
        and host.get("http_status") == "200"
        and not host.get("cf_mitigated")
        and "just a moment" not in (host.get("title") or "").lower()
        and host.get("login_form_rendered") is True
        and not host.get("markers")
    )


def _load_playwright():
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import sync_playwright
    return sync_playwright, PlaywrightError


def run_probe(spec, now=None, urls=URLS):
    """Returns (result_dict, exit_code). ``spec`` is a ProxySpec or None (direct)."""
    try:
        sync_playwright, PlaywrightError = _load_playwright()
    except ImportError as exc:
        print(f"environment: playwright not importable ({type(exc).__name__})")
        return None, 1
    secrets = spec.secrets() if spec else []
    result = {
        "ts": (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "via_proxy": bool(spec), "proxy_state": "none", "egress_org": "unknown", "hosts": {}, "app_pass": False,
    }
    with sync_playwright() as p:
        try:
            launch_kw = {"headless": True}
            if spec:
                launch_kw["proxy"] = spec.playwright_proxy()  # explicit proxy: Chromium has no direct fallback
            browser = p.chromium.launch(**launch_kw)
        except PlaywrightError as exc:
            print(f"environment: chromium failed to launch ({error_token(exc, secrets)})")
            return None, 1
        if spec:
            ctx = browser.new_context()
            page = ctx.new_page()
            try:
                page.goto("https://ipinfo.io/org", wait_until="domcontentloaded", timeout=30_000)
                org = (page.inner_text("body") or "").strip()[:80]
                result["egress_org"] = org if ORG_RX.match(org) else "unknown"
                result["proxy_state"] = "ok"
            except PlaywrightError as exc:
                token = error_token(exc, secrets)
                result["proxy_state"] = "proxy_unreachable"
                result["proxy_error"] = token
            finally:
                ctx.close()
            if result["proxy_state"] != "ok":
                browser.close()
                return result, 0  # fail-closed: no navigation to the targets, no direct fallback
        else:
            result["egress_org"] = direct_egress_org()
        for url in urls:
            host_key = url.split("//", 1)[1].rstrip("/")
            rec = {"http_status": "", "cf_mitigated": "", "server": "", "title": "", "markers": [],
                   "login_form_rendered": False, "navigation_error": ""}
            ctx = browser.new_context(viewport={"width": 1280, "height": 800})
            page = ctx.new_page()
            try:
                resp = page.goto(url, wait_until="domcontentloaded", timeout=45_000)
                page.wait_for_timeout(5_000)  # let a challenge interstitial or the login form settle
                if resp is not None:
                    rec["http_status"] = str(resp.status)
                    hdr = {k.lower(): v for k, v in resp.headers.items()}
                    rec["server"] = scrub(hdr.get("server", ""), secrets)[:40]
                    rec["cf_mitigated"] = scrub(hdr.get("cf-mitigated", ""), secrets)[:40]
                rec["title"] = scrub(page.title() or "", secrets)[:80]
                body = page.inner_text("body")[:20000] if page.query_selector("body") else ""
                html = page.content()[:200000]
                rec["markers"] = [k for k, rx in MARKERS.items() if rx.search(body) or rx.search(html)]
                has_pw = bool(page.query_selector("input[type=password]"))
                has_user = bool(page.query_selector("input#username, input[name=username], input[type=email]"))
                rec["login_form_rendered"] = bool(has_pw and has_user)
            except PlaywrightError as exc:
                rec["navigation_error"] = error_token(exc, secrets)
                if spec and is_proxy_error(rec["navigation_error"]):
                    result["proxy_state"] = "proxy_unreachable"
            finally:
                ctx.close()
            result["hosts"][host_key] = rec
        browser.close()
    result["app_pass"] = app_pass(result["hosts"].get(APP_HOST)) and (
        not spec or (result["proxy_state"] == "ok" and result["egress_org"] != "unknown"))
    return result, 0


def print_result(result: dict) -> None:
    print(f"via_proxy={'yes' if result['via_proxy'] else 'no'}")
    print(f"proxy_state={result['proxy_state']}" + (f" ({result['proxy_error']})" if result.get("proxy_error") else ""))
    print(f"egress_org={result['egress_org']}")
    for host, rec in result["hosts"].items():
        print(f"--- {host}")
        print(f"  http_status={rec['http_status'] or 'none'}")
        print(f"  server={rec['server'] or 'none'}")
        print(f"  cf-mitigated={rec['cf_mitigated'] or 'none'}")
        print(f"  title={rec['title']!r}")
        print(f"  cloudflare_markers={','.join(rec['markers']) or 'none'}")
        print(f"  login_form_rendered={'yes' if rec['login_form_rendered'] else 'no'}")
        if rec["navigation_error"]:
            print(f"  navigation_error={rec['navigation_error']}")
    print(f"app_pass={'yes' if result['app_pass'] else 'no'}")


def _ts(r: dict) -> datetime:
    return datetime.strptime(r["ts"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def evaluate(results, now=None):
    """(verdict, reasons). Verdict is PASS, FAIL, PENDING or NO_PROXY_RUNS. See the module docstring."""
    now = now or datetime.now(timezone.utc)
    proxy = sorted((r for r in results if r.get("via_proxy") and now - _ts(r) <= timedelta(hours=WINDOW_H)), key=_ts)
    if not proxy:
        return "NO_PROXY_RUNS", ["no proxy-mode result in the last %d h" % WINDOW_H]
    org = None
    passes = []
    for r in proxy:
        stamp = r["ts"]
        if r.get("proxy_state") != "ok":
            return "FAIL", [f"{stamp}: {r.get('proxy_state')} (route ended)"]
        if r.get("egress_org", "unknown") == "unknown":
            return "FAIL", [f"{stamp}: egress organisation unknown, so 'same organisation' cannot be shown"]
        if not r.get("app_pass"):
            h = (r.get("hosts") or {}).get(APP_HOST, {})
            return "FAIL", [f"{stamp}: app.breakoutprop.com not served "
                            f"(http_status={h.get('http_status') or 'none'}, cf-mitigated={h.get('cf_mitigated') or 'none'}, "
                            f"login_form_rendered={h.get('login_form_rendered')}); route ended"]
        if org is None:
            org = r["egress_org"]
        elif r["egress_org"] != org:
            return "FAIL", [f"{stamp}: egress organisation changed; route ended"]
        if not passes or (_ts(r) - passes[-1]).total_seconds() >= MIN_SPACING_S:
            passes.append(_ts(r))
        if len(passes) >= NEEDED_PASSES:
            return "PASS", [f"{NEEDED_PASSES} passing runs at least {MIN_SPACING_S // 60} min apart, same egress organisation"]
    return "PENDING", [f"{len(passes)} of {NEEDED_PASSES} spaced passing runs so far, no failure"]


def cmd_evaluate(directory: str) -> int:
    root = Path(directory)
    if not root.is_dir():
        print("evaluate: results directory not found")
        return 2
    results = []
    for f in sorted(root.rglob("*.json")):
        try:
            results.append(json.loads(f.read_text()))
        except (OSError, ValueError):
            print(f"evaluate: skipped an unreadable file ({f.name})")
    results = [r for r in results if isinstance(r, dict) and "ts" in r]
    verdict, reasons = evaluate(results)
    print(f"results_read={len(results)} proxy_runs={sum(1 for r in results if r.get('via_proxy'))}")
    print(f"verdict={verdict}")
    for why in reasons:
        print(f"  {why}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--evaluate", metavar="DIR", help="evaluate stored probe-result JSON files instead of probing")
    ap.add_argument("--out", default="probe-out/probe-result.json")
    args = ap.parse_args(argv)
    if args.evaluate:
        return cmd_evaluate(args.evaluate)
    raw = os.environ.get("EGRESS_PROBE_PROXY", "").strip()
    now = datetime.now(timezone.utc)
    if os.environ.get("GITHUB_EVENT_NAME") == "schedule":
        if now >= SCHEDULE_ACTIVE_UNTIL:
            print("schedule_expired: the hourly measurement window has ended")
            return 0
        if not raw:
            print("schedule_skipped: no proxy configured")
            return 0
    spec = None
    if raw:
        try:
            spec = parse_proxy(raw)
        except ProxyFormatError as exc:
            print(f"proxy_format_invalid: {exc.reason}")
            return 2
    result, code = run_probe(spec, now=now)
    if result is None:
        return code
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(result, sort_keys=True))
    print_result(result)
    return code


if __name__ == "__main__":
    sys.exit(main())
