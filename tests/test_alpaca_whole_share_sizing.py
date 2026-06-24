"""Alpaca whole-share sizing (BL-20260622-ALPACA-FRACTIONAL-SIZE).

Alpaca bracket orders — the only class the executor sends for alpaca — reject
fractional share quantities (``AlpacaClient.place`` floors to
``max(1, int(round(qty)))``). The risk sizer must therefore produce WHOLE
shares for alpaca, the equity analogue of the ``market_type: futures``
whole-contract rule. Before this fix the crypto default ``qty_precision=3``
produced e.g. 9.079 shares (trade #2771), the broker floored it to 9, and the
journal recorded a qty that was never placed.

The per-exchange constraint is expressed as the ``whole_units`` flag threaded
into ``RiskManager.position_size`` (resolved from the exchange via
``requires_whole_unit_qty`` at the call sites, since the RiskManager is built
from only the ``risk`` sub-block and never sees the exchange).

Pairs with ``test_ib_sizing_and_data.py`` (the futures whole-contract path).
"""
from __future__ import annotations

import pytest

from src.core.coordinator import OrderPackage
from src.units.accounts.risk import (
    RiskManager,
    requires_whole_unit_qty,
    size_order_from_cfg,
)


def _pkg(symbol="IWM", entry=298.50, sl=282.02, tp=328.05):
    return OrderPackage(
        strategy="iwm_trend_long_1d",
        symbol=symbol,
        direction="long",
        entry=entry,
        sl=sl,
        tp=tp,
        meta={"strategy_name": "iwm_trend_long_1d", "strategy_risk_pct": 1.0},
    )


# Crypto-default risk config, exactly like the live alpaca_paper risk block
# (no min_qty / qty_precision → falls back to 3dp / 0.001 lot).
_ALPACA_LIKE = {"risk_pct": 0.01, "daily_usd": 100_000, "min_balance_usd": 50}


# ---------------------------------------------------------------------------
# requires_whole_unit_qty capability resolver
# ---------------------------------------------------------------------------


class TestRequiresWholeUnitQty:
    def test_alpaca_true(self):
        assert requires_whole_unit_qty("alpaca") is True
        assert requires_whole_unit_qty("ALPACA") is True  # case-insensitive

    @pytest.mark.parametrize("ex", ["bybit", "interactive_brokers", "oanda", "", None, "kraken"])
    def test_others_false(self, ex):
        assert requires_whole_unit_qty(ex) is False


# ---------------------------------------------------------------------------
# position_size(whole_units=True) — integer shares
# ---------------------------------------------------------------------------


class TestWholeShareSizing:
    def test_whole_units_forces_integer_shares(self):
        """Regression for trade #2771: same config shape as live alpaca_paper.
        Whatever the balance, an alpaca-sized qty is a whole number — never
        9.079. (balance 100k, risk 1% = $1000, risk/share = 16.48 → 60.67 →
        floored to 60.)"""
        rm = RiskManager(dict(_ALPACA_LIKE))
        for balance in (5_000, 25_000, 100_000, 1_000_000):
            qty = rm.position_size(_pkg(), balance, whole_units=True)
            assert qty == int(qty), f"fractional alpaca qty {qty} at balance={balance}"

    def test_whole_units_floors_not_rounds(self):
        """60.67 floors to 60, not rounds to 61 (never exceed the risk cap)."""
        rm = RiskManager(dict(_ALPACA_LIKE))
        assert rm.position_size(_pkg(), 100_000, whole_units=True) == pytest.approx(60.0)

    def test_sub_one_share_is_refused_not_bumped(self):
        """A computed size below 1 share returns 0.0 (per-trade refusal) — not
        bumped to min_qty nor to a whole share (which would exceed the cap).
        balance 1k, risk 1% = $10, risk/share 16.48 → 0.6 → floor 0 → refusal."""
        rm = RiskManager(dict(_ALPACA_LIKE))
        assert rm.position_size(_pkg(), 1_000, whole_units=True) == 0.0

    def test_explicit_fractional_precision_is_overridden(self):
        """Even an explicit (mis)configured qty_precision=3 cannot produce
        fractional shares when whole_units is set."""
        rm = RiskManager(dict(_ALPACA_LIKE, qty_precision=3, min_qty=0.001))
        qty = rm.position_size(_pkg(), 100_000, whole_units=True)
        assert qty == int(qty)
        assert qty == pytest.approx(60.0)

    def test_whole_units_false_keeps_fractional(self):
        """Default (whole_units=False) preserves the existing fractional crypto
        behaviour — this fix is opt-in per exchange and changes nothing else."""
        rm = RiskManager(dict(_ALPACA_LIKE))
        qty = rm.position_size(_pkg(), 100_000)
        assert qty != int(qty)  # 60.679 — fractional, unchanged
        assert qty == pytest.approx(60.679, abs=1e-3)


# ---------------------------------------------------------------------------
# size_order_from_cfg — resolves whole_units from the account's exchange
# ---------------------------------------------------------------------------


class TestSizeOrderFromCfgWiring:
    def test_alpaca_cfg_sizes_whole_shares(self):
        cfg = dict(_ALPACA_LIKE, exchange="alpaca")
        qty = size_order_from_cfg(_pkg(), cfg, 100_000)
        assert qty == int(qty) and qty == pytest.approx(60.0)

    def test_non_alpaca_cfg_keeps_fractional(self):
        """A bybit account (or any non-whole-unit exchange) is untouched."""
        cfg = dict(_ALPACA_LIKE, exchange="bybit")
        qty = size_order_from_cfg(_pkg(), cfg, 100_000)
        assert qty != int(qty) and qty == pytest.approx(60.679, abs=1e-3)


# ---------------------------------------------------------------------------
# Margin pre-flight cap must also honour the whole-unit granularity
# (the cap branch previously floored with self.qty_precision/self.min_qty,
#  re-opening the fractional-bracket hole on the margin-capped path).
# ---------------------------------------------------------------------------


class TestMarginCapWholeUnits:
    """When the margin pre-flight cap BINDS on a whole-unit (alpaca) account,
    the capped qty must be floored to a WHOLE share and refused below 1 — not
    shaved to a fractional share by the crypto-default qty_precision.

    Regression for the gap left by BL-20260622-ALPACA-FRACTIONAL-SIZE: the
    risk-based path was whole-share-safe but the margin-cap branch still used
    self.qty_precision (3dp) / self.min_qty (0.001).
    """

    def _pkg_iwm(self):
        # IWM $230, $4 stop; risk-based qty on $5k @ effective 0.3% ≈ 3.75 → 3
        return _pkg(symbol="IWM", entry=230.0, sl=226.0, tp=238.0)

    def test_margin_cap_floors_to_whole_share(self):
        rm = RiskManager(dict(_ALPACA_LIKE))  # leverage unset → effective 1x
        pkg = OrderPackage(
            strategy="iwm_trend_long_1d", symbol="IWM", direction="long",
            entry=230.0, sl=226.0, tp=238.0,
            meta={"strategy_name": "iwm_trend_long_1d", "strategy_risk_pct": 0.3},
        )
        # buying_power=560 → cap 560/230 = 2.43; must floor to 2.0, never 2.43
        qty = rm.position_size(pkg, 5000.0, market_type="spot",
                               available_usd=560.0, whole_units=True)
        assert qty == 2.0

    def test_margin_cap_below_one_share_refuses(self):
        rm = RiskManager(dict(_ALPACA_LIKE))
        pkg = OrderPackage(
            strategy="iwm_trend_long_1d", symbol="IWM", direction="long",
            entry=230.0, sl=226.0, tp=238.0,
            meta={"strategy_name": "iwm_trend_long_1d", "strategy_risk_pct": 0.3},
        )
        # buying_power=120 → 120/230 = 0.52 < 1 share → per-trade refusal (0.0)
        qty = rm.position_size(pkg, 5000.0, market_type="spot",
                               available_usd=120.0, whole_units=True)
        assert qty == 0.0

    def test_ample_buying_power_lets_risk_size_govern(self):
        rm = RiskManager(dict(_ALPACA_LIKE))
        pkg = OrderPackage(
            strategy="iwm_trend_long_1d", symbol="IWM", direction="long",
            entry=230.0, sl=226.0, tp=238.0,
            meta={"strategy_name": "iwm_trend_long_1d", "strategy_risk_pct": 0.3},
        )
        # buying_power=10000 → cap 43 shares; risk-based 3.75 → 3 governs
        qty = rm.position_size(pkg, 5000.0, market_type="spot",
                               available_usd=10000.0, whole_units=True)
        assert qty == 3.0
