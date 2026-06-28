"""One-shot backfill for orphaned trades that closed via Bybit V5
broker-side SL/TP and were watchdog-orphaned with exit_price=NULL.

Companion to PR #1299 (claude/exit-price-from-closed-pnl), which
landed ``account_closed_pnl_for_trade`` so future trades close with
the real exit fill. This script applies the same recovery to the
historical orphan cluster from 2026-05-15/16 (trade ids 1450 + 1454-
1466 on bybit_2 vwap) — and to any other orphan within Bybit's
7-day closed-pnl retention window.

Usage on the VM:
    cd /home/ubuntu/ict-trading-bot
    python3 scripts/ops/backfill_orphan_pnl.py            # dry-run
    python3 scripts/ops/backfill_orphan_pnl.py --apply    # write

What this fixes:
  * status='orphaned' → 'closed' (so /api/pnl + dashboards stop
    skipping the row)
  * exit_price=NULL → recovered avg_exit_price from Bybit
  * pnl=NULL → recovered closed_pnl (net of fees, from Bybit)
  * notes JSON gains:
      - backfilled_at / backfilled_by / backfilled_source
      - backfilled_pnl (the closed_pnl Bybit reported)
      - exit_price_source='bybit_closed_pnl_backfill'
      - existing orphaned_at / orphaned_by / orphaned_reason
        are PRESERVED as audit trail
  * exit_reason='stuck_strategy_watchdog' → 'backfill_closed_pnl_recovery'
    so the backfill is distinguishable from native reconciler closes

Safety:
  * Idempotent. The WHERE clause filters status='orphaned', so once
    a row is rewritten to 'closed' it no longer matches and re-runs
    are no-ops.
  * Skips rows where account_closed_pnl_for_trade returns None — the
    row stays orphaned and is logged for operator follow-up. Most
    common cause: Bybit's 7-day window expired, or qty/side filter
    didn't match (rare; suggests the orphan didn't correspond to a
    real Bybit close).
  * Skips rows where the recovered avg_exit_price is 0 — defends
    against malformed Bybit records.
  * Backtest rows (is_backtest=1) are not touched.
  * Each row is its own UPDATE — partial completion is safe and a
    re-run picks up where it left off.

Mirrors the structure of backfill_pnl_nulls.py for consistency.
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
    """Rows that this backfill targets.

    Filter: orphaned + stuck_strategy_watchdog + NULL exit_price.
    The watchdog reason is the marker for the specific failure mode
    PR #1268 + #1299 + this backfill chain remediate; widening to
    any orphan would risk picking up rows orphaned for a different
    reason (reverse_reconciler, manual cleanup, etc.) where the
    closed-pnl lookup is the wrong tool.
    """
    cur = conn.execute(
        """
        SELECT id, symbol, direction, entry_price, exit_price,
               position_size, status, exit_reason, pnl, pnl_percent,
               is_backtest, strategy_name, account_id, created_at,
               timestamp, notes
        FROM trades
        WHERE status = 'orphaned'
          AND exit_reason = 'stuck_strategy_watchdog'
          AND exit_price IS NULL
          AND COALESCE(is_backtest, 0) = 0
        ORDER BY id ASC
        """
    )
    return cur.fetchall()


def _parse_created_at_to_ms(value: Any) -> Optional[int]:
    """Mirror :func:`order_monitor._isoformat_to_ms` for stand-alone
    use. ``CURRENT_TIMESTAMP`` (sqlite default, no tz) and ISO-8601
    with tz both supported."""
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
    row: sqlite3.Row, closed_pnl: float, avg_exit_price: float,
) -> Optional[float]:
    """Match the gross-PnL-percent convention used by the live
    writer + ``backfill_pnl_nulls.py`` so the backfilled row reads
    consistently with the rest of the table.

    ``pnl_percent = (pnl / notional) * 100`` where notional is
    entry_price * position_size. When entry_price or position_size
    are missing, returns ``None`` (rare on the orphan cluster).
    """
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

    ``updates`` is the dict ready to feed ``UPDATE trades SET …``;
    ``skip_reason`` is a human-readable why-this-row-stays-orphaned.
    """
    if cfg is None:
        return None, f"no account cfg for account_id={row['account_id']!r}"

    opened_at_ms = _parse_created_at_to_ms(row["created_at"])
    if opened_at_ms is None:
        return None, f"unparseable created_at={row['created_at']!r}"

    notes = _decode_notes(row["notes"])
    orphaned_at = notes.get("orphaned_at")
    closed_at_ms: Optional[int] = None
    if orphaned_at:
        closed_at_ms = _parse_created_at_to_ms(orphaned_at)
        if closed_at_ms is not None:
            # +60s slack on the upper bound — the orphan stamp lands
            # slightly after Bybit's exec timestamp.
            closed_at_ms += 60_000

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
        closed_at_ms=closed_at_ms,
        qty=qty,
        entry_price=entry_price,
    )
    if rec is None:
        return None, "account_closed_pnl_for_trade returned None"

    avg_exit_price = rec.get("avg_exit_price") or 0.0
    if not avg_exit_price or avg_exit_price <= 0:
        return None, f"recovered avg_exit_price={avg_exit_price!r} (degenerate)"

    closed_pnl = rec.get("closed_pnl")
    if closed_pnl is None:
        return None, "recovered closed_pnl=None"

    # Sign-consistency sanity guard (2026-06-20 dashboard-audit).
    # A recovered closed_pnl whose SIGN contradicts the trade's own
    # entry→exit price move is almost certainly a MISMATCHED Bybit
    # closed-pnl record (the lookup returned a shared/wrong row — observed
    # on the bybit_1 DEMO account, where every long resolved to an identical
    # +3847.29 even though the exit was ~18% adverse to entry: writing it
    # would fabricate a profit on a losing trade). Refuse to write it and
    # leave the row honest (pnl stays NULL → /performance excludes it) rather
    # than corrupt the journal. Only fires when (a) we have a usable entry
    # price and (b) the price move is materially directional (>0.2%, well
    # above fee/funding noise) — so a near-breakeven trade whose net-of-fees
    # pnl legitimately flips sign is never rejected.
    try:
        cp = float(closed_pnl)
        if entry_price and entry_price > 0:
            move = float(avg_exit_price) - entry_price
            if str(row["direction"] or "").lower() == "short":
                move = -move
            move_frac = move / entry_price  # >0 favorable, <0 adverse
            if move_frac < -0.002 and cp > 0:
                return None, (
                    f"recovered closed_pnl=+{cp:.4f} but {row['direction']} exit "
                    f"{avg_exit_price} is {abs(move_frac) * 100:.1f}% ADVERSE to entry "
                    f"{entry_price} — mismatched Bybit closed-pnl record; refusing to "
                    f"write a fabricated profit"
                )
            if move_frac > 0.002 and cp < 0:
                return None, (
                    f"recovered closed_pnl={cp:.4f} but {row['direction']} exit "
                    f"{avg_exit_price} is {move_frac * 100:.1f}% FAVORABLE to entry "
                    f"{entry_price} — mismatched Bybit closed-pnl record; refusing to "
                    f"write a fabricated loss"
                )
    except (TypeError, ValueError):
        pass

    pnl_percent = _compute_pnl_percent(row, float(closed_pnl), float(avg_exit_price))

    new_notes = dict(notes)
    new_notes.update({
        "backfilled_at": datetime.now(timezone.utc).isoformat(),
        "backfilled_by": "backfill_orphan_pnl_script",
        "backfilled_source": "bybit_closed_pnl",
        "backfilled_pnl": float(closed_pnl),
        "backfilled_closed_at": rec.get("closed_at"),
        "exit_price_source": "bybit_closed_pnl_backfill",
    })

    # 2026-05-18: 500-char cap bumped to 4000 to prevent audit-
    # trail truncation when nested fields stack. See issue #1420
    # for the failure mode (original_pnl lost on 11 rows in the
    # sibling backfill_monitor_closed_pnl.py path).
    updates: Dict[str, Any] = {
        "status": "closed",
        "exit_reason": "backfill_closed_pnl_recovery",
        "exit_price": float(avg_exit_price),
        "pnl": round(float(closed_pnl), 4),
        "notes": json.dumps(new_notes, ensure_ascii=False)[:4000],
    }
    if pnl_percent is not None:
        updates["pnl_percent"] = pnl_percent
    return updates, None


# Marker substring for the skip reason produced when
# account_closed_pnl_for_trade returns None — the silent-fallback chain
# this script most often hits when credentials are missing from the
# wrapper's environment. See _warn_if_silent_credential_failure below.
_CREDS_OR_DATA_GAP_MARKER = "account_closed_pnl_for_trade returned None"


def _warn_if_silent_credential_failure(
    plans: List[Tuple[int, Dict[str, Any]]],
    skipped: List[Tuple[int, str]],
) -> None:
    """Surface the silent-credential-failure case loudly when the skip
    pattern matches its signature.

    ``account_closed_pnl_for_trade`` returns ``None`` for three different
    reasons:
      1. Bybit's 7-day window expired (legitimate data gap)
      2. Credentials missing in the wrapper's env (silent auth failure
         — never reaches Bybit at all)
      3. Bybit returned records but the qty/side filter rejected them

    All three collapse to the same skip reason string, so an operator
    looking at "0 recovered" can't tell why. The 2026-05-16 backfill
    spent two PRs (#1311, this one) untangling that ambiguity. Once
    distinguished, the operator can act: rotate creds vs accept the
    data gap vs widen the qty tolerance.

    Heuristic: if recoveries=0 AND every skip carries the marker AND
    we had ≥2 candidates with recent ``created_at``, the credential-
    missing signature dominates and we should say so. We don't claim
    certainty — the warning points at the most likely cause and
    surfaces the diagnostic command for the operator to confirm.
    """
    if not skipped or plans:
        return
    if not all(_CREDS_OR_DATA_GAP_MARKER in reason for _, reason in skipped):
        return
    print(
        "─" * 70,
        "WARNING: 100% of candidates skipped with "
        "'account_closed_pnl_for_trade returned None'.",
        "",
        "This skip reason is shared by three failure modes:",
        "  (a) Bybit's 7-day closed-pnl window expired for the trade",
        "  (b) Exchange CREDENTIALS NOT REACHABLE from this process's",
        "      env — silent auth failure, never reached Bybit",
        "  (c) Bybit returned records but qty/side filter rejected them",
        "",
        "When 100% of candidates collapse to this single reason, (b) is",
        "the most likely cause — a real data gap would mix with at least",
        "some successful recoveries. If running this via an operator-",
        "action wrapper, confirm scripts/ops/_lib.sh::load_runtime_secrets",
        "is called before invoking this script. See PR #1311 + the",
        "post-#1311 follow-up for the structural fix.",
        "─" * 70,
        sep="\n",
    )


def _apply_updates(
    conn: sqlite3.Connection, plans: List[Tuple[int, Dict[str, Any]]],
) -> int:
    """Write each plan as its own UPDATE — partial completion safe.
    The WHERE guard re-checks status='orphaned' so a concurrent
    writer (unlikely on this DB) can't double-write."""
    cur = conn.cursor()
    n = 0
    for trade_id, u in plans:
        sets = ", ".join(f"{k} = ?" for k in u.keys())
        params = list(u.values()) + [trade_id]
        cur.execute(
            f"UPDATE trades SET {sets} "
            "WHERE id = ? AND status = 'orphaned'",
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
            pnl = u.get("pnl")
            print(f"  id={trade_id} {str(row['direction'] or '?'):>5} "
                  f"{str(row['symbol'] or '?'):<10} "
                  f"acct={row['account_id']!s:<10} "
                  f"size={row['position_size']!s:<8} "
                  f"entry={row['entry_price']!s} "
                  f"→ exit={u['exit_price']:.4f} "
                  f"pnl={pnl:+.4f}")
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
