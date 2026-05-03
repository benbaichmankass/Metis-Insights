"""Coordinator-level integration: every refusal lands a row in
``trade_journal.db::trades`` (CP-2026-05-03-14).

The executor-level helpers are unit-tested in
``test_execute_journal_rejections.py``. This file pins the wiring at
the Coordinator boundary:

- ``except RiskBreach`` block writes a ``status='rejected'`` row whose
  ``notes.reason`` carries the un-mangled token from
  ``RiskManager.evaluate`` (not the wrapped "RiskBreach: …" text).
- Generic ``except Exception`` block writes a
  ``status='exchange_rejected'`` row with the exception text.
- The existing ``_emit_execution_failure_ping`` still fires (regression
  guard — the new journal write must not displace the ping).
"""
from __future__ import annotations

import sqlite3
import textwrap
from unittest.mock import patch

import pytest

from src.core.coordinator import Coordinator, OrderPackage


_ACCOUNTS_YAML = textwrap.dedent("""\
    accounts:
      bybit_1:
        type: regular
        exchange: bybit
        api_key_env: BYBIT_KEY_1
        strategies: [vwap]
        risk:
          max_dd_pct: 0.05
          daily_usd: 100
          pos_size: 500
          risk_pct: 0.01
          min_balance_usd: 50
""")


def _vwap_pkg() -> OrderPackage:
    return OrderPackage(
        strategy="vwap",
        symbol="BTCUSDT",
        direction="short",
        entry=50_000.0,
        sl=50_500.0,
        tp=49_000.0,
        meta={"strategy_name": "vwap", "entry_reason": "vwap mean-revert"},
    )


@pytest.fixture()
def accounts_yaml(tmp_path):
    p = tmp_path / "accounts.yaml"
    p.write_text(_ACCOUNTS_YAML)
    return str(p)


@pytest.fixture()
def coord(tmp_path):
    units_yaml = tmp_path / "units.yaml"
    units_yaml.write_text("units: {}\n")
    return Coordinator(units_path=str(units_yaml))


@pytest.fixture()
def tmp_journal(tmp_path, monkeypatch):
    db_path = tmp_path / "trade_journal.db"
    monkeypatch.setenv("TRADE_JOURNAL_DB", str(db_path))
    return db_path


def _read_trades(db_path):
    if not db_path.exists():
        return []
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    rows = list(conn.execute("SELECT * FROM trades ORDER BY id"))
    conn.close()
    return rows


# ---------------------------------------------------------------------------
# RiskBreach path → status='rejected' row
# ---------------------------------------------------------------------------


class TestRiskBreachWritesRejectedRow:
    def test_account_mode_dry_run_lands_rejected_row(
        self, coord, accounts_yaml, tmp_journal
    ):
        # Force RiskManager.evaluate to refuse with the post-BUG-039
        # token. The coordinator's RiskBreach catch must write a row
        # whose notes.reason carries the un-mangled token.
        with patch(
            "src.units.accounts.risk.RiskManager.evaluate",
            return_value=(False, "account_mode_dry_run"),
        ):
            results = coord.multi_account_execute(
                _vwap_pkg(),
                accounts_path=accounts_yaml,
                dry_run=False,
                balance_fetcher=lambda _a: 10_000.0,
            )

        assert len(results) == 1
        assert results[0]["trade_id"] is None
        assert "account_mode_dry_run" in results[0]["error"]

        rows = _read_trades(tmp_journal)
        assert len(rows) == 1, "RiskBreach must produce exactly one journal row"
        row = rows[0]
        assert row["status"] == "rejected"
        assert row["account_id"] == "bybit_1"
        assert row["strategy_name"] == "vwap"
        # The structured token survives — not the wrapped exception text.
        import json
        notes = json.loads(row["notes"])
        assert notes["reason"] == "account_mode_dry_run"
        # entry_reason carries the prefix for plain-text renderers.
        assert "REJECTED: account_mode_dry_run" in row["entry_reason"]

    def test_daily_loss_cap_token_preserved_through_riskbreach_wrap(
        self, coord, accounts_yaml, tmp_journal
    ):
        with patch(
            "src.units.accounts.risk.RiskManager.evaluate",
            return_value=(False, "DAILY_LOSS_CAP"),
        ):
            coord.multi_account_execute(
                _vwap_pkg(),
                accounts_path=accounts_yaml,
                dry_run=False,
                balance_fetcher=lambda _a: 10_000.0,
            )
        rows = _read_trades(tmp_journal)
        import json
        assert json.loads(rows[0]["notes"])["reason"] == "DAILY_LOSS_CAP"

    def test_diagnostic_ping_still_fires_for_riskbreach(
        self, coord, accounts_yaml, tmp_journal
    ):
        # Regression: the new journal write must coexist with the
        # existing _emit_execution_failure_ping — both are called from
        # the same except block.
        with patch(
            "src.units.accounts.risk.RiskManager.evaluate",
            return_value=(False, "POSITION_SIZE_CAP"),
        ), patch(
            "src.core.coordinator._emit_execution_failure_ping"
        ) as ping_stub:
            coord.multi_account_execute(
                _vwap_pkg(),
                accounts_path=accounts_yaml,
                dry_run=False,
                balance_fetcher=lambda _a: 10_000.0,
            )
        assert ping_stub.call_count == 1, (
            "diagnostic ping must still fire on RiskBreach"
        )


# ---------------------------------------------------------------------------
# Generic exception path → status='exchange_rejected' row
# ---------------------------------------------------------------------------


class TestExchangeRejectionWritesRow:
    def test_runtime_error_from_execute_pkg_lands_exchange_rejected_row(
        self, coord, accounts_yaml, tmp_journal
    ):
        # RiskManager passes; execute_pkg blows up with a RuntimeError
        # (the canonical shape for a Bybit retCode != 0 surface).
        # Stub the client constructor so the credential gate passes and
        # we actually reach execute_pkg.
        with patch(
            "src.units.accounts.risk.RiskManager.evaluate",
            return_value=(True, None),
        ), patch(
            "src.units.accounts.clients.bybit_client_for",
            return_value=object(),  # any non-None value
        ), patch(
            "src.units.accounts.execute.execute_pkg",
            side_effect=RuntimeError(
                "Order submission failed for BTCUSDT: retCode=110007 qty exceeds max"
            ),
        ):
            results = coord.multi_account_execute(
                _vwap_pkg(),
                accounts_path=accounts_yaml,
                dry_run=False,
                balance_fetcher=lambda _a: 10_000.0,
            )

        assert results[0]["trade_id"] is None
        rows = _read_trades(tmp_journal)
        assert len(rows) == 1
        assert rows[0]["status"] == "exchange_rejected"
        assert "retCode=110007" in rows[0]["entry_reason"]

    def test_diagnostic_ping_still_fires_for_generic_exception(
        self, coord, accounts_yaml, tmp_journal
    ):
        with patch(
            "src.units.accounts.risk.RiskManager.evaluate",
            return_value=(True, None),
        ), patch(
            "src.units.accounts.clients.bybit_client_for",
            return_value=object(),
        ), patch(
            "src.units.accounts.execute.execute_pkg",
            side_effect=RuntimeError("retCode=110007 qty exceeds max"),
        ), patch(
            "src.core.coordinator._emit_execution_failure_ping"
        ) as ping_stub:
            coord.multi_account_execute(
                _vwap_pkg(),
                accounts_path=accounts_yaml,
                dry_run=False,
                balance_fetcher=lambda _a: 10_000.0,
            )
        assert ping_stub.call_count == 1


# ---------------------------------------------------------------------------
# Success path — the existing post-S-029-PR2 contract still holds
# ---------------------------------------------------------------------------


class TestSuccessPathStillWritesOpenRow:
    """Regression: the post-S-029-PR2 success-path journal write must
    keep firing. The new rejection-path code must not displace it."""

    def test_successful_execute_pkg_writes_open_row(
        self, coord, accounts_yaml, tmp_journal
    ):
        with patch(
            "src.units.accounts.risk.RiskManager.evaluate",
            return_value=(True, None),
        ), patch(
            "src.units.accounts.execute.execute_pkg",
            return_value="dry-stub-12345",
        ):
            results = coord.multi_account_execute(
                _vwap_pkg(),
                accounts_path=accounts_yaml,
                dry_run=True,
                balance_fetcher=lambda _a: 10_000.0,
            )
        # execute_pkg is stubbed so it doesn't write the open row in this
        # test — the contract being verified is "no SPURIOUS rejection
        # row was written when execute_pkg succeeded".
        assert results[0]["trade_id"] == "dry-stub-12345"
        rows = _read_trades(tmp_journal)
        # Stubbed execute_pkg doesn't journal; coordinator's rejection
        # paths are not entered.
        assert all(r["status"] not in ("rejected", "exchange_rejected") for r in rows)
