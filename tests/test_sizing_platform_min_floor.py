"""BL-20260617-SIZEFLOOR: crypto sizing floors on the PLATFORM minimum
(config/instruments.yaml), never a hardcoded 0.001, and a risk-based size
below that minimum is a per-trade REFUSAL (operator directive 2026-06-17,
risk-faithful) rather than a bump-up. This is what un-freezes a small
real-money account (bybit_2) that was pinned in a permanent at_target loop by
the bumped min-lot equalling the held position.
"""
from __future__ import annotations

from src.core.coordinator import OrderPackage
from src.units.accounts.risk import RiskManager, instrument_min_qty_step_for


def _pkg(**ov):
    base = dict(strategy="vwap", symbol="BTCUSDT", direction="long",
                entry=70_000.0, sl=68_600.0, tp=71_400.0, confidence=0.6,
                meta={})
    base.update(ov)
    return OrderPackage(**base)


def _rm(**ov):
    cfg = dict(risk_pct=0.01, min_qty=0.001, qty_precision=3,
               daily_usd=1_000.0, max_dd_pct=0.05, pos_size=5_000.0,
               min_balance_usd=0.0, leverage=1)
    cfg.update(ov)
    return RiskManager(cfg)


def test_resolver_reads_platform_min_for_btc():
    # BTCUSDT in config/instruments.yaml: min_qty 0.001, qty_step 0.001 (3dp).
    assert instrument_min_qty_step_for("BTCUSDT") == (0.001, 3)


def test_resolver_none_for_unknown_or_empty_symbol():
    assert instrument_min_qty_step_for("NOPE_NOT_A_SYMBOL") is None
    assert instrument_min_qty_step_for("") is None


def test_small_account_refuses_below_platform_min():
    """The bybit_2 shape: ~$100 equity, 0.3% effective risk, ~$1.2k BTC stop →
    raw qty ~0.00025 < the 0.001 platform min → REFUSED (0.0), not bumped."""
    rm = _rm()
    pkg = _pkg(entry=66_000.0, sl=64_800.0, meta={"strategy_risk_pct": 0.3})
    qty = rm.position_size(pkg, balance_usd=100.0, total_account_usd=100.0)
    assert qty == 0.0


def test_adequate_account_sizes_a_real_lot():
    """A funded account still sizes normally (>= platform min), floored to the
    platform precision — the fix only removes the sub-min bump, not sizing."""
    rm = _rm(leverage=10)
    pkg = _pkg(entry=66_000.0, sl=64_800.0)  # distance 1200
    qty = rm.position_size(pkg, balance_usd=20_000.0, total_account_usd=20_000.0)
    assert qty >= 0.001
    assert round(qty, 3) == qty  # floored to 3dp (platform granularity)
