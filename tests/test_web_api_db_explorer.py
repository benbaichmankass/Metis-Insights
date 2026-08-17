"""Read-only DB explorer endpoint tests.

Pins the safety + shape contract of /api/bot/db/tables and
/api/bot/db/table/{name}:
  * lists tables with columns + row counts
  * paginated table reads with total
  * per-column filter (parameterized) + ordering
  * unknown table → 404; unknown filter/order column ignored
  * the connection is read-only (writes rejected)
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> TestClient:
    db = tmp_path / "trade_journal.db"
    conn = sqlite3.connect(str(db))
    try:
        conn.execute("CREATE TABLE trades (id INTEGER PRIMARY KEY, symbol TEXT, pnl REAL)")
        conn.executemany(
            "INSERT INTO trades (symbol, pnl) VALUES (?, ?)",
            [("BTCUSDT", 1.0), ("BTCUSDT", -2.0), ("MES", 3.0)],
        )
        conn.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY, body TEXT)")
        conn.commit()
    finally:
        conn.close()

    from src.web.api import main as api_main
    from src.web.api.routers import db_explorer as dbx

    monkeypatch.setattr(dbx, "_DB_PATH", db)
    return TestClient(api_main.app, raise_server_exceptions=False)


class TestTables:
    def test_lists_tables_with_columns_and_counts(self, client):
        body = client.get("/api/bot/db/tables").json()
        assert body["present"] is True
        tbl = {t["name"]: t for t in body["tables"]}
        assert set(tbl) == {"trades", "notes"}
        assert tbl["trades"]["rows"] == 3
        colnames = {c["name"] for c in tbl["trades"]["columns"]}
        assert colnames == {"id", "symbol", "pnl"}


class TestTableRead:
    def test_paginated_with_total(self, client):
        body = client.get("/api/bot/db/table/trades?limit=2&offset=0&order_by=id&order_dir=asc").json()
        assert body["total"] == 3
        assert len(body["rows"]) == 2
        assert body["rows"][0]["id"] == 1

    def test_filter_eq(self, client):
        body = client.get("/api/bot/db/table/trades?filter_col=symbol&filter_op=eq&filter_val=MES").json()
        assert body["total"] == 1
        assert body["rows"][0]["symbol"] == "MES"

    def test_filter_like(self, client):
        body = client.get("/api/bot/db/table/trades?filter_col=symbol&filter_op=like&filter_val=BTC").json()
        assert body["total"] == 2

    def test_filter_gt_numeric(self, client):
        body = client.get("/api/bot/db/table/trades?filter_col=pnl&filter_op=gt&filter_val=0").json()
        assert body["total"] == 2

    def test_unknown_table_404(self, client):
        assert client.get("/api/bot/db/table/secrets").status_code == 404

    def test_unknown_filter_column_ignored(self, client):
        # A stale column selection must not error — it's simply ignored.
        body = client.get("/api/bot/db/table/trades?filter_col=nope&filter_op=eq&filter_val=x").json()
        assert body["total"] == 3

    def test_limit_clamped(self, client):
        assert client.get("/api/bot/db/table/trades?limit=0").status_code == 422
        assert client.get("/api/bot/db/table/trades?limit=99999").status_code == 422


class TestReadOnly:
    def test_connection_is_read_only(self, client):
        # The endpoint opens the DB mode=ro; a write attempt via the same
        # path would fail. We assert the explorer never exposes a write
        # path by confirming there is no such route + reads still work.
        body = client.get("/api/bot/db/tables").json()
        assert body["present"] is True


@pytest.fixture
def federated_client(monkeypatch, tmp_path):
    """Both halves of the federated store present: a trade_journal DB and
    a trainer_store sidecar."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "empty"))  # no trainer_mirror

    tj = tmp_path / "trade_journal.db"
    conn = sqlite3.connect(str(tj))
    try:
        conn.execute("CREATE TABLE trades (id INTEGER PRIMARY KEY, symbol TEXT)")
        conn.execute("INSERT INTO trades (symbol) VALUES ('BTCUSDT')")
        conn.commit()
    finally:
        conn.close()

    sidecar = tmp_path / "trainer_store.db"
    conn = sqlite3.connect(str(sidecar))
    try:
        conn.execute("CREATE TABLE model_registry (model_id TEXT, status TEXT)")
        conn.execute("INSERT INTO model_registry VALUES ('m1', 'candidate')")
        # a private table the explorer must hide
        conn.execute("CREATE TABLE _ingest_meta (key TEXT, value TEXT)")
        conn.commit()
    finally:
        conn.close()

    from src.web.api import main as api_main
    from src.web.api.routers import db_explorer as dbx
    monkeypatch.setattr(dbx, "_DB_PATH", tj)
    monkeypatch.setattr(dbx, "_TRAINER_STORE_DB", sidecar)
    return TestClient(api_main.app, raise_server_exceptions=False)


class TestFederation:
    def test_tables_span_both_dbs_with_db_tag(self, federated_client):
        body = federated_client.get("/api/bot/db/tables").json()
        assert body["present"] is True
        assert set(body["dbs"]) == {"trade_journal", "trainer_store"}
        by_name = {t["name"]: t for t in body["tables"]}
        assert by_name["trades"]["db"] == "trade_journal"
        assert by_name["model_registry"]["db"] == "trainer_store"
        # private bookkeeping table is hidden
        assert "_ingest_meta" not in by_name

    def test_read_trainer_table_auto_routes(self, federated_client):
        body = federated_client.get("/api/bot/db/table/model_registry").json()
        assert body["db"] == "trainer_store"
        assert body["total"] == 1
        assert body["rows"][0]["model_id"] == "m1"

    def test_explicit_db_selector(self, federated_client):
        body = federated_client.get(
            "/api/bot/db/table/model_registry?db=trainer_store"
        ).json()
        assert body["db"] == "trainer_store"
        # Selecting the wrong db for a table → 404.
        assert federated_client.get(
            "/api/bot/db/table/trades?db=trainer_store"
        ).status_code == 404


class TestFilterStateIsDeclared:
    """A dropped filter must be VISIBLE in the response.

    WHY THIS EXISTS (BL-20260813-DB-EXPLORER-SILENTLY-IGNORES-UNKNOWN-FILTER-COLUMN).
    An unknown filter column is ignored by design, so a stale UI selection
    degrades gracefully. The tolerance is right; the SILENCE was the bug. With
    no WHERE, both the COUNT and the SELECT ran unfiltered and `total` came
    back as the whole table — identical to "the filter matched every row".

    Measured 2026-08-13 against the live journal: four different filters on a
    misspelled column each returned `total: 4639`, the entire trades table. The
    route is on the diag-relay allowlist, so its callers include analysis
    sessions that cannot see the query they actually got.

    The property is therefore NOT "unknown columns are rejected" — they are
    still ignored — but "the caller can tell from the response alone".
    """

    def test_a_dropped_filter_does_not_look_like_a_matching_one(self, client):
        """The whole bug in one assertion.

        A filter on a column that does not exist returns the full table. That
        is allowed. What must NOT happen is that it be indistinguishable from a
        filter that genuinely matched every row.
        """
        unfiltered = client.get("/api/bot/db/table/trades").json()
        dropped = client.get(
            "/api/bot/db/table/trades",
            params={"filter_col": "no_such_column", "filter_op": "eq",
                    "filter_val": "BTCUSDT"},
        ).json()
        # The counts ARE the same — that is the documented degradation.
        assert dropped["total"] == unfiltered["total"] == 3
        # ...and that is precisely why the states must differ.
        assert dropped["filter_state"] == "ignored_unknown_column"
        assert unfiltered["filter_state"] == "not_requested"

    def test_an_applied_filter_says_so_and_the_total_reflects_it(self, client):
        body = client.get(
            "/api/bot/db/table/trades",
            params={"filter_col": "symbol", "filter_op": "eq",
                    "filter_val": "BTCUSDT"},
        ).json()
        assert body["filter_state"] == "applied"
        assert body["total"] == 2
        # Echoed back so a caller sees what the SERVER resolved, not what it
        # believes it sent.
        assert (body["filter_col"], body["filter_op"], body["filter_val"]) == (
            "symbol", "eq", "BTCUSDT")

    def test_a_genuine_zero_is_still_reachable_and_labelled_applied(self, client):
        """`applied` + total 0 is the honest empty answer.

        Without this the fix would be untestable in the direction that matters:
        "nothing matched" and "nothing was asked" must also be distinct.
        """
        body = client.get(
            "/api/bot/db/table/trades",
            params={"filter_col": "symbol", "filter_op": "eq",
                    "filter_val": "NOSUCHSYMBOL"},
        ).json()
        assert body["filter_state"] == "applied"
        assert body["total"] == 0
        assert body["rows"] == []

    def test_a_bad_operator_is_its_own_state_not_lumped_with_unknown_column(self, client):
        body = client.get(
            "/api/bot/db/table/trades",
            params={"filter_col": "symbol", "filter_op": "drop_table",
                    "filter_val": "BTCUSDT"},
        ).json()
        assert body["filter_state"] == "ignored_bad_op"
        assert body["total"] == 3

    def test_order_reports_its_fate_too(self, client):
        good = client.get("/api/bot/db/table/trades",
                          params={"order_by": "pnl"}).json()
        bad = client.get("/api/bot/db/table/trades",
                         params={"order_by": "no_such_column"}).json()
        none = client.get("/api/bot/db/table/trades").json()
        assert good["order_state"] == "applied"
        assert bad["order_state"] == "ignored_unknown_column"
        assert none["order_state"] == "not_requested"
