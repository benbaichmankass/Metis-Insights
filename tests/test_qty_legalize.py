"""Phase 1 of the sizing/qty-legalization consolidation
(``docs/sizing-legalization-DESIGN.md``).

Exercises the ``legalize_qty`` seam in isolation — nothing is wired to it yet.
The seam owns venue legalization (step-align floor + minimum-lot refuse), with
the minimum resolved from ``InstrumentProfile`` (``config/instruments.yaml``)
first, then the live Bybit lot rule, then passthrough.

Parity anchor: for a Bybit symbol the seam must return the SAME legal/refuse
verdict the coordinator sized-qty guard + the ``_submit_order`` pre-flight
produce today (floor-down to ``qty_step``; refuse below ``minOrderQty``; leave a
qty untouched when no rule resolves).
"""
from __future__ import annotations

import pytest

from src.core.instrument_profile import InstrumentProfile
from src.units.accounts import precision
from src.units.accounts import qty_legalize
from src.units.accounts.qty_legalize import legalize_qty


@pytest.fixture(autouse=True)
def _clean_caches():
    qty_legalize._reset_profile_cache()
    precision._LOT_CACHE.clear()
    precision._LIVE_CACHE.clear()
    yield
    qty_legalize._reset_profile_cache()
    precision._LOT_CACHE.clear()
    precision._LIVE_CACHE.clear()


def _profile(symbol, exchange, category, step, min_qty):
    return InstrumentProfile(
        symbol=symbol, exchange=exchange, category=category,
        base_asset=symbol[:3], quote_currency="USDT",
        settlement_currency="USDT", tick_size=0.01,
        min_qty=min_qty, qty_step=step,
    )


_BYBIT = {"account_id": "bybit_2", "exchange": "bybit", "market_type": "linear"}
_IB = {"account_id": "ib_paper", "exchange": "interactive_brokers", "market_type": "futures"}


# --- Profile-backed resolution: floor to step, refuse below min ------------

class TestProfilePathFloor:
    def _eth_profiles(self):
        return {"ETHUSDT": _profile("ETHUSDT", "bybit", "linear", 0.01, 0.01)}

    def test_eth_floors_down_never_up(self):
        # 0.111 ETH -> 0.11 (floor to 0.01 step), accepted.
        r = legalize_qty(0.111, account_cfg=_BYBIT, symbol="ETHUSDT",
                         profiles=self._eth_profiles())
        assert r.ok is True
        assert r.qty == pytest.approx(0.11, abs=1e-9)
        assert r.venue_min == pytest.approx(0.01)
        assert r.step == pytest.approx(0.01)
        assert r.source == "instrument_profile"

    def test_eth_sub_min_delta_refused(self):
        # 0.009 ETH -> floors to 0.0 -> below 0.01 min -> REFUSE. This is the
        # exact PR #5700 / recurring-ping quantity.
        r = legalize_qty(0.009, account_cfg=_BYBIT, symbol="ETHUSDT",
                         profiles=self._eth_profiles())
        assert r.ok is False
        assert r.reason == "below_venue_min_qty"
        assert r.qty == pytest.approx(0.0, abs=1e-12)

    def test_eth_exactly_one_lot_accepted(self):
        r = legalize_qty(0.015, account_cfg=_BYBIT, symbol="ETHUSDT",
                         profiles=self._eth_profiles())
        assert r.ok is True
        assert r.qty == pytest.approx(0.01, abs=1e-9)

    def test_eth_just_below_min_refused(self):
        r = legalize_qty(0.005, account_cfg=_BYBIT, symbol="ETHUSDT",
                         profiles=self._eth_profiles())
        assert r.ok is False
        assert r.reason == "below_venue_min_qty"

    def test_btc_001_lot_unchanged(self):
        profs = {"BTCUSDT": _profile("BTCUSDT", "bybit", "linear", 0.001, 0.001)}
        r = legalize_qty(0.111, account_cfg=_BYBIT, symbol="BTCUSDT", profiles=profs)
        assert r.ok is True
        assert r.qty == pytest.approx(0.111, abs=1e-9)

    def test_sol_01_lot(self):
        profs = {"SOLUSDT": _profile("SOLUSDT", "bybit", "linear", 0.1, 0.1)}
        assert legalize_qty(0.35, account_cfg=_BYBIT, symbol="SOLUSDT", profiles=profs).qty == pytest.approx(0.3)
        assert legalize_qty(0.05, account_cfg=_BYBIT, symbol="SOLUSDT", profiles=profs).ok is False


# --- Whole-contract (futures) generalizes through the same seam ------------

class TestFuturesWholeContract:
    def _mes_profiles(self):
        return {"MES": _profile("MES", "interactive_brokers", "futures", 1.0, 1.0)}

    def test_fractional_contract_floors_to_whole(self):
        r = legalize_qty(3.6, account_cfg=_IB, symbol="MES", profiles=self._mes_profiles())
        assert r.ok is True
        assert r.qty == pytest.approx(3.0)

    def test_sub_one_contract_refused(self):
        r = legalize_qty(0.5, account_cfg=_IB, symbol="MES", profiles=self._mes_profiles())
        assert r.ok is False
        assert r.reason == "below_venue_min_qty"


# --- Passthrough: rule unknown never adds a refusal ------------------------

class TestPassthrough:
    def test_unknown_symbol_bybit_no_client_passthrough(self):
        # No profile, no client, no static-map entry -> rule unknown -> passthrough.
        r = legalize_qty(1.2345, account_cfg=_BYBIT, symbol="DOGEUSDT", profiles={})
        assert r.ok is True
        assert r.qty == pytest.approx(1.2345)
        assert r.source == "unknown"
        assert r.venue_min is None

    def test_non_bybit_no_profile_passthrough(self):
        # Alpaca/OANDA carry their own whole-unit handling in risk.py; the
        # venue seam has no rule for them here -> passthrough, unchanged.
        cfg = {"account_id": "alpaca_paper", "exchange": "alpaca"}
        r = legalize_qty(9.079, account_cfg=cfg, symbol="IWM", profiles={})
        assert r.ok is True
        assert r.qty == pytest.approx(9.079)
        assert r.source == "unknown"

    def test_venue_mismatch_profile_ignored(self):
        # A bybit-venue profile must NOT apply to an alpaca account trading a
        # same-named symbol -> profile skipped -> passthrough (no live rule).
        cfg = {"account_id": "alpaca_paper", "exchange": "alpaca"}
        profs = {"ETHUSDT": _profile("ETHUSDT", "bybit", "linear", 0.01, 0.01)}
        r = legalize_qty(0.009, account_cfg=cfg, symbol="ETHUSDT", profiles=profs)
        assert r.ok is True  # not refused — the bybit lot didn't apply
        assert r.source == "unknown"


# --- Live-lot fallback (static map) when no profile entry ------------------

class TestLiveLotFallback:
    def test_eth_via_static_map_when_no_profile(self):
        # Empty profiles -> profile miss -> Bybit lot rule -> static map
        # ETHUSDT linear (0.01, 0.01). 0.009 still refuses.
        r = legalize_qty(0.009, account_cfg=_BYBIT, symbol="ETHUSDT", profiles={})
        assert r.ok is False
        assert r.reason == "below_venue_min_qty"
        assert r.source == "live_lot_rule"
        assert r.venue_min == pytest.approx(0.01)

    def test_live_client_lot_rule_used(self):
        class _LotClient:
            def get_instruments_info(self, *, category, symbol):
                return {"result": {"list": [{
                    "lotSizeFilter": {"qtyStep": "0.02", "minOrderQty": "0.05"},
                }]}}
        r = legalize_qty(0.049, account_cfg=_BYBIT, symbol="ETHUSDT",
                         client=_LotClient(), profiles={})
        assert r.ok is False  # 0.049 floors to 0.04 < 0.05
        r2 = legalize_qty(0.07, account_cfg=_BYBIT, symbol="ETHUSDT",
                          client=_LotClient(), profiles={})
        assert r2.ok is True and r2.qty == pytest.approx(0.06)  # floor to 0.02 step


# --- Integration against the real config/instruments.yaml ------------------

class TestRealInstrumentsYaml:
    def test_eth_from_repo_yaml_refuses_sub_lot(self):
        # profiles=None -> loads the real config/instruments.yaml (ETHUSDT 0.01).
        r = legalize_qty(0.009, account_cfg=_BYBIT, symbol="ETHUSDT")
        assert r.ok is False
        assert r.venue_min == pytest.approx(0.01)
        assert r.source == "instrument_profile"

    def test_btc_from_repo_yaml_accepts(self):
        r = legalize_qty(0.111, account_cfg=_BYBIT, symbol="BTCUSDT")
        assert r.ok is True
        assert r.qty == pytest.approx(0.111, abs=1e-9)
        assert r.venue_min == pytest.approx(0.001)

    def test_mes_from_repo_yaml_whole_contract(self):
        r = legalize_qty(2.9, account_cfg=_IB, symbol="MES")
        assert r.ok is True
        assert r.qty == pytest.approx(2.0)
