"""Phase 3 (BL-20260628-CRYPTO-INSTRUMENT-MIN-FLOOR): RiskManager.position_size
resolves the sub-min refusal + step from the per-instrument InstrumentProfile
(config/instruments.yaml) on the non-whole path, instead of the account-shaped
default (min_qty 0.001 / qty_precision 3). Whole-share sizing stays exchange-
gated by whole_units (BL-20260622) — never inferred from the instrument.
"""
from __future__ import annotations

import pytest

from src.core.coordinator import OrderPackage
from src.units.accounts.qty_legalize import instrument_lot, _reset_profile_cache
from src.units.accounts.risk import RiskManager, _step_to_precision


@pytest.fixture(autouse=True)
def _clean():
    _reset_profile_cache()
    yield
    _reset_profile_cache()


def _pkg(symbol, entry, sl, confidence=1.0):
    return OrderPackage(
        strategy="eth_pullback_2h", symbol=symbol, direction="long",
        entry=entry, sl=sl, tp=entry + 2 * (entry - sl), confidence=confidence,
    )


# --- _step_to_precision -----------------------------------------------------

@pytest.mark.parametrize("step,prec", [
    (0.01, 2), (0.001, 3), (0.1, 1), (1.0, 0), (0.0001, 4), (0.5, 1),
])
def test_step_to_precision(step, prec):
    assert _step_to_precision(step) == prec


# --- instrument_lot ---------------------------------------------------------

def test_instrument_lot_from_repo_yaml():
    assert instrument_lot("ETHUSDT") == pytest.approx((0.01, 0.01))
    assert instrument_lot("SOLUSDT") == pytest.approx((0.1, 0.1))
    assert instrument_lot("BTCUSDT") == pytest.approx((0.001, 0.001))
    assert instrument_lot("IWM") == pytest.approx((1.0, 1.0))  # whole-share equity


def test_instrument_lot_unknown_symbol_none():
    assert instrument_lot("NOSUCHPAIRXYZ") is None


# --- RiskManager.position_size: instrument-aware crypto minimum -------------

class TestEthInstrumentMin:
    def _rm(self):
        return RiskManager({"risk_pct": 0.01, "daily_usd": 100_000, "leverage": 3})

    def test_eth_below_instrument_min_refused(self):
        # Tiny balance → risk-sized ETH qty floors below the 0.01 instrument lot
        # → per-trade REFUSAL (0.0), not a sub-lot qty passed downstream.
        rm = self._rm()
        pkg = _pkg("ETHUSDT", entry=1725.0, sl=1685.0)  # 40-wide stop
        # balance 20 → raw ~0.005 ETH < 0.01 → refuse.
        assert rm.position_size(pkg, 20.0, market_type="linear") == 0.0

    def test_eth_size_aligned_to_001_step(self):
        # Larger balance → risk-sized qty floored to the 0.01 step (Phase 3),
        # NOT the account 0.001 granularity. The returned qty is a 0.01 multiple.
        rm = self._rm()
        pkg = _pkg("ETHUSDT", entry=1725.0, sl=1685.0)
        q = rm.position_size(pkg, 400.0, market_type="linear")
        assert q > 0
        # Aligned to 0.01 (2dp), so q*100 is an integer.
        assert abs(q * 100 - round(q * 100)) < 1e-9

    def test_btc_unchanged_001_granularity(self):
        # BTC's instrument step (0.001) == the account default → behaviour
        # unchanged; qty stays 0.001-granular.
        rm = self._rm()
        pkg = _pkg("BTCUSDT", entry=60000.0, sl=59000.0)  # 1000-wide stop
        q = rm.position_size(pkg, 400.0, market_type="linear")
        assert q > 0
        assert abs(q * 1000 - round(q * 1000)) < 1e-9
