"""Venue-aware round-trip fee in the regime-debt research harness.

Regression guard for **BL-20260730-RESEARCH-VENUE-FEE**: `build_harness_cmd` used
to pass the literal `7.5` bps (the crypto-perp default) for EVERY symbol, so all 14
commission-free `(alpaca, spot)` US equity/ETF instruments were charged a ~25x
over-charge — worth ~0.04-0.12 R/trade. Because over-charging can only make a
strategy look WORSE, the bug's signature is **false OFF cells** (gating a leg that
is actually fine), never a fabricated edge. It graded the equity/ETF regime-debt
matrix (#7918) and its walk-forward verdicts (#7920-#7924), including the shipped
Tier-3 cell `trending.gld_pullback_1h`.

#7930 fixed the identical bug in the live close path; these tests pin the research
side so the two can never drift apart again.
"""
from __future__ import annotations

import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts", "research"))

regime_debt_matrix = pytest.importorskip("regime_debt_matrix")

# The 14 commission-free rows in config/instruments.yaml: exchange alpaca + category
# spot. Keyed on the VENUE, not the underlying asset class, so GLD/SLV/TLT resolve
# to 0 too even though they aren't equities.
COMMISSION_FREE = [
    "SPY", "QQQ", "TQQQ", "QLD", "GLD", "IWM", "TLT",
    "IEF", "SLV", "USO", "GDX", "SPLG", "IAUM", "SCHA",
]
# Venues that genuinely charge: the estimator default must be preserved.
FEE_BEARING = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "MES", "MGC", "MHG", "EURUSD"]


class TestRoundtripFeeBps:
    @pytest.mark.parametrize("symbol", COMMISSION_FREE)
    def test_commission_free_venue_resolves_zero(self, symbol):
        assert regime_debt_matrix.roundtrip_fee_bps(symbol) == 0.0, (
            f"{symbol} is commission-free on Alpaca; a non-zero fee re-introduces "
            "the phantom-drag that produces false OFF cells"
        )

    @pytest.mark.parametrize("symbol", FEE_BEARING)
    def test_fee_bearing_venue_keeps_the_default(self, symbol):
        assert regime_debt_matrix.roundtrip_fee_bps(symbol) == 7.5

    def test_unknown_symbol_is_conservative(self):
        """An unknown symbol must NOT silently resolve to zero — never assume a
        venue is free just because we can't identify it."""
        assert regime_debt_matrix.roundtrip_fee_bps("NOT_A_REAL_SYMBOL_XYZ") == 7.5

    def test_default_is_not_duplicated_locally(self):
        """The 7.5 default must come from trade_costs, so there is one owner."""
        if REPO not in sys.path:
            sys.path.insert(0, REPO)
        from src.runtime.trade_costs import DEFAULT_FEE_BPS_ROUNDTRIP

        assert regime_debt_matrix.roundtrip_fee_bps("BTCUSDT") == float(
            DEFAULT_FEE_BPS_ROUNDTRIP
        )


class TestHarnessArgv:
    @staticmethod
    def _fee_in_argv(symbol):
        cfg = {
            "symbols": [symbol], "atr_period": 14, "atr_stop_mult": 2.5,
            "trail_mult": 5.0, "donchian": 20,
        }
        argv, _faithful, _omitted = regime_debt_matrix.build_harness_cmd(
            f"{symbol.lower()}_probe", cfg, "trend",
            "/tmp/data.csv", "1h", "/tmp/emit.jsonl", "/tmp/out.json",
        )
        return float(argv[argv.index("--fee-bps-roundtrip") + 1])

    def test_equity_etf_argv_carries_zero_fee(self):
        assert self._fee_in_argv("GLD") == 0.0

    def test_crypto_argv_carries_the_default_fee(self):
        assert self._fee_in_argv("BTCUSDT") == 7.5

    def test_fee_flag_is_passed_exactly_once(self):
        """Duplicate flags would make the effective fee depend on argparse order."""
        cfg = {"symbols": ["GLD"], "donchian": 20}
        argv, _f, _o = regime_debt_matrix.build_harness_cmd(
            "gld_probe", cfg, "trend",
            "/tmp/d.csv", "1h", "/tmp/e.jsonl", "/tmp/o.json",
        )
        assert argv.count("--fee-bps-roundtrip") == 1
