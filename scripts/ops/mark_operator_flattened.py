"""Mark specific closed trades as **operator-flattened**, so exit analyses can
exclude them.

THE PROBLEM. When an operator flattens a live position for an OPERATIONAL
reason — a venue-config change, a migration, clearing a symbol so a Bybit
position-mode switch can proceed — the flatten scripts
(``scripts/ops/flatten_{bybit,ib,alpaca}_position.py``) deliberately do NOT
touch the journal. Their docstrings say so: *"The journal row is left for the
trader's reconciler to close-on-disappear."* The reconciler duly notices the
position is flat and stamps ``exit_reason='reconciler_filled'`` +
``notes.closed_by='monitor_reconciler'``.

That is a correct description of HOW the row closed and a misleading one about
WHY. The resulting row is byte-indistinguishable from an ordinary close the
reconciler happened to book, so:

  * ``/api/bot/performance``'s ``perExitPath`` buckets it under
    ``reconciler_filled`` beside genuine strategy exits;
  * the exit-refinement corpus reads its entry→exit geometry as evidence about
    the strategy's exit quality, when the exit time was chosen by a human for a
    reason unrelated to the market.

The existing operator-flatten convention (``exit_reason=
'operator_flatten_reconciled'`` + ``notes.exit_price_source=
'operator_flatten_fill'``) lives in ``close_stranded_journal_row.py``, which
hard-filters ``WHERE status = 'open'`` — it is the tool for when the reconciler
FAILS to close the row. On the normal path, where the reconciler SUCCEEDS, the
marker is never applied. This script is that missing half.

⚠️ TARGETS ARE NAMED EXPLICITLY, NEVER DERIVED. There is no field that
distinguishes an operational flatten from a strategy exit — that absence is the
whole bug. So the caller passes trade ids and a reason; the script refuses to
guess. A filter-based version of this tool would be inventing the very signal it
exists to record.

⚠️ WHAT IT DOES NOT WRITE, deliberately. No monetary field (``pnl``,
``exit_price``, ``pnl_percent``) and — importantly — NOT ``exit_price_source``.
Trade 4934 carries ``bybit_closed_pnl``, genuine broker truth; stamping
``operator_flatten_fill`` over it would destroy a more specific provenance in
exactly the way
``BL-20260824-RECORDED-EXIT-PRICE-OUTNUMBERS-ALL-BROKER-TRUTH-COMBINED`` records
``order_monitor`` doing. (An id must never be line-wrapped: the ref guard scans
line by line, so a wrapped id reads as a truncated one that resolves to nothing.) The PRICE's provenance and the CLOSE's CAUSE
are different questions; this script answers only the second.

Usage on the VM:
    cd /home/ubuntu/ict-trading-bot
    python3 scripts/ops/mark_operator_flattened.py --trade-ids 4904,4934 \
        --reason "flattened to satisfy the flat-symbol guard for the \
bybit_2 hedge position-mode switch" # dry-run
    python3 scripts/ops/mark_operator_flattened.py --trade-ids 4904,4934 \
        --reason "..." --apply

Safety:
  * Refuses a row that is not ``status='closed'`` (an open row belongs to
    ``close-stranded-journal-row``), a backtest row, or an id that does not
    exist — each is reported, and nothing is written if ANY id is bad.
  * Idempotent: a row already carrying ``notes.closed_by_operator`` is skipped.
  * Records the prior label in ``notes.pre_mark_exit_reason``, so it is
    reversible per row.
  * Single transaction; partial failure rolls back.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple

EXIT_REASON = "operator_flatten_reconciled"
NOTE_FLAG = "closed_by_operator"


def _db_path() -> str:
    """The ONE sanctioned resolver. Deliberately no inline env-read and no
    CWD-relative fallback — that fallback is what seeded the stray duplicate
    journals, and `canonical-db-resolver` fails CI on it (it caught this exact
    file on its first draft)."""
    from src.utils.paths import trade_journal_db_path
    return str(trade_journal_db_path())


def _dump_notes(notes: Dict[str, Any]) -> str:
    """Serialize through the canonical capped writer when it is importable.

    Falls back to plain JSON only when src is unavailable (a bare invocation
    outside the repo); the fallback is why this is a function rather than an
    inline call — a silent uncapped write is exactly what caused the 5238/5239
    key loss, so the degraded path is named rather than implicit.
    """
    try:
        from src.utils.json_notes import dump_capped
        return dump_capped(notes, 500)
    except Exception:  # noqa: BLE001
        return json.dumps(notes)


def _load_notes(raw: Any) -> Dict[str, Any]:
    try:
        n = json.loads(raw) if raw else {}
    except (TypeError, ValueError, json.JSONDecodeError):
        return {"_original_notes": raw}
    return n if isinstance(n, dict) else {"_original_notes": raw}


def _recover_prior_exit_reason(conn: sqlite3.Connection, trade_id: int):
    """The pre-mark ``exit_reason`` for a row whose notes were shed.

    Reads ``order_packages.close_reason`` on the package linked to this trade.
    That column is written by the close cascade and is never touched by this
    script, so it is independent evidence rather than a restatement of the row
    being repaired. Returns ``None`` when it cannot be established — the caller
    REFUSES on None; it must not fall back to the row's own (already-marked)
    label.
    """
    try:
        cur = conn.execute(
            "SELECT close_reason FROM order_packages WHERE linked_trade_id = ? "
            "AND close_reason IS NOT NULL AND close_reason != '' "
            "ORDER BY updated_at DESC LIMIT 1", (trade_id,))
        got = cur.fetchone()
    except sqlite3.Error:
        return None
    if got is None:
        return None
    val = str(got[0] or "").strip()
    if not val or val == EXIT_REASON:
        return None  # the cascade carries the mark too — no independent prior
    return val


def plan(conn: sqlite3.Connection, trade_ids: List[int], reason: str
         ) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Return (updates, refusals). A non-empty refusal list means write NOTHING."""
    conn.row_factory = sqlite3.Row
    updates: List[Dict[str, Any]] = []
    refusals: List[str] = []
    now = datetime.now(timezone.utc).isoformat()

    for tid in trade_ids:
        cur = conn.execute(
            "SELECT id, status, exit_reason, notes, is_backtest, symbol, account_id "
            "FROM trades WHERE id = ?", (tid,))
        row = cur.fetchone()
        if row is None:
            refusals.append(f"id={tid}: no such trade row")
            continue
        if int(row["is_backtest"] or 0):
            refusals.append(f"id={tid}: is a backtest row")
            continue
        if str(row["status"] or "") != "closed":
            refusals.append(
                f"id={tid}: status={row['status']!r}, not 'closed' — an open row "
                f"belongs to close-stranded-journal-row, not here")
            continue
        notes = _load_notes(row["notes"])
        if notes.get(NOTE_FLAG):
            continue  # idempotent: already marked
        # THREE states, never collapsed — the flag's ABSENCE does not mean
        # unmarked. Trades 5238/5239 carry `exit_reason=EXIT_REASON` with the
        # notes keys gone (shed by `dump_capped(notes, 500)` before those keys
        # were protected). Taking `row["exit_reason"]` as the prior on such a
        # row records THE MARK ITSELF as what preceded it, destroying the one
        # field that makes the marking reversible — laundering a value into the
        # journal, the failure `PROTECTION_REASSERT_MODE` exists to refuse.
        prior_source = "trades.exit_reason"
        prior = row["exit_reason"]
        if str(prior or "") == EXIT_REASON:
            # Marked, notes lost. Recover the prior from an INDEPENDENT
            # source the marking tool never writes: the linked order package's
            # `close_reason`, stamped by the close cascade. Evidence, not memory.
            prior = _recover_prior_exit_reason(conn, tid)
            prior_source = "order_packages.close_reason"
            if prior is None:
                refusals.append(
                    f"id={tid}: already carries exit_reason={EXIT_REASON!r} but no "
                    f"{NOTE_FLAG} in notes (marked, notes shed by the cap), and the "
                    f"prior label is NOT recoverable from order_packages.close_reason "
                    f"— refusing rather than recording the mark as its own prior")
                continue
        notes[NOTE_FLAG] = True
        notes["operator_close_reason"] = reason
        notes["pre_mark_exit_reason"] = prior
        # Which evidence the prior came from. Without it a repaired row is
        # indistinguishable from a first-time mark, and they are not the same
        # claim about what was observed.
        notes["pre_mark_exit_reason_source"] = prior_source
        notes["operator_marked_at"] = now
        updates.append({
            "id": int(row["id"]),
            "symbol": row["symbol"],
            "account_id": row["account_id"],
            "prior_exit_reason": prior,
            # Cap it the way every other trades.notes writer does, at the
            # TIGHTEST budget in use (500 — 15 call sites use it, 7 use 2000).
            # Writing an uncapped blob here is what let the next capped rewrite
            # shed these keys on trades 5238/5239: this tool wrote plain JSON,
            # then order_monitor's broker-pnl close path re-read the notes,
            # added exit_price_source, and wrote back through
            # dump_capped(notes, 500). Capping here does not prevent that
            # rewrite — protecting the keys does — but it stops this tool from
            # being the one that pushes the blob over budget.
            "notes": _dump_notes(notes),
        })
    return updates, refusals


def apply(conn: sqlite3.Connection, updates: List[Dict[str, Any]]) -> int:
    with conn:
        for u in updates:
            conn.execute(
                "UPDATE trades SET exit_reason = ?, notes = ? WHERE id = ?",
                (EXIT_REASON, u["notes"], u["id"]))
    return len(updates)


def _self_test() -> int:
    # data-wiring: creates NO persistent table. This is an in-memory (":memory:")
    # fixture that exists only for the duration of --self-test. The canonical
    # store is trade_journal.db::trades in src/units/db/database.py; this script
    # only ever UPDATEs two of its columns and never creates it. The columns
    # below are a SUBSET of that DDL, and tests/ops/test_mark_operator_flattened
    # ::test_the_fixture_columns_exist_in_the_real_ddl asserts so — a fixture
    # declaring a schema production does not have is how the pairs tests passed
    # against a fictional order_packages table.
    conn = sqlite3.connect(":memory:")
    conn.execute("""CREATE TABLE trades (id INTEGER PRIMARY KEY, status TEXT,
        exit_reason TEXT, notes TEXT, is_backtest INTEGER, symbol TEXT,
        account_id TEXT, pnl REAL, exit_price REAL)""")
    # data-wiring: creates NO persistent table — an in-memory (":memory:")
    # --self-test fixture only. The canonical store is
    # trade_journal.db::order_packages in src/units/db/database.py; this script
    # never creates it and never WRITES it, it only SELECTs close_reason as the
    # independent evidence for a repaired row's prior label. History is not
    # backfilled here because nothing here persists. The four columns below are
    # a SUBSET of that canonical DDL, asserted by
    # tests/ops/test_mark_operator_flattened::test_the_fixture_columns_exist_in_the_real_ddl
    # — which is what makes this annotation true rather than merely present.
    conn.execute("""CREATE TABLE order_packages (order_package_id TEXT PRIMARY KEY,
        linked_trade_id INTEGER, close_reason TEXT, updated_at TEXT)""")
    conn.executemany(
        "INSERT INTO order_packages VALUES (?,?,?,?)",
        [("pkg-5", 5, "reconciler_filled", "2026-08-30T09:33:26+00:00"),
         ("pkg-6", 6, "", "2026-08-30T09:33:26+00:00")])
    conn.executemany(
        "INSERT INTO trades (id,status,exit_reason,notes,is_backtest,symbol,"
        "account_id,pnl,exit_price) VALUES (?,?,?,?,?,?,?,?,?)",
        [(1, "closed", "reconciler_filled",
          json.dumps({"exit_price_source": "bybit_closed_pnl"}), 0, "XRPUSDT",
          "bybit_2", -2.45, 1.38),
         (2, "open", "", "{}", 0, "ETHUSDT", "bybit_2", None, None),
         (3, "closed", "reconciler_filled", "{}", 1, "BTCUSDT", "bt", 0.0, 1.0),
         (4, "closed", EXIT_REASON, json.dumps({NOTE_FLAG: True}), 0, "ADAUSDT",
          "bybit_2", -1.0, 1.0),
         # 5 + 6: marked, notes SHED by the cap (trades 5238/5239 live). 5 has a
         # recoverable prior on its package; 6 does not.
         (5, "closed", EXIT_REASON, json.dumps({"_truncated": True}), 0,
          "BNBUSDT", "bybit_1", -1.0, 1.0),
         (6, "closed", EXIT_REASON, json.dumps({"_truncated": True}), 0,
          "BTCUSDT", "bybit_1", -1.0, 1.0)])
    conn.commit()
    fails = []

    def ck(name, ok):
        print(("  ok   " if ok else "  FAIL ") + name)
        if not ok:
            fails.append(name)

    ups, refs = plan(conn, [1], "test")
    ck("closed row is planned", len(ups) == 1 and ups[0]["id"] == 1)
    ck("prior label is recorded",
       json.loads(ups[0]["notes"])["pre_mark_exit_reason"] == "reconciler_filled")
    ck("broker-truth exit_price_source is left alone",
       json.loads(ups[0]["notes"])["exit_price_source"] == "bybit_closed_pnl")

    _, refs = plan(conn, [2], "test")
    ck("an OPEN row is refused", any("not 'closed'" in r for r in refs))
    _, refs = plan(conn, [3], "test")
    ck("a BACKTEST row is refused", any("backtest" in r for r in refs))
    ups, refs = plan(conn, [5], "test")
    ck("a marked-but-notes-shed row is REPAIRED, not re-marked",
       refs == [] and len(ups) == 1)
    ck("its prior comes from the package, NOT from the mark itself",
       json.loads(ups[0]["notes"])["pre_mark_exit_reason"] == "reconciler_filled")
    ck("and the prior's SOURCE is recorded",
       json.loads(ups[0]["notes"])["pre_mark_exit_reason_source"]
       == "order_packages.close_reason")

    _, refs = plan(conn, [6], "test")
    ck("an unrecoverable prior REFUSES rather than recording the mark as its own",
       any("NOT recoverable" in r for r in refs))

    _, refs = plan(conn, [999], "test")
    ck("a MISSING id is refused", any("no such trade" in r for r in refs))
    ups, refs = plan(conn, [4], "test")
    ck("an already-marked row is a no-op", ups == [] and refs == [])

    apply(conn, plan(conn, [1], "because")[0])
    r = conn.execute("SELECT exit_reason, notes, pnl, exit_price FROM trades "
                     "WHERE id=1").fetchone()
    ck("apply sets the operator exit_reason", r[0] == EXIT_REASON)
    n = json.loads(r[1])
    ck("apply records the reason", n["operator_close_reason"] == "because")
    ck("apply touches NO monetary field", r[2] == -2.45 and r[3] == 1.38)
    ups2, _ = plan(conn, [1], "again")
    ck("re-running is idempotent", ups2 == [])

    # Failure path: the planner must REFUSE rather than silently skip.
    ups3, refs3 = plan(conn, [1, 2], "mixed")
    ck("a bad id in a batch produces a refusal", len(refs3) == 1)

    print(f"self-test: {len(fails)} failure(s)")
    return 1 if fails else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--trade-ids", help="comma-separated trade ids")
    ap.add_argument("--reason", help="why the operator flattened these")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--db", default=None)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--from-intent", action="store_true",
                    help="DERIVE the targets from the operator_flatten_intent "
                         "marker the flatten script wrote itself, instead of "
                         "naming ids by hand. This is the durable path: it "
                         "needs nobody to remember which trades were flattened.")
    a = ap.parse_args(argv)
    if a.self_test:
        return _self_test()

    conn = sqlite3.connect(a.db or _db_path())

    if a.from_intent:
        if a.trade_ids:
            ap.error("--from-intent derives the ids; do not also pass --trade-ids")
        from src.runtime.operator_flatten_intent import find_unmarked_intent_rows
        pending = find_unmarked_intent_rows(conn)
        if not pending:
            print("No closed rows carry an unmarked operator_flatten_intent.")
            return 0
        ids = [r["id"] for r in pending]
        # The reason is the one the FLATTEN recorded, not one invented now.
        reasons = {r["intent"].get("reason") or "operator flatten" for r in pending}
        derived_reason = (reasons.pop() if len(reasons) == 1
                          else "operator flatten (multiple recorded reasons)")
        for r in pending:
            # `detail_state` is printed because a `shed` detail means the reason
            # below is a FALLBACK, not what the flatten recorded — the notes cap
            # dropped the prose (by design; the flag is what survives).
            print(f"  from-intent id={r['id']} {r['account_id']}/{r['symbol']} "
                  f"detail={r.get('detail_state')} "
                  f"intent_at={r['intent'].get('at')} reason={r['intent'].get('reason')!r}")
        a = argparse.Namespace(**{**vars(a), "reason": a.reason or derived_reason})
    else:
        if not a.trade_ids or not a.reason:
            ap.error("--trade-ids and --reason are both required "
                     "(or use --from-intent)")
        ids = [int(x) for x in a.trade_ids.split(",") if x.strip()]
    ups, refs = plan(conn, ids, a.reason)
    for r in refs:
        print(f"REFUSED {r}")
    if refs:
        print("Nothing written — resolve every refusal first.")
        return 2
    for u in ups:
        print(f"  id={u['id']} {u['account_id']}/{u['symbol']} "
              f"{u['prior_exit_reason']!r} -> {EXIT_REASON!r}")
    if not ups:
        print("No changes (already marked).")
        return 0
    if not a.apply:
        print(f"DRY-RUN: {len(ups)} row(s) would be marked. Re-run with --apply.")
        return 0
    n = apply(conn, ups)
    print(f"APPLIED: {n} row(s) marked {EXIT_REASON}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
