#!/usr/bin/env python3
"""One-shot operator cleanup of accumulated Partial-tpsl legs on one Bybit symbol.

Why this exists
---------------
BL-20260721-BYBIT2-XRP-TPSL-LEGCAP: under ``BYBIT_TPSL_MODE=partial``,
``modify_open_order`` (and the entry-time bracket) call Bybit's
``set_trading_stop(tpslMode=Partial)`` on every trailing-stop tick. Per
Bybit's own V5 docs, that call is documented as "can only ADD partial
position TP/SL orders" — unlike Full mode, it never amends an existing
partial leg in place. With no leg lifecycle management (nothing cancels a
strategy's old leg when a new one is added or a trade closes), legs
accumulate without bound until Bybit's 20-combined-leg-per-symbol cap
(ErrCode 110061) blocks ANY further amend — including a genuine protective
tightening, which then silently fails (order_monitor logs the error and
leaves the DB/live stop unchanged).

Live-confirmed 2026-07-21: bybit_2 XRPUSDT accumulated 23 legs, all sharing
the qty of the account's single real position — i.e., duplicate/stale legs
stacked on one position, not separate positions.

What it does
------------
This is a STOPGAP to relieve the cap, not the structural fix (see
BL-20260721-BYBIT2-XRP-TPSL-LEGCAP's tracked follow-up for the per-leg
order-ID-tracking + amend-in-place design). It:

1. Lists the symbol's live conditional (StopOrder-filtered) orders on the
   account via the bot's own Bybit client factory.
2. Splits them into SL legs and TP legs (by ``stopOrderType``).
3. Within each group, keeps the MOST RECENTLY created leg (the newest
   trailing-stop/take-profit level is the one the strategy currently
   intends — earlier legs are stale duplicates from prior ticks) and marks
   every older leg in that group as a cancel candidate.
4. DRY-RUN by default: prints every leg found + exactly what would be kept
   vs cancelled. ``--apply`` actually cancels the stale legs.
5. Refuses to run if there are zero SL legs (the position may already be
   naked — cancelling further would be pointless and this needs a human
   look, not this script) or if the live position is flat (nothing to
   protect; touching orphaned orders on a flat symbol is out of scope here
   — use the reconciler / a manual review instead).
6. Re-reads the leg list after an apply and reports the post-state so the
   caller can confirm exactly one SL (and at most one TP) leg remains.

Never cancels the leg(s) it decided to KEEP. Best-effort per-cancel (one
failed cancel doesn't abort the rest); every raw response is reported.

Usage (on the live VM, via the ``cancel-stale-tpsl-legs`` system-action):
    python3 scripts/ops/cancel_stale_tpsl_legs.py --account bybit_2 --symbol XRPUSDT
    python3 scripts/ops/cancel_stale_tpsl_legs.py --account bybit_2 --symbol XRPUSDT --apply
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

_SL_TYPES = {"stoploss", "partialstoploss"}
_TP_TYPES = {"takeprofit", "partialtakeprofit"}


def _load_account(account_id: str) -> Optional[Dict[str, Any]]:
    from src.units.ui.data_loaders import list_accounts

    for acc in list_accounts() or []:
        if (acc or {}).get("account_id") == account_id or (acc or {}).get("name") == account_id:
            return acc
    return None


def _build_client(account_cfg: Dict[str, Any]):
    from src.units.accounts.clients import bybit_client_for

    return bybit_client_for(account_cfg)


def _category(account_cfg: Dict[str, Any]) -> str:
    from src.units.accounts.execute import _bybit_category

    return _bybit_category(account_cfg)


def _live_position_size(account_cfg: Dict[str, Any], symbol: str) -> Optional[float]:
    """Returns live size (0.0 if flat), or None if unreadable."""
    from src.units.ui.data_loaders import account_open_positions

    rows = account_open_positions(account_cfg)
    if rows is None:
        return None
    for r in rows:
        if str(r.get("symbol") or "").upper() == symbol.upper():
            try:
                return abs(float(r.get("size") or 0.0))
            except (TypeError, ValueError):
                return 0.0
    return 0.0


def _stop_orders(client, category: str, symbol: str) -> List[Dict[str, Any]]:
    resp = client.get_open_orders(category=category, symbol=symbol, orderFilter="StopOrder")
    return ((resp or {}).get("result") or {}).get("list") or []


def _leg_group(order: Dict[str, Any]) -> Optional[str]:
    kind = str(order.get("stopOrderType", "")).lower()
    if kind in _SL_TYPES:
        return "sl"
    if kind in _TP_TYPES:
        return "tp"
    return None


def _created_ms(order: Dict[str, Any]) -> int:
    try:
        return int(order.get("createdTime") or 0)
    except (TypeError, ValueError):
        return 0


def _summarize(order: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "orderId": order.get("orderId"),
        "stopOrderType": order.get("stopOrderType"),
        "qty": order.get("qty"),
        "triggerPrice": order.get("triggerPrice"),
        "orderStatus": order.get("orderStatus"),
        "createdTime": order.get("createdTime"),
    }


def cancel_stale_legs(account_id: str, symbol: str, *, apply: bool) -> Dict[str, Any]:
    """Core routine. Returns a structured result dict (never raises)."""
    symbol = symbol.upper()
    out: Dict[str, Any] = {
        "account_id": account_id, "symbol": symbol, "apply": apply,
        "action": None, "ok": False, "detail": None,
    }

    account_cfg = _load_account(account_id)
    if not account_cfg:
        out["detail"] = f"account {account_id!r} not found in accounts.yaml"
        return out
    exchange = str(account_cfg.get("exchange") or "").lower()
    if exchange != "bybit":
        out["detail"] = f"account {account_id!r} is exchange={exchange!r}, not Bybit — refusing"
        return out

    size = _live_position_size(account_cfg, symbol)
    if size is None:
        out["action"] = "abort_unreadable"
        out["detail"] = "could not read the live Bybit position — refusing to act blind"
        return out
    if size <= 0:
        out["action"] = "abort_flat"
        out["detail"] = (f"live {symbol} position on {account_id} is flat (size=0) — "
                         "out of scope for this script; nothing to protect")
        return out
    out["live_position_size"] = size

    client = _build_client(account_cfg)
    if client is None:
        out["action"] = "abort_no_client"
        out["detail"] = "bybit_client_for returned None (missing creds?)"
        return out
    category = _category(account_cfg)

    legs = _stop_orders(client, category, symbol)
    groups: Dict[str, List[Dict[str, Any]]] = {"sl": [], "tp": []}
    unclassified: List[Dict[str, Any]] = []
    for leg in legs:
        g = _leg_group(leg)
        if g is None:
            unclassified.append(leg)
            continue
        groups[g].append(leg)

    out["legs_found"] = {
        "sl": [_summarize(o) for o in groups["sl"]],
        "tp": [_summarize(o) for o in groups["tp"]],
        "unclassified": [_summarize(o) for o in unclassified],
    }

    if not groups["sl"]:
        out["action"] = "abort_no_sl_legs"
        out["detail"] = (f"ZERO SL legs found for {symbol} on {account_id} while the position "
                         f"is live (size={size}) — the position may already be NAKED. Refusing "
                         "to cancel anything; this needs an immediate manual look "
                         "(naked-position auto-protect is IB-only, does not cover Bybit).")
        return out

    def _keep_and_stale(group: List[Dict[str, Any]]):
        if not group:
            return None, []
        ordered = sorted(group, key=_created_ms, reverse=True)
        return ordered[0], ordered[1:]

    keep_sl, stale_sl = _keep_and_stale(groups["sl"])
    keep_tp, stale_tp = _keep_and_stale(groups["tp"])
    stale = stale_sl + stale_tp

    out["plan"] = {
        "keep_sl": _summarize(keep_sl) if keep_sl else None,
        "keep_tp": _summarize(keep_tp) if keep_tp else None,
        "cancel": [_summarize(o) for o in stale],
    }

    if not stale:
        out["action"] = "noop_already_clean"
        out["ok"] = True
        out["detail"] = (f"{symbol} on {account_id} has {len(groups['sl'])} SL leg(s) + "
                         f"{len(groups['tp'])} TP leg(s), no duplicates to cancel.")
        return out

    if not apply:
        out["action"] = "dry_run"
        out["ok"] = True
        out["detail"] = (f"DRY-RUN — would cancel {len(stale)} stale leg(s) for {symbol} on "
                         f"{account_id}, keeping the most-recent SL"
                         f"{' + TP' if keep_tp else ''}. Re-run with --apply to execute.")
        return out

    cancel_results = []
    for leg in stale:
        oid = leg.get("orderId")
        try:
            resp = client.cancel_order(category=category, symbol=symbol, orderId=oid)
            ret_code = (resp or {}).get("retCode")
            cancel_results.append({"orderId": oid, "ok": ret_code in (0, "0", None),
                                   "retCode": ret_code, "retMsg": (resp or {}).get("retMsg")})
        except Exception as exc:  # noqa: BLE001
            cancel_results.append({"orderId": oid, "ok": False, "error": str(exc)})
    out["cancel_results"] = cancel_results

    after = _stop_orders(client, category, symbol)
    after_sl = [o for o in after if _leg_group(o) == "sl"]
    after_tp = [o for o in after if _leg_group(o) == "tp"]
    out["post_state"] = {"sl_count": len(after_sl), "tp_count": len(after_tp),
                         "sl": [_summarize(o) for o in after_sl],
                         "tp": [_summarize(o) for o in after_tp]}

    n_failed = sum(1 for r in cancel_results if not r.get("ok"))
    if not after_sl:
        out["action"] = "cancel_left_naked"
        out["ok"] = False
        out["detail"] = ("CRITICAL: post-cancel re-read shows ZERO SL legs remaining — the "
                         "position may be naked. Investigate immediately.")
    elif n_failed:
        out["action"] = "partial_cancel"
        out["ok"] = True
        out["detail"] = (f"cancelled {len(stale) - n_failed}/{len(stale)} stale legs "
                         f"({n_failed} failed — see cancel_results); {len(after_sl)} SL leg(s) "
                         f"remain live.")
    else:
        out["action"] = "cancelled"
        out["ok"] = True
        out["detail"] = (f"cancelled {len(stale)} stale leg(s); {len(after_sl)} SL leg(s) + "
                         f"{len(after_tp)} TP leg(s) remain live for {symbol} on {account_id}.")
    return out


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(
        description="One-shot guarded cleanup of accumulated Partial-tpsl legs on one Bybit symbol.")
    ap.add_argument("--account", default="bybit_2", help="account_id in accounts.yaml (default bybit_2)")
    ap.add_argument("--symbol", required=True, help="bot symbol to clean up, e.g. XRPUSDT")
    ap.add_argument("--apply", action="store_true", help="actually cancel the stale legs (default: dry-run)")
    args = ap.parse_args(argv)

    result = cancel_stale_legs(args.account, args.symbol, apply=args.apply)
    print(json.dumps(result, indent=2, default=str))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
