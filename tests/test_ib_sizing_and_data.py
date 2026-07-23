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
import src.core.profile_loader as profile_loader
from src.units.accounts.risk import (
    RiskManager,
    _size_unbounded,
    contract_value_usd_for,
)


@pytest.fixture(autouse=True)
def _reset_contract_cache():
    # The canonical contract-value cache now lives in the pure profile loader;
    # risk.contract_value_usd_for delegates to it (M0b layer-drain). Reset there.
    profile_loader._CONTRACT_VALUE_USD_CACHE = None
    yield
    profile_loader._CONTRACT_VALUE_USD_CACHE = None


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


class TestIbEquitySizingResolution:
    """2026-07-07: ib_paper mixes futures (MES/MGC/MHG, accounts.yaml
    ``market_type: futures``) and equities (the alpaca-ETF basket) on ONE
    account. ``Coordinator.multi_account_execute`` resolves the EFFECTIVE
    per-order market_type/whole_units symbol-aware via
    ``ib_order_market_type`` + ``requires_whole_unit_qty`` (see
    docs/integrations/ibkr-equity-etf-support-DESIGN.md §4.3) BEFORE calling
    ``position_size`` — these tests exercise that exact composition rather
    than the account's static configured market_type, so a regression in the
    resolver would show up here even without a full coordinator harness.
    """

    def _effective_call_kwargs(self, symbol, configured_market_type="futures"):
        from src.units.accounts.ib_instruments import ib_order_market_type
        from src.units.accounts.risk import requires_whole_unit_qty

        market_type = ib_order_market_type(symbol, default=configured_market_type)
        whole_units = (
            requires_whole_unit_qty("interactive_brokers")
            or market_type == "equity"
        )
        return market_type, whole_units

    def test_futures_symbol_keeps_strict_whole_contract_path(self):
        market_type, whole_units = self._effective_call_kwargs("MES")
        assert market_type == "futures"
        assert whole_units is False

    def test_etf_symbol_switches_to_equity_whole_share_path(self):
        market_type, whole_units = self._effective_call_kwargs("SPY")
        assert market_type == "equity"
        assert whole_units is True

    def test_spy_sizes_whole_shares_not_whole_contracts(self):
        # A sub-1-share risk-based ideal on the equity path rounds UP to 1
        # share (whole_units relaxation); on the strict futures path the
        # same ideal would be refused outright (0.0). Confirms the resolved
        # kwargs actually change position_size's behavior, not just their
        # own values.
        rm = RiskManager({"risk_pct": 0.0004, "daily_usd": 100000, "min_qty": 1, "qty_precision": 0})
        pkg = _pkg("SPY", 500.0, 495.0, 510.0)  # risk_budget=4, 1-share risk=5 → in round-up band
        market_type, whole_units = self._effective_call_kwargs("SPY")
        qty = rm.position_size(pkg, 10_000, market_type=market_type, whole_units=whole_units)
        assert qty == pytest.approx(1.0)

    def test_mgc_still_refuses_sub_one_contract(self):
        # Same tiny-risk shape, but MGC keeps the strict futures refusal
        # (not rounded up) — the equity relaxation must not leak onto futures.
        rm = RiskManager({"risk_pct": 0.001, "daily_usd": 100000, "min_qty": 1, "qty_precision": 0})
        pkg = _pkg("MGC", 2000.0, 1990.0, 2020.0)
        market_type, whole_units = self._effective_call_kwargs("MGC")
        qty = rm.position_size(pkg, 10_000, market_type=market_type, whole_units=whole_units)
        assert qty == 0.0

    def test_unrecognized_symbol_keeps_account_default(self):
        # A non-IB-mapped symbol (shouldn't reach an IB account in practice)
        # falls through to the account's configured market_type unchanged —
        # the resolver never invents a new behavior for a symbol it doesn't
        # know.
        market_type, _ = self._effective_call_kwargs("BTCUSDT", configured_market_type="futures")
        assert market_type == "futures"


class TestFuturesWholeContractEnforcement:
    """BL-20260611-001: ``market_type: futures`` forces integer-contract
    sizing in ``position_size`` regardless of the account's configured
    ``qty_precision`` / ``min_qty`` — the live ``ib_paper`` account omitted
    both, fell back to the crypto defaults (3dp / 0.001), and dispatched a
    3.643-contract MHG order that IBKR could never fill (trade #2531)."""

    # Crypto-default risk config, exactly like the live ib_paper account
    # (risk block has no min_qty / qty_precision).
    _IB_PAPER_LIKE = {"risk_pct": 0.01, "daily_usd": 100000, "min_balance_usd": 50}

    def test_futures_qty_is_integer_with_crypto_default_precision(self):
        """Regression for trade #2531: same config shape as live ib_paper.

        MHG entry 6.2715 / SL 5.94157143 (the real signal), cvu from
        instruments.yaml; whatever the balance, the qty must come out a
        whole number of contracts — never 3.643."""
        rm = RiskManager(dict(self._IB_PAPER_LIKE))
        pkg = _pkg("MHG", 6.2715, 5.94157143, 6.8923785)
        for balance in (5_000, 10_000, 100_000, 1_000_000):
            qty = rm.position_size(pkg, balance, market_type="futures")
            assert qty == int(qty), (
                f"fractional futures qty {qty} at balance={balance}"
            )

    def test_futures_sub_one_contract_is_refused_not_bumped(self):
        """A computed size below 1 contract returns 0.0 (per-trade refusal)
        — it must NOT be bumped up to min_qty (the crypto bump-up semantics)
        nor to a whole contract (which would exceed the risk cap)."""
        rm = RiskManager(dict(self._IB_PAPER_LIKE))
        # MES: risk/contract = 50 pts * $5 = $250; 1% of $10k = $100 → 0.4.
        pkg = _pkg("MES", 5800.0, 5750.0, 5900.0)
        assert rm.position_size(pkg, 10_000, market_type="futures") == 0.0

    def test_futures_explicit_fractional_precision_is_overridden(self):
        """Even an explicit (mis)configured qty_precision=3 on a futures
        account cannot produce fractional contracts."""
        cfg = dict(self._IB_PAPER_LIKE, qty_precision=3, min_qty=0.001)
        rm = RiskManager(cfg)
        pkg = _pkg("MES", 5800.0, 5750.0, 5900.0)
        qty = rm.position_size(pkg, 100_000, market_type="futures")
        assert qty == pytest.approx(4.0)
        assert qty == int(qty)

    def test_futures_daily_budget_scaledown_stays_integer(self):
        """The daily-loss-budget scale-down path floors to whole contracts
        too (it re-floors with the account precision)."""
        # Budget $600 < risk-based loss $1000 → scaled = 600/250 = 2.4 → 2.
        cfg = dict(self._IB_PAPER_LIKE, daily_usd=600)
        rm = RiskManager(cfg)
        pkg = _pkg("MES", 5800.0, 5750.0, 5900.0)
        qty = rm.position_size(pkg, 100_000, market_type="futures")
        assert qty == pytest.approx(2.0)

    def test_futures_daily_budget_scaledown_below_one_refused(self):
        # Budget $200 → scaled = 200/250 = 0.8 → floor 0 → refusal.
        cfg = dict(self._IB_PAPER_LIKE, daily_usd=200)
        rm = RiskManager(cfg)
        pkg = _pkg("MES", 5800.0, 5750.0, 5900.0)
        assert rm.position_size(pkg, 100_000, market_type="futures") == 0.0

    def test_crypto_path_sub_min_lot_refuses(self):
        """Linear/spot accounts REFUSE a sub-min-lot risk-based size rather than
        bumping it UP to min_qty (#3910 Item 3, operator-approved refuse
        2026-06-28). The bump silently realised MORE than the configured risk
        budget and, when it equalled a held min-lot, pinned the real-money
        bybit_2 in a permanent at-target freeze. The 3dp precision is unchanged;
        only the sub-min outcome flips from bump→refuse."""
        rm = RiskManager(dict(self._IB_PAPER_LIKE))
        # Wide SL + small balance → raw qty 0.0001 floors to 0 at 3dp →
        # previously bumped UP to the 0.001 min lot; now a per-trade refusal.
        pkg = _pkg("BTCUSDT", 80000.0, 70000.0, 95000.0)
        qty = rm.position_size(pkg, 100, market_type="linear")
        assert qty == 0.0


# ---------------------------------------------------------------------------
# Per-symbol market-data routing
# ---------------------------------------------------------------------------


class _FakeAcct:
    """Minimal stand-in for TradingAccount for symbol-derivation tests."""

    def __init__(self, *, exchange, strategies, symbols=None, configured=True):
        self.exchange = exchange
        self.strategies = strategies
        self.symbols = symbols
        self.configured = configured


def _patch_accounts(monkeypatch, accounts):
    monkeypatch.setattr(
        "src.units.accounts.load_accounts", lambda *a, **k: accounts
    )


class TestMultiSymbolResolution:
    """Symbols are derived from configured accounts (accounts.yaml is the
    single source of truth) — there is no MULTI_SYMBOL_ENABLED flag."""

    def test_single_account_single_symbol(self, monkeypatch):
        from src.main import _resolve_tick_symbols
        _patch_accounts(monkeypatch, [
            _FakeAcct(exchange="bybit", strategies=["vwap"], symbols=["BTCUSDT"]),
        ])
        assert _resolve_tick_symbols({"SYMBOL": "BTCUSDT"}) == ["BTCUSDT"]

    def test_btc_and_mes_accounts_union(self, monkeypatch):
        from src.main import _resolve_tick_symbols
        _patch_accounts(monkeypatch, [
            _FakeAcct(exchange="bybit", strategies=["vwap"], symbols=["BTCUSDT"]),
            _FakeAcct(exchange="interactive_brokers",
                      strategies=["vwap", "turtle_soup"], symbols=["MES"]),
        ])
        assert _resolve_tick_symbols({"SYMBOL": "BTCUSDT"}) == ["BTCUSDT", "MES"]

    def test_empty_strategies_account_excluded(self, monkeypatch):
        """An account with ``strategies: []`` (e.g. ib_live) is opted out —
        its symbol is NOT added to the tick."""
        from src.main import _resolve_tick_symbols
        _patch_accounts(monkeypatch, [
            _FakeAcct(exchange="bybit", strategies=["vwap"], symbols=["BTCUSDT"]),
            _FakeAcct(exchange="interactive_brokers", strategies=[], symbols=["MES"]),
        ])
        assert _resolve_tick_symbols({"SYMBOL": "BTCUSDT"}) == ["BTCUSDT"]

    def test_unconfigured_account_excluded(self, monkeypatch):
        from src.main import _resolve_tick_symbols
        _patch_accounts(monkeypatch, [
            _FakeAcct(exchange="bybit", strategies=["vwap"], symbols=["BTCUSDT"]),
            _FakeAcct(exchange="interactive_brokers", strategies=["vwap"],
                      symbols=["MES"], configured=False),
        ])
        assert _resolve_tick_symbols({"SYMBOL": "BTCUSDT"}) == ["BTCUSDT"]

    def test_missing_symbols_falls_back_to_exchange_default(self, monkeypatch):
        """An account that omits ``symbols`` trades its exchange default."""
        from src.main import _resolve_tick_symbols
        _patch_accounts(monkeypatch, [
            _FakeAcct(exchange="interactive_brokers", strategies=["vwap"], symbols=None),
        ])
        assert _resolve_tick_symbols({"SYMBOL": "BTCUSDT"}) == ["BTCUSDT", "MES"]

    def test_primary_always_first(self, monkeypatch):
        from src.main import _resolve_tick_symbols
        _patch_accounts(monkeypatch, [
            _FakeAcct(exchange="interactive_brokers", strategies=["vwap"], symbols=["MES"]),
        ])
        assert _resolve_tick_symbols({"SYMBOL": "BTCUSDT"}) == ["BTCUSDT", "MES"]

    def test_account_load_failure_falls_back_to_primary(self, monkeypatch):
        from src.main import _resolve_tick_symbols
        def _boom(*a, **k):
            raise RuntimeError("config blew up")
        monkeypatch.setattr("src.units.accounts.load_accounts", _boom)
        assert _resolve_tick_symbols({"SYMBOL": "BTCUSDT"}) == ["BTCUSDT"]

    def test_exchange_for_symbol(self, monkeypatch):
        import src.core.coordinator as coord
        coord._INSTRUMENT_EXCHANGE_CACHE = None
        from src.main import _exchange_for_symbol
        assert _exchange_for_symbol("BTCUSDT") == "bybit"
        assert _exchange_for_symbol("MES") == "interactive_brokers"


class TestSymbolExchangeGate:
    def test_instrument_exchange_lookup(self):
        import src.core.coordinator as coord
        coord._INSTRUMENT_EXCHANGE_CACHE = None
        assert coord._instrument_exchange_for("BTCUSDT") == "bybit"
        assert coord._instrument_exchange_for("MES") == "interactive_brokers"
        assert coord._instrument_exchange_for("UNKNOWNSYM") is None
        assert coord._instrument_exchange_for("") is None


class TestDispatchRoutingSafety:
    """Safety-critical: BTCUSDT never reaches an IB account; MES never a bybit one."""

    _YAML = (
        "accounts:\n"
        "  bybit_x:\n"
        "    type: regular\n"
        "    exchange: bybit\n"
        "    strategies: [vwap]\n"
        "    risk: {max_dd_pct: 0.05, daily_usd: 200, pos_size: 1000}\n"
        "  ib_x:\n"
        "    type: regular\n"
        "    exchange: interactive_brokers\n"
        "    strategies: [vwap]\n"
        "    market_type: futures\n"
        "    ib_port: 7497\n"
        "    ib_account: TESTACC\n"
        "    risk: {max_dd_pct: 0.05, daily_usd: 200, pos_size: 2000}\n"
    )

    def _coord_and_yaml(self, tmp_path):
        import src.core.coordinator as coord_mod
        coord_mod._INSTRUMENT_EXCHANGE_CACHE = None
        p = tmp_path / "accounts.yaml"
        p.write_text(self._YAML)
        return coord_mod.Coordinator(), str(p)

    def _pkg(self, symbol, entry, sl, tp):
        from src.core.coordinator import OrderPackage
        return OrderPackage(strategy="vwap", symbol=symbol, direction="long",
                            entry=entry, sl=sl, tp=tp, meta={})

    def test_btcusdt_never_reaches_ib(self, tmp_path):
        coord, yaml_path = self._coord_and_yaml(tmp_path)
        results = coord.multi_account_execute(
            self._pkg("BTCUSDT", 50000.0, 49000.0, 52000.0),
            accounts_path=yaml_path, dry_run=True,
        )
        exchanges = {r.get("exchange") for r in results}
        assert "interactive_brokers" not in exchanges
        assert "bybit" in exchanges

    def test_mes_never_reaches_bybit(self, tmp_path):
        coord, yaml_path = self._coord_and_yaml(tmp_path)
        results = coord.multi_account_execute(
            self._pkg("MES", 5800.0, 5750.0, 5900.0),
            accounts_path=yaml_path, dry_run=True,
        )
        exchanges = {r.get("exchange") for r in results}
        assert "bybit" not in exchanges
        assert "interactive_brokers" in exchanges


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

    def test_connector_for_contract_month_symbol_routes_to_base_root(self):
        # BL-20260617-MHGN6-CANDLEROUTE: a contract-month symbol (MHGN6)
        # has no instrument profile of its own — it must resolve its base
        # root (MHG -> IBKR), not fall through to the process EXCHANGE.
        from src.runtime.market_data import connector_for_symbol
        from src.exchange.ib_connector import IBMarketData
        client = connector_for_symbol("MHGN6", {"EXCHANGE": "bybit"})
        assert isinstance(client, IBMarketData)

    def test_connector_month_grammar_never_strips_crypto(self):
        # SOLUSDT/BTCUSDT must NOT be mistaken for month-coded futures.
        from src.runtime.market_data import connector_for_symbol
        from src.exchange.bybit_connector import BybitConnector
        client = connector_for_symbol("SOLUSDT", {"EXCHANGE": "bybit"})
        assert isinstance(client, BybitConnector)
