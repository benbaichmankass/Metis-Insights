"""One-shot backfill for monitor-closed trades that recorded gross
PnL from the (now-deleted) ``_compute_close_pnl`` formula instead of
Bybit's authoritative net.

Companion to PR #1409 (claude/bybit-only-pnl), which deleted the
local gross-PnL calculator and added ``_sweep_pending_pnl_from_bybit``
so future monitor closes are reconciled against Bybit's
``/v5/position/closed-pnl`` endpoint within a couple of ticks. This
script applies the same correction retroactively to rows that were
closed under the old code path and never got back-filled (because
the gross-PnL write set ``pnl`` non-NULL, which excluded them from
the new sweep's ``pnl IS NULL`` filter).

The cluster this targets: any closed, non-backtest trade whose
``notes`` blob does not contain a ``bybit_closed_pnl`` stamp. Most
visible example as of 2026-05-18: trade #1540, closed via
``tp_cross`` at pnl=+$1.03 (gross — fees of ~$0.46 not deducted),
vs the actual Bybit-net of ~+$0.57.

Usage on the VM:
    cd /home/ubuntu/ict-trading-bot
    python3 scripts/ops/backfill_monitor_closed_pnl.py            # dry-run
    python3 scripts/ops/backfill_monitor_closed_pnl.py --apply    # write

What this fixes:
  * ``pnl`` (gross, fees-blind) → ``closed_pnl`` (net, from Bybit)
  * ``pnl_percent`` recomputed from notional + the corrected pnl
  * ``exit_price`` → recovered ``avg_exit_price`` from Bybit when
    materially different from the stored verdict price; otherwise
    left untouched
  * ``notes`` JSON gains:
      - ``backfilled_at`` / ``backfilled_by`` / ``backfilled_source``
      - ``bybit_closed_pnl`` (mirrors the live sweep's notes stamp
        so subsequent runs treat the row as done)
      - ``original_pnl`` (preserved audit trail of the wrong number)
      - ``exit_price_source='bybit_closed_pnl_backfill'``

What this script does NOT touch:
  * Rows with ``COALESCE(is_backtest, 0) = 1`` — backtest PnL is
    locally computed, no Bybit truth exists for them.
  * Rows whose ``notes`` already contain ``bybit_closed_pnl`` —
    those were closed via the reconciler path (or back-filled in
    a previous run) and already carry Bybit-truth. Idempotent.
  * Rows older than Bybit's 7-day ``closed-pnl`` retention window —
    Bybit no longer has the record, so the original gross PnL is
    the only number we have. Listed as ``skipped: bybit window
    expired`` in the output so the operator can decide whether to
    backstop these manually.
  * Rows where the account is no longer in ``config/accounts.yaml``
    (retired accounts) — listed in skipped.

Safety:
  * Idempotent. The WHERE clause filters
    ``notes NOT LIKE '%bybit_closed_pnl%'``, so once a row's notes
    get the stamp it no longer matches and re-runs are no-ops.
  * Each row is its own UPDATE — partial completion is safe.
  * Default is dry-run; ``--apply`` is required to write.

Mirrors the structure of ``backfill_orphan_pnl.py`` for consistency;
shares the silent-credential-failure warning heuristic.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

# The script lives in scripts/ops/; the repo root is two levels up.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from src.config.accounts_loader import load_accounts_dict  # noqa: E402
from src.units.accounts.clients import account_closed_pnl_for_trade  # noqa: E402


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _candidate_rows(conn: sqlite3.Connection) -> List[sqlite3.Row]:
    """Rows that this backfill targets: closed + non-backtest +
    notes lacks the bybit_closed_pnl stamp + opened within Bybit's
    7-day closed-pnl window.

    The notes filter is the idempotency gate — both the live sweep
    (``_sweep_pending_pnl_from_bybit``) and the orphan-recovery path
    (``_close_trade_from_order_status``) write ``bybit_closed_pnl``
    into notes when they apply Bybit truth. A row carrying that
    stamp is already correct.
    """
    cur = conn.execute(
        """
        SELECT id, symbol, direction, entry_price, exit_price,
               position_size, status, exit_reason, pnl, pnl_percent,
               is_backtest, strategy_name, account_id, created_at,
               timestamp, notes
        FROM trades
        WHERE status = 'closed'
          AND COALESCE(is_backtest, 0) = 0
          AND (notes IS NULL
               OR notes NOT LIKE '%bybit_closed_pnl%')
          AND datetime(created_at) >= datetime('now', '-7 days')
        ORDER BY id ASC
        """
    )
    return cur.fetchall()


def _parse_created_at_to_ms(value: Any) -> Optional[int]:
    """Mirror ``order_monitor._isoformat_to_ms`` for stand-alone use."""
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    if "T" not in s and " " in s:
        s = s.replace(" ", "T", 1)
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def _decode_notes(raw: Any) -> Dict[str, Any]:
    if not raw:
        return {}
    try:
        decoded = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _compute_pnl_percent(
    row: sqlite3.Row, closed_pnl: float,
) -> Optional[float]:
    """``pnl_percent = (pnl / notional) * 100`` where notional is
    ``entry_price * position_size``."""
    try:
        entry = float(row["entry_price"]) if row["entry_price"] else None
        size = float(row["position_size"]) if row["position_size"] else None
    except (TypeError, ValueError):
        return None
    if not entry or not size:
        return None
    notional = entry * size
    if notional == 0:
        return None
    return round((closed_pnl / notional) * 100.0, 4)


def _plan_row(
    row: sqlite3.Row, cfg: Optional[Dict[str, Any]],
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Return ``(updates, skip_reason)``. Exactly one is non-None.

    Unlike the orphan-backfill variant, this one preserves
    ``status`` and ``exit_reason`` — the row is already closed with
    a real exit reason (tp_cross etc.); we only correct ``pnl`` /
    ``exit_price`` / ``notes`` against Bybit truth.
    """
    if cfg is None:
        return None, f"no account cfg for account_id={row['account_id']!r}"

    opened_at_ms = _parse_created_at_to_ms(row["created_at"])
    if opened_at_ms is None:
        return None, f"unparseable created_at={row['created_at']!r}"

    qty: Optional[float] = None
    try:
        if row["position_size"] is not None:
            qty = float(row["position_size"])
    except (TypeError, ValueError):
        qty = None

    entry_price: Optional[float] = None
    try:
        if row["entry_price"] is not None:
            entry_price = float(row["entry_price"])
    except (TypeError, ValueError):
        entry_price = None

    rec = account_closed_pnl_for_trade(
        cfg,
        symbol=str(row["symbol"] or ""),
        direction=str(row["direction"] or ""),
        opened_at_ms=opened_at_ms,
        qty=qty,
        entry_price=entry_price,
    )
    if rec is None:
        return None, "account_closed_pnl_for_trade returned None"

    closed_pnl = rec.get("closed_pnl")
    if closed_pnl is None:
        return None, "recovered closed_pnl=None"

    avg_exit_price = rec.get("avg_exit_price") or 0.0
    if not avg_exit_price or avg_exit_price <= 0:
        return None, f"recovered avg_exit_price={avg_exit_price!r} (degenerate)"

    notes = _decode_notes(row["notes"])
    new_notes = dict(notes)
    new_notes.update({
        "backfilled_at": datetime.now(timezone.utc).isoformat(),
        "backfilled_by": "backfill_monitor_closed_pnl_script",
        "backfilled_source": "bybit_closed_pnl",
        "bybit_closed_pnl": float(closed_pnl),
        "original_pnl": row["pnl"],
        "exit_price_source": "bybit_closed_pnl_backfill",
    })
    if rec.get("closed_at") and "closed_at" not in new_notes:
        new_notes["closed_at"] = rec["closed_at"]

    pnl_percent = _compute_pnl_percent(row, float(closed_pnl))

    # 2026-05-18: the 500-char notes cap previously used here
    # silently truncated the JSON when audit fields stacked
    # (incident #1420 — original_pnl was lost on 11 rows because
    # backfill stamps + pre-existing notes pushed past 500). The
    # `notes` column is TEXT (unbounded in SQLite); 4000 chars
    # leaves room for any reasonable nested audit trail while
    # keeping diagnostic output readable.
    updates: Dict[str, Any] = {
        "pnl": round(float(closed_pnl), 4),
        "exit_price": float(avg_exit_price),
        "notes": json.dumps(new_notes, ensure_ascii=False)[:4000],
    }
    if pnl_percent is not None:
        updates["pnl_percent"] = pnl_percent
    return updates, None


# Marker substring (same shape as backfill_orphan_pnl.py); the
# credential-silent-failure heuristic below piggybacks on it.
_CREDS_OR_DATA_GAP_MARKER = "account_closed_pnl_for_trade returned None"


def _warn_if_silent_credential_failure(
    plans: List[Tuple[int, Dict[str, Any]]],
    skipped: List[Tuple[int, str]],
) -> None:
    """Same diagnostic as backfill_orphan_pnl.py — when 100% of
    candidates collapse to "returned None", credentials missing is
    by far the most likely cause."""
    if not skipped or plans:
        return
    if not all(_CREDS_OR_DATA_GAP_MARKER in reason for _, reason in skipped):
        return
    print(
        "─" * 70,
        "WARNING: 100% of candidates skipped with "
        "'account_closed_pnl_for_trade returned None'.",
        "",
        "Most likely cause: exchange CREDENTIALS NOT REACHABLE from "
        "this process's env — silent auth failure, never reached Bybit. "
        "If running this via an system-action wrapper, confirm "
        "scripts/ops/_lib.sh::load_runtime_secrets is called before "
        "invoking this script. See backfill_orphan_pnl.py docstring for "
        "the full explanation.",
        "─" * 70,
        sep="\n",
    )


def _apply_updates(
    conn: sqlite3.Connection, plans: List[Tuple[int, Dict[str, Any]]],
) -> int:
    """Write each plan as its own UPDATE — partial completion safe.
    The WHERE guard re-checks the idempotency condition so a
    concurrent writer (the live sweep, unlikely on the same row but
    possible) can't double-write."""
    cur = conn.cursor()
    n = 0
    for trade_id, u in plans:
        sets = ", ".join(f"{k} = ?" for k in u.keys())
        params = list(u.values()) + [trade_id]
        cur.execute(
            f"UPDATE trades SET {sets} "
            "WHERE id = ? "
            "  AND status = 'closed' "
            "  AND (notes IS NULL OR notes NOT LIKE '%bybit_closed_pnl%')",
            params,
        )
        n += cur.rowcount
    conn.commit()
    return n


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="Write the backfill (default: dry-run).")
    parser.add_argument("--db", default=None,
                        help="Path to trade_journal.db (default: "
                             "$TRADE_JOURNAL_DB or ./trade_journal.db).")
    args = parser.parse_args()

    from src.utils.paths import trade_journal_db_path
    db_path = args.db or str(trade_journal_db_path())
    if not os.path.exists(db_path):
        print(f"error: db not found at {db_path}", file=sys.stderr)
        return 2

    conn = _connect(db_path)
    rows = _candidate_rows(conn)
    if not rows:
        print(f"no candidate rows in {db_path} — nothing to backfill")
        return 0

    cfgs = load_accounts_dict()

    plans: List[Tuple[int, Dict[str, Any]]] = []
    skipped: List[Tuple[int, str]] = []
    for row in rows:
        cfg = cfgs.get(str(row["account_id"])) if row["account_id"] else None
        updates, reason = _plan_row(row, cfg)
        if updates is None:
            skipped.append((row["id"], reason or "unknown"))
            continue
        plans.append((row["id"], updates))

    print(f"db: {db_path}")
    print(f"candidates: {len(rows)} | recoverable: {len(plans)} | "
          f"skipped: {len(skipped)}")
    print()
    if plans:
        print("would update:")
        for trade_id, u in plans[:20]:
            row = next(r for r in rows if r["id"] == trade_id)
            old_pnl = row["pnl"]
            new_pnl = u.get("pnl")
            delta = (
                f"{new_pnl - old_pnl:+.4f}"
                if isinstance(old_pnl, (int, float))
                and isinstance(new_pnl, (int, float))
                else "n/a"
            )
            print(f"  id={trade_id} {str(row['direction'] or '?'):>5} "
                  f"{str(row['symbol'] or '?'):<10} "
                  f"acct={row['account_id']!s:<10} "
                  f"old_pnl={old_pnl!s:<10} "
                  f"→ new_pnl={new_pnl:+.4f} "
                  f"(Δ={delta})")
        if len(plans) > 20:
            print(f"  ... and {len(plans) - 20} more")
        print()
    if skipped:
        print("skipped:")
        for trade_id, why in skipped:
            print(f"  id={trade_id}: {why}")
        print()

    _warn_if_silent_credential_failure(plans, skipped)

    if not args.apply:
        print("dry-run — pass --apply to write.")
        return 0

    n = _apply_updates(conn, plans)
    print(f"wrote {n} row(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
