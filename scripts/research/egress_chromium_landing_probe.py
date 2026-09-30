#!/usr/bin/env python3
"""Landing-only Chromium reachability probe (PROP-TERM egress scoping, 2026-09-30).

Question it answers, and nothing else: from THIS machine's egress, does a real
headless Chromium get SERVED, CHALLENGED or BANNED at Breakout's two terminal
hosts? A plain ``curl`` and a browser can be scored differently by Cloudflare,
so the earlier curl-only measurements (part 1 of the egress scoping series) do
not settle it.

Scope, deliberately narrow (same posture as ``breakout-terminal-probe``'s
landing mode):
  - a FIXED allowlist below; there is NO url argument;
  - one navigation per URL, default headless Chromium, no stealth of any kind;
  - NO credentials, nothing typed, nothing clicked, cookies never printed;
  - prints only: the egress ORGANISATION (never the address), HTTP status of the
    main document, ``server``, ``cf-mitigated``, the page title, Cloudflare
    block/challenge markers, and whether a login form rendered.

Exit 0 always for any HTTP outcome (an outcome is a measurement); 1 only when the
browser cannot start.
"""
from __future__ import annotations

import re
import subprocess
import sys

URLS = (
    "https://app.breakoutprop.com/",
    "https://wss.breakoutprop.com/",
)
MARKERS = {
    "cloudflare_1005_asn_ban": re.compile(r"banned the autonomous system number|error code:?\s*1005", re.I),
    "cloudflare_other_error": re.compile(r"error code:?\s*10\d\d", re.I),
    "challenge": re.compile(r"just a moment|challenge-platform|cf-chl|turnstile|attention required", re.I),
}


def egress_org() -> str:
    """The egress ORGANISATION only (e.g. 'AS8075 Microsoft Corporation'). Never the address."""
    try:
        out = subprocess.run(["curl", "-sS", "-m", "15", "https://ipinfo.io/org"],
                             capture_output=True, text=True, timeout=20).stdout.strip()
        return out[:80] or "unknown"
    except (subprocess.SubprocessError, OSError) as exc:
        return f"unknown ({type(exc).__name__})"


def main() -> int:
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        print(f"environment: playwright not importable ({type(exc).__name__})")
        return 1
    print(f"egress_org={egress_org()}")
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=True)
        except PlaywrightError as exc:
            print(f"environment: chromium failed to launch ({type(exc).__name__})")
            return 1
        for url in URLS:
            print(f"--- {url}")
            ctx = browser.new_context(viewport={"width": 1280, "height": 800})
            page = ctx.new_page()
            status = server = mitigated = ""
            try:
                resp = page.goto(url, wait_until="domcontentloaded", timeout=45_000)
                page.wait_for_timeout(5_000)  # let a challenge interstitial or the login form settle
                if resp is not None:
                    status = str(resp.status)
                    hdr = {k.lower(): v for k, v in resp.headers.items()}
                    server = hdr.get("server", "")[:40]
                    mitigated = hdr.get("cf-mitigated", "")[:40]
                title = (page.title() or "")[:80]
                body = page.inner_text("body")[:20000] if page.query_selector("body") else ""
                html = page.content()[:200000]
                found = [k for k, rx in MARKERS.items() if rx.search(body) or rx.search(html)]
                has_pw = bool(page.query_selector("input[type=password]"))
                has_user = bool(page.query_selector("input#username, input[name=username], input[type=email]"))
                print(f"  http_status={status or 'none'}")
                print(f"  server={server or 'none'}")
                print(f"  cf-mitigated={mitigated or 'none'}")
                print(f"  title={title!r}")
                print(f"  cloudflare_markers={','.join(found) or 'none'}")
                print(f"  login_form_rendered={'yes' if (has_pw and has_user) else 'no'} (password_input={has_pw})")
            except PlaywrightError as exc:
                print(f"  navigation_error={type(exc).__name__}: {str(exc)[:120]}")
            finally:
                ctx.close()
        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
