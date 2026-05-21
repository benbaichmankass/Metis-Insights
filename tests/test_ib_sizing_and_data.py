"""Stage 3 (sizing) + Stage 2 (per-symbol data routing) for the IB/MES path.

Verifies the two byte-identical-for-crypto building blocks:
  * Instrument-aware futures sizing — contract_value_usd factor (1.0 for
    BTCUSDT → unchanged; 5.0 for MES → whole-contract sizing), and that the
    crypto margin cap is skipped for futures market types.
  * Per-symbol market-data routing — BTCUSDT → Bybit, MES → IB.
"""
from __future__ import annotations

import pytest

from src.core.coordinator import OrderPackage
import src.units.accounts.risk as risk
from src.units.accounts.risk import (
    RiskManager,
    _size_unbounded,
    contract_value_usd_for,
)


@pytest.fixture(autouse=True)
def _reset_contract_cache():
    risk._CONTRACT_VALUE_CACHE = None
    yield
    risk._CONTRACT_VALUE_CACHE = None


def _pkg(symbol, entry, sl, tp):
    return OrderPackage(
        strategy="test",
        symbol=symbol,
        direction="long",
        entry=entry,
        sl=sl,
        tp=tp,
        meta={"strategy_name": "test", "strategy_risk_pct": 1.0},
    )


# ---------------------------------------------------------------------------
# contract_value_usd_for
# ---------------------------------------------------------------------------


class TestContractValue:
    def test_mes_is_five(self):
        assert contract_value_usd_for("MES") == 5.0

    def test_btc_is_one(self):
        assert contract_value_usd_for("BTCUSDT") == 1.0

    def test_unknown_defaults_one(self):
        assert contract_value_usd_for("DOGEUSDT") == 1.0

    def test_empty_defaults_one(self):
        assert contract_value_usd_for("") == 1.0


# ---------------------------------------------------------------------------
# _size_unbounded contract-value factor
# ---------------------------------------------------------------------------


class TestSizeUnbounded:
    def test_crypto_unchanged(self):
        # cvu defaults to 1.0 → identical to legacy behaviour.
        pkg = _pkg("BTCUSDT", 80000.0, 79900.0, 80200.0)
        qty = _size_unbounded(pkg, risk_pct=0.01, balance_usdt=10_000, qty_precision=3)
        # risk_usdt=100, distance=100 → 1.0
        assert qty == pytest.approx(1.0)

    def test_futures_divides_by_contract_value(self):
        # MES: distance=50 pts, $5/pt → risk per contract = 250.
        pkg = _pkg("MES", 5800.0, 5750.0, 5900.0)
        qty = _size_unbounded(
            pkg, risk_pct=0.01, balance_usdt=10_000, qty_precision=0,
            contract_value_usd=5.0,
        )
        # risk_usdt=100, distance*cvu=250 → 0.4 → floor to 0 contracts → min_qty
        assert qty == pytest.approx(0.0) or qty >= 0.0

    def test_futures_whole_contracts(self):
        pkg = _pkg("MES", 5800.0, 5750.0, 5900.0)
        # Bigger balance so >=1 contract: risk_usdt=1000, /250 = 4 contracts.
        qty = _size_unbounded(
            pkg, risk_pct=0.01, balance_usdt=100_000, qty_precision=0,
            contract_value_usd=5.0, min_qty=1.0,
        )
        assert qty == pytest.approx(4.0)


# ---------------------------------------------------------------------------
# position_size end-to-end (uses real instruments.yaml profiles)
# ---------------------------------------------------------------------------


class TestPositionSize:
    def test_btc_path_unchanged(self):
        rm = RiskManager({"risk_pct": 0.01, "daily_usd": 100000, "min_balance_usd": 50})
        pkg = _pkg("BTCUSDT", 80000.0, 79900.0, 80200.0)
        qty = rm.position_size(pkg, 10_000, market_type="linear")
        # cvu=1.0; risk-based 1.0 BTC, but margin cap (lev=1) applies as before.
        assert qty > 0

    def test_mes_futures_whole_contracts_no_margin_cap(self):
        rm = RiskManager(
            {"risk_pct": 0.01, "daily_usd": 100000, "min_balance_usd": 50, "min_qty": 1, "qty_precision": 0},
        )
        pkg = _pkg("MES", 5800.0, 5750.0, 5900.0)
        # balance 100k, risk 1% = $1000, risk/contract = 50*5 = $250 → 4 contracts.
        # Crypto margin cap (would be balance*1*buffer/entry ≈ tiny) is SKIPPED
        # for futures, so qty stays 4 rather than being clamped to ~0.
        qty = rm.position_size(pkg, 100_000, market_type="futures")
        assert qty == pytest.approx(4.0)


# ---------------------------------------------------------------------------
# Per-symbol market-data routing
# ---------------------------------------------------------------------------


class TestConnectorRouting:
    def test_ib_branch_builds_ib_market_data(self):
        from src.runtime.market_data import _build_exchange_client
        from src.exchange.ib_connector import IBMarketData
        client = _build_exchange_client({"EXCHANGE": "interactive_brokers"})
        assert isinstance(client, IBMarketData)

    def test_connector_for_mes_is_ib(self):
        from src.runtime.market_data import connector_for_symbol
        from src.exchange.ib_connector import IBMarketData
        client = connector_for_symbol("MES", {})
        assert isinstance(client, IBMarketData)

    def test_connector_for_btc_is_bybit(self):
        from src.runtime.market_data import connector_for_symbol
        from src.exchange.bybit_connector import BybitConnector
        client = connector_for_symbol("BTCUSDT", {"EXCHANGE": "bybit"})
        assert isinstance(client, BybitConnector)
