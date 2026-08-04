"""ict_scalp harness execution-realism cost wiring (P1 § 3.B).

ict_scalp previously had NO cost model (gross_r == net_r). This locks the wiring
that added it through the ONE shared model (src.runtime.execution_costs):
  1. with slippage+funding at 0 the harness is fee-only and net == fee-only arm;
  2. the summary carries both arms (net-of-full-cost + fee-only) additively;
  3. the CLI resolves the mandatory VENUE-AWARE default — funding is perp-only,
     so a non-perp symbol pays 0 funding (no fabricated cost) while a crypto perp
     pays it.
"""
from __future__ import annotations

import importlib

import pandas as pd

import scripts.backtest_ict_scalp as h


def _reset_globals():
    """Restore the module-default (fee-only) cost globals between cases."""
    h.FEE_BPS_ROUNDTRIP = h.execution_costs.DEFAULT_FEE_BPS_ROUNDTRIP
    h.SLIPPAGE_BPS_ROUNDTRIP = 0.0
    h.FUNDING_BPS_PER_WINDOW = 0.0
    h.FUNDING_WINDOW_HOURS = h.execution_costs.FUNDING_WINDOW_HOURS
    h.execution_costs._PERP_CATEGORY_CACHE = None


def _mk_trade(entry, exit_price, sl, r, *, direction="long",
              entry_time="2026-01-01T00:00:00Z", exit_time="2026-01-01T00:30:00Z"):
    return h.Trade(
        entry_index=0, entry_time=entry_time, direction=direction,
        entry=entry, sl=sl, tp=entry + 2 * (entry - sl), risk=abs(entry - sl),
        exit_index=6, exit_time=exit_time, exit_price=exit_price,
        outcome="tp_hit" if r > 0 else "sl_hit", r_multiple=r, meta={},
        confidence=0.5,
    )


class TestCostBreakdown:
    def test_fee_only_default_matches_shared_legacy_term(self):
        _reset_globals()
        t = _mk_trade(100.0, 110.0, 96.0, 2.5)
        cb = h._cost_breakdown(t)
        legacy = (h.FEE_BPS_ROUNDTRIP / 1e4) * ((t.entry + t.exit_price) / 2.0) / t.risk
        assert abs(cb["fee_r"] - legacy) < 1e-9
        assert cb["slippage_r"] == 0.0 and cb["funding_r"] == 0.0
        assert abs(cb["total_cost_r"] - legacy) < 1e-9

    def test_zero_risk_or_no_exit_is_zero_cost(self):
        _reset_globals()
        t = _mk_trade(100.0, 110.0, 100.0, 0.0)  # risk 0
        assert h._cost_breakdown(t)["total_cost_r"] == 0.0


class TestSummaryBothArms:
    def test_fee_only_globals_net_equals_fee_only_arm(self):
        _reset_globals()
        trades = [_mk_trade(100.0, 110.0, 96.0, 2.5),
                  _mk_trade(100.0, 96.0, 96.0, -1.0)]
        s = h._summarize(trades, pd.DataFrame({"close": [1.0]}),
                         timeframe="5m", symbol="BTCUSDT")
        # slippage+funding are 0 → the two arms coincide.
        assert s["net_total_r"] == s["net_total_r_fee_only"]
        gross = sum(t.r_multiple for t in trades)
        fee = sum(h._fee_only_r(t) for t in trades)
        # summary aggregates round to 4 dp — compare within that tolerance.
        assert abs(s["net_total_r"] - (gross - fee)) < 1e-3
        assert s["total_r"] == round(gross, 4)  # gross arm preserved

    def test_slippage_makes_net_strictly_below_fee_only(self):
        _reset_globals()
        h.SLIPPAGE_BPS_ROUNDTRIP = 5.0
        trades = [_mk_trade(100.0, 110.0, 96.0, 2.5)]
        s = h._summarize(trades, pd.DataFrame({"close": [1.0]}),
                         timeframe="5m", symbol="BTCUSDT")
        assert s["net_total_r"] < s["net_total_r_fee_only"]
        assert s["mean_cost_r"]["slippage_r"] > 0.0
        _reset_globals()


class TestVenueAwareCLI:
    def _run(self, symbol, tmp_path, extra=None):
        # Real BTC candles ship in-repo; relabel to `symbol` to exercise the
        # venue-aware funding resolver (funding is perp-only) without new data.
        out = tmp_path / "s.json"
        argv = ["backtest_ict_scalp.py", "--data", "data/backtest_candles.csv",
                "--symbol", symbol, "--json", str(out)]
        if extra:
            argv += extra
        rc = h.main(argv)
        assert rc == 0
        import json
        return json.loads(out.read_text())

    def test_crypto_perp_gets_funding_default(self, tmp_path):
        _reset_globals()
        s = self._run("BTCUSDT", tmp_path)
        assert s["funding_bps_per_window"] == h.execution_costs.DEFAULT_FUNDING_BPS_PER_WINDOW
        assert s["slippage_bps_roundtrip"] == h.execution_costs.DEFAULT_SLIPPAGE_BPS_ROUNDTRIP

    def test_non_perp_gets_zero_funding(self, tmp_path):
        _reset_globals()
        s = self._run("MES", tmp_path)
        assert s["funding_bps_per_window"] == 0.0            # perp-only, no fabricated cost
        assert s["slippage_bps_roundtrip"] == h.execution_costs.DEFAULT_SLIPPAGE_BPS_ROUNDTRIP

    def test_explicit_zero_wins_fee_only_arm(self, tmp_path):
        _reset_globals()
        s = self._run("BTCUSDT", tmp_path,
                      extra=["--slippage-bps-roundtrip", "0", "--funding-bps-per-window", "0"])
        assert s["slippage_bps_roundtrip"] == 0.0
        assert s["funding_bps_per_window"] == 0.0
        if s["total_trades"]:
            assert s["net_total_r"] == s["net_total_r_fee_only"]


def test_module_imports_clean():
    importlib.reload(h)  # no import-time side effects beyond the globals
