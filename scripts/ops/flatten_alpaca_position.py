#!/usr/bin/env python3
"""One-shot operator flatten of a single Alpaca exchange position.

Why this exists
---------------
Until now the remote position-closing actions were ``flatten-ib-position``
(Interactive Brokers) and ``flatten-bybit-position`` (Bybit) — **Alpaca had
none**, even though ``alpaca_live`` is a real-money account. A web/PM session
that needed to flatten an Alpaca position had no sanctioned path: the diag API
is read-only, the session can't egress to the broker, and the live VM only
runs the fixed system-actions allowlist (no arbitrary exec). This is the
guarded, one-shot Alpaca sibling.

The specific gap it closes (BL-20260708-ALPACA-CLOSE-QTY-AVAILABLE): an Alpaca
long carries a resting protective **bracket** (stop-loss + take-profit sell
legs). Those open sell orders reserve the shares as ``held_for_orders``, so the
position's ``qty_available`` is 0 and a naive sell — including the operator's
own sell in the Alpaca app — is rejected with *"insufficient qty available for
order (requested: N, available: 0)"*. You cannot sell shares locked by another
open order. The fix is cancel-then-flatten, which is exactly what
:meth:`AlpacaClient.close` does: it cancels the resting bracket legs, polls
until ``qty_available`` actually releases, then market-closes the whole
position (``DELETE /v2/positions/{symbol}``; a 404 = already-flat = ok).

What it does
------------
1. Reads the account's LIVE exchange position for ``--symbol`` via the bot's own
   read path (:func:`account_open_positions`) — the BROKER's truth, not the
   journal.
2. DRY-RUN by default: prints the live position + the close it WOULD place.
   ``--apply`` actually flattens.
3. On ``--apply``: places the close through the bot's unified
   :func:`close_open_position` (for Alpaca: :meth:`AlpacaClient.close`, the
   native qty-available-gated flatten described above — cancels the reserving
   bracket first, so it can never be blocked by ``held_for_orders``).
4. Re-reads the position and reports whether it is flat.

The journal row is left for the trader's reconciler to close-on-disappear once
the broker reads flat — this script's single responsibility is the BROKER-side
flatten. It is best-effort and never raises into the caller; a connect/read/
close failure is reported, not thrown.

**Market hours:** Alpaca's position-close submits a MARKET order, which the
broker rejects outside regular US equity hours (09:30–16:00 ET / 13:30–20:00
UTC). Run ``--apply`` during RTH; a dry-run is safe any time.

Usage (on the live VM, via the ``flatten-alpaca-position`` system-action):
    python3 scripts/ops/flatten_alpaca_position.py --account alpaca_live --symbol IEF
    python3 scripts/ops/flatten_alpaca_position.py --account alpaca_live --symbol IEF --apply
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, Optional

# The script lives in scripts/ops/; the repo root is two levels up. Add it to
# sys.path so `from src...` resolves when the wrapper invokes this by absolute
# path (system python3, cwd != repo root) — mirrors flatten_bybit_position.py.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)


def _load_account(account_id: str) -> Optional[Dict[str, Any]]:
    """Return the accounts.yaml block for *account_id* (or None).

    Uses ``list_accounts`` — the same per-account dict shape that
    ``account_open_positions`` / ``alpaca_client_for`` consume (carries
    ``exchange`` / ``alpaca_env`` / ``api_key_env`` + creds).
    """
    from src.units.ui.data_loaders import list_accounts

    for acc in list_accounts() or []:
        if (acc or {}).get("account_id") == account_id or (acc or {}).get("name") == account_id:
            return acc
    return None


def _build_ops_client(account_cfg: Dict[str, Any]):
    """Build a place-capable Alpaca client. Thin indirection (lazy import) so
    callers/tests can patch it cleanly. ``alpaca_client_for`` pairs the
    account's OWN key env (paper vs live host via ``alpaca_env``) and returns
    ``None`` when creds are missing."""
    from src.units.accounts.clients import alpaca_client_for

    return alpaca_client_for(account_cfg)


def _place_close(client, account_cfg: Dict[str, Any], *, symbol: str, side: str, qty: float) -> Dict[str, Any]:
    """Place the unified close (Alpaca: AlpacaClient.close — the native,
    qty-available-gated flatten that cancels the reserving bracket first).
    Thin indirection (lazy import) for testability."""
    from src.units.accounts.execute import close_open_position

    return close_open_position(client, account_cfg, symbol=symbol, side=side, qty=qty)


def _live_position(account_cfg: Dict[str, Any], symbol: str) -> Optional[Dict[str, Any]]:
    """Read the live exchange position for *symbol* DIRECTLY off the Alpaca
    client. Returns None on could-not-read, ``{}`` when the read succeeded but
    there is no matching position (flat), or the normalized row otherwise.

    Deliberately does NOT go through ``account_open_positions``: that read path
    gates ``mode != "live"`` alpaca accounts to ``None`` (so the reverse
    reconciler never dials a dry account — clients.py:1321). A one-shot operator
    flatten is a MODE-AGNOSTIC ops action — it must be able to read + close a
    position on a ``dry_run``-shelved account (the exact case here: alpaca_live
    was shelved to dry_run this session while still holding the IEF). The close
    path (``AlpacaClient.close``) is already mode-agnostic; this makes the read
    match. Normalizes the client's ``{side: buy/sell, qty, avg_price,
    unrealized_pnl}`` into the ``{side: long/short, size, entry_price,
    unrealised_pnl}`` shape the rest of this script consumes."""
    try:
        client = _build_ops_client(account_cfg)
        if client is None:
            return None  # could-not-read (creds unset)
        rows = client.positions()
        if rows is None:
            return None  # could-not-read (API error) — never treated as flat
        for r in rows:
            if str(r.get("symbol") or "").upper() == symbol.upper():
                side = str(r.get("side") or "").lower()  # buy/sell
                canonical = "long" if side in ("buy", "long") else "short"
                return {
                    "symbol": r.get("symbol"),
                    "side": canonical,
                    "size": r.get("qty") or r.get("size"),
                    "entry_price": r.get("avg_price") or r.get("entry_price"),
                    "unrealised_pnl": r.get("unrealized_pnl", r.get("unrealised_pnl")),
                }
        return {}  # read OK, no matching position → flat
    except Exception:  # noqa: BLE001 — best-effort, never raise into the caller
        return None


def _read_open_orders(account_cfg: Dict[str, Any], symbol: str) -> Dict[str, Any]:
    """Best-effort list of the account's OPEN orders for *symbol* (incl. bracket
    child legs) — diagnostics that reveal WHAT reserves the shares
    (``held_for_orders``): a resting SL/TP leg, its ``status`` / ``type`` /
    ``order_class`` / ``time_in_force``. Non-secret order metadata only. Never
    raises."""
    try:
        client = _build_ops_client(account_cfg)
        if client is None:
            return {"error": "no client (alpaca creds unset in env)"}
        rows = client._open_orders_for_symbol(symbol)  # type: ignore[attr-defined]
        if rows is None:
            return {"error": "open-orders read failed (None)"}
        keep = ("id", "symbol", "side", "type", "order_type", "order_class",
                "status", "qty", "filled_qty", "limit_price", "stop_price",
                "time_in_force", "extended_hours", "created_at", "expired_at",
                "canceled_at", "legs")
        summarized = []
        for o in rows:
            row = {k: o.get(k) for k in keep if k in o}
            if isinstance(row.get("legs"), list):
                row["legs"] = [{k: leg.get(k) for k in keep if k in leg}
                               for leg in row["legs"]]
            summarized.append(row)
        return {"count": len(summarized), "orders": summarized}
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"}


def _read_diagnostic(account_cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Why did the position read come back unreadable? Non-secret: reports which
    cred env-var NAMES the account resolves to and whether they are SET (never
    the values), plus whether a client built + what ``positions()`` did."""
    key_env = str(account_cfg.get("api_key_env") or "ALPACA_API_KEY_ID")
    secret_env = str(account_cfg.get("api_secret_env") or "ALPACA_API_SECRET_KEY")
    diag: Dict[str, Any] = {
        "key_env_name": key_env, "secret_env_name": secret_env,
        "key_env_set": bool(os.environ.get(key_env)),
        "secret_env_set": bool(os.environ.get(secret_env)),
        "alpaca_env": account_cfg.get("alpaca_env"),
    }
    try:
        client = _build_ops_client(account_cfg)
        diag["client_built"] = client is not None
        if client is not None:
            try:
                pos = client.positions()
                diag["positions_call"] = "ok" if pos is not None else "None (read failure)"
                diag["positions_count"] = None if pos is None else len(pos)
            except Exception as exc:  # noqa: BLE001
                diag["positions_call_error"] = f"{type(exc).__name__}: {exc}"
    except Exception as exc:  # noqa: BLE001
        diag["client_build_error"] = f"{type(exc).__name__}: {exc}"
    return diag


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
    if exchange != "alpaca":
        out["detail"] = f"account {account_id!r} is exchange={exchange!r}, not Alpaca — refusing"
        return out

    pos = _live_position(account_cfg, symbol)
    if pos is None:
        out["action"] = "abort_unreadable"
        out["detail"] = ("could not read the live Alpaca position (missing creds / "
                         "API error) — refusing to act blind")
        out["read_diagnostic"] = _read_diagnostic(account_cfg)
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
                            "note": "native Alpaca flatten (DELETE /v2/positions) — cancels the "
                                    "resting protective bracket that reserves the shares "
                                    "(held_for_orders), waits for qty_available, then market-closes "
                                    "the whole position. Rejected outside RTH (market order)."}

    if not apply:
        out["action"] = "dry_run"
        out["ok"] = True
        # Surface the resting orders that reserve the shares (the held_for_orders
        # cause of "insufficient qty available") + their status/type/tif, so a
        # stuck / un-cancellable SL/TP leg is visible before the apply.
        out["open_orders"] = _read_open_orders(account_cfg, symbol)
        out["detail"] = (f"DRY-RUN — would {close_side_word} {size} {symbol} (native flatten, "
                         f"cancels the reserving bracket first) on {account_id}. Re-run with "
                         f"--apply during RTH (13:30–20:00 UTC) to execute.")
        return out

    # --apply: build a place-capable client and flatten.
    client = _build_ops_client(account_cfg)
    if client is None:
        out["action"] = "abort_no_client"
        out["detail"] = "alpaca_client_for returned None (missing creds?) — cannot place the close"
        return out

    res = _place_close(client, account_cfg, symbol=symbol, side=canonical, qty=size)
    out["close_result"] = res
    if not res.get("ok"):
        out["action"] = "close_failed"
        out["detail"] = (f"close_open_position refused/failed: {res.get('error')} "
                         f"(a 'market is closed' / insufficient-qty error means retry during RTH)")
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
                         f"{after.get('size')} — re-run --apply (during RTH) or check the account")
    return out


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description="One-shot guarded flatten of one Alpaca position.")
    ap.add_argument("--account", default="alpaca_live", help="account_id in accounts.yaml (default alpaca_live)")
    ap.add_argument("--symbol", required=True, help="bot symbol to flatten, e.g. IEF")
    ap.add_argument("--apply", action="store_true", help="actually place the close (default: dry-run)")
    args = ap.parse_args(argv)

    result = flatten(args.account, args.symbol, apply=args.apply)
    print(json.dumps(result, indent=2, default=str))
    # exit 0 on success/clean-noop/dry-run; 1 on any refusal/failure so the
    # wrapper + system-action surface a non-zero status.
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
