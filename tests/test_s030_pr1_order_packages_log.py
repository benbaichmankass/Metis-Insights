"""S-030 PR1 regression tests — DB unit's order_packages log
(architecture-audit-2026-05-02 P1-5).

Three layers under test:
  1. ``Database.insert_order_package`` / ``update_order_package`` /
     ``get_order_packages_by_strategy`` — the writers + reader.
  2. ``Coordinator._log_new_order_package`` — the helper that
     ``multi_account_execute`` calls once per dispatch round.
  3. End-to-end through ``multi_account_execute`` — a fresh package
     produces a row whose ``order_package_id`` is then stamped on
     ``pkg.meta`` so per-account result rows can reference it.
"""
from __future__ import annotations

import json
import sqlite3
import textwrap
from unittest.mock import patch

import pytest

from src.core.coordinator import Coordinator, OrderPackage
from src.data_layer.database import Database


# ---------------------------------------------------------------------------
# Layer 1 — DB writers
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_db(tmp_path):
    return Database(db_path=str(tmp_path / "trade_journal.db"))


class TestDatabaseOrderPackagesTable:
    def test_table_created_with_indexes(self, tmp_db):
        conn = sqlite3.connect(str(tmp_db.db_path))
        try:
            row = conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name='order_packages'"
            ).fetchone()
            assert row is not None, "order_packages table must exist"

            indexes = {
                r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='index' AND tbl_name='order_packages'"
                )
            }
            assert "idx_order_packages_strategy_created" in indexes
            assert "idx_order_packages_status" in indexes
        finally:
            conn.close()

    def test_insert_minimal_row_returns_id(self, tmp_db):
        result = tmp_db.insert_order_package({
            "order_package_id": "pkg-abc",
            "strategy_name": "vwap",
            "symbol": "BTCUSDT",
            "direction": "short",
            "entry": 50_000.0,
            "sl": 50_500.0,
            "tp": 49_000.0,
        })
        assert result == "pkg-abc"

        rows = tmp_db.get_order_packages_by_strategy("vwap")
        assert len(rows) == 1
        row = rows[0]
        assert row["status"] == "open"
        assert row["created_at"] is not None
        assert row["updated_at"] is not None

    def test_insert_serialises_meta_dict(self, tmp_db):
        tmp_db.insert_order_package({
            "order_package_id": "pkg-meta",
            "strategy_name": "vwap",
            "symbol": "BTCUSDT",
            "direction": "long",
            "entry": 50_000.0,
            "sl": 49_500.0,
            "tp": 51_000.0,
            "meta": {"strategy_risk_pct": 0.5, "killzone": "asia"},
        })
        row = tmp_db.get_order_packages_by_strategy("vwap")[0]
        # meta is stored as JSON text.
        decoded = json.loads(row["meta"])
        assert decoded == {"strategy_risk_pct": 0.5, "killzone": "asia"}

    def test_insert_requires_order_package_id(self, tmp_db):
        with pytest.raises(ValueError):
            tmp_db.insert_order_package({
                "strategy_name": "vwap",
                "symbol": "BTCUSDT",
                "direction": "long",
                "entry": 50_000.0,
                "sl": 49_500.0,
                "tp": 51_000.0,
            })

    def test_update_modifies_status_and_bumps_updated_at(self, tmp_db):
        tmp_db.insert_order_package({
            "order_package_id": "pkg-upd",
            "strategy_name": "vwap",
            "symbol": "BTCUSDT",
            "direction": "long",
            "entry": 50_000.0,
            "sl": 49_500.0,
            "tp": 51_000.0,
        })
        before = tmp_db.get_order_packages_by_strategy("vwap")[0]
        affected = tmp_db.update_order_package(
            "pkg-upd",
            {"status": "closed", "close_reason": "sl_hit", "linked_trade_id": 7},
        )
        assert affected == 1

        after = tmp_db.get_order_packages_by_strategy("vwap")[0]
        assert after["status"] == "closed"
        assert after["close_reason"] == "sl_hit"
        assert after["linked_trade_id"] == 7
        assert after["updated_at"] >= before["updated_at"]

    def test_update_unknown_id_returns_zero(self, tmp_db):
        affected = tmp_db.update_order_package("pkg-missing", {"status": "closed"})
        assert affected == 0

    def test_update_requires_id(self, tmp_db):
        with pytest.raises(ValueError):
            tmp_db.update_order_package("", {"status": "closed"})

    def test_get_by_strategy_filters_and_orders_newest_first(self, tmp_db):
        # Two strategies, three rows.
        tmp_db.insert_order_package({
            "order_package_id": "pkg-1",
            "strategy_name": "vwap",
            "symbol": "BTCUSDT", "direction": "long",
            "entry": 1.0, "sl": 0.9, "tp": 1.1,
            "created_at": "2026-05-02T10:00:00+00:00",
            "updated_at": "2026-05-02T10:00:00+00:00",
        })
        tmp_db.insert_order_package({
            "order_package_id": "pkg-2",
            "strategy_name": "vwap",
            "symbol": "BTCUSDT", "direction": "short",
            "entry": 1.0, "sl": 1.1, "tp": 0.9,
            "created_at": "2026-05-02T11:00:00+00:00",
            "updated_at": "2026-05-02T11:00:00+00:00",
        })
        tmp_db.insert_order_package({
            "order_package_id": "pkg-3",
            "strategy_name": "turtle_soup",
            "symbol": "BTCUSDT", "direction": "long",
            "entry": 1.0, "sl": 0.9, "tp": 1.1,
            "created_at": "2026-05-02T11:30:00+00:00",
            "updated_at": "2026-05-02T11:30:00+00:00",
        })

        vwap_rows = tmp_db.get_order_packages_by_strategy("vwap")
        assert [r["order_package_id"] for r in vwap_rows] == ["pkg-2", "pkg-1"]

        turtle_rows = tmp_db.get_order_packages_by_strategy("turtle_soup")
        assert [r["order_package_id"] for r in turtle_rows] == ["pkg-3"]

    def test_get_by_strategy_status_filter(self, tmp_db):
        tmp_db.insert_order_package({
            "order_package_id": "pkg-open",
            "strategy_name": "vwap",
            "symbol": "X", "direction": "long",
            "entry": 1.0, "sl": 0.9, "tp": 1.1,
            "status": "open",
        })
        tmp_db.insert_order_package({
            "order_package_id": "pkg-closed",
            "strategy_name": "vwap",
            "symbol": "X", "direction": "long",
            "entry": 1.0, "sl": 0.9, "tp": 1.1,
            "status": "closed",
        })
        open_rows = tmp_db.get_order_packages_by_strategy("vwap", status="open")
        assert [r["order_package_id"] for r in open_rows] == ["pkg-open"]


# ---------------------------------------------------------------------------
# Layer 2 — _log_new_order_package helper
# ---------------------------------------------------------------------------


class TestLogNewOrderPackageHelper:
    def test_helper_inserts_and_returns_id(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TRADE_JOURNAL_DB", str(tmp_path / "trade_journal.db"))
        from src.core.coordinator import _log_new_order_package

        pkg = OrderPackage(
            strategy="vwap", symbol="BTCUSDT", direction="short",
            entry=50_000.0, sl=50_500.0, tp=49_000.0,
            confidence=0.8,
            meta={"strategy_name": "vwap", "killzone": "asia"},
        )

        order_package_id = _log_new_order_package(pkg)
        assert order_package_id and order_package_id.startswith("pkg-")

        db = Database(db_path=str(tmp_path / "trade_journal.db"))
        rows = db.get_order_packages_by_strategy("vwap")
        assert len(rows) == 1
        row = rows[0]
        assert row["order_package_id"] == order_package_id
        assert row["strategy_name"] == "vwap"
        assert row["confidence"] == pytest.approx(0.8)
        assert row["status"] == "open"

    def test_helper_returns_none_on_db_failure(self, tmp_path, monkeypatch):
        monkeypatch.setenv(
            "TRADE_JOURNAL_DB", str(tmp_path / "missing_dir" / "x.db"),
        )
        from src.core.coordinator import _log_new_order_package

        pkg = OrderPackage(
            strategy="vwap", symbol="BTCUSDT", direction="short",
            entry=50_000.0, sl=50_500.0, tp=49_000.0,
        )
        # Should swallow the error and return None.
        result = _log_new_order_package(pkg)
        assert result is None


# ---------------------------------------------------------------------------
# Layer 3 — multi_account_execute integration
# ---------------------------------------------------------------------------


_ACCOUNTS_YAML = textwrap.dedent("""\
    accounts:
      bybit_2:
        type: regular
        exchange: bybit
        api_key_env: BYBIT_KEY_2
        strategies: [vwap]
        risk:
          max_dd_pct: 0.05
          daily_usd: 100
          pos_size: 500
          risk_pct: 0.01
          min_balance_usd: 50
""")


@pytest.fixture()
def coord_and_yaml(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADE_JOURNAL_DB", str(tmp_path / "trade_journal.db"))
    # Post-PR-#507 the coordinator filters out accounts where
    # ``configured=False`` (env-var creds missing). The fixture's
    # accounts.yaml declares ``api_key_env: BYBIT_KEY_2`` so we have
    # to plant the var to make the loader mark the account configured.
    monkeypatch.setenv("BYBIT_KEY_2", "test-value")
    monkeypatch.setenv("BYBIT_KEY_2_SECRET", "test-secret")
    units_yaml = tmp_path / "units.yaml"
    units_yaml.write_text("units: {}\n")
    accounts_yaml = tmp_path / "accounts.yaml"
    accounts_yaml.write_text(_ACCOUNTS_YAML)
    return Coordinator(units_path=str(units_yaml)), str(accounts_yaml), tmp_path


class TestMultiAccountExecuteWritesOrderPackageRow:
    def test_dispatch_inserts_one_row_and_stamps_pkg_meta(
        self, coord_and_yaml,
    ):
        coord, accounts_yaml, tmp_path = coord_and_yaml

        pkg = OrderPackage(
            strategy="vwap", symbol="BTCUSDT", direction="short",
            entry=50_000.0, sl=50_500.0, tp=49_000.0,
            confidence=0.7,
            meta={"strategy_name": "vwap"},
        )

        with patch(
            "src.units.accounts.execute.execute_pkg",
            side_effect=lambda p, cfg, **kw: f"dry-{cfg['account_id']}",
        ):
            coord.multi_account_execute(
                pkg, accounts_path=accounts_yaml, dry_run=True,
                balance_fetcher=lambda _a: 10_000.0,
            )

        # The package's meta now carries the order_package_id.
        assert pkg.meta.get("order_package_id", "").startswith("pkg-")

        db = Database(db_path=str(tmp_path / "trade_journal.db"))
        rows = db.get_order_packages_by_strategy("vwap")
        assert len(rows) == 1
        row = rows[0]
        assert row["order_package_id"] == pkg.meta["order_package_id"]
        assert row["status"] == "open"
        assert row["confidence"] == pytest.approx(0.7)

    def test_journal_failure_does_not_break_dispatch(
        self, coord_and_yaml, monkeypatch,
    ):
        coord, accounts_yaml, tmp_path = coord_and_yaml
        # Point at an unwritable path AFTER the fixture set TRADE_JOURNAL_DB.
        monkeypatch.setenv(
            "TRADE_JOURNAL_DB", str(tmp_path / "nope_dir" / "x.db"),
        )

        pkg = OrderPackage(
            strategy="vwap", symbol="BTCUSDT", direction="short",
            entry=50_000.0, sl=50_500.0, tp=49_000.0,
            meta={"strategy_name": "vwap"},
        )

        with patch(
            "src.units.accounts.execute.execute_pkg",
            side_effect=lambda p, cfg, **kw: f"dry-{cfg['account_id']}",
        ):
            results = coord.multi_account_execute(
                pkg, accounts_path=accounts_yaml, dry_run=True,
                balance_fetcher=lambda _a: 10_000.0,
            )

        # Dispatch still returned a result for the assigned account.
        assert any(r["error"] is None for r in results)


# ---------------------------------------------------------------------------
# BUG-049 backstop — a package with no eligible account is terminalised,
# never left status='open' to orphan at +5min.
# ---------------------------------------------------------------------------


class TestMultiAccountExecuteBug049Backstop:
    """A logged package that places no trade must reach a terminal status in
    the same dispatch round, so the monitor reconciler never mis-stamps it
    'orphaned — never executed' (BUG-049, system-review 2026-06-25)."""

    def test_no_eligible_account_terminalises_package(self, coord_and_yaml):
        coord, accounts_yaml, tmp_path = coord_and_yaml

        # 'turtle_soup' is on NO account's strategies list (the fixture routes
        # only 'vwap' to bybit_2) → the eligibility filter yields zero accounts,
        # the per-account loop never runs, and pre-fix the package would sit
        # status='open' / linked_trade_id=NULL → orphaned at +5min.
        pkg = OrderPackage(
            strategy="turtle_soup", symbol="BTCUSDT", direction="short",
            entry=50_000.0, sl=50_500.0, tp=49_000.0,
            confidence=0.7, meta={"strategy_name": "turtle_soup"},
        )

        results = coord.multi_account_execute(
            pkg, accounts_path=accounts_yaml, dry_run=True,
            balance_fetcher=lambda _a: 10_000.0,
        )

        # No eligible account → no per-account result rows.
        assert results == []

        db = Database(db_path=str(tmp_path / "trade_journal.db"))
        rows = db.get_order_packages_by_strategy("turtle_soup")
        assert len(rows) == 1
        row = rows[0]
        # Terminalised in-round — NOT left 'open' to orphan.
        assert row["status"] == "rejected"
        assert row["close_reason"] == "no_eligible_account"
        assert row["linked_trade_id"] is None

    def test_successful_dispatch_left_open_for_trade_link(self, coord_and_yaml):
        """The backstop must NOT touch a package that DID place a trade — it
        stays 'open' so the trade row's order_package_id links it (unchanged)."""
        coord, accounts_yaml, tmp_path = coord_and_yaml

        pkg = OrderPackage(
            strategy="vwap", symbol="BTCUSDT", direction="short",
            entry=50_000.0, sl=50_500.0, tp=49_000.0,
            confidence=0.7, meta={"strategy_name": "vwap"},
        )

        with patch(
            "src.units.accounts.execute.execute_pkg",
            side_effect=lambda p, cfg, **kw: f"dry-{cfg['account_id']}",
        ):
            coord.multi_account_execute(
                pkg, accounts_path=accounts_yaml, dry_run=True,
                balance_fetcher=lambda _a: 10_000.0,
            )

        db = Database(db_path=str(tmp_path / "trade_journal.db"))
        row = db.get_order_packages_by_strategy("vwap")[0]
        assert row["status"] == "open"
        assert row["close_reason"] in (None, "")
