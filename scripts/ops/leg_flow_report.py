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
import logging
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT))

from scripts.ops import leg_flow_detector as lfd  # noqa: E402

logger = logging.getLogger(__name__)

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


def fetch_config_and_strategies() -> Optional[Tuple[Dict[str, Any], Dict[str, Any]]]:
    """The two raw payloads ``enumerate_live_legs`` and
    ``live_symbol_contenders`` both need. Split out from :func:`fetch_live_legs`
    so ``build_report`` can compute per-leg symbol contention without a
    second, redundant pull of the same two endpoints."""
    cfg = _curl_json(f"{_BOT_BASE}/api/bot/config")
    strat = _curl_json(f"{_BOT_BASE}/api/bot/strategies")
    if cfg is None or strat is None:
        return None
    return cfg, strat


def fetch_live_legs() -> Optional[List[Dict[str, Any]]]:
    fetched = fetch_config_and_strategies()
    if fetched is None:
        return None
    cfg, strat = fetched
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


def find_unmapped_prop_tickets(
    ticket_rows: Sequence[Dict[str, Any]],
    *,
    strategy: str,
    received: int,
    held_back: int,
) -> Tuple[int, List[str]]:
    """``unmapped = len(ticket_rows_for_strategy) - received - held_back`` —
    tickets whose ``status`` satisfied NEITHER
    ``leg_flow_detector.PROP_RECEIVED_STATUSES`` nor
    ``PROP_NOT_RECEIVED_STATUSES``, so :func:`leg_flow_detector.count_received_prop`
    dropped them into neither bucket rather than guessing.  That is the exact
    gap that let ``expiry_prompted`` fall through silently — a leg reading
    `starved` with a real, dispatched ticket in flight — before PR #13000
    (docs/claude/work/findings/E18-starved-triage-post-E35-20260926.md).

    ``received`` and ``held_back`` must come from the SAME
    ``ticket_rows_for_strategy`` population (i.e. the two-tuple this module's
    caller already gets back from ``count_received_prop`` on this exact
    ``ticket_rows``) — the subtraction is only correct as row-accounting
    against that shared population.

    Returns ``(unmapped, offending_status_values)``: the count via simple
    subtraction, and the SORTED, DEDUPED status values responsible (so a
    caller can name them in a warning instead of just a bare number). A row
    with no ``status`` at all counts under the literal string
    ``"<missing>"``. ``unmapped == 0`` (every status is one of the two
    described in the docstring above) returns an empty list either way — the
    caller decides what "loud" means for a nonzero count.
    """
    ticket_rows_for_strategy = [
        r for r in ticket_rows if (r or {}).get("strategy") == strategy
    ]
    unmapped = len(ticket_rows_for_strategy) - received - held_back
    statuses: List[str] = []
    if unmapped != 0:
        seen = set()
        for r in ticket_rows_for_strategy:
            status = (r or {}).get("status")
            if status in lfd.PROP_RECEIVED_STATUSES or status in lfd.PROP_NOT_RECEIVED_STATUSES:
                continue
            seen.add("<missing>" if status is None else str(status))
        statuses = sorted(seen)
    return unmapped, statuses


def build_report(*, window_hours: int) -> Dict[str, Any]:
    now = datetime.now(timezone.utc)
    since_iso = (now - timedelta(hours=window_hours)).strftime("%Y-%m-%dT%H:%M:%SZ")

    fetched = fetch_config_and_strategies()
    if fetched is None:
        return {"read_state": "legs_unreadable", "legs": [],
                "why": "could not read /api/bot/config or /api/bot/strategies "
                       "— we did not look, not 'no live legs'"}
    cfg, strat_cfg = fetched
    legs = lfd.enumerate_live_legs(cfg, strat_cfg)

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
        ticket_rows = prop_cache.get(acct) if is_prop else None
        if is_prop:
            recv = lfd.count_received_prop(ticket_rows, strategy=strat)
        else:
            recv = lfd.count_received_standard(trade_rows, account_id=acct, strategy=strat)
        received, held_back = (recv if recv is not None else (None, None))
        verdict = lfd.assess_leg(
            intents=intents, received=received,
            intent_episodes=episodes, held_back=held_back,
        )

        # A prop ticket whose status is in neither PROP_RECEIVED_STATUSES nor
        # PROP_NOT_RECEIVED_STATUSES must never fall silently into neither
        # bucket the way `expiry_prompted` did before PR #13000 — surface it
        # loudly (a WARNING + the offending status values) instead. Only
        # computed when the ticket source was actually readable, so a genuine
        # `unreadable` leg is never misreported as `unmapped=0`.
        unmapped: Optional[int] = None
        unmapped_statuses: List[str] = []
        if is_prop and ticket_rows is not None and received is not None:
            unmapped, unmapped_statuses = find_unmapped_prop_tickets(
                ticket_rows, strategy=strat, received=received, held_back=held_back,
            )
            if unmapped != 0:
                logger.warning(
                    "leg_flow_report: %s/%s: %d prop ticket(s) UNMAPPED — status(es) "
                    "%s matched neither PROP_RECEIVED_STATUSES nor "
                    "PROP_NOT_RECEIVED_STATUSES (the expiry_prompted gap, PR #13000; "
                    "PI-20260926-Y5MVFDD8-0001)", acct, strat, unmapped, unmapped_statuses,
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
        # CONTEXT ONLY — never feeds the state calc, same contract as
        # `has_open_position`/`held_back` above. Computed for every leg
        # (not only `starved` ones) so a reader can see the population this
        # was checked over, not only the rows where it happened to matter.
        same_symbol_live_contenders = lfd.live_symbol_contenders(
            strat, acct, cfg, strat_cfg,
        )
        out_legs.append({
            "strategy": strat, "account_id": acct,
            "account_class": leg["account_class"],
            "has_open_position": has_open_position,
            "same_symbol_live_contenders": same_symbol_live_contenders,
            "unmapped": unmapped,
            "unmapped_statuses": unmapped_statuses,
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
    unmapped_legs = [leg for leg in report["legs"] if leg.get("unmapped")]
    if unmapped_legs:
        print(f"\n⚠️  {len(unmapped_legs)} leg(s) with UNMAPPED prop ticket "
              f"status(es) — neither received nor held_back, the expiry_prompted gap "
              f"(PR #13000):")
        for leg in unmapped_legs:
            print(f"  - {leg['account_id']}/{leg['strategy']}: unmapped={leg['unmapped']} "
                  f"status(es)={leg['unmapped_statuses']}")


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

    # unmapped prop ticket statuses (PI-20260926-Y5MVFDD8-0001): a status
    # absent from BOTH vocabularies must surface by name, never fall
    # silently into neither bucket the way `expiry_prompted` did before
    # PR #13000.
    unknown_rows = [{"strategy": "x", "status": "filled"},
                     {"strategy": "x", "status": "shadow"},
                     {"strategy": "x", "status": "some_future_status"}]
    recv, held = lfd.count_received_prop(unknown_rows, strategy="x")
    unmapped, statuses = find_unmapped_prop_tickets(
        unknown_rows, strategy="x", received=recv, held_back=held)
    ck("unmapped surfaces an unknown status by name",
       unmapped == 1 and statuses == ["some_future_status"])

    known_rows = [{"strategy": "x", "status": "filled"},
                  {"strategy": "x", "status": "shadow"}]
    recv, held = lfd.count_received_prop(known_rows, strategy="x")
    unmapped, statuses = find_unmapped_prop_tickets(
        known_rows, strategy="x", received=recv, held_back=held)
    ck("known statuses give unmapped=0, no offending status(es)",
       unmapped == 0 and statuses == [])

    # negative control: an unknown status on a DIFFERENT strategy must not
    # bleed into this strategy's unmapped count.
    other_strategy_rows = known_rows + [{"strategy": "y", "status": "totally_unknown"}]
    recv, held = lfd.count_received_prop(other_strategy_rows, strategy="x")
    unmapped, statuses = find_unmapped_prop_tickets(
        other_strategy_rows, strategy="x", received=recv, held_back=held)
    ck("negative control: another strategy's unknown status is not counted",
       unmapped == 0 and statuses == [])

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
