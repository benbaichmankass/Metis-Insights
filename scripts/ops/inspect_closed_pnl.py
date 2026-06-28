"""Diagnostic: dump Bybit V5 ``/v5/position/closed-pnl`` records for
the search window the backfill / live sweep would use for one trade.

Read-only. Doesn't touch trade_journal.db; doesn't call any
matching logic. Just shows what Bybit returns, so the operator can
see the raw records and judge whether the matcher's selection
makes sense.

Triggered by the 2026-05-18 incident chain (issues #1411 → #1419):
the matcher kept producing wrong values for the same set of trades
even with the entry_price discriminator. We need to see Bybit's
actual records to know whether the bug is in the matcher (we're
picking the wrong record) or in our assumptions (Bybit reports
positions, not orders — so multiple bot-level "trades" may map to
one Bybit close).

Usage on the VM:
    cd /home/ubuntu/ict-trading-bot
    python3 scripts/ops/inspect_closed_pnl.py --trade-id 1540
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from typing import Any, Dict, Optional

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from src.config.accounts_loader import load_accounts_dict  # noqa: E402
from src.units.accounts.clients import bybit_client_for  # noqa: E402
from src.units.accounts.execute import _bybit_category  # noqa: E402


def _parse_ms(value: Any) -> Optional[int]:
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


def _load_trade(db_path: str, trade_id: int) -> Optional[Dict[str, Any]]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.execute(
        "SELECT id, symbol, direction, entry_price, exit_price, "
        "       position_size, status, exit_reason, pnl, "
        "       account_id, created_at, timestamp, notes "
        "  FROM trades WHERE id = ?",
        (trade_id,),
    )
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def _dump_list(label: str, resp: Any, fields: tuple) -> int:
    """Print the inner result.list of a Bybit V5 response, projecting
    *fields*. Returns the record count."""
    rows = ((resp or {}).get("result") or {}).get("list") or []
    print(f"\n===== {label}: {len(rows)} record(s) =====")
    for i, r in enumerate(rows[:50]):
        proj = {f: r.get(f) for f in fields}
        print(f"  [{i:02d}] " + "  ".join(f"{k}={v}" for k, v in proj.items()))
    return len(rows)


def _probe_alt_pnl_sources(client: Any, *, category: str, symbol: str,
                           start_ms: int, end_ms: int) -> None:
    """BL-20260608-DEMOPNL recon: dump the two endpoints that could
    serve as a realised-PnL source when /v5/position/closed-pnl is
    empty (as it is on the Bybit DEMO venue) — the per-fill execution
    list and the order history. Read-only; every call is best-effort
    so one unsupported endpoint doesn't abort the probe.

    ``execList`` carries per-fill ``execPrice`` / ``execQty`` /
    ``execFee`` / ``closedSize`` / ``execType`` (and sometimes
    ``execPnl``) — enough to reconstruct an exit avg-price + realised
    PnL for the closing fills. ``order/history`` carries the closing
    order's ``avgPrice`` / ``cumExecQty`` / ``cumExecValue`` /
    ``cumExecFee``. This probe tells us which (if either) the demo
    venue actually populates before we build the recovery path.
    """
    try:
        resp = client.get_executions(
            category=category, symbol=symbol,
            startTime=start_ms, endTime=end_ms, limit=100,
        )
        _dump_list(
            "execution-list (/v5/execution/list)", resp,
            ("execTime", "side", "execPrice", "execQty", "execFee",
             "execType", "closedSize", "orderId"),
        )
    except Exception as exc:  # noqa: BLE001
        print(f"\n===== execution-list: get_executions raised: {exc} =====")

    try:
        resp = client.get_order_history(
            category=category, symbol=symbol,
            startTime=start_ms, endTime=end_ms, limit=50,
        )
        _dump_list(
            "order-history (/v5/order/history)", resp,
            ("updatedTime", "side", "orderStatus", "avgPrice",
             "cumExecQty", "cumExecValue", "cumExecFee", "reduceOnly",
             "stopOrderType", "orderId"),
        )
    except Exception as exc:  # noqa: BLE001
        print(f"\n===== order-history: get_order_history raised: {exc} =====")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trade-id", type=int, required=True,
                        help="trades.id to inspect")
    parser.add_argument("--db", default=None,
                        help="Path to trade_journal.db")
    parser.add_argument("--window-hours", type=float, default=None,
                        help="Search-window upper bound, hours after "
                             "opened_at. Default: until now().")
    args = parser.parse_args()

    from src.utils.paths import trade_journal_db_path
    db_path = args.db or str(trade_journal_db_path())
    if not os.path.exists(db_path):
        print(f"error: db not found at {db_path}", file=sys.stderr)
        return 2

    trade = _load_trade(db_path, args.trade_id)
    if trade is None:
        print(f"error: trade id={args.trade_id} not found in {db_path}",
              file=sys.stderr)
        return 3

    print("===== local trade row =====")
    print(json.dumps({k: v for k, v in trade.items() if k != "notes"},
                     indent=2, default=str))
    # Dump notes BOTH as raw text and as parsed JSON (when parseable).
    # The raw view is essential for diagnosing truncation — when the
    # backfill's 500-char cap chopped the JSON mid-string, the parser
    # rejects it but the raw bytes still carry useful context (e.g.
    # the original_pnl key/value may still be intact even if a
    # following field is truncated).
    raw_notes = trade.get("notes")
    print(f"\nnotes (raw, {len(raw_notes or '')} chars):")
    print(repr(raw_notes))
    try:
        notes = json.loads(raw_notes or "{}")
        print("\nnotes (parsed):", json.dumps(notes, indent=2, default=str))
    except Exception as exc:  # noqa: BLE001
        print(f"\nnotes (parsed): <unparseable: {exc}>")

    cfgs = load_accounts_dict()
    cfg = cfgs.get(str(trade.get("account_id")))
    if cfg is None:
        print(f"\nerror: no account cfg for account_id="
              f"{trade.get('account_id')!r}", file=sys.stderr)
        return 4

    try:
        category = _bybit_category(cfg)
    except Exception as exc:  # noqa: BLE001
        print(f"error: _bybit_category raised: {exc}", file=sys.stderr)
        return 5
    if category not in ("linear", "inverse"):
        print(f"error: unsupported category={category!r}", file=sys.stderr)
        return 6

    direction = str(trade.get("direction") or "").lower()
    close_side = "Sell" if direction == "long" else "Buy"

    opened_at_ms = _parse_ms(trade.get("created_at"))
    if opened_at_ms is None:
        print("error: unparseable created_at", file=sys.stderr)
        return 7

    if args.window_hours is not None:
        end_ms = opened_at_ms + int(args.window_hours * 3600 * 1000)
    else:
        end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    start_ms = max(opened_at_ms - 60_000,
                   end_ms - 7 * 24 * 60 * 60 * 1000)

    print("\n===== query window =====")
    print(f"  start_ms = {start_ms} ({datetime.fromtimestamp(start_ms/1000, tz=timezone.utc).isoformat()})")
    print(f"  end_ms   = {end_ms} ({datetime.fromtimestamp(end_ms/1000, tz=timezone.utc).isoformat()})")
    print(f"  category = {category}")
    print(f"  symbol   = {trade.get('symbol')}")
    print(f"  side     = {close_side}  (close side for direction={direction})")

    client = bybit_client_for(cfg)
    if client is None:
        print("error: bybit_client_for returned None (creds missing?)",
              file=sys.stderr)
        return 8

    try:
        resp = client.get_closed_pnl(
            category=category,
            symbol=str(trade.get("symbol") or ""),
            startTime=start_ms,
            endTime=end_ms,
            limit=50,
        ) or {}
    except Exception as exc:  # noqa: BLE001
        print(f"error: get_closed_pnl raised: {exc}", file=sys.stderr)
        return 9

    records = ((resp.get("result") or {}).get("list") or [])
    print(f"\n===== closed-pnl: Bybit returned {len(records)} record(s) =====")

    if not records:
        print("(window contains no closed-pnl rows — Bybit may have "
              "dropped them past the 7-day retention, OR the trade's "
              "qty/symbol didn't actually transact, OR — on the DEMO "
              "venue — /v5/position/closed-pnl simply isn't populated; "
              "see the execution-list + order-history probes below for "
              "alternative PnL sources, BL-20260608-DEMOPNL)")
        # Fall through to the alternative-source probes — on demo these
        # are the only way to recover realised PnL.
        _probe_alt_pnl_sources(
            client, category=category,
            symbol=str(trade.get("symbol") or ""),
            start_ms=start_ms, end_ms=end_ms,
        )
        return 0

    for i, rec in enumerate(records):
        rec_side = str(rec.get("side") or "")
        rec_qty = rec.get("qty")
        rec_entry = rec.get("avgEntryPrice")
        rec_exit = rec.get("avgExitPrice")
        rec_pnl = rec.get("closedPnl")
        rec_created = rec.get("createdTime")
        rec_order = rec.get("orderId")
        # Match column relative to the local trade
        try:
            entry_target = float(trade.get("entry_price") or 0)
            entry_actual = float(rec_entry or 0)
            if entry_target > 0 and entry_actual > 0:
                bps = abs(entry_actual - entry_target) / entry_target * 10_000
                entry_match = f"{bps:+.1f}bps"
            else:
                entry_match = "n/a"
        except (TypeError, ValueError):
            entry_match = "n/a"
        side_match = ("OK" if rec_side.lower() == close_side.lower()
                      else "MISS")
        # Time match
        try:
            created_iso = datetime.fromtimestamp(
                int(rec_created) / 1000, tz=timezone.utc
            ).isoformat()
        except (TypeError, ValueError):
            created_iso = "?"
        print(
            f"  [{i:02d}] side={rec_side:<5} ({side_match})  "
            f"qty={rec_qty:<8}  "
            f"entry={rec_entry:<10}  exit={rec_exit:<10}  "
            f"pnl={rec_pnl:<10}  "
            f"entry_match={entry_match}  "
            f"closed_at={created_iso}  "
            f"order_id={rec_order}"
        )

    # Summary: pre-fix matcher pick vs post-fix matcher pick
    print("\n===== matcher comparison =====")
    print(f"  local trade entry_price = {trade.get('entry_price')}")
    print(f"  local trade qty         = {trade.get('position_size')}")
    print(f"  local trade gross pnl   = {trade.get('pnl')}  "
          "(from notes.original_pnl, gross-formula derivation)")

    # What the pre-fix matcher (most-recent-after-side+qty) would pick
    same_side = [r for r in records
                 if str(r.get("side") or "").lower() == close_side.lower()]
    if same_side:
        most_recent = max(same_side, key=lambda r: int(r.get("updatedTime") or 0))
        print(f"  pre-fix matcher pick:    pnl={most_recent.get('closedPnl')!s:<10}  "
              f"entry={most_recent.get('avgEntryPrice')!s}  "
              f"closed_at={most_recent.get('createdTime')!s}")

    # What the post-fix matcher (entry_price filter, 10 bps) would pick
    try:
        entry_target = float(trade.get("entry_price") or 0)
    except (TypeError, ValueError):
        entry_target = 0
    if entry_target > 0:
        within_tol = []
        for r in same_side:
            try:
                e = float(r.get("avgEntryPrice") or 0)
                if e > 0 and abs(e - entry_target) / entry_target <= 0.001:
                    within_tol.append(r)
            except (TypeError, ValueError):
                continue
        if within_tol:
            best = min(within_tol, key=lambda r: abs(
                float(r.get("avgEntryPrice") or 0) - entry_target
            ))
            print(f"  post-fix matcher pick:   pnl={best.get('closedPnl')!s:<10}  "
                  f"entry={best.get('avgEntryPrice')!s}  "
                  f"closed_at={best.get('createdTime')!s}  "
                  f"(within 10 bps; {len(within_tol)} record(s) matched)")
        else:
            print(f"  post-fix matcher pick:   None (no record within 10 bps of "
                  f"entry={entry_target})")

    # Always show the alternative PnL sources too, so a single inspect
    # call gives the full picture (closed-pnl + executions + orders).
    _probe_alt_pnl_sources(
        client, category=category,
        symbol=str(trade.get("symbol") or ""),
        start_ms=start_ms, end_ms=end_ms,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
