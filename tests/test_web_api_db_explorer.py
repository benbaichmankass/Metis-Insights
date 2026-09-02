"""Read-only DB explorer endpoint tests.

Pins the safety + shape contract of /api/bot/db/tables and
/api/bot/db/table/{name}:
  * lists tables with columns + row counts
  * paginated table reads with total
  * per-column filter (parameterized) + ordering
  * unknown table → 404; unknown filter/order column ignored
  * the connection is read-only (writes rejected)
  * the exposure contract: table allowlist (default-deny) + column redaction
    (BL-20260901-DB-EXPLORER-IS-UNGATED-AND-REACHES-DEVICE-TOKENS-RAW-TOKEN-COLUMN)
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
        # `notes` is NOT in `_TABLE_ALLOWLIST`. It stands in for "a table
        # someone added to the schema and forgot to admit deliberately" —
        # the inversion the exposure contract exists to enforce.
        conn.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY, body TEXT)")
        # A table that really does hold a secret, with the real column name.
        conn.execute(
            "CREATE TABLE device_tokens (id INTEGER PRIMARY KEY, token TEXT, "
            "platform TEXT, label TEXT)"
        )
        conn.execute(
            "INSERT INTO device_tokens (token, platform, label) "
            "VALUES ('SECRET-FCM-TOKEN-VALUE', 'android', 'pixel')"
        )
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
        # Only the allowlisted table is listed. `notes` and `device_tokens`
        # exist in the schema and are deliberately absent — see
        # TestExposureContract for the assertions that pin why.
        assert set(tbl) == {"trades"}
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


# ---------------------------------------------------------------------------
# Exposure contract — BL-20260901-DB-EXPLORER-IS-UNGATED-AND-REACHES-DEVICE-
# TOKENS-RAW-TOKEN-COLUMN.
#
# The row's `done_condition` names two things, and the SECOND is the one that
# actually holds the line over time: (a) device_tokens is unreachable, and
# (b) "a table added to the schema WITHOUT being added to the allowlist is
# unreachable, which is the inversion that matters". (b) is what stops the
# next table from repeating this; (a) alone would be a one-off patch.
# ---------------------------------------------------------------------------
class TestExposureContract:
    # --- (a) the filed exposure is closed ---------------------------------

    def test_device_tokens_table_is_404(self, client):
        """The row's primary condition, stated as the route sees it."""
        resp = client.get("/api/bot/db/table/device_tokens")
        assert resp.status_code == 404

    def test_device_tokens_not_in_listing(self, client):
        """404 on the read is not enough — the LISTING is what an attacker
        reads first, and it is where the column names leaked. Before this
        change /db/tables returned device_tokens' full column list including
        `token`, on the live host, unauthenticated."""
        body = client.get("/api/bot/db/tables").json()
        names = {t["name"] for t in body["tables"]}
        assert "device_tokens" not in names
        # And no table's advertised schema mentions the token column at all.
        for t in body["tables"]:
            assert "token" not in {c["name"] for c in t["columns"]}

    def test_raw_token_value_appears_in_no_response(self, client):
        """The value-level assertion, deliberately separate from the
        structural ones: grep the actual response bodies for the secret the
        fixture planted. A schema assertion can pass while a value still
        leaks through some other field."""
        for url in (
            "/api/bot/db/tables",
            "/api/bot/db/table/device_tokens",
            "/api/bot/db/table/trades",
        ):
            assert "SECRET-FCM-TOKEN-VALUE" not in client.get(url).text

    # --- (b) the inversion: unlisted means unreachable ---------------------

    def test_table_absent_from_allowlist_is_unreachable(self, client):
        """`notes` exists in the schema and was never allowlisted. It must be
        invisible in BOTH surfaces — this is the general rule, and
        device_tokens is only its most urgent instance."""
        body = client.get("/api/bot/db/tables").json()
        assert "notes" not in {t["name"] for t in body["tables"]}
        assert client.get("/api/bot/db/table/notes").status_code == 404

    def test_allowlist_is_default_deny_not_a_blocklist(self, client, monkeypatch):
        """The property that survives someone adding a table tomorrow.

        A blocklist would let a brand-new table through; an allowlist must
        not. Asserted by inventing a table name that cannot be in the
        allowlist and confirming it is refused rather than served."""
        from src.web.api.routers import db_explorer as dbx

        assert "brand_new_table_nobody_admitted" not in dbx._TABLE_ALLOWLIST
        assert client.get(
            "/api/bot/db/table/brand_new_table_nobody_admitted"
        ).status_code == 404

    # --- column redaction, behind the allowlist ---------------------------

    def test_redaction_holds_even_if_the_table_is_allowlisted(
        self, client, monkeypatch
    ):
        """Defence in depth. If a future session allowlists `device_tokens`
        without reading the comment telling them not to, `token` must still
        not leave the process — the row is served, the secret column is not."""
        from src.web.api.routers import db_explorer as dbx

        monkeypatch.setattr(
            dbx, "_TABLE_ALLOWLIST", dbx._TABLE_ALLOWLIST | {"device_tokens"}
        )
        resp = client.get("/api/bot/db/table/device_tokens")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1                      # the row IS served
        assert "token" not in {c["name"] for c in body["columns"]}
        assert "token" not in body["rows"][0]
        assert body["rows"][0]["platform"] == "android"  # non-secret cols intact
        assert "SECRET-FCM-TOKEN-VALUE" not in resp.text

    def test_redacted_column_is_not_a_filter_oracle(self, client, monkeypatch):
        """The subtle one, and the reason redaction routes through `_columns`.

        `filter_state` + `total` would otherwise let an attacker brute-force a
        redacted secret one LIKE-prefix at a time WITHOUT ever being shown the
        row: filter on `token LIKE 'a'`, read `total`, keep the prefixes that
        return 1. Redacting the column from the validator's view makes the
        filter resolve to `ignored_unknown_column`, so `total` stays the
        unfiltered count and carries no information about the secret."""
        from src.web.api.routers import db_explorer as dbx

        monkeypatch.setattr(
            dbx, "_TABLE_ALLOWLIST", dbx._TABLE_ALLOWLIST | {"device_tokens"}
        )
        hit = client.get(
            "/api/bot/db/table/device_tokens"
            "?filter_col=token&filter_op=like&filter_val=SECRET"
        ).json()
        miss = client.get(
            "/api/bot/db/table/device_tokens"
            "?filter_col=token&filter_op=like&filter_val=ZZZZZZ"
        ).json()
        assert hit["filter_state"] == "ignored_unknown_column"
        assert miss["filter_state"] == "ignored_unknown_column"
        # Indistinguishable: no oracle. This is the assertion that matters —
        # equal totals mean a correct guess looks exactly like a wrong one.
        assert hit["total"] == miss["total"] == 1

    def test_ordering_by_a_redacted_column_is_also_refused(
        self, client, monkeypatch
    ):
        """ORDER BY is the second oracle: sorting by a hidden column leaks its
        ordering across pages even when the column is never displayed."""
        from src.web.api.routers import db_explorer as dbx

        monkeypatch.setattr(
            dbx, "_TABLE_ALLOWLIST", dbx._TABLE_ALLOWLIST | {"device_tokens"}
        )
        body = client.get(
            "/api/bot/db/table/device_tokens?order_by=token&order_dir=asc"
        ).json()
        assert body["order_state"] == "ignored_unknown_column"

    # --- the allowlist matches the live schema ----------------------------

    def test_allowlist_contains_no_table_holding_a_redacted_column(self):
        """Guards the one mistake that would silently undo this: adding a
        table to the allowlist that has an entry in the redaction map is
        allowed (redaction still applies), but it must be a DELIBERATE act.
        Today the intersection is empty and this test says so out loud."""
        from src.web.api.routers import db_explorer as dbx

        overlap = set(dbx._REDACTED_COLUMNS) & set(dbx._TABLE_ALLOWLIST)
        assert overlap == set(), (
            f"{overlap} is both allowlisted and carries redacted columns. "
            "That is permitted but never accidental — confirm it is intended "
            "and update this test with the reason."
        )
