"""BL-20260619-ETHMIN — sub-venue-min qty is a clean per-trade risk refusal.

Reproduces the bybit_2 ETHUSDT churn (6 rejections on 2026-06-19): a
~$100 real-money balance at ~0.26% per-trade risk sizes ~0.005-0.007 ETH,
which is below Bybit's ETHUSDT ``minOrderQty`` of 0.01. Pre-fix that
sub-min qty reached the ``_submit_order`` pre-flight and was journaled as
``status='exchange_rejected'`` on every recurring signal — noisy churn
that looks like a broker/exec failure.

The fix refuses the order at the sizing/risk layer in the coordinator with
a clean ``status='rejected'`` row and a distinct ``below_venue_min_qty``
cause, NOT an ``exchange_rejected`` fill row. The qty is never floored UP
(that would silently exceed the configured risk). The account stays live
(Prime Directive) — only this one trade is refused.

Exercised end-to-end: ``Coordinator.multi_account_execute`` routes a sub-min
sized qty through the clean-refusal path with the ``below_venue_min_qty`` token.

The per-symbol venue-minimum RESOLUTION now lives in the one seam,
``src/units/accounts/qty_legalize.py`` (``legalize_qty`` / ``instrument_lot``);
the sizing-layer ``execute.venue_min_qty_for`` copy was retired in Phase 4 of the
qty-legalization consolidation (``docs/sizing-legalization-DESIGN.md``). Direct
resolver-matrix coverage now lives in ``tests/test_qty_legalize.py``; this file
keeps the live-path integration regression that proves the seam is wired into the
coordinator's clean refusal.
"""
from __future__ import annotations

import sqlite3
import textwrap
from unittest.mock import patch

import pytest

from src.core.coordinator import Coordinator, OrderPackage
from src.units.accounts import precision


@pytest.fixture(autouse=True)
def _clean_caches():
    precision._LOT_CACHE.clear()
    precision._LIVE_CACHE.clear()
    yield
    precision._LOT_CACHE.clear()
    precision._LIVE_CACHE.clear()


# --- Coordinator integration: live path routes sub-min through clean refusal ---

_ACCOUNTS_YAML = textwrap.dedent("""\
    accounts:
      bybit_2:
        type: regular
        exchange: bybit
        market_type: linear
        account_class: real_money
        api_key_env: BYBIT_KEY_2
        strategies: [eth_pullback_2h]
        risk:
          max_dd_pct: 0.05
          daily_usd: 100
          pos_size: 500
          risk_pct: 0.01
          min_balance_usd: 50
""")


def _eth_pkg() -> OrderPackage:
    return OrderPackage(
        strategy="eth_pullback_2h",
        symbol="ETHUSDT",
        direction="short",
        entry=1700.0,
        sl=1740.0,
        tp=1620.0,
        confidence=0.42,
        meta={"strategy_name": "eth_pullback_2h", "entry_reason": "pullback signal"},
    )


@pytest.fixture(autouse=True)
def _stub_account_creds(monkeypatch):
    monkeypatch.setenv("BYBIT_KEY_2", "test-value")
    monkeypatch.setenv("BYBIT_KEY_2_SECRET", "test-secret")


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


def _rejected_rows(db_path):
    conn = sqlite3.connect(str(db_path))
    try:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute(
            "SELECT account_id, strategy_name, status, entry_reason "
            "FROM trades"
        ).fetchall()]
    finally:
        conn.close()


class TestCoordinatorVenueMinRefusal:
    def test_sub_min_eth_qty_clean_refusal_not_exchange_rejected(
        self, coord, accounts_yaml, tmp_journal,
    ):
        # Live (non-dry) path. Force the sizer to return a sub-venue-min qty
        # (0.006 ETH < 0.01 minOrderQty) and stub client construction so the
        # account is treated as live without a real exchange socket. The new
        # venue-min guard must refuse it BEFORE _submit_order is reached.
        with patch(
            "src.units.accounts.risk.RiskManager.position_size",
            return_value=0.006,
        ), patch(
            # Pin the account live regardless of the repo strategies.yaml
            # execution mode — this test asserts the LIVE-path refusal.
            "src.strategy_registry.execution_mode",
            return_value="live",
        ), patch(
            "src.units.accounts.clients.bybit_client_for",
            return_value=object(),
        ), patch(
            # Defensive: if the guard regressed and the order reached
            # dispatch, this would record an exchange_rejected row.
            "src.units.accounts.execute.execute_pkg",
            side_effect=AssertionError("execute_pkg must not be reached"),
        ):
            coord.multi_account_execute(
                _eth_pkg(),
                accounts_path=accounts_yaml,
                balance_fetcher=lambda _a: 100.0,
            )

        rows = _rejected_rows(tmp_journal)
        # Exactly one trade row, a clean 'rejected' (NOT 'exchange_rejected').
        assert len(rows) == 1
        row = rows[0]
        assert row["account_id"] == "bybit_2"
        assert row["status"] == "rejected"
        assert row["status"] != "exchange_rejected"
        assert "below_venue_min_qty" in (row["entry_reason"] or "")
