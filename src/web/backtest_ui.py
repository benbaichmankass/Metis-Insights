"""Backtesting UI — Streamlit web app (S-011 PR #3).

Run locally:
    streamlit run src/web/backtest_ui.py

Data sources (in priority order):
  1. BACKTEST_CSV env var → explicit CSV path
  2. data/backtests.csv in repo root
  3. data/backtest_candles.csv (legacy) in repo root
  4. Mock data (always works, no dependencies)

The data loading and formatting helpers are importable for unit tests
without importing Streamlit.
"""
from __future__ import annotations

import os
from typing import List, Optional

import pandas as pd

# ---------------------------------------------------------------------------
# Data loading helpers (importable without Streamlit)
# ---------------------------------------------------------------------------

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

_CSV_CANDIDATES = [
    os.environ.get("BACKTEST_CSV", ""),
    os.path.join(_REPO_ROOT, "data", "backtests.csv"),
    os.path.join(_REPO_ROOT, "data", "backtest_candles.csv"),
]

_REQUIRED_COLUMNS = ["strategy", "symbol", "win_rate", "profit_factor",
                     "total_trades", "max_drawdown_pct", "total_pnl", "run_date"]


def _mock_backtest_df() -> pd.DataFrame:
    """Return a minimal mock DataFrame when no CSV is available."""
    return pd.DataFrame({
        "strategy": ["ict", "vwap", "killzone", "ict", "vwap"],
        "symbol": ["BTCUSDT", "BTCUSDT", "ETHUSDT", "ETHUSDT", "ETHUSDT"],
        "win_rate": [0.62, 0.55, 0.58, 0.60, 0.52],
        "profit_factor": [1.8, 1.4, 1.6, 1.7, 1.3],
        "total_trades": [120, 85, 95, 110, 78],
        "max_drawdown_pct": [0.08, 0.12, 0.10, 0.09, 0.14],
        "total_pnl": [1850.0, 940.0, 1120.0, 1600.0, 760.0],
        "run_date": ["2026-04-01", "2026-04-01", "2026-04-01",
                     "2026-04-15", "2026-04-15"],
        "note": ["mock"] * 5,
    })


def load_backtest_data(csv_path: Optional[str] = None) -> pd.DataFrame:
    """Load backtest results from CSV, falling back to mock data.

    Parameters
    ----------
    csv_path : str, optional
        Explicit path to a CSV file.  When None, the standard candidates
        are tried in order.

    Returns
    -------
    pd.DataFrame
        DataFrame with at minimum the ``_REQUIRED_COLUMNS`` columns.
        A ``"source"`` column indicates whether data is real or mock.
    """
    candidates = [csv_path] if csv_path else _CSV_CANDIDATES
    for path in candidates:
        if not path:
            continue
        if os.path.exists(path):
            try:
                df = pd.read_csv(path)
                # Add any missing required columns with NA
                for col in _REQUIRED_COLUMNS:
                    if col not in df.columns:
                        df[col] = None
                df["source"] = os.path.basename(path)
                return df
            except Exception:
                continue
    df = _mock_backtest_df()
    df["source"] = "mock"
    return df


def build_equity_curve(df: pd.DataFrame, strategy: Optional[str] = None) -> pd.DataFrame:
    """Compute a cumulative equity curve from the backtest DataFrame.

    Parameters
    ----------
    df : pd.DataFrame
        Backtest results with ``total_pnl`` and ``run_date`` columns.
    strategy : str, optional
        When set, filter to this strategy only.

    Returns
    -------
    pd.DataFrame
        Two-column frame: ``run_date``, ``cumulative_pnl``.
    """
    filtered = df.copy()
    if strategy and "strategy" in filtered.columns:
        filtered = filtered[filtered["strategy"] == strategy]
    if "total_pnl" not in filtered.columns or filtered.empty:
        return pd.DataFrame({"run_date": [], "cumulative_pnl": []})
    filtered = filtered.sort_values("run_date")
    filtered["cumulative_pnl"] = filtered["total_pnl"].cumsum()
    return filtered[["run_date", "cumulative_pnl"]].reset_index(drop=True)


def filter_backtest_data(
    df: pd.DataFrame,
    strategy: Optional[str] = None,
    symbol: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> pd.DataFrame:
    """Apply user-selected filters to the backtest DataFrame."""
    out = df.copy()
    if strategy and "strategy" in out.columns:
        out = out[out["strategy"] == strategy]
    if symbol and "symbol" in out.columns:
        out = out[out["symbol"] == symbol]
    if date_from and "run_date" in out.columns:
        out = out[out["run_date"] >= date_from]
    if date_to and "run_date" in out.columns:
        out = out[out["run_date"] <= date_to]
    return out.reset_index(drop=True)


def summary_stats(df: pd.DataFrame) -> dict:
    """Return aggregate summary stats for the metrics bar."""
    if df.empty:
        return {"total_runs": 0, "avg_win_rate": 0.0,
                "avg_pf": 0.0, "total_pnl": 0.0}
    return {
        "total_runs": len(df),
        "avg_win_rate": round(float(df["win_rate"].mean()), 3) if "win_rate" in df else 0.0,
        "avg_pf": round(float(df["profit_factor"].mean()), 2) if "profit_factor" in df else 0.0,
        "total_pnl": round(float(df["total_pnl"].sum()), 2) if "total_pnl" in df else 0.0,
    }


# ---------------------------------------------------------------------------
# Streamlit app entry point
# ---------------------------------------------------------------------------

def run_app() -> None:
    """Launch the Streamlit backtesting dashboard."""
    import streamlit as st

    st.set_page_config(page_title="ICT Backtesting UI", layout="wide")
    st.title("📈 ICT Trading Bot — Backtesting Dashboard")

    df = load_backtest_data()
    is_mock = df["source"].iloc[0] == "mock" if not df.empty else True
    if is_mock:
        st.info("ℹ️ Showing mock data. Place a real CSV at `data/backtests.csv` or set `BACKTEST_CSV` env var.")

    # Sidebar filters
    st.sidebar.header("Filters")
    strategies: List[str] = ["All"] + sorted(df["strategy"].dropna().unique().tolist()) if "strategy" in df.columns else ["All"]
    symbols: List[str] = ["All"] + sorted(df["symbol"].dropna().unique().tolist()) if "symbol" in df.columns else ["All"]

    sel_strategy = st.sidebar.selectbox("Strategy", strategies)
    sel_symbol = st.sidebar.selectbox("Symbol", symbols)
    sel_date_from = st.sidebar.text_input("Date from (YYYY-MM-DD)", "")
    sel_date_to = st.sidebar.text_input("Date to (YYYY-MM-DD)", "")

    filtered = filter_backtest_data(
        df,
        strategy=None if sel_strategy == "All" else sel_strategy,
        symbol=None if sel_symbol == "All" else sel_symbol,
        date_from=sel_date_from or None,
        date_to=sel_date_to or None,
    )

    # Summary metrics bar
    stats = summary_stats(filtered)
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Backtest Runs", stats["total_runs"])
    col2.metric("Avg Win Rate", f"{stats['avg_win_rate']:.1%}")
    col3.metric("Avg Profit Factor", f"{stats['avg_pf']:.2f}")
    col4.metric("Total PnL", f"${stats['total_pnl']:,.0f}")

    # Equity curve
    st.subheader("Equity Curve")
    equity = build_equity_curve(filtered, strategy=None if sel_strategy == "All" else sel_strategy)
    if not equity.empty:
        try:
            import plotly.express as px
            fig = px.line(equity, x="run_date", y="cumulative_pnl",
                          title="Cumulative PnL", labels={"cumulative_pnl": "PnL ($)", "run_date": "Date"})
            st.plotly_chart(fig, use_container_width=True)
        except ImportError:
            st.line_chart(equity.set_index("run_date")["cumulative_pnl"])
    else:
        st.write("No data for equity curve.")

    # Results table
    st.subheader("Backtest Results")
    display_cols = [c for c in _REQUIRED_COLUMNS if c in filtered.columns]
    if display_cols:
        st.dataframe(filtered[display_cols].style.format({
            "win_rate": "{:.1%}",
            "profit_factor": "{:.2f}",
            "max_drawdown_pct": "{:.1%}",
            "total_pnl": "${:,.0f}",
        }), use_container_width=True)
    else:
        st.dataframe(filtered, use_container_width=True)

    st.caption("Run `streamlit run src/web/backtest_ui.py` to launch locally.")


if __name__ == "__main__":
    run_app()
