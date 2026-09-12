#!/usr/bin/env python3
"""Did the VENUE book the close that a `netting_attributed` journal row records?

Discharges option (b) of
``BL-20260912-THE-NETTING-ATTRIBUTED-PATH-CARRIES-THE-LARGEST-SHARE-OF-THE-WINNER-COLLAPSE-AND-IS-100-PERCENT-ESTIMATED``:
*"the rows are individually adjudicated against venue truth"*. That row measured
`netting_attributed` as the single largest contributor to MI-277's winner-size
collapse and **100% ESTIMATED provenance** — not one measured row in it — and
said explicitly that it had NOT established any row to be a false close, because
a journal query cannot.

This asks the venue.

THE SECOND, INDEPENDENT ENDPOINT
================================
``/api/diag/bybit_raw_closed_pnl`` wraps ``/v5/position/closed-pnl``, which asks
*did the venue book a realised close?* — a different question from
``/v5/position/list``, sharing no filter, dedupe, cursor or ``size`` field with
it. That matters here specifically: ``OI-20260908`` establishes that a zero-size
hedge-book sibling makes the POSITION path return `_flat` for a symbol that is
not flat, which is the mechanism that produces these rows. Adjudicating them
against the same endpoint that produced them would prove nothing.

⚠️ THE MATCHER IS GRADED BEFORE IT IS TRUSTED, AND ITS CONTROL CHANGED THE ANSWER
================================================================================
A journal row is matched to a venue record by SIDE and QTY. Under one-way
netting a symbol is one exchange position holding N journal rows, so a qty
coincidence is a live hazard, not a hypothetical.

Every match is therefore graded against ``avg_entry_price`` — a field neither
qty nor time determines, so agreement is independent corroboration and
disagreement REFUTES the match. Measured 2026-09-12 over the newest 1000-row
journal tail: of 38 qty+side matches, **25 corroborated (entry within 25 bp), 8
weak (25-100 bp), 5 refuted (>100 bp)**.

**That grading flips the headline's SIGN.** Over all 38 matches, journal minus
venue is **-1,955.86**; over the 25 corroborated it is **+4,416.65**. One
refuted match alone (trade 5262, whose best qty match sits 5.8 DAYS away and
disagrees on entry by 150 bp) carries most of the difference. A figure whose
sign flips on a filter choice is the exact hazard `CLAUDE.md` § "Always state
the population" exists for; here the filter is *"is this match real?"* and
answering it is not optional.

WHAT A MATCH DOES AND DOES NOT ESTABLISH
========================================
It establishes the venue booked a close of that size on that side. It does NOT
establish a 1:1 correspondence — netting attribution exists precisely because
one venue close can cover several journal rows — and it does not establish that
the journal closed the row for the right reason or at the right time. The
TIME DELTA is reported for that second question and is where the interesting
minority lives.

Tier-1: research tooling. Reads two journal dumps and N read-only diag windows,
prints a report. No DB write, no order path, nothing mutated.

USAGE

    bash scripts/ops/diag_fetch.sh '/api/diag/journal?table=order_packages&limit=1000' > pkgs.json
    bash scripts/ops/diag_fetch.sh '/api/diag/journal?table=trades&limit=1000'         > trades.json
    python3 scripts/research/netting_close_venue_adjudication.py \
        --trades trades.json --venue-dir venue/ --emit-fetch-plan
    # run the printed diag_fetch lines, then re-run without --emit-fetch-plan

⚠️ The venue window is capped at **7 days per call by Bybit** (`ErrCode 10001`),
so ``--emit-fetch-plan`` chunks it. The plan is printed rather than executed
because this script makes no network call of its own.

# wiring: manual-only - it needs N read-only diag pulls a session makes by hand
# against a live VM, and its output is read by a person deciding whether a
# population is admissible evidence. There is nothing for a scheduled runner to
# do with the answer, and a cron would hammer the venue endpoint for a report
# nobody asked for.
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import glob
import json
import os
import statistics
import sys

#: A match whose entry price agrees within this many basis points is
#: CORROBORATED by a field the matcher did not key on.
CORROBORATED_BP = 25.0
#: Beyond this, the match is REFUTED. Between the two it is `weak` — reported,
#: never silently promoted or dropped.
REFUTED_BP = 100.0
#: qty agreement required for a candidate match, as a fraction.
QTY_TOL = 0.005
#: Bybit's own cap on a closed-pnl window.
VENUE_WINDOW_DAYS = 7


def _ms(iso: str) -> int:
    return int(dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() * 1000)


def _f(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f


def load_population(trades_path: str, exit_reason: str) -> list[dict]:
    with open(trades_path) as fh:
        trades = json.load(fh)
    return [
        t for t in trades
        if t.get("exit_reason") == exit_reason
        and str(t.get("status") or "").lower() == "closed"
        and t.get("closed_at") and t.get("position_size") and t.get("entry_price")
    ]


def emit_fetch_plan(rows: list[dict]) -> None:
    """Print the diag_fetch lines that produce the venue side of this report."""
    spans: dict[tuple[str, str], list[int]] = {}
    for t in rows:
        k = (t["account_id"], t["symbol"])
        m = _ms(t["closed_at"])
        lo, hi = spans.get(k, (m, m))
        spans[k] = (min(lo, m), max(hi, m))
    day = 86_400_000
    n = 0
    for (acct, sym), (lo, hi) in sorted(spans.items()):
        lo -= 2 * 3_600_000
        hi += 2 * 3_600_000
        start, i = lo, 0
        while start < hi:
            end = min(start + (VENUE_WINDOW_DAYS - 1) * day, hi)
            print(
                f"bash scripts/ops/diag_fetch.sh "
                f"'/api/diag/bybit_raw_closed_pnl?account_id={acct}&symbol={sym}"
                f"&start_ms={start}&end_ms={end}' > venue/{acct}_{sym}_{i}.json"
            )
            start, i, n = end + 1, i + 1, n + 1
    print(f"# {n} window(s) over {len(spans)} (account, symbol) pair(s), "
          f"{VENUE_WINDOW_DAYS}-day cap per call", file=sys.stderr)


def load_venue(venue_dir: str) -> tuple[dict, dict, list[str]]:
    """Records keyed by (account, symbol), the per-pair query states, and problems.

    Records are deduped on ``order_id`` because the windows deliberately overlap
    at their edges. A window that could not be read is RECORDED, never dropped:
    an unread window and an empty one are opposite facts.
    """
    records: dict = collections.defaultdict(dict)
    states: dict = collections.defaultdict(list)
    problems: list[str] = []
    files = sorted(glob.glob(os.path.join(venue_dir, "*.json")))
    if not files:
        problems.append(f"no venue windows found under {venue_dir!r} — "
                        "this is 'we did not look', not 'the venue booked nothing'")
    for path in files:
        try:
            with open(path) as fh:
                doc = json.load(fh)
        except (json.JSONDecodeError, OSError) as exc:
            problems.append(f"UNPARSEABLE {os.path.basename(path)}: {exc}")
            continue
        res = doc.get("result") or {}
        key = (doc.get("account_id"), res.get("symbol"))
        states[key].append(res.get("query_state"))
        if res.get("error"):
            problems.append(f"VENUE ERROR {os.path.basename(path)}: {str(res['error'])[:120]}")
        if res.get("pages_truncated"):
            problems.append(f"TRUNCATED {os.path.basename(path)} — a page bound was spent, "
                            "so this window's records are INCOMPLETE")
        for rec in res.get("records") or []:
            records[key][rec["order_id"]] = rec
    return records, states, problems


def adjudicate(rows: list[dict], records: dict, states: dict) -> list[dict]:
    out = []
    for t in sorted(rows, key=lambda x: str(x.get("closed_at"))):
        key = (t["account_id"], t["symbol"])
        qty = _f(t["position_size"])
        closing_side = "Sell" if str(t.get("direction")).lower() == "long" else "Buy"
        closed_ms = _ms(t["closed_at"])
        pair_states = set(states.get(key) or [])
        pool = list(records.get(key, {}).values())

        r = {"trade": t["id"], "account_id": t["account_id"],
             "account_class": t.get("account_class"), "symbol": t["symbol"],
             "direction": t.get("direction"), "qty": qty,
             "journal_pnl": _f(t.get("pnl")), "venue_pnl": None,
             "match_quality": None, "entry_bp": None, "updated_dt_min": None,
             "n_candidates": 0, "state": None}

        if not pool:
            r["state"] = ("could_not_look" if "could_not_look" in pair_states
                          else "venue_no_rows_in_window")
            out.append(r)
            continue
        same_side = [x for x in pool if x.get("side") == closing_side]
        if not same_side:
            r["state"] = "venue_no_close_on_side"
            out.append(r)
            continue
        cands = [x for x in same_side
                 if abs(_f(x["qty"]) - qty) <= QTY_TOL * max(qty, 1e-9)]
        r["n_candidates"] = len(cands)
        if not cands:
            r["state"] = "venue_closed_different_qty"
            out.append(r)
            continue

        best = min(cands, key=lambda x: abs(int(x["updated_time"]) - closed_ms))
        v_entry = _f(best["avg_entry_price"])
        j_entry = _f(t["entry_price"])
        bp = abs(j_entry - v_entry) / v_entry * 1e4 if v_entry else None
        r["entry_bp"] = bp
        r["venue_pnl"] = _f(best["closed_pnl"])
        r["updated_dt_min"] = (int(best["updated_time"]) - closed_ms) / 60000.0
        r["created_dt_min"] = (int(best["created_time"]) - closed_ms) / 60000.0
        r["state"] = "venue_close_matched_qty"
        if bp is None:
            r["match_quality"] = "ungradeable_no_venue_entry_price"
        elif bp <= CORROBORATED_BP:
            r["match_quality"] = "corroborated"
        elif bp <= REFUTED_BP:
            r["match_quality"] = "weak"
        else:
            r["match_quality"] = "refuted"
        # Exact decomposition of journal - venue, on the matched pair.
        sign = 1.0 if str(t.get("direction")).lower() == "long" else -1.0
        j_gross = qty * (_f(t["exit_price"]) - j_entry) * sign
        v_gross = qty * (_f(best["avg_exit_price"]) - v_entry) * sign
        r["journal_is_gross"] = abs(j_gross - r["journal_pnl"]) <= max(
            0.02, 0.002 * abs(j_gross))
        r["price_basis"] = j_gross - v_gross
        r["fee_basis"] = v_gross - r["venue_pnl"]
        out.append(r)
    return out


def report(results: list[dict], problems: list[str]) -> int:
    print("PROBLEMS WITH THE VENUE READ ITSELF "
          "(reported first — an unread window is not an empty one)")
    if problems:
        for p in problems:
            print(f"  ! {p}")
    else:
        print("  none: every window parsed, none truncated, none errored")
    print()

    states = collections.Counter(r["state"] for r in results)
    print(f"POPULATION: {len(results)} closed `netting_attributed` trade row(s)")
    for s, n in states.most_common():
        print(f"  {s:<28}{n:>4}")
    print()

    matched = [r for r in results if r["state"] == "venue_close_matched_qty"]
    if not matched:
        print("NOTHING MATCHED — 'we could not look', never 'the venue booked nothing'.")
        return 1

    qual = collections.Counter(r["match_quality"] for r in matched)
    print("MATCH QUALITY — graded on `avg_entry_price`, a field the matcher did NOT key on")
    for q, n in qual.most_common():
        print(f"  {q:<28}{n:>4}")
    print()

    def totals(pop):
        j = sum(r["journal_pnl"] for r in pop if r["journal_pnl"] is not None)
        v = sum(r["venue_pnl"] for r in pop if r["venue_pnl"] is not None)
        return j, v, j - v

    print("⚠️ WHY THE GRADING IS NOT OPTIONAL — the aggregate's SIGN depends on it")
    for label, pop in (("all qty matches", matched),
                       ("corroborated only", [r for r in matched
                                              if r["match_quality"] == "corroborated"])):
        j, v, d = totals(pop)
        print(f"  {label:<20} n={len(pop):<4} journal {j:>12,.2f}  venue {v:>12,.2f}  "
              f"journal-venue {d:>+12,.2f}")
    print()

    corr = [r for r in matched if r["match_quality"] == "corroborated"]
    ungross = [r for r in corr if not r.get("journal_is_gross")]
    print("ON THE CORROBORATED ROWS")
    print(f"  journal pnl equals its own gross price arithmetic on "
          f"{len(corr) - len(ungross)} of {len(corr)} "
          "— so the journal figure carries NO fee term")
    price = sum(r["price_basis"] for r in corr)
    fee = sum(r["fee_basis"] for r in corr)
    j, v, d = totals(corr)
    share = f"{fee / d:.1%}" if abs(d) > 1e-9 else "n/a"
    print(f"  journal - venue {d:>+12,.2f}  =  price basis {price:>+12,.2f}  "
          f"+  fee basis {fee:>+12,.2f}")
    print(f"  the fee term is {share} of the gap and is a BASIS DIFFERENCE, not an error;")
    print("  the price term is the estimation error this population's provenance warns about.")
    print()

    signs = [r for r in corr
             if r["journal_pnl"] is not None and r["venue_pnl"] is not None
             and (r["journal_pnl"] > 0) != (r["venue_pnl"] > 0)]
    print(f"  SIGN DISAGREEMENTS: {len(signs)} of {len(corr)} "
          "— the journal books a profit where the venue booked a loss, or vice versa")
    for r in signs:
        print(f"    trade {r['trade']} {r['account_id']}/{r['symbol']}: "
              f"journal {r['journal_pnl']:+.2f}  venue {r['venue_pnl']:+.2f}")
    print()

    by_acct = collections.defaultdict(lambda: [0, 0.0, 0.0, 0.0])
    for r in corr:
        k = (r["account_id"], r["account_class"])
        by_acct[k][0] += 1
        by_acct[k][1] += (r["journal_pnl"] or 0) - (r["venue_pnl"] or 0)
        by_acct[k][2] += r["price_basis"]
        by_acct[k][3] += r["fee_basis"]
    print("  BY ACCOUNT — account_class decides how to read the dollars")
    print(f"    {'account':<18}{'class':<12}{'n':>4}{'j-v':>12}{'price':>11}{'fees':>10}")
    for (a, c), v4 in sorted(by_acct.items()):
        print(f"    {a:<18}{str(c):<12}{v4[0]:>4}{v4[1]:>12.2f}{v4[2]:>11.2f}{v4[3]:>10.2f}")
    print()

    late = [r for r in corr if (r["updated_dt_min"] or 0) > 5]
    ds = [r["updated_dt_min"] for r in corr if r["updated_dt_min"] is not None]
    print("  WHEN DID THE VENUE BOOK IT, relative to the journal's `closed_at`?")
    print(f"    median {statistics.median(ds):+.1f} min · min {min(ds):+.1f} · max {max(ds):+.1f}")
    print(f"    booked MORE THAN 5 MIN AFTER the journal closed the row: "
          f"{len(late)} of {len(corr)}")
    for r in late:
        print(f"      trade {r['trade']} {r['account_id']}/{r['symbol']}: "
              f"venue booked it {r['updated_dt_min']:+.1f} min later")
    print("    -- a NEGATIVE delta is the healthy shape (the venue closed, then the")
    print("       reconciler recorded it). A large POSITIVE delta is the journal")
    print("       closing a row the venue had not yet closed, which is the shape")
    print("       OI-20260908 describes.")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--trades",
                    help="JSON from /api/diag/journal?table=trades")
    ap.add_argument("--venue-dir", default="venue",
                    help="directory of /api/diag/bybit_raw_closed_pnl responses")
    ap.add_argument("--exit-reason", default="netting_attributed",
                    help="the journal label to adjudicate (default: netting_attributed)")
    ap.add_argument("--emit-fetch-plan", action="store_true",
                    help="print the diag_fetch lines for the venue windows and exit")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)

    if a.self_test:
        return self_test()
    if not a.trades:
        ap.error("--trades is required unless --self-test is given")

    rows = load_population(a.trades, a.exit_reason)
    if not rows:
        print(f"no closed `{a.exit_reason}` rows in {a.trades} — "
              "an empty population is 'we found none here', not 'none exist'")
        return 1
    if a.emit_fetch_plan:
        emit_fetch_plan(rows)
        return 0
    records, states, problems = load_venue(a.venue_dir)
    return report(adjudicate(rows, records, states), problems)


def self_test() -> int:
    """Assert the three things this script would be worthless without."""
    fails = []

    def chk(name, got, want):
        if got != want:
            fails.append(f"{name}: got {got!r} want {want!r}")
        print(f"  {'ok ' if got == want else 'FAIL'} {name}")

    base = {"id": 1, "account_id": "a", "account_class": "paper", "symbol": "S",
            "direction": "long", "position_size": 10.0, "entry_price": 100.0,
            "exit_price": 110.0, "pnl": 100.0, "status": "closed",
            "closed_at": "2026-09-01T00:00:00+00:00", "exit_reason": "netting_attributed"}
    rec = {"order_id": "o1", "side": "Sell", "qty": "10.0", "avg_entry_price": "100.0",
           "avg_exit_price": "110.0", "closed_pnl": "90.0",
           "updated_time": str(_ms(base["closed_at"])), "created_time": str(_ms(base["closed_at"]))}

    r = adjudicate([base], {("a", "S"): {"o1": rec}}, {("a", "S"): ["rows_returned"]})[0]
    chk("a clean match is corroborated", r["match_quality"], "corroborated")
    chk("the journal figure is recognised as gross", r["journal_is_gross"], True)
    chk("fee basis is the venue's own charge", round(r["fee_basis"], 6), 10.0)
    chk("price basis is zero when the prices agree", round(r["price_basis"], 6), 0.0)

    far = dict(rec, avg_entry_price="90.0")   # 1111 bp away
    r = adjudicate([base], {("a", "S"): {"o1": far}}, {("a", "S"): ["rows_returned"]})[0]
    chk("an entry-price disagreement REFUTES the match", r["match_quality"], "refuted")

    # A qty match on the WRONG side must not be taken.
    wrong = dict(rec, side="Buy")
    r = adjudicate([base], {("a", "S"): {"o1": wrong}}, {("a", "S"): ["rows_returned"]})[0]
    chk("a close on the wrong side is not a match", r["state"], "venue_no_close_on_side")

    # An unreadable window must never read as an empty one.
    r = adjudicate([base], {}, {("a", "S"): ["could_not_look"]})[0]
    chk("could_not_look is not venue_no_rows", r["state"], "could_not_look")
    r = adjudicate([base], {}, {("a", "S"): ["no_rows"]})[0]
    chk("an empty venue answer is its own state", r["state"], "venue_no_rows_in_window")

    print("self-test: FAIL" if fails else "self-test: OK")
    for f in fails:
        print("   ", f)
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
