"""Sub-minimum-lot REFUSAL on the crypto/spot path (#3910 Item 3).

Operator-approved 2026-06-28 ("refuse sub-minimum"): when the risk-based size
on a non-whole-unit account (crypto perp / fx) computes BELOW the exchange
minimum lot, ``RiskManager.position_size`` REFUSES the trade (returns 0.0)
rather than bumping the size UP to ``min_qty``.

Why the bump was wrong (the bug this pins shut): the bump silently realised
MORE than the configured per-trade risk budget, and — when the bumped size
equalled a held min-lot — it pinned the real-money ``bybit_2`` account in a
permanent ``at_target`` freeze (the position could never be sized down, so it
never closed cleanly). A per-trade refusal is the Prime-Directive-correct shape:
the account stays LIVE, only this one trade is declined with a logged cause.

Pairs with:
  * ``test_ib_sizing_and_data.py::TestFuturesWholeContractEnforcement`` — the
    futures whole-contract refuse-sub-1 path (BL-20260611-001).
  * ``test_alpaca_whole_share_sizing.py`` — the equity whole-share path, which
    instead ROUNDS UP to 1 share when its stop risk fits the budget (the
    2026-06-24 directive); crypto/fx do NOT round up (this file).
"""
from __future__ import annotations

import pytest

from src.core.coordinator import OrderPackage
from src.units.accounts.risk import RiskManager


# bybit_2-like real-money config: 1% risk, no min_qty/qty_precision override
# (falls back to the 0.001 BTC lot / 3dp crypto defaults), Bybit linear perp.
_BYBIT2_LIKE = {"risk_pct": 0.01, "daily_usd": 100_000}


def _pkg(symbol="BTCUSDT", entry=80_000.0, sl=79_200.0, tp=82_000.0):
    return OrderPackage(
        strategy="vwap",
        symbol=symbol,
        direction="long",
        entry=entry,
        sl=sl,
        tp=tp,
        meta={},
    )


class TestSubMinLotRefuses:
    def test_wide_sl_small_balance_refuses_not_bumped(self):
        """Wide SL ($10k) on a tiny balance → risk-based size is far below the
        0.001 BTC lot → REFUSE (0.0), never the legacy bump-to-0.001 (which
        would risk $10 = 10% of a $100 account at SL)."""
        rm = RiskManager(dict(_BYBIT2_LIKE))
        pkg = _pkg("BTCUSDT", 80_000.0, 70_000.0, 95_000.0)  # $10k risk-distance
        assert rm.position_size(pkg, 100.0, market_type="linear") == 0.0

    def test_refused_size_would_have_over_risked(self):
        """The refused trade's bumped min-lot would have breached the 1% budget
        — proving the refusal protects the cap. $200 balance, $10k SL distance:
        budget = $2; the 0.001 min lot risks 0.001*10000 = $10 = 5x the cap."""
        rm = RiskManager(dict(_BYBIT2_LIKE))
        pkg = _pkg("BTCUSDT", 80_000.0, 70_000.0, 95_000.0)
        assert rm.position_size(pkg, 200.0, market_type="linear") == 0.0

    def test_normal_sl_still_sizes(self):
        """Regression guard against OVER-refusing: a normal SL where the
        risk-based size clears the min lot still sizes (the fix only declines
        sub-min-lot sizes, it does not freeze the account)."""
        rm = RiskManager(dict(_BYBIT2_LIKE))
        # $800 risk-distance, $50k balance → a healthy multi-tenth-BTC size,
        # ≫ the 0.001 lot (the exact figure is then trimmed by the margin
        # pre-flight cap; the point is it SIZES, far above the floor — the fix
        # declines only sub-min-lot sizes, it does not freeze the account).
        pkg = _pkg("BTCUSDT", 80_000.0, 79_200.0, 82_000.0)
        qty = rm.position_size(pkg, 50_000.0, market_type="linear")
        assert qty > 0.5  # orders of magnitude above the 0.001 min lot

    def test_exactly_min_lot_sizes(self):
        """Boundary: a risk-based size landing exactly on the min lot is taken
        (the refusal is strict-less-than the floor, not <=)."""
        rm = RiskManager(dict(_BYBIT2_LIKE))
        # Choose balance so raw == 0.001 exactly: risk_usdt / risk_distance =
        # 0.001 → (balance*0.01)/1000 = 0.001 → balance = 100.
        pkg = _pkg("BTCUSDT", 80_000.0, 79_000.0, 82_000.0)  # $1000 risk-distance
        qty = rm.position_size(pkg, 100.0, market_type="linear")
        assert qty == pytest.approx(0.001)
