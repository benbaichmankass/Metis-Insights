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

import ast
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


# --------------------------------------------------------------------------
# 3b. the inverse-ETF entries are RESEARCH-ONLY, and that claim is falsifiable
# --------------------------------------------------------------------------
# SH / PSQ were added 2026-08-25 so the M15 alpaca short-proxy BACKTEST can
# obtain their history. alpaca is long-only with short proxies, permanently
# (standing operator directive) — a leg that would short SPY/QQQ instead goes
# long SH/PSQ. The evidence gate is explicit that the backtest runs BEFORE
# anything is built (BL-20260823-NO-INVERSE-ETF-INSTRUMENTS-DECLARED).
#
# The risk these tests exist to kill is a reader treating map membership as
# "wired". The source comment says it is not; a comment is a claim, so the two
# tests below MEASURE it. If the Tier-3 build later declares these instruments
# for real, `test_the_inverse_proxies_are_not_yet_tradeable` SHOULD fail — that
# is the signal to update it deliberately, in the PR that does the wiring.
_INVERSE_PROXIES = ("PSQ", "SH")


@pytest.mark.parametrize("sym", _INVERSE_PROXIES)
def test_the_inverse_proxies_resolve_to_themselves(sym):
    from ml.datasets.adapters.yfinance_offvm import _DEFAULT_TICKER_MAP
    assert _DEFAULT_TICKER_MAP[sym] == sym


@pytest.mark.parametrize("sym", _INVERSE_PROXIES)
def test_the_inverse_proxies_are_not_yet_tradeable(sym):
    """Fetchable != declared. Nothing may route to these until the gate clears."""
    instruments = (_ROOT / "config" / "instruments.yaml").read_text()
    strategies = (_ROOT / "config" / "strategies.yaml").read_text()
    # A crude substring test is the right strength here: it cannot miss a
    # declaration, and a false positive (the letters appearing inside another
    # token) would only make the test fail loudly rather than pass wrongly.
    for name, text in (("instruments.yaml", instruments), ("strategies.yaml", strategies)):
        for line in text.splitlines():
            bare = line.split("#", 1)[0]
            assert f"{sym}USD" not in bare and not _names_symbol(bare, sym), (
                f"{sym} now appears in config/{name} — if that is the deliberate "
                f"Tier-3 wiring, update this test in the same PR; if it is not, "
                f"an inverse instrument has been declared by accident"
            )


# The 2026-08-25 affordability candidates (map size 24 -> 74). Same contract as
# _INVERSE_PROXIES above and asserted the same way: fetchable so the probe can
# MEASURE them, declared nowhere. They exist because the standing answer in the
# record — "Nasdaq-100 has no sub-$100 ETF" — is PROSE, and RULE ONE says test
# the field rather than quote the comment.
#
# ⚠️ This list is asserted against the map, so it cannot silently drift out of
# sync with it: a candidate dropped from the map fails
# `test_the_affordability_candidates_resolve_to_themselves` rather than quietly
# stopping being covered by the not-tradeable check.
_AFFORDABILITY_CANDIDATES = (
    "VOO", "IVV", "SPTM",
    "QQQM", "ONEQ", "QQQJ", "XLK", "VGT", "FTEC", "IYW", "IGM", "QTEC", "SMH",
    "VTWO", "IJR", "VB", "SCHX",
    "GLDM", "IAU", "SGOL", "BAR", "AAAU", "OUNZ",
    "SIVR",
    "XLE", "XOP", "BNO", "USL", "VDE", "IEO", "OIH",
    "SHY", "IEI", "TLH", "SPTL", "VGLT", "GOVT", "BND", "SCHO", "SCHR",
    "GDXJ", "RING", "SGDM", "GOAU",
    "RWM", "DOG", "SEF", "DDG", "DGZ", "EUM",
)


@pytest.mark.parametrize("sym", _AFFORDABILITY_CANDIDATES)
def test_the_affordability_candidates_resolve_to_themselves(sym):
    from ml.datasets.adapters.yfinance_offvm import _DEFAULT_TICKER_MAP
    assert _DEFAULT_TICKER_MAP[sym] == sym


@pytest.mark.parametrize("sym", _AFFORDABILITY_CANDIDATES)
def test_the_affordability_candidates_are_not_yet_tradeable(sym):
    """Measurable != declared. Promoting any of these is Tier-3, on evidence.

    Written to go RED when that wiring lands — that is the signal to update it
    deliberately in the same PR, not a reason to loosen it.
    """
    instruments = (_ROOT / "config" / "instruments.yaml").read_text()
    strategies = (_ROOT / "config" / "strategies.yaml").read_text()
    for name, text in (("instruments.yaml", instruments), ("strategies.yaml", strategies)):
        for line in text.splitlines():
            bare = line.split("#", 1)[0]
            assert not _names_symbol(bare, sym), (
                f"{sym} now appears in config/{name} — if that is the deliberate "
                f"Tier-3 wiring, update this test in the same PR; if it is not, "
                f"a candidate instrument has been declared by accident"
            )


def test_no_affordability_candidate_collides_with_a_roster_symbol():
    """A candidate that shadows a live roster symbol would silently re-point it."""
    from ml.datasets.adapters.yfinance_offvm import _DEFAULT_TICKER_MAP
    roster = {"SPY", "QQQ", "GLD", "IWM", "TLT", "IEF", "SLV", "USO", "GDX",
              "SPLG", "IAUM", "SCHA", "QLD", "TQQQ", "SH", "PSQ", "TBF", "TBX"}
    collisions = sorted(set(_AFFORDABILITY_CANDIDATES) & roster)
    assert not collisions, f"candidate list shadows roster symbols: {collisions}"
    # and every candidate really is in the map (catches a typo in either list)
    missing = sorted(s for s in _AFFORDABILITY_CANDIDATES if s not in _DEFAULT_TICKER_MAP)
    assert not missing, f"candidates absent from the ticker map: {missing}"


def _names_symbol(line: str, sym: str) -> bool:
    """True when `line` uses `sym` as a symbol token rather than inside a word."""
    import re
    return re.search(rf"(?<![A-Za-z0-9_]){re.escape(sym)}(?![A-Za-z0-9_])", line) is not None


def test_the_puller_does_not_carry_its_own_ticker_map():
    """A fourth copy is how the existing three drift apart."""
    src = (_ROOT / "scripts" / "ops" / "fetch_backtest_candles.py").read_text()
    assert "yf_symbols" in src, "the puller no longer reaches the one home"
    assert "ES=F" not in src, "the puller re-declared a ticker literal"


def test_the_symbol_leaf_imports_nothing_local():
    """The by-path load in the puller works ONLY while `yf_symbols` is
    import-free. A relative import added here would re-create exactly the
    coupling the leaf exists to remove — and would do it silently, so the
    property is asserted rather than left to the module docstring."""
    leaf = _ROOT / "ml" / "datasets" / "adapters" / "yf_symbols.py"
    tree = ast.parse(leaf.read_text())
    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level:                      # any relative import at all
                offenders.append(f"relative import (level {node.level})")
            elif node.module not in {"__future__", "typing"}:
                offenders.append(f"from {node.module}")
        elif isinstance(node, ast.Import):
            for a in node.names:
                if a.name != "typing":
                    offenders.append(f"import {a.name}")
    assert not offenders, f"yf_symbols is no longer import-free: {offenders}"


def test_the_map_loads_without_the_ml_package():
    """The whole point: reading a dict of ticker strings must not execute
    `ml/datasets/__init__` -> `.registry` -> fourteen family builders (one of
    which imports `yaml`). Asserted by the ABSENCE of `ml.datasets` from
    sys.modules after the load, not by the load merely succeeding."""
    for name in [n for n in sys.modules if n.startswith("ml.datasets")]:
        del sys.modules[name]
    syms = _puller()._load_yf_symbols()
    assert syms._DEFAULT_TICKER_MAP["SPY"] == "SPY"
    assert "ml.datasets" not in sys.modules, (
        "the by-path load executed the ml.datasets package after all")


# --------------------------------------------------------------------------
# 3b. the failure STAGE is reported, not one label over three causes
# --------------------------------------------------------------------------
def test_an_unservable_interval_is_a_refusal_not_a_fetch_failure():
    """`diagnostic-provenance-guard` sub-class A: the lane's first proof run
    printed "yfinance fetch failed: No module named 'yaml'" for an error
    raised at import time, blaming a venue that was never contacted. A
    refusal and a dependency gap are both PRE-fetch and must say so."""
    mod = _puller()
    with pytest.raises(mod.YfRefused):
        mod.fetch_klines_yfinance("SPY", "240", 0, 86_400_000)


def test_an_unmapped_symbol_is_a_refusal_not_a_fetch_failure():
    mod = _puller()
    with pytest.raises(mod.YfRefused):
        mod.fetch_klines_yfinance("BTCUSDT", "D", 0, 86_400_000)


def test_the_three_stages_are_distinct_types_with_distinct_labels():
    mod = _puller()
    stages = (mod.YfDependencyMissing, mod.YfRefused, mod.YfFetchFailed)
    assert len({mod._YF_STAGE_LABEL[s] for s in stages}) == 3, (
        "two stages share a label, so the message cannot tell them apart")
    # Only the post-request stage may claim a fetch happened.
    assert "fetch failed" in mod._YF_STAGE_LABEL[mod.YfFetchFailed]
    for pre in (mod.YfDependencyMissing, mod.YfRefused):
        assert "fetch failed" not in mod._YF_STAGE_LABEL[pre], (
            f"{pre.__name__} claims a fetch that never left the process")


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


# --------------------------------------------------------------------------
# 5. the venue REFUSES an over-long intraday request; it does not clip it
# --------------------------------------------------------------------------
def _capture_yf_request(monkeypatch, mod, *, interval, days):
    """Run the lane with the network stubbed, returning the (start, end) it asked for."""
    seen = {}

    class _FakeYF:
        @staticmethod
        def download(*, tickers, interval, start, end, **kw):
            seen["start"], seen["end"] = start, end
            import pandas as pd
            return pd.DataFrame()          # empty is fine; we want the REQUEST

    monkeypatch.setitem(sys.modules, "yfinance", _FakeYF)
    end_ms = 1_787_500_000_000
    start_ms = end_ms - days * 86_400_000
    mod.fetch_klines_yfinance("SPY", interval, start_ms, end_ms)
    return seen


def test_an_overlong_intraday_request_is_clamped_not_passed_through(monkeypatch, capsys):
    """MEASURED (proof run 32734360738): asking Yahoo for 1001 d of SPY 1h
    returns ZERO rows — "The requested range must be within the last 730 days".
    It refuses; it does not truncate. The lane must therefore clamp the START
    rather than warn that a truncation will happen and pass the request on."""
    mod = _puller()
    seen = _capture_yf_request(monkeypatch, mod, interval="60", days=1001)
    span = (seen["end"] - seen["start"]).days
    assert span <= 730, f"asked the venue for {span} d, above its 730 d ceiling"
    assert span >= 700, f"clamped far below the cap ({span} d) — history thrown away"


def test_a_request_inside_the_cap_is_left_alone(monkeypatch):
    """The clamp must not shrink a window the venue would have served."""
    mod = _puller()
    seen = _capture_yf_request(monkeypatch, mod, interval="60", days=400)
    assert (seen["end"] - seen["start"]).days == 400


def test_daily_is_never_clamped(monkeypatch):
    """`1d` is uncapped (max_history_days -> None); clamping it would silently
    discard decades of history the venue is willing to serve."""
    mod = _puller()
    seen = _capture_yf_request(monkeypatch, mod, interval="D", days=5000)
    assert (seen["end"] - seen["start"]).days == 5000


def test_the_clamp_is_announced_not_silent(monkeypatch, capsys):
    """A silently shortened window reads exactly like a complete one."""
    mod = _puller()
    _capture_yf_request(monkeypatch, mod, interval="60", days=1001)
    err = capsys.readouterr().err
    assert "clamped" in err.lower()
    assert "PARTIAL" in err
    assert "WILL be truncated" not in err, (
        "still predicting a truncation the venue does not perform")
