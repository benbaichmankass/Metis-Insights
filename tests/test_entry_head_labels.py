"""M21 E-3 — P_win label + entry_confidence contract on the E0 builder.

Contract (docs/research/M21-entry-refinement-DESIGN.md § E-3):
  * ``first_touch_1r`` — the trade touched +1R (bar-HIGH basis) before it
    touched −1R (bar-LOW basis); a bar crossing both counts conservatively
    as loss-first (the intrabar-stop-first convention).
  * ``reaches_2r`` — after a winning first touch, the trade also touched +2R.
  * Both are constant per trade and stamped on every row.
  * ``entry_confidence`` — the emit's live-parity ``confidence`` stamped on
    every row; ``None`` when the source emit predates the field.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "build_exit_head_dataset",
    Path(__file__).resolve().parents[1] / "scripts" / "ml"
    / "build_exit_head_dataset.py")
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)


def _candles(bars):
    """bars: list of (high, low, close) hourly from t=1000h."""
    out = []
    for i, (hi, lo, cl) in enumerate(bars):
        out.append({"t": (1000 + i) * 3600.0, "high": float(hi),
                    "low": float(lo), "close": float(cl), "volume": 1.0})
    return out


def _trade(t_open_bar, t_close_bar, entry=100.0, sl=90.0, conf=0.25):
    return {"source": "harness", "strategy": "trend_donchian",
            "symbol": "X", "direction": "long",
            "t_open": (1000 + t_open_bar) * 3600.0,
            "t_close": (1000 + t_close_bar) * 3600.0,
            "entry": entry, "sl": sl, "final_r": 1.0,
            "final_r_source": "harness_net_r", "exit_reason": "trail_stop",
            "confidence": conf}


def _rows(tr, candles):
    ts = [c["t"] for c in candles]
    atrs = _mod.atr_series(candles)
    return _mod.rows_for_trade(tr, candles, ts, atrs)


def test_win_first_touch_and_2r():
    # entry 100 / sl 90 (risk 10): bar1 high 111 (+1.1R), bar2 high 121 (+2.1R)
    bars = [(101, 99, 100)] * 1 + [(111, 100, 108), (121, 105, 118),
                                   (119, 110, 112), (113, 108, 110)]
    tr = _trade(0, 4)
    rows = _rows(tr, _candles(bars))
    assert rows and all(r["first_touch_1r"] == 1 for r in rows)
    assert all(r["reaches_2r"] == 1 for r in rows)
    assert all(r["entry_confidence"] == 0.25 for r in rows)


def test_loss_first_touch():
    # bar1 low 89 (−1.1R) before any +1R high
    bars = [(101, 99, 100), (102, 89, 95), (96, 92, 94), (95, 91, 93)]
    rows = _rows(_trade(0, 3), _candles(bars))
    assert rows and all(r["first_touch_1r"] == 0 for r in rows)
    assert all(r["reaches_2r"] == 0 for r in rows)


def test_both_in_one_bar_counts_loss_first():
    # bar1 spans 89..111 — crosses BOTH ±1R: conservative loss-first
    bars = [(101, 99, 100), (111, 89, 105), (112, 100, 108), (109, 101, 104)]
    rows = _rows(_trade(0, 3), _candles(bars))
    assert rows and all(r["first_touch_1r"] == 0 for r in rows)


def test_missing_confidence_is_none():
    tr = _trade(0, 3, conf=None)
    bars = [(101, 99, 100), (102, 98, 101), (103, 99, 102), (104, 100, 103)]
    rows = _rows(tr, _candles(bars))
    assert rows and all(r["entry_confidence"] is None for r in rows)
