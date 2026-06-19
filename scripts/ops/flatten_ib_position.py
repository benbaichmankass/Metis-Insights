#!/usr/bin/env python3
"""One-shot operator flatten of a single IB exchange position.

Why this exists
---------------
The 2026-06-17 reconciler dup-adoption incident (BL-20260618-RECONCILE-DUP)
left a real, oversized position on the ``ib_paper`` IB gateway — a −232 MGC
short (paper account ``DUQ325724``). The bot's intent layer *wants* to close it
but the IBKR-futures reduce path refuses (BL-20260619-MGC-REDUCE-GUARD), so it
cannot self-clean, and the stale journal row drives an hourly
``intent_reduce_requires_derivatives`` rejection cluster. This script is the
guarded, one-shot flatten the operator approved (2026-06-19).

What it does
------------
1. Reads the account's LIVE exchange position for ``--symbol`` via the bot's own
   read path (``account_open_positions`` — a process-unique READ clientId).
2. DRY-RUN by default: prints the live position + the close it WOULD place.
   ``--apply`` actually flattens.
3. On ``--apply``: places the close through the bot's unified
   ``close_open_position`` (for IB: cancel the resting protective bracket/OCA
   legs, then an opposing reduce market order **clamped to the live exchange
   qty** so it can never flip), using a process-unique OPS clientId distinct
   from BOTH the trader's execution clientId (496/497) AND the read range
   (9000–9899) — so it never races or is rejected as "clientId already in use".
4. Re-reads the position and reports whether it is flat.

The journal row is left for the trader's reconciler to close-on-disappear
(2-observation confirm) once the broker reads flat — this script's single
responsibility is the BROKER-side flatten. It is best-effort and never raises
into the caller; a connect/read/close failure is reported, not thrown.

Usage (on the live VM, via the ``flatten-ib-position`` system-action):
    python3 scripts/ops/flatten_ib_position.py --account ib_paper --symbol MGC
    python3 scripts/ops/flatten_ib_position.py --account ib_paper --symbol MGC --apply
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, Optional


def _ib_ops_client_id() -> int:
    """A place-capable, process-unique IB clientId for one-shot ops flattens.

    Distinct from the trader's execution clientId (496/497) AND the read range
    (:func:`src.units.accounts.clients._ib_read_client_id`, 9000–9899), so an
    ops flatten can never be rejected as "clientId already in use" nor race the
    live execution socket. Stable within one process (registry reuses one
    socket); salted by PID so two ops runs don't collide.
    """
    return 9900 + (os.getpid() % 90)


def _load_account(account_id: str) -> Optional[Dict[str, Any]]:
    """Return the accounts.yaml block for *account_id* (or None).

    Uses ``list_accounts`` — the same per-account dict shape that
    ``account_open_positions`` / ``ib_client_for`` consume (carries
    ``ib_host`` / ``ib_port`` / ``ib_client_id`` / ``account`` + creds).
    """
    from src.units.ui.data_loaders import list_accounts

    for acc in list_accounts() or []:
        if (acc or {}).get("account_id") == account_id or (acc or {}).get("name") == account_id:
            return acc
    return None


def _build_ops_client(account_cfg: Dict[str, Any]):
    """Build a place-capable IBClient with a process-unique OPS clientId.
    Thin indirection (lazy import) so callers/tests can patch it cleanly."""
    from src.units.accounts.clients import ib_client_for

    return ib_client_for(account_cfg, client_id=_ib_ops_client_id(), readonly=False)


def _place_close(client, account_cfg: Dict[str, Any], *, symbol: str, side: str, qty: float) -> Dict[str, Any]:
    """Place the unified close (IB: cancel resting bracket + clamped opposing
    reduce market order). Thin indirection (lazy import) for testability."""
    from src.units.accounts.execute import close_open_position

    return close_open_position(client, account_cfg, symbol=symbol, side=side, qty=qty)


def _live_position(account_cfg: Dict[str, Any], symbol: str) -> Optional[Dict[str, Any]]:
    """Read the live exchange position for *symbol* (READ clientId). None on
    could-not-read; {} sentinel is not used — returns the matching row dict or
    None if flat / unreadable. Distinguish via the ``_read_ok`` flag."""
    from src.units.ui.data_loaders import account_open_positions

    rows = account_open_positions(account_cfg)
    if rows is None:
        return None  # could-not-read
    for r in rows:
        if str(r.get("symbol") or "").upper() == symbol.upper():
            return r
    return {}  # read OK, no matching position → flat


def flatten(account_id: str, symbol: str, *, apply: bool) -> Dict[str, Any]:
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
    if exchange not in ("interactive_brokers", "ib"):
        out["detail"] = f"account {account_id!r} is exchange={exchange!r}, not IB — refusing"
        return out

    pos = _live_position(account_cfg, symbol)
    if pos is None:
        out["action"] = "abort_unreadable"
        out["detail"] = ("could not read the live IB position (logged-out gateway / "
                         "missing creds / SDK error) — refusing to act blind")
        return out
    if not pos:
        out["action"] = "noop_already_flat"
        out["ok"] = True
        out["detail"] = f"no live {symbol} position on {account_id} — nothing to flatten"
        return out

    size = abs(float(pos.get("size") or 0.0))
    side = str(pos.get("side") or "").lower()  # 'long'/'short' (or buy/sell)
    canonical = {"buy": "long", "long": "long", "sell": "short", "short": "short"}.get(side, side)
    out["live_position"] = {"side": canonical, "size": size,
                            "entry_price": pos.get("entry_price"),
                            "unrealised_pnl": pos.get("unrealised_pnl")}
    if size <= 0:
        out["action"] = "noop_already_flat"
        out["ok"] = True
        out["detail"] = f"live {symbol} size is 0 — nothing to flatten"
        return out

    close_side_word = "BUY" if canonical == "short" else "SELL"
    out["planned_close"] = {"action": close_side_word, "qty": size,
                            "note": "cancel resting protective orders, then opposing "
                                    "reduce market order clamped to live qty"}

    if not apply:
        out["action"] = "dry_run"
        out["ok"] = True
        out["detail"] = (f"DRY-RUN — would {close_side_word} {size} {symbol} to flatten the "
                         f"{canonical} on {account_id}. Re-run with --apply to execute.")
        return out

    # --apply: build a place-capable OPS client (distinct clientId) and flatten.
    client = _build_ops_client(account_cfg)
    if client is None:
        out["action"] = "abort_no_client"
        out["detail"] = "ib_client_for returned None (no ib_port?) — cannot place the close"
        return out

    res = _place_close(client, account_cfg, symbol=symbol, side=canonical, qty=size)
    out["close_result"] = res
    if not res.get("ok"):
        out["action"] = "close_failed"
        out["detail"] = f"close_open_position refused/failed: {res.get('error')}"
        return out

    # Verify flat (re-read).
    after = _live_position(account_cfg, symbol)
    if after is not None and not after:
        out["action"] = "flattened"
        out["ok"] = True
        out["detail"] = (f"flattened {symbol} on {account_id}; broker reads flat. The trader's "
                         f"reconciler will close the journal row on close-on-disappear.")
    elif after is None:
        out["action"] = "flattened_unverified"
        out["ok"] = True
        out["detail"] = ("close placed OK but the post-flatten read came back unreadable; "
                         "verify via the trader before assuming flat")
    else:
        out["action"] = "close_placed_still_open"
        out["ok"] = False
        out["detail"] = (f"close placed but {symbol} still shows size "
                         f"{after.get('size')} — re-run --apply or check the gateway")
    return out


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description="One-shot guarded flatten of one IB position.")
    ap.add_argument("--account", default="ib_paper", help="account_id in accounts.yaml (default ib_paper)")
    ap.add_argument("--symbol", required=True, help="bot symbol to flatten, e.g. MGC")
    ap.add_argument("--apply", action="store_true", help="actually place the close (default: dry-run)")
    args = ap.parse_args(argv)

    result = flatten(args.account, args.symbol, apply=args.apply)
    print(json.dumps(result, indent=2, default=str))
    # exit 0 on success/clean-noop/dry-run; 1 on any refusal/failure so the
    # wrapper + system-action can surface a non-zero status.
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
