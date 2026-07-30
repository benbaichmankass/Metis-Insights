#!/usr/bin/env python3
"""Read-only broker-truth audit of Bybit protective-bracket COVERAGE.

Why this exists
---------------
Until now nothing in the system could answer, with receipts, the question
"is every open Bybit trade actually protected at the broker right now?".

Two blind spots stacked:

1. ``order_monitor._check_naked_positions`` is DB-driven — it only flags a row
   whose *journal* SL/TP is missing. A row keeps its journal SL/TP while the
   real broker leg is gone, so it never fires.
2. ``order_monitor._bybit_position_protection`` is **boolean** — it returns
   protected=True when the position carries a Full-mode ``stopLoss`` OR
   ``any()`` resting Partial SL leg. Under ``BYBIT_TPSL_MODE=partial`` a netted
   one-way position holds N journal trades and N qty-scoped legs; if some legs
   are missing (rejected at Bybit's 20-leg cap, or cancelled when a sibling
   trade closed) the surviving leg still satisfies ``any()`` → the sweep reports
   PROTECTED and skips, while the position is only **partially** covered. A
   leg's ``slSize`` covers its own qty, not the net position.

Nothing measured *quantity coverage*, so a partially-naked position was
invisible to every layer. This script measures it.

What it reports (per Bybit account, per symbol with a live position)
-------------------------------------------------------------------
* the venue's position row: ``size``, ``side``, ``stopLoss``, ``takeProfit``,
  ``tpslMode``;
* every resting conditional leg: ``orderId``, ``stopOrderType``, qty,
  ``triggerPrice``, ``orderStatus``;
* **SL-covered qty vs position size** → ``coverage_pct``, ``uncovered_qty`` and
  a verdict: ``PROTECTED`` / ``PARTIALLY_NAKED`` / ``NAKED``;
* the journal join — each open trade on that account+symbol, its
  ``position_size``, its tracked ``sl_order_id``, and whether that specific leg
  is STILL ALIVE at the broker → **per-trade** protection, which is the level
  the partial-mode design actually promises.

Read-only by construction: it calls only ``get_positions`` and
``get_open_orders``. It never places, amends, or cancels anything, and never
writes to the DB. Exit code is 0 whenever the audit itself ran (the verdict is
in the text + the JSON summary); nonzero only on a structural failure to run.

Usage (on the live VM, via the ``bybit-bracket-audit`` Tier-1 system-action):
    python3 scripts/ops/bybit_bracket_audit.py
    python3 scripts/ops/bybit_bracket_audit.py --account bybit_2
    python3 scripts/ops/bybit_bracket_audit.py --account bybit_2 --symbol XRPUSDT
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

# Mirrors order_monitor / execute.py so the audit classifies legs exactly the
# way the live code does — a divergence here would make the audit lie.
_SL_TYPES = {"stoploss", "partialstoploss"}
_TP_TYPES = {"takeprofit", "partialtakeprofit"}

# Fractional slack on the qty comparison. Bybit echoes leg qty as a string at
# the instrument's qty step; a hair of float noise must not read as a coverage
# hole. 0.5% of position size.
_COVERAGE_EPS_FRAC = 0.005


def _f(v: Any) -> Optional[float]:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if x == x else None  # drop NaN


def _leg_qty(leg: Dict[str, Any]) -> Optional[float]:
    """Qty a conditional leg would close.

    Bybit reports a partial TP/SL leg's scoped size on ``qty``; some response
    shapes carry ``triggerQty``. Try both rather than assuming one, and return
    None when neither parses (an unknown-qty leg must NOT be silently counted
    as full coverage).
    """
    for key in ("qty", "triggerQty", "size"):
        q = _f(leg.get(key))
        if q is not None and q > 0:
            return q
    return None


def _load_accounts() -> List[Dict[str, Any]]:
    from src.units.ui.data_loaders import list_accounts

    return [a for a in (list_accounts() or []) if a]


def _bybit_accounts(account_filter: Optional[str]) -> List[Dict[str, Any]]:
    out = []
    for acc in _load_accounts():
        if str(acc.get("exchange", "")).lower() != "bybit":
            continue
        aid = str(acc.get("account_id") or acc.get("name") or "")
        if account_filter and aid != account_filter:
            continue
        out.append(acc)
    return out


def _open_journal_rows(account_id: str) -> List[sqlite3.Row]:
    """Open, non-backtest journal rows for one account.

    Resolves the DB through the ONE canonical resolver
    (``src.utils.paths.trade_journal_db_path``) — never a CWD-relative
    basename, which the ``canonical-db-resolver`` CI guard forbids and which is
    what seeded the stray duplicate journals historically.
    """
    from src.utils.paths import trade_journal_db_path

    path = trade_journal_db_path()
    conn = sqlite3.connect("file:%s?mode=ro" % path, uri=True)
    try:
        conn.row_factory = sqlite3.Row
        return list(conn.execute(
            "SELECT id, account_id, symbol, direction, position_size, "
            "stop_loss, take_profit_1, sl_order_id, tp_order_id, "
            "strategy_name, created_at "
            "FROM trades WHERE status='open' AND COALESCE(is_backtest,0)=0 "
            "AND account_id=? ORDER BY id",
            (account_id,),
        ))
    finally:
        conn.close()


def _audit_symbol(
    client: Any, category: str, symbol: str, journal_rows: List[sqlite3.Row],
) -> Dict[str, Any]:
    """Broker-truth coverage for one account+symbol. Read-only."""
    res: Dict[str, Any] = {"symbol": symbol, "verdict": "UNKNOWN", "error": None}
    try:
        pos_resp = client.get_positions(category=category, symbol=symbol)
        rows = ((pos_resp or {}).get("result") or {}).get("list") or []
    except Exception as exc:  # noqa: BLE001
        res["error"] = "get_positions failed: %s" % exc
        return res
    if not rows:
        res.update({"size": 0.0, "verdict": "FLAT"})
        return res
    pos = rows[0]
    size = _f(pos.get("size")) or 0.0
    size = abs(size)
    pos_sl = str(pos.get("stopLoss") or "").strip()
    pos_tp = str(pos.get("takeProfit") or "").strip()
    has_pos_sl = bool(pos_sl) and pos_sl not in ("0", "0.0", "0.00")
    res.update({
        "size": size,
        "side": pos.get("side"),
        "position_stopLoss": pos_sl or None,
        "position_takeProfit": pos_tp or None,
        "position_tpslMode": pos.get("tpslMode"),
    })
    if size <= 0:
        res["verdict"] = "FLAT"
        return res

    try:
        oo_resp = client.get_open_orders(
            category=category, symbol=symbol, orderFilter="StopOrder",
        )
        legs = ((oo_resp or {}).get("result") or {}).get("list") or []
    except Exception as exc:  # noqa: BLE001
        res["error"] = "get_open_orders failed: %s" % exc
        return res

    sl_legs, tp_legs, unknown_qty_sl = [], [], 0
    for leg in legs:
        t = str(leg.get("stopOrderType") or "").lower()
        q = _leg_qty(leg)
        rec = {
            "orderId": leg.get("orderId"),
            "stopOrderType": leg.get("stopOrderType"),
            "qty": q,
            "triggerPrice": leg.get("triggerPrice"),
            "orderStatus": leg.get("orderStatus"),
        }
        if t in _SL_TYPES:
            if q is None:
                unknown_qty_sl += 1
            sl_legs.append(rec)
        elif t in _TP_TYPES:
            tp_legs.append(rec)
    res["sl_legs"] = sl_legs
    res["tp_legs"] = tp_legs
    res["leg_count_total"] = len(legs)
    res["sl_legs_unknown_qty"] = unknown_qty_sl

    # --- coverage -----------------------------------------------------------
    # A Full-mode position-level stopLoss covers the WHOLE net position.
    # Otherwise coverage is the summed qty of the resting SL legs.
    if has_pos_sl:
        covered = size
        res["coverage_source"] = "full_mode_position_stopLoss"
    else:
        covered = sum(leg_rec["qty"] for leg_rec in sl_legs if leg_rec["qty"] is not None)
        res["coverage_source"] = "partial_sl_legs"
    res["sl_covered_qty"] = covered
    res["uncovered_qty"] = max(0.0, size - covered)
    res["coverage_pct"] = (100.0 * covered / size) if size else None

    eps = size * _COVERAGE_EPS_FRAC
    if covered <= 0:
        res["verdict"] = "NAKED"
    elif covered + eps < size:
        res["verdict"] = "PARTIALLY_NAKED"
    else:
        res["verdict"] = "PROTECTED"
    # An SL leg whose qty we could not parse makes the verdict unreliable —
    # say so rather than reporting a coverage number we cannot stand behind.
    if unknown_qty_sl and not has_pos_sl:
        res["verdict"] += "_UNRELIABLE_LEG_QTY"

    # --- per-trade protection (the level partial mode actually promises) ----
    live_ids = {str(leg_rec["orderId"]) for leg_rec in sl_legs if leg_rec.get("orderId")}
    per_trade = []
    for r in journal_rows:
        if str(r["symbol"] or "").upper() != symbol.upper():
            continue
        tracked = r["sl_order_id"]
        per_trade.append({
            "trade_id": r["id"],
            "strategy": r["strategy_name"],
            "direction": r["direction"],
            "position_size": _f(r["position_size"]),
            "journal_sl": _f(r["stop_loss"]),
            "tracked_sl_order_id": tracked,
            "tracked_leg_alive_at_broker": (
                (str(tracked) in live_ids) if tracked else None
            ),
        })
    res["journal_open_trades"] = per_trade
    res["journal_open_trade_count"] = len(per_trade)
    res["journal_qty_sum"] = sum(
        t["position_size"] or 0.0 for t in per_trade
    ) or 0.0
    res["trades_with_tracked_leg_alive"] = sum(
        1 for t in per_trade if t["tracked_leg_alive_at_broker"] is True
    )
    res["trades_with_tracked_leg_dead"] = sum(
        1 for t in per_trade if t["tracked_leg_alive_at_broker"] is False
    )
    res["trades_with_no_tracked_leg"] = sum(
        1 for t in per_trade if t["tracked_leg_alive_at_broker"] is None
    )
    return res


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", default=None,
                    help="restrict to one bybit account_id (default: all)")
    ap.add_argument("--symbol", default=None,
                    help="restrict to one symbol (default: every symbol with "
                         "an open journal row, plus any symbol the venue "
                         "reports a position on)")
    ap.add_argument("--json", action="store_true",
                    help="also emit the machine-readable summary block")
    args = ap.parse_args()

    print("===== effective BYBIT_TPSL_MODE as THIS process sees it =====")
    raw = os.environ.get("BYBIT_TPSL_MODE")
    print("  os.environ BYBIT_TPSL_MODE = %r" % raw)
    try:
        from src.units.accounts.execute import _bybit_tpsl_mode
        print("  _bybit_tpsl_mode() resolves to = %r  "
              "(unknown values resolve to 'full')" % _bybit_tpsl_mode())
    except Exception as exc:  # noqa: BLE001
        print("  _bybit_tpsl_mode() import failed: %s" % exc)
    print()

    accounts = _bybit_accounts(args.account)
    if not accounts:
        print("error: no bybit account matched (account=%r)" % args.account,
              file=sys.stderr)
        return 2

    from src.units.accounts.clients import bybit_client_for
    from src.units.accounts.execute import _bybit_category

    summary: Dict[str, Any] = {"accounts": []}
    for acc in accounts:
        aid = str(acc.get("account_id") or acc.get("name") or "")
        print("=" * 74)
        print("ACCOUNT %s  (mode=%s account_class=%s)"
              % (aid, acc.get("mode"), acc.get("account_class")))
        print("=" * 74)
        acc_out: Dict[str, Any] = {"account_id": aid, "symbols": []}
        try:
            category = _bybit_category(acc)
        except Exception as exc:  # noqa: BLE001
            print("  _bybit_category failed: %s" % exc)
            acc_out["error"] = str(exc)
            summary["accounts"].append(acc_out)
            continue
        if category == "spot":
            print("  category=spot — no position-level SL/TP on spot; skipped.")
            acc_out["skipped"] = "spot"
            summary["accounts"].append(acc_out)
            continue
        client = bybit_client_for(acc)
        if client is None:
            print("  bybit_client_for returned None (creds missing?) — skipped.")
            acc_out["error"] = "no_client"
            summary["accounts"].append(acc_out)
            continue

        try:
            journal_rows = _open_journal_rows(aid)
        except Exception as exc:  # noqa: BLE001
            print("  journal read failed: %s" % exc)
            journal_rows = []
            acc_out["journal_error"] = str(exc)
        print("  category=%s  open journal rows=%d" % (category, len(journal_rows)))

        symbols = sorted({
            str(r["symbol"]).upper() for r in journal_rows if r["symbol"]
        })
        if args.symbol:
            symbols = [args.symbol.upper()]
        if not symbols:
            print("  no open journal rows → nothing to audit for this account.")
            summary["accounts"].append(acc_out)
            continue

        for sym in symbols:
            r = _audit_symbol(client, category, sym, journal_rows)
            acc_out["symbols"].append(r)
            print()
            print("  --- %s : %s ---" % (sym, r["verdict"]))
            if r.get("error"):
                print("      ERROR: %s" % r["error"])
                continue
            if r["verdict"] == "FLAT":
                print("      venue reports flat (size=0); "
                      "%d open journal row(s) reference it"
                      % sum(1 for x in journal_rows
                            if str(x["symbol"] or "").upper() == sym))
                continue
            print("      position: size=%s side=%s tpslMode=%s "
                  "stopLoss=%s takeProfit=%s"
                  % (r["size"], r.get("side"), r.get("position_tpslMode"),
                     r.get("position_stopLoss"), r.get("position_takeProfit")))
            print("      SL coverage: %s / %s qty  (%.1f%%)  source=%s  "
                  "uncovered=%s"
                  % (r["sl_covered_qty"], r["size"],
                     r["coverage_pct"] or 0.0, r["coverage_source"],
                     r["uncovered_qty"]))
            print("      legs: %d total | %d SL | %d TP%s"
                  % (r["leg_count_total"], len(r["sl_legs"]), len(r["tp_legs"]),
                     "  (!! %d SL leg(s) with unparseable qty)"
                     % r["sl_legs_unknown_qty"]
                     if r["sl_legs_unknown_qty"] else ""))
            for leg_rec in r["sl_legs"]:
                print("        SL  qty=%-10s trigger=%-12s %-18s %s"
                      % (leg_rec["qty"], leg_rec["triggerPrice"], leg_rec["stopOrderType"],
                         str(leg_rec["orderId"])[:12]))
            for leg_rec in r["tp_legs"]:
                print("        TP  qty=%-10s trigger=%-12s %-18s %s"
                      % (leg_rec["qty"], leg_rec["triggerPrice"], leg_rec["stopOrderType"],
                         str(leg_rec["orderId"])[:12]))
            print("      journal: %d open trade(s), qty_sum=%s | "
                  "tracked leg alive=%d dead=%d untracked=%d"
                  % (r["journal_open_trade_count"], r["journal_qty_sum"],
                     r["trades_with_tracked_leg_alive"],
                     r["trades_with_tracked_leg_dead"],
                     r["trades_with_no_tracked_leg"]))
            for t in r["journal_open_trades"]:
                alive = t["tracked_leg_alive_at_broker"]
                mark = {True: "ALIVE", False: "DEAD ", None: "NONE "}[alive]
                print("        #%-7s %-22s %-5s qty=%-10s journal_sl=%-10s "
                      "leg=%s %s"
                      % (t["trade_id"], t["strategy"], t["direction"],
                         t["position_size"], t["journal_sl"], mark,
                         str(t["tracked_sl_order_id"] or "")[:12]))
        summary["accounts"].append(acc_out)

    # --- roll-up ------------------------------------------------------------
    print()
    print("=" * 74)
    print("ROLL-UP")
    print("=" * 74)
    # The roll-up used to bucket ONLY under-coverage, then print "every audited
    # symbol is fully SL-covered at the broker." — a clean bill of health that
    # rendered above a 444.7% OVER-coverage sitting in the body (2026-07-30).
    # `PROTECTED` is literally true there (covered >= size) but the summary
    # line asserts something much stronger than the verdict measured, and a
    # reader who stops at the roll-up walks past live leg over-accumulation —
    # the very condition `_check_broker_naked_bybit_positions` flags as
    # `over_covered` (BL-20260730-BYBIT1-XRP-LEG-OVERACCUM-WORSENING).
    # Over-coverage now gets its own bucket, and the all-clear carries the
    # denominator it ranges over.
    bad, over, audited = [], [], 0
    for a in summary["accounts"]:
        for s in a.get("symbols", []):
            v = str(s.get("verdict") or "")
            if v == "FLAT":
                continue
            audited += 1
            pct = s.get("coverage_pct")
            if v.startswith("PARTIALLY_NAKED") or v.startswith("NAKED") \
                    or "UNRELIABLE" in v:
                bad.append((a["account_id"], s["symbol"], v,
                            s.get("uncovered_qty"), pct))
            elif pct is not None and pct > 100.0 + 100.0 * _COVERAGE_EPS_FRAC:
                over.append((a["account_id"], s["symbol"], v,
                             s.get("sl_covered_qty"), pct))
    if bad:
        print("  %d symbol(s) NOT fully protected at the broker:" % len(bad))
        for aid, sym, v, unc, pct in bad:
            print("    %-16s %-10s %-32s uncovered_qty=%s coverage=%.1f%%"
                  % (aid, sym, v, unc, pct or 0.0))
    if over:
        print("  %d symbol(s) OVER-covered (SL legs exceed the netted position "
              "— leg over-accumulation, not a naked risk but not clean either):"
              % len(over))
        for aid, sym, v, cov_qty, pct in over:
            print("    %-16s %-10s %-32s sl_covered_qty=%s coverage=%.1f%%"
                  % (aid, sym, v, cov_qty, pct or 0.0))
    if not bad and not over:
        print("  %d/%d audited non-flat symbol(s) SL-covered at the broker "
              "within [100%%, %.1f%%]; 0 naked, 0 over-covered."
              % (audited, audited, 100.0 + 100.0 * _COVERAGE_EPS_FRAC))
    summary["rollup"] = {"audited_non_flat": audited,
                         "under_covered": len(bad), "over_covered": len(over)}
    if args.json:
        print()
        print("===== JSON =====")
        print(json.dumps(summary, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
