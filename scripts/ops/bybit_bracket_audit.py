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
from typing import Any, Dict, List, Optional, Tuple

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

# BL-20260821-BYBIT-BRACKET-AUDIT-HAS-NO-PRICE-AXIS.
# Relative tolerance when asking "does the resting leg sit where the journal
# declared". CHOSEN, not measured -- stated plainly because this file already
# insists elsewhere that a bound with no distribution behind it must say so.
# The reasoning: a genuine stop sits ~1-3% from entry, so 0.5% is comfortably
# below the smallest divergence that could matter while staying above venue
# tick rounding. Widening it hides real divergence; narrowing it turns tick
# noise into alarm fatigue, which this repo treats as a P1 in its own right.
_PRICE_EPS_FRAC = 0.005


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
    # ``verdict`` grades the STOP SIDE ONLY and always has. Stamped explicitly
    # because "PROTECTED" reads as a claim about the whole bracket, and this
    # script collected `tp_legs` from the start while never letting them reach
    # the verdict — so it printed PROTECTED over books with no take-profit at
    # all (BL-20260816-TARGET-NAKEDNESS-UNDETECTABLE-ON-ALPACA-AND-BYBIT). The
    # scope now travels WITH the value rather than living in a docstring the
    # reader of the output never sees.
    res["verdict_scope"] = "stop_side_only"

    # --- target side, graded SEPARATELY ------------------------------------
    # A stop and a take-profit are not interchangeable: a fully stop-covered
    # position with no target can only stop out or run. Three states, never
    # collapsed — `unknown` is "we could not grade it", NOT "absent".
    if pos_tp and pos_tp not in ("0", "0.0", "0.00"):
        res["target_state"] = "present"          # Full-mode position-level TP
        res["target_source"] = "full_mode_position_takeProfit"
    elif tp_legs:
        res["target_state"] = "present"
        res["target_source"] = "partial_tp_legs"
    else:
        res["target_state"] = "absent"
        res["target_source"] = None
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
    # --- price agreement, graded SEPARATELY from existence ------------------
    # BL-20260821-BYBIT-BRACKET-AUDIT-HAS-NO-PRICE-AXIS. Every other dimension
    # here grades a QUANTITY or an EXISTENCE, so a clean bill of health was
    # compatible with every stop resting at the wrong price. Both sides of the
    # comparison were already collected and simply never compared -- the leg's
    # `triggerPrice` and the row's `journal_sl` -- which is the
    # written-but-never-read shape `provenance-consumer-guard` exists to catch.
    #
    # THIS IS NOT HYPOTHETICAL. Under Full mode the venue holds ONE
    # position-level stopLoss for the whole netted position and each new open
    # REPLACES it, so N journal rows with N different declared stops all ride
    # the newest row's geometry and N-1 of them are necessarily wrong. That is
    # the documented Jun 21-23 mechanism behind BYBIT_TPSL_MODE=partial. The
    # audit could not see it.
    #
    # Mapping is 1:1 and never guessed: a row is compared to ITS OWN tracked
    # leg. Three states, never collapsed -- `agree` / `diverged` /
    # `ungradeable` -- because "no tracked id" and "prices match" are opposite
    # statements, and a row we could not check must never count as clean.
    pos_sl_f = _f(pos_sl) if has_pos_sl else None
    for t in per_trade:
        want = t["journal_sl"]
        if want is None or want <= 0:
            t["price_state"], t["price_basis"], t["broker_sl"] = "ungradeable", "no_journal_sl", None
            continue
        got, basis = None, None
        if has_pos_sl:
            # Full mode: every row is measured against the ONE position-level
            # stop, which is exactly how the netting defect surfaces.
            got, basis = pos_sl_f, "full_mode_position_stopLoss"
        elif t["tracked_leg_alive_at_broker"] is True:
            tracked_id = str(t["tracked_sl_order_id"])
            got = next((_f(leg_rec.get("triggerPrice")) for leg_rec in sl_legs
                        if str(leg_rec.get("orderId")) == tracked_id), None)
            basis = "tracked_partial_leg"
        t["broker_sl"], t["price_basis"] = got, basis
        if got is None or got <= 0:
            # Covers: no tracked id, a dead leg, an unparseable triggerPrice.
            # We did not look -- not "it agrees".
            t["price_state"] = "ungradeable"
            t["price_basis"] = basis or "no_tracked_live_leg"
        elif abs(got - want) > want * _PRICE_EPS_FRAC:
            t["price_state"] = "diverged"
        else:
            t["price_state"] = "agree"

    res["price_diverged_count"] = sum(1 for t in per_trade if t["price_state"] == "diverged")
    res["price_agree_count"] = sum(1 for t in per_trade if t["price_state"] == "agree")
    res["price_ungradeable_count"] = sum(1 for t in per_trade if t["price_state"] == "ungradeable")
    # Envelope state. `ungradeable` is reported when NOTHING could be graded,
    # so a symbol whose price axis is entirely unreadable can never present as
    # clean -- the denominator travels with the verdict.
    if not per_trade:
        res["price_state"] = "not_applicable"
    elif res["price_diverged_count"]:
        res["price_state"] = "diverged"
    elif res["price_agree_count"]:
        res["price_state"] = "agree"
    else:
        res["price_state"] = "ungradeable"

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
    # --- journal qty vs the exchange position ------------------------------
    # Measured 2026-08-16 on bybit_portfolio ETHUSDT: the exchange held 21.05
    # while the journal held two open rows summing 35.01, the exchange size
    # equalling ONE row's qty exactly — so the other row's 13.96 was backed by
    # nothing, and its tracked SL leg was DEAD. Coverage read 100% because it
    # is measured against the EXCHANGE size, which is the right denominator
    # for "is the venue position covered" and the wrong one for "is every
    # journal row backed". Both are worth knowing; only one was reported.
    j_sum = res["journal_qty_sum"] or 0.0
    res["journal_qty_excess"] = max(0.0, j_sum - size)
    res["journal_qty_divergent"] = res["journal_qty_excess"] > size * _COVERAGE_EPS_FRAC
    return res


def classify_rollup(summary: Dict[str, Any]) -> Tuple[List[Tuple], List[Tuple], int]:
    """Bucket audited symbols into ``(under_covered, over_covered, audited_n)``.

    Extracted from ``main`` so the roll-up's CLAIM is testable without a live
    broker (the live run then only has to verify integration).

    The roll-up used to bucket ONLY under-coverage and then print "every
    audited symbol is fully SL-covered at the broker" — a clean bill of health
    that rendered above a **444.7% OVER-coverage** sitting in the body
    (2026-07-30). ``PROTECTED`` is *literally true* there (``covered >= size``),
    which is what makes this sub-class A2 rather than a simple mislabel: the
    summary asserts something far stronger than the verdict measured, and a
    reader who stops at the roll-up walks past live leg over-accumulation — the
    very condition the runtime's own ``_check_broker_naked_bybit_positions``
    flags as ``over_covered`` (BL-20260730-BYBIT1-XRP-LEG-OVERACCUM-WORSENING).
    The audit and the runtime disagreed about the same position, and only the
    audit was being read.

    ``FLAT`` symbols are excluded from the denominator — a symbol with no
    position is not evidence of coverage either way, and counting it inflates
    the reassurance.
    """
    bad: List[Tuple] = []
    over: List[Tuple] = []
    audited = 0
    for a in summary.get("accounts", []):
        for s in a.get("symbols", []):
            v = str(s.get("verdict") or "")
            if v == "FLAT":
                continue
            audited += 1
            pct = s.get("coverage_pct")
            if v.startswith("PARTIALLY_NAKED") or v.startswith("NAKED") \
                    or "UNRELIABLE" in v:
                bad.append((a.get("account_id"), s.get("symbol"), v,
                            s.get("uncovered_qty"), pct))
            elif pct is not None and pct > 100.0 + 100.0 * _COVERAGE_EPS_FRAC:
                over.append((a.get("account_id"), s.get("symbol"), v,
                             s.get("sl_covered_qty"), pct))
    return bad, over, audited


# Every condition the roll-up is allowed to fold into a clean bill of health.
# THE LIST IS THE POINT. This function has now produced an all-clear over an
# ungraded condition three times: the 444.7% over-coverage (2026-07-30), and
# then target-nakedness AND a dead tracked leg with unbacked journal qty
# (2026-08-16, both sitting in the record while the summary printed
# "0 naked, 0 over-covered"). The first fix bolted on one bucket, which is
# exactly why there was a second and a third. So the all-clear is now computed
# FROM this list and prints the dimensions it actually cleared — a summary that
# names its own scope cannot overclaim, and a new concern field that no
# dimension reads is caught by `test_every_concern_field_is_graded`.
_ROLLUP_DIMENSIONS: Tuple[Tuple[str, str], ...] = (
    ("sl_coverage", "stop side covers the position"),
    ("over_coverage", "SL legs do not exceed the netted position"),
    ("target_present", "a resting take-profit exists"),
    ("tracked_legs_alive", "no open journal row's tracked SL leg is dead"),
    ("journal_qty_backed", "open journal qty does not exceed the exchange position"),
    ("price_agreement", "resting protective legs sit where the journal declared"),
)


def grade_rollup(summary: Dict[str, Any]) -> Tuple[Dict[str, List[Tuple]], int]:
    """Grade every declared dimension. Returns ``(findings_by_dimension, audited)``.

    Delegates the two stop-side dimensions to :func:`classify_rollup` rather
    than recomputing them — one definition of "under-covered", so the roll-up
    and the per-symbol verdict can never drift apart.
    """
    bad, over, audited = classify_rollup(summary)
    found: Dict[str, List[Tuple]] = {k: [] for k, _ in _ROLLUP_DIMENSIONS}
    found["sl_coverage"] = bad
    found["over_coverage"] = over
    for a in summary.get("accounts", []):
        for s in a.get("symbols", []):
            if str(s.get("verdict") or "") == "FLAT":
                continue
            aid, sym = a.get("account_id"), s.get("symbol")
            if s.get("target_state") == "absent":
                found["target_present"].append((aid, sym, "TARGET_ABSENT", None, None))
            if (s.get("trades_with_tracked_leg_dead") or 0) > 0:
                found["tracked_legs_alive"].append(
                    (aid, sym, "TRACKED_LEG_DEAD",
                     s.get("trades_with_tracked_leg_dead"), None))
            if s.get("journal_qty_divergent"):
                found["journal_qty_backed"].append(
                    (aid, sym, "JOURNAL_QTY_UNBACKED",
                     s.get("journal_qty_excess"), None))
            if (s.get("price_diverged_count") or 0) > 0:
                found["price_agreement"].append(
                    (aid, sym, "SL_PRICE_DIVERGED",
                     s.get("price_diverged_count"), s.get("price_state")))
    return found, audited


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
            # Print the verdict's SCOPE inline. "PROTECTED" alone reads as a
            # claim about the whole bracket; it has only ever graded the stop.
            _tgt = r.get("target_state")
            print("  --- %s : SL=%s | TARGET=%s ---"
                  % (sym, r["verdict"],
                     (_tgt or "—").upper() if r["verdict"] != "FLAT" else "—"))
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
    found, audited = grade_rollup(summary)
    bad, over = found["sl_coverage"], found["over_coverage"]
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
    if found["target_present"]:
        print("  %d symbol(s) TARGET-NAKED (fully stop-covered and holding NO "
              "take-profit — can only stop out or run):"
              % len(found["target_present"]))
        for aid, sym, v, _a, _b in found["target_present"]:
            print("    %-16s %-10s %s" % (aid, sym, v))
    if found["tracked_legs_alive"]:
        print("  %d symbol(s) with an open journal row whose tracked SL leg is "
              "DEAD at the broker:" % len(found["tracked_legs_alive"]))
        for aid, sym, v, n, _b in found["tracked_legs_alive"]:
            print("    %-16s %-10s %-24s dead_legs=%s" % (aid, sym, v, n))
    if found["journal_qty_backed"]:
        print("  %d symbol(s) whose open journal qty EXCEEDS the exchange "
              "position (rows backed by nothing):"
              % len(found["journal_qty_backed"]))
        for aid, sym, v, excess, _b in found["journal_qty_backed"]:
            print("    %-16s %-10s %-24s unbacked_qty=%s" % (aid, sym, v, excess))

    if not any(found.values()):
        # The all-clear NAMES ITS OWN SCOPE. It previously read
        # "…SL-covered…; 0 naked, 0 over-covered" over books that were
        # target-naked and carrying dead legs — literally true and far weaker
        # than it sounded. It can now only claim the dimensions it graded.
        print("  %d/%d audited non-flat symbol(s) clean across ALL %d graded "
              "dimension(s):" % (audited, audited, len(_ROLLUP_DIMENSIONS)))
        for name, desc in _ROLLUP_DIMENSIONS:
            print("      · %-20s %s" % (name, desc))
        print("    Nothing outside those dimensions was checked.")
    summary["rollup"] = {
        "audited_non_flat": audited,
        "under_covered": len(bad), "over_covered": len(over),
        "graded_dimensions": [n for n, _ in _ROLLUP_DIMENSIONS],
        "findings_by_dimension": {k: len(v) for k, v in found.items()},
    }
    if args.json:
        print()
        print("===== JSON =====")
        print(json.dumps(summary, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
