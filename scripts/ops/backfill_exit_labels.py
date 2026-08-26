#!/usr/bin/env python3
"""Backfill the exit LABEL on rows that were priced after they were closed.

WHY THIS EXISTS
---------------
``_close_trade_from_order_status``'s no-record fallback hard-codes
``exit_reason='reconciler_filled'`` and leaves ``exit_price`` NULL — correctly,
because at that moment there is no price to classify against. Two sweeps later
supply the price: ``_sweep_pending_pnl_from_bybit`` (broker truth) and
``_sweep_local_pnl_for_unpriced`` (anchored / mark-derived). Until #10151 the
first left the label frozen; until this change's sibling commit, so did the
second. Every row priced before those fixes still carries the generic label.

MEASURED 2026-08-23 over the whole live journal (1,309 closed non-backtest rows):

    still carrying the generic reason ............ 578
    minus reduce legs (guard 1) .................. 497   <- the population here
      resolvable off a MEASURED price ............ 156   (123 sl, 33 tp)
      resolvable off an ESTIMATED price ..........  35   ( 21 sl, 14 tp)
      no bracket touch (honest `unresolved`) ...... 198
      REFUSED — fabricated price .................. 105
      REFUSED — unverified price ..................   3

WHAT IT REFUSES TO DO, AND WHY THAT IS THE WHOLE DESIGN
-------------------------------------------------------
``_classify_broker_exit`` compares a price to the package bracket. It is
provenance-BLIND and cannot know whether the price it was handed is a broker
fill or ``local_markprice`` — the market read at SWEEP time, hours after the
exit. Comparing THAT to the bracket does not recover a lost label; it
manufactures an sl/tp verdict out of later, unrelated price action. So the
price's provenance decides whether a label is derivable at all:

  * MEASURED  -> classify; stamp ``price_vs_pkg_bracket``
  * ESTIMATED -> classify; stamp ``price_vs_pkg_bracket_est_price``
                (an inference on an inference — it must not read as the stronger
                verdict, which is the only reason it is a distinct value)
  * FABRICATED / UNVERIFIED -> REFUSE; stamp ``refused_unmeasured_price``

The refusal is STAMPED rather than skipped. The ABSENCE of
``exit_reason_source`` is load-bearing — it is the 100% signature that made this
defect class readable in the first place — so a row we looked at and declined
must be distinguishable from a row the classifier never reached.

It does NOT touch ``pnl``, ``exit_price``, or any monetary field. It writes one
label and its provenance, and records the prior value under
``notes.pre_backfill_exit_reason`` so the operation is reversible from the row.

DRY-RUN BY DEFAULT. ``--apply`` is required to write.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from collections import Counter
from typing import Any, Dict, Optional, Tuple

# ``parents[2]`` — scripts/ops/<file> -> repo root. It walked only TWO levels
# (landing on ``scripts/``) from the day this shipped, so ``import src`` raised
# ModuleNotFoundError and the script could not run AT ALL: not from the repo
# root, not from the Tier-2 wrapper, which invokes it by absolute path with no
# PYTHONPATH and no cd. Every sibling backfill in this directory uses
# ``parents[2]``; this was the only one that did not. The wrapper runs
# ``--self-test`` as a precondition and aborts on a non-zero exit, so the action
# was fail-SAFE — it could never half-apply — but it was also inert, which is
# why 0 rows on the live journal carried ``pre_backfill_exit_reason`` three days
# after the tool merged. BL-20260826-BACKFILL-EXIT-LABELS-CANNOT-IMPORT-SRC.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.runtime import provenance as prov  # noqa: E402

GENERIC_REASONS = ("", "reconciler_filled")

LABEL_SOURCE_BY_BASIS = {
    prov.MEASURED: "price_vs_pkg_bracket",
    prov.ESTIMATED: "price_vs_pkg_bracket_est_price",
}


def _f(v: Any) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f > 0 else None


def _notes(raw: Any) -> Dict[str, Any]:
    try:
        d = json.loads(raw or "{}")
        return d if isinstance(d, dict) else {}
    except (TypeError, ValueError):
        return {}


def classify(direction: str, exit_price: float,
             sl: Optional[float], tp: Optional[float]) -> Optional[str]:
    """The SAME conservative inequality as ``_classify_broker_exit``.

    Kept as a pure function so the backfill and the live classifier can be
    compared directly; a second, subtly different rule would relabel history to
    a standard production does not use.
    """
    d = (direction or "").lower()
    if d not in ("long", "short"):
        return None
    if d == "long":
        if sl is not None and exit_price <= sl:
            return "sl"
        if tp is not None and exit_price >= tp:
            return "tp"
    else:
        if sl is not None and exit_price >= sl:
            return "sl"
        if tp is not None and exit_price <= tp:
            return "tp"
    return None


def plan(conn: sqlite3.Connection) -> Tuple[list, Counter]:
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT t.id, t.direction, t.exit_price, t.stop_loss, t.take_profit_1, "
        "       t.exit_reason, t.notes, t.setup_type, t.order_package_id, "
        "       p.sl AS pkg_sl, p.tp AS pkg_tp "
        "  FROM trades t "
        "  LEFT JOIN order_packages p "
        "         ON p.order_package_id = t.order_package_id "
        " WHERE t.status = 'closed' "
        "   AND COALESCE(t.is_backtest, 0) = 0 "
        # Guard 1 — reduce legs. Their bracket can be INVERTED relative to the
        # order direction, so classifying one mislabels it as sl/tp. Same
        # predicate shape as the live sweeps'.
        "   AND COALESCE(t.setup_type, '') != 'intent_reduce' "
        "   AND COALESCE(t.notes, '') NOT LIKE '%\"intent_reduce\": true%' "
    ).fetchall()

    stats: Counter = Counter()
    out = []
    for r in rows:
        stats["scanned"] += 1
        # Guard 2 — only a row still carrying the GENERIC reason.
        if str(r["exit_reason"] or "").strip() not in GENERIC_REASONS:
            stats["skip_has_real_reason"] += 1
            continue
        n = _notes(r["notes"])
        if n.get("exit_reason_source"):
            stats["skip_already_classified"] += 1
            continue
        px = _f(r["exit_price"])
        if px is None:
            stats["skip_no_price"] += 1
            continue
        stats["eligible"] += 1

        basis = prov.classify(n.get("exit_price_source"), "exit_price_source")
        if basis not in LABEL_SOURCE_BY_BASIS:
            stats[f"refused_{basis}"] += 1
            out.append({"id": r["id"], "action": "refuse", "basis": basis,
                        "source": prov.EXIT_LABEL_REFUSED_UNMEASURED,
                        "new_reason": None})
            continue

        sl = _f(r["pkg_sl"]) or _f(r["stop_loss"])
        tp = _f(r["pkg_tp"]) or _f(r["take_profit_1"])
        resolved = classify(str(r["direction"] or ""), px, sl, tp)
        if resolved:
            stats[f"relabel_{resolved}_{basis}"] += 1
            out.append({"id": r["id"], "action": "relabel", "basis": basis,
                        "source": LABEL_SOURCE_BY_BASIS[basis],
                        "new_reason": resolved,
                        "old_reason": str(r["exit_reason"] or "")})
        else:
            stats[f"unresolved_{basis}"] += 1
            out.append({"id": r["id"], "action": "unresolved", "basis": basis,
                        "source": "unresolved", "new_reason": None})
    return out, stats


def apply(conn: sqlite3.Connection, planned: list) -> int:
    written = 0
    for p in planned:
        cur = conn.execute("SELECT notes, exit_reason FROM trades WHERE id = ?",
                           (p["id"],)).fetchone()
        if cur is None:
            continue
        n = _notes(cur[0])
        n["exit_reason_source"] = p["source"]
        if p["basis"]:
            n["exit_reason_price_basis"] = p["basis"]
        if p["new_reason"]:
            # Reversible from the row itself.
            n["pre_backfill_exit_reason"] = str(cur[1] or "")
            conn.execute("UPDATE trades SET exit_reason = ?, notes = ? WHERE id = ?",
                         (p["new_reason"], json.dumps(n), p["id"]))
        else:
            conn.execute("UPDATE trades SET notes = ? WHERE id = ?",
                         (json.dumps(n), p["id"]))
        written += 1
    conn.commit()
    return written


def _self_test() -> int:
    """Planted controls: every branch must be shown able to fire."""
    checks = []

    def ck(name, got, want):
        ok = got == want
        checks.append(ok)
        print(f"  {'ok ' if ok else 'FAIL'} {name}: got {got!r} want {want!r}")

    ck("long stopped out", classify("long", 90.0, 95.0, 110.0), "sl")
    ck("long target hit", classify("long", 111.0, 95.0, 110.0), "tp")
    ck("long mid-range", classify("long", 100.0, 95.0, 110.0), None)
    ck("short stopped out", classify("short", 106.0, 105.0, 90.0), "sl")
    ck("short target hit", classify("short", 89.0, 105.0, 90.0), "tp")
    ck("short mid-range", classify("short", 98.0, 105.0, 90.0), None)
    ck("unknown direction", classify("", 90.0, 95.0, 110.0), None)
    ck("no levels", classify("long", 90.0, None, None), None)
    # The basis gate — the control this script exists for.
    ck("markprice is fabricated",
       prov.classify("local_markprice", "exit_price_source") in LABEL_SOURCE_BY_BASIS,
       False)
    ck("candle_at_close is labellable",
       prov.classify("candle_at_close", "exit_price_source") in LABEL_SOURCE_BY_BASIS,
       True)
    ck("bybit truth is labellable",
       prov.classify("bybit_closed_pnl", "exit_price_source") in LABEL_SOURCE_BY_BASIS,
       True)
    ck("estimated stamps the weaker source",
       LABEL_SOURCE_BY_BASIS[prov.ESTIMATED], "price_vs_pkg_bracket_est_price")
    ok = sum(checks)
    print(f"self-test: {ok}/{len(checks)} passed")
    return 0 if ok == len(checks) else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db")
    ap.add_argument("--apply", action="store_true",
                    help="write. Without it this is a read-only dry run.")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)

    if a.self_test:
        return _self_test()

    db = a.db
    if not db:
        from src.utils.paths import trade_journal_db_path
        db = str(trade_journal_db_path())

    # Read-only unless --apply, so a dry run cannot mutate the money DB even by
    # accident.
    uri = f"file:{db}" + ("" if a.apply else "?mode=ro")
    conn = sqlite3.connect(uri, uri=True)
    try:
        planned, stats = plan(conn)
        if a.json:
            print(json.dumps({"stats": dict(stats), "planned": planned}, indent=1))
        else:
            print(f"backfill-exit-labels   db={db}")
            for k in sorted(stats):
                print(f"  {k:34s} {stats[k]}")
            relabels = [p for p in planned if p["action"] == "relabel"]
            print(f"\n  ROWS THAT WOULD CHANGE exit_reason: {len(relabels)}")
            print(f"  rows stamped-only (unresolved/refused): "
                  f"{len(planned) - len(relabels)}")
        if a.apply:
            n = apply(conn, planned)
            print(f"\nAPPLIED — {n} row(s) stamped.")
        else:
            print("\nDRY RUN — nothing written. Re-run with --apply.")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
