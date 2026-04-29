"""S-011 PR #3: Backtesting UI — data helper tests.

Tests the importable helpers in src/web/backtest_ui.py without
loading Streamlit (no streamlit dependency in the test environment).
All data is hand-crafted — no CSV files, no exchange calls.
"""
from __future__ import annotations

import os

import pandas as pd
import pytest

from src.web.backtest_ui import (
    _mock_backtest_df,
    load_backtest_data,
    filter_backtest_data,
    build_equity_curve,
    summary_stats,
    _REQUIRED_COLUMNS,
)


# ---------------------------------------------------------------------------
# _mock_backtest_df
# ---------------------------------------------------------------------------

class TestMockBacktestDf:
    def test_returns_dataframe(self):
        df = _mock_backtest_df()
        assert isinstance(df, pd.DataFrame)

    def test_has_required_columns(self):
        df = _mock_backtest_df()
        for col in _REQUIRED_COLUMNS:
            assert col in df.columns, f"missing column: {col}"

    def test_non_empty(self):
        df = _mock_backtest_df()
        assert len(df) > 0

    def test_win_rate_in_valid_range(self):
        df = _mock_backtest_df()
        assert (df["win_rate"] >= 0).all() and (df["win_rate"] <= 1).all()

    def test_profit_factor_positive(self):
        df = _mock_backtest_df()
        assert (df["profit_factor"] > 0).all()


# ---------------------------------------------------------------------------
# load_backtest_data
# ---------------------------------------------------------------------------

class TestLoadBacktestData:
    def test_returns_mock_when_no_csv(self, tmp_path, monkeypatch):
        monkeypatch.setenv("BACKTEST_CSV", "")
        df = load_backtest_data(csv_path=str(tmp_path / "nonexistent.csv"))
        assert df["source"].iloc[0] == "mock"

    def test_loads_real_csv(self, tmp_path):
        csv = tmp_path / "bt.csv"
        mock = _mock_backtest_df()
        mock.to_csv(csv, index=False)
        df = load_backtest_data(csv_path=str(csv))
        assert df["source"].iloc[0] == "bt.csv"
        assert len(df) == len(mock)

    def test_adds_missing_columns_with_none(self, tmp_path):
        csv = tmp_path / "partial.csv"
        pd.DataFrame({"strategy": ["ict"], "symbol": ["BTCUSDT"],
                      "total_pnl": [1000.0], "run_date": ["2026-01-01"]}).to_csv(csv, index=False)
        df = load_backtest_data(csv_path=str(csv))
        for col in _REQUIRED_COLUMNS:
            assert col in df.columns

    def test_fallback_when_csv_unreadable(self, tmp_path):
        bad = tmp_path / "bad.csv"
        bad.write_text("not,valid,csv\n{broken}")
        df = load_backtest_data(csv_path=str(bad))
        # Should fall back to mock (bad.csv has no matching columns)
        assert isinstance(df, pd.DataFrame)
        assert len(df) > 0

    def test_env_var_path_takes_priority(self, tmp_path, monkeypatch):
        csv = tmp_path / "env.csv"
        mock = _mock_backtest_df()
        mock["strategy"] = "env_strategy"
        mock.to_csv(csv, index=False)
        monkeypatch.setenv("BACKTEST_CSV", str(csv))
        # Reload module-level candidates
        import importlib
        import src.web.backtest_ui as m
        importlib.reload(m)
        df = m.load_backtest_data()
        assert df["strategy"].iloc[0] == "env_strategy"
        monkeypatch.delenv("BACKTEST_CSV", raising=False)
        importlib.reload(m)


# ---------------------------------------------------------------------------
# filter_backtest_data
# ---------------------------------------------------------------------------

class TestFilterBacktestData:
    def _df(self):
        return _mock_backtest_df()

    def test_filter_by_strategy(self):
        filtered = filter_backtest_data(self._df(), strategy="ict")
        assert all(filtered["strategy"] == "ict")

    def test_filter_by_symbol(self):
        filtered = filter_backtest_data(self._df(), symbol="BTCUSDT")
        assert all(filtered["symbol"] == "BTCUSDT")

    def test_no_filter_returns_all(self):
        df = self._df()
        filtered = filter_backtest_data(df)
        assert len(filtered) == len(df)

    def test_date_from_filter(self):
        df = self._df()
        filtered = filter_backtest_data(df, date_from="2026-04-10")
        assert all(filtered["run_date"] >= "2026-04-10")

    def test_date_to_filter(self):
        df = self._df()
        filtered = filter_backtest_data(df, date_to="2026-04-05")
        assert all(filtered["run_date"] <= "2026-04-05")

    def test_combined_filters(self):
        filtered = filter_backtest_data(self._df(), strategy="vwap", symbol="BTCUSDT")
        assert all(filtered["strategy"] == "vwap")
        assert all(filtered["symbol"] == "BTCUSDT")

    def test_unknown_strategy_returns_empty(self):
        filtered = filter_backtest_data(self._df(), strategy="nonexistent")
        assert len(filtered) == 0


# ---------------------------------------------------------------------------
# build_equity_curve
# ---------------------------------------------------------------------------

class TestBuildEquityCurve:
    def _df(self):
        return pd.DataFrame({
            "strategy": ["ict", "ict", "vwap"],
            "run_date": ["2026-01-01", "2026-02-01", "2026-03-01"],
            "total_pnl": [100.0, 200.0, 50.0],
        })

    def test_returns_cumulative_pnl(self):
        curve = build_equity_curve(self._df())
        assert list(curve["cumulative_pnl"]) == [100.0, 300.0, 350.0]

    def test_filtered_by_strategy(self):
        curve = build_equity_curve(self._df(), strategy="ict")
        assert len(curve) == 2
        assert curve["cumulative_pnl"].iloc[-1] == 300.0

    def test_sorted_by_date(self):
        df = self._df().iloc[::-1].reset_index(drop=True)  # reverse order
        curve = build_equity_curve(df)
        assert curve["run_date"].is_monotonic_increasing

    def test_empty_df_returns_empty_curve(self):
        curve = build_equity_curve(pd.DataFrame())
        assert len(curve) == 0

    def test_has_run_date_and_cumulative_pnl_columns(self):
        curve = build_equity_curve(self._df())
        assert "run_date" in curve.columns
        assert "cumulative_pnl" in curve.columns


# ---------------------------------------------------------------------------
# summary_stats
# ---------------------------------------------------------------------------

class TestSummaryStats:
    def test_total_runs_count(self):
        df = _mock_backtest_df()
        stats = summary_stats(df)
        assert stats["total_runs"] == len(df)

    def test_avg_win_rate_in_range(self):
        df = _mock_backtest_df()
        stats = summary_stats(df)
        assert 0.0 <= stats["avg_win_rate"] <= 1.0

    def test_total_pnl_is_sum(self):
        df = _mock_backtest_df()
        stats = summary_stats(df)
        assert abs(stats["total_pnl"] - float(df["total_pnl"].sum())) < 0.01

    def test_empty_df_returns_zeros(self):
        stats = summary_stats(pd.DataFrame())
        assert stats["total_runs"] == 0
        assert stats["total_pnl"] == 0.0
