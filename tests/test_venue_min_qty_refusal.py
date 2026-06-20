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

Two layers exercised:
  * ``venue_min_qty_for`` resolves the per-symbol venue minimum (Bybit-only).
  * ``Coordinator.multi_account_execute`` routes a sub-min sized qty through
    the clean-refusal path with the ``below_venue_min_qty`` token.
"""
from __future__ import annotations

import sqlite3
import textwrap
from unittest.mock import patch

import pytest

from src.core.coordinator import Coordinator, OrderPackage
from src.units.accounts import precision
from src.units.accounts.execute import venue_min_qty_for


@pytest.fixture(autouse=True)
def _clean_caches():
    precision._LOT_CACHE.clear()
    precision._LIVE_CACHE.clear()
    yield
    precision._LOT_CACHE.clear()
    precision._LIVE_CACHE.clear()


class TestVenueMinQtyFor:
    """The sizing-layer venue-minimum resolver."""

    def test_eth_linear_static_min(self):
        # No client → static-map fallback: ETHUSDT linear = (0.01, 0.01).
        cfg = {"account_id": "bybit_2", "exchange": "bybit", "market_type": "linear"}
        assert venue_min_qty_for(None, cfg, "ETHUSDT") == pytest.approx(0.01)

    def test_btc_linear_static_min(self):
        cfg = {"account_id": "bybit_1", "exchange": "bybit", "market_type": "linear"}
        assert venue_min_qty_for(None, cfg, "BTCUSDT") == pytest.approx(0.001)

    def test_unknown_symbol_returns_none(self):
        # No static entry + no client → rule unknown → None (no refusal).
        cfg = {"account_id": "bybit_2", "exchange": "bybit", "market_type": "linear"}
        assert venue_min_qty_for(None, cfg, "DOGEUSDT") is None

    def test_non_bybit_exchange_returns_none(self):
        cfg = {"account_id": "ib_paper", "exchange": "interactive_brokers"}
        assert venue_min_qty_for(None, cfg, "MES") is None

    def test_live_lookup_used_when_client_present(self):
        class _LotClient:
            def get_instruments_info(self, *, category, symbol):
                return {"result": {"list": [{
                    "lotSizeFilter": {"qtyStep": "0.02", "minOrderQty": "0.05"},
                }]}}

        cfg = {"account_id": "bybit_2", "exchange": "bybit", "market_type": "linear"}
        assert venue_min_qty_for(_LotClient(), cfg, "ETHUSDT") == pytest.approx(0.05)


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
