"""The non-crypto candle lane, and the daily-interval bug it sat behind.

Two independent defects blocked the 25 `no_free_lane_candle_feed` bracket-
geometry cells, and only one of them was the missing source:

1. `_interval_ms` CRASHED on a bare ``D``/``W`` — `int(""[:-1])` — while the
   `--interval` help advertised ``.../240/D/W``. ⚠️ Scope, stated precisely:
   that function is on the BYBIT path only, so daily was NOT globally broken.
   What held is that **no single spelling worked on both sources** — bare
   ``D``/``W`` crashed Bybit but resolved on the archive, and ``1D``/``1W``
   computed fine but had no archive label. Under ``--source auto`` a bare ``D``
   burned the Bybit arm on an exception and fell through to Binance: a venue
   chosen by a stack trace rather than a decision.
2. Both real sources are CRYPTO archives, so no lane could serve the 18
   equity/ETF/futures symbols at all.

These tests pin the behaviours a data refresh cannot move.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _puller():
    spec = importlib.util.spec_from_file_location(
        "_fetch_bt", _ROOT / "scripts" / "ops" / "fetch_backtest_candles.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --------------------------------------------------------------------------
# 1. the daily/weekly interval bug
# --------------------------------------------------------------------------
@pytest.mark.parametrize("code,want_ms", [
    ("5", 300_000), ("60", 3_600_000), ("240", 14_400_000),
    ("D", 86_400_000), ("1D", 86_400_000),      # a BARE code means one unit
    ("W", 604_800_000), ("1W", 604_800_000),
    ("7D", 7 * 86_400_000),                      # an explicit count still works
])
def test_interval_ms_accepts_both_spellings(code, want_ms):
    assert _puller()._interval_ms(code) == want_ms


def test_bare_daily_does_not_crash():
    """The exact regression: `int(""[:-1])` raised ValueError on "D"."""
    assert _puller()._interval_ms("D") == 86_400_000


@pytest.mark.parametrize("code", ["D", "1D", "W", "1W"])
def test_every_daily_spelling_reaches_a_binance_label(code):
    """A code `_interval_ms` accepts must also RESOLVE on the archive source.

    Computing a duration for a code the source cannot name is how the daily
    feed was half-working in two different ways at once.
    """
    m = _puller()
    m._interval_ms(code)                       # must not raise
    assert m._BYBIT_TO_BINANCE_INTERVAL.get(code.upper()) is not None


# --------------------------------------------------------------------------
# 2. the yfinance lane — its REFUSALS are the contract
# --------------------------------------------------------------------------
@pytest.mark.parametrize("interval", ["240", "120", "3", "30"])
def test_unsupported_interval_is_refused_not_coerced(interval):
    """A 4h request quietly served as 1h is a wrong backtest that looks fine."""
    with pytest.raises(RuntimeError, match="cannot serve interval"):
        _puller().fetch_klines_yfinance("SPY", interval, 0, 86_400_000)


def test_unmapped_symbol_is_refused_not_passed_through():
    """An unlisted symbol is UNKNOWN, not 'probably fine as-is'."""
    with pytest.raises(RuntimeError, match="no yfinance ticker mapped"):
        _puller().fetch_klines_yfinance("NOTASYMBOL", "D", 0, 86_400_000)


def test_yfinance_is_never_reached_by_the_auto_chain():
    """`auto` exists to survive a Bybit geoblock, not to substitute venues.

    A crypto symbol silently answered from Yahoo would be a DIFFERENT
    instrument than the one the caller asked for.
    """
    src = (_ROOT / "scripts" / "ops" / "fetch_backtest_candles.py").read_text()
    assert 'args.source == "yfinance"' in src
    assert 'args.source in ("auto", "yfinance")' not in src


# --------------------------------------------------------------------------
# 3. the ticker map has ONE home, and it covers the blocked population
# --------------------------------------------------------------------------
# The 18 distinct symbols behind the 25 `no_free_lane_candle_feed` cells.
_BLOCKED_SYMBOLS = frozenset({
    "GDX", "GLD", "IAUM", "IEF", "IWM", "MES", "MGC", "MHG", "QLD",
    "QQQ", "SCHA", "SLV", "SPLG", "SPY", "TLT", "TQQQ", "USO", "XAUUSD",
})


def test_every_blocked_leg_symbol_resolves():
    from ml.datasets.adapters.yfinance_offvm import known_symbols
    missing = _BLOCKED_SYMBOLS - known_symbols()
    assert not missing, f"unmapped blocked-leg symbols: {sorted(missing)}"


@pytest.mark.parametrize("sym,want", [
    ("MES", "ES=F"), ("MGC", "GC=F"), ("XAUUSD", "GC=F"), ("MHG", "HG=F"),
])
def test_futures_translate_rather_than_pass_through(sym, want):
    from ml.datasets.adapters.yfinance_offvm import _DEFAULT_TICKER_MAP
    assert _DEFAULT_TICKER_MAP[sym] == want


def test_the_puller_does_not_carry_its_own_ticker_map():
    """A fourth copy is how the existing three drift apart."""
    src = (_ROOT / "scripts" / "ops" / "fetch_backtest_candles.py").read_text()
    assert "yfinance_offvm import" in src
    assert "ES=F" not in src, "the puller re-declared a ticker literal"


# --------------------------------------------------------------------------
# 4. the history cap is DATA, and an unknown timeframe is not 'uncapped'
# --------------------------------------------------------------------------
def test_daily_is_uncapped_and_intraday_is_not():
    from ml.datasets.adapters.yfinance_offvm import max_history_days
    assert max_history_days("1d") is None
    assert max_history_days("1h") == 730
    assert max_history_days("15m") == 60


def test_unknown_timeframe_raises_rather_than_claiming_uncapped():
    """Returning None for a bar we cannot serve would make a truncated span
    read as a complete one — the failure this whole file is about."""
    from ml.datasets.adapters.yfinance_offvm import max_history_days
    with pytest.raises(KeyError):
        max_history_days("4h")
