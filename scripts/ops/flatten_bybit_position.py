#!/usr/bin/env python3
"""One-shot operator flatten of a single Bybit exchange position.

Why this exists
---------------
Until now the only remote position-closing capability was
``flatten-ib-position`` (Interactive Brokers only). A web/PM session that
needed to flatten a Bybit position had no path: the diag API is read-only, the
session can't egress to the exchange, and the system-actions allowlist had no
Bybit close. This is the guarded, one-shot Bybit sibling — built so the
autonomous operator can flatten a real-money Bybit position (e.g. to clear an
account before a different-account key rotation) without a human at the
exchange terminal.

What it does
------------
1. Reads the account's LIVE exchange position for ``--symbol`` via the bot's own
   read path (:func:`account_open_positions`) — the BROKER's truth, not the
   journal.
2. DRY-RUN by default: prints the live position + the close it WOULD place.
   ``--apply`` actually flattens.
3. On ``--apply``: places the close through the bot's unified
   :func:`close_open_position` (for Bybit: a **reduce-only** market order on the
   opposite side, sized to the live exchange qty). ``reduceOnly=True`` is the
   safety property — the order can only shrink the position to flat, never flip
   it. Unlike IB there is no resting bracket to cancel first: Bybit SL/TP are
   position-attached conditional orders that clear automatically when the
   position closes.
4. Re-reads the position and reports whether it is flat.

The journal row is left for the trader's reconciler to close-on-disappear once
the broker reads flat — this script's single responsibility is the BROKER-side
flatten. It is best-effort and never raises into the caller; a connect/read/
close failure is reported, not thrown.

Usage (on the live VM, via the ``flatten-bybit-position`` system-action):
    python3 scripts/ops/flatten_bybit_position.py --account bybit_2 --symbol BTCUSDT
    python3 scripts/ops/flatten_bybit_position.py --account bybit_2 --symbol BTCUSDT --apply
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, Optional

# The script lives in scripts/ops/; the repo root is two levels up. Add it to
# sys.path so `from src...` resolves when the wrapper invokes this by absolute
# path (system python3, cwd != repo root) — mirrors flatten_ib_position.py.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)


def _load_account(account_id: str) -> Optional[Dict[str, Any]]:
    """Return the accounts.yaml block for *account_id* (or None).

    Uses ``list_accounts`` — the same per-account dict shape that
    ``account_open_positions`` / ``bybit_client_for`` consume (carries
    ``exchange`` / ``demo`` / ``market_type`` / ``api_key_env`` + creds).
    """
    from src.units.ui.data_loaders import list_accounts

    for acc in list_accounts() or []:
        if (acc or {}).get("account_id") == account_id or (acc or {}).get("name") == account_id:
            return acc
    return None


def _build_ops_client(account_cfg: Dict[str, Any]):
    """Build a place-capable Bybit HTTP client. Thin indirection (lazy import)
    so callers/tests can patch it cleanly. Bybit has no per-socket clientId
    concept (the IB constraint), so this is just the standard factory; the
    ``demo`` flag in the account routes the demo vs mainnet endpoint."""
    from src.units.accounts.clients import bybit_client_for

    return bybit_client_for(account_cfg)


def _place_close(client, account_cfg: Dict[str, Any], *, symbol: str, side: str, qty: float) -> Dict[str, Any]:
    """Place the unified close (Bybit: reduce-only opposing market order).
    Thin indirection (lazy import) for testability."""
    from src.units.accounts.execute import close_open_position

    return close_open_position(client, account_cfg, symbol=symbol, side=side, qty=qty)


def _live_position(account_cfg: Dict[str, Any], symbol: str) -> Optional[Dict[str, Any]]:
    """Read the live exchange position for *symbol*. Returns None on
    could-not-read, ``{}`` when the read succeeded but there is no matching
    position (flat), or the matching row dict otherwise."""
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
    if exchange != "bybit":
        out["detail"] = f"account {account_id!r} is exchange={exchange!r}, not Bybit — refusing"
        return out

    pos = _live_position(account_cfg, symbol)
    if pos is None:
        out["action"] = "abort_unreadable"
        out["detail"] = ("could not read the live Bybit position (missing creds / "
                         "API error / unmatched-IP) — refusing to act blind")
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
                            "note": "reduce-only opposing market order clamped to "
                                    "live qty (Bybit SL/TP clear with the position)"}

    if not apply:
        out["action"] = "dry_run"
        out["ok"] = True
        out["detail"] = (f"DRY-RUN — would {close_side_word} {size} {symbol} (reduce-only) to "
                         f"flatten the {canonical} on {account_id}. Re-run with --apply to execute.")
        return out

    # --apply: build a place-capable client and flatten.
    client = _build_ops_client(account_cfg)
    if client is None:
        out["action"] = "abort_no_client"
        out["detail"] = "bybit_client_for returned None (missing creds?) — cannot place the close"
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
                         "verify via the trader / diag before assuming flat")
    else:
        out["action"] = "close_placed_still_open"
        out["ok"] = False
        out["detail"] = (f"close placed but {symbol} still shows size "
                         f"{after.get('size')} — re-run --apply or check the account")
    return out


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description="One-shot guarded flatten of one Bybit position.")
    ap.add_argument("--account", default="bybit_2", help="account_id in accounts.yaml (default bybit_2)")
    ap.add_argument("--symbol", required=True, help="bot symbol to flatten, e.g. BTCUSDT")
    ap.add_argument("--apply", action="store_true", help="actually place the close (default: dry-run)")
    args = ap.parse_args(argv)

    result = flatten(args.account, args.symbol, apply=args.apply)
    print(json.dumps(result, indent=2, default=str))
    # exit 0 on success/clean-noop/dry-run; 1 on any refusal/failure so the
    # wrapper + system-action surface a non-zero status.
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
