"""M28 P4 — tests for the value-thesis backtest runner.

Covers the runner's own new units (the CSV close-panel loader, the leakage-safe
as-of-or-prior price reader, and rebalance-date derivation) plus one end-to-end
wire (fixture snapshots → build_replay_entries → run_thesis_backtest → a real
scorecard). The former/backtest math itself is covered by the macro_thesis
package tests; this proves the RUNNER glues them without lookahead.
"""
from __future__ import annotations

import importlib.util
import os

import pytest

# The runner lives under scripts/ (not an importable package) — load it by path.
_RUNNER_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),  # tests/ -> repo root
    "scripts", "macro", "thesis_backtest_run.py",
)
_spec = importlib.util.spec_from_file_location("thesis_backtest_run", _RUNNER_PATH)
runner = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(runner)


# ---------------------------------------------------------------------------
# CSV close-panel loader
# ---------------------------------------------------------------------------
def _write_csv(path, rows, *, header="date,open,high,low,close,volume"):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(header + "\n")
        for r in rows:
            fh.write(r + "\n")


class TestLoadClosePanels:
    def test_reads_symbol_and_sorts(self, tmp_path):
        _write_csv(tmp_path / "SPY.csv", [
            "2026-02-01,0,0,0,110.0,0",
            "2026-01-01,0,0,0,100.0,0",   # out of order on purpose
        ])
        panels = runner.load_close_panels(str(tmp_path))
        assert "SPY" in panels
        assert panels["SPY"] == [("2026-01-01", 100.0), ("2026-02-01", 110.0)]

    def test_skips_unparseable_close(self, tmp_path):
        _write_csv(tmp_path / "QQQ.csv", [
            "2026-01-01,0,0,0,,0",         # empty close → skipped
            "2026-01-02,0,0,0,x,0",        # non-numeric → skipped
            "2026-01-03,0,0,0,50.0,0",
        ])
        panels = runner.load_close_panels(str(tmp_path))
        assert panels["QQQ"] == [("2026-01-03", 50.0)]

    def test_case_insensitive_columns(self, tmp_path):
        _write_csv(tmp_path / "GLD.csv", ["2026-01-01,200.0"], header="Date,Close")
        panels = runner.load_close_panels(str(tmp_path))
        assert panels["GLD"] == [("2026-01-01", 200.0)]


# ---------------------------------------------------------------------------
# leakage-safe as-of-or-prior price reader (the correctness-critical unit)
# ---------------------------------------------------------------------------
class TestPriceAt:
    def _price(self):
        panels = {"SPY": [("2026-01-05", 100.0), ("2026-01-12", 110.0), ("2026-01-20", 120.0)]}
        return runner.make_price_at(panels)

    def test_exact_date(self):
        assert self._price()("SPY", "2026-01-12") == 110.0

    def test_prior_date_no_lookahead(self):
        # A date between bars resolves to the LAST PRIOR close, never the next one.
        assert self._price()("SPY", "2026-01-15") == 110.0

    def test_after_last_uses_last(self):
        assert self._price()("SPY", "2026-03-01") == 120.0

    def test_before_history_is_none(self):
        # No leak-free price exists before the first bar → None (drops the thesis).
        assert self._price()("SPY", "2026-01-01") is None

    def test_unknown_symbol_is_none(self):
        assert self._price()("NVDA", "2026-01-12") is None

    def test_timestamp_forms_accepted(self):
        assert self._price()("SPY", "2026-01-12T00:00:00Z") == 110.0


# ---------------------------------------------------------------------------
# rebalance-date derivation
# ---------------------------------------------------------------------------
class TestDeriveRebalanceDates:
    def test_spans_history_by_step(self):
        recs = [{"observed_at": "2026-01-01"}, {"observed_at": "2026-03-02"}]
        out = runner.derive_rebalance_dates(recs, 30)
        assert out[0] == "2026-01-01"
        assert out[-1] <= "2026-03-02"
        # monotonic, 30-day steps
        assert out == ["2026-01-01", "2026-01-31", "2026-03-02"]

    def test_empty_history(self):
        assert runner.derive_rebalance_dates([], 30) == []


# ---------------------------------------------------------------------------
# end-to-end wiring: fixture snapshots → replay → scorecard
# ---------------------------------------------------------------------------
def _snap(symbol, cheap_score, observed_at, *, metric="erp", value=1.0):
    """A minimal valuation_snapshots row the former will turn into a thesis."""
    return {
        "symbol": symbol, "metric": metric, "value": value,
        "cheap_score": cheap_score, "label": "cheap" if cheap_score >= 0.5 else "rich",
        "higher_is_cheaper": True, "n_history": 60, "percentile": cheap_score,
        "z_score": 0.0, "observed_at": observed_at, "as_of": observed_at,
        "source": "test", "asset_class": "equity", "inputs": {}, "note": "",
    }


class TestEndToEnd:
    def test_wires_to_a_real_scorecard(self):
        # SPY reads very cheap (long, high conviction); price rises → a win.
        records = [_snap("SPY", 0.9, "2026-01-05")]
        panels = {"SPY": [("2026-01-05", 100.0), ("2026-02-04", 120.0)]}
        price_at = runner.make_price_at(panels)
        cfg = {"min_conviction": 0.4, "universe": ["SPY"],
               "express_as": "debit_vertical", "account": "alpaca_options_paper"}
        from src.units.strategies.macro_thesis.thesis_replay import build_replay_entries
        from src.units.strategies.macro_thesis.thesis_backtest import run_thesis_backtest

        entries = build_replay_entries(
            records, price_at, rebalance_dates=["2026-01-05"], cfg=cfg, horizon_days=30.0,
        )
        assert len(entries) == 1
        e = entries[0]
        assert e["symbol"] == "SPY" and e["direction"] == "long"
        assert e["entry_price"] == 100.0 and e["exit_price"] == 120.0

        card = run_thesis_backtest(entries, fee_frac=0.001, carry_frac_per_day=0.0)
        assert card["n"] == 1
        assert card["win_rate"] == 1.0            # long into a +20% move, net of 0.1% fee
        assert card["mean_net_return"] is not None and card["mean_net_return"] > 0

    def test_empty_history_is_honest_zero_not_crash(self):
        cfg = {"min_conviction": 0.4, "universe": []}
        from src.units.strategies.macro_thesis.thesis_replay import build_replay_entries
        from src.units.strategies.macro_thesis.thesis_backtest import run_thesis_backtest
        entries = build_replay_entries(
            [], runner.make_price_at({}), rebalance_dates=[], cfg=cfg, horizon_days=30.0,
        )
        card = run_thesis_backtest(entries)
        assert card["n"] == 0
        assert card["win_rate"] is None           # None on empty, never 0
        assert card["mean_net_return"] is None


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
