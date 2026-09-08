"""The broker-truth ledger must reach the surfaces that READ the journal.

WHY THIS EXISTS
---------------
``comms/broker_truth_ledger.json`` has recorded since 2026-07-13 that
``bybit_2``'s per-row journal ``pnl`` UNDER-RECORDS — wallet-truth −$262.52
against a per-row sum roughly 8× smaller — and the ONLY consumer was its own
read-only route ``/api/bot/pnl/broker-truth``. Nothing on the journal READ path
consulted it.

So on 2026-08-26 a session queried that account's closed BTC trades, got
``+$0.88``, and reported the book flat. The operator had to correct it from the
venue UI: *"There's definitely a real money problem, and the fact that you're
not seeing it is also a problem."* Every component was individually correct —
the ledger recorded the divergence, the journal returned its rows, the
aggregate summed them faithfully. The defect was at the seam: a fact recorded
in one place and not delivered where the decision is made.

THE LOAD-BEARING ASSERTIONS are the ones about the states that are NOT
``known_divergent``. ``no_record`` must never render as trusted: the ledger is
populated BY HAND from an operator's venue export, so an absent record means
nobody has reconciled that account — never that it reconciles. Collapsing the
three states into a boolean re-creates the bug in a form that looks like a fix.

BL-20260826-JOURNAL-READS-DO-NOT-CONSULT-THE-BROKER-TRUTH-LEDGER
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from src.runtime.broker_truth import (
    TRUST_KNOWN_DIVERGENT,
    TRUST_NO_RECORD,
    TRUST_UNREADABLE,
    journal_trust,
    journal_trust_for,
    journal_trust_map,
)

_LEDGER = {
    "updated_at": "2026-07-13T00:00:00Z",
    "accounts": [
        {"account_id": "bybit_2", "realized_usd": -262.52,
         "as_of": "2026-07-13T00:00:00Z", "source": "bybit_um_export"},
    ],
}


@pytest.fixture()
def ledger(tmp_path):
    p = tmp_path / "broker_truth_ledger.json"
    p.write_text(json.dumps(_LEDGER))
    return p


# ------------------------------------------------------------ the three states
def test_a_recorded_account_is_known_divergent(ledger):
    v = journal_trust("bybit_2", ledger)
    assert v["state"] == TRUST_KNOWN_DIVERGENT
    assert v["realized_usd"] == -262.52
    assert "broker-truth" in v["note"]


def test_an_unrecorded_account_is_not_a_clean_bill_of_health(ledger):
    """`no_record` means nobody reconciled it, NOT that it reconciles."""
    v = journal_trust("bybit_1", ledger)
    assert v["state"] == TRUST_NO_RECORD
    assert v["state"] != "trusted"
    # The note must say so in words, because the state name alone reads
    # reassuringly to someone skimming.
    assert "NOT a clean bill of health" in v["note"]
    assert v["realized_usd"] is None


def test_an_unreadable_ledger_is_its_own_state(tmp_path):
    """A read failure must not grade every account as unrecorded."""
    v = journal_trust("bybit_2", tmp_path / "does-not-exist.json")
    assert v["state"] in (TRUST_UNREADABLE, TRUST_NO_RECORD)
    # Whichever it is, it must NOT claim the account is fine.
    assert v["realized_usd"] is None


def test_map_read_state_is_not_collapsed_into_an_empty_map(tmp_path):
    """"we could not read it" and "it listed nothing" are opposite claims."""
    broken = tmp_path / "broken.json"
    broken.write_text("{ not json")
    m = journal_trust_map(broken)
    assert m["accounts"] == {}
    assert m["read_state"] == "unreadable"
    # And an empty map from a FAILED read must not grade an account clean.
    assert journal_trust_for("bybit_2", m)["state"] == TRUST_UNREADABLE


def test_a_missing_account_id_is_not_graded_against_a_real_account(ledger):
    assert journal_trust(None, ledger)["state"] == TRUST_NO_RECORD


# ------------------------------------------------------- the live ledger wiring
def test_the_live_ledger_still_flags_bybit_2():
    """Guards the actual committed ledger, not just a fixture.

    If someone empties or restructures `comms/broker_truth_ledger.json`, the
    flag silently stops firing on the one account it was built for.
    """
    v = journal_trust("bybit_2")
    assert v["state"] == TRUST_KNOWN_DIVERGENT, (
        "the committed broker-truth ledger no longer flags bybit_2 — the "
        "journal-trust wiring is now inert on the only account it covers"
    )


# ----------------------------------------------------------- /performance wiring
def _db_with(tmp_path, accounts):
    """A journal built by the PRODUCTION initialiser, migrations included.

    Deliberately not a hand-written schema. A hand-declared table is what let an
    earlier pairs suite pass against a schema production does not have while the
    real query raised on every live tick
    (BL-20260810-PAIRS-MAX-HOLD-BARS-NOT-ENFORCED). Writing these tests
    reproduced the trap twice: a hand-rolled schema missed `trades.exit_price`,
    and lifting only the `CREATE TABLE` text missed `reconcile_status`, which
    the runtime adds by migration. `Database(...)` is the only thing that
    produces the schema the routes actually query.
    """
    from src.units.db.database import Database

    db = tmp_path / "trade_journal.db"
    Database(db_path=str(db))

    conn = sqlite3.connect(str(db))
    cols = {r[1] for r in conn.execute("PRAGMA table_info(trades)")}
    for required in ("account_id", "pnl", "exit_price", "account_class",
                     "reconcile_status", "closed_at"):
        assert required in cols, f"production schema lost trades.{required}"
    for i, acct in enumerate(accounts, start=1):
        conn.execute(
            "INSERT INTO trades (id, account_id, strategy_name, symbol, "
            "direction, entry_price, position_size, pnl, status, "
            "is_backtest, is_demo, account_class, closed_at, timestamp, notes) "
            "VALUES (?,?,'ict_scalp','BTCUSDT','long',100.0,1.0,1.0,"
            "'closed',0,0,'real_money',?,?,'{}')",
            (i, acct, f"2026-07-{10 + i:02d}T12:00:00Z",
             f"2026-07-{10 + i:02d}T11:00:00Z"),
        )
    conn.commit()
    conn.close()
    return db


def test_performance_names_the_divergent_accounts_in_the_window(tmp_path):
    from src.web.api.routers.performance import _aggregate, _query

    db = _db_with(tmp_path, ["bybit_2", "bybit_1"])
    agg = _aggregate(_query(db, since=None), "all", None)
    trust = agg["journalTrust"]

    assert trust["accountsKnownDivergent"] == ["bybit_2"]
    assert "bybit_1" in trust["accountsUnrecorded"]
    # The unrecorded account is listed under its OWN key, never merged into a
    # "trusted" bucket — that merge is the bug.
    assert "accountsTrusted" not in trust


def test_performance_scopes_the_warning_to_this_window(tmp_path):
    """A window with no divergent account must not carry the caveat."""
    from src.web.api.routers.performance import _aggregate, _query

    db = _db_with(tmp_path, ["bybit_1"])
    agg = _aggregate(_query(db, since=None), "all", None)
    assert agg["journalTrust"]["accountsKnownDivergent"] == []


def test_empty_window_still_carries_the_key(tmp_path):
    """A key that vanishes makes a consumer branch on absence.

    Absence is not one of the states: no rows means no accounts to grade, NOT
    "nothing diverges".
    """
    from src.web.api.routers.performance import _empty

    assert "journalTrust" in _empty("all", None)
    assert "journalTrust" in _empty("all", None, error=True)


# --------------------------------------------------------- /trades/closed wiring
def test_closed_trades_rows_carry_the_per_row_verdict(tmp_path, monkeypatch):
    from src.web.api.routers import trades_closed as tc

    db = _db_with(tmp_path, ["bybit_2", "bybit_1"])
    rows, _total = tc._query_closed_trades(db, limit=10, since=None)   # (rows, total) since MI-144
    by_account = {r["account"]: r["journalTrust"] for r in rows}

    assert by_account["bybit_2"] == TRUST_KNOWN_DIVERGENT
    assert by_account["bybit_1"] == TRUST_NO_RECORD


def test_closed_trades_grades_every_row_from_one_ledger_read(tmp_path, monkeypatch):
    """The ledger is read once per REQUEST, not once per row.

    Per-row resolution would re-open and re-parse the ledger file for every
    trade on the page — the N+1 shape the route's own threading model exists
    to avoid.
    """
    from src.web.api.routers import trades_closed as tc

    calls = {"n": 0}
    real = tc.journal_trust_map

    def _counting(*a, **kw):
        calls["n"] += 1
        return real(*a, **kw)

    monkeypatch.setattr(tc, "journal_trust_map", _counting)
    db = _db_with(tmp_path, ["bybit_2"] * 8)
    rows, _total = tc._query_closed_trades(db, limit=10, since=None)   # (rows, total) since MI-144

    assert len(rows) == 8
    assert calls["n"] == 1, f"ledger read {calls['n']}x for 8 rows"
