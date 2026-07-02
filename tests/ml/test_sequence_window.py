"""Tests for the causal sequence-window materialization (M19 T1.1).

Pure-stdlib — no torch/numpy — so these run everywhere (incl. the money-box CI).
"""
from __future__ import annotations

from ml.datasets.sequence_window import SEQ_WINDOW_COLUMN, build_causal_windows


def _row(ts, sym, a, b, label="range"):
    return {"ts": ts, "symbol": sym, "timeframe": "15m", "a": a, "b": b, "regime_label": label}


def test_window_shape_and_causality():
    rows = [_row(f"2026-01-01T00:{i:02d}:00Z", "BTCUSDT", float(i), float(-i)) for i in range(5)]
    out = build_causal_windows(rows, feature_columns=["a", "b"], seq_len=3)
    # 5 bars, seq_len 3 → first 2 dropped, 3 remain.
    assert len(out) == 3
    # The window for the LAST row must be bars [2,3,4] (all <= its own index) —
    # i.e. its own value is the last element and no forward bar leaks in.
    last = out[-1]
    assert last[SEQ_WINDOW_COLUMN] == [[2.0, -2.0], [3.0, -3.0], [4.0, -4.0]]
    # Every window is exactly (seq_len, n_features).
    for r in out:
        w = r[SEQ_WINDOW_COLUMN]
        assert len(w) == 3 and all(len(bar) == 2 for bar in w)


def test_groups_never_cross_symbol():
    rows = []
    for i in range(4):
        rows.append(_row(f"t{i}", "BTCUSDT", float(i), 0.0))
    for i in range(4):
        rows.append(_row(f"t{i}", "ETHUSDT", float(100 + i), 0.0))
    out = build_causal_windows(rows, feature_columns=["a", "b"], seq_len=3)
    # 2 groups × (4 - 2) = 4 windows; none mixes symbols.
    assert len(out) == 4
    for r in out:
        vals = [bar[0] for bar in r[SEQ_WINDOW_COLUMN]]
        assert all(v < 50 for v in vals) or all(v >= 100 for v in vals)


def test_time_sorted_within_group_regardless_of_input_order():
    rows = [_row("t2", "BTCUSDT", 2.0, 0.0), _row("t0", "BTCUSDT", 0.0, 0.0), _row("t1", "BTCUSDT", 1.0, 0.0)]
    out = build_causal_windows(rows, feature_columns=["a"], seq_len=3)
    assert len(out) == 1
    # Sorted by ts → window is [0,1,2] not the input order.
    assert [bar[0] for bar in out[0][SEQ_WINDOW_COLUMN]] == [0.0, 1.0, 2.0]


def test_missing_feature_coerces_to_zero_not_crash():
    rows = [{"ts": f"t{i}", "symbol": "BTCUSDT", "timeframe": "15m", "regime_label": "range"} for i in range(3)]
    out = build_causal_windows(rows, feature_columns=["a", "b"], seq_len=2)
    assert len(out) == 2
    assert out[0][SEQ_WINDOW_COLUMN] == [[0.0, 0.0], [0.0, 0.0]]


def test_original_keys_preserved():
    rows = [_row(f"t{i}", "BTCUSDT", float(i), 0.0, label="volatile") for i in range(3)]
    out = build_causal_windows(rows, feature_columns=["a"], seq_len=2)
    for r in out:
        assert r["regime_label"] == "volatile"
        assert "ts" in r and "symbol" in r and "timeframe" in r
