#!/usr/bin/env python3
# wiring: manual (analysis-time) + scripts/ci/run_guards.py
#         (research-tooling-selftests, --self-test only).
"""E18 — for every live leg, intents produced vs orders/tickets received.

Live caller for the pure contract in ``src/runtime/leg_flow_detector.py``
(read that module's docstring first — it has the full rationale, the exact
state contract, and why the two source tables are what they are). This
script does the I/O: enumerate live legs from ``/api/bot/config`` +
``/api/bot/strategies``, pull ``signals`` (intents) per strategy and
``trades`` / ``/api/bot/prop/tickets`` (received) once each, and grade every
leg through :func:`leg_flow_detector.assess_leg`.

Transport: ``/api/bot/*`` is public (browser-direct via CORS, per
``docs/CLAUDE-RULES-CANONICAL.md`` § VM topology) and is fetched with plain
``curl``, mirroring ``scripts/ops/strategy_liveness.py``. ``/api/diag/*``
needs the bearer and goes through ``scripts/ops/diag_fetch.sh`` (Transport A —
direct HTTPS; falls back to the GitHub-issue relay only when that script
itself signals it cannot reach the API, which this script surfaces as
``unreadable`` for the affected source rather than crashing — the exact
condition :data:`leg_flow_detector.LEG_UNREADABLE` exists to make sayable).

Usage:
    python3 scripts/ops/leg_flow_report.py [--window-hours 24] [--json]
    python3 scripts/ops/leg_flow_report.py --self-test

Exit codes:
  0  ran; no leg graded `starved`
  1  ran; at least one leg graded `starved` — THE FINDING
  2  could not enumerate live legs at all (config/strategies unreadable) —
     we did not look, distinct from "nothing was starved"
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT))

from scripts.ops import leg_flow_detector as lfd  # noqa: E402

_BOT_BASE = os.environ.get("BOT_API_URL", "https://ict-bot.duckdns.org").rstrip("/")
_DIAG_FETCH = _REPO_ROOT / "scripts" / "ops" / "diag_fetch.sh"

#: Bound on paginated pulls, so a busy window cannot make this script hang or
#: hammer the API. A window that exceeds this is reported PARTIALLY covered
#: (`*_window_fully_covered: false`), never silently truncated without saying
#: so — the population-must-be-stated rule applies to this script's own reads
#: as much as to the leg counts it produces.
_MAX_PAGES = 5
_PAGE_SIZE = 1000


def _curl_json(url: str, *, timeout: int = 30) -> Optional[Any]:
    try:
        p = subprocess.run(
            ["curl", "-sS", "--max-time", str(timeout), url],
            capture_output=True, text=True, timeout=timeout + 10,
        )
        if p.returncode != 0 or not p.stdout.strip():
            return None
        return json.loads(p.stdout)
    except Exception:  # noqa: BLE001 — unreachable API is `None`, i.e. "we did not look"
        return None


def _diag_fetch(path: str, *, timeout: int = 30) -> Optional[Any]:
    try:
        p = subprocess.run(
            ["bash", str(_DIAG_FETCH), path],
            capture_output=True, text=True, timeout=timeout + 10,
        )
        if p.returncode != 0 or not p.stdout.strip():
            return None
        return json.loads(p.stdout)
    except Exception:  # noqa: BLE001 — same contract as `_curl_json`
        return None


def fetch_live_legs() -> Optional[List[Dict[str, Any]]]:
    cfg = _curl_json(f"{_BOT_BASE}/api/bot/config")
    strat = _curl_json(f"{_BOT_BASE}/api/bot/strategies")
    if cfg is None or strat is None:
        return None
    return lfd.enumerate_live_legs(cfg, strat)


def fetch_signal_rows(strategy: str, *, since_iso: str) -> Optional[List[Dict[str, Any]]]:
    """All `{strategy}_eval` rows since `since_iso`, paginated up to
    `_MAX_PAGES`. Returns `None` if the FIRST page could not be read at all
    (we never looked); returns whatever was accumulated if a LATER page
    fails or the page cap is hit (we looked, partially — the leg's `intents`
    count is then a lower bound, and `count_intents` still returns a real
    number rather than `None`, because we DID observe real rows).
    """
    event = f"{strategy}_eval"
    rows: List[Dict[str, Any]] = []
    for page in range(_MAX_PAGES):
        offset = page * _PAGE_SIZE
        doc = _diag_fetch(
            f"audit_query?strategy={strategy}&event={event}&since={since_iso}"
            f"&limit={_PAGE_SIZE}&offset={offset}"
        )
        if doc is None:
            return rows if rows else None
        page_rows = doc.get("rows") or []
        rows.extend(page_rows)
        if len(page_rows) < _PAGE_SIZE:
            break  # exhausted — the window is fully covered
    return rows


def fetch_trades_all() -> Optional[List[Dict[str, Any]]]:
    """Every `trades` row this script is willing to page for (up to
    `_MAX_PAGES` * `_PAGE_SIZE`), unfiltered. Returns the raw rows so the
    caller can BOTH window them for the received-count AND scan them
    unwindowed for an open position that predates the window (see
    `has_open_position` in `build_report` — a leg holding a position opened
    before the window will legitimately keep re-signalling "stay in this
    position" with zero NEW orders in-window, which is NOT starvation and
    was MEASURED live 2026-09-25 on `alpaca_paper/tlt_pullback_1d` and
    `alpaca_paper/gld_pullback_1h`, each holding since before the window with
    zero window trades).
    """
    rows: List[Dict[str, Any]] = []
    for page in range(_MAX_PAGES):
        offset = page * _PAGE_SIZE
        doc = _diag_fetch(f"journal?table=trades&limit={_PAGE_SIZE}&offset={offset}")
        if doc is None:
            return rows if rows else None
        page_rows = doc if isinstance(doc, list) else (doc.get("rows") or [])
        if not page_rows:
            break
        rows.extend(page_rows)
        if len(page_rows) < _PAGE_SIZE:
            break
    return rows


def _window_filter(rows: Sequence[Dict[str, Any]], *, since_iso: str) -> List[Dict[str, Any]]:
    return [r for r in rows
            if str((r or {}).get("created_at") or (r or {}).get("timestamp") or "") >= since_iso]


def fetch_prop_tickets(account_id: str, *, since_iso: str) -> Optional[List[Dict[str, Any]]]:
    """Tickets for `account_id` newer than `since_iso`.

    ⚠️ THE WINDOW FILTER IS APPLIED HERE, CLIENT-SIDE — the route takes no
    `since` param, so an unfiltered pull would compare "intents in the last
    N hours" against "tickets ever" and could report `flowing` (or mask a
    fresh `starved`) off a ticket that is weeks old. `limit=500` bounds the
    pull; a leg whose true window population exceeds 500 rows is the kind of
    activity this script's other legs never see, so the cap is not scoped
    tighter than that.
    """
    doc = _curl_json(f"{_BOT_BASE}/api/bot/prop/tickets?account_id={account_id}&limit=500")
    if doc is None:
        return None
    rows = doc.get("tickets") or []
    return [r for r in rows if str((r or {}).get("created_at") or "") >= since_iso]


def build_report(*, window_hours: int) -> Dict[str, Any]:
    now = datetime.now(timezone.utc)
    since_iso = (now - timedelta(hours=window_hours)).strftime("%Y-%m-%dT%H:%M:%SZ")

    legs = fetch_live_legs()
    if legs is None:
        return {"read_state": "legs_unreadable", "legs": [],
                "why": "could not read /api/bot/config or /api/bot/strategies "
                       "— we did not look, not 'no live legs'"}

    strategies = sorted({leg["strategy"] for leg in legs})
    signal_cache: Dict[str, Optional[List[Dict[str, Any]]]] = {
        s: fetch_signal_rows(s, since_iso=since_iso) for s in strategies
    }

    all_trade_rows = fetch_trades_all()
    trade_rows = _window_filter(all_trade_rows, since_iso=since_iso) if all_trade_rows is not None else None
    prop_accounts = sorted({leg["account_id"] for leg in legs if leg["is_prop"]})
    prop_cache: Dict[str, Optional[List[Dict[str, Any]]]] = {
        a: fetch_prop_tickets(a, since_iso=since_iso) for a in prop_accounts
    }

    out_legs: List[Dict[str, Any]] = []
    for leg in legs:
        strat, acct, is_prop = leg["strategy"], leg["account_id"], leg["is_prop"]
        sig_rows = signal_cache.get(strat)
        intents = lfd.count_intents(sig_rows)
        episodes = lfd.count_intent_episodes(sig_rows)
        if is_prop:
            recv = lfd.count_received_prop(prop_cache.get(acct), strategy=strat)
        else:
            recv = lfd.count_received_standard(trade_rows, account_id=acct, strategy=strat)
        received, held_back = (recv if recv is not None else (None, None))
        verdict = lfd.assess_leg(
            intents=intents, received=received,
            intent_episodes=episodes, held_back=held_back,
        )
        # CONTEXT ONLY — never feeds the state calc, same contract as
        # `held_back`. A leg graded `starved` while ALREADY HOLDING a
        # position opened before the window is very likely "still holding",
        # not "unreached"; a leg graded `starved` with NO open position and
        # no held_back is the cleanest reading of the E18 finding shape.
        has_open_position = None
        if not is_prop and all_trade_rows is not None:
            has_open_position = any(
                (r or {}).get("account_id") == acct
                and (r or {}).get("strategy_name") == strat
                and (r or {}).get("status") == "open"
                for r in all_trade_rows
            )
        out_legs.append({
            "strategy": strat, "account_id": acct,
            "account_class": leg["account_class"],
            "has_open_position": has_open_position,
            **verdict,
        })

    return {
        "read_state": "measured",
        "window_hours": window_hours,
        "since_utc": since_iso,
        "as_of_utc": now.isoformat(),
        "legs": out_legs,
    }


def _print_table(report: Dict[str, Any]) -> None:
    if report["read_state"] != "measured":
        print(f"leg_flow_report: {report['read_state']} — {report['why']}")
        return
    print(f"population: {len(report['legs'])} live leg(s), "
          f"window {report['since_utc']} -> {report['as_of_utc']} "
          f"({report['window_hours']}h)\n")
    by_state: Dict[str, int] = {}
    for leg in report["legs"]:
        by_state[leg["state"]] = by_state.get(leg["state"], 0) + 1
        marker = {
            lfd.LEG_STARVED: "\U0001F534",
            lfd.LEG_UNREADABLE: "❓",
            lfd.LEG_NO_INTENTS: "⚪",
            lfd.LEG_FLOWING: "\U0001F7E2",
        }.get(leg["state"], "?")
        print(f"  {marker} {leg['state']:<11} {leg['account_id']:<20} {leg['strategy']:<35} "
              f"intents={leg['intents']!s:<6} received={leg['received']!s:<6} "
              f"episodes={leg['intent_episodes']!s:<4} held_back={leg['held_back']!s}")
    print("\nby state:", by_state)
    starved = [leg for leg in report["legs"] if leg["state"] == lfd.LEG_STARVED]
    if starved:
        print(f"\n{len(starved)} STARVED leg(s) — intents produced, zero received:")
        for leg in starved:
            caveat = ""
            if leg.get("has_open_position"):
                caveat = "  [HOLDING a position opened before the window — likely NOT starvation]"
            elif leg.get("held_back"):
                caveat = "  [held_back>0 — likely BLOCKED (refused/suppressed), not UNREACHED]"
            print(f"  - {leg['account_id']}/{leg['strategy']}: "
                  f"{leg['intents']} intent(s) ({leg['intent_episodes']} episode(s)), "
                  f"0 received, {leg['held_back']} held back{caveat}")


def _self_test() -> int:
    """Offline. Proves all four states are reachable, with a positive
    control for the finding (`starved`) and for the collapse this module
    exists to prevent (`unreadable` staying apart from `starved`)."""
    ok = True

    def ck(label: str, cond: bool) -> None:
        nonlocal ok
        ok &= cond
        print(f"  self-test ({label}): {'PASS' if cond else 'FAIL'}")

    # positive control: unreadable, NEVER starved, on a None source.
    v = lfd.assess_leg(intents=None, received=0)
    ck("unreadable when intents source unreadable", v["state"] == lfd.LEG_UNREADABLE)
    v = lfd.assess_leg(intents=5, received=None)
    ck("unreadable when received source unreadable", v["state"] == lfd.LEG_UNREADABLE)

    # positive control: no_intents, distinct from starved.
    v = lfd.assess_leg(intents=0, received=0)
    ck("no_intents when nothing was produced", v["state"] == lfd.LEG_NO_INTENTS)

    # positive control: the finding.
    v = lfd.assess_leg(intents=21, received=0, held_back=0)
    ck("starved: THE breakout_1 shape (21 produced, 0 received)",
       v["state"] == lfd.LEG_STARVED)

    # a refusal alone does not soften starved (held_back is context only).
    v = lfd.assess_leg(intents=3, received=0, held_back=3)
    ck("starved even when every intent was held_back (context, not state)",
       v["state"] == lfd.LEG_STARVED)

    v = lfd.assess_leg(intents=4, received=2)
    ck("flowing when at least one order/ticket was received",
       v["state"] == lfd.LEG_FLOWING)

    # count_intents / episodes: side vocabulary independence (the exact
    # undercounting risk a hardcoded long/short check would reintroduce).
    rows = [{"side": "none"}, {"side": "sell", "logged_at_utc": "2026-01-01T00:00:00Z"},
            {"side": "sell", "logged_at_utc": "2026-01-01T00:02:00Z"},
            {"side": "none", "logged_at_utc": "2026-01-01T00:04:00Z"},
            {"side": "sell", "logged_at_utc": "2026-01-01T00:06:00Z"}]
    ck("count_intents reads a non long/short actionable token ('sell')",
       lfd.count_intents(rows) == 3)
    ck("count_intent_episodes debounces the contiguous run",
       lfd.count_intent_episodes(rows) == 2)
    ck("count_intents(None) is None, never 0",
       lfd.count_intents(None) is None)

    # prop vs standard received sources stay separate and context-tagged.
    recv, held = lfd.count_received_prop(
        [{"strategy": "x", "status": "shadow"}, {"strategy": "x", "status": "filled"},
         {"strategy": "x", "status": "suppressed"}, {"strategy": "y", "status": "filled"}],
        strategy="x",
    )
    ck("count_received_prop: shadow/suppressed held back, not received",
       recv == 1 and held == 2)

    placed, refused = lfd.count_received_standard(
        [{"account_id": "a", "strategy_name": "s", "status": "open"},
         {"account_id": "a", "strategy_name": "s", "status": "rejected"},
         {"account_id": "b", "strategy_name": "s", "status": "closed"}],
        account_id="a", strategy="s",
    )
    ck("count_received_standard: placed vs refused, other-account excluded",
       placed == 1 and refused == 1)

    print("leg_flow_report self-test:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--window-hours", type=int, default=24)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)

    if a.self_test:
        return _self_test()

    report = build_report(window_hours=a.window_hours)
    if a.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        _print_table(report)

    if report["read_state"] != "measured":
        return 2
    if any(leg["state"] == lfd.LEG_STARVED for leg in report["legs"]):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
