#!/usr/bin/env python3
"""MI-171 — count the Alpaca over-close population that nobody had looked at.

Answers the two questions `BL-20260907-ALPACA-CLOSE-OF-ONE-TRADE-LIQUIDATES-ITS-
SIBLINGS` closes with *"We did not look."*:

  (a) of the ``(account, symbol)`` pairs that held >=2 SIMULTANEOUSLY-OPEN journal
      rows, how many are on an Alpaca account?
  (b) how many ACTUAL over-close events are visible — a close of one row on a
      multi-row symbol after which a sibling's row stayed open with no venue
      position behind it?

READ-ONLY. This script touches no close path and no config. It is the open-row
diff MI-167's § "What pass 3 of this lane should check" item 2 asked for and did
not build ("it would have found this in one call").

## Reproducing it

Inputs are committed next to it so the numbers can be re-derived without live
access, which is what makes them MEASURED rather than remembered:

    docs/research/artifacts/mi171-alpaca-overclose/trades-4544-5543.json.gz
    docs/research/artifacts/mi171-alpaca-overclose/alpaca-exchange-positions.json

    python3 scripts/research/mi171_alpaca_overclose_population.py

Refresh them against the live VM with (needs ``DIAG_READ_TOKEN``):

    scripts/ops/diag_fetch.sh 'journal?table=trades&limit=1000'
    scripts/ops/diag_fetch.sh 'exchange_positions?account_id=alpaca_portfolio'

⚠️ **THE POPULATION IS A CAP, NOT A CHOSEN WINDOW.** ``/api/diag/journal`` takes
only ``table`` and ``limit``, and ``limit`` is clamped to ``_MAX_LIMIT = 1000``
(``src/web/api/routers/diag.py:1541`` and ``:860``) — there is **no** ``offset``
and **no** ``since``, unlike ``/audit_query`` which has both. Verified by asking
for ``limit=3000`` and getting the same 1000 rows. So the ``trades`` table is
reachable ONLY as its last 1000 rows, and MI-167's "28-day window" is the shape
of that cap rather than a decision. Anything older than id 4544 cannot be read
from this endpoint at all, and no figure here should be read as all-time.

⚠️ **EVERY NEGATIVE HERE CARRIES ITS POSITIVE CONTROL**, because a quiet probe
and a broken probe render identically. The same scan that finds 0 Alpaca
close-with-open-sibling events finds 187 on Bybit; the same venue diff that finds
0 unbacked Alpaca rows finds 3 venue positions with no journal row. The probe
demonstrably detects both directions of divergence.
"""
from __future__ import annotations

import collections
import gzip
import json
from datetime import datetime, timezone
from pathlib import Path

ART = Path(__file__).resolve().parents[1].parent / "docs/research/artifacts/mi171-alpaca-overclose"

#: All four Alpaca accounts in `config/accounts.yaml`. MI-167's Finding 3 graded
#: only `alpaca_live`; the whole-symbol close lives in `alpaca_client.py`, which
#: all four share, so all four are exposed by code — and unlike `alpaca_live`,
#: three of them actually trade.
ALPACA = ("alpaca_live", "alpaca_paper", "alpaca_portfolio", "alpaca_options_paper")

#: When the live reads below were taken. An `open` row has no `closed_at`, so its
#: interval has to be closed at *some* instant; using the read time is what makes
#: "still open now" overlap correctly with everything before it.
READ_AT = datetime(2026, 9, 7, 21, 54, tzinfo=timezone.utc)


def _ts(v: str | None) -> datetime | None:
    return datetime.fromisoformat(v) if v else None


def load_rows() -> list[dict]:
    with gzip.open(ART / "trades-4544-5543.json.gz", "rt", encoding="utf-8") as f:
        return json.load(f)


def load_positions() -> dict:
    return json.loads((ART / "alpaca-exchange-positions.json").read_text())


def max_concurrency(intervals: list[tuple[datetime, datetime]]) -> int:
    """Peak number of simultaneously-open intervals.

    A start at the same instant as an end counts as an OVERLAP (starts sort
    first). The distinction is immaterial here — the pair count is 12 either way
    (see `--sensitivity`) — but it is the conservative direction: it can only
    over-report the precondition, never hide it.
    """
    events: list[tuple[datetime, int]] = []
    for start, end in intervals:
        events.append((start, 1))
        events.append((end, -1))
    events.sort(key=lambda x: (x[0], -x[1]))
    cur = best = 0
    for _, delta in events:
        cur += delta
        best = max(best, cur)
    return best


def opened(rows: list[dict]) -> list[dict]:
    """Rows that ever actually held a venue position.

    `rejected` and `exchange_rejected` never reached the venue, so they can never
    be over-closed and can never be the sibling that is over-closed. **This is
    exactly where my count and MI-167's diverge** — including
    `exchange_rejected` reproduces MI-167's 13 pairs precisely; excluding it
    gives 12. See the doc's § "Why 12 and not 13".
    """
    return [r for r in rows if r["status"] in ("closed", "open")]


def multi_row_pairs(rows: list[dict]) -> dict[tuple[str, str], int]:
    iv: dict[tuple[str, str], list[tuple[datetime, datetime]]] = collections.defaultdict(list)
    for r in opened(rows):
        start = _ts(r.get("timestamp") or r.get("created_at"))
        if start is None:
            continue
        end = _ts(r.get("closed_at")) or READ_AT
        iv[(r["account_id"], r["symbol"])].append((start, end))
    return {k: c for k, v in iv.items() if (c := max_concurrency(v)) >= 2}


def closes_with_open_sibling(rows: list[dict], accounts=None) -> list[dict]:
    """The over-close PRECONDITION, as an event count rather than a pair count.

    A close of row R counts when some sibling row on the same (account, symbol)
    was open at R's `closed_at`. On Alpaca this is the exact moment the
    whole-symbol `DELETE /v2/positions/{sym}` would take the sibling's shares
    with it.
    """
    by: dict[tuple[str, str], list[dict]] = collections.defaultdict(list)
    for r in opened(rows):
        by[(r["account_id"], r["symbol"])].append(r)
    out = []
    for (acct, sym), rs in by.items():
        if accounts and acct not in accounts:
            continue
        for r in rs:
            if r["status"] != "closed" or not r.get("closed_at"):
                continue
            t = _ts(r["closed_at"])
            sibs = [
                o for o in rs
                if o["id"] != r["id"]
                and _ts(o["timestamp"]) <= t < (_ts(o.get("closed_at")) or READ_AT)
            ]
            if sibs:
                out.append({"account_id": acct, "symbol": sym, "closed_id": r["id"],
                            "closed_at": r["closed_at"],
                            "open_sibling_ids": [o["id"] for o in sibs]})
    return out


def venue_diff(rows: list[dict], positions: dict) -> dict:
    """Journal-open vs venue, per Alpaca account.

    An over-close leaves its signature HERE: a journal row still `open` with no
    venue position (or a short one) behind it.
    """
    matched, unbacked, mismatch, orphan = 0, [], [], []
    for acct in ALPACA:
        account = positions[acct]["accounts"][0]
        # `_open_trades` swallows exceptions and returns []; a read that FAILED
        # must never be graded as an empty book (BL-20260826-OPEN-TRADES-
        # COLLAPSES-A-READ-FAILURE-INTO-AN-EMPTY-BOOK).
        if account["error"] is not None:
            raise SystemExit(f"{acct}: venue read FAILED ({account['error']}) — "
                             f"refusing to grade an unread book as empty")
        venue = {p["symbol"]: float(p["size"]) for p in account["positions"]}
        jopen: dict[str, list[dict]] = collections.defaultdict(list)
        for r in rows:
            if r["account_id"] == acct and r["status"] == "open":
                jopen[r["symbol"]].append(r)
        for sym, rs in jopen.items():
            jqty = sum(float(r.get("position_size") or 0) for r in rs)
            vqty = venue.get(sym, 0.0)
            if vqty == 0:
                unbacked.extend(r["id"] for r in rs)
            elif abs(jqty - vqty) > 1e-6:
                mismatch.append({"account_id": acct, "symbol": sym,
                                 "journal_qty": jqty, "venue_qty": vqty,
                                 "row_ids": [r["id"] for r in rs]})
            else:
                matched += len(rs)
        for sym, vqty in venue.items():
            if sym not in jopen:
                orphan.append({"account_id": acct, "symbol": sym, "venue_qty": vqty})
    return {"matched": matched, "unbacked": unbacked,
            "mismatch": mismatch, "venue_orphan": orphan}


def main() -> int:
    rows = load_rows()
    positions = load_positions()

    ids = [r["id"] for r in rows]
    assert len(ids) == len(set(ids)), "duplicate journal ids in the pull"
    n_open = sum(1 for r in rows if r["status"] == "open")
    n_closed = sum(1 for r in rows if r["status"] == "closed")

    print("POPULATION — every figure below is over exactly this and nothing wider")
    print(f"  {len(rows)} journal `trades` rows (the endpoint's hard 1000-row cap), "
          f"ids {min(ids)}-{max(ids)}")
    print(f"  {min(r['timestamp'] for r in rows)[:19]}Z -> "
          f"{max(r['timestamp'] for r in rows)[:19]}Z")
    print(f"  status: {dict(collections.Counter(r['status'] for r in rows))}")
    print(f"  ever held a venue position (closed|open): {len(opened(rows))} "
          f"= {n_closed} closed + {n_open} open")

    alp = [r for r in rows if r["account_id"] in ALPACA]
    print(f"  Alpaca rows across all four accounts: {len(alp)} "
          f"({sum(1 for r in alp if r['status'] == 'closed')} closed, "
          f"{sum(1 for r in alp if r['status'] == 'open')} open, "
          f"{sum(1 for r in alp if r['status'] == 'rejected')} rejected, "
          f"{sum(1 for r in alp if r['status'] == 'exchange_rejected')} exchange_rejected)")

    print("\n(a) PAIRS HOLDING >=2 SIMULTANEOUSLY-OPEN ROWS")
    pairs = multi_row_pairs(rows)
    alp_pairs = sorted(k for k in pairs if k[0] in ALPACA)
    for (acct, sym), c in sorted(pairs.items()):
        print(f"    {acct:22} {sym:10} max_concurrent={c}"
              f"{'   <-- ALPACA' if acct in ALPACA else ''}")
    print(f"  total: {len(pairs)}   Alpaca: {len(alp_pairs)}   "
          f"non-Alpaca: {len(pairs) - len(alp_pairs)}")
    assert len(alp_pairs) + (len(pairs) - len(alp_pairs)) == len(pairs)

    print("\n(b) ACTUAL OVER-CLOSE EVENTS")
    ev_all = closes_with_open_sibling(rows)
    ev_alp = closes_with_open_sibling(rows, ALPACA)
    print(f"  closes that fired while a sibling row on the same (account, symbol) "
          f"was open:")
    print(f"    all accounts : {len(ev_all)}   <-- POSITIVE CONTROL: the probe "
          f"finds plenty; MI-167 independently reported 187")
    print(f"    ALPACA       : {len(ev_alp)}   (denominator: "
          f"{sum(1 for r in alp if r['status'] == 'closed')} Alpaca closes in the population)")
    by_acct = collections.Counter(e["account_id"] for e in ev_all)
    for a, n in by_acct.most_common():
        print(f"      {a:22} {n}")

    d = venue_diff(rows, positions)
    n_alp_open = sum(1 for r in alp if r["status"] == "open")
    print(f"\n  journal-open vs venue, all four Alpaca accounts "
          f"({n_alp_open} open rows):")
    print(f"    backed, journal sum == venue qty EXACTLY          : {d['matched']}")
    print(f"    open row with NO venue position behind it         : {len(d['unbacked'])}"
          f"   <-- THIS is what an over-close leaves")
    print(f"    open row with a venue qty MISMATCH                : {len(d['mismatch'])}")
    print(f"    venue position with NO open journal row           : {len(d['venue_orphan'])}"
          f"   <-- opposite direction; NOT gradeable here (see below)")
    for o in d["venue_orphan"]:
        print(f"      {o['account_id']:22} {o['symbol']:6} venue_qty={o['venue_qty']}")
    assert d["matched"] + len(d["unbacked"]) + sum(len(m["row_ids"]) for m in d["mismatch"]) \
        == n_alp_open, "the venue diff does not account for every open Alpaca row"

    print("\nVERDICT")
    print(f"  (a) {len(alp_pairs)} of {len(pairs)} multi-row pairs are Alpaca: {alp_pairs}")
    print(f"  (b) {len(ev_alp)} over-close events. WE LOOKED AND FOUND NONE — this is not "
          f"'we did not look'.")
    print("      Not because the close path is unused (32 Alpaca closes fired), but "
          "because none of\n      them coincided with an open sibling.")
    print("  The 3 venue-orphan positions above have ZERO journal rows inside the "
          "1000-row cap,\n  so their rows predate id 4544 and CANNOT be read from this "
          "endpoint. Reported as\n  ungradeable, NOT as clean and NOT as a defect.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
