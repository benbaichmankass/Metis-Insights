#!/usr/bin/env python3
"""Read-only MEASUREMENT of Breakout's proprietary terminal (lane PROP-TERM).

Spec: ``docs/research/prop-automation-options-2026-09-27.md`` § 2.4. Run on the
live VM by the ``breakout-terminal-probe`` system-action
(``scripts/ops/breakout_terminal_probe_action.sh``), from the same egress IP
and the same isolated Chromium the DXtrade feed uses.

It answers, in order, and prints every answer (redacted):

1. **Landing** (always): what a default headless Chromium gets at the probe's
   ``login_url`` (``config/prop_platforms.yaml`` ``probes.breakout_terminal``,
   default ``https://app.breakoutprop.com/``): the page classification
   (challenge / login form / dashboard / …), the redacted page shape (form
   id/class/action path, input id/name/type — NEVER values — button text,
   iframe origins, first 40 text lines), canvas / iframe / input counts, and
   the ORIGINS (scheme + host only) of every request the page made. Nothing
   is typed or clicked. Run 36337076971 hit this URL and only recorded
   ``unknown_page``; this is the stage that says what the page is.
2. **Login** (``--login`` only): ONE credential attempt through
   ``BreakoutTerminalAdapter.login`` — fill the one password field and its
   identity field, submit, and if the dashboard shows an "Open Terminal"
   control, press it (a navigation control) and wait for the terminal. A
   challenge / CAPTCHA / emailed code / 2FA / rejected login / no account
   ends it with ``feasibility: <reason>`` (exit 4) and the page shape of where
   it stopped — never retried, never worked around.
3. **Terminal** (after a login that reached it): account metrics as parsed,
   the positions / orders tables as parsed (``None`` = no such table found),
   the redacted structure dump (labels with their ancestor chains, every
   table's header row), ``dump_tables`` (per VIEW tab: every table, how the
   readers classify it, the first row hovered and its controls' markup with
   ids masked), and — passively — whether an order form is already open and
   whether a one-click-trading control exists.
4. **Ticket** (``--probe-ticket`` only, after 3): ``probe_order_ticket`` —
   opens the order form only if one-click trading reads OFF, records its
   field labels / buttons / canvas counts, types NOTHING, closes it.
   ``canvas_ticket`` is exit 4.

It has NO code path that places, modifies, cancels or closes an order: it
never calls ``place_bracket``, ``modify_bracket``, ``cancel_order`` or
``flatten`` (a test asserts the names are absent). It never prints the
username, the password, a cookie, storage, or a URL path/query. No
screenshots, no tracing, no anti-detection of any kind.

Exit codes: 0 done (landing only, or reached the terminal and every read
parsed); 3 reached the terminal but a read did not parse; 4 feasibility
stop; 5 environment (Playwright/Chromium unusable); 1 anything else.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, List, Optional, Sequence, Set

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.prop.platform import FeasibilityError, adapter_for_platform, load_platform_config  # noqa: E402
from src.prop.platform.dxtrade import _strip_url, redact_text  # noqa: E402

EXIT_OK, EXIT_ERROR, EXIT_UNPARSED, EXIT_FEASIBILITY, EXIT_ENV = 0, 1, 3, 4, 5

COUNTS_JS = r"""
() => ({canvases: document.querySelectorAll('canvas').length,
        iframes: document.querySelectorAll('iframe').length,
        inputs: document.querySelectorAll('input, select, textarea').length,
        password_inputs: document.querySelectorAll('input[type=password]').length,
        role_tabs: document.querySelectorAll('[role=tab]').length,
        tables: document.querySelectorAll('table, [role=grid], [role=table]').length,
        elements: document.querySelectorAll('*').length})
"""


# Account / login / order numbers: a 5+ digit run, optionally with a short
# letter prefix and a dash or hash ("BO-1234567", "#88231", "1029384"). A
# comma-grouped balance ("4,724.50") never matches. Applied to EVERY printed
# line on top of redact_text (the brief: no account numbers in logs).
_ID_RE = re.compile(r"(?<![\w.,])(?:[A-Za-z]{1,4}[-#]?|#)?\d{5,}(?![\w,]|\.\d)")


def redact_ids(text: str) -> str:
    return _ID_RE.sub("<id>", text)


def _say(line: str, secrets: Sequence[str] = ()) -> None:
    print(redact_ids(redact_text(line, *secrets)), flush=True)


def origins_line(urls: Sequence[str]) -> str:
    """Sorted unique ORIGINS (scheme://host[:port]) of the given URLs. Pure."""
    seen: Set[str] = set()
    for u in urls:
        o = _strip_url(u)
        if "://" in o:
            seen.add(o)
    return f"probe.request_origins: {sorted(seen)}"


def stage_dump(adapter: Any, page: Any, stage: str, secrets: Sequence[str]) -> None:
    """Classification + counts + redacted page shape of ``page``. Read-only."""
    try:
        state = adapter.page_state(page)
    except FeasibilityError as fe:
        state = f"feasibility:{fe.reason}"
    except Exception as exc:
        state = f"error:{type(exc).__name__}"
    _say(f"probe.{stage}.state: {state}")
    _say(f"probe.{stage}.location: {adapter._where(page)}")
    try:
        _say(f"probe.{stage}.counts: {page.evaluate(COUNTS_JS)}")
    except Exception as exc:
        _say(f"probe.{stage}.counts: ERROR {type(exc).__name__}")
    for line in adapter.page_shape(page, secrets):
        _say(line, secrets)


def terminal_dump(adapter: Any, page: Any, secrets: Sequence[str]) -> bool:
    """Everything the terminal exposes to the read path. Returns True when
    balance/equity, positions and orders all parsed."""
    ok = True
    snap = adapter.read_account(page)
    _say(f"probe.terminal.account: {snap.as_dict()}", secrets)
    if snap.balance is None and snap.equity is None:
        ok = False
    for name, fn in (("positions", adapter.read_positions), ("orders", adapter.read_orders)):
        try:
            rows = fn(page)
            _say(f"probe.terminal.{name}: {len(rows)} parsed", secrets)
            for r in rows[:10]:
                d = r.as_dict()
                d.pop("raw", None)
                _say(f"probe.terminal.{name}.row: {d}", secrets)
        except LookupError as exc:
            ok = False
            _say(f"probe.terminal.{name}: NOT FOUND ({exc})", secrets)
    oc = adapter.read_one_click(page)
    _say(f"probe.terminal.one_click: {oc}", secrets)
    form = adapter._find_form(page)
    _say(f"probe.terminal.form_already_open: found={form.get('found')} "
         f"fields={sorted((form.get('fields') or {}).keys())} buttons={form.get('buttons') or {}} "
         f"canvases_in_page={form.get('canvases_in_page')} inputs_in_page={form.get('inputs_in_page')}", secrets)
    for line in adapter.structure(page, secrets):
        _say(line, secrets)
    # Every table per VIEW tab, with the first row hovered and its controls'
    # markup (ids masked): what the close / cancel selection will key on.
    for line in adapter.dump_tables(page, secrets):
        _say(line, secrets)
    return ok


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--probe", default="breakout_terminal",
                    help="entry under `probes:` in config/prop_platforms.yaml")
    ap.add_argument("--login", action="store_true", help="stage 2: ONE credential login attempt")
    ap.add_argument("--probe-ticket", default=None, metavar="VENUE_SYMBOL",
                    help="stage 4 (needs --login): read-only order-ticket probe for this symbol")
    ap.add_argument("--timeout-s", type=int, default=45)
    ap.add_argument("--settle-s", type=int, default=12,
                    help="wait after landing before reading (client-rendered pages)")
    args = ap.parse_args(argv)
    if args.probe_ticket and not args.login:
        print("usage: --probe-ticket needs --login")
        return EXIT_ERROR

    cfg = load_platform_config(args.probe, section="probes")
    username = os.environ.get(cfg.get("username_env", ""), "")
    password = os.environ.get(cfg.get("password_env", ""), "")
    secrets = (username, password)
    print(f"probe: {args.probe}")
    print(f"platform: {cfg['platform']}")
    print(f"login_url: {cfg['login_url']}")
    print(f"stages: landing{', login' if args.login else ''}{', ticket' if args.probe_ticket else ''}")
    if args.login:
        print(f"credentials: {cfg.get('username_env')} {'set' if username else 'MISSING'}, "
              f"{cfg.get('password_env')} {'set' if password else 'MISSING'} (values never printed)")
        if not username or not password:
            print("feasibility: no_credentials")
            return EXIT_FEASIBILITY

    adapter = adapter_for_platform(cfg["platform"])
    adapter.timeout_ms = args.timeout_s * 1000
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        print(f"environment: playwright is not importable ({exc})")
        return EXIT_ENV

    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch(headless=True)
        except Exception as exc:
            _say(f"environment: chromium failed to launch ({type(exc).__name__}: "
                 f"{(str(exc).splitlines() or [''])[0][:300]})", secrets)
            return EXIT_ENV
        try:
            context = browser.new_context()
            page = context.new_page()
            requested: List[str] = []
            context.on("request", lambda r: requested.append(r.url))

            # ── stage 1: landing ──
            try:
                resp = page.goto(cfg["login_url"], wait_until="domcontentloaded", timeout=args.timeout_s * 1000)
                _say(f"probe.landing.http_status: {resp.status if resp else None}")
            except Exception as exc:
                _say(f"probe.landing.goto: ERROR {type(exc).__name__}: {(str(exc).splitlines() or [''])[0][:200]}")
            time.sleep(max(0, args.settle_s))
            stage_dump(adapter, page, "landing", secrets)
            _say(origins_line(requested))
            if not args.login:
                print("probe: landing only (no credentials used)")
                return EXIT_OK

            # ── stage 2: login (ONE attempt; the adapter re-navigates) ──
            try:
                adapter.login(page, cfg["login_url"], username, password)
            except FeasibilityError as fe:
                _say(f"feasibility: {fe.reason}" + (f" ({fe.detail})" if fe.detail else ""), secrets)
                stage_dump(adapter, adapter._t(page), "stopped", secrets)
                _say(origins_line(requested))
                return EXIT_FEASIBILITY
            print("probe.login: reached the terminal")
            term = adapter._t(page)
            _say(f"probe.terminal.new_tab: {term is not page}")
            _say(origins_line(requested))

            # ── stage 3: terminal ──
            adapter.wait_ready(page, timeout_ms=20_000)
            parsed = terminal_dump(adapter, page, secrets)

            # ── stage 4: ticket (read-only) ──
            if args.probe_ticket:
                got = adapter.probe_order_ticket(page, args.probe_ticket)
                _say(f"probe.ticket: {got}", secrets)
                if got.get("surface") == "canvas_ticket":
                    print("feasibility: canvas_ticket")
                    return EXIT_FEASIBILITY
            return EXIT_OK if parsed else EXIT_UNPARSED
        except Exception as exc:
            _say(f"probe: ERROR {type(exc).__name__}: {(str(exc).splitlines() or [''])[0][:300]}", secrets)
            return EXIT_ERROR
        finally:
            try:
                browser.close()
            except Exception:
                pass


if __name__ == "__main__":
    sys.exit(main())
