#!/usr/bin/env python3
"""Attach the declared take-profit to a TARGET-NAKED IB position.

Why this exists
---------------
Measured on ``ib_paper`` 2026-08-16 (``/api/diag/ib_open_orders``, a confirmed
clean account-wide read): MGC 105 long held one stop and no limit; MES 15 long
held TWO stops and no limit. **Zero limit orders existed on the account.** Both
positions carried a declared take-profit in the journal that was never placed
at the broker, and `protection_coverage` graded a stop and a target as
interchangeable, so nothing ever alerted (BL-20260816-COVERAGE-IS-ONE-SIDED).

A live position that can only stop out or run is not an acceptable state.

The mechanism, and why it is not a re-arm
-----------------------------------------
The target is placed **into the OCA group the existing stop already lives in**
(``IBClient.place_target_in_group``). IBKR then cancels the stop when the
target fills, and vice versa — one leg, no window, nothing cancelled up front.

The alternative (`place_protective`) mints a NEW group and pre-cancels on a
group name that will not match, so it would leave the original stop resting and
add a second pair on top. When that new target filled it would cancel only its
own sibling, leaving the original stop alive on a FLAT book — able to fill into
a reverse position. That is the failure this script is shaped to avoid.

Refusals (each is a real hazard observed on this account)
--------------------------------------------------------
* **A resting non-protective order on the symbol** (a stray ``MKT``) — if the
  target fills and the position goes flat, that stray can still fill and open a
  REVERSE position. Orders 6 and 378 on MGC are exactly this. Clear them with
  ``cancel-ib-order`` first.
* **A target already resting** — never place a second one.
* **More than one candidate OCA group** (MES has two stop groups) — joining one
  leaves the other stop unlinked, so this refuses rather than picking.
* **Stop qty != position size** — the geometry is not understood; do not guess.

DRY-RUN by default. Never raises; every failure is reported as JSON.

Usage (on the live VM, via the ``attach-ib-target`` system-action):
    python3 scripts/ops/attach_ib_target.py --account ib_paper --symbol MGC
    python3 scripts/ops/attach_ib_target.py --account ib_paper --symbol MGC --apply
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List, Optional

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

_STOP_TYPES = ("STP", "TRAIL")


def _is_stop(order_type: Optional[str]) -> bool:
    t = str(order_type or "").upper()
    return "TRAIL" in t or t.startswith("STP")


def _is_target(order_type: Optional[str]) -> bool:
    t = str(order_type or "").upper()
    return not _is_stop(t) and ("LMT" in t or t == "LIMIT")


def _is_protective(order_type: Optional[str]) -> bool:
    return _is_stop(order_type) or _is_target(order_type)


def _load_account(account_id: str) -> Optional[Dict[str, Any]]:
    from src.units.ui.data_loaders import list_accounts

    for acc in list_accounts() or []:
        if (acc or {}).get("account_id") == account_id or (acc or {}).get("name") == account_id:
            return acc
    return None


def _read_orders(cfg: Dict[str, Any]) -> Optional[List[Dict[str, Any]]]:
    from src.units.accounts.clients import ib_read_client_for

    client = ib_read_client_for(cfg)
    if client is None:
        return None
    try:
        return client.list_open_orders()
    except Exception:  # noqa: BLE001
        return None


def _open_trade(account_id: str, symbol: str) -> Optional[Dict[str, Any]]:
    """The open journal row for (account, symbol) — carries the DECLARED tp.

    Returns None when there is not exactly one; the caller refuses rather than
    choosing, because two open rows on a netted contract mean two different
    declared targets and no basis to prefer either.
    """
    import sqlite3

    from src.utils.paths import trade_journal_db_path

    # READ-ONLY, and resolved through the single canonical resolver — the
    # `canonical-db-resolver` guard forbids an inline env-read or a
    # CWD-relative fallback here, and this function must never be able to
    # write to the money DB.
    #
    # 2026-08-18: this used to `from src.units.db.database import
    # get_connection`, a symbol that module has never exported (it exposes
    # `Database`/`Database.connect`/`get_db`). Every invocation of this action
    # therefore died with ImportError at line 1 of the read — see
    # BL-20260818-ATTACH-IB-TARGET-HAS-NEVER-RUN. Note also that the old
    # `with get_connection() as conn:` shape would have been wrong even had the
    # symbol existed: sqlite3's context manager commits/rolls back a
    # TRANSACTION, it does not close the connection.
    conn = sqlite3.connect(f"file:{trade_journal_db_path()}?mode=ro", uri=True)
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, symbol, direction, position_size, stop_loss, "
            "take_profit_1 FROM trades WHERE status='open' AND account_id=? "
            "AND UPPER(symbol) LIKE ?",
            (account_id, f"{symbol.upper()}%"),
        ).fetchall()
    finally:
        conn.close()
    return dict(rows[0]) if len(rows) == 1 else None


def _ib_ops_client_id() -> int:
    """A place-capable, process-unique IB clientId for one-shot ops writes.

    ⚠️ THIS WAS MISSING UNTIL 2026-08-22 AND THE ACTION COULD NOT WORK WITHOUT IT.
    `_attach` called `ib_client_for(cfg, readonly=False)`, which resolves the
    TRADER'S OWN EXECUTION clientId (497). While the trader is running — i.e.
    always — IBKR refuses the second connection outright:

        Error 326, reqId -1: Unable to connect as the client id is already in use.
        IBClient: circuit breaker tripped ... IB calls suppressed for 120s.
        {"action": "place_failed"}

    Observed live on ib_paper/MES, system-action issue #10139. So a repair action
    shipped 2026-08-16 for BL-20260816-COVERAGE-IS-ONE-SIDED had never actually
    placed a target against a live trader; its dry run reports `state: ready`
    and the apply cannot connect. A dry run that passes and an apply that cannot
    is the worst shape for a repair tool — it reads as available right up to the
    moment it is needed.

    Its sibling `flatten_ib_position.py` had this right from the start and says
    why in the same words; this is that function, not a new idea. Distinct from
    the trader's execution ids (496/497) AND the read range (9000-9899), so an
    ops write can neither be rejected as "clientId already in use" nor race the
    live execution socket. Salted by PID so two ops runs do not collide.

    NOTE the refusal is what PROTECTED the trader here: IBKR rejects a duplicate
    clientId rather than evicting the incumbent, so the live session was never at
    risk (verified after the failure — all three clients connected, zero
    consecutive failures, tick age 1.2s). The 120s breaker trip was in the
    short-lived ops process, which then exited.
    """
    return 9900 + (os.getpid() % 90)


def _attach(cfg: Dict[str, Any], *, symbol: str, direction: str, qty: float,
            tp: float, oca_group: str) -> Dict[str, Any]:
    from src.units.accounts.clients import ib_client_for

    client = ib_client_for(cfg, client_id=_ib_ops_client_id(), readonly=False)
    if client is None:
        return {"retCode": 1, "retMsg": "could not build an IB client"}
    try:
        return client.place_target_in_group({
            "symbol": symbol, "direction": direction, "qty": qty,
            "tp": tp, "oca_group": oca_group,
        })
    except Exception as exc:  # noqa: BLE001
        return {"retCode": 1, "retMsg": f"{type(exc).__name__}: {exc}"}


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--account", default="ib_paper")
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args(argv)

    sym = args.symbol.upper()
    out: Dict[str, Any] = {"account": args.account, "symbol": sym,
                           "apply": bool(args.apply)}

    cfg = _load_account(args.account)
    if cfg is None:
        out.update(state="could_not_look", error=f"unknown account {args.account!r}")
        print(json.dumps(out, indent=2))
        return 2

    rows = _read_orders(cfg)
    if rows is None:
        out.update(state="could_not_look",
                   error="account-wide order read failed — this is NOT evidence "
                         "that the position is unprotected or that no target exists")
        print(json.dumps(out, indent=2))
        return 3

    mine = [r for r in rows if str(r.get("symbol") or "").upper() == sym]
    out["orders_on_symbol"] = len(mine)

    targets = [r for r in mine if _is_target(r.get("order_type"))]
    if targets:
        out.update(state="already_has_target", targets=targets,
                   note="a resting target already exists — nothing to do")
        print(json.dumps(out, indent=2))
        return 0

    strays = [r for r in mine if not _is_protective(r.get("order_type"))]
    if strays:
        out.update(state="refused", blocker=(
            f"{len(strays)} non-protective order(s) resting on {sym} — if the "
            "target fills and the position goes flat, one of these can still "
            "fill and open a REVERSE position. Clear them with cancel-ib-order "
            "first."), strays=strays)
        print(json.dumps(out, indent=2))
        return 4

    stops = [r for r in mine if _is_stop(r.get("order_type"))]
    groups = sorted({str(r.get("oca_group") or "") for r in stops if r.get("oca_group")})
    if len(groups) != 1:
        out.update(state="refused", stops=stops, oca_groups=groups, blocker=(
            f"expected exactly ONE stop OCA group on {sym}, found {len(groups)} "
            f"{groups} — joining one would leave the other stop unlinked, so "
            "this refuses rather than picking. Cancel the stray stop first."))
        print(json.dumps(out, indent=2))
        return 4
    group = groups[0]

    trade = _open_trade(args.account, sym)
    if trade is None:
        out.update(state="refused", blocker=(
            "expected exactly one OPEN journal row for this (account, symbol) "
            "to read the declared take-profit from"))
        print(json.dumps(out, indent=2))
        return 4
    tp = trade.get("take_profit_1")
    if tp in (None, 0) or float(tp) <= 0:
        out.update(state="no_declared_target", trade_id=trade.get("id"), note=(
            "this trade declares no take-profit, so there is nothing to "
            "restore. Whether every strategy SHOULD declare one is a Tier-3 "
            "question, not a repair — a target is never invented here."))
        print(json.dumps(out, indent=2))
        return 0

    size = sum(float(r.get("total_quantity") or 0) for r in stops)
    declared_qty = float(trade.get("position_size") or 0)
    if abs(size - declared_qty) > 1e-9:
        out.update(state="refused", blocker=(
            f"stop coverage {size} != journal position_size {declared_qty} — "
            "the geometry is not understood; refusing to guess a target qty"))
        print(json.dumps(out, indent=2))
        return 4

    out.update(state="ready", oca_group=group, declared_tp=float(tp),
               qty=declared_qty, direction=trade.get("direction"),
               trade_id=trade.get("id"))

    if not args.apply:
        out["action"] = "dry_run"
        out["would"] = (f"place LMT {declared_qty} @ {float(tp)} into OCA group "
                        f"{group!r} (GTC); the resting stop cancels when it fills")
        print(json.dumps(out, indent=2))
        return 0

    resp = _attach(cfg, symbol=sym, direction=str(trade.get("direction") or ""),
                   qty=declared_qty, tp=float(tp), oca_group=group)
    out["place_response"] = resp
    if resp.get("retCode") != 0:
        out["action"] = "place_failed"
        print(json.dumps(out, indent=2))
        return 1

    after = _read_orders(cfg)
    if after is None:
        out.update(action="placed_unconfirmed", verify_state="could_not_look",
                   note="the order was accepted but the verification read failed "
                        "— accepted is not confirmed; re-run the dry-run")
        print(json.dumps(out, indent=2))
        return 3
    now_targets = [r for r in after
                   if str(r.get("symbol") or "").upper() == sym
                   and _is_target(r.get("order_type"))]
    out["targets_after"] = now_targets
    out.update(action="placed" if now_targets else "place_not_effective",
               verify_state="target_resting" if now_targets else "still_absent")
    print(json.dumps(out, indent=2))
    return 0 if now_targets else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
