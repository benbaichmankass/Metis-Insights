#!/usr/bin/env python3
"""Propose the exact cancel for an OVER-COVERED IB position. Proposes only.

WHAT THIS IS FOR
================
`ib_paper` MHG rests 200% stop cover across two disjoint OCA groups, and it has
been handed forward as "operator-owed" by three separate sessions in one day
with no state change — the register row
`BL-20260825-OPERATOR-OWED-ITEMS-HAVE-NO-REGISTER-NO-AGE-AND-NO-ESCALATION`.
It sat in the human column not because a human is required
— `cancel-ib-order` is a documented, allowlisted system-action — but because one
auto-remediation on 2026-08-20 cancelled the leg that MATCHED the journal
(`BL-20260820-OVERCOVER-REMEDIATION-CANCELLED-THE-JOURNAL-MATCHING-LEG`).

That is a reason to distrust the SELECTION. So the selection now lives in
`src/runtime/over_cover_decision.py` as a pure function tested against that
exact recorded failure, and this script is the thing that feeds it real venue
and journal state and prints what to dispatch.

⚠️ IT DISPATCHES NOTHING. It emits an issue body for a human to read and fire.
Dispatching `cancel-ib-order` against a live position is Tier-2 and needs one
operator OK; a repair tool that could fire itself would be exactly the shape
that produced the 2026-08-20 outcome.

⚠️ IT NEVER TAKES A LEVEL FROM A CALLER. The declared stop/target come from the
journal rows this script is given (`trades.stop_loss` / `take_profit_1`, as
`/api/bot/positions` publishes them). There is no `--stop` flag and there must
not be one.

# wiring: manual-only — run by a session (or the operator) while working the
# `OO-20260825-IB-PAPER-MHG-STOP-OVER-COVER` row in
# docs/claude/operator-owed-register.json. It is a PROPOSAL step by design and
# must stay one: the live sweep already detects and pages this condition; what
# was missing is the selection, and firing it is the operator's call.

USAGE
=====
Reads two diag payloads, so it runs anywhere and needs no broker::

    scripts/ops/diag_fetch.sh '/api/diag/ib_open_orders?account_id=ib_paper' > /tmp/o.json
    scripts/ops/diag_fetch.sh '/api/bot/positions?include_paper=true'        > /tmp/p.json
    python3 scripts/ops/over_cover_proposal.py \
        --orders-json /tmp/o.json --positions-json /tmp/p.json --account ib_paper

    python3 scripts/ops/over_cover_proposal.py --self-test
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List, Optional

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from scripts.ops.broker_bracket_reconcile import (  # noqa: E402
    load_tick_sizes,
    protective_leg_side,
)
from src.runtime.over_cover_decision import (  # noqa: E402
    STATE_CANCEL_GROUP,
    decide_over_cover,
)

# collapsed-state: not_graded — this script branches on the ONE state that
# produces an action (STATE_CANCEL_GROUP, via the constant) and renders every
# other state as "nothing to dispatch" with its own reason string printed
# verbatim. That is deliberate and is not a collapse: the states are DISPLAYED
# distinctly to the operator, and the difference between "measured all-clear",
# "we could not look" and "the journal did not single a group out" is exactly
# what the reason line carries. Acting on any of them identically is the point
# — none of them may cancel an order. The literal here is the diag read-state
# vocabulary, a different field. Full state coverage lives in
# tests/test_over_cover_decision.py.
#: Read states from /api/diag/ib_open_orders, kept apart on purpose: an
#: account we could not look at must never read as an account holding nothing.
_READ_OK = "orders_read"


def _accounts(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows = payload.get("accounts")
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def _positions(payload: Any) -> List[Dict[str, Any]]:
    rows = payload if isinstance(payload, list) else (
        payload.get("positions") if isinstance(payload, dict) else None)
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def _norm(symbol: Any) -> str:
    return str(symbol or "").strip().upper()


def propose(
    orders_payload: Dict[str, Any],
    positions_payload: Any,
    *,
    account: Optional[str] = None,
    symbol: Optional[str] = None,
    tick_sizes: Optional[Dict[str, float]] = None,
) -> List[Dict[str, Any]]:
    """One proposal per (account, symbol) that has an open journal position."""
    ticks = tick_sizes if tick_sizes is not None else load_tick_sizes()
    positions = _positions(positions_payload)
    proposals: List[Dict[str, Any]] = []

    for acct in _accounts(orders_payload):
        acct_id = str(acct.get("account_id") or "")
        if account and acct_id != account:
            continue

        read_state = str(acct.get("read_state") or "")
        if read_state != _READ_OK:
            proposals.append({
                "account": acct_id,
                "symbol": None,
                "decision": {
                    "state": "not_graded",
                    "cancel_order_ids": [],
                    "reason": (
                        f"read_state={read_state!r} — we could not look at this "
                        f"account's resting orders. Emphatically not 'it holds "
                        f"nothing'."),
                },
            })
            continue

        orders = acct.get("orders")
        if not isinstance(orders, list):
            continue

        symbols = {_norm(o.get("symbol")) for o in orders if isinstance(o, dict)}
        for sym in sorted(s for s in symbols if s):
            if symbol and sym != _norm(symbol):
                continue
            row = next(
                (p for p in positions
                 if _norm(p.get("symbol")) == sym
                 and str(p.get("account") or "") == acct_id), None)
            if row is None:
                # No open journal row. A resting leg with no position is a
                # different finding (a stranded order) and is not this tool's.
                continue

            legs = [
                dict(o, side=protective_leg_side(o.get("order_type")))
                for o in orders
                if isinstance(o, dict) and _norm(o.get("symbol")) == sym
            ]
            decision = decide_over_cover(
                position_qty=row.get("qty"),
                direction=row.get("side"),
                declared_stop=row.get("stopLoss"),
                declared_target=row.get("takeProfit"),
                legs=legs,
                tick_size=ticks.get(sym),
            )
            proposals.append(
                {"account": acct_id, "symbol": sym, "decision": decision,
                 "trade_id": row.get("id")})
    return proposals


def render(proposal: Dict[str, Any]) -> str:
    """The operator-facing text: the verdict, and the body to fire if any."""
    decision = proposal["decision"]
    head = (f"{proposal['account']}/{proposal.get('symbol') or '(account)'}: "
            f"{decision['state']}")
    lines = [head, f"  {decision.get('reason')}"]

    if decision["state"] != STATE_CANCEL_GROUP:
        lines.append("  → nothing to dispatch. A refusal here is the tool "
                     "working: the journal did not single a group out.")
        return "\n".join(lines)

    lines.append(f"  keep:   {decision['keep_groups']} (matches the journal's "
                 f"declared stop)")
    lines.append(f"  cancel: {decision['cancel_groups']} → order id(s) "
                 f"{decision['cancel_order_ids']}")
    lines.append("")
    lines.append("  ⚠️ Tier-2. DRY-RUN FIRST (omit `apply`), read the refusals it "
                 "reports, and only then re-fire with `apply: true`.")
    lines.append("  ⚠️ One issue PER ORDER — `cancel-ib-order` takes exactly one.")
    for order_id in decision["cancel_order_ids"]:
        lines.append("")
        lines.append(f"  --- issue body (label: system-action) for order "
                     f"{order_id} ---")
        lines.append("  action: cancel-ib-order")
        lines.append(f"  account: {proposal['account']}")
        lines.append(f"  order: {order_id}")
        lines.append("  force_protective: true")
        lines.append("  force_client_id: true")
        lines.append(f"  reason: over-cover repair on "
                     f"{proposal['account']}/{proposal['symbol']} — this order's "
                     f"OCA group {decision['cancel_groups']} matches no level "
                     f"declared in the journal, while group "
                     f"{decision['keep_groups']} matches trades.stop_loss. "
                     f"Selected by src/runtime/over_cover_decision.py.")
    lines.append("")
    lines.append("  Both force_* keys are set because this class of order trips "
                 "both default refusals (a protective, trader-band-owned leg) — "
                 "that combination is what BL-20260816-NO-PER-ORDER-IB-CANCEL "
                 "was. Read the dry run before waiving them.")
    return "\n".join(lines)


def _self_test() -> int:
    """The recorded failure, end to end through this script's own plumbing."""
    orders = {"accounts": [{
        "account_id": "ib_paper",
        "read_state": _READ_OK,
        "orders": [
            {"order_id": 338, "symbol": "MES", "order_type": "STP",
             "total_quantity": 15.0, "aux_price": 7516.50,
             "oca_group": "oca-protect-336"},
            {"order_id": 375, "symbol": "MES", "order_type": "STP",
             "total_quantity": 15.0, "aux_price": 7533.75,
             "oca_group": "oca-protect-373"},
        ],
    }]}
    positions = [{"id": "4350", "account": "ib_paper", "symbol": "MES",
                  "side": "buy", "qty": 15.0, "stopLoss": 7533.69642857,
                  "takeProfit": 8390.59025}]

    failures = []
    got = propose(orders, positions, tick_sizes={"MES": 0.25})
    if len(got) != 1:
        failures.append(f"expected one proposal, got {len(got)}")
    else:
        decision = got[0]["decision"]
        if decision["state"] != STATE_CANCEL_GROUP:
            failures.append(f"expected {STATE_CANCEL_GROUP}, got {decision['state']}")
        if decision["cancel_order_ids"] != [338]:
            failures.append(
                f"must cancel the STRAY 338, not the journal-matching 375; "
                f"got {decision['cancel_order_ids']}")
        body = render(got[0])
        if "order: 338" not in body or "order: 375" in body:
            failures.append("the rendered issue body names the wrong order")

    # An unreadable account must not render as 'nothing to do'.
    blind = propose({"accounts": [{"account_id": "ib_paper",
                                   "read_state": "could_not_look"}]}, [])
    if not blind or blind[0]["decision"]["state"] != "not_graded":
        failures.append("a could_not_look account must grade not_graded")

    if failures:
        print("over-cover proposal self-test: FAIL")
        for failure in failures:
            print(f"  ✗ {failure}")
        return 1
    print("over-cover proposal self-test: OK — the 2026-08-20 inputs select the "
          "STRAY (338) and keep the journal-matching leg (375)")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--orders-json", help="/api/diag/ib_open_orders payload")
    ap.add_argument("--positions-json", help="/api/bot/positions payload")
    ap.add_argument("--account")
    ap.add_argument("--symbol")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)

    if args.self_test:
        return _self_test()
    if not args.orders_json or not args.positions_json:
        ap.error("--orders-json and --positions-json are required")

    with open(args.orders_json, "r", encoding="utf-8") as fh:
        orders = json.load(fh)
    with open(args.positions_json, "r", encoding="utf-8") as fh:
        positions = json.load(fh)

    proposals = propose(orders, positions, account=args.account,
                        symbol=args.symbol)
    if not proposals:
        print("over-cover proposal: no (account, symbol) with both resting "
              "protective legs and an open journal row. Nothing GRADED — which "
              "is not the same as nothing wrong.")
        return 0
    for proposal in proposals:
        print(render(proposal))
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
