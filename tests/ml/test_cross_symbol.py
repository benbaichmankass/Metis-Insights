"""Tests for cross-symbol setup_candidates builds (S-MLOPT-S8)."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ml.datasets.families.setup_candidates import (
    SetupCandidatesBuilder,
    _resolve_market_raw_paths,
)


def _write_market_raw(root: Path, symbol: str, closes: list[float]) -> Path:
    ddir = root / "market_raw" / symbol / "1h" / "v1"
    ddir.mkdir(parents=True, exist_ok=True)
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with (ddir / "data.jsonl").open("w", encoding="utf-8") as fh:
        prev = closes[0]
        for i, c in enumerate(closes):
            fh.write(json.dumps({
                "ts": (base + timedelta(hours=i)).isoformat(), "symbol": symbol,
                "timeframe": "1h", "open": float(prev),
                "high": float(max(prev, c) * 1.004), "low": float(min(prev, c) * 0.996),
                "close": float(c), "volume": 1.0, "source": "test",
            }) + "\n")
            prev = c
    return ddir


def _trend(n: int, step: float) -> list[float]:
    closes = [100.0]
    for i in range(1, n):
        drift = step if (i // 12) % 2 == 0 else -step
        closes.append(closes[-1] * (1 + drift))
    return closes


def test_resolve_paths_forms():
    assert _resolve_market_raw_paths("a", None) == [Path("a")]
    assert _resolve_market_raw_paths(None, ["a", "b"]) == [Path("a"), Path("b")]
    # comma-separated string (the build CLI's key=value form)
    assert _resolve_market_raw_paths(None, "a, b") == [Path("a"), Path("b")]
    with pytest.raises(ValueError, match="requires"):
        _resolve_market_raw_paths(None, None)


def test_joint_build_concatenates_both_symbols(tmp_path: Path):
    btc = _write_market_raw(tmp_path, "BTCUSDT", _trend(200, 0.004))
    mes = _write_market_raw(tmp_path, "MES", _trend(200, 0.002))  # different vol
    rows = list(SetupCandidatesBuilder().iter_rows(
        market_raw_paths=[btc, mes], vol_window_n=10, max_holding=8,
        cusum_threshold_mult=0.5,
    ))
    symbols = {r["symbol"] for r in rows}
    assert symbols == {"BTCUSDT", "MES"}, symbols
    # Each symbol contributes candidates (joint > either alone).
    btc_only = list(SetupCandidatesBuilder().iter_rows(
        market_raw_path=btc, vol_window_n=10, max_holding=8, cusum_threshold_mult=0.5))
    assert len(rows) > len(btc_only)
    # Per-symbol vol bucketing: each symbol's rows carry vol_b* labels derived
    # from its OWN distribution (build didn't crash mixing scales).
    assert all(r["vol_bucket"].startswith("vol_b") for r in rows)


def test_comma_string_paths_build(tmp_path: Path):
    btc = _write_market_raw(tmp_path, "BTCUSDT", _trend(120, 0.004))
    mes = _write_market_raw(tmp_path, "MES", _trend(120, 0.003))
    rows = list(SetupCandidatesBuilder().iter_rows(
        market_raw_paths=f"{btc},{mes}", vol_window_n=10, max_holding=8,
        cusum_threshold_mult=0.5,
    ))
    assert {r["symbol"] for r in rows} == {"BTCUSDT", "MES"}
