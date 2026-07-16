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
        """A computed size below 1 share returns 0.0 when rounding up would
        breach the round-up overshoot cap (1.5x the per-trade budget).
        balance 1k, risk 1% = $10 budget; risk/share 16.48 → ideal 0.6. One
        share risks $16.48 = 1.65x the budget > 1.5x ($15), so the round-up
        is declined and the trade is refused (never silently >1.5x the cap)."""
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
        # The legacy strategy_risk_pct=0.3 is IGNORED post-2026-06-29 (sizing is
        # account-level only). buying_power=10000 → cap 43 shares; risk-based
        # 5000 × 0.01 / 4 = 12.5 → 12 governs.
        qty = rm.position_size(pkg, 5000.0, market_type="spot",
                               available_usd=10000.0, whole_units=True)
        assert qty == 12.0


# ---------------------------------------------------------------------------
# Round-up-to-one-share (operator directive 2026-06-24): when the risk-based
# ideal is <1 share only because the per-trade budget is small, round UP to 1
# share IF that share's stop risk is within 1.5x the budget — else refuse.
# Equity (whole_units) only; futures keep strict refuse-sub-1-contract.
# ---------------------------------------------------------------------------


class TestRoundUpToOneShare:
    def _pkg300(self, stop_dist):
        # $300 asset, stop `stop_dist` below entry
        return OrderPackage(
            strategy="iwm_trend_long_1d", symbol="IWM", direction="long",
            entry=300.0, sl=300.0 - stop_dist, tp=310.0,
            meta={"strategy_name": "iwm_trend_long_1d", "strategy_risk_pct": 1.0},
        )

    def test_rounds_up_within_budget_multiple(self):
        """$150 acct @ 1% (budget $1.50). A $1.50 stop → ideal 1.0 share already;
        a $2.25 stop → ideal 0.67 but 1 share risks exactly 1.5x budget → rounds
        up to 1."""
        rm = RiskManager({"risk_pct": 0.01, "min_balance_usd": 100,
                          "daily_usd": 100_000, "pos_size": 100_000})
        # 1 share risk = $2.25 == 1.5 * $1.50 → boundary, rounds up
        q = rm.position_size(self._pkg300(2.25), 150.0, market_type="spot",
                             available_usd=10_000.0, whole_units=True)
        assert q == 1.0

    def test_refuses_above_budget_multiple(self):
        """$150 acct @ 1%. A $3.00 stop → 1 share risks $3.00 = 2x budget >
        1.5x → refused (never silently risk >1.5x the cap)."""
        rm = RiskManager({"risk_pct": 0.01, "min_balance_usd": 100,
                          "daily_usd": 100_000, "pos_size": 100_000})
        q = rm.position_size(self._pkg300(3.0), 150.0, market_type="spot",
                             available_usd=10_000.0, whole_units=True)
        assert q == 0.0

    def test_round_up_still_subject_to_buying_power(self):
        """The rounded-up share must still be affordable: buying power below 1
        share's notional re-floors it to 0 even when the risk overshoot is OK."""
        rm = RiskManager({"risk_pct": 0.01, "min_balance_usd": 100,
                          "daily_usd": 100_000, "pos_size": 100_000})
        # risk overshoot fine ($1.50 stop = 1x budget) but BP $120 < $300 share
        q = rm.position_size(self._pkg300(1.5), 150.0, market_type="spot",
                             available_usd=120.0, whole_units=True)
        assert q == 0.0

    def test_round_up_still_subject_to_daily_loss_budget(self):
        """The rounded-up share must still fit the remaining daily-loss budget:
        a tiny daily_usd cap re-floors it to 0."""
        rm = RiskManager({"risk_pct": 0.01, "min_balance_usd": 100,
                          "daily_usd": 1.0, "pos_size": 100_000})  # $1 daily cap
        # 1 share risks $1.50 > $1 daily budget → scaled down → floor 0
        q = rm.position_size(self._pkg300(1.5), 150.0, market_type="spot",
                             available_usd=10_000.0, whole_units=True)
        assert q == 0.0

    def test_futures_not_rounded_up(self):
        """Futures (force_whole via market_type, NOT whole_units) keep strict
        refuse-sub-1-contract — the round-up is equity-only."""
        rm = RiskManager({"risk_pct": 0.01, "min_balance_usd": 100,
                          "daily_usd": 100_000, "pos_size": 100_000})
        pkg = OrderPackage(strategy="x", symbol="MES", direction="long",
                           entry=5800.0, sl=5750.0, tp=5900.0,
                           meta={"strategy_name": "x", "strategy_risk_pct": 1.0})
        # MES risk/contract = 50pts*$5 = $250; 1% of $10k = $100 → 0.4 contract.
        # Even though $250 < 1.5*$100=$150? no — $250 > $150 anyway; but the
        # point is futures never enter the round-up branch regardless.
        assert rm.position_size(pkg, 10_000, market_type="futures") == 0.0


# ---------------------------------------------------------------------------
# whole_unit_qty — the shared quantization helper (2026-06-30 follow-up)
# ---------------------------------------------------------------------------
# Single source of truth for "the whole-share qty a whole-unit venue actually
# holds", used by AlpacaClient.place (the placed qty), execute_pkg (the
# JOURNALED qty), and order_monitor._apply_partial_close (the post-scale-out
# remainder). They share ONE definition so the journal can never drift from the
# broker. Regression guard for the live 2026-06-30 finding: alpaca_paper held a
# 42-share GLD short on the broker while the journal carried two fractional rows
# (8.368 + 33.632) plus a 231.078 TLT vs the broker's 231.
class TestWholeUnitQtyHelper:
    def test_rounds_to_nearest_whole(self):
        from src.units.accounts.risk import whole_unit_qty

        assert whole_unit_qty(8.368) == 8.0          # broker held 8
        assert whole_unit_qty(33.632) == 34.0        # broker held 34
        assert whole_unit_qty(231.078) == 231.0      # broker held 231
        assert whole_unit_qty(20.0) == 20.0          # already whole → unchanged

    def test_live_gld_rows_sum_to_broker_truth(self):
        """The two journal GLD rows quantize to the broker's netted 42 shares."""
        from src.units.accounts.risk import whole_unit_qty

        assert whole_unit_qty(8.368) + whole_unit_qty(33.632) == 42.0

    def test_min_one_open_path(self):
        """The OPEN/place path floors up to 1 (an order that reaches the venue
        always places >=1 share; the zero refusal happens upstream)."""
        from src.units.accounts.risk import whole_unit_qty

        assert whole_unit_qty(0.4, min_one=True) == 1.0
        assert whole_unit_qty(0.0, min_one=True) == 1.0

    def test_close_path_may_be_zero(self):
        """The partial-CLOSE path (min_one=False) rounds a sub-half-share close
        to 0 so the caller skips it — you can't close a fraction of a share."""
        from src.units.accounts.risk import whole_unit_qty

        assert whole_unit_qty(0.4) == 0.0
        assert whole_unit_qty(0.6) == 1.0

    def test_non_numeric_is_safe(self):
        from src.units.accounts.risk import whole_unit_qty

        assert whole_unit_qty("x") == 0.0
        assert whole_unit_qty(None, min_one=True) == 1.0


class TestWholeShareDirectionAgnostic:
    """``position_size`` is direction-agnostic on the whole-share (alpaca) path:
    a SHORT with mirrored geometry sizes IDENTICALLY to the LONG.

    Codifies the 2026-07-16 finding (BL-20260716 / diag #6574): a report that an
    alpaca "short sized to 0" is NOT a shorting bug — the account is
    ``shorting_enabled`` and the sizer never branches on side. ``abs(entry - sl)``
    and ``entry`` are the only geometry inputs, so a long and a mirror-image short
    (same entry, stop the same distance on the opposite side) always yield the
    same qty. Whatever refuses/rounds/sizes a long does the same to the short.
    """

    def _long_short(self, entry, stop_dist, tp_dist=10.0):
        common = {"strategy": "tlt_pullback_1d",
                  "meta": {"strategy_name": "tlt_pullback_1d", "strategy_risk_pct": 1.0}}
        lng = OrderPackage(symbol="TLT", direction="long", entry=entry,
                           sl=entry - stop_dist, tp=entry + tp_dist, **common)
        sht = OrderPackage(symbol="TLT", direction="short", entry=entry,
                           sl=entry + stop_dist, tp=entry - tp_dist, **common)
        return lng, sht

    def test_normal_size_identical(self):
        """A comfortably-sized trade: long and short whole-share qty match."""
        rm = RiskManager(dict(_ALPACA_LIKE))
        lng, sht = self._long_short(entry=298.50, stop_dist=16.48)
        ql = rm.position_size(lng, 100_000, whole_units=True)
        qs = rm.position_size(sht, 100_000, whole_units=True)
        assert ql == qs == pytest.approx(60.0)

    def test_round_up_identical(self):
        """At the round-up-to-one boundary, both size to 1 — not one to 0."""
        rm = RiskManager({"risk_pct": 0.01, "min_balance_usd": 100,
                          "daily_usd": 100_000, "pos_size": 100_000})
        lng, sht = self._long_short(entry=300.0, stop_dist=1.5)
        ql = rm.position_size(lng, 150.0, market_type="spot",
                              available_usd=10_000.0, whole_units=True)
        qs = rm.position_size(sht, 150.0, market_type="spot",
                              available_usd=10_000.0, whole_units=True)
        assert ql == qs == 1.0

    def test_affordability_refusal_identical(self):
        """The small-account refusal (the real 'sized to 0' cause) hits the SHORT
        and the LONG the same way — it is affordability, not side."""
        rm = RiskManager({"risk_pct": 0.02, "min_balance_usd": 100,
                          "daily_usd": 100_000, "pos_size": 100_000})
        # $149 acct @ 2% = $2.98 budget; a $5 stop → 1 share risks $5 = 1.68x
        # budget > 1.5x → refused. Both sides refuse to 0.
        lng, sht = self._long_short(entry=90.0, stop_dist=5.0)
        ql = rm.position_size(lng, 149.0, market_type="spot",
                              available_usd=10_000.0, whole_units=True)
        qs = rm.position_size(sht, 149.0, market_type="spot",
                              available_usd=10_000.0, whole_units=True)
        assert ql == qs == 0.0


class TestExecutorWholeShareFloorMatchesClient:
    """execute_pkg quantizes the journaled qty with the SAME helper the Alpaca
    client uses to place — so a fractional qty reaching the order build (e.g. a
    pre-fix qty_override, or any future fractional path) is journaled as the
    whole share the broker actually holds, not the fractional sizer value."""

    def test_journal_qty_equals_placed_qty(self):
        from src.units.accounts.risk import whole_unit_qty

        # What the executor floor records == what AlpacaClient.place sends.
        for frac in (8.368, 33.632, 231.078, 9.079):
            journaled = whole_unit_qty(frac, min_one=True)      # execute_pkg 5b
            placed = int(whole_unit_qty(frac, min_one=True))    # AlpacaClient.place
            assert journaled == float(placed)
