"""S-064 prereq — tests for per-symbol liquidity-state writer.

Covers ``build_state`` (pure function), ``write_state`` (atomic
multi-symbol merge), and ``read_state`` (graceful empty-file
behaviour). The pipeline-side hook is a one-liner wrapped in
``try/except``; its only contract is "never raise into the tick
loop", verified here by feeding malformed input.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.runtime import liquidity_state


def _candles_with_equal_highs() -> pd.DataFrame:
    """A 30-bar DataFrame engineered to produce two equal-highs at 100.0."""
    idx = pd.date_range("2026-05-01", periods=30, freq="15min", tz="UTC")
    # Build a price series with two clear swing highs at 100.0
    # (bar 7 + bar 17), both surrounded by 5 lower bars on each side.
    closes = np.linspace(80, 90, 30)
    highs = closes + 1.0
    lows = closes - 1.0
    # Plant the equal swing highs — left/right windows must be lower.
    highs[7] = 100.0
    highs[17] = 100.0
    # And a deep wick that sweeps the level on bar 25.
    highs[25] = 101.5
    # Lows + close consistent with highs.
    closes = np.minimum(closes, highs - 0.5)
    df = pd.DataFrame(
        {
            "open": closes - 0.1,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": np.full(30, 1000.0),
        },
        index=idx,
    )
    return df


def _candles_with_equal_lows() -> pd.DataFrame:
    """Mirror of equal-highs fixture for the SSL path."""
    idx = pd.date_range("2026-05-01", periods=30, freq="15min", tz="UTC")
    closes = np.linspace(120, 110, 30)
    highs = closes + 1.0
    lows = closes - 1.0
    lows[7] = 50.0
    lows[17] = 50.0
    lows[25] = 48.5
    df = pd.DataFrame(
        {
            "open": closes + 0.1,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": np.full(30, 1000.0),
        },
        index=idx,
    )
    return df


def _flat_candles() -> pd.DataFrame:
    """20 bars of identical price — no swings, no liquidity zones."""
    idx = pd.date_range("2026-05-01", periods=20, freq="15min", tz="UTC")
    df = pd.DataFrame(
        {
            "open": np.full(20, 100.0),
            "high": np.full(20, 100.0),
            "low": np.full(20, 100.0),
            "close": np.full(20, 100.0),
            "volume": np.full(20, 1.0),
        },
        index=idx,
    )
    return df


# ---------------------------------------------------------------------------
# build_state
# ---------------------------------------------------------------------------


def test_build_state_detects_equal_highs_and_marks_sweep():
    df = _candles_with_equal_highs()
    now = datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc)

    state = liquidity_state.build_state("BTCUSDT", df, now_utc=now)

    assert state["schema_version"] == liquidity_state.SCHEMA_VERSION
    assert state["symbol"] == "BTCUSDT"
    assert state["as_of"] == "2026-05-01T12:00:00Z"
    assert state["equal_lows"] == []
    assert len(state["equal_highs"]) >= 1

    pool = state["equal_highs"][0]
    assert pool["side"] == "buy"
    assert pool["price"] == pytest.approx(100.0)
    assert pool["touches"] >= 2
    assert pool["swept"] is True
    assert pool["sweep_time"] is not None
    assert pool["first_touch"].endswith("Z")
    assert pool["last_touch"].endswith("Z")

    assert len(state["recent_sweeps"]) >= 1
    sweep = state["recent_sweeps"][0]
    assert sweep["side"] == "buy"
    assert sweep["price"] == pytest.approx(100.0)


def test_build_state_detects_equal_lows():
    df = _candles_with_equal_lows()
    state = liquidity_state.build_state("ETHUSDT", df)
    assert state["equal_highs"] == []
    assert len(state["equal_lows"]) >= 1
    pool = state["equal_lows"][0]
    assert pool["side"] == "sell"
    assert pool["price"] == pytest.approx(50.0)


def test_build_state_returns_empty_arrays_when_no_swings():
    df = _flat_candles()
    state = liquidity_state.build_state("BTCUSDT", df)
    assert state["equal_highs"] == []
    assert state["equal_lows"] == []
    assert state["recent_sweeps"] == []
    # Schema invariants still hold on the empty path.
    assert state["symbol"] == "BTCUSDT"
    assert state["schema_version"] == liquidity_state.SCHEMA_VERSION
    assert state["as_of"].endswith("Z")


def test_build_state_caps_pools_per_side(monkeypatch):
    monkeypatch.setattr(liquidity_state, "MAX_POOLS_PER_SIDE", 2)
    df = _candles_with_equal_highs()
    state = liquidity_state.build_state("BTCUSDT", df)
    assert len(state["equal_highs"]) <= 2


# ---------------------------------------------------------------------------
# write_state — atomic per-symbol merge
# ---------------------------------------------------------------------------


def test_write_state_creates_file_with_single_symbol(tmp_path):
    target = tmp_path / "liquidity_state.json"
    df = _candles_with_equal_highs()
    liquidity_state.write_state("BTCUSDT", df, path=target)

    assert target.exists()
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert "BTCUSDT" in payload
    assert payload["BTCUSDT"]["symbol"] == "BTCUSDT"
    assert len(payload["BTCUSDT"]["equal_highs"]) >= 1


def test_write_state_preserves_other_symbols(tmp_path):
    target = tmp_path / "liquidity_state.json"
    target.write_text(
        json.dumps({
            "ETHUSDT": {
                "schema_version": 1,
                "symbol": "ETHUSDT",
                "as_of": "2026-04-30T00:00:00Z",
                "equal_highs": [],
                "equal_lows": [],
                "recent_sweeps": [],
            },
        }),
        encoding="utf-8",
    )

    df = _candles_with_equal_highs()
    liquidity_state.write_state("BTCUSDT", df, path=target)

    payload = json.loads(target.read_text(encoding="utf-8"))
    assert set(payload.keys()) == {"ETHUSDT", "BTCUSDT"}
    assert payload["ETHUSDT"]["as_of"] == "2026-04-30T00:00:00Z"  # untouched
    assert payload["BTCUSDT"]["symbol"] == "BTCUSDT"


def test_write_state_replaces_existing_symbol_slot(tmp_path):
    target = tmp_path / "liquidity_state.json"
    target.write_text(
        json.dumps({
            "BTCUSDT": {
                "schema_version": 1,
                "symbol": "BTCUSDT",
                "as_of": "2026-04-30T00:00:00Z",
                "equal_highs": [{"side": "buy", "price": 999.0,
                                  "touches": 99, "first_touch": None,
                                  "last_touch": None, "swept": False,
                                  "sweep_time": None}],
                "equal_lows": [],
                "recent_sweeps": [],
            },
        }),
        encoding="utf-8",
    )

    df = _candles_with_equal_highs()
    liquidity_state.write_state("BTCUSDT", df, path=target)

    payload = json.loads(target.read_text(encoding="utf-8"))
    # Old fixture pool is gone; new detection wrote fresh content.
    prices = [p["price"] for p in payload["BTCUSDT"]["equal_highs"]]
    assert 999.0 not in prices


def test_write_state_swallows_exceptions(tmp_path, caplog):
    """Tick-loop contract: never raise. Bad input must log + return."""
    target = tmp_path / "liquidity_state.json"
    # Pass a non-DataFrame; build_state will explode at .copy() / detector.
    liquidity_state.write_state("BTCUSDT", "not a dataframe", path=target)
    # File must NOT exist (write was skipped after the exception).
    assert not target.exists()


def test_write_state_atomic_replace_no_partial_file(tmp_path, monkeypatch):
    """During a write, the .tmp file is the staging slot — the canonical
    file only flips via os.replace, so a concurrent reader sees old or new,
    never a half-written blob."""
    target = tmp_path / "liquidity_state.json"
    df = _candles_with_equal_highs()
    liquidity_state.write_state("BTCUSDT", df, path=target)

    # The temp sibling must be cleaned up by os.replace.
    tmp_sibling = target.with_suffix(target.suffix + ".tmp")
    assert not tmp_sibling.exists()
    assert target.exists()


# ---------------------------------------------------------------------------
# read_state
# ---------------------------------------------------------------------------


def test_read_state_returns_empty_dict_when_missing(tmp_path):
    assert liquidity_state.read_state(tmp_path / "nope.json") == {}


def test_read_state_returns_empty_dict_on_malformed_file(tmp_path):
    target = tmp_path / "broken.json"
    target.write_text("not json", encoding="utf-8")
    assert liquidity_state.read_state(target) == {}


def test_read_state_returns_empty_dict_when_root_is_not_object(tmp_path):
    target = tmp_path / "broken.json"
    target.write_text("[1, 2, 3]", encoding="utf-8")
    assert liquidity_state.read_state(target) == {}


def test_read_state_round_trips_written_payload(tmp_path):
    target = tmp_path / "liquidity_state.json"
    df = _candles_with_equal_highs()
    liquidity_state.write_state("BTCUSDT", df, path=target)
    state = liquidity_state.read_state(target)
    assert "BTCUSDT" in state
    assert state["BTCUSDT"]["symbol"] == "BTCUSDT"
