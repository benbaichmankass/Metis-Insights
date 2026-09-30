"""GET /api/bot/performance/recent (the T3 demotion rule's input) and the
unknown-window 400 on GET /api/bot/performance (PI-20260929-VOLSKIP-SIGNAL-0006).

The golden under tests/fixtures/performance_window_golden/ was captured from
performance.py BEFORE either change, over the journal `_perf_window_golden_seed`
builds, with the clock and the journal-trust ledger frozen. Every valid window
token must still produce it byte-for-byte.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tests"))
import _perf_window_golden_seed as S  # noqa: E402

from src.web.api.routers import performance as P  # noqa: E402

GOLDEN = REPO / "tests/fixtures/performance_window_golden/golden.json"
_REAL_BOOK_ROSTERS = P._book_rosters


@pytest.fixture
def db(tmp_path, monkeypatch):
    def make(**kw):
        path = tmp_path / "trade_journal.db"
        S.seed(path, **kw)
        monkeypatch.setattr(P, "_DB_PATH", path)
        monkeypatch.setattr(P, "datetime", S.FrozenDatetime)
        monkeypatch.setattr(P, "journal_trust_map", lambda: S.TRUST_MAP)
        monkeypatch.setattr(P, "_portfolio_paper_account_ids", lambda: ["bybit_portfolio"])
        monkeypatch.setattr(P, "_book_rosters", lambda: ROSTERS)
        return path
    return make


ROSTERS = {"readState": "ok", "realMoneyLegs": ["leg_a", "leg_b", "leg_never_closed"],
           "portfolioAccounts": ["bybit_portfolio"], "portfolioLegs": ["leg_a"]}


# ── /performance: valid tokens unchanged, unknown token 400 ────────────────
@pytest.mark.parametrize("window", ["24h", "7d", "30d", "all"])
def test_valid_window_response_is_byte_identical_to_pre_change_golden(db, window):
    db()
    golden = json.loads(GOLDEN.read_text())[window]
    got = P.get_performance(window=window)
    assert json.dumps(got, sort_keys=True) == json.dumps(golden, sort_keys=True)


def test_golden_is_not_vacuous():
    """Positive control: the windows differ, so byte-identity above is a real test."""
    g = json.loads(GOLDEN.read_text())
    counts = [g[w]["totalTrades"] for w in ("24h", "7d", "30d", "all")]
    assert counts == sorted(counts) and len(set(counts)) == 4, counts


@pytest.mark.parametrize("bad", ["90d", "bogus", "", "ALL", "1d"])
def test_unknown_window_is_400_not_all_time(db, bad):
    db()
    with pytest.raises(HTTPException) as e:
        P.get_performance(window=bad)
    assert e.value.status_code == 400 and "unknown window" in e.value.detail


def test_unknown_window_is_400_through_http(db):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    db()
    app = FastAPI()
    app.include_router(P.router)
    c = TestClient(app)
    assert c.get("/api/bot/performance?window=90d").status_code == 400
    assert c.get("/api/bot/performance?window=7d").status_code == 200
    r = c.get("/api/bot/performance/recent?n=40")
    assert r.status_code == 200 and r.json()["n"] == 40
    assert c.get("/api/bot/performance/recent?n=30").status_code == 400   # not a block multiple
    assert c.get("/api/bot/performance/recent?n=0").status_code == 422    # below the floor


# ── /performance/recent ────────────────────────────────────────────────────
def test_recent_splits_the_last_40_into_two_20_trade_blocks_per_book(db):
    db()
    out = P.get_performance_recent(n=40)
    assert out["error"] is False and out["block"] == 20
    real = out["realMoney"]["perStrategy"]["leg_a"]
    assert real["closedAvailable"] == 70 and real["nUsed"] == 40 and real["complete"]
    assert [b["totalTrades"] for b in real["blocks"]] == [20, 20]
    # NEWEST FIRST and named; disjoint on trade IDS (not timestamps)
    assert [b["label"] for b in real["blocks"]] == ["recent", "prior"]
    recent, prior = real["blocks"]
    assert not set(recent["tradeIds"]) & set(prior["tradeIds"])
    assert prior["closedTo"] <= recent["closedFrom"]
    assert recent["firstTradeId"] == recent["tradeIds"][0]
    assert recent["lastTradeId"] == recent["tradeIds"][-1]
    assert out["realMoney"]["newestClosedAt"] == recent["closedTo"]
    # the mirror is the portfolio book only -- never the soak book
    mirror = out["mirror"]["perStrategy"]
    assert out["mirror"]["accountIds"] == ["bybit_portfolio"]
    assert "leg_a" in mirror and "leg_c" not in mirror
    assert "leg_c" not in out["realMoney"]["perStrategy"]


def test_a_rostered_leg_with_no_closed_trade_is_present_with_zero(db):
    db()
    leg = P.get_performance_recent(n=40)["realMoney"]["perStrategy"]["leg_never_closed"]
    assert leg["closedAvailable"] == 0 and leg["blocks"] == [] and leg["complete"] is False


def test_block_figures_are_aggregate_outputs_over_exactly_those_rows(db):
    """No second R/risk implementation: a block equals _aggregate() over its
    own 20 rows from _query(), and `last` over the 40."""
    path = db()
    out = P.get_performance_recent(n=40)
    rows = [r for r in P._query(path, None, demo=False) if r["strategy_name"] == "leg_a"]
    leg = out["realMoney"]["perStrategy"]["leg_a"]
    want_new = P._strip_envelope(P._aggregate(rows[-20:], "recent", None))
    want_old = P._strip_envelope(P._aggregate(rows[-40:-20], "recent", None))
    extra = ("label", "closedFrom", "closedTo", "firstTradeId", "lastTradeId", "tradeIds")
    for got, want in zip(leg["blocks"], (want_new, want_old)):
        assert {k: v for k, v in got.items() if k not in extra} == want
    assert leg["blocks"][0]["tradeIds"] == [r["trade_id"] for r in rows[-20:]]
    assert leg["last"] == P._strip_envelope(P._aggregate(rows[-40:], "recent", None))
    assert [s["name"] for s in leg["blocks"][0]["perStrategy"]] == ["leg_a"]
    assert leg["blocks"][0]["perStrategy"][0]["totalR"] is not None


def test_same_second_closes_are_ordered_by_trade_id_deterministically(tmp_path, monkeypatch):
    """Review of #14257: close time is second-precision. Two trades closing in
    the same second straddling the block edge must land in the same block
    whatever order SQLite happens to read them in -- the tiebreak is t.id."""
    import sqlite3
    outs = []
    for flip in (False, True):
        path = tmp_path / f"tie{flip}.db"
        S.seed(path, legs=(("leg_a", "bybit_2", "real_money", 0),), n_per=40)
        conn = sqlite3.connect(str(path))
        # trades 20 and 21 (ids) close in the same second; 21 is a -500 loss
        t20 = conn.execute("SELECT closed_at FROM trades WHERE id=20").fetchone()[0]
        conn.execute("UPDATE trades SET closed_at=?, timestamp=? WHERE id=21", (t20, t20))
        conn.execute("UPDATE trades SET pnl=-500 WHERE id=21")
        # Rebuild so `id` is a PLAIN column, not the rowid, and store the rows
        # in ascending or DESCENDING physical order: the scan order then
        # differs from id order, which is how a same-second tie became
        # order-dependent in the review's reproduction.
        conn.executescript(
            f"CREATE TABLE t2 AS SELECT * FROM trades ORDER BY id {'DESC' if flip else 'ASC'};"
            "DROP TABLE trades; ALTER TABLE t2 RENAME TO trades;")
        conn.commit()
        conn.close()
        monkeypatch.setattr(P, "_DB_PATH", path)
        monkeypatch.setattr(P, "journal_trust_map", lambda: S.TRUST_MAP)
        monkeypatch.setattr(P, "_book_rosters", lambda: {**ROSTERS, "portfolioAccounts": []})
        leg = P.get_performance_recent(n=40)["realMoney"]["perStrategy"]["leg_a"]
        outs.append([(b["tradeIds"], b["totalPnl"]) for b in leg["blocks"]])
    assert outs[0] == outs[1]
    recent_ids, prior_ids = outs[0][0][0], outs[0][1][0]
    assert 21 in recent_ids and 20 in prior_ids


def test_a_thin_leg_reports_incomplete_and_never_a_short_block(db):
    db(n_per=25)
    leg = P.get_performance_recent(n=40)["realMoney"]["perStrategy"]["leg_a"]
    assert leg["complete"] is False and leg["nUsed"] == 25
    assert [(b["label"], b["totalTrades"]) for b in leg["blocks"]] == [("recent", 20)]


def test_mirror_does_not_fall_back_to_the_soak_book(db, monkeypatch):
    db()
    monkeypatch.setattr(P, "_book_rosters", lambda: {**ROSTERS, "portfolioAccounts": [],
                                                     "portfolioLegs": []})
    out = P.get_performance_recent(n=40)
    assert out["mirror"]["readState"] == "no_portfolio_accounts_declared"
    assert out["mirror"]["perStrategy"] == {}


def test_unreadable_accounts_is_its_own_state_not_none_declared(db, monkeypatch):
    # collapsed-state: accounts_unreadable — "could not read the config" vs
    # "no portfolio account declared" are distinct mirror readStates.
    db()

    def boom(*a, **k):
        raise ValueError("garbled yaml")
    import src.config.accounts_loader as L
    monkeypatch.setattr(L, "load_accounts_dict", boom)
    monkeypatch.setattr(P, "_book_rosters", _REAL_BOOK_ROSTERS)
    out = P.get_performance_recent(n=40)
    assert out["mirror"]["readState"] == "accounts_unreadable"
    assert out["realMoney"]["rosterReadState"] == "unreadable"


def test_db_error_is_an_explicit_error_not_an_empty_book(db, monkeypatch):
    db()

    def boom(*a, **k):
        raise P.sqlite3.OperationalError("locked")
    monkeypatch.setattr(P, "_query", boom)
    out = P.get_performance_recent(n=40)
    assert out["error"] is True
    assert out["realMoney"]["readState"] == "error" and out["mirror"]["readState"] == "error"


def test_missing_db_reads_absent(tmp_path, monkeypatch):
    # collapsed-state: absent — this is /performance/recent's own readState
    # (ok / absent / error / no_portfolio_accounts_declared), not broker_truth's;
    # each of its states has its own test in this file.
    monkeypatch.setattr(P, "_DB_PATH", tmp_path / "nope.db")
    out = P.get_performance_recent(n=40)
    assert out["realMoney"]["readState"] == "absent" and out["error"] is False
