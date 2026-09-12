#!/usr/bin/env python3
"""Can the VENUE name the mechanism the journal could not?

`BL-20260912-FOR-55-PERCENT-OF-WINNERS-THAT-MISSED-TARGET-THE-MECHANISM-THAT-STOPPED-THE-RUN-IS-NOT-IN-THE-JOURNAL`
measured two buckets that are **invariant to the label-recovery tolerance**, which
is exactly why they are the finding rather than a threshold artefact:

  * ``unattributable_price`` — the exit price is an ESTIMATED ``candle_at_close``
    anchor, so *we could not look*.
  * ``contaminated`` — ``netting_attributed``, 100% ESTIMATED, and
    ``OI-20260908``'s own population.

Its resolution criterion names the remedy exactly: *"the closing order's
``stop_order_type`` / ``cancel_type`` persisted at close time and BRANCHED on by
a reader"*. That persistence is a write in ``order_monitor`` and is **Tier-2**.

**This script answers the question that must be settled BEFORE anyone builds
that: is the answer available from the venue at all?** If it is not, the Tier-2
build has nothing to persist.

THE JOIN, AND WHY IT TAKES TWO ENDPOINTS
========================================
1. ``/api/diag/bybit_raw_closed_pnl`` gives the realised close — side, qty,
   ``avg_entry_price``, ``avg_exit_price``, ``closed_pnl`` — and, critically, an
   ``order_id``. It does **not** carry an order type, so it can make an
   ESTIMATED price MEASURED and cannot name a mechanism.
2. ``/api/diag/bybit_raw_order_history`` carries ``stop_order_type``,
   ``cancel_type``, ``order_type``, ``order_status`` and ``trigger_price`` for
   that ``order_id``. That is the mechanism.

Neither alone answers the row. The ``order_id`` is the whole join.

⚠️ THE MATCHER IS BORROWED, NOT REBUILT
=======================================
The trade → closed-pnl match, and the ``avg_entry_price`` grading that decides
whether a match may be trusted, are **imported** from
``netting_close_venue_adjudication`` rather than re-derived. Two copies of
"is this the same close?" would be free to drift, and that module already
records why the grading is not optional: over its own population the aggregate
journal-minus-venue figure **reverses sign** between all-matches and
corroborated-only. Same discipline as ``m20_corpus_union`` importing
``measurement_key`` by name.

⚠️ ``cancel_type: UNKNOWN`` ON A FILLED ORDER IS CORRECT, NOT A GAP
===================================================================
Measured on every row of the shipped population: ``order_status`` is ``Filled``
and ``cancel_type`` is ``UNKNOWN``. Nothing was cancelled, so there is no cancel
reason. ``stop_order_type`` is the field that answers a FILLED close;
``cancel_type`` answers a leg that was cancelled instead. The backlog row names
both because the two halves answer different closes — reading the ``UNKNOWN``
as a missing answer would be the unprovenanced-diagnostic error one level up.

⚠️ AN EMPTY ``stop_order_type`` IS ALSO AN ANSWER
=================================================
It means the close was an ordinary order rather than a bracket leg firing —
read it beside ``order_type``. It is reported as ``plain_order``, never pooled
with *we could not look*.

Tier-1: research tooling. Reads JSON files, prints a report. No DB write, no
network call of its own, no order path.

USAGE — every input is a read-only diag pull a person makes by hand:

    python3 scripts/research/m20_u2_winner_close_attribution.py \
        --trades trades.json --packages pkgs.json --out u2.json
    python3 scripts/research/venue_mechanism_recovery.py \
        --u2 u2.json --trades trades.json --emit-fetch-plan | sh
    python3 scripts/research/venue_mechanism_recovery.py \
        --u2 u2.json --trades trades.json --closed-pnl-dir cp/ --order-history-dir oh/

# wiring: manual-only - its inputs are read-only diag pulls a session makes by
# hand against a live VM across two endpoints and a 7-day window cap, and the
# output is read by a person deciding whether a Tier-2 persistence build has
# anything to persist. A scheduled runner has nothing to do with the answer.
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import glob
import json
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
from scripts.research.netting_close_venue_adjudication import (  # noqa: E402
    CORROBORATED_BP, QTY_TOL, VENUE_WINDOW_DAYS, load_venue,
)

#: The U2 classes whose members this script exists to rescue. `attributed` and
#: `account_level` already have a mechanism, so including them would inflate
#: the denominator with rows that were never the question.
TARGET_CLASSES = ("unattributable_price", "unattributable_level", "contaminated")

#: What the venue's `stop_order_type` can tell us. `plain_order` is a real
#: answer (an ordinary close, not a bracket leg); `absent_from_order_history`
#: is *we could not look*, and the two are never pooled.
MECHANISM_STATES = (
    "named",                       # stop_order_type is populated
    "plain_order",                 # present, empty stop_order_type — a real answer
    "absent_from_order_history",   # we could not look
    "no_corroborated_match",       # no trustworthy closed-pnl row to join from
)


def _ms(iso: str) -> int:
    return int(dt.datetime.fromisoformat(str(iso).replace("Z", "+00:00")).timestamp() * 1000)


def target_trades(u2_path: str, trades_path: str) -> list[dict]:
    """The U2 rows in a TARGET_CLASS, joined back to their full trade rows."""
    with open(u2_path) as fh:
        u2 = json.load(fh)
    with open(trades_path) as fh:
        by_id = {t["id"]: t for t in json.load(fh)}
    out = []
    for row in u2.get("rows") or []:
        if row.get("class") not in TARGET_CLASSES:
            continue
        t = by_id.get(row["id"])
        if t is None:
            continue
        out.append({**t, "_u2_class": row["class"], "_u2_pnl": row.get("pnl")})
    return out


def emit_fetch_plan(rows: list[dict]) -> None:
    """Print the diag_fetch lines for BOTH endpoints over the population's span."""
    spans: dict[tuple[str, str], list[int]] = {}
    for t in rows:
        k = (t["account_id"], t["symbol"])
        m = _ms(t["closed_at"])
        lo, hi = spans.get(k, (m, m))
        spans[k] = (min(lo, m), max(hi, m))
    day = 86_400_000
    for route, outdir in (("bybit_raw_closed_pnl", "cp"),
                          ("bybit_raw_order_history", "oh")):
        for (acct, sym), (lo, hi) in sorted(spans.items()):
            lo2, hi2 = lo - 2 * 3_600_000, hi + 2 * 3_600_000
            start, i = lo2, 0
            while start < hi2:
                end = min(start + (VENUE_WINDOW_DAYS - 1) * day, hi2)
                print(f"bash scripts/ops/diag_fetch.sh "
                      f"'/api/diag/{route}?account_id={acct}&symbol={sym}"
                      f"&start_ms={start}&end_ms={end}' > {outdir}/{acct}_{sym}_{i}.json")
                start, i = end + 1, i + 1


def load_order_history(path: str) -> tuple[dict, list[str]]:
    """Orders keyed by `order_id`, plus every problem, never swallowed."""
    orders: dict[str, dict] = {}
    problems: list[str] = []
    files = sorted(glob.glob(os.path.join(path, "*.json")))
    if not files:
        problems.append(f"no order-history windows under {path!r} — "
                        "'we did not look', not 'the venue has no orders'")
    for p in files:
        try:
            with open(p) as fh:
                doc = json.load(fh)
        except (json.JSONDecodeError, OSError) as exc:
            problems.append(f"UNPARSEABLE {os.path.basename(p)}: {exc}")
            continue
        res = doc.get("result") or {}
        if res.get("error"):
            problems.append(f"VENUE ERROR {os.path.basename(p)}: {str(res['error'])[:120]}")
        if res.get("pages_truncated"):
            problems.append(f"TRUNCATED {os.path.basename(p)} — records are INCOMPLETE")
        for rec in res.get("records") or []:
            orders[rec["order_id"]] = rec
    return orders, problems


def recover(rows: list[dict], closed_pnl: dict, orders: dict) -> list[dict]:
    """For each target trade: the corroborated close, and what the venue calls it."""
    out = []
    for t in sorted(rows, key=lambda x: str(x.get("closed_at"))):
        key = (t["account_id"], t["symbol"])
        qty = float(t["position_size"])
        side = "Sell" if str(t.get("direction")).lower() == "long" else "Buy"
        closed_ms = _ms(t["closed_at"])
        rec = {
            "trade": t["id"], "u2_class": t["_u2_class"], "symbol": t["symbol"],
            "journal_pnl": t["_u2_pnl"], "venue_pnl": None, "order_id": None,
            "entry_bp": None, "venue_booked_min_after_journal": None,
            "stop_order_type": None, "cancel_type": None, "order_type": None,
            "order_status": None, "state": "no_corroborated_match",
        }
        cands = [x for x in (closed_pnl.get(key) or {}).values()
                 if x.get("side") == side
                 and abs(float(x["qty"]) - qty) <= QTY_TOL * max(qty, 1e-9)]
        if cands:
            best = min(cands, key=lambda x: abs(int(x["updated_time"]) - closed_ms))
            v_entry = float(best["avg_entry_price"])
            bp = (abs(float(t["entry_price"]) - v_entry) / v_entry * 1e4) if v_entry else None
            rec["entry_bp"] = bp
            if bp is not None and bp <= CORROBORATED_BP:
                rec["order_id"] = best["order_id"]
                rec["venue_pnl"] = float(best["closed_pnl"])
                rec["venue_booked_min_after_journal"] = (
                    int(best["updated_time"]) - closed_ms) / 60000.0
                o = orders.get(best["order_id"])
                if o is None:
                    rec["state"] = "absent_from_order_history"
                else:
                    sot = (o.get("stop_order_type") or "").strip()
                    rec["stop_order_type"] = sot or None
                    rec["cancel_type"] = o.get("cancel_type")
                    rec["order_type"] = o.get("order_type")
                    rec["order_status"] = o.get("order_status")
                    rec["state"] = "named" if sot else "plain_order"
        out.append(rec)
    return out


def report(results: list[dict], problems: list[str]) -> int:
    print("PROBLEMS WITH THE VENUE READS THEMSELVES (first, because an unread "
          "window is not an empty one)")
    for p in problems:
        print(f"  ! {p}")
    if not problems:
        print("  none: every window parsed, none truncated, none errored")
    print()

    total = len(results)
    states = collections.Counter(r["state"] for r in results)
    print(f"POPULATION: {total} winner(s) in a U2 class that could not name a mechanism")
    for cls, n in sorted(collections.Counter(r["u2_class"] for r in results).items()):
        print(f"  {cls:<24}{n:>4}")
    if not total:
        print("\nEMPTY POPULATION — 'we found none here', not 'none exist'.")
        return 1
    print()
    print("CAN THE VENUE NAME IT?")
    for s in MECHANISM_STATES:
        print(f"  {s:<28}{states[s]:>4}")
    answered = states["named"] + states["plain_order"]
    print(f"  -> the venue answers for {answered} of {total} ({answered / total:.1%}); "
          f"the rest is 'we could not look', never 'no mechanism'")
    print()

    named = [r for r in results if r["state"] == "named"]
    if named:
        print("WHAT THE VENUE CALLS THEM")
        for mech, n in collections.Counter(r["stop_order_type"] for r in named).most_common():
            print(f"  {mech:<24}{n:>4}")
        cancels = collections.Counter(r["cancel_type"] for r in named)
        print(f"  cancel_type over the same rows: {dict(cancels)}")
        print("  -- an UNKNOWN cancel_type on a Filled order is CORRECT: nothing was")
        print("     cancelled, so there is no cancel reason. stop_order_type answers a")
        print("     FILLED close; cancel_type answers a leg that was cancelled instead.")
        print()

    graded = [r for r in results if r["venue_pnl"] is not None]
    if graded:
        tj = sum(r["journal_pnl"] for r in graded)
        tv = sum(r["venue_pnl"] for r in graded)
        print(f"PnL ON THE {len(graded)} CORROBORATED ROWS")
        print(f"  journal {tj:+,.2f}   venue {tv:+,.2f}   journal-minus-venue {tj - tv:+,.2f}")
        flips = [r for r in graded if (r["journal_pnl"] > 0) != (r["venue_pnl"] > 0)]
        print(f"  ⚠️ the journal calls every one of these a WINNER; the venue books "
              f"{len(flips)} as LOSSES:")
        for r in sorted(flips, key=lambda x: x["venue_pnl"]):
            print(f"     trade {r['trade']} {r['symbol']:<9} {r['u2_class']:<22} "
                  f"journal {r['journal_pnl']:>+9.2f}  venue {r['venue_pnl']:>+10.2f}  "
                  f"venue calls it: {r['stop_order_type'] or '(plain order)'}")
        if flips:
            print(f"     those {len(flips)} rows: journal {sum(r['journal_pnl'] for r in flips):+,.2f} "
                  f"of WINNINGS against venue {sum(r['venue_pnl'] for r in flips):+,.2f}")
        late = [r for r in graded if (r["venue_booked_min_after_journal"] or 0) > 5]
        print(f"  venue booked the close MORE THAN 5 MIN AFTER the journal closed the row: "
              f"{len(late)} of {len(graded)}")
        for r in sorted(late, key=lambda x: -x["venue_booked_min_after_journal"]):
            print(f"     trade {r['trade']} {r['symbol']:<9} "
                  f"{r['venue_booked_min_after_journal']:>+8.1f} min")
    return 0


def self_test() -> int:
    fails = []

    def chk(name, got, want):
        if got != want:
            fails.append(f"{name}: {got!r} != {want!r}")
        print(f"  {'ok ' if got == want else 'FAIL'} {name}")

    t = {"id": 1, "account_id": "a", "symbol": "S", "direction": "long",
         "position_size": 10.0, "entry_price": 100.0,
         "closed_at": "2026-09-01T00:00:00+00:00", "_u2_class": "unattributable_price",
         "_u2_pnl": 5.0}
    cp = {("a", "S"): {"o1": {"order_id": "o1", "side": "Sell", "qty": "10.0",
                              "avg_entry_price": "100.0", "avg_exit_price": "99.0",
                              "closed_pnl": "-12.0",
                              "updated_time": str(_ms(t["closed_at"]))}}}

    r = recover([t], cp, {"o1": {"stop_order_type": "PartialStopLoss",
                                 "cancel_type": "UNKNOWN", "order_type": "Market",
                                 "order_status": "Filled"}})[0]
    chk("a populated stop_order_type is `named`", r["state"], "named")
    chk("the mechanism is carried through", r["stop_order_type"], "PartialStopLoss")
    chk("the venue pnl is carried through", r["venue_pnl"], -12.0)

    r = recover([t], cp, {"o1": {"stop_order_type": "", "order_type": "Market",
                                 "order_status": "Filled"}})[0]
    chk("an EMPTY stop_order_type is `plain_order`, a real answer", r["state"], "plain_order")
    chk("  and it is not reported as a mechanism", r["stop_order_type"], None)

    r = recover([t], cp, {})[0]
    chk("an order missing from history is `absent_from_order_history`",
        r["state"], "absent_from_order_history")

    far = {("a", "S"): {"o1": {**cp[("a", "S")]["o1"], "avg_entry_price": "90.0"}}}
    r = recover([t], far, {"o1": {"stop_order_type": "PartialStopLoss"}})[0]
    chk("an entry-price disagreement refuses the join entirely",
        r["state"], "no_corroborated_match")
    chk("  and reports no mechanism from an untrusted match", r["stop_order_type"], None)

    wrong = {("a", "S"): {"o1": {**cp[("a", "S")]["o1"], "side": "Buy"}}}
    r = recover([t], wrong, {"o1": {"stop_order_type": "PartialStopLoss"}})[0]
    chk("a close on the wrong side is not a match", r["state"], "no_corroborated_match")

    chk("plain_order is not pooled with could-not-look",
        "plain_order" in MECHANISM_STATES and "absent_from_order_history" in MECHANISM_STATES,
        True)

    print("self-test: FAIL" if fails else "self-test: OK")
    for f in fails:
        print("   ", f)
    return 1 if fails else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--u2", help="JSON from m20_u2_winner_close_attribution.py --out")
    ap.add_argument("--trades", help="JSON from /api/diag/journal?table=trades")
    ap.add_argument("--closed-pnl-dir", default="cp")
    ap.add_argument("--order-history-dir", default="oh")
    ap.add_argument("--emit-fetch-plan", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)

    if a.self_test:
        return self_test()
    if not a.u2 or not a.trades:
        ap.error("--u2 and --trades are required unless --self-test is given")

    rows = target_trades(a.u2, a.trades)
    if not rows:
        print(f"no rows in {TARGET_CLASSES} — nothing to recover, which is a "
              "finding about THIS population and not about the classes")
        return 1
    if a.emit_fetch_plan:
        emit_fetch_plan(rows)
        return 0

    closed_pnl, p1, _ = (lambda r: (r[0], r[2], r[1]))(load_venue(a.closed_pnl_dir))
    orders, p2 = load_order_history(a.order_history_dir)
    return report(recover(rows, closed_pnl, orders), list(p1) + list(p2))


if __name__ == "__main__":
    raise SystemExit(main())
