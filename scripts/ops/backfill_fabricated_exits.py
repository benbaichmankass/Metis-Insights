#!/usr/bin/env python3
"""Re-derive fabricated exit prices from the broker fills we already stored.

WHY THIS IS NOT "RELABEL ONLY" (operator-challenged 2026-07-31)
---------------------------------------------------------------
The standing "historical pass is RELABEL ONLY, never re-price" rule exists
because **IBKR's** execution history is short-lived — `reqExecutions` serves
roughly the current trading day, so for IB the evidence is genuinely gone and
any reconstruction would be invention.

**Bybit's evidence is not gone — for RECENT rows.** It is sitting in
`exchange_fills.sqlite`. Applying the IB rule blanket-wide was an
over-generalisation of a venue-specific constraint — the same mistake as
`if is_demo: return None`, which generalised "this ENDPOINT is unreliable on
demo" into "there is no broker truth for demo".

.. warning::

   **MEASURED 2026-07-31 (dry run #8161): this recovers 13 of 327 fabricated
   rows via own fills — 4.0%.** The paragraph above was written before that
   measurement and overstated the case; it is corrected here rather than left
   to read as a promise the tool does not keep.

   The store did NOT accrue "the whole time". It held **zero** fills until
   2026-07-13 (`BL-20260713-EXCHANGE-FILLS-STORE-EMPTY` — the puller existed
   but had no timer), and `pull_exchange_fills_action.sh` pulls only a
   **7-day** window per run, so it accrues strictly FORWARD from that date.
   Measured contents: 305 fills, 12 symbols, 2026-07-06..07-30.

   Fabrication starts **2026-06-08**. The ~5 intervening weeks have no local
   fills, so for those rows the evidence really is gone — the same
   venue-retention constraint as IB, over a different window. The exclusion
   filter, not this backfill, is the remedy for them.

   Before concluding they are permanently unrecoverable, note
   ``exchange_fills_puller`` calls ``fetch_my_trades(sym, since_ms, 200, {})``
   with **limit 200 and no pagination**, so a naively widened ``--days`` would
   truncate silently. Full record + the experiment to run:
   ``BL-20260731-FILLS-STORE-PREDATES-THE-FABRICATION``.

THE REGRESSION THIS REPAIRS, WITH ITS START DATE
------------------------------------------------
Fabrication is not drift. Measured per account-month on the live journal:

    bybit_1   May 0/47 (0.0%)  ->  Jun 28/124 (22.6%)  ->  Jul 126/155 (81.3%)

Same account, same code path. The exit-source mix shows the mechanism: May is
`bybit_closed_pnl` x187, July is `local_markprice` x161. The broker-truth path
stopped being taken at the June boundary — when `BL-20260608-DEMOPNL` added the
demo dead-end. Every demo close after 2026-06-08 fell through to mark
substitution, so the damage COMPOUNDS with every trade. Waiting makes it worse.

TWO TIERS, AND THEY ARE NOT THE SAME THING
-------------------------------------------
**Tier 1 — own fills (MEASURED).** The account's own close-side fills, matched
on account+symbol+side+window via the same `exit_from_fills` the live path uses.
Stamped :data:`~src.runtime.fills_pnl.FILL_EXIT_SOURCE`.

**Tier 2 — MIRROR account (ESTIMATED, never MEASURED).** Operator observation,
2026-07-31: `bybit_portfolio` mirrors `bybit_2` and `alpaca_portfolio` mirrors
`alpaca_live` — same setups, so a paper trade's exit should land near its live
sibling's fill. That is TRUE and USEFUL and it is still an **inference about a
different account's execution**, not a measurement of this one. The operator
said so themselves: *"it didn't happen exactly because bybit_2 might not have
enough capacity."*

So the mirror price is ESTIMATED — the same bucket as a candle close, and for
the same reason: a defensible anchor, not a fill of THIS order. It is stamped
:data:`MIRROR_EXIT_SOURCE` so it is queryable and reversible, and **qty is never
copied** (capacity differs, which is exactly why the mirror is not a fill).
Collapsing it into MEASURED would re-import fabrication wearing a better label,
which is the failure this whole workstream exists to end.

SAFETY
------
* ``--dry-run`` is the DEFAULT. Writing requires ``--apply``.
* Only rows whose pnl is currently FABRICATED or UNVERIFIED are touched. A
  measured row is never overwritten — this can only improve provenance.
* Every write records ``notes.backfill`` with the prior value, the new source
  and the run id, so the pass is auditable and reversible.
* Refusals are inherited from ``exit_from_fills`` (qty tolerance, unusable
  rows). A row that cannot be resolved is LEFT ALONE, never guessed.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.runtime.broker_cost_attribution import normalize_symbol  # noqa: E402
# The window slack and timestamp format are imported, not re-derived: the probe
# below must ask the store EXACTLY what exit_from_fills asked it, or its answer
# describes a different query than the one that actually refused.
from src.runtime.fills_pnl import (  # noqa: E402
    _OPEN_SLACK_MS,
    FILL_EXIT_SOURCE,
    exit_from_fills,
)
from src.runtime.fills_pnl import _iso as _iso_ms  # noqa: E402
from src.runtime.provenance import (  # noqa: E402
    ESTIMATED, MEASURED, classify_pnl,
)

#: ``exit_price_source`` for a price taken from the MIRROR account's fill.
#: ESTIMATED — a sibling account's execution, not this order's.
MIRROR_EXIT_SOURCE = "mirror_account_fill"

#: paper book -> the live book it mirrors (CLAUDE.md S-PAPER-PORTFOLIO).
MIRRORS = {"bybit_portfolio": "bybit_2", "alpaca_portfolio": "alpaca_live"}

#: How close in time a mirror fill must be to count as the same setup.
MIRROR_WINDOW_MS = 15 * 60 * 1000


def _ms(value: Any) -> Optional[int]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        return int(text)
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def _notes(raw: Any) -> dict:
    try:
        out = json.loads(raw) if isinstance(raw, str) else raw
        return out if isinstance(out, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _needs_repair(notes: dict) -> bool:
    """Only FABRICATED / UNVERIFIED rows are candidates. Never downgrade."""
    bucket, _why = classify_pnl(notes)
    return bucket not in (MEASURED, ESTIMATED)


def _accounts_with_fills(conn_factory) -> set[str]:
    """Accounts that have ANY row in the fills store.

    Measured from the store, never inferred from an account-name list — the
    point is to distinguish "we looked and found nothing for this trade" from
    "this account was never reachable by a fills pull at all".
    """
    c = None
    try:
        c = conn_factory()
        return {
            str(r[0])
            for r in c.execute("SELECT DISTINCT account_id FROM exchange_fills")
            if r[0]
        }
    except Exception:  # noqa: BLE001
        # Unknown is NOT the same as empty: return a sentinel-free empty set but
        # the caller labels it explicitly rather than claiming zero coverage.
        return set()
    finally:
        if c is not None:
            c.close()


#: `exit_from_fills` maps direction through this; anything else refuses early.
_DIRECTION_SIDE = {"long": "sell", "short": "buy"}


def _candidate_fill_count(conn_factory, *, account_id, symbol, direction,
                          opened_at_ms, closed_at_ms) -> Optional[int]:
    """How many close-side fills `exit_from_fills` had to work with.

    Mirrors that function's own account+side+window+normalised-symbol filter,
    read-only, so the caller can tell "we found NOTHING" from "we found fills
    and rejected them". Returns None if the store can't be read.
    """
    side = _DIRECTION_SIDE.get(str(direction or "").lower())
    want = normalize_symbol(symbol)
    if not side or not want:
        return None
    start_ms = int(opened_at_ms) - _OPEN_SLACK_MS
    end_ms = int(closed_at_ms) if closed_at_ms else int(
        datetime.now(timezone.utc).timestamp() * 1000)
    if end_ms <= start_ms:
        return 0
    c = None
    try:
        c = conn_factory()
        return sum(
            1 for r in c.execute(
                "SELECT symbol FROM exchange_fills "
                " WHERE account_id = ? AND side = ? "
                "   AND datetime(exec_time) >= datetime(?) "
                "   AND datetime(exec_time) <= datetime(?)",
                (str(account_id).strip(), side,
                 _iso_ms(start_ms), _iso_ms(end_ms)),
            )
            if normalize_symbol(r[0]) == want
        )
    except Exception:  # noqa: BLE001
        return None
    finally:
        if c is not None:
            c.close()


def _unresolved_reason(account_id: str, covered: set[str], candidates) -> str:
    """Why THIS row did not resolve — branching on the actual refusal stage.

    `exit_from_fills` returns a bare None for four different refusals, so a
    single label over all of them names a cause no code path tested (CLAUDE.md
    § "Diagnostic provenance", sub-class A — the failure-message variant). The
    distinction is load-bearing here, not cosmetic:

      * `no_fill_in_window` — the evidence genuinely isn't stored. A deeper or
        differently-timed pull is the fix.
      * `fills_present_but_qty_unreconciled` — fills WERE found and the
        resolver **deliberately refused** them, because cumulative matched qty
        missed the trade's qty by more than QTY_TOLERANCE. Under one-way
        netting one exchange position backs N journal rows, so a single
        close-side fill covers several rows' qty and this refusal is EXPECTED.
        These rows are not repairable by pulling harder — attributing them is
        the netting-attribution problem (`NETTING_ATTRIBUTION_MODE`), and
        guessing a split here is the proration error the resolver's own
        docstring exists to refuse.
      * `direction_not_long_short` — the row's direction is outside the
        vocabulary the resolver maps, so it never reached the store at all.
    """
    if not covered:
        return "coverage_unknown_store_unreadable"
    if account_id not in covered:
        return "account_has_no_fills_stored"
    if candidates is None:
        return "direction_or_symbol_unmappable"
    if candidates == 0:
        return "no_fill_in_window"
    return "fills_present_but_qty_unreconciled"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", required=True, help="trade_journal.db (live VM)")
    ap.add_argument("--fills", required=True, help="exchange_fills.sqlite")
    ap.add_argument("--apply", action="store_true",
                    help="WRITE. Omit for the default dry run.")
    ap.add_argument("--allow-mirror", action="store_true",
                    help="also use the mirror account's fill as an ESTIMATED "
                         "price when the account's own fills are missing")
    ap.add_argument("--run-id", default=None)
    args = ap.parse_args()

    run_id = args.run_id or datetime.now(timezone.utc).strftime("bf-%Y%m%dT%H%M%SZ")
    fills_path = Path(args.fills)
    if not fills_path.is_file():
        print(f"FATAL: fills store not found at {fills_path}", file=sys.stderr)
        return 2

    def fills_conn():
        return sqlite3.connect(f"file:{fills_path}?mode=ro", uri=True)

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    rows = list(conn.execute(
        "SELECT id, account_id, symbol, direction, position_size, entry_price,"
        "       created_at, closed_at, timestamp, pnl, notes "
        "  FROM trades "
        " WHERE status='closed' AND pnl IS NOT NULL AND is_backtest=0 "
        " ORDER BY id ASC"
    ))

    # WHY THIS BREAKDOWN EXISTS. `unresolved, left alone: 307` is a single
    # opaque counter over at least three different situations, and they demand
    # opposite responses:
    #
    #   * the account has NO fills stored at all (ib_paper / alpaca / oanda have
    #     no Bybit-style fills puller) — no deeper Bybit pull will ever help, and
    #     counting these as "evidence missing" overstates what is recoverable;
    #   * the account HAS fills but none covering this trade's window — that IS
    #     a coverage gap a deeper or differently-timed pull might close;
    #   * a fill was found but its price was unusable.
    #
    # Reporting them as one number is the unasserted-denominator shape
    # (CLAUDE.md § "Diagnostic provenance", sub-class C): a reader takes 307 as
    # "307 rows whose evidence is gone" when much of it was never in scope.
    # `covered_accounts` is read from the fills store itself, so the split is
    # measured rather than assumed from an account-name list.
    covered = _accounts_with_fills(fills_conn)
    unresolved_by_reason: Counter = Counter()
    unresolved_by_account: Counter = Counter()

    stat = Counter()
    plan: list[tuple] = []

    for r in rows:
        notes = _notes(r["notes"])
        if not _needs_repair(notes):
            stat["skip_already_ok"] += 1
            continue
        opened = _ms(r["created_at"]) or _ms(r["timestamp"])
        closed = _ms(r["closed_at"])
        if opened is None:
            stat["skip_no_open_time"] += 1
            continue

        qty = r["position_size"]
        acct = r["account_id"]

        rec = exit_from_fills(
            account_id=acct, symbol=r["symbol"], direction=r["direction"],
            opened_at_ms=opened, closed_at_ms=closed, qty=qty,
            conn_factory=fills_conn,
        )
        source = FILL_EXIT_SOURCE

        if rec is None and args.allow_mirror and acct in MIRRORS:
            # The mirror's fill is an ESTIMATE of where this order would have
            # gone — same setup, different book. qty is deliberately NOT passed:
            # capacity differs between the live and paper books, and demanding a
            # qty match would reject the very rows this tier exists to rescue.
            rec = exit_from_fills(
                account_id=MIRRORS[acct], symbol=r["symbol"],
                direction=r["direction"],
                opened_at_ms=opened - MIRROR_WINDOW_MS,
                closed_at_ms=(closed + MIRROR_WINDOW_MS) if closed else None,
                qty=None,
                conn_factory=fills_conn,
            )
            source = MIRROR_EXIT_SOURCE
            if rec is not None:
                stat["via_mirror"] += 1

        if rec is None:
            cands = (
                _candidate_fill_count(
                    fills_conn, account_id=acct, symbol=r["symbol"],
                    direction=r["direction"], opened_at_ms=opened,
                    closed_at_ms=closed,
                )
                if acct in covered else None
            )
            stat["unresolved_left_alone"] += 1
            unresolved_by_reason[_unresolved_reason(acct, covered, cands)] += 1
            unresolved_by_account[acct] += 1
            continue

        exit_price = rec.get("avg_exit_price")
        if not exit_price or float(exit_price) <= 0:
            stat["unresolved_left_alone"] += 1
            unresolved_by_reason["fill_found_but_price_unusable"] += 1
            unresolved_by_account[acct] += 1
            continue

        if source == FILL_EXIT_SOURCE:
            stat["via_own_fills"] += 1
        plan.append((r["id"], float(exit_price), source,
                     notes.get("exit_price_source"), rec.get("fees") or 0.0))

    print(f"run_id={run_id}  mode={'APPLY' if args.apply else 'DRY-RUN'}")
    print(f"scanned                : {len(rows)}")
    print(f"already measured/est   : {stat['skip_already_ok']}")
    print(f"RESOLVABLE (own fills) : {stat['via_own_fills']}  -> MEASURED")
    print(f"RESOLVABLE (mirror)    : {stat['via_mirror']}  -> ESTIMATED"
          f"{'' if args.allow_mirror else '  (--allow-mirror off)'}")
    print(f"unresolved, left alone : {stat['unresolved_left_alone']}")
    print(f"no open time           : {stat['skip_no_open_time']}")
    print(f"TOTAL TO WRITE         : {len(plan)}")

    if stat["unresolved_left_alone"]:
        # Split the opaque total: "never reachable" and "reachable but no match"
        # are different problems, and only the second is a coverage gap a deeper
        # pull could close.
        print("  unresolved BY REASON:")
        for reason, n in unresolved_by_reason.most_common():
            print(f"    {reason:34s} {n}")
        print("  unresolved BY ACCOUNT:")
        for acct_id, n in unresolved_by_account.most_common():
            mark = "" if acct_id in covered else "   <- NO fills stored for this account"
            print(f"    {acct_id:34s} {n}{mark}")
        print(f"  accounts with fills stored: "
              f"{', '.join(sorted(covered)) if covered else '(none / store unreadable)'}")

    if not args.apply:
        print("\nDRY RUN — nothing written. Re-run with --apply to commit.")
        return 0

    written = 0
    for trade_id, price, source, prior, fees in plan:
        cur = conn.execute("SELECT notes FROM trades WHERE id=?", (trade_id,))
        row = cur.fetchone()
        n = _notes(row["notes"] if row else None)
        n["exit_price_source"] = source
        if fees:
            n["close_fees_usd"] = round(float(fees), 6)
        # Auditable + reversible: what it was, what replaced it, which run.
        n["backfill"] = {
            "run_id": run_id, "prior_exit_price_source": prior,
            "new_exit_price_source": source,
            "at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        conn.execute(
            "UPDATE trades SET exit_price=?, notes=? WHERE id=?",
            (price, json.dumps(n)[:2000], trade_id),
        )
        written += 1
    conn.commit()
    print(f"\nWROTE {written} rows. pnl is NOT recomputed here — the monitor's "
          f"local-PnL sweep re-derives it from the corrected exit price on its "
          f"next tick, through the same path a live close uses.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
