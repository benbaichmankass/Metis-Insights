#!/usr/bin/env python3
"""Read-only login check for a prop account's web terminal (probe step 1).

Spec: ``docs/research/prop-automation-options-2026-09-27.md`` § 4, step 1.
Run on the live VM by the ``breakout-login-check`` system-action
(``scripts/ops/breakout_login_check_action.sh``).

What it does, in order:
1. Resolves the account's platform adapter from ``config/prop_platforms.yaml``.
2. Launches Playwright Chromium, **headless, default configuration** — no
   stealth plugin, no user-agent or fingerprint change, no fake jitter.
3. Registers a READ-ONLY ``page.on("response", ...)`` hook (see step 6)
   BEFORE logging in, so it also sees whatever the login/account flow
   itself triggers.
4. Logs in with the username/password env vars named in that config.
5. Reads balance/equity, open positions and working orders. Prints all of
   it as it goes. Never prints the username, the password, or a cookie.
   When part of that read did not parse, also prints the adapter's
   REDACTED structure dump (``DXtradeAdapter.structure``: label elements,
   header rows, ancestor classes, visible text with e-mails/tokens/
   credentials stripped) and, with ``--dump-dir``, writes the page text +
   tables to disk (VM-only) — so selectors can be fixed from the public
   run log.
6. Reads per-symbol instrument specs (contract size, lot/tick step, etc.)
   for ``--symbols`` (default: ``BTCUSD,ETHUSD,SOLUSD,ADAUSD,XRPUSD`` —
   BTC/ETH/SOL confirmed dxtrade symbols, ADA/XRP candidates pending
   ``PI-20260927-ODDTM5QY-0002``) from the responses step 3 captured —
   never from the UI (three rounds of scraping an info panel never
   converged; see ``src/prop/platform/dxtrade.py``'s module docstring).
   Purely passive: nothing is clicked, filled, or navigated for this
   step, so it can run at any point without affecting what step 5 saw.
   A symbol this never turns up for reports no fields, never a fabricated
   number; a response with no matching object contributes only a
   redacted discovery line (origin+path and key names, never a value).
   ``--symbols=''`` skips this read entirely. Its result NEVER affects
   the exit code.
7. Only with ``--emit-status``: posts ONE ``account_status`` to the local
   ``POST /api/bot/prop/report`` (the existing ingest chokepoint). Default OFF.

``--storage-state PATH`` (used ONLY by the scheduled feed,
``scripts/ops/prop_feed_tick.sh``; operator decision 2026-09-28 "Reuse saved
session"): step 4 first opens the terminal with the Playwright storage_state
saved at PATH and accepts it only on the same POSITIVE terminal marker a login
needs (``DXtradeAdapter.resume_session`` -- which fills and clicks nothing).
Anything else deletes the file and falls through to ONE normal credential
login; there is no second attempt, so it can never loop. After a good session
either way, the state is re-saved to PATH (mode 0600, atomic replace).
Prints ``session: reused`` or ``session: relogin``. Without the flag
(the ``breakout-login-check`` action) every run is a fresh login and prints
``session: fresh``. The state file is a credential equivalent: this script
never prints, dumps or posts its content -- only whether it was used.

What it can NOT do: click any order control. The adapter's order methods
raise ``NotImplementedError`` and this script never calls them.

Exit codes:
  0  login ok and every read parsed
  3  login ok, but part of the read path did not parse (selector work needed)
  4  feasibility stop: challenge / CAPTCHA / 2FA / login rejected / timeout /
     no credentials. Printed as ``feasibility: <reason>``.
  5  environment: Playwright or Chromium is not usable on this host
  1  anything else
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.prop.platform import API_PLATFORMS, FeasibilityError, adapter_for_platform, load_platform_config  # noqa: E402

EXIT_OK, EXIT_ERROR, EXIT_UNPARSED, EXIT_FEASIBILITY, EXIT_ENV = 0, 1, 3, 4, 5

# BTCUSD/ETHUSD/SOLUSD are the operator-confirmed DXtrade symbols in
# config/prop_rulesets/breakout_routing.yaml (2026-06-23). ADAUSD/XRPUSD are
# CANDIDATE names only — the venue's convention of dropping the perp "T"
# suffix, unconfirmed (PI-20260927-ODDTM5QY-0002) — this read either
# confirms or refutes them, it does not assume them. AVAXUSD (PROP-ETH,
# 2026-09-29) is the SAME kind of candidate, added here so this passive read
# at least reports whatever the terminal's own traffic happens to carry for
# it; MEASURED 2026-09-29 (issue #14038) that this passive path reports
# fields ONLY for symbols the terminal's own traffic already requests
# (ETHUSD/SOLUSD, the routed strategies) — BTCUSD/ADAUSD/XRPUSD already
# report nothing here, and AVAXUSD is expected to do the same until it is
# reached by the active `instrument-probe` step (prop_executor_tick.py
# --instrument-probe) instead.
DEFAULT_INSTRUMENT_SYMBOLS = ("BTCUSD", "ETHUSD", "SOLUSD", "ADAUSD", "XRPUSD", "AVAXUSD")


def _strip_raw(items: list) -> list:
    """Drop each row's vendor ``raw`` blob before it leaves this process —
    same redaction the stdout print already applies (main(), the ``positions``/
    ``orders`` loop); a report posted to a Tier-1 API is not the place to
    forward whatever the terminal's DOM happened to carry."""
    out = []
    for it in items:
        d = dict(it)
        d.pop("raw", None)
        out.append(d)
    return out


def build_status_report(
    account_id: str, snapshot: Dict[str, Any],
    positions: Optional[list] = None, orders: Optional[list] = None,
) -> Optional[Dict[str, Any]]:
    """The ``account_status`` body for ``ingest_report``; None if balance AND
    equity are both unread (never post a snapshot of nothing).

    ``positions`` / ``orders`` are three-state, matching the read loop in
    ``main()`` that produces them: ``None`` — the terminal read raised
    (``LookupError``, e.g. an unparsed table), so this is "we did not look",
    never "flat"; ``[]`` — a confirmed clean read of nothing resting; a
    non-empty list — the rows themselves (``Position``/``WorkingOrder``
    ``as_dict()``, ``raw`` stripped).

    Before this, ``main()`` already read positions/orders off the terminal
    every ``ict-prop-feed`` tick (5 min) and printed them to the unit's own
    journal, but never forwarded them here — so ``prop_account_status`` (and
    therefore ``GET /api/bot/prop/status``) carried balance/equity only, with
    no durable broker-side record of open positions or their protective
    levels at that same read. ``ingest_report`` (``src/prop/prop_report.py``)
    already stores the WHOLE posted report verbatim in the ``raw`` column, so
    adding these keys here is what it takes for that already-scheduled,
    already-read-only tick to actually carry them forward — no schema change,
    no new login, no new read.
    """
    if snapshot.get("balance") is None and snapshot.get("equity") is None:
        return None
    return {
        "kind": "account_status",
        "account_id": account_id,
        "balance": snapshot.get("balance"),
        "equity": snapshot.get("equity"),
        "unrealized": snapshot.get("unrealized"),
        "realized_today": snapshot.get("realized_today"),
        "source": "breakout_login_check",
        "open_positions": None if positions is None else _strip_raw(positions),
        "open_orders": None if orders is None else _strip_raw(orders),
    }


def post_status(report: Dict[str, Any], api_base: str) -> Dict[str, Any]:
    req = urllib.request.Request(
        api_base.rstrip("/") + "/api/bot/prop/report",
        data=json.dumps(report).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    token = os.environ.get("DASHBOARD_API_TOKEN", "").strip()
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310 (localhost)
        return json.loads(resp.read().decode() or "{}")


def load_storage_state(path: str) -> Optional[Dict[str, Any]]:
    """The saved session at ``path``, or None. A file that is not a JSON
    object with a ``cookies`` list is corrupt: it is DELETED (so the next tick
    cannot trip on it again) and None is returned. Never prints its content."""
    p = Path(path)
    if not p.exists():
        return None
    try:
        state = json.loads(p.read_text())
        if not isinstance(state, dict) or not isinstance(state.get("cookies"), list):
            raise ValueError("not a storage_state object")
        return state
    except Exception as exc:
        print(f"session: saved state unusable ({type(exc).__name__}); deleted", flush=True)
        discard_storage_state(path)
        return None


def discard_storage_state(path: str) -> None:
    try:
        Path(path).unlink()
    except FileNotFoundError:
        pass


def save_storage_state(context: Any, path: str) -> None:
    """Write the context's storage_state to ``path``: created 0600 from the
    first byte (never world-readable, even briefly), then atomically moved into
    place. Prints only that it saved, never the content."""
    state = context.storage_state()
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.tmp")
    try:
        tmp.unlink()
    except FileNotFoundError:
        pass
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(state, fh)
    os.replace(tmp, p)
    print("session: state saved", flush=True)


def _redact(text: str, *secrets: str, limit: int = 0) -> str:
    """Everything this script prints goes to a PUBLIC issue comment: strip the
    secrets case-insensitively, URL paths/queries, e-mails and token runs.
    ``limit`` truncates AFTER redaction; never slice text before passing it
    here, or a secret split by the cut no longer matches and its prefix leaks."""
    from src.prop.platform.dxtrade import redact_text
    out = redact_text(text, *secrets)
    return out if not limit or len(out) <= limit else out[:limit] + "…"


def layout_watchlist_line(adapter: Any, page: Any, *, polls: int = 3, wait_ms: int = 1_000) -> str:
    """``layout_watchlist: ok n=<k> symbols=<...>`` or ``layout_watchlist: MISSING (<why>)``.
    Polls a slow-rendering watchlist a few times; a read that cannot look is
    MISSING with its reason (the feed decides what to do with it)."""
    wl: Dict[str, Any] = {}
    for i in range(polls):
        wl = adapter.watchlist_symbols(page) or {}
        if wl.get("readable"):
            syms = list(wl.get("symbols") or [])
            return f"layout_watchlist: ok n={len(syms)} symbols={','.join(syms)}"
        if i + 1 < polls:
            page.wait_for_timeout(wait_ms)
    return f"layout_watchlist: MISSING ({wl.get('why') or 'not readable'})"


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--account", default="breakout_1")
    ap.add_argument("--emit-status", action="store_true",
                    help="post ONE account_status through POST /api/bot/prop/report (default: off)")
    ap.add_argument("--layout-canary", action="store_true",
                    help="print a click-free 'layout_watchlist:' line (TRADEIFY-GOLIVE: the feed pings once "
                         "when the watchlist widget disappears); never affects the exit code")
    ap.add_argument("--api-base", default="http://127.0.0.1:8001")
    ap.add_argument("--timeout-s", type=int, default=45)
    ap.add_argument("--dump-tables", action="store_true",
                    help="print every table the read path extracts (kind, headers, row count, first rows, "
                         "ids masked) and how the positions / orders readers classify it (read-only diagnostic)")
    ap.add_argument("--dump-dir", default="",
                    help="write the post-login page text + extracted tables here (on the VM, "
                         "never to stdout) so selectors can be fixed from the first live run")
    ap.add_argument("--storage-state", default="",
                    help="reuse/save the Playwright session at this path (the scheduled feed "
                         "only; a credential equivalent -- never printed)")
    ap.add_argument("--symbols", default=None,
                    help="comma-separated symbols to read instrument specs for, from captured "
                         f"network responses (default: {','.join(DEFAULT_INSTRUMENT_SYMBOLS)}); "
                         "--symbols='' (empty) skips the instrument-spec read entirely")
    args = ap.parse_args(argv)

    cfg = load_platform_config(args.account)
    platform = cfg["platform"]
    username = os.environ.get(cfg.get("username_env", ""), "")
    password = os.environ.get(cfg.get("password_env", ""), "")
    print(f"account: {args.account}")
    print(f"platform: {platform}")
    print(f"login_url: {cfg['login_url']}")
    print(f"credentials: username {'set' if username else 'MISSING'}, "
          f"password {'set' if password else 'MISSING'} (values never printed)")
    if not username or not password:
        print("feasibility: no_credentials (sync BREAKOUT_DX_* to the VM .env first)")
        return EXIT_FEASIBILITY

    if platform in API_PLATFORMS:
        # A REST-API account has no browser terminal to log in to here
        # (VELOTRADE-API-EXEC): refuse before Chromium, never half-drive it.
        print(f"feasibility: api_platform ({platform} is driven over REST; "
              "use the velotrade-api-roundtrip action, not the browser check)")
        return EXIT_FEASIBILITY

    adapter = adapter_for_platform(platform)

    if args.symbols is None:
        symbols = list(DEFAULT_INSTRUMENT_SYMBOLS)
    elif args.symbols == "":
        symbols = []
    else:
        symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        print(f"environment: playwright is not importable ({exc})")
        return EXIT_ENV

    rc = EXIT_OK
    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch(headless=True)
        except Exception as exc:  # missing browser build or system libraries
            print(_redact(f"environment: chromium failed to launch ({type(exc).__name__}: "
                          f"{(str(exc).splitlines() or [''])[0]})", username, password, limit=400))
            return EXIT_ENV
        try:
            if hasattr(adapter, "timeout_ms"):
                adapter.timeout_ms = args.timeout_s * 1000

            def open_page(state: Optional[Dict[str, Any]]):
                ctx = browser.new_context(storage_state=state) if state else browser.new_context()
                pg = ctx.new_page()
                # Passive: attached BEFORE login so it also sees whatever the
                # login/account flow itself triggers. Never clicks, fills or
                # navigates anything.
                cap = []
                if symbols and hasattr(adapter, "start_response_capture"):
                    try:
                        cap = adapter.start_response_capture(pg)
                    except Exception as exc:
                        print(_redact(f"instruments: capture ERROR ({type(exc).__name__}: {exc})",
                                      username, password, limit=300))
                return ctx, pg, cap

            saved = load_storage_state(args.storage_state) if args.storage_state else None
            reused = False
            context = page = None
            captured_responses = []
            if saved is not None and hasattr(adapter, "resume_session"):
                try:
                    context, page, captured_responses = open_page(saved)
                except Exception as exc:
                    print(f"session: saved state rejected by the browser ({type(exc).__name__}); deleted", flush=True)
                    discard_storage_state(args.storage_state)
                    context = None
                if context is not None:
                    try:
                        st = adapter.resume_session(page, cfg["login_url"])
                    except FeasibilityError as fe:
                        # A challenge on the saved session: stop, never answer
                        # it with a credential login.
                        discard_storage_state(args.storage_state)
                        print(_redact(f"feasibility: {fe.reason}" + (f" ({fe.detail})" if fe.detail else ""),
                                      username, password))
                        return EXIT_FEASIBILITY
                    except Exception as exc:
                        st = f"error {type(exc).__name__}"
                    if st == "logged_in":
                        reused = True
                        print("session: reused", flush=True)
                    else:
                        print(f"session: saved state not accepted ({st}); deleted, logging in", flush=True)
                        discard_storage_state(args.storage_state)
                        try:
                            context.close()
                        except Exception:
                            pass
                        context = None
            if context is None:
                context, page, captured_responses = open_page(None)
            try:
                if not reused:
                    if args.storage_state:
                        # Printed BEFORE the attempt so the feed counts every
                        # credential submission, including rejected or
                        # timed-out ones (its per-day relogin ceiling).
                        print("session: login_attempt", flush=True)
                    adapter.login(page, cfg["login_url"], username, password)
                    print("session: relogin" if args.storage_state else "session: fresh", flush=True)
            except FeasibilityError as fe:
                print(_redact(f"feasibility: {fe.reason}" + (f" ({fe.detail})" if fe.detail else ""),
                              username, password))
                # Where did we land? Only for an unrecognised page, never to
                # probe a challenge further.
                if fe.reason in ("unknown_page", "timeout") and hasattr(adapter, "page_shape"):
                    try:
                        for line in adapter.page_shape(page, (username, password)):
                            print(_redact(line, username, password))
                    except Exception as exc:
                        print(_redact(f"page_shape: FAILED ({type(exc).__name__}: {exc})",
                                      username, password, limit=300))
                return EXIT_FEASIBILITY
            except Exception as exc:
                # Playwright errors can echo call logs; redact before printing.
                print(_redact(f"login: ERROR ({type(exc).__name__}: {exc})",
                              username, password, limit=600))
                return EXIT_ERROR
            print("login: ok")
            if args.storage_state:
                try:
                    save_storage_state(context, args.storage_state)
                except Exception as exc:
                    # Not fatal: the next tick simply logs in again.
                    print(f"session: state NOT saved ({type(exc).__name__})", flush=True)
            try:
                print(f"landed: {_redact(page.url, username, password)}")
            except Exception:
                pass
            page.wait_for_timeout(5_000)  # let the terminal populate its panels
            if hasattr(adapter, "wait_ready"):
                ready = adapter.wait_ready(page, timeout_ms=20_000)
                print(f"ready: {'balance/equity visible' if ready else 'NOT within 25s'}")

            snap = adapter.read_account(page).as_dict()
            print("account: " + json.dumps({k: v for k, v in snap.items() if k != "unparsed"}))
            if snap.get("unparsed"):
                print(f"account_unparsed: {', '.join(snap['unparsed'])}")
            if snap.get("balance") is None or snap.get("equity") is None:
                rc = EXIT_UNPARSED

            # Layout canary (TRADEIFY-GOLIVE, manager-approved 2026-10-01):
            # tradeify_1's "My Trading Account" lost its Watchlist between
            # 16:33Z and 20:47Z for a cause nobody could see, and every opener
            # silently had nothing to act on. Click-free; never affects rc.
            if args.layout_canary and hasattr(adapter, "watchlist_symbols"):
                print(layout_watchlist_line(adapter, page), flush=True)

            # None = the read raised (UNPARSED below) = "we did not look";
            # kept distinct from `[]` all the way into the posted report
            # (build_status_report / --emit-status), not just this printout.
            read_rows: Dict[str, Optional[list]] = {"positions": None, "orders": None}
            for label, reader in (("positions", adapter.read_positions),
                                  ("orders", adapter.read_orders)):
                try:
                    items = [i.as_dict() for i in reader(page)]
                    read_rows[label] = items
                    print(f"{label}: {len(items)}")
                    for it in items:
                        it.pop("raw", None)
                        print(_redact(f"  {json.dumps(it)}", username, password))
                except LookupError as le:
                    print(_redact(f"{label}: UNPARSED ({le})", username, password))
                    rc = EXIT_UNPARSED

            if args.dump_tables and hasattr(adapter, "dump_tables"):
                try:
                    for line in adapter.dump_tables(page, (username, password)):
                        print(_redact(line, username, password))
                except Exception as exc:
                    print(_redact(f"dump_tables: FAILED ({type(exc).__name__}: {exc})",
                                  username, password, limit=300))

            if rc == EXIT_UNPARSED and hasattr(adapter, "structure"):
                try:
                    for line in adapter.structure(page, (username, password)):
                        print(_redact(line, username, password))
                except Exception as exc:
                    print(_redact(f"structure: FAILED ({type(exc).__name__}: {exc})",
                                  username, password, limit=300))

            if args.dump_dir:
                d = Path(args.dump_dir)
                d.mkdir(parents=True, exist_ok=True)
                body = _redact(page.inner_text("body"), username, password)
                (d / "page_text.txt").write_text(body)
                from src.prop.platform.dxtrade import EXTRACT_TABLES_JS
                (d / "tables.json").write_text(_redact(
                    json.dumps(page.evaluate(EXTRACT_TABLES_JS), indent=1), username, password))
                print(f"dump: written to {d} (on the VM only)")

            # Instrument specs, read from the responses captured above.
            # NEVER affects rc: a wrong number would be worse than none,
            # and this is a best-effort, unmeasured secondary read.
            if symbols:
                if hasattr(adapter, "start_response_capture"):
                    from src.prop.platform.dxtrade import extract_instrument_specs_from_responses
                    try:
                        result = extract_instrument_specs_from_responses(
                            captured_responses, symbols, secrets=(username, password))
                    except Exception as exc:
                        print(_redact(f"instruments: ERROR ({type(exc).__name__}: {exc})",
                                      username, password, limit=400))
                        result = {"specs": {}, "discovery": []}
                    for sym in symbols:
                        fields = result["specs"].get(sym, {})
                        print(_redact(f"instrument: {json.dumps({'symbol': sym, **fields})}",
                                      username, password))
                    for line in result["discovery"]:
                        print(_redact(line, username, password))
                else:
                    print("instruments: SKIPPED (adapter has no start_response_capture)")

            if args.emit_status:
                report = build_status_report(
                    args.account, snap,
                    positions=read_rows["positions"], orders=read_rows["orders"])
                if report is None:
                    print("emit_status: SKIPPED (balance and equity both unread)")
                else:
                    try:
                        res = post_status(report, args.api_base)
                        print(f"emit_status: ok id={res.get('id')}")
                    except Exception as exc:
                        print(_redact(f"emit_status: FAILED ({type(exc).__name__}: {exc})", username, password))
                        rc = rc or EXIT_ERROR
            else:
                print("emit_status: off (default)")
        except Exception as exc:
            print(_redact(f"read: ERROR ({type(exc).__name__}: {exc})", username, password, limit=600))
            return EXIT_ERROR
        finally:
            browser.close()
    return rc


if __name__ == "__main__":
    sys.exit(main())
