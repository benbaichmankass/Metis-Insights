#!/usr/bin/env python3
"""One prop-executor cycle against a prop account's web terminal (step 3).

Spec: ``docs/research/prop-automation-options-2026-09-27.md`` § 3, § 4 step 3.
Logic: ``src/prop/prop_executor.py::run_cycle``. Platform: the adapter
``config/prop_platforms.yaml`` names (dxtrade for breakout_1).

Modes (exactly one; default = one scheduled cycle):

- *(default)* one cycle in ``PROP_EXECUTOR_MODE`` (off | read_only | live,
  default read_only). Run by ``scripts/ops/prop_executor_tick.sh`` (unit
  ``ict-prop-executor``, shipped NOT enabled).
- ``--dry-run``: forces ``read_only`` and, when a real emitted ticket passes
  every guard, also opens the order form, types it and reads it back
  (``place_bracket(arm=False)``), then closes it. Nothing is submitted and
  nothing is written to the API.
- ``--probe-ticket SYMBOL``: READ-ONLY feasibility measurement of the order
  ticket (is it DOM or canvas?). Opens the form, records its shape and the
  one-click toggle's reading (a diagnostic: nothing gates on it, operator
  2026-09-28), closes it. Types nothing.
- ``--instrument-probe SYMBOLS`` (comma-separated venue symbols, e.g.
  ``BTCUSD,ADAUSD,AVAXUSD,XRPUSD``): READ-ONLY, one at a time — searches the
  watchlist/instrument search field for each symbol and dumps whatever
  surfaces (digit-run-masked), then resets the field before the next symbol.
  Never opens the order ticket, never touches BUY/SELL/submit/the chart. See
  ``DXtradeAdapter.probe_instrument_details``'s docstring for why this stops
  at a structure DUMP rather than parsing named fields. Always exits
  ``EXIT_OK`` (like the passive instrument-spec read, a probe result never
  gates the exit code) unless the session/environment itself fails.
- ``--instrument-search-dump``: READ-ONLY measurement (PROP-ETH-DOM,
  2026-09-30) of where the symbol search/add control sits: every visible
  input / combobox / searchbox / textbox / contenteditable / search-like
  button, with its attributes and ancestor chain, nearest the watchlist
  first (``DXtradeAdapter.instrument_search_dump``). Types, clicks and reads
  no value. Always ``EXIT_OK`` unless the session/environment fails.
- ``--round-trip VENUE [--lots N] [--side long|short] [--live]``: the
  end-to-end test (operator 2026-09-28): ONE minimum-size market bracket with
  SL+TP → confirm by re-read → report ``open`` → the bot closes it at market →
  confirm flat → report ``closed``. Without ``--live`` it is a dry walk that
  clicks nothing. ``--live`` is refused unless ``PROP_EXECUTOR_MODE=live``.
- ``--watched-click``: the watched step-3 test. ``live`` for ONE cycle, at
  most one ticket, at the per-symbol minimum size in
  ``executor.watched_click_max_lots``. Refused unless ``PROP_EXECUTOR_MODE=live``.

Session (``--login``):
- ``reuse`` (the scheduled unit): open the terminal on the saved session at
  ``--storage-state`` (the FEED's file) and NEVER type credentials. If that
  session is not accepted the cycle exits 6 without logging in: credential
  logins stay with the feed, which owns the relogin ceiling. The caller holds
  the shared ``login.lock`` so the two never drive the terminal at once.
- ``fresh``: a credential login, like ``breakout-login-check``. Nothing
  dispatches it by default: the served client carries a "You have logged in
  somewhere else" force-logout, so a second login could end the feed's (or
  the operator's) session. For a manual run only.

Prints JSON lines, redacted like the login check (no credentials, cookies,
URL paths or tokens). Exit: 0 ok · 3 read did not parse / halted · 4
feasibility stop (incl. ``feasibility: canvas_ticket``) · 5 environment ·
6 no reusable session · 1 other.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.prop import prop_executor as pe  # noqa: E402
from src.prop.platform import FeasibilityError, adapter_for_platform, load_platform_config  # noqa: E402

EXIT_OK, EXIT_ERROR, EXIT_UNPARSED, EXIT_FEASIBILITY, EXIT_ENV, EXIT_NO_SESSION = 0, 1, 3, 4, 5, 6

# Headless viewport. Playwright's default (1280x720) clipped the sidebar
# ticket at y 640 with the submit footer at y 659 (dry runs #13917, #13965);
# the operator's ~2000px-tall browser shows the whole ticket, submit
# included, without a scroll. Match that layout; the adapter's scroll path
# (SUBMIT_JS scroll_step / mark) stays as the fallback when it still clips.
VIEWPORT: Dict[str, int] = {"width": 1920, "height": 1600}


def _redact(text: str, *secrets: str) -> str:
    from src.prop.platform.dxtrade import redact_text
    return redact_text(text, *secrets)


def emit(obj: Dict[str, Any], *secrets: str) -> None:
    print(_redact(json.dumps(obj, default=str), *secrets), flush=True)


def resolve_mode(args: argparse.Namespace, env: Optional[Dict[str, str]] = None) -> str:
    """The mode this run executes in. Pure; tested.

    ⚠️ The three PROBE modes (``probe``, ``instrument_probe``,
    ``instrument_search_dump``) are decided BEFORE ``PROP_EXECUTOR_MODE`` and
    so run even when it reads ``off``. That is a deliberate exception to
    "off — nothing read, nothing clicked" (manager review of #14527,
    2026-09-30): ``off`` is the EXECUTOR's kill switch (no cycle, no ticket,
    no submit, no reconcile), while a probe is a manual, one-shot,
    operator/manager-dispatched measurement that places nothing and writes
    nothing to the API or the executor's state dir. Blocking it under
    ``off`` would block measurement exactly when the executor has been
    reverted. ``probe`` opens and closes the order form but types nothing;
    the other two never reach the order form."""
    base = pe.executor_mode(env)
    if args.probe_ticket:
        return "probe"
    if getattr(args, "instrument_probe", ""):
        return "instrument_probe"
    if getattr(args, "instrument_search_dump", False):
        return "instrument_search_dump"
    if args.dry_run:
        return "read_only"
    # A manual LIVE run (watched click, live round trip) needs the kill switch
    # EXPLICITLY armed: PROP_EXECUTOR_MODE=live. Unset / read_only / a typo
    # (all of which read as read_only) refuse, never click (manager review of
    # #13647, 2026-09-28).
    if args.watched_click:
        return "live" if base == "live" else "not_armed"
    if getattr(args, "round_trip", ""):
        if not getattr(args, "live", False):
            return "round_trip_dry"
        return "round_trip_live" if base == "live" else "not_armed"
    if getattr(args, "close_position", ""):
        if not getattr(args, "live", False):
            return "close_position_dry"
        return "close_position_live" if base == "live" else "not_armed"
    return base


def emit_search_dump(dump: Dict[str, Any], *secrets: str) -> None:
    """Print an ``instrument_search_dump`` result one row per line, FARTHEST
    first, then each frame's summary last: a run log read from its tail keeps
    the nearest rows and the summary if it is cut."""
    for fr in dump.get("frames") or []:
        fr = dict(fr)
        rows = fr.pop("rows", None) or []
        for i in range(len(rows) - 1, -1, -1):
            emit({"search_dump_row": {"frame": fr.get("frame"), "rank": i, **rows[i]}}, *secrets)
        emit({"instrument_search_dump": fr}, *secrets)


def _code_sha() -> str:
    """The commit this tick runs from, so a run log proves WHICH code ran
    (three dry runs on 2026-09-29 could not tell a deploy lag from a wrong
    hypothesis). Read-only; "unknown" when git cannot answer."""
    import subprocess
    try:
        return subprocess.run(["git", "rev-parse", "--short=9", "HEAD"], cwd=str(Path(__file__).resolve().parents[2]),
                              capture_output=True, text=True, timeout=5).stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--account", default="breakout_1")
    ap.add_argument("--api-base", default="http://127.0.0.1:8001")
    ap.add_argument("--timeout-s", type=int, default=45)
    ap.add_argument("--state-dir", default=str(Path.home() / ".cache" / "metis-prop-browser" / "executor"))
    ap.add_argument("--storage-state", default="")
    ap.add_argument("--login", choices=("reuse", "fresh"), default="reuse")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--probe-ticket", default="", metavar="VENUE_SYMBOL")
    g.add_argument("--instrument-probe", default="", metavar="VENUE_SYMBOLS",
                   help="comma-separated venue symbols to search + dump (read-only); see module docstring")
    g.add_argument("--instrument-search-dump", action="store_true",
                   help="read-only dump of the search/add controls near the watchlist; see module docstring")
    g.add_argument("--watched-click", action="store_true")
    g.add_argument("--round-trip", default="", metavar="VENUE_SYMBOL",
                   help="end-to-end test: min-size market bracket, confirm, close at market, confirm flat")
    g.add_argument("--close-position", default="", metavar="VENUE_SYMBOL",
                   help="close the ONE existing position for this symbol through the terminal's Close Position "
                        "flow and journal it (refused unless PROP_EXECUTOR_MODE=live with --live; else a dry locate)")
    ap.add_argument("--lots", type=float, default=None,
                    help="round trip size (default and maximum: executor.watched_click_max_lots)")
    ap.add_argument("--side", choices=("long", "short"), default="long")
    ap.add_argument("--live", action="store_true",
                    help="round trip: actually click (refused unless PROP_EXECUTOR_MODE=live); default is a dry walk")
    ap.add_argument("--ticket-id", default="", help="watched click: act on this ticket only")
    args = ap.parse_args(argv)

    mode = resolve_mode(args)
    cfg_plat = load_platform_config(args.account)
    username = os.environ.get(cfg_plat.get("username_env", ""), "")
    password = os.environ.get(cfg_plat.get("password_env", ""), "")
    secrets = (username, password)
    emit({"executor": "start", "account": args.account, "mode": mode, "login": args.login,
          "env_mode": pe.executor_mode(), "code_sha": _code_sha()})
    if mode == "not_armed":
        emit({"executor": "not_armed", "why": f"a live click needs {pe.MODE_ENV}=live explicitly "
                                              f"(it reads {pe.executor_mode()!r}); nothing clicked"})
        return EXIT_ERROR
    if mode == "off":
        emit({"executor": "off", "why": f"{pe.MODE_ENV}=off — nothing read, nothing clicked"})
        return EXIT_OK
    if args.login == "reuse" and not args.storage_state:
        emit({"session": "none", "why": "--login reuse needs --storage-state (the feed's session file)"})
        return EXIT_NO_SESSION
    if args.login == "fresh" and (not username or not password):
        emit({"feasibility": "no_credentials"})
        return EXIT_FEASIBILITY

    cfg = pe.load_config(args.account)
    adapter = adapter_for_platform(cfg_plat["platform"])
    if hasattr(adapter, "timeout_ms"):
        adapter.timeout_ms = args.timeout_s * 1000
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        emit({"environment": f"playwright not importable ({exc})"})
        return EXIT_ENV

    from scripts.prop.breakout_login_check import save_storage_state

    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch(headless=True)
        except Exception as exc:
            emit({"environment": f"chromium failed to launch ({type(exc).__name__})"}, *secrets)
            return EXIT_ENV
        try:
            if args.login == "reuse":
                # Read-only on the saved file: the executor never deletes or
                # replaces the feed's session because IT could not use it.
                saved = None
                try:
                    got_state = json.loads(Path(args.storage_state).read_text())
                    if isinstance(got_state, dict) and isinstance(got_state.get("cookies"), list):
                        saved = got_state
                except Exception:
                    saved = None
                if saved is None:
                    emit({"session": "none", "why": "no saved session; the feed logs in, the executor does not"})
                    return EXIT_NO_SESSION
                context = browser.new_context(storage_state=saved, viewport=VIEWPORT)
                page = context.new_page()
                try:
                    st = adapter.resume_session(page, cfg_plat["login_url"])
                except FeasibilityError as fe:
                    emit({"feasibility": fe.reason, "detail": fe.detail}, *secrets)
                    return EXIT_FEASIBILITY
                if st != "logged_in":
                    emit({"session": "not_accepted", "state": st,
                          "why": "left for the feed's next tick to log in again"})
                    return EXIT_NO_SESSION
                emit({"session": "reused"})
            else:
                context = browser.new_context(viewport=VIEWPORT)
                page = context.new_page()
                try:
                    adapter.login(page, cfg_plat["login_url"], username, password)
                except FeasibilityError as fe:
                    emit({"feasibility": fe.reason, "detail": fe.detail}, *secrets)
                    return EXIT_FEASIBILITY
                emit({"session": "fresh"})
            page.wait_for_timeout(5_000)
            if hasattr(adapter, "wait_ready"):
                adapter.wait_ready(page, timeout_ms=20_000)

            if mode == "probe":
                got = adapter.probe_order_ticket(page, args.probe_ticket)
                emit({"probe": got}, *secrets)
                surface = got.get("surface")
                if surface == "canvas_ticket":
                    emit({"feasibility": "canvas_ticket"})
                    return EXIT_FEASIBILITY
                return EXIT_OK if surface == "dom" else EXIT_UNPARSED

            if mode == "instrument_probe":
                syms = [s.strip() for s in args.instrument_probe.split(",") if s.strip()]
                # Read-only before/after read of the watchlist's symbol set:
                # did typing into its search box persist anything server-side?
                # (manager review of #14563)
                wl_before = adapter.watchlist_symbols(page)
                for sym in syms:
                    got = adapter.probe_instrument_details(page, sym)
                    emit({"instrument_probe": {"symbol": sym, **got}}, *secrets)
                page.wait_for_timeout(2_000)
                wl_after = adapter.watchlist_symbols(page)
                from src.prop.platform.dxtrade import watchlist_diff
                emit({"watchlist_diff": {"before": wl_before, "after": wl_after,
                                         **watchlist_diff(wl_before, wl_after)}}, *secrets)
                # A probe result never gates the exit code — same doctrine as
                # the passive instrument-spec read in breakout_login_check.py.
                return EXIT_OK

            if mode == "instrument_search_dump":
                emit_search_dump(adapter.instrument_search_dump(page), *secrets)
                return EXIT_OK

            api = pe.LocalApi(args.api_base, os.environ.get("DASHBOARD_API_TOKEN", "").strip())
            state_dir = Path(args.state_dir)
            if mode.startswith("round_trip") or mode.startswith("close_position"):
                if mode.startswith("close_position"):
                    res = pe.run_close_position(
                        adapter=adapter, page=page, api=api, cfg=cfg,
                        ledger=pe.IntentLedger(state_dir / "intent_ledger.jsonl"),
                        venue_symbol=args.close_position, arm=(mode == "close_position_live"),
                        sleep=lambda s: page.wait_for_timeout(int(s * 1000)))
                else:
                    res = pe.run_round_trip(
                        adapter=adapter, page=page, api=api, cfg=cfg,
                        ledger=pe.IntentLedger(state_dir / "intent_ledger.jsonl"),
                        venue_symbol=args.round_trip, side=args.side, lots=args.lots,
                        arm=(mode == "round_trip_live"),
                        sleep=lambda s: page.wait_for_timeout(int(s * 1000)))
                emit({"reads": res.reads}, *secrets)
                for key, items in (("action", res.actions), ("report", res.reports), ("alert", res.alerts)):
                    for it in items:
                        emit({key: it}, *secrets)
                emit({"executor": "done", "mode": res.mode, "halted": res.halted}, *secrets)
                if args.login == "reuse":
                    try:
                        save_storage_state(context, args.storage_state)
                    except Exception as exc:
                        emit({"session": f"state NOT re-saved ({type(exc).__name__})"})
                return EXIT_UNPARSED if res.halted else EXIT_OK
            res = pe.run_cycle(
                adapter=adapter, page=page, api=api, cfg=cfg, mode=mode,
                ledger=pe.IntentLedger(state_dir / "intent_ledger.jsonl"),
                state=pe.ExecutorState(state_dir),
                walk_form=args.dry_run,
                max_lots=cfg.watched_click_max_lots if args.watched_click else None,
                only_ticket_id=args.ticket_id or None,
                sleep=lambda s: page.wait_for_timeout(int(s * 1000)))
            emit({"reads": res.reads}, *secrets)
            for a in res.actions:
                emit({"action": a}, *secrets)
            for r in res.reports:
                emit({"report": r}, *secrets)
            for al in res.alerts:
                emit({"alert": al}, *secrets)
            emit({"executor": "done", "mode": res.mode, "halted": res.halted}, *secrets)
            if args.login == "reuse":
                try:
                    save_storage_state(context, args.storage_state)
                except Exception as exc:
                    emit({"session": f"state NOT re-saved ({type(exc).__name__})"})
            return EXIT_UNPARSED if res.halted else EXIT_OK
        except Exception as exc:
            emit({"error": f"{type(exc).__name__}: {str(exc)[:300]}"}, *secrets)
            if mode in ("live", "read_only"):
                # A raised tick is an executor error; two in a row trip the
                # auto-revert latch (live only), and the alert is pinged.
                trip = pe.record_tick_error(Path(args.state_dir), mode == "live", type(exc).__name__)
                if trip:
                    emit({"alert": trip + " — new entries halted until cleared"}, *secrets)
            return EXIT_ERROR
        finally:
            browser.close()


if __name__ == "__main__":
    sys.exit(main())
