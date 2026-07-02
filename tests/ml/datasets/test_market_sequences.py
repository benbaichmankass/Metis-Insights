"""Tests for the `market_sequences` dataset family (M19 T1.1).

No torch/numpy — the family is pure windowing over a market_features JSONL.
"""
from __future__ import annotations

import json
from pathlib import Path

from ml.datasets.families.market_sequences import (
    DEFAULT_FEATURE_COLUMNS,
    MarketSequencesBuilder,
)
from ml.datasets.registry import get_builder
from ml.datasets.sequence_window import SEQ_WINDOW_COLUMN


def _write_market_features(root: Path, n: int = 10) -> Path:
    d = root / "market_features" / "BTCUSDT" / "15m" / "v002"
    d.mkdir(parents=True, exist_ok=True)
    with (d / "data.jsonl").open("w") as fh:
        for i in range(n):
            row = {
                "ts": f"2026-01-01T00:{i:02d}:00Z",
                "symbol": "BTCUSDT",
                "timeframe": "15m",
                "log_return": float(i) * 0.001,
                "rolling_log_return_vol": 0.01 + i * 0.0001,
                "hour_of_day": i % 24,
                "dayofweek": i % 7,
                "regime_label": "volatile" if i % 3 == 0 else "range",
                "direction_label": "up" if i % 2 == 0 else "down",
            }
            fh.write(json.dumps(row) + "\n")
    return d


def test_family_is_registered():
    assert isinstance(get_builder("market_sequences"), MarketSequencesBuilder)


def test_build_attaches_window_and_carries_label(tmp_path: Path):
    mf = _write_market_features(tmp_path, n=10)
    out_root = tmp_path / "out"
    paths = MarketSequencesBuilder().build(
        output_dir=out_root,
        version="v001",
        source="market_features",
        symbol_scope="BTCUSDT",
        timeframe="15m",
        market_features_path=str(mf),
        seq_len=4,
    )
    lines = [json.loads(ln) for ln in paths.data.read_text().splitlines() if ln]
    # 10 bars, seq_len 4 → 7 windowed rows.
    assert len(lines) == 7
    for row in lines:
        w = row[SEQ_WINDOW_COLUMN]
        assert len(w) == 4
        assert all(len(bar) == len(DEFAULT_FEATURE_COLUMNS) for bar in w)
        assert row["regime_label"] in {"range", "volatile"}
        assert "ts" in row


def test_window_is_causal_last_bar_is_own(tmp_path: Path):
    mf = _write_market_features(tmp_path, n=6)
    out_root = tmp_path / "out"
    paths = MarketSequencesBuilder().build(
        output_dir=out_root, version="v001", source="market_features",
        symbol_scope="BTCUSDT", timeframe="15m",
        market_features_path=str(mf), seq_len=3,
        feature_columns="log_return",
    )
    lines = [json.loads(ln) for ln in paths.data.read_text().splitlines() if ln]
    # First windowed row corresponds to bar index 2 (ts ...:02:..). Its window's
    # last element is bar 2's log_return; no bar 3+ leaks in.
    first = lines[0]
    assert first["ts"].endswith(":02:00Z")
    last_bar_val = first[SEQ_WINDOW_COLUMN][-1][0]
    assert abs(last_bar_val - 0.002) < 1e-9  # bar index 2 → 2*0.001
