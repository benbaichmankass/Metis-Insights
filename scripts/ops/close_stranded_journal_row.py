"""Close a STRANDED open journal row whose broker position is already flat.

Why this exists
---------------
When an Alpaca account is shelved to ``mode: dry_run`` (e.g. ``alpaca_live`` was
shelved 2026-07-15 while still holding an open IEF), the reverse reconciler can
no longer close its journal rows on close-on-disappear: ``account_open_positions``
gates a ``mode != "live"`` alpaca account to ``None`` ("could-not-read",
clients.py:1319-1323), so the reconciler's ``if positions is None: continue``
guard SKIPS the account entirely. If the operator then flattens the position
out-of-band (via ``flatten-alpaca-position``), the BROKER goes flat but the
``trades`` row stays ``status='open'`` forever — it keeps showing on
``/api/bot/positions`` (the dashboard + Android "open" list) even though nothing
is held.

This one-shot ops action closes that stranded row directly — but ONLY after a
MODE-AGNOSTIC broker read confirms the position is actually flat. It never
flips account mode (the account stays shelved) and it never touches the broker
(no order path) — it is a pure journal writeback gated on broker-confirmed-flat.

What it does
------------
1. Reads the account's LIVE exchange position for ``--symbol`` DIRECTLY off the
   Alpaca client (``flatten_alpaca_position._live_position`` — the mode-agnostic
   read, NOT ``account_open_positions``). This is the SAFETY GATE:
     * could-not-read (None)  → ABORT (never close a row blind).
     * a real position present → REFUSE (closing the row would orphan a live
       position — the exact bug this guards against).
     * flat ({}) → proceed.
2. Finds the open ``trades`` rows for ``(account_id, symbol)`` (non-backtest).
3. DRY-RUN by default: prints the rows it WOULD close + the computed exit/pnl.
   ``--apply`` writes ``status='closed'`` with the exit price / realised pnl and
   an audit stamp in ``notes``.

Closing ONE row at its own venue fill (``--row`` + ``--exit-kind``)
------------------------------------------------------------------
Added 2026-09-30 (PAPER-RESET, alpaca_paper). A netted symbol can hold one row
whose own protective leg already FILLED at the venue while a sibling row keeps
the symbol non-flat, so the reconciler never sees it go (SPY row 6131: its
8-share stop filled 2026-09-29 @ 763.70 while row 4347's 11 shares stayed).
When the symbol later reads flat, closing every row as an operator flatten at
entry would book a real strategy stop as an operational close with pnl 0.
``--row <id>`` restricts the close to that one open row; ``--exit-kind sl|tp``
(requires ``--row`` and ``--exit-price``) stamps ``exit_reason`` = the kind and
``exit_price_source='exchange_fill'``: the price is the venue fill, read from
Alpaca order history by the caller.

The broker-flat gate has ONE narrow exception, and only with ``--row`` +
``--exit-kind``. Added 2026-09-30, when the operator kept the account instead of
resetting it, so SPY never reads flat while row 4347 holds 11 shares. The row
may close while the symbol is still held only if the broker position is
exactly accounted for by the OTHER open rows: same side, and the siblings'
summed size equals the broker size. Closing the row then leaves the journal
matching the broker, so no live share is orphaned. Any mismatch in side, qty or
missing siblings is ``refused_position_open``, as before. Without ``--row`` and
``--exit-kind`` the gate stays broker-flat.

Exit price / pnl
----------------
Alpaca is a local-compute PnL venue (no broker-truth reader), so realised pnl is
computed the same way the rest of the stack does for non-Bybit closes
(``pnl_source='local_compute'``). The exit price comes from ``--exit-price`` when
the operator captured the flatten fill; absent that it falls back to the entry
price (pnl 0) and stamps ``exit_price_source='entry_fallback_no_fill'`` so the
row is honestly labelled rather than carrying a fabricated number. Equity shares
have ``contract_value=1`` (no multiplier).

Safety
------
* Broker-flat is REQUIRED — a still-open position or an unreadable account both
  refuse to write.
* Idempotent: the WHERE guard re-checks ``status='open'``, so a re-run after the
  row is closed is a no-op.
* Backtest rows (``is_backtest=1``) are never touched.
* Alpaca-only in v1 (the mode-gated-reconciler failure mode is Alpaca/IB); a
  non-Alpaca account is refused.
* Best-effort: never raises into the caller; a read/DB failure is reported.

Usage (on the live VM, via the ``close-stranded-journal-row`` system-action):
    python3 scripts/ops/close_stranded_journal_row.py --account alpaca_live --symbol IEF
    python3 scripts/ops/close_stranded_journal_row.py --account alpaca_live --symbol IEF --exit-price 93.665 --apply
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

# scripts/ops/ → repo root is two levels up. Add it to sys.path so `from src...`
# resolves when the wrapper invokes this by absolute path (system python3, cwd
# != repo root) — mirrors flatten_alpaca_position.py.
_OPS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(os.path.dirname(_OPS_DIR))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
# scripts/ops on the path too, so the sibling import below resolves regardless
# of how this module is loaded (bare `python3 scripts/ops/...` puts the script
# dir on sys.path automatically; importlib-from-a-test does not).
if _OPS_DIR not in sys.path:
    sys.path.insert(0, _OPS_DIR)

# Reuse the account loader + the MODE-AGNOSTIC broker read from the flatten
# script so the broker-flat safety gate is the exact same read path.
from flatten_alpaca_position import _live_position, _load_account  # noqa: E402


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _open_rows(conn: sqlite3.Connection, account_id: str, symbol: str) -> List[sqlite3.Row]:
    """Open, non-backtest trades for (account_id, symbol) — case-insensitive
    symbol match to tolerate stored-case drift."""
    cur = conn.execute(
        """
        SELECT id, symbol, direction, entry_price, exit_price,
               position_size, status, exit_reason, pnl, pnl_percent,
               is_backtest, strategy_name, account_id, created_at,
               timestamp, notes
        FROM trades
        WHERE status = 'open'
          AND account_id = ?
          AND UPPER(symbol) = UPPER(?)
          AND COALESCE(is_backtest, 0) = 0
        ORDER BY id ASC
        """,
        (account_id, symbol),
    )
    return cur.fetchall()


def _now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


EXIT_KINDS = ("sl", "tp")


def _plan_close(row: sqlite3.Row, *, exit_price: Optional[float], reason: str,
                exit_kind: Optional[str] = None) -> Dict[str, Any]:
    """Build the UPDATE dict that closes *row*. Local-compute realised pnl from
    the (optional) exit price; fall back to entry (pnl 0) when no fill captured."""
    direction = str(row["direction"] or "").lower()
    is_long = direction in ("long", "buy")
    try:
        entry = float(row["entry_price"]) if row["entry_price"] is not None else None
    except (TypeError, ValueError):
        entry = None
    try:
        size = abs(float(row["position_size"])) if row["position_size"] is not None else None
    except (TypeError, ValueError):
        size = None

    if exit_kind is not None and exit_price is not None:
        # The row's own protective leg filled at the venue: a strategy exit.
        eff_exit = float(exit_price)
        exit_source = "exchange_fill"
        reason = exit_kind
    elif exit_price is not None:
        eff_exit = float(exit_price)
        exit_source = "operator_flatten_fill"
    else:
        eff_exit = entry if entry is not None else 0.0
        exit_source = "entry_fallback_no_fill"

    pnl: Optional[float] = None
    pnl_percent: Optional[float] = None
    if entry is not None and size is not None:
        # Equity shares: contract_value = 1 (no multiplier).
        pnl = (eff_exit - entry) * size if is_long else (entry - eff_exit) * size
        pnl = round(pnl, 4)
        denom = abs(entry) * size
        if denom > 0:
            pnl_percent = round(pnl / denom * 100.0, 4)

    try:
        notes = json.loads(row["notes"]) if row["notes"] else {}
        if not isinstance(notes, dict):
            notes = {"_original_notes": row["notes"]}
    except (TypeError, ValueError, json.JSONDecodeError):
        notes = {"_original_notes": row["notes"]}
    notes.update({
        "closed_by": "close_stranded_journal_row_script",
        "closed_at_iso": datetime.now(timezone.utc).isoformat(),
        "broker_flat_confirmed": True,
        "original_status": "open",
        "exit_price_source": exit_source,
        "pnl_source": "local_compute",
    })
    if exit_kind is not None:
        notes["closed_by_venue_leg"] = exit_kind

    updates: Dict[str, Any] = {
        "status": "closed",
        "exit_reason": reason,
        "exit_price": round(eff_exit, 6),
        "closed_at": str(_now_ms()),  # raw epoch-ms string, matching the reconciler close path
        "notes": json.dumps(notes, ensure_ascii=False)[:4000],
    }
    if pnl is not None:
        updates["pnl"] = pnl
    if pnl_percent is not None:
        updates["pnl_percent"] = pnl_percent
    return updates


def _siblings_account_for_position(
    account_id: str, symbol: str, pos: Dict[str, Any], row_id: int,
    db_path: Optional[str],
) -> Tuple[bool, str]:
    """True only if the OTHER open rows on (account, symbol) exactly account for
    the live broker position: same side, summed size == broker size."""
    resolved_db = db_path
    if not resolved_db:
        from src.utils.paths import trade_journal_db_path
        resolved_db = str(trade_journal_db_path())
    if not os.path.exists(resolved_db):
        return False, "db not found"
    try:
        conn = _connect(resolved_db)
        try:
            rows = _open_rows(conn, account_id, symbol)
        finally:
            conn.close()
    except sqlite3.Error as exc:
        return False, f"db read failed: {type(exc).__name__}"
    if not any(int(r["id"]) == row_id for r in rows):
        return False, f"row {row_id} is not an open row on {account_id}/{symbol}"
    siblings = [r for r in rows if int(r["id"]) != row_id]
    if not siblings:
        return False, "no sibling row holds the live position"
    side = str(pos.get("side") or "").lower()
    try:
        broker_size = abs(float(pos.get("size")))
    except (TypeError, ValueError):
        return False, "broker size unreadable"
    total = 0.0
    for r in siblings:
        d = str(r["direction"] or "").lower()
        d = "long" if d in ("long", "buy") else "short" if d in ("short", "sell") else d
        if d != side:
            return False, f"sibling row {r['id']} direction {d!r} != broker side {side!r}"
        try:
            total += abs(float(r["position_size"]))
        except (TypeError, ValueError):
            return False, f"sibling row {r['id']} size unreadable"
    if abs(total - broker_size) > 1e-9:
        return False, (f"siblings hold {total} but broker holds {broker_size} — "
                       "not exactly accounted for")
    ids = ",".join(str(r["id"]) for r in siblings)
    return True, f"broker {side} {broker_size} == sibling rows [{ids}]"


def _apply_updates(conn: sqlite3.Connection, plans: List[Tuple[int, Dict[str, Any]]]) -> int:
    """Write each plan as its own UPDATE. The WHERE guard re-checks
    status='open' so a re-run (or a concurrent close) can't double-write."""
    cur = conn.cursor()
    n = 0
    for trade_id, u in plans:
        sets = ", ".join(f"{k} = ?" for k in u.keys())
        params = list(u.values()) + [trade_id]
        cur.execute(
            f"UPDATE trades SET {sets} WHERE id = ? AND status = 'open'",
            params,
        )
        n += cur.rowcount
    conn.commit()
    return n


def close_stranded(
    account_id: str, symbol: str, *, apply: bool,
    exit_price: Optional[float], reason: str, db_path: Optional[str] = None,
    row_id: Optional[int] = None, exit_kind: Optional[str] = None,
) -> Dict[str, Any]:
    """Core routine. Returns a structured result dict (never raises)."""
    symbol = symbol.upper()
    out: Dict[str, Any] = {
        "account_id": account_id, "symbol": symbol, "apply": apply,
        "action": None, "ok": False, "detail": None,
    }

    if exit_kind is not None and (exit_kind not in EXIT_KINDS or row_id is None
                                  or exit_price is None):
        out["action"] = "refused_exit_kind"
        out["detail"] = (f"exit_kind must be one of {EXIT_KINDS} and needs both row and "
                         "exit_price (the venue fill) — refusing")
        return out

    account_cfg = _load_account(account_id)
    if not account_cfg:
        out["action"] = "abort"
        out["detail"] = f"account {account_id!r} not found in accounts.yaml"
        return out
    exchange = str(account_cfg.get("exchange") or "").lower()
    if exchange != "alpaca":
        out["action"] = "abort"
        out["detail"] = (f"account {account_id!r} is exchange={exchange!r}, not Alpaca — "
                         "close-stranded-journal-row supports Alpaca accounts only in v1")
        return out

    # SAFETY GATE: the broker MUST read flat (mode-agnostic) before we close the row.
    pos = _live_position(account_cfg, symbol)
    if pos is None:
        out["action"] = "abort_unreadable"
        out["detail"] = ("could not read the live Alpaca position (missing creds / API "
                         "error) — refusing to close the journal row blind")
        return out
    netted_ok = False
    if pos and row_id is not None and exit_kind is not None:
        netted_ok, why = _siblings_account_for_position(
            account_id, symbol, pos, int(row_id), db_path)
        out["netted_check"] = why
    if pos and not netted_ok:  # a real position is still open on the broker
        size = pos.get("size")
        out["action"] = "refused_position_open"
        out["live_position"] = {"side": pos.get("side"), "size": size,
                                "entry_price": pos.get("entry_price"),
                                "unrealised_pnl": pos.get("unrealised_pnl")}
        out["detail"] = (f"broker still holds a live {symbol} position (size={size}) on "
                         f"{account_id} — refusing to close the journal row (would orphan a "
                         "live position). Flatten it first (flatten-alpaca-position apply).")
        return out
    # pos == {} → broker confirmed FLAT, or (netted_ok) the broker position is
    # exactly the siblings' — either way closing this row orphans no live share.
    if pos:
        out["broker_position_accounted_by_siblings"] = True

    resolved_db = db_path
    if not resolved_db:
        from src.utils.paths import trade_journal_db_path
        resolved_db = str(trade_journal_db_path())
    out["db"] = resolved_db
    if not os.path.exists(resolved_db):
        out["action"] = "abort"
        out["detail"] = f"trade_journal.db not found at {resolved_db}"
        return out

    conn = _connect(resolved_db)
    try:
        rows = _open_rows(conn, account_id, symbol)
    except sqlite3.Error as exc:
        out["action"] = "abort"
        out["detail"] = f"db read failed: {type(exc).__name__}: {exc}"
        conn.close()
        return out

    if row_id is not None:
        rows = [r for r in rows if int(r["id"]) == int(row_id)]
        if not rows:
            out["action"] = "refused_row_not_open"
            out["detail"] = (f"row {row_id} is not an open, non-backtest {symbol} row on "
                             f"{account_id} — nothing written")
            conn.close()
            return out

    if not rows:
        out["action"] = "noop_no_open_row"
        out["ok"] = True
        out["detail"] = (f"broker is flat and no open {symbol} journal row exists for "
                         f"{account_id} — nothing to close")
        conn.close()
        return out

    plans: List[Tuple[int, Dict[str, Any]]] = []
    preview: List[Dict[str, Any]] = []
    for row in rows:
        updates = _plan_close(row, exit_price=exit_price, reason=reason,
                              exit_kind=exit_kind)
        plans.append((row["id"], updates))
        preview.append({
            "id": row["id"], "direction": row["direction"], "symbol": row["symbol"],
            "strategy": row["strategy_name"], "entry_price": row["entry_price"],
            "size": row["position_size"], "exit_price": updates.get("exit_price"),
            "pnl": updates.get("pnl"), "exit_reason": updates["exit_reason"],
        })
    out["rows"] = preview
    out["broker_flat_confirmed"] = True

    if not apply:
        out["action"] = "dry_run"
        out["ok"] = True
        out["detail"] = (f"DRY-RUN — broker is flat; would close {len(plans)} open {symbol} "
                         f"row(s) on {account_id}. Re-run with --apply to write.")
        conn.close()
        return out

    try:
        n = _apply_updates(conn, plans)
    except sqlite3.Error as exc:
        out["action"] = "abort"
        out["detail"] = f"db write failed: {type(exc).__name__}: {exc}"
        conn.close()
        return out
    conn.close()
    out["action"] = "closed"
    out["ok"] = True
    out["rows_closed"] = n
    out["detail"] = (f"closed {n} stranded {symbol} journal row(s) on {account_id} "
                     f"(broker-confirmed flat). They now leave /positions and appear in "
                     f"/trades/closed.")
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--account", required=True, help="account_id in accounts.yaml (e.g. alpaca_live)")
    parser.add_argument("--symbol", required=True, help="bot symbol whose stranded open row to close (e.g. IEF)")
    parser.add_argument("--apply", action="store_true", help="write the close (default: dry-run)")
    parser.add_argument("--exit-price", type=float, default=None,
                        help="the flatten fill price for local-compute pnl (default: entry → pnl 0)")
    parser.add_argument("--reason", default="operator_flatten_reconciled",
                        help="exit_reason to stamp (default: operator_flatten_reconciled)")
    parser.add_argument("--row", type=int, default=None,
                        help="close only this open trade id (default: every open row)")
    parser.add_argument("--exit-kind", choices=EXIT_KINDS, default=None,
                        help="the row's own protective leg filled at the venue: stamp "
                             "exit_reason=<kind>, exit_price_source=exchange_fill "
                             "(needs --row and --exit-price)")
    parser.add_argument("--db", default=None,
                        help="Path to trade_journal.db (default: resolver)")
    args = parser.parse_args()

    result = close_stranded(
        args.account, args.symbol, apply=args.apply,
        exit_price=args.exit_price, reason=args.reason, db_path=args.db,
        row_id=args.row, exit_kind=args.exit_kind,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    # exit non-zero on a refusal/abort so the wrapper surfaces it as FAILED.
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
