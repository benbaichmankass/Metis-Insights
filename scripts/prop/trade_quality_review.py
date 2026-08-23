#!/usr/bin/env python3
"""P4 — prop trade-quality review, on a cadence rather than by hand.

WHY THIS EXISTS. The operator's two health questions for the prop bridge are
*"do we have the correct strategies routed there"* and *"are the trades we do
place good"* — and they explicitly ruled out the third thing a reviewer reaches
for: **ticket answer-rate is not a metric of success**, because on a manual
bridge the operator is not always at the terminal when a ticket is live. So
this reads only what was actually PLACED.

**THE USEFUL CUT IS PLACED-VS-TICKETED, and it exists to separate two failures
that a win-rate cannot tell apart:**

  * **BRIDGE quality** — did the human place what the bot asked for? Measured as
    entry slippage against the ticketed entry, plus how many fills carry no
    ticket link at all.
  * **STRATEGY quality** — given a faithful placement, was the trade any good?
    Measured as where the exit landed relative to the *ticketed* levels.

A book that stops out constantly with clean entries has a strategy problem. A
book with the same win rate and 200bps of entry slippage has a bridge problem.
Conflating them sends the next session to fix the wrong half — the 2026-08-23
hand review found execution fidelity GOOD and strategy quality WEAK, which is a
conclusion no aggregate PnL number could have produced.

⚠️ **STATES ARE NOT COLLAPSED — "we could not classify" is not "manual exit".**
An exit is graded against its ticket's own levels, so a fill with no ticket
link, or a ticket carrying no SL/TP, is **unclassified** and says so. Bucketing
those as `manual` would manufacture a discretionary-exit rate out of missing
data, and manual exits are precisely the interesting bucket.

⚠️ **THE TOLERANCE MOVES THE ANSWER, so it is reported, not hidden.** "The exit
landed on the stop" is a judgement about closeness, and `--tolerance-bps`
decides it. The output states the value used AND counts how many rows sit
within 2x of the boundary, so a reader can see whether the split is robust or
an artifact of the threshold.

Reads the Tier-1 read surface by default (`/api/bot/prop/fills` +
`/api/bot/prop/tickets`), so a review session can run it without VM access;
`--db` reads the local prop journal instead when run on the VM.

Usage::

    python scripts/prop/trade_quality_review.py
    python scripts/prop/trade_quality_review.py --account breakout_1 --tolerance-bps 15
    python scripts/prop/trade_quality_review.py --db --json
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

DEFAULT_API = "https://ict-bot.duckdns.org"
DEFAULT_ACCOUNT = "breakout_1"
DEFAULT_TOLERANCE_BPS = 15.0

# Exit classifications. The four `unclassified_*` values are deliberately
# distinct from every substantive one: each names a DIFFERENT reason we could
# not grade the row, and none of them means "the operator exited by hand".
AT_STOP = "at_stop"
AT_TARGET = "at_target"
BEYOND_STOP = "beyond_stop"
BEYOND_TARGET = "beyond_target"
MANUAL_IN_PROFIT = "manual_in_profit"
MANUAL_IN_LOSS = "manual_in_loss"
MANUAL_FLAT = "manual_flat"
UNCLASSIFIED_NO_TICKET = "unclassified_no_ticket"
UNCLASSIFIED_NO_LEVELS = "unclassified_no_levels"
UNCLASSIFIED_NO_EXIT = "unclassified_no_exit"

_BRIDGE_STATES = (UNCLASSIFIED_NO_TICKET,)
_UNCLASSIFIED = (UNCLASSIFIED_NO_TICKET, UNCLASSIFIED_NO_LEVELS,
                 UNCLASSIFIED_NO_EXIT)


def _num(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and f not in (float("inf"), float("-inf")) else None


def _bps(actual: float, reference: float) -> Optional[float]:
    if reference == 0:
        return None
    return (actual - reference) / abs(reference) * 10_000.0


# ── data access ───────────────────────────────────────────────────────

def _get_json(url: str, timeout: float = 60.0) -> Dict[str, Any]:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.load(resp)


def load_from_api(base: str, account: str, limit: int) -> Tuple[List[dict], List[dict]]:
    fills = _get_json(
        f"{base}/api/bot/prop/fills?account_id={account}&limit={limit}"
    ).get("fills") or []
    tickets = _get_json(
        f"{base}/api/bot/prop/tickets?account_id={account}&limit={limit}"
    ).get("tickets") or []
    return fills, tickets


def load_from_db(account: str, limit: int) -> Tuple[List[dict], List[dict]]:
    from src.prop import prop_journal
    fills = prop_journal.list_fills(account_id=account, limit=limit)
    tickets = prop_journal.list_outbound_tickets(account_id=account, limit=limit)
    return fills, tickets


# ── classification (pure) ─────────────────────────────────────────────

def resolve_ticket(fill: dict, by_id: Dict[str, dict]) -> Optional[dict]:
    """The fill's ticket, by explicit id first then the canonical matcher.

    Reuses `prop_reconcile.match_fill_to_ticket` rather than re-deriving the
    match, so this review and the reconciler can never disagree about which
    ticket a fill belongs to.
    """
    tid = fill.get("ticket_id")
    if tid and str(tid) in by_id:
        return by_id[str(tid)]
    try:
        from src.prop.prop_reconcile import match_fill_to_ticket
        matched = match_fill_to_ticket(fill)
    except Exception:  # noqa: BLE001 — a matcher failure is not a match
        return None
    return by_id.get(str(matched)) if matched else None


def classify_exit(fill: dict, ticket: Optional[dict],
                  *, tolerance_bps: float) -> Dict[str, Any]:
    """Grade one closed fill against its ticket's declared levels.

    Returns `{state, exit_slip_bps, entry_slip_bps, near_boundary}`.
    `near_boundary` is True when the exit sits within 2x the tolerance of a
    level without being inside it — the rows whose bucket would move if the
    tolerance did.
    """
    out: Dict[str, Any] = {
        "state": UNCLASSIFIED_NO_TICKET,
        "entry_slip_bps": None,
        "exit_slip_bps": None,
        "near_boundary": False,
    }
    exit_px = _num(fill.get("exit_price"))
    pnl = _num(fill.get("pnl"))

    if ticket is None:
        return out

    t_entry, t_sl, t_tp = (_num(ticket.get("entry")), _num(ticket.get("sl")),
                           _num(ticket.get("tp")))
    f_entry = _num(fill.get("entry_price"))
    direction = str(fill.get("direction") or ticket.get("direction") or "").lower()
    is_long = direction in ("long", "buy")

    # Entry fidelity — signed so POSITIVE always means WORSE for the trader.
    if t_entry is not None and f_entry is not None:
        raw = _bps(f_entry, t_entry)
        out["entry_slip_bps"] = None if raw is None else (raw if is_long else -raw)

    if exit_px is None:
        out["state"] = UNCLASSIFIED_NO_EXIT
        return out
    if t_sl is None and t_tp is None:
        out["state"] = UNCLASSIFIED_NO_LEVELS
        return out

    def _within(level: Optional[float], mult: float = 1.0) -> bool:
        if level is None:
            return False
        d = _bps(exit_px, level)
        return d is not None and abs(d) <= tolerance_bps * mult

    if _within(t_sl):
        out["state"] = AT_STOP
        out["exit_slip_bps"] = _bps(exit_px, t_sl)
    elif _within(t_tp):
        out["state"] = AT_TARGET
        out["exit_slip_bps"] = _bps(exit_px, t_tp)
    elif t_sl is not None and ((is_long and exit_px < t_sl) or
                               (not is_long and exit_px > t_sl)):
        # Past the stop — a real outcome (gap / slippage), NOT "at" it.
        out["state"] = BEYOND_STOP
        out["exit_slip_bps"] = _bps(exit_px, t_sl)
    elif t_tp is not None and ((is_long and exit_px > t_tp) or
                               (not is_long and exit_px < t_tp)):
        out["state"] = BEYOND_TARGET
        out["exit_slip_bps"] = _bps(exit_px, t_tp)
    elif pnl is None:
        out["state"] = UNCLASSIFIED_NO_EXIT
    elif pnl > 0:
        out["state"] = MANUAL_IN_PROFIT
    elif pnl < 0:
        out["state"] = MANUAL_IN_LOSS
    else:
        out["state"] = MANUAL_FLAT

    out["near_boundary"] = (
        out["state"] not in (AT_STOP, AT_TARGET)
        and (_within(t_sl, 2.0) or _within(t_tp, 2.0))
    )
    return out


def review(fills: List[dict], tickets: List[dict],
           *, tolerance_bps: float) -> Dict[str, Any]:
    """The whole review. Pure — no I/O, so it is testable against fixtures."""
    by_id = {str(t["ticket_id"]): t for t in tickets if t.get("ticket_id")}
    closed = [f for f in fills if str(f.get("status") or "").lower() == "closed"]

    rows: List[Dict[str, Any]] = []
    for f in closed:
        tk = resolve_ticket(f, by_id)
        g = classify_exit(f, tk, tolerance_bps=tolerance_bps)
        rows.append({
            "id": f.get("id"),
            "symbol": f.get("symbol"),
            "direction": f.get("direction"),
            "strategy": (tk or {}).get("strategy"),
            "pnl": _num(f.get("pnl")),
            "reported_at": f.get("reported_at"),
            "ticket_id": (tk or {}).get("ticket_id"),
            **g,
        })

    by_state: Dict[str, int] = {}
    for r in rows:
        by_state[r["state"]] = by_state.get(r["state"], 0) + 1

    pnls = [r["pnl"] for r in rows if r["pnl"] is not None]
    wins = [p for p in pnls if p > 0]
    slips = [r["entry_slip_bps"] for r in rows if r["entry_slip_bps"] is not None]
    graded = [r for r in rows if r["state"] not in _UNCLASSIFIED]

    def _med(xs: List[float]) -> Optional[float]:
        if not xs:
            return None
        s = sorted(xs)
        n = len(s)
        return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2

    return {
        "tolerance_bps": tolerance_bps,
        "population": {
            "fills_seen": len(fills),
            "closed": len(closed),
            "graded": len(graded),
            "unclassified": len(rows) - len(graded),
            "near_boundary": sum(1 for r in rows if r["near_boundary"]),
        },
        # BRIDGE half — did the human place what the bot asked for?
        "bridge": {
            "entry_slip_measured_n": len(slips),
            "entry_slip_bps_median": _med(slips),
            "entry_slip_bps_worst": max(slips) if slips else None,
            "fills_with_no_ticket_link": by_state.get(UNCLASSIFIED_NO_TICKET, 0),
        },
        # STRATEGY half — given a faithful placement, was the trade good?
        "strategy": {
            "by_exit_state": by_state,
            "pnl_total": round(sum(pnls), 2) if pnls else None,
            "pnl_measured_n": len(pnls),
            "win_rate": round(len(wins) / len(pnls), 4) if pnls else None,
        },
        "rows": rows,
    }


# ── rendering ─────────────────────────────────────────────────────────

_STATE_LABEL = {
    AT_STOP: "exited at the declared STOP",
    AT_TARGET: "exited at the declared TARGET",
    BEYOND_STOP: "exited PAST the stop (gap/slippage)",
    BEYOND_TARGET: "exited PAST the target",
    MANUAL_IN_PROFIT: "manual exit, in profit",
    MANUAL_IN_LOSS: "manual exit, in loss",
    MANUAL_FLAT: "manual exit, flat",
    UNCLASSIFIED_NO_TICKET: "UNCLASSIFIED — no ticket link",
    UNCLASSIFIED_NO_LEVELS: "UNCLASSIFIED — ticket carries no SL/TP",
    UNCLASSIFIED_NO_EXIT: "UNCLASSIFIED — no exit price recorded",
}


def render(res: Dict[str, Any], account: str) -> str:
    p, b, s = res["population"], res["bridge"], res["strategy"]
    L: List[str] = []
    L.append(f"# Prop trade quality — `{account}`\n")
    L.append(
        f"_Population: {p['closed']} closed fills of {p['fills_seen']} seen; "
        f"{p['graded']} graded, {p['unclassified']} unclassified. "
        f"Exit tolerance {res['tolerance_bps']:g} bps._\n"
    )
    if p["closed"] < 20:
        L.append(
            f"> ⚠️ **n = {p['closed']}.** Every rate below is over a small "
            "denominator — read the counts, not the percentages.\n"
        )

    L.append("\n## Bridge — did the human place what the bot asked for?\n")
    if b["entry_slip_measured_n"]:
        L.append(
            f"- Entry slippage vs ticketed entry (signed, **+ = worse**): "
            f"median **{b['entry_slip_bps_median']:.1f} bps**, worst "
            f"**{b['entry_slip_bps_worst']:.1f} bps**, over "
            f"{b['entry_slip_measured_n']} fills."
        )
    else:
        L.append("- Entry slippage: **not measurable** — no fill carried both a "
                 "ticketed and an actual entry price.")
    L.append(f"- Fills with **no ticket link**: {b['fills_with_no_ticket_link']} "
             "(these cannot be graded on exit, and are NOT counted as manual).")

    L.append("\n## Strategy — given a faithful placement, was the trade good?\n")
    L.append("| exit landed | n |")
    L.append("|---|---:|")
    for st, n in sorted(s["by_exit_state"].items(), key=lambda kv: -kv[1]):
        L.append(f"| {_STATE_LABEL.get(st, st)} | {n} |")
    if s["pnl_measured_n"]:
        L.append(
            f"\n- Net **${s['pnl_total']:,.2f}** over {s['pnl_measured_n']} "
            f"resolved fills; win rate **{s['win_rate']*100:.1f}%**."
        )
    if p["near_boundary"]:
        L.append(
            f"\n> ⚠️ **{p['near_boundary']} row(s) sit within 2x the tolerance of "
            "a level without being inside it** — their bucket would move if "
            f"`--tolerance-bps` did. The split is not robust to the threshold at "
            "this n."
        )
    else:
        L.append(
            "\n> No row sits near a tolerance boundary, so the split above does "
            "not depend on the threshold choice."
        )
    return "\n".join(L) + "\n"


def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--account", default=DEFAULT_ACCOUNT)
    ap.add_argument("--api-base", default=DEFAULT_API)
    ap.add_argument("--db", action="store_true",
                    help="Read the local prop journal instead of the API "
                         "(for a VM-side run).")
    ap.add_argument("--limit", type=int, default=500)
    ap.add_argument("--tolerance-bps", type=float, default=DEFAULT_TOLERANCE_BPS,
                    help=f"How close to a level counts as 'at' it "
                         f"(default {DEFAULT_TOLERANCE_BPS:g}). Reported in the "
                         "output, because it moves the classification.")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv[1:])

    try:
        fills, tickets = (load_from_db(args.account, args.limit) if args.db
                          else load_from_api(args.api_base, args.account, args.limit))
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(f"ERROR: could not read the prop journal: {exc}\n")
        return 2

    if not fills:
        # An empty read is reported as such, never as a clean review.
        sys.stderr.write(
            f"ERROR: no prop fills returned for {args.account!r}. That is "
            "'we saw nothing', not 'the book is clean' — check the source "
            "before treating this as a passing review.\n"
        )
        return 3

    res = review(fills, tickets, tolerance_bps=args.tolerance_bps)
    print(json.dumps(res, indent=2) if args.json else render(res, args.account))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
