"""backtest_system (USD-space) execution-realism cost wiring (P1 § 3.B).

This harness already charged a round-trip FEE; the rollout ADDS slippage + perp-only
funding through the ONE shared USD model without touching the fee convention. Locks:
  1. the fee formula is unchanged → with slippage+funding at 0 the run is byte-identical
     (net_pnl == net_pnl_fee_only);
  2. the summary carries the cost config + per-cost USD totals + the fee-only arm;
  3. the CLI resolves the mandatory VENUE-AWARE default — funding is perp-only, so a
     non-perp symbol pays 0 funding (no fabricated cost).
"""
from __future__ import annotations

import json

import scripts.backtest_system as bs


def _reset_globals():
    bs.SLIPPAGE_BPS_ROUNDTRIP = 0.0
    bs.FUNDING_BPS_PER_WINDOW = 0.0
    bs.FUNDING_WINDOW_HOURS = bs.execution_costs.FUNDING_WINDOW_HOURS
    bs.execution_costs._PERP_CATEGORY_CACHE = None


def _mk_closed(pnl, fee, *, slippage=0.0, funding=0.0):
    return bs._ClosedTrade(
        owner="s", side="long", entry_ts="2026-01-01T00:00:00Z",
        exit_ts="2026-01-01T01:00:00Z", entry=100.0, exit=101.0, qty=1.0,
        pnl=pnl, fee=fee, slippage=slippage, funding=funding,
        reason="tp", bars_held=4)


class TestClosedTradeFields:
    def test_slippage_funding_default_zero(self):
        t = bs._ClosedTrade(owner="s", side="long", entry_ts=None, exit_ts=None,
                            entry=1.0, exit=1.0, qty=1.0, pnl=0.0, fee=0.0,
                            reason="tp", bars_held=0)
        assert t.slippage == 0.0 and t.funding == 0.0


class TestSummaryCostFields:
    def _summ(self, closed):
        return bs._summarize(
            closed, [(0, 10_000.0 + sum(t.pnl for t in closed))],
            base_balance=10_000.0, util_bars=1, total_bars=100, roster=["s"],
            params={}, data_start="a", data_end="b", symbol="BTCUSDT")

    def test_zero_cost_net_equals_fee_only(self):
        _reset_globals()
        s = self._summ([_mk_closed(50.0, 5.0), _mk_closed(-20.0, 5.0)])
        assert s["net_pnl"] == s["net_pnl_fee_only"]  # slippage+funding both 0
        assert s["total_slippage_usd"] == 0.0 and s["total_funding_usd"] == 0.0
        assert s["total_fee_usd"] == 10.0

    def test_slippage_funding_totals_and_fee_only_arm(self):
        _reset_globals()
        s = self._summ([_mk_closed(50.0, 5.0, slippage=2.0, funding=1.0)])
        assert s["total_slippage_usd"] == 2.0 and s["total_funding_usd"] == 1.0
        # fee-only arm adds slippage+funding back onto the net.
        assert s["net_pnl_fee_only"] == round(s["net_pnl"] + 2.0 + 1.0, 2)

    def test_cost_config_echoed(self):
        _reset_globals()
        bs.SLIPPAGE_BPS_ROUNDTRIP = 5.0
        bs.FUNDING_BPS_PER_WINDOW = 1.0
        s = self._summ([_mk_closed(50.0, 5.0)])
        assert s["slippage_bps_roundtrip"] == 5.0
        assert s["funding_bps_per_window"] == 1.0
        _reset_globals()


class TestVenueAwareCLI:
    def _run(self, symbol, tmp_path):
        out = tmp_path / "s.json"
        rc = bs.main(["backtest_system.py", "--data", "data/backtest_candles.csv",
                      "--symbol", symbol, "--refresh-signals", "--json", str(out)])
        assert rc == 0
        return json.loads(out.read_text())

    def test_crypto_perp_gets_funding_default(self, tmp_path):
        _reset_globals()
        s = self._run("BTCUSDT", tmp_path)
        assert s["funding_bps_per_window"] == bs.execution_costs.DEFAULT_FUNDING_BPS_PER_WINDOW
        assert s["slippage_bps_roundtrip"] == bs.execution_costs.DEFAULT_SLIPPAGE_BPS_ROUNDTRIP

    def test_non_perp_gets_zero_funding(self, tmp_path):
        _reset_globals()
        s = self._run("MES", tmp_path)
        assert s["funding_bps_per_window"] == 0.0  # perp-only, no fabricated cost
        assert s["slippage_bps_roundtrip"] == bs.execution_costs.DEFAULT_SLIPPAGE_BPS_ROUNDTRIP
