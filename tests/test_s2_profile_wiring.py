"""S2 tests — profile wiring: account_profile.py schema fix + profile_loader.py.

Tests verify:
1. AccountProfile.from_dict() correctly maps accounts.yaml schema (mode: live/dry_run,
   demo: true) — schema was updated in S2 to match actual accounts.yaml fields.
2. profile_loader.load_account_profiles() reads a real-schema YAML correctly.
3. profile_loader.load_instrument_profiles() reads instruments.yaml correctly.
4. coordinator.account_profiles and coordinator.instrument_profiles properties return
   the right types (integration smoke, no live calls).
"""
from __future__ import annotations

import os
import tempfile
import textwrap

from src.core.account_profile import AccountProfile
from src.core.profile_loader import load_account_profiles, load_instrument_profiles


# ---------------------------------------------------------------------------
# AccountProfile.from_dict — schema-correct mapping (S2 fix)
# ---------------------------------------------------------------------------

class TestAccountProfileSchemaFix:
    """Verify from_dict() handles the actual accounts.yaml schema fields."""

    def test_bybit_1_demo_mode_live(self):
        data = {"exchange": "bybit", "demo": True, "mode": "live"}
        p = AccountProfile.from_dict("bybit_1", data)
        assert p.demo is True
        assert p.dry_run is False
        assert p.is_live is True
        assert p.account_type == "bybit_demo"

    def test_bybit_2_live_no_demo(self):
        data = {"exchange": "bybit", "mode": "live"}
        p = AccountProfile.from_dict("bybit_2", data)
        assert p.demo is False
        assert p.dry_run is False
        assert p.is_live is True
        assert p.account_type == "bybit_live"

    def test_prop_velotrade_dry_run(self):
        data = {"exchange": "velotrade", "mode": "dry_run"}
        p = AccountProfile.from_dict("prop_velotrade_1", data)
        assert p.dry_run is True
        assert p.is_live is False
        assert p.exchange == "unknown"
        assert p.account_type == "unknown"

    def test_missing_mode_defaults_to_dry_run(self):
        data = {"exchange": "bybit"}
        p = AccountProfile.from_dict("test_acct", data)
        assert p.dry_run is True


# ---------------------------------------------------------------------------
# load_account_profiles
# ---------------------------------------------------------------------------

ACCOUNTS_YAML = textwrap.dedent("""\
    accounts:
      bybit_1:
        exchange: bybit
        demo: true
        mode: live
        market_type: linear
        strategies: [turtle_soup, vwap, ict_scalp_5m]
        risk:
          risk_pct: 0.01
      bybit_2:
        exchange: bybit
        mode: live
        market_type: linear
        strategies: [vwap]
        risk:
          risk_pct: 0.01
      prop_velotrade_1:
        exchange: velotrade
        mode: dry_run
        strategies: []
""")


class TestLoadAccountProfiles:
    def _write_yaml(self, content: str) -> str:
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
        f.write(content)
        f.close()
        return f.name

    def test_loads_three_accounts(self):
        path = self._write_yaml(ACCOUNTS_YAML)
        try:
            profiles = load_account_profiles(path)
            assert set(profiles.keys()) == {"bybit_1", "bybit_2", "prop_velotrade_1"}
        finally:
            os.unlink(path)

    def test_bybit_1_is_demo(self):
        path = self._write_yaml(ACCOUNTS_YAML)
        try:
            profiles = load_account_profiles(path)
            p = profiles["bybit_1"]
            assert p.demo is True
            assert p.is_live is True
            assert p.account_type == "bybit_demo"
        finally:
            os.unlink(path)

    def test_bybit_2_is_live(self):
        path = self._write_yaml(ACCOUNTS_YAML)
        try:
            profiles = load_account_profiles(path)
            p = profiles["bybit_2"]
            assert p.demo is False
            assert p.is_live is True
            assert p.account_type == "bybit_live"
        finally:
            os.unlink(path)

    def test_missing_file_returns_empty(self):
        profiles = load_account_profiles("/nonexistent/path/accounts.yaml")
        assert profiles == {}


# ---------------------------------------------------------------------------
# load_instrument_profiles
# ---------------------------------------------------------------------------

INSTRUMENTS_YAML = textwrap.dedent("""\
    instruments:
      BTCUSDT:
        exchange: bybit
        category: linear
        base_asset: BTC
        quote_currency: USDT
        settlement_currency: USDT
        tick_size: 0.1
        min_qty: 0.001
        qty_step: 0.001
        contract_value_usd: 1.0
        max_leverage: 100
        display_name: "BTC/USDT Perp (Bybit)"
      MES:
        exchange: interactive_brokers
        category: futures
        base_asset: ES
        quote_currency: USD
        settlement_currency: USD
        tick_size: 0.25
        min_qty: 1.0
        qty_step: 1.0
        contract_value_usd: 5.0
        max_leverage: 0
        display_name: "Micro E-mini S&P 500 (CME/IB)"
""")


class TestLoadInstrumentProfiles:
    def _write_yaml(self, content: str) -> str:
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
        f.write(content)
        f.close()
        return f.name

    def test_loads_two_instruments(self):
        path = self._write_yaml(INSTRUMENTS_YAML)
        try:
            profiles = load_instrument_profiles(path)
            assert set(profiles.keys()) == {"BTCUSDT", "MES"}
        finally:
            os.unlink(path)

    def test_btcusdt_spec(self):
        path = self._write_yaml(INSTRUMENTS_YAML)
        try:
            p = load_instrument_profiles(path)["BTCUSDT"]
            assert p.exchange == "bybit"
            assert p.category == "linear"
            assert p.tick_size == 0.1
            assert p.min_qty == 0.001
            assert p.contract_value_usd == 1.0
            assert p.max_leverage == 100
        finally:
            os.unlink(path)

    def test_mes_spec(self):
        path = self._write_yaml(INSTRUMENTS_YAML)
        try:
            p = load_instrument_profiles(path)["MES"]
            assert p.exchange == "interactive_brokers"
            assert p.category == "futures"
            assert p.contract_value_usd == 5.0
            assert p.tick_size == 0.25
            assert p.is_futures is True
        finally:
            os.unlink(path)

    def test_missing_file_returns_btcusdt_fallback(self):
        profiles = load_instrument_profiles("/nonexistent/instruments.yaml")
        assert "BTCUSDT" in profiles
        assert profiles["BTCUSDT"].exchange == "bybit"


# ---------------------------------------------------------------------------
# coordinator.account_profiles and coordinator.instrument_profiles smoke test
# ---------------------------------------------------------------------------

class TestCoordinatorProfileProperties:
    """Smoke test: coordinator properties return correct types."""

    def _write_yaml(self, content: str) -> str:
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
        f.write(content)
        f.close()
        return f.name

    def test_coordinator_account_profiles_type(self):
        from src.core.coordinator import Coordinator
        from src.core.account_profile import AccountProfile

        accts_path = self._write_yaml(ACCOUNTS_YAML)
        try:
            coord = Coordinator(accounts_path=accts_path)
            profiles = coord.account_profiles
            assert isinstance(profiles, dict)
            assert all(isinstance(v, AccountProfile) for v in profiles.values())
        finally:
            os.unlink(accts_path)

    def test_coordinator_instrument_profiles_fallback(self):
        from src.core.coordinator import Coordinator
        from src.core.instrument_profile import InstrumentProfile

        coord = Coordinator(instruments_path="/nonexistent/instruments.yaml")
        profiles = coord.instrument_profiles
        assert "BTCUSDT" in profiles
        assert isinstance(profiles["BTCUSDT"], InstrumentProfile)

    def test_coordinator_instrument_profiles_from_yaml(self):
        from src.core.coordinator import Coordinator
        from src.core.instrument_profile import InstrumentProfile

        inst_path = self._write_yaml(INSTRUMENTS_YAML)
        try:
            coord = Coordinator(instruments_path=inst_path)
            profiles = coord.instrument_profiles
            assert isinstance(profiles, dict)
            assert all(isinstance(v, InstrumentProfile) for v in profiles.values())
            assert "MES" in profiles
        finally:
            os.unlink(inst_path)
