#!/usr/bin/env python3
# wiring: system-action `switch-bybit-position-mode` (scripts/ops/bybit_switch_position_mode_action.sh).
"""Read — and with ``--apply``, SWITCH — a Bybit symbol's VENUE position mode.

Workplan item **T.2 arming**, `BL-20260821-PAIRS-SOL-ETH-STRANDS-ON-EVERY-OPEN`.

WHY THIS EXISTS. `src/runtime/bybit_position_mode.py` made the CODE expressible
in hedge mode and deliberately stopped there, saying so in terms: *"Turning
hedge mode on for a real (account, symbol) is a separate, operator-gated step —
it requires switching the position mode AT THE VENUE, which this module
deliberately does NOT do."* Nothing in the repo performed that step: measured
2026-08-30, `switch_position_mode` had **zero** call sites across `src/` and
`scripts/`. This is that step, and it is a separate script precisely so the
venue mutation is never a side effect of importing the resolver.

WHAT ONE-WAY COSTS, so the motivation is not re-derived. Under one-way netting a
symbol is ONE net position. The market-neutral pairs sleeve opens a leg that is
frequently OPPOSITE a concurrent directional strategy on the same symbol, so the
leg does not open a book — it REDUCES the standing one. Measured 2026-08-21
against exchange truth: every SOLUSDT/ETHUSDT pair opened since 2026-08-18
stranded, **8 of 8**, and the arithmetic closed exactly.

⚠️ **THE MODE IS READ FROM `positionIdx`, NOT FROM AN ACCOUNT SETTING, AND THAT
IS WHAT MAKES IT READABLE WHILE FLAT.** Bybit returns a position row per book
even at size 0: one-way yields a single row with `positionIdx 0`, hedge yields
two rows with `positionIdx 1` and `2`. So the SET of indices IS the mode, and it
survives the symbol being flat — which matters because a symbol MUST be flat to
switch, i.e. the state we most need to read is the one
`BL-20260826-BYBIT-POSITION-MODE-UNREADABLE-FOR-A-FLAT-SYMBOL` records as
unreadable on the existing surfaces (both of those skip ``size <= 0``).

⚠️ **THE BYBIT MODE ENUM IS NOT ASSERTED FROM MEMORY — IT IS VERIFIED BY
RE-READING.** Bybit v5 documents `mode` 0 = "Merged Single" (one-way) and
3 = "Both Sides" (hedge); note it is **3, not 1**, which is the obvious wrong
guess. Rather than trust that, ``--apply`` re-reads the mode afterwards and
reports ``switch_verified`` only when the observed `positionIdx` set actually
changed to the requested shape. A silent no-op therefore reports as a FAILURE
rather than as success.

FOUR READ STATES, NEVER COLLAPSED:

  ``read``    the venue returned rows and the mode is resolved.
  ``absent``  the venue returned NO row for this symbol — never traded here.
              This is not "one-way"; it is "we have nothing to read".
  ``ambiguous`` rows came back but their `positionIdx` set matches neither
              shape. Reported rather than guessed.
  ``error``   the call raised — WE COULD NOT LOOK. Never read as one-way.

REFUSALS BEFORE ANY MUTATION (all of them, every time, on ``--apply``):
  * the symbol must be FLAT — every returned row at size 0. Bybit itself
    rejects a switch on an open position, but refusing locally gives a legible
    reason instead of a venue error code, and keeps the dry-run honest.
  * there must be NO resting orders on the symbol. A resting protective leg
    belongs to a book that is about to be redefined.
  * ``--confirm-account`` must match ``--account``. A position-mode switch is
    per-SYMBOL and per-ACCOUNT and there is no undo that restores the books;
    the echo makes the target explicit at the call site.

Default is REPORT-ONLY. ``--apply`` mutates. Always prints the mode before and
(on apply) after, so an unchanged mode is visible rather than assumed.

Usage:
    python3 scripts/ops/bybit_switch_position_mode.py --account bybit_1 --symbol SOLUSDT
    python3 scripts/ops/bybit_switch_position_mode.py --account bybit_1 --symbol SOLUSDT \
        --mode hedge --confirm-account bybit_1 --apply
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict, List, Optional

MODE_ONE_WAY = 0      # Bybit v5 "Merged Single"
MODE_BOTH_SIDES = 3   # Bybit v5 "Both Sides" (hedge). NOT 1 — see the docstring.

ONE_WAY = "one_way"
HEDGE = "hedge"


def _f(v: Any) -> float:
    try:
        return float(str(v).strip() or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _account_cfg(account_id: str) -> Optional[Dict[str, Any]]:
    from src.units.ui.data_loaders import list_accounts
    for acc in list_accounts() or []:
        if str(acc.get("id") or acc.get("account_id")) == account_id:
            return acc
    return None


def read_mode(client, category: str, symbol: str) -> Dict[str, Any]:
    """Resolve the venue's position mode for *symbol*. Never guesses."""
    try:
        resp = client.get_positions(category=category, symbol=symbol)
    except Exception as exc:  # noqa: BLE001 — a failed read is a STATE, not a crash
        return {"read_state": "error", "mode": None, "error": f"{type(exc).__name__}: {exc}",
                "position_idxs": None, "flat": None, "rows": None}

    rows: List[Dict[str, Any]] = ((resp or {}).get("result") or {}).get("list") or []
    if not rows:
        return {"read_state": "absent", "mode": None, "position_idxs": [], "flat": None,
                "rows": [], "error": None}

    idxs = sorted({int(r["positionIdx"]) for r in rows if str(r.get("positionIdx", "")).strip().isdigit()})
    sizes = [_f(r.get("size")) for r in rows]
    flat = all(s <= 0 for s in sizes)

    if idxs == [0]:
        mode = ONE_WAY
        state = "read"
    elif idxs and set(idxs) <= {1, 2}:
        mode = HEDGE
        state = "read"
    else:
        mode = None
        state = "ambiguous"

    return {
        "read_state": state,
        "mode": mode,
        "position_idxs": idxs,
        "flat": flat,
        "rows": [{"positionIdx": r.get("positionIdx"), "side": r.get("side"),
                  "size": r.get("size"), "avgPrice": r.get("avgPrice")} for r in rows],
        "error": None,
    }


def resting_orders(client, category: str, symbol: str) -> Dict[str, Any]:
    """Resting orders on *symbol*. ``count: None`` means WE COULD NOT LOOK."""
    try:
        resp = client.get_open_orders(category=category, symbol=symbol)
    except Exception as exc:  # noqa: BLE001
        return {"read_state": "error", "count": None, "error": f"{type(exc).__name__}: {exc}"}
    rows = ((resp or {}).get("result") or {}).get("list") or []
    return {"read_state": "read", "count": len(rows), "error": None,
            "order_ids": [r.get("orderId") for r in rows][:20]}


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--account", required=True, help="account_id in accounts.yaml, e.g. bybit_1")
    ap.add_argument("--symbol", required=True, help="bot symbol, e.g. SOLUSDT")
    ap.add_argument("--mode", choices=[ONE_WAY, HEDGE], default=None,
                    help="target mode; omit to REPORT ONLY")
    ap.add_argument("--category", default="linear")
    ap.add_argument("--confirm-account", default=None,
                    help="must equal --account for --apply to proceed")
    ap.add_argument("--apply", action="store_true", help="actually switch (default: report only)")
    a = ap.parse_args(argv)

    out: Dict[str, Any] = {"status": "position_mode", "account": a.account, "symbol": a.symbol,
                           "category": a.category, "requested_mode": a.mode,
                           "applied": False, "switch_verified": None}

    cfg = _account_cfg(a.account)
    if cfg is None:
        out.update(action="abort_unknown_account",
                   detail=f"no account {a.account!r} in accounts.yaml")
        print(json.dumps(out)); return 2

    from src.units.accounts.clients import bybit_client_for
    client = bybit_client_for(cfg)
    if client is None:
        out.update(action="abort_no_client",
                   detail="bybit_client_for returned None (missing creds?)")
        print(json.dumps(out)); return 2

    before = read_mode(client, a.category, a.symbol)
    orders = resting_orders(client, a.category, a.symbol)
    out["before"] = before
    out["resting_orders"] = orders

    if not a.apply:
        out["action"] = "report_only"
        out["detail"] = ("read-only. Re-run with --mode, --confirm-account and --apply to switch."
                         if a.mode else "read-only, no --mode requested.")
        print(json.dumps(out)); return 0

    # ---- refusals, all of them, before any mutation ----
    if a.mode is None:
        out.update(action="abort_no_mode", detail="--apply requires --mode")
        print(json.dumps(out)); return 2
    if a.confirm_account != a.account:
        out.update(action="abort_unconfirmed",
                   detail="--confirm-account must equal --account for --apply")
        print(json.dumps(out)); return 2
    if before["read_state"] != "read":
        out.update(action="abort_unreadable_mode",
                   detail=f"read_state={before['read_state']!r} — refusing to switch a mode we could not read")
        print(json.dumps(out)); return 3
    if before["mode"] == a.mode:
        out.update(action="noop_already_in_mode", switch_verified=True,
                   detail=f"already {a.mode!r}; nothing to do")
        print(json.dumps(out)); return 0
    if before["flat"] is not True:
        out.update(action="abort_not_flat",
                   detail="symbol is NOT flat — every position row must be size 0 before a mode switch")
        print(json.dumps(out)); return 3
    if orders["read_state"] != "read":
        out.update(action="abort_orders_unreadable",
                   detail="could not read resting orders — refusing (a resting leg belongs to a book about to be redefined)")
        print(json.dumps(out)); return 3
    if orders["count"]:
        out.update(action="abort_resting_orders",
                   detail=f"{orders['count']} resting order(s) — cancel them first")
        print(json.dumps(out)); return 3

    venue_mode = MODE_BOTH_SIDES if a.mode == HEDGE else MODE_ONE_WAY
    out["venue_mode_sent"] = venue_mode
    try:
        resp = client.switch_position_mode(category=a.category, symbol=a.symbol, mode=venue_mode)
        out["switch_response"] = {"retCode": (resp or {}).get("retCode"),
                                  "retMsg": (resp or {}).get("retMsg")}
    except Exception as exc:  # noqa: BLE001
        out.update(action="switch_failed", detail=f"{type(exc).__name__}: {exc}")
        print(json.dumps(out)); return 4

    out["applied"] = True
    after = read_mode(client, a.category, a.symbol)
    out["after"] = after
    # The re-read IS the verification. A silent no-op must report as failure.
    out["switch_verified"] = (after["read_state"] == "read" and after["mode"] == a.mode)
    out["action"] = "switched" if out["switch_verified"] else "switch_unverified"
    if not out["switch_verified"]:
        out["detail"] = ("the venue accepted the call but the re-read does NOT show the requested "
                         "mode — treat as NOT switched and do not arm the allowlist")
    print(json.dumps(out))
    return 0 if out["switch_verified"] else 5


if __name__ == "__main__":
    sys.exit(main())
