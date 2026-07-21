#!/usr/bin/env python3
"""Backfill ``trades.sl_order_id``/``tp_order_id`` for an already-open Bybit
partial-tpsl position (BL-20260721-BYBIT2-XRP-TPSL-LEGCAP structural-fix gap).

Why this exists
----------------
PR #7321's structural fix captures a trade's Bybit Partial-tpsl leg id(s) at
ENTRY time via a before/after snapshot diff in ``execute_pkg``. It has no way
to retroactively attach an id to a trade that was ALREADY OPEN when the fix
deployed — those rows keep ``sl_order_id``/``tp_order_id`` NULL forever, so
``modify_open_order`` keeps falling back to the legacy add-a-leg
``set_trading_stop`` path for them, and they can keep re-accumulating
duplicate legs exactly like before the fix. Live-confirmed 2026-07-21:
bybit_2/XRPUSDT (open since before the fix deployed) kept adding new legs for
hours after PR #7321 shipped, because its trade row was never re-opened.

What it does
------------
For one ``(account_id, symbol)``:

1. Confirms the live Bybit position is non-flat (refuses on flat/unreadable
   — nothing to backfill).
2. Lists the symbol's live conditional (StopOrder) legs, splits SL/TP by
   ``stopOrderType``, exactly like ``cancel_stale_tpsl_legs.py``.
3. Refuses if there is more than one live SL leg or more than one live TP
   leg — ambiguous, mirroring ``execute_pkg``'s own entry-time capture logic
   (0-or->1 new legs of a type => leave untracked rather than risk
   mis-attribution). Run ``cancel-stale-tpsl-legs`` first to get down to
   exactly one of each.
4. Finds the open, non-backtest ``trades`` row(s) for ``(account_id,
   symbol)`` with ``status='open'`` AND (``sl_order_id`` IS NULL OR
   ``tp_order_id`` IS NULL). Refuses if more than one such row exists —
   ambiguous which trade the single live leg pair belongs to.
5. Writes the found SL/TP order id(s) onto that one trade row — only the
   columns that are currently NULL and have a corresponding live leg; never
   overwrites an already-populated column.
6. Dry-run by default; ``--apply`` performs the write.

Read-only against the broker (only ``get_open_orders``); the only DB write is
the two new columns on the one matched row. Never touches order routing, SL/TP
prices, or any other row/symbol.

Usage (on the live VM, via the ``backfill-tpsl-leg-ids`` system-action):
    python3 scripts/ops/backfill_tpsl_leg_ids.py --account bybit_2 --symbol XRPUSDT
    python3 scripts/ops/backfill_tpsl_leg_ids.py --account bybit_2 --symbol XRPUSDT --apply
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
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


def _summarize(order: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "orderId": order.get("orderId"),
        "stopOrderType": order.get("stopOrderType"),
        "qty": order.get("qty"),
        "triggerPrice": order.get("triggerPrice"),
        "orderStatus": order.get("orderStatus"),
        "createdTime": order.get("createdTime"),
    }


def backfill_leg_ids(account_id: str, symbol: str, *, apply: bool,
                      db_path: Optional[str] = None) -> Dict[str, Any]:
    """Core routine. Returns a structured result dict (never raises)."""
    from src.utils.paths import trade_journal_db_path

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
        out["detail"] = f"live {symbol} position on {account_id} is flat — nothing to backfill"
        return out
    out["live_position_size"] = size

    client = _build_client(account_cfg)
    if client is None:
        out["action"] = "abort_no_client"
        out["detail"] = "bybit_client_for returned None (missing creds?)"
        return out
    category = _category(account_cfg)

    legs = _stop_orders(client, category, symbol)
    sl_legs = [o for o in legs if _leg_group(o) == "sl"]
    tp_legs = [o for o in legs if _leg_group(o) == "tp"]
    out["legs_found"] = {"sl": [_summarize(o) for o in sl_legs],
                          "tp": [_summarize(o) for o in tp_legs]}

    if len(sl_legs) > 1 or len(tp_legs) > 1:
        out["action"] = "abort_ambiguous_legs"
        out["detail"] = (f"found {len(sl_legs)} SL leg(s) + {len(tp_legs)} TP leg(s) for {symbol} on "
                          f"{account_id} — need exactly one of each to attribute unambiguously. Run "
                          "cancel-stale-tpsl-legs first, then retry.")
        return out
    if not sl_legs and not tp_legs:
        out["action"] = "abort_no_legs"
        out["detail"] = (f"no live conditional legs found for {symbol} on {account_id} — nothing "
                          "to backfill (position may be naked; needs a look).")
        return out

    sl_order_id = sl_legs[0].get("orderId") if sl_legs else None
    tp_order_id = tp_legs[0].get("orderId") if tp_legs else None

    resolved_db_path = db_path or str(trade_journal_db_path())
    conn = sqlite3.connect(resolved_db_path)
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, symbol, account_id, status, sl_order_id, tp_order_id, is_backtest "
            "FROM trades WHERE account_id = ? AND symbol = ? AND status = 'open' AND "
            "(sl_order_id IS NULL OR tp_order_id IS NULL) AND "
            "(is_backtest IS NULL OR is_backtest = 0)",
            (account_id, symbol),
        ).fetchall()
        out["candidate_trades"] = [dict(r) for r in rows]

        if not rows:
            out["action"] = "noop_no_candidate_rows"
            out["ok"] = True
            out["detail"] = (f"no open, non-backtest {symbol} trade on {account_id} is missing "
                              "sl_order_id/tp_order_id — nothing to backfill (already tracked, or "
                              "no matching open row).")
            return out
        if len(rows) > 1:
            out["action"] = "abort_ambiguous_trades"
            out["detail"] = (f"found {len(rows)} open untracked {symbol} trade rows on {account_id} "
                              "— can't attribute the single live leg pair to one of them "
                              "unambiguously. Needs a manual look.")
            return out

        trade = rows[0]
        updates: Dict[str, str] = {}
        if sl_order_id and trade["sl_order_id"] is None:
            updates["sl_order_id"] = str(sl_order_id)
        if tp_order_id and trade["tp_order_id"] is None:
            updates["tp_order_id"] = str(tp_order_id)

        out["plan"] = {"trade_id": trade["id"], "updates": updates}

        if not updates:
            out["action"] = "noop_already_tracked"
            out["ok"] = True
            out["detail"] = (f"trade id={trade['id']} already has both columns populated or no "
                              "matching live leg to attach.")
            return out

        if not apply:
            out["action"] = "dry_run"
            out["ok"] = True
            out["detail"] = (f"DRY-RUN — would write {updates} onto trade id={trade['id']}. "
                              "Re-run with --apply to write.")
            return out

        assignments = ", ".join(f"{k} = ?" for k in updates)
        conn.execute(
            f"UPDATE trades SET {assignments} WHERE id = ? AND status = 'open'",
            list(updates.values()) + [trade["id"]],
        )
        conn.commit()

        after = conn.execute(
            "SELECT id, sl_order_id, tp_order_id FROM trades WHERE id = ?",
            (trade["id"],),
        ).fetchone()
        out["post_state"] = dict(after) if after else None
        out["action"] = "backfilled"
        out["ok"] = True
        out["detail"] = (f"wrote {updates} onto trade id={trade['id']}; post-state confirms "
                          f"{dict(after) if after else None}.")
        return out
    finally:
        conn.close()


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(
        description="One-shot backfill of trades.sl_order_id/tp_order_id for an already-open "
                    "Bybit partial-tpsl position.")
    ap.add_argument("--account", default="bybit_2", help="account_id in accounts.yaml (default bybit_2)")
    ap.add_argument("--symbol", required=True, help="bot symbol to backfill, e.g. XRPUSDT")
    ap.add_argument("--apply", action="store_true", help="actually write the DB row (default: dry-run)")
    ap.add_argument("--db", default=None, help="trade_journal.db path override (default: canonical resolver)")
    args = ap.parse_args(argv)

    result = backfill_leg_ids(args.account, args.symbol, apply=args.apply, db_path=args.db)
    print(json.dumps(result, indent=2, default=str))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
