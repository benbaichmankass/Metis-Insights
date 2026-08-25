#!/usr/bin/env python3
"""Cancel ONE resting IB order by id — the per-order cancel wire.

Why this exists
---------------
Until 2026-08-16 there was **no way to cancel a single IB order** from any
session, and that was a capability gap, not an inconvenience. The three things
that existed each failed for a different reason:

* :meth:`IBClient.cancel` resolves its target by iterating ``ib.openTrades()``,
  which returns only orders THIS clientId placed. An ops caller runs on a
  process-unique id, so it reports ``order <id> not found among open trades``
  for an order it can plainly see on the account-wide read.
* ``flatten-ib-position`` is a *flatten*: it would place another market order.
* ``reqGlobalCancel`` cancels **everything**, including the protective stops of
  every unrelated position on the account.

The stranded ``ib_paper``/MGC order 6 (``MKT SELL 105``, tif DAY, abandoned by a
failed flatten) sat unreachable through all three.

The mechanism, verified against the TWS API docs
------------------------------------------------
IB binds an order to the clientId that submitted it:

    "Orders submitted via the TWS API will always be bound to the client
    application (i.e. client Id) they were submitted from meaning only the
    submitting client will be able to modify the placed order."
    -- TWS API, open_orders

    ``cancelOrder`` "can only be used to cancel an order that was placed
    originally by a client with the same client ID (or from TWS for client
    ID 0)."
    -- TWS API, cancel_order

So the ONLY per-order cancel path is to **connect as the owning clientId**.

⚠️ The Master API client ID does **not** grant this. It is documented solely as
receiving order-status callbacks for all clients ("The client with Master Client
ID (set in TWS/IBG) will receive order status messages for all clients") — a
*visibility* role, with no cancellation authority anywhere in the API reference.
An earlier backlog note proposed setting one as the fix; that premise is refuted
and configuring one would not have cancelled order 6.

What this script does
---------------------
1. Reads the account's orders **account-wide** (``reqAllOpenOrders`` via
   :meth:`IBClient.list_open_orders`) on a read client, and locates the target
   by ``--order-id`` or ``--perm-id``.
2. Reports a three-state lookup that never collapses: ``could_not_look`` (the
   read failed — we did not look) / ``not_found`` (a confirmed clean read holds
   no such order) / ``found``. An empty result is only ever ``not_found`` when
   the read itself succeeded.
3. Refuses two classes by default, because a cancel is irreversible:
   * a **protective** order (carries an ``oca_group``, or is a stop/trailing
     type) — cancelling it strips a live position's exit. ``--force-protective``
     overrides, deliberately verbose.
   * an order owned by a clientId **below the read range** (< 9000), i.e. the
     trader's own execution band. Connecting as that id would evict the
     trader's live IB session. ``--force-client-id`` overrides.
4. DRY-RUN by default. ``--apply`` connects as the owning clientId and cancels.
5. Re-reads account-wide and reports whether the order is actually gone — the
   same three-state, so "we could not confirm" never reads as "cancelled".

Never raises into the caller; every failure is reported as JSON.

Usage (on the live VM, via the ``cancel-ib-order`` system-action):
    python3 scripts/ops/cancel_ib_order.py --account ib_paper --order-id 6
    python3 scripts/ops/cancel_ib_order.py --account ib_paper --order-id 6 --apply
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

# Below this, a clientId belongs to the trader's execution band (496/497/498
# and their reconnect rotations). The read range starts at 9000 and the ops
# range at 9900 -- see clients._ib_read_client_id and
# flatten_ib_position._ib_ops_client_id. Taking over an execution id would
# evict the trader's live session, so it is refused rather than band-listed:
# an explicit numeric floor cannot drift as the rotation geometry changes.
_TRADER_CLIENT_ID_CEILING = 9000

# Order types that ARE the exit. Cancelling one of these strips a live
# position's protection, which is the opposite of the safety this tool exists
# to restore -- so it is a separate, louder override than the ordinary one.
_PROTECTIVE_ORDER_TYPES = {"STP", "STP LMT", "TRAIL", "TRAIL LIMIT", "STP PRT"}


def _is_protective(row: Dict[str, Any]) -> bool:
    """True when *row* is a protective leg rather than a working order.

    Two independent signals, either sufficient: an OCA group (a bracket leg is
    always OCA-grouped here -- see ``IBClient.place_protective``) or a
    stop/trailing order type. Deliberately over-inclusive: a false positive
    costs one explicit ``--force-protective`` flag, a false negative costs a
    live position its stop.
    """
    if str(row.get("oca_group") or "").strip():
        return True
    return str(row.get("order_type") or "").strip().upper() in _PROTECTIVE_ORDER_TYPES


def _find(rows: List[Dict[str, Any]], *, order_id: Optional[int],
          perm_id: Optional[int]) -> List[Dict[str, Any]]:
    """Every row matching the requested id. Returns a LIST, not a first match.

    A caller must be told when an id is ambiguous rather than silently handed
    one of several -- ``order_id`` is only unique per clientId, so an account
    holding orders from two clients genuinely can carry the same orderId twice.
    ``perm_id`` is IB's account-stable identifier and is the safer selector.
    """
    out = []
    for r in rows:
        if order_id is not None and r.get("order_id") is not None \
                and int(r["order_id"]) == order_id:
            out.append(r)
        elif perm_id is not None and r.get("perm_id") is not None \
                and int(r["perm_id"]) == perm_id:
            out.append(r)
    return out


def _load_account(account_id: str) -> Optional[Dict[str, Any]]:
    from src.units.ui.data_loaders import list_accounts

    for acc in list_accounts() or []:
        if (acc or {}).get("account_id") == account_id or (acc or {}).get("name") == account_id:
            return acc
    return None


def _read_orders(account_cfg: Dict[str, Any]) -> Optional[List[Dict[str, Any]]]:
    """Account-wide order rows, or ``None`` when we could not look."""
    from src.units.accounts.clients import ib_read_client_for

    client = ib_read_client_for(account_cfg)
    if client is None:
        return None
    try:
        return client.list_open_orders()
    except Exception:  # noqa: BLE001 -- a read failure is "could not look"
        return None


def _cancel_as_owner(account_cfg: Dict[str, Any], *, owner_client_id: int,
                     order_id: int) -> Dict[str, Any]:
    """Connect AS *owner_client_id* and cancel *order_id*.

    The account-wide read is issued first on this same connection so IB has
    delivered the client's own open orders into the session before
    :meth:`IBClient.cancel` resolves the target through ``openTrades()``.
    """
    from src.units.accounts.clients import ib_client_for

    client = ib_client_for(account_cfg, client_id=owner_client_id, readonly=False)
    if client is None:
        return {"retCode": 1, "retMsg": "could not build a client for the owning clientId"}
    try:
        client.list_open_orders()  # populate this session's order book
    except Exception:  # noqa: BLE001 -- best-effort priming; cancel still tries
        pass
    try:
        return client.cancel(str(order_id))
    except Exception as exc:  # noqa: BLE001
        return {"retCode": 1, "retMsg": f"{type(exc).__name__}: {exc}"}


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--account", default="ib_paper")
    ap.add_argument("--order-id", type=int, default=None)
    ap.add_argument("--perm-id", type=int, default=None)
    ap.add_argument("--apply", action="store_true",
                    help="actually cancel (default: dry-run)")
    ap.add_argument("--force-protective", action="store_true",
                    help="allow cancelling a stop / OCA-grouped protective leg")
    ap.add_argument("--force-client-id", action="store_true",
                    help="allow taking over a trader execution clientId (<9000)")
    args = ap.parse_args(argv)

    out: Dict[str, Any] = {
        "account": args.account,
        "order_id": args.order_id,
        "perm_id": args.perm_id,
        "apply": bool(args.apply),
    }

    if args.order_id is None and args.perm_id is None:
        out.update(lookup_state="not_requested",
                   error="one of --order-id / --perm-id is required")
        print(json.dumps(out, indent=2))
        return 2

    acc = _load_account(args.account)
    if acc is None:
        out.update(lookup_state="could_not_look",
                   error=f"account {args.account!r} not found in accounts.yaml")
        print(json.dumps(out, indent=2))
        return 2

    rows = _read_orders(acc)
    if rows is None:
        # NOT "no such order" -- we never reached the broker. Distinguishing
        # these is the whole point of the three-state; collapsing them would
        # let a gateway outage read as a successful cancel.
        out.update(lookup_state="could_not_look",
                   error="account-wide order read failed (gateway unreachable, "
                         "breaker open, or ib_port unset) -- this is NOT evidence "
                         "the order is absent")
        print(json.dumps(out, indent=2))
        return 3

    out["orders_on_account"] = len(rows)
    matches = _find(rows, order_id=args.order_id, perm_id=args.perm_id)
    if not matches:
        out.update(lookup_state="not_found",
                   note="confirmed clean account-wide read; no order with that id")
        print(json.dumps(out, indent=2))
        return 0
    if len(matches) > 1:
        out.update(lookup_state="ambiguous", matches=matches,
                   error="that id matches more than one order (orderId is unique "
                         "only per clientId) -- re-run with --perm-id")
        print(json.dumps(out, indent=2))
        return 2

    row = matches[0]
    out.update(lookup_state="found", order=row)

    owner = row.get("client_id")
    if owner is None:
        out["error"] = ("IB did not report a clientId for this order, so the "
                        "owning session is unknown and cancelOrder cannot be "
                        "addressed. This is 'not reported', not 'client 0'.")
        print(json.dumps(out, indent=2))
        return 3
    owner = int(owner)
    out["owner_client_id"] = owner

    blockers = []
    if _is_protective(row) and not args.force_protective:
        blockers.append(
            "order is PROTECTIVE (oca_group set, or a stop/trailing type) -- "
            "cancelling it would strip a live position's exit; pass "
            "--force-protective if that is genuinely intended")
    if owner < _TRADER_CLIENT_ID_CEILING and not args.force_client_id:
        blockers.append(
            f"owning clientId {owner} is in the trader's execution band "
            f"(<{_TRADER_CLIENT_ID_CEILING}); connecting as it would evict the "
            "trader's live IB session. The trader itself owns this order; pass "
            "--force-client-id only with the trader stopped")
    if blockers:
        out.update(action="refused", blockers=blockers)
        print(json.dumps(out, indent=2))
        return 4

    if not args.apply:
        out.update(action="dry_run",
                   would="connect as clientId %d and cancelOrder(%s)"
                         % (owner, row.get("order_id")))
        print(json.dumps(out, indent=2))
        return 0

    resp = _cancel_as_owner(acc, owner_client_id=owner,
                            order_id=int(row["order_id"]))
    out["cancel_response"] = resp
    # A VENUE REFUSAL is not the same failure as "we could not send it", and the
    # two want opposite follow-ups: a refusal is permanent and no wider verify
    # window fixes it, whereas a slow accept is exactly what a wider window is
    # for (BL-20260825-CANCEL-IB-ORDER-REPORTS-RETMSG-OK-WHILE-IBKR-REFUSED,
    # and see BL-20260817-CANCEL-IB-ORDER-VERIFY-WINDOW-TOO-SHORT for a run
    # where the slow-accept reading was the correct one). IBKR answers a
    # refusal on the error event only; IBClient.cancel now captures it.
    refusal = resp.get("refusal") if isinstance(resp, dict) else None
    if refusal:
        out.update(action="refused_by_venue", verify_state="still_present",
                   refusal=refusal,
                   note="IBKR REFUSED this cancel — the order is still resting "
                        "and re-running with a longer verify window will not "
                        "change that. Error 10147 specifically means the "
                        "submitting clientId no longer holds the order, which "
                        "no API client can override; clearing it needs TWS.")
        print(json.dumps(out, indent=2))
        return 1
    if resp.get("retCode") != 0:
        out.update(action="cancel_failed")
        print(json.dumps(out, indent=2))
        return 1

    after = _read_orders(acc)
    if after is None:
        # The cancel was accepted but we could not re-read. Say exactly that:
        # an accepted cancel is not a confirmed one.
        out.update(action="cancelled_unconfirmed", verify_state="could_not_look",
                   note="cancel was accepted but the verification read failed; "
                        "re-run the dry-run to confirm")
        print(json.dumps(out, indent=2))
        return 3
    still = _find(after, order_id=args.order_id, perm_id=args.perm_id)
    out["orders_on_account_after"] = len(after)
    out.update(action="cancelled" if not still else "cancel_not_effective",
               verify_state="gone" if not still else "still_present",
               remaining=still or None)
    print(json.dumps(out, indent=2))
    return 0 if not still else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
