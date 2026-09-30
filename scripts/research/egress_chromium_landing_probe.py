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
  hour apart is PASS. The first failing run, an organisation change, a run that
  ended in an error, or a workflow run that FAILED (``--failed-runs``: a crash
  writes no result file, so the workflow lists such runs itself) is FAIL (the
  route is ended). A run whose organisation lookup was unreadable (``org_unreadable``,
  e.g. a 429) is INCONCLUSIVE (it neither passes nor ends the route) ONLY if the app landing page was itself served; if
  it was challenged, blocked or missing, the run is a FAIL. Fewer than
  three spaced passes and no failure is PENDING.
  THE WINDOW IS INTENDED: only the last 72 h are considered. A FAIL therefore
  ages out of this verdict after 72 h; that is by design (the proxy lasts 24 h,
  the hourly schedule ends 2026-10-04, and the manager records the verdict on
  the PROP-TERM checklist row, which is the durable record).

LEVERS (``--variants a,b,c --out-dir DIR``; diagnostics for choosing how the browser is run through the SAME proxy)
  ``baseline`` (today: Playwright's headless shell, the first response is judged), ``wait`` (keep watching a
  "Just a moment..." page up to 30 s, because a JS/managed challenge often clears by itself; records BOTH the first
  load and ``cleared_after_s``), ``newheadless`` (Chromium's own new headless mode, ``channel="chromium"``) and
  ``headed`` (a real window; needs a display, xvfb on the runner). Each lever is a fresh browser and writes its own
  ``probe-result-<variant>.json`` carrying ``variant``; per-host records add ``challenge_kind`` (``none`` /
  ``js_or_managed`` / ``interactive_widget`` / ``other``: a Turnstile widget is only OBSERVED, never clicked),
  ``cf_clearance_set`` (cookie NAME presence only) and, in proxy mode, ``exit_country`` (the ``loc=`` value only) read from
  Cloudflare's trace page (the ``ip=`` line on it is never kept). ``--evaluate --variant NAME`` judges one lever at a
  time (results with no ``variant`` are ``baseline``), so the PASS rule is unchanged. NOT built, on purpose: any option
  that HIDES automation (webdriver / User-Agent overrides, AutomationControlled): that needs an operator decision.

EVERY PROXY-MODE EXIT WRITES A RESULT (``proxy_state`` ok / proxy_unreachable /
org_unreadable / error / format_invalid), so a crash can never be mistaken for a
quiet success. ``main`` also catches everything except ``SystemExit`` and prints
only a scrubbed token, so no traceback (which could echo Chromium's launch
arguments, i.e. ``--proxy-server=host:port``) is ever emitted.

Exit: 0 for any measurement outcome; 1 the browser cannot start or an unexpected
failure; 2 a malformed proxy value, or an unreadable results directory.
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
from urllib.parse import quote, quote_plus, unquote

URLS = (
    "https://app.breakoutprop.com/",
    "https://wss.breakoutprop.com/",
)
APP_HOST = "app.breakoutprop.com"

#: Levers to try on the SAME proxy (diagnostics for choosing a lever; the PASS rule is unchanged and judges one
#: variant at a time). None of them types, clicks or hides anything: they only change HOW the unmodified Chromium is
#: launched and how long the landing page is observed. ``wait_s``: keep observing a "Just a moment..." page up to
#: this long, because a JS/managed challenge often clears by itself. ``channel="chromium"``: Chromium's own
#: "new headless" mode instead of Playwright's separate headless shell. ``headed``: a real window (needs a display,
#: e.g. xvfb on a runner). Deliberately NOT here: options that HIDE automation (webdriver / User-Agent overrides,
#: AutomationControlled): that needs an explicit operator decision and was not built.
VARIANTS = {
    "baseline": {"wait_s": 0},
    "wait": {"wait_s": 30},
    "newheadless": {"wait_s": 30, "channel": "chromium"},
    "headed": {"wait_s": 30, "channel": "chromium", "headed": True},
}
TRACE_URL = "https://www.cloudflare.com/cdn-cgi/trace"
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
                 f"{self.host}:{self.port}", quote(self.username, safe=""), quote(self.password, safe=""),
                 quote_plus(self.username), quote_plus(self.password)]
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
    """Remove every secret fragment, CASE-INSENSITIVELY (a host is often echoed upper- or lower-cased)."""
    out = str(text)
    for frag in sorted({f for f in (secrets or ()) if f}, key=len, reverse=True):
        out = re.sub(re.escape(frag), MASK, out, flags=re.IGNORECASE)
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


def _new_result(spec, now=None) -> dict:
    return {
        "ts": (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "via_proxy": bool(spec), "proxy_state": "none", "egress_org": "unknown", "hosts": {}, "app_pass": False,
    }


def _lookup_org(page, PlaywrightError, secrets):
    """Read the egress ORGANISATION through the proxy. Returns (state, org, token).

    ``ok`` only when the lookup answered HTTP 200 with an organisation-shaped body. A 429 or an error
    page is ``org_unreadable`` (one retry first); a transport failure is ``proxy_unreachable``.
    """
    token = ""
    for attempt in (1, 2):
        try:
            resp = page.goto("https://ipinfo.io/org", wait_until="domcontentloaded", timeout=30_000)
        except PlaywrightError as exc:
            return "proxy_unreachable", "unknown", error_token(exc, secrets)
        status = resp.status if resp is not None else None
        text = (page.inner_text("body") or "").strip()[:80]
        if status == 200 and ORG_RX.match(text):
            return "ok", text, ""
        token = f"org_lookup_http_{status}"
        if attempt == 1:
            page.wait_for_timeout(2_000)
    return "org_unreadable", "unknown", token


def _lookup_trace(page, PlaywrightError):
    """Exit country through the proxy from Cloudflare's own trace page. ONLY the two-letter ``loc=`` value is kept:
    the page also prints the address (``ip=``) and more, which are never stored or printed."""
    try:
        resp = page.goto(TRACE_URL, wait_until="domcontentloaded", timeout=30_000)
        text = page.inner_text("body") or ""
    except PlaywrightError:
        return "unknown"
    if resp is None or resp.status != 200:
        return "unknown"
    loc = re.search(r"^loc=([A-Z]{2})$", text, re.M)
    return loc.group(1) if loc else "unknown"


def _is_challenge_title(title: str) -> bool:
    return "just a moment" in (title or "").lower()


def _observe(page, secrets):
    """What the landing page looks like NOW. Observation only: nothing is typed or clicked."""
    title = scrub(page.title() or "", secrets)[:80]
    body = page.inner_text("body")[:20000] if page.query_selector("body") else ""
    html = page.content()[:200000]
    markers = [k for k, rx in MARKERS.items() if rx.search(body) or rx.search(html)]
    has_pw = bool(page.query_selector("input[type=password]"))
    has_user = bool(page.query_selector("input#username, input[name=username], input[type=email]"))
    widget = bool(page.query_selector("iframe[src*='challenges.cloudflare.com'], input[name='cf-turnstile-response'], .cf-turnstile"))
    return title, markers, bool(has_pw and has_user), widget, body


def _challenge_kind(title, markers, login, widget, body) -> str:
    """``none`` (served), ``interactive_widget`` (a Turnstile checkbox/iframe is present: needs a click we never make),
    ``js_or_managed`` (a challenge page with no widget: may clear by itself), or ``other``."""
    if login and not _is_challenge_title(title):
        return "none"
    if widget or re.search(r"verify you are human", body or "", re.I):
        return "interactive_widget"
    if _is_challenge_title(title) or "challenge" in markers:
        return "js_or_managed"
    return "other"


def _probe_url(browser, url, cfg, secrets, PlaywrightError, spec, result):
    rec = {"http_status": "", "cf_mitigated": "", "server": "", "title": "", "markers": [],
           "login_form_rendered": False, "navigation_error": "", "first_http_status": "", "first_cf_mitigated": "",
           "first_title": "", "challenged_at_first_load": None, "cleared_after_s": None, "challenge_kind": "unknown",
           "cf_clearance_set": False}
    ctx = browser.new_context(viewport={"width": 1280, "height": 800})
    page = ctx.new_page()
    last_nav = {}

    def _note(resp):  # the LAST main-frame document response: after a challenge clears the page navigates again
        try:
            if resp.request.is_navigation_request() and resp.frame == page.main_frame:
                last_nav["status"] = str(resp.status)
                last_nav["cf"] = scrub({k.lower(): v for k, v in resp.headers.items()}.get("cf-mitigated", ""), secrets)[:40]
        except PlaywrightError:
            pass

    try:
        page.on("response", _note)
        resp = page.goto(url, wait_until="domcontentloaded", timeout=45_000)
        page.wait_for_timeout(5_000)  # let a challenge interstitial or the login form settle
        if resp is not None:
            rec["http_status"] = rec["first_http_status"] = str(resp.status)
            hdr = {k.lower(): v for k, v in resp.headers.items()}
            rec["server"] = scrub(hdr.get("server", ""), secrets)[:40]
            rec["cf_mitigated"] = rec["first_cf_mitigated"] = scrub(hdr.get("cf-mitigated", ""), secrets)[:40]
        title, markers, login, widget, body = _observe(page, secrets)
        rec["first_title"] = title
        served_first = login and not _is_challenge_title(title) and not rec["first_cf_mitigated"] and rec["first_http_status"] == "200"
        rec["challenged_at_first_load"] = not served_first
        if not served_first and cfg.get("wait_s"):
            for sec in range(1, int(cfg["wait_s"]) + 1):  # keep watching: a JS/managed challenge often clears by itself
                page.wait_for_timeout(1_000)
                title, markers, login, widget, body = _observe(page, secrets)
                cleared = any(c.get("name") == "cf_clearance" for c in ctx.cookies())  # name only, never the value
                if (login or cleared) and not _is_challenge_title(title):
                    rec["cleared_after_s"] = 5 + sec
                    break
            if last_nav.get("status"):
                rec["http_status"], rec["cf_mitigated"] = last_nav["status"], last_nav.get("cf", "")
        elif served_first:
            rec["cleared_after_s"] = 0
        rec["title"], rec["markers"], rec["login_form_rendered"] = title, markers, login
        rec["challenge_kind"] = _challenge_kind(title, markers, login, widget, body)
        rec["cf_clearance_set"] = any(c.get("name") == "cf_clearance" for c in ctx.cookies())  # name only, never the value
    except PlaywrightError as exc:
        rec["navigation_error"] = error_token(exc, secrets)
        if spec and is_proxy_error(rec["navigation_error"]):
            result["proxy_state"] = "proxy_unreachable"
    finally:
        ctx.close()
    return rec


def run_probe(spec, now=None, urls=URLS, variant="baseline"):
    """Returns (result_dict, exit_code). ``spec`` is a ProxySpec or None (direct).

    Never returns None: every path yields a result, so the caller can always store one. ``variant`` picks one of
    ``VARIANTS`` (how the browser is launched and how long the landing page is observed).
    """
    cfg = VARIANTS[variant]
    result = _new_result(spec, now)
    result["variant"] = variant
    secrets = spec.secrets() if spec else []
    try:
        sync_playwright, PlaywrightError = _load_playwright()
    except ImportError as exc:
        print(f"environment: playwright not importable ({type(exc).__name__})")
        result["proxy_state"] = "error" if spec else "none"
        result["proxy_error"] = "playwright_not_importable"
        return result, 1
    with sync_playwright() as p:
        try:
            launch_kw = {"headless": not cfg.get("headed", False)}
            if cfg.get("channel"):
                launch_kw["channel"] = cfg["channel"]
            if spec:
                launch_kw["proxy"] = spec.playwright_proxy()  # explicit proxy: Chromium has no direct fallback
            browser = p.chromium.launch(**launch_kw)
        except PlaywrightError as exc:
            token = error_token(exc, secrets)
            print(f"environment: chromium failed to launch ({token})")
            result["proxy_state"] = "error" if spec else "none"
            result["proxy_error"] = token
            return result, 1
        if spec:
            ctx = browser.new_context()
            page = ctx.new_page()
            try:
                state, org, token = _lookup_org(page, PlaywrightError, secrets)
                if state == "ok":
                    result["exit_country"] = _lookup_trace(page, PlaywrightError)
            finally:
                ctx.close()
            result["proxy_state"], result["egress_org"] = state, org
            if token:
                result["proxy_error"] = token
            if state == "proxy_unreachable":
                browser.close()
                return result, 0  # fail-closed: no navigation to the targets, no direct fallback
        else:
            result["egress_org"] = direct_egress_org()
        for url in urls:
            host_key = url.split("//", 1)[1].rstrip("/")
            result["hosts"][host_key] = _probe_url(browser, url, cfg, secrets, PlaywrightError, spec, result)
        browser.close()
    result["app_pass"] = app_pass(result["hosts"].get(APP_HOST)) and (not spec or result["proxy_state"] == "ok")
    return result, 0


def print_result(result: dict) -> None:
    print(f"via_proxy={'yes' if result['via_proxy'] else 'no'}")
    print(f"proxy_state={result['proxy_state']}" + (f" ({result['proxy_error']})" if result.get("proxy_error") else ""))
    print(f"egress_org={result['egress_org']}")
    if result.get("variant"):
        print(f"variant={result['variant']}")
    if result.get("exit_country"):
        print(f"exit_country={result['exit_country']}")
    for host, rec in result["hosts"].items():
        print(f"--- {host}")
        print(f"  http_status={rec['http_status'] or 'none'}")
        print(f"  server={rec['server'] or 'none'}")
        print(f"  cf-mitigated={rec['cf_mitigated'] or 'none'}")
        print(f"  title={rec['title']!r}")
        print(f"  cloudflare_markers={','.join(rec['markers']) or 'none'}")
        print(f"  login_form_rendered={'yes' if rec['login_form_rendered'] else 'no'}")
        if rec.get("challenged_at_first_load") is not None:
            first = (f"{rec['first_http_status'] or 'none'}"
                     f"{' cf-mitigated=' + rec['first_cf_mitigated'] if rec['first_cf_mitigated'] else ''}")
            print(f"  first_load={first} title={rec['first_title']!r} challenged_at_first_load={'yes' if rec['challenged_at_first_load'] else 'no'}")
            print(f"  cleared_after_s={rec['cleared_after_s'] if rec['cleared_after_s'] is not None else 'never'}"
                  f" challenge_kind={rec['challenge_kind']} cf_clearance_set={'yes' if rec['cf_clearance_set'] else 'no'}")
        if rec["navigation_error"]:
            print(f"  navigation_error={rec['navigation_error']}")
    print(f"app_pass={'yes' if result['app_pass'] else 'no'}")


def print_summary(results: list) -> None:
    """One line per lever (variant) for a dispatch that tried several. Diagnostics: the PASS rule is unchanged."""
    print("=== per-lever summary (diagnostic; the PASS rule judges one variant at a time) ===")
    for r in results:
        app = (r.get("hosts") or {}).get(APP_HOST, {})
        cleared = app.get("cleared_after_s")
        print(f"variant={r.get('variant', 'baseline')} proxy_state={r['proxy_state']} exit={r.get('exit_country', 'n/a')}"
              f" org={r['egress_org']} | app: first={app.get('first_http_status') or 'none'}"
              f"{'/' + app['first_cf_mitigated'] if app.get('first_cf_mitigated') else ''}"
              f" final={app.get('http_status') or 'none'} cleared_after_s={cleared if cleared is not None else 'never'}"
              f" kind={app.get('challenge_kind', 'n/a')} login={'yes' if app.get('login_form_rendered') else 'no'}"
              f" app_pass={'yes' if r['app_pass'] else 'no'}")


def _ts(r: dict) -> datetime:
    return datetime.strptime(r["ts"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def evaluate(results, now=None, failed_runs=(), variant="baseline"):
    """(verdict, reasons). Verdict is PASS, FAIL, PENDING or NO_PROXY_RUNS. See the module docstring.

    ``failed_runs`` are ISO timestamps of workflow runs that FAILED (a crash writes no result file). Each
    counts as a proxy-mode failure at that instant, so three passes around a crash are not a PASS.
    """
    now = now or datetime.now(timezone.utc)
    pool = [r for r in results if r.get("via_proxy") and r.get("variant", "baseline") == variant]
    for stamp in failed_runs or ():
        pool.append({"ts": stamp, "via_proxy": True, "proxy_state": "run_failed", "egress_org": "unknown"})
    proxy = sorted((r for r in pool if now - _ts(r) <= timedelta(hours=WINDOW_H)), key=_ts)
    if not proxy:
        return "NO_PROXY_RUNS", ["no proxy-mode result in the last %d h" % WINDOW_H]
    org = None
    passes = []
    inconclusive = 0
    for r in proxy:
        stamp, state = r["ts"], r.get("proxy_state")
        if state == "org_unreadable":
            # The organisation lookup failed (e.g. 429). That is inconclusive ONLY when the app itself was
            # served; a challenged/blocked/missing landing page is a failure whatever the lookup said.
            if not app_pass((r.get("hosts") or {}).get(APP_HOST)):
                h = (r.get("hosts") or {}).get(APP_HOST) or {}
                return "FAIL", [f"{stamp}: organisation lookup unreadable AND app.breakoutprop.com not served "
                                f"(http_status={h.get('http_status') or 'none'}, cf-mitigated={h.get('cf_mitigated') or 'none'}); route ended"]
            inconclusive += 1
            continue
        if state != "ok":
            return "FAIL", [f"{stamp}: {state} (route ended)"]
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
    note = f"; {inconclusive} run(s) inconclusive (organisation lookup unreadable)" if inconclusive else ""
    return "PENDING", [f"{len(passes)} of {NEEDED_PASSES} spaced passing runs so far, no failure{note}"]


def cmd_evaluate(directory: str, failed_file: str = "", variant: str = "baseline") -> int:
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
    failed = []
    if failed_file:
        try:
            failed = [x for x in json.loads(Path(failed_file).read_text()) if isinstance(x, str)]
        except (OSError, ValueError):
            print("evaluate: the failed-runs file is unreadable; refusing to evaluate without it")
            return 2
    verdict, reasons = evaluate(results, failed_runs=failed, variant=variant)
    print(f"variant={variant} results_read={len(results)} "
          f"proxy_runs={sum(1 for r in results if r.get('via_proxy') and r.get('variant', 'baseline') == variant)} failed_runs={len(failed)}")
    print(f"verdict={verdict}")
    for why in reasons:
        print(f"  {why}")
    return 0


#: What the last-resort handler in ``main`` may use to scrub and to store. Filled in as soon as it is known.
_STATE: dict = {"spec": None, "raw_set": False, "out": None, "now": None, "out_dir": None, "variants_left": []}


def _store(result: dict, out) -> None:
    if out:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(json.dumps(result, sort_keys=True))


def _main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--evaluate", metavar="DIR", help="evaluate stored probe-result JSON files instead of probing")
    ap.add_argument("--failed-runs", metavar="FILE", default="",
                    help="with --evaluate: JSON list of ISO timestamps of workflow runs that failed")
    ap.add_argument("--out", default="probe-out/probe-result.json")
    ap.add_argument("--variant", default="baseline", help="with --evaluate: judge only results of this variant (lever)")
    ap.add_argument("--variants", default="",
                    help="comma-separated levers to try in one run (" + ", ".join(VARIANTS) + "); one result file each in --out-dir")
    ap.add_argument("--out-dir", default="probe-out", help="with --variants: where probe-result-<variant>.json is written")
    args = ap.parse_args(argv)
    if args.evaluate:
        return cmd_evaluate(args.evaluate, args.failed_runs, args.variant)
    names = [v.strip() for v in args.variants.split(",") if v.strip()]
    unknown = [v for v in names if v not in VARIANTS]
    if unknown:
        print(f"unknown_variant: choose from {', '.join(VARIANTS)}")
        return 2
    raw = os.environ.get("EGRESS_PROBE_PROXY", "").strip()
    now = datetime.now(timezone.utc)
    _STATE.update(spec=None, raw_set=bool(raw), out=args.out, now=now,
                  out_dir=args.out_dir if names else None, variants_left=list(names))
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
            for name in (names or [None]):  # a malformed secret must not read as "no run happened"
                bad = _new_result(None, now)
                bad.update(via_proxy=True, proxy_state="format_invalid", proxy_error=exc.reason)
                if name:
                    bad["variant"] = name
                    _store(bad, str(Path(args.out_dir) / f"probe-result-{name}.json"))
                else:
                    _store(bad, args.out)
            return 2
        _STATE["spec"] = spec
    if not names:
        result, code = run_probe(spec, now=now)
        _store(result, args.out)
        print_result(result)
        return code
    results, codes = [], []
    for name in names:
        print(f"##### lever: {name}")
        result, code = run_probe(spec, now=now, variant=name)
        _store(result, str(Path(args.out_dir) / f"probe-result-{name}.json"))
        _STATE["variants_left"].remove(name)
        print_result(result)
        results.append(result)
        codes.append(code)
    print_summary(results)
    return 1 if all(codes) else 0  # a lever that cannot start (e.g. no display) is recorded as its own error result


def main(argv=None) -> int:
    """Last-resort wrapper: no traceback is ever printed, because one could echo Chromium's launch
    arguments (``--proxy-server=host:port``). Anything unexpected becomes a scrubbed token, an ``error``
    result on disk (proxy mode) and exit 1."""
    try:
        return _main(argv)
    except SystemExit:
        raise
    except BaseException as exc:  # allow-silent: prints a scrubbed token, stores an error result, returns 1; the traceback is withheld on purpose  # noqa: BLE001
        spec = _STATE.get("spec")
        token = error_token(exc, spec.secrets() if spec else [])
        print(f"error: unexpected failure ({token})")
        if _STATE.get("raw_set") and _STATE.get("out_dir"):
            for name in _STATE.get("variants_left") or []:
                fail = _new_result(spec, _STATE.get("now"))
                fail.update(via_proxy=True, proxy_state="error", proxy_error=token, variant=name)
                _store(fail, str(Path(_STATE["out_dir"]) / f"probe-result-{name}.json"))
        elif _STATE.get("raw_set") and _STATE.get("out"):
            fail = _new_result(spec, _STATE.get("now"))
            fail.update(via_proxy=True, proxy_state="error", proxy_error=token)
            _store(fail, _STATE["out"])
        return 1


if __name__ == "__main__":
    sys.exit(main())
