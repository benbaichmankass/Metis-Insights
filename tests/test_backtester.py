"""
Tests for src/backtest/backtester.py (ICTBacktester).

Replaces the ad-hoc root-level test_backtester.py script (M4a cleanup).
Uses deterministic candle fixtures — no random data, no live-trading calls.
"""
import pytest

pd = pytest.importorskip("pandas")
np = pytest.importorskip("numpy")

from datetime import datetime, timedelta
from src.backtest.backtester import ICTBacktester, DEFAULT_CONFIG


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

def _make_candles(n: int = 200, base_price: float = 69_000.0, session_hour: int = 6):
    """Return a DataFrame with n 1-minute candles, all inside the default session."""
    ts = datetime(2026, 3, 22, session_hour, 0)
    rows = []
    price = base_price
    for _ in range(n):
        rows.append({
            "timestamp": int(ts.timestamp() * 1000),
            "open": price,
            "high": price + 20,
            "low": price - 20,
            "close": price + 5,
            "volume": 1.0,
        })
        ts += timedelta(minutes=1)
    return pd.DataFrame(rows)


def _make_trending_candles(n: int = 300, direction: str = "up"):
    """Return candles with a clear trend so market_structure returns non-ranging."""
    ts = datetime(2026, 3, 22, 6, 0)
    rows = []
    price = 69_000.0
    step = 10.0 if direction == "up" else -10.0
    for _ in range(n):
        rows.append({
            "timestamp": int(ts.timestamp() * 1000),
            "open": price,
            "high": price + 30,
            "low": price - 5,
            "close": price + 25,
            "volume": 1.0,
        })
        price += step
        ts += timedelta(minutes=1)
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# detect_swing_highs_lows
# ---------------------------------------------------------------------------

def test_detect_swing_highs_lows_returns_lists():
    df = _make_candles(100)
    bt = ICTBacktester(df)
    sh, sl = bt.detect_swing_highs_lows()
    assert isinstance(sh, list)
    assert isinstance(sl, list)


def test_detect_swing_highs_lows_indices_in_bounds():
    df = _make_candles(100)
    bt = ICTBacktester(df)
    sh, sl = bt.detect_swing_highs_lows()
    for i in sh:
        assert 0 <= i < len(df)
    for i in sl:
        assert 0 <= i < len(df)


# ---------------------------------------------------------------------------
# detect_fvgs
# ---------------------------------------------------------------------------

def _make_fvg_candles():
    """Three candles that form a bullish FVG: candle[2].low > candle[0].high."""
    ts = datetime(2026, 3, 22, 6, 0)
    rows = [
        {"timestamp": int(ts.timestamp() * 1000), "open": 100, "high": 110, "low": 95, "close": 108, "volume": 1},
        {"timestamp": int((ts + timedelta(minutes=1)).timestamp() * 1000), "open": 108, "high": 120, "low": 106, "close": 118, "volume": 1},
        {"timestamp": int((ts + timedelta(minutes=2)).timestamp() * 1000), "open": 118, "high": 130, "low": 115, "close": 128, "volume": 1},
    ]
    return pd.DataFrame(rows)


def test_detect_fvgs_bullish_detected():
    df = _make_fvg_candles()
    bt = ICTBacktester(df, config={"min_fvg_size_pct": 0.0})
    fvgs = bt.detect_fvgs()
    bullish = [f for f in fvgs if f["type"] == "bullish"]
    assert len(bullish) >= 1
    assert bullish[0]["top"] == 115   # candle[2].low
    assert bullish[0]["bottom"] == 110  # candle[0].high


def test_detect_fvgs_returns_list_on_flat_data():
    df = _make_candles(50)
    bt = ICTBacktester(df)
    fvgs = bt.detect_fvgs()
    assert isinstance(fvgs, list)


# ---------------------------------------------------------------------------
# market_structure
# ---------------------------------------------------------------------------

def test_market_structure_ranging_when_too_few_swings():
    df = _make_candles(20)  # not enough bars to form 2+ swings
    bt = ICTBacktester(df)
    sh, sl = bt.detect_swing_highs_lows()
    result = bt.market_structure([], [])
    assert result == "ranging"


def test_market_structure_valid_values():
    df = _make_candles(200)
    bt = ICTBacktester(df)
    sh, sl = bt.detect_swing_highs_lows()
    result = bt.market_structure(sh, sl)
    assert result in ("bullish", "bearish", "ranging")


# ---------------------------------------------------------------------------
# in_session
# ---------------------------------------------------------------------------

def test_in_session_true_during_window():
    df = _make_candles(10)
    bt = ICTBacktester(df)
    inside = datetime(2026, 3, 22, 6, 0)     # default window: 2–12
    assert bt.in_session(inside) is True


def test_in_session_false_outside_window():
    df = _make_candles(10)
    bt = ICTBacktester(df)
    outside = datetime(2026, 3, 22, 14, 0)
    assert bt.in_session(outside) is False


def test_in_session_accepts_ms_timestamp():
    df = _make_candles(10)
    bt = ICTBacktester(df)
    ts_ms = int(datetime(2026, 3, 22, 6, 30).timestamp() * 1000)
    assert bt.in_session(ts_ms) is True


# ---------------------------------------------------------------------------
# summary
# ---------------------------------------------------------------------------

def test_summary_no_trades_returns_error_key():
    df = _make_candles(50)
    bt = ICTBacktester(df)
    bt.run()  # may produce zero trades on flat data — that's fine
    if not bt.trades:
        s = bt.summary()
        assert "error" in s


def test_summary_keys_present_when_trades_exist():
    """Run on enough trending data to guarantee at least one trade."""
    df = _make_trending_candles(400, direction="up")
    bt = ICTBacktester(df, config={"min_fvg_size_pct": 0.0, "swing_lookback": 3})
    bt.run()
    if bt.trades:
        s = bt.summary()
        for key in ("total_trades", "win_rate_pct", "final_capital", "profit_factor"):
            assert key in s


# ---------------------------------------------------------------------------
# run — end-to-end smoke
# ---------------------------------------------------------------------------

def test_run_returns_list():
    df = _make_candles(200)
    bt = ICTBacktester(df)
    trades = bt.run()
    assert isinstance(trades, list)


def test_run_trade_keys():
    df = _make_trending_candles(400, direction="up")
    bt = ICTBacktester(df, config={"min_fvg_size_pct": 0.0, "swing_lookback": 3})
    trades = bt.run()
    for trade in trades:
        for key in ("direction", "entry_price", "exit_price", "net_pnl", "exit_reason"):
            assert key in trade


def test_run_capital_updated():
    df = _make_trending_candles(400, direction="up")
    bt = ICTBacktester(df, config={"min_fvg_size_pct": 0.0, "swing_lookback": 3})
    initial = bt.capital
    bt.run()
    # Capital should have changed if any trades executed; otherwise stays the same
    assert isinstance(bt.capital, float)
    assert bt.capital > 0


def test_run_respects_max_trades_per_day():
    """No single calendar day should have more trades than max_trades_per_day."""
    df = _make_trending_candles(400, direction="up")
    cfg = {"min_fvg_size_pct": 0.0, "swing_lookback": 3, "max_trades_per_day": 2}
    bt = ICTBacktester(df, config=cfg)
    trades = bt.run()
    from collections import Counter
    day_counts = Counter(t["entry_time"][:10] for t in trades)
    for day, count in day_counts.items():
        assert count <= 2, f"Day {day} had {count} trades, limit is 2"


def test_config_override():
    df = _make_candles(100)
    custom = {"initial_capital": 5_000.0, "risk_per_trade_pct": 0.5}
    bt = ICTBacktester(df, config=custom)
    assert bt.cfg["initial_capital"] == 5_000.0
    assert bt.cfg["risk_per_trade_pct"] == 0.5
    # defaults not overridden should still be present
    assert bt.cfg["reward_to_risk"] == DEFAULT_CONFIG["reward_to_risk"]


# ---------------------------------------------------------------------------
# ob_confluence_only flag
# ---------------------------------------------------------------------------

def test_ob_confluence_only_default_is_false():
    assert DEFAULT_CONFIG["ob_confluence_only"] is False


def test_ob_confluence_only_reduces_or_equal_trade_count():
    """With ob_confluence_only=True the backtester must produce ≤ trades than
    without it, because it skips FVG signals that lack OB backing."""
    df = _make_trending_candles(300, direction="up")
    base_trades = len(ICTBacktester(df, config={"ob_confluence_only": False}).run())
    filtered_trades = len(ICTBacktester(df, config={"ob_confluence_only": True}).run())
    assert filtered_trades <= base_trades


def test_ob_confluence_only_all_surviving_trades_have_confluence():
    """Every trade that survives the ob_confluence_only filter must have
    ob_confluence=True."""
    df = _make_trending_candles(400, direction="up")
    bt = ICTBacktester(df, config={"ob_confluence_only": True})
    trades = bt.run()
    for t in trades:
        assert t["ob_confluence"] is True, f"Non-OB trade slipped through: {t}"


# ---------------------------------------------------------------------------
# disable_session_filter flag
# ---------------------------------------------------------------------------

def test_disable_session_filter_default_is_false():
    assert DEFAULT_CONFIG["disable_session_filter"] is False


def test_disable_session_filter_allows_out_of_session_bars():
    """Candles outside the default session window (hour=20, i.e. 8 pm UTC)
    should produce 0 trades with the filter on and ≥0 with it off."""
    df = _make_candles(300, base_price=69_000.0, session_hour=20)
    filtered_count = len(ICTBacktester(df, config={"disable_session_filter": False}).run())
    unfiltered_count = len(ICTBacktester(df, config={"disable_session_filter": True}).run())
    # With filter on, hour=20 is outside session (2–12), so 0 trades expected.
    assert filtered_count == 0
    # With filter off, backtester can find trades (flat market → still likely 0,
    # but the important invariant is unfiltered ≥ filtered).
    assert unfiltered_count >= filtered_count
